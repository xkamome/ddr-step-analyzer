"""兩階段落地判定：(A) 每隻腳「這一幀踩下去」機率 → 找峰值；(B) 每次踩下去判斷是哪一塊板（4 類）。
標籤：亮燈起點，歸給當下離那塊板最近的腳。留一支法（依遊玩分組）、門檻用其他組選。

用法：experiments/py.sh experiments/learn_two_stage.py halpe|rtmw
"""
from __future__ import annotations

import json
import sys
import time

import lightgbm as lgb
import numpy as np

from common import CACHE, DATA, GROUP, LIT, RESULTS, UNSCORED, heartbeat, load_json, pool, save_json, score_events
from eval_contact import prepare_run, tol_frames
from learn_contact import gt_events

from ddrpose.analyze import ARROWS, LEG, _h_from_points, _load_motion, _play_mask, fix_left_right
from ddrpose.contact import _shift, panel_dist, per_foot, prep_raw

PK = 'LDUR'
OFFS_A = tuple(np.round(np.arange(-0.3, 0.301, 1 / 30), 3))
OFFS_B = (-0.1, -0.067, -0.033, 0.0, 0.033, 0.067, 0.1, 0.133, 0.167)
PA = dict(objective='binary', learning_rate=0.05, num_leaves=31, min_data_in_leaf=50, feature_fraction=0.8,
          bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, num_threads=4)
PB = dict(objective='multiclass', num_class=4, learning_rate=0.05, num_leaves=15, min_data_in_leaf=20,
          feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, num_threads=4)


def load(vid, pose):
    d = prepare_run(vid, pose)
    meta = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
    a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
    pts = {k: np.asarray(v, float) for k, v in a['calibration']['points'].items() if k in PK}
    z = np.load(d / 'pose.npz')
    t, K, S, valid = z['t'], z['kpts'].astype(float), z['scores'].astype(float), z['valid']
    fps = 1.0 / np.median(np.diff(t))
    Mot, _, mot_ok = _load_motion(d, len(t), meta['width'])
    play = _play_mask(K, S, valid, Mot, mot_ok, fps)
    valid = valid & play
    S = np.where(play[:, None], S, 0.0)
    K, S, _ = fix_left_right(K, S, valid)
    X, rel = prep_raw(K, S, valid)
    pf = per_foot(X, S, rel, Mot, _h_from_points(pts), fps)
    n = len(t)
    dist = {s: np.stack([panel_dist((pf[s]['heel_pad'], pf[s]['toe_pad']), ARROWS[k]) for k in PK], 1) for s in LEG}
    D = dict(n=n, fps=fps, play=play, pf=pf, dist=dist)
    D['FA'], D['namesA'] = feats_a(D)
    D['FB'] = feats_b(D)
    if vid in LIT:
        g = np.load(CACHE / vid / 'gt.npz')
        ons = [(int(f), int(p)) for f, p in g['onsets'] if f < n]
        D['onsets'] = ons
        # 歸腳：起點後 0–2 幀，離那塊板較近的腳
        foot_of = []
        for f, p in ons:
            dl = np.nanmedian(dist['L'][f:f + 3, p]); dr = np.nanmedian(dist['R'][f:f + 3, p])
            foot_of.append(0 if (np.nan_to_num(dl, nan=9) <= np.nan_to_num(dr, nan=9)) else 1)
        D['foot_of'] = foot_of
        yA = np.zeros((n, 2), bool)
        w = max(1, int(round(0.05 * fps)))
        for (f, p), fo in zip(ons, foot_of):
            yA[max(0, f - w):f + w + 1, fo] = True
        D['yA'] = yA
    return D


def feats_a(D):
    """(N, 2 腳, F)：這隻腳的時間序列＋另一隻腳的少量量。"""
    n, fps = D['n'], D['fps']
    offs = [int(round(o * fps)) for o in OFFS_A]
    out, names = [], []
    for si, s in enumerate('LR'):
        o_ = 'R' if s == 'L' else 'L'
        d, do = D['pf'][s], D['pf'][o_]
        cols, nm = [], []
        dmin = np.nanmin(D['dist'][s], 1)
        for k_, o in zip(OFFS_A, offs):
            for name, arr in (('speed', d['speed']), ('vy', d['vy']), ('lift', d['lift']), ('pspeed', d['pspeed']),
                              ('dmin', dmin)):
                cols.append(_shift(arr, o)); nm.append(f'{name}{k_:+.3f}')
        for name in ('acc', 'tilt', 'conf', 'rel', 'knee'):
            cols.append(d[name]); nm.append(name)
        for name in ('toe_pad', 'heel_pad', 'ank_pad'):
            cols.append(d[name][:, 0]); nm.append(name + 'x'); cols.append(d[name][:, 1]); nm.append(name + 'y')
        for name in ('speed', 'lift'):
            cols.append(do[name]); nm.append('other_' + name)
        cols.append(np.full(n, float(si))); nm.append('is_right')
        out.append(np.stack(cols, 1)); names = nm
    return np.stack(out, 1).astype(np.float32), names


