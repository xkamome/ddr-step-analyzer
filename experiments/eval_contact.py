"""方法 pose_halpe / pose_rtmw：現行 analyze.py 的純骨架落地判定（lights:false），對亮燈標準答案評分。

板位一律用 analysis.json 的最終板位（當作手動校正），只比較「踩下去與否＋哪一板」的判定，
不把校正誤差混進來。analyze 在 cache/<vid>/run_<pose>/ 的複本上跑，不動 data/。

用法：experiments/py.sh experiments/eval_contact.py halpe|rtmw
"""
from __future__ import annotations

import json
import shutil
import sys

import numpy as np

from common import CACHE, DATA, LIT, RESULTS, UNSCORED, heartbeat, load_json, pool, save_json, score_events

from ddrpose.analyze import analyze

SYM2IDX = {'←': 0, '↓': 1, '↑': 2, '→': 3}


def tol_frames(fps):
    return max(2, int(round(0.133 * fps)))


def prepare_run(vid, pose):
    """cache/<vid>/run_<pose>/：meta、motion、vertical 從 data 複製；pose.npz 依方法。"""
    d = CACHE / vid / f'run_{pose}'
    d.mkdir(parents=True, exist_ok=True)
    for f in ('meta.json', 'motion.npz', 'vertical.json'):
        if (DATA / vid / f).exists():
            shutil.copy2(DATA / vid / f, d / f)
    if pose == 'halpe':
        shutil.copy2(DATA / vid / 'pose.npz', d / 'pose.npz')
    else:
        z = np.load(CACHE / vid / 'pose_rtmw.npz')
        np.savez_compressed(d / 'pose.npz', t=z['t'], kpts=z['kpts'], scores=z['scores'], valid=z['valid'])
    return d


def user_calib(vid, lights=False):
    """最終板位＋使用者設定（foot_offset、hidden_arms）。"""
    a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
    c = load_json(DATA / vid / 'calib.json', {}) or {}
    cal = {'points': a['calibration']['points'], 'lights': lights}
    for k in ('foot_offset', 'hidden_arms'):
        if k in c:
            cal[k] = c[k]
    return cal


def pred_events(steps):
    return [(int(st['frame']), SYM2IDX[st['panel']]) for st in steps if st.get('panel') in SYM2IDX]


def gt_events(vid):
    z = np.load(CACHE / vid / 'gt.npz')
    return [(int(f), int(p)) for f, p in z['onsets']]


def run(pose):
    out = load_json(RESULTS / 'contact.json', {}) or {}
    cells = {}
    detail = {}
    for vid in LIT + UNSCORED:
        d = prepare_run(vid, pose)
        r = analyze(d, user_calib(vid))
        heartbeat()
        pe = pred_events(r['steps'])
        detail[vid] = dict(n_steps=len(r['steps']), n_pred_arrow=len(pe), fps=r['fps'])
        np.save(d / 'pred_events.npy', np.array(pe, int).reshape(-1, 2))
        if vid in LIT:
            cells[vid] = score_events(gt_events(vid), pe, r['fps'], tol_frames(r['fps']))
            print(vid, cells[vid], flush=True)
    cells['pooled'] = pool([cells[v] for v in LIT])
    cells['_detail'] = detail
    out[f'pose_{pose}'] = cells
    save_json(out, RESULTS / 'contact.json')
    print('pooled', cells['pooled'])


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    run(sys.argv[1])
