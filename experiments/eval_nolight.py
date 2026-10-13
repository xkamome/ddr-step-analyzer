"""沒燈時到底準不準、資料夠不夠：
  A) 學習曲線——訓練用 1／2／3 次遊玩，對沒看過的那次遊玩打分數；
     另外固定 3 次遊玩，只用 25／50／75／100% 的時間長度。看 F1 是否還在上升。
  B) 端到端留一支法——用「沒看過這次遊玩」的模型，跑完整 analyze.py（lights:false），
     再用這支影片自己的亮燈打分數。分開算：全部步、只算模型補的步、只算骨架原本的步。
部署模型的設定：Halpe26、不平滑、onset 目標、±0.3 s、門檻 (0.2, 0.07, None)。

用法：experiments/py.sh experiments/eval_nolight.py [A|B|AB]
輸出：results/nolight.json
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
import time

import lightgbm as lgb
import numpy as np

from common import CACHE, DATA, GROUP, LIT, RESULTS, heartbeat, load_json, match_events, pool, save_json, \
    score_events
from contact_features import load_video_features
from eval_contact import SYM2IDX, prepare_run, tol_frames
from export_gbdt import flatten
from learn_contact import OFFS_WIDE, PARAMS, ROUNDS, gt_events, onset_target, peaks_from_prob
from test_integration import PROD_PY, prep, user_calib

THR = (0.2, 0.07, None)
MODELS = CACHE / 'models' / 'loo'


def load(vid):
    d = prepare_run(vid, 'halpe')
    a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
    pts = {k: v for k, v in a['calibration']['points'].items() if k in 'LDUR'}
    F, names, play, fps = load_video_features(d, d / 'pose.npz', pts, smooth=False, offs_s=OFFS_WIDE)
    y = onset_target(np.load(CACHE / vid / 'gt.npz')['onsets'], len(F), fps)
    return dict(F=F, names=names, play=play, fps=fps, y=y)


def train(Ds, masks=None):
    X, y = [], []
    for i, D in enumerate(Ds):
        m = D['play'] if masks is None else (D['play'] & masks[i])
        X.append(D['F'][m].reshape(-1, D['F'].shape[2])); y.append(D['y'][m].reshape(-1))
    return lgb.train(PARAMS, lgb.Dataset(np.concatenate(X), np.concatenate(y).astype(int)), ROUNDS)


def score(model, vids, data):
    cells, timing = [], []
    for v in vids:
        D = data[v]
        P = model.predict(D['F'].reshape(-1, D['F'].shape[2])).reshape(len(D['F']), 4)
        P[~D['play']] = 0.0
        ev = peaks_from_prob(P, THR[0], THR[1], D['fps'], valley=THR[2])
        gt = gt_events(v)
        cells.append(score_events(gt, ev, D['fps'], tol_frames(D['fps'])))
        timing.append(timing_cell(gt, ev, D['fps']))
    return cells, timing


def timing_cell(gt, pred, fps):
    """不管板位，只看時間對不對（一對一配對，±133 ms）。"""
    gt, pred, pairs, _, _ = match_events(gt, pred, tol_frames(fps))
    return dict(n_gt=len(gt), n_pred=len(pred), n_pair=len(pairs))


def pool_timing(cells):
    ng, npd, npr = (sum(c[k] for c in cells) for k in ('n_gt', 'n_pred', 'n_pair'))
    p, r = npr / max(npd, 1), npr / max(ng, 1)
    return dict(f1=round(2 * p * r / max(p + r, 1e-9), 4), precision=round(p, 4), recall=round(r, 4))


def summarize(cells, timing):
    s = pool(cells)
    return dict(f1=s['f1'], precision=s['precision'], recall=s['recall'], panel_acc=s['panel_acc'],
                timing=pool_timing(timing))


def part_a(data):
    groups = sorted(set(GROUP[v] for v in LIT))
    out = {'by_plays': {}, 'by_minutes': {}}
    t0 = time.time()
    for k in (1, 2, 3):
        cells, timing = [], []
        for g in groups:
            test = [v for v in LIT if GROUP[v] == g]
            others = [h for h in groups if h != g]
            for sub in itertools.combinations(others, k):
                tr = [data[v] for v in LIT if GROUP[v] in sub]
                c, t = score(train(tr), test, data)
                cells += c; timing += t
                heartbeat()
        out['by_plays'][k] = summarize(cells, timing)
        print('plays', k, out['by_plays'][k], f'{time.time() - t0:.0f}s', flush=True)
    rng = np.random.default_rng(0)
    for frac in (0.25, 0.5, 0.75, 1.0):
        cells, timing = [], []
        for seed in range(2 if frac < 1 else 1):
            for g in groups:
                test = [v for v in LIT if GROUP[v] == g]
                tr = [data[v] for v in LIT if GROUP[v] != g]
                masks = []
                for D in tr:          # 每支影片切 8 段連續區塊，隨機取 frac 的區塊
                    n = len(D['F'])
                    blk = np.minimum(np.arange(n) * 8 // n, 7)
                    keep = rng.permutation(8)[:max(1, int(round(8 * frac)))]
                    masks.append(np.isin(blk, keep))
                c, t = score(train(tr, masks), test, data)
                cells += c; timing += t
                heartbeat()
        out['by_minutes'][frac] = summarize(cells, timing)
        print('minutes', frac, out['by_minutes'][frac], f'{time.time() - t0:.0f}s', flush=True)
    out['train_minutes_per_fold'] = {
        g: round(sum(data[v]['play'].sum() / data[v]['fps'] for v in LIT if GROUP[v] != g) / 60, 1)
        for g in groups}
    return out


RUNNER = r'''
import sys, json
sys.path.insert(0, sys.argv[1])
sys.stdout.reconfigure(errors='replace')
import ddrpose.contact as C
_orig = C.load_model
C.load_model = lambda path=None: _orig(sys.argv[4])
from ddrpose.analyze import analyze
r = analyze(sys.argv[2], json.loads(sys.argv[3]))
print(json.dumps({'n_steps': len(r['steps']), 'model_only': r['calibration'].get('model_only_steps')}))
'''


def part_b(data):
    from test_integration import ROOT
    MODELS.mkdir(parents=True, exist_ok=True)
    meta = json.loads(str(np.load(ROOT / 'ddrpose' / 'models' / 'contact_gbdt.npz')['meta']))
    groups = sorted(set(GROUP[v] for v in LIT))
    res = {}
    allc = {'all': [], 'model': [], 'skeleton': []}
    allt = {'all': [], 'model': [], 'skeleton': []}
    for g in groups:
        model = train([data[v] for v in LIT if GROUP[v] != g])
        npz = MODELS / f'contact_{g}.npz'
        m = dict(meta, trained_on=f'LOO without {g}')
        np.savez_compressed(npz, **flatten(model), meta=json.dumps(m, ensure_ascii=False))
        for v in (v for v in LIT if GROUP[v] == g):
            d = prep(v, 'nolight_loo')
            (d / 'analysis.json').unlink(missing_ok=True)
            p = subprocess.run([str(PROD_PY), '-X', 'utf8', '-c', RUNNER, str(ROOT), str(d),
                                json.dumps(user_calib(v, lights=False)), str(npz)],
                               capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
            if p.returncode != 0:
                res[v] = dict(ok=False, err=p.stderr[-800:])
                print(v, 'FAIL', p.stderr[-800:], flush=True)
                continue
            a = json.loads((d / 'analysis.json').read_text(encoding='utf-8'))
            fps, gt = a['fps'], gt_events(v)
            r = dict(ok=True, run=json.loads(p.stdout.strip().splitlines()[-1]))
            for kind, keep in (('all', lambda s: True), ('model', lambda s: s.get('from_model')),
                               ('skeleton', lambda s: not s.get('from_model'))):
                pe = [(int(s['frame']), SYM2IDX[s['panel']]) for s in a['steps']
                      if s.get('panel') in SYM2IDX and keep(s)]
                c = score_events(gt, pe, fps, tol_frames(fps))
                t = timing_cell(gt, pe, fps)
                r[kind] = dict(f1=c['f1'], precision=c['precision'], recall=c['recall'], n_pred=c['n_pred'],
                               timing=pool_timing([t]))
                allc[kind].append(c); allt[kind].append(t)
            res[v] = r
            heartbeat()
            print(v, {k: r[k]['f1'] for k in ('all', 'model', 'skeleton')}, r['run'], flush=True)
    res['pooled'] = {k: summarize(allc[k], allt[k]) for k in allc if allc[k]}
    print('pooled', res['pooled'], flush=True)
    return res


def main():
    sys.stdout.reconfigure(errors='replace')
    which = sys.argv[1] if len(sys.argv) > 1 else 'AB'
    t0 = time.time()
    data = {v: load(v) for v in LIT}
    print(f'load {time.time() - t0:.0f}s', flush=True)
    for key, part in (('A', part_a), ('B', part_b)):
        if key in which:
            r = part(data)
            out = load_json(RESULTS / 'nolight.json', {}) or {}     # 重讀：A、B 可能分兩個行程同時跑
            out['learning_curve' if key == 'A' else 'end_to_end_loo'] = r
            save_json(out, RESULTS / 'nolight.json')
    print('done', f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