def feats_b(D):
    """(N, 2 腳, F)：判斷板位用——腳在踏板上的位置（前後幾幀）與到四塊板的距離。"""
    n, fps = D['n'], D['fps']
    offs = [int(round(o * fps)) for o in OFFS_B]
    out = []
    for si, s in enumerate('LR'):
        d = D['pf'][s]
        cols = []
        for o in offs:
            for name in ('toe_pad', 'heel_pad', 'ank_pad'):
                cols.append(_shift(d[name][:, 0], o)); cols.append(_shift(d[name][:, 1], o))
            for p in range(4):
                cols.append(_shift(D['dist'][s][:, p], o))
        do = D['pf']['R' if s == 'L' else 'L']
        for name in ('toe_pad', 'heel_pad'):
            cols.append(do[name][:, 0]); cols.append(do[name][:, 1])
        cols.append(np.full(n, float(si)))
        out.append(np.stack(cols, 1))
    return np.stack(out, 1).astype(np.float32)


def peaks(x, thr, sep_f):
    from scipy.ndimage import maximum_filter1d
    w = max(3, 2 * sep_f + 1)
    idx = np.where((x >= maximum_filter1d(x, w)) & (x > thr))[0]
    out, last = [], -10 ** 9
    for i in idx:
        if i - last >= w // 2:
            out.append(int(i)); last = i
    return out


def train_a(Ds):
    X = np.concatenate([D['FA'][D['play']].reshape(-1, D['FA'].shape[2]) for D in Ds])
    y = np.concatenate([D['yA'][D['play']].reshape(-1) for D in Ds]).astype(int)
    return lgb.train(PA, lgb.Dataset(X, y), 400)


def train_b(Ds):
    X, y = [], []
    for D in Ds:
        for (f, p), fo in zip(D['onsets'], D['foot_of']):
            X.append(D['FB'][f, fo]); y.append(p)
    return lgb.train(PB, lgb.Dataset(np.array(X), np.array(y)), 300)


def predict(mA, mB, D, thr, sep_s):
    n = D['n']
    PA_ = mA.predict(D['FA'].reshape(-1, D['FA'].shape[2])).reshape(n, 2)
    PA_[~D['play']] = 0
    ev = []
    sep_f = max(1, int(round(sep_s * D['fps'])))
    cand = [(f, fo) for fo in (0, 1) for f in peaks(PA_[:, fo], thr, sep_f)]
    if cand:
        Xb = np.array([D['FB'][f, fo] for f, fo in cand])
        pb = mB.predict(Xb)
        for (f, fo), pr in zip(cand, pb):
            ev.append((f, int(np.argmax(pr))))
    return sorted(ev), PA_


def run(pose):
    t0 = time.time()
    data = {v: load(v, pose) for v in LIT + UNSCORED}
    heartbeat()
    print(f'load {time.time() - t0:.0f}s FA={data[LIT[0]]["FA"].shape}', flush=True)
    groups = sorted(set(GROUP[v] for v in LIT))
    models = {}
    for g in groups:
        tr = [data[v] for v in LIT if GROUP[v] != g]
        models[g] = (train_a(tr), train_b(tr))
        heartbeat()
    # 板位分類本身（時間給定為真實起點）的正確率
    pacc = []
    for v in LIT:
        mA, mB = models[GROUP[v]]
        D = data[v]
        Xb = np.array([D['FB'][f, fo] for (f, p), fo in zip(D['onsets'], D['foot_of'])])
        pacc.append(float((np.argmax(mB.predict(Xb), 1) == np.array([p for f, p in D['onsets']])).mean()))
    print('stage B panel acc given true onsets:', np.round(pacc, 3), flush=True)
    grid = [(thr, sep) for thr in (0.15, 0.2, 0.25, 0.3, 0.4, 0.5) for sep in (0.07, 0.1, 0.13)]
    cache = {}

    def sc(v, th):
        if (v, th) not in cache:
            mA, mB = models[GROUP[v]]
            ev, _ = predict(mA, mB, data[v], *th)
            cache[(v, th)] = score_events(gt_events(v), ev, data[v]['fps'], tol_frames(data[v]['fps']))
        return cache[(v, th)]

    cells, chosen = {}, {}
    for v in LIT:
        others = [u for u in LIT if GROUP[u] != GROUP[v]]
        best = max(grid, key=lambda th: pool([sc(u, th) for u in others])['f1'])
        chosen[v] = best
        cells[v] = sc(v, best)
        print(v[:12], cells[v]['f1'], cells[v]['precision'], cells[v]['recall'], cells[v]['panel_acc'], best, flush=True)
    cells['pooled'] = pool([cells[v] for v in LIT])
    cells['_thresholds'] = {v: list(c) for v, c in chosen.items()}
    cells['_stageB_panel_acc_true_onsets'] = dict(zip(LIT, [round(x, 3) for x in pacc]))
    print('pooled', cells['pooled'], f'{time.time() - t0:.0f}s')
    var = load_json(RESULTS / 'contact_variants.json', {}) or {}
    var[f'{pose}_two_stage'] = cells
    save_json(var, RESULTS / 'contact_variants.json')


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    run(sys.argv[1])
