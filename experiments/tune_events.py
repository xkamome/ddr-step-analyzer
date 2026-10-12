"""用 OOF 機率調整「機率 → 踩板事件」的後處理（不重新訓練）。參數一樣用其他組選，避免偷看。

用法：experiments/py.sh experiments/tune_events.py halpe
"""
from __future__ import annotations

import itertools
import sys

import numpy as np
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from common import CACHE, GROUP, LIT, RESULTS, load_json, pool, save_json, score_events
from eval_contact import tol_frames
from learn_contact import gt_events


def extract(P, fps, thr, sep_s, ratio, k_s, smooth, cap=2):
    """每板局部最大值 → 跨板抑制：±k_s 秒內有別板更高的峰，且本峰 < ratio × 它 → 丟掉；
    同一視窗最多保留 cap 個（兩隻腳）。"""
    if smooth > 1:
        P = uniform_filter1d(P, smooth, axis=0)
    w = max(3, int(round(sep_s * fps)) * 2 + 1)
    cands = []
    for p in range(4):
        x = P[:, p]
        mx = maximum_filter1d(x, w)
        idx = np.where((x >= mx) & (x > thr))[0]
        last = -10 ** 9
        for i in idx:
            if i - last >= w // 2:
                cands.append((int(i), p, float(x[i]))); last = i
    cands.sort()
    k = max(1, int(round(k_s * fps)))
    keep = []
    for i, (f, p, v) in enumerate(cands):
        near = [c for c in cands if abs(c[0] - f) <= k and c[1] != p]
        stronger = [c for c in near if c[2] > v]
        if any(v < ratio * c[2] for c in stronger):
            continue
        if len(stronger) >= cap:
            continue
        keep.append((f, p))
    return keep


def main(pose, target='onset'):
    oof = {v: np.load(CACHE / v / f'oof_{pose}_{target}.npy') for v in LIT}
    fps = {v: load_json(RESULTS / 'gt.json')[v]['fps'] for v in LIT}
    gts = {v: gt_events(v) for v in LIT}
    grid = list(itertools.product((0.2, 0.25, 0.3, 0.4), (0.07, 0.1), (0.0, 0.5, 0.7, 0.85), (0.067, 0.1), (1, 3)))
    sc_cache = {}

    def sc(v, th):
        key = (v, th)
        if key not in sc_cache:
            sc_cache[key] = score_events(gts[v], extract(oof[v], fps[v], *th), fps[v], tol_frames(fps[v]))
        return sc_cache[key]

    cells, chosen = {}, {}
    for v in LIT:
        others = [u for u in LIT if GROUP[u] != GROUP[v]]
        best = max(grid, key=lambda th: pool([sc(u, th) for u in others])['f1'])
        chosen[v] = best
        cells[v] = sc(v, best)
        print(v, cells[v]['f1'], cells[v]['precision'], cells[v]['recall'], best, flush=True)
    cells['pooled'] = pool([cells[v] for v in LIT])
    allbest = max(grid, key=lambda th: pool([sc(u, th) for u in LIT])['f1'])
    print('pooled', cells['pooled'])
    print('best on all (for deployment):', allbest, pool([sc(u, allbest) for u in LIT])['f1'])
    return cells, chosen, allbest


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    cells, chosen, allbest = main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'onset')
    var = load_json(RESULTS / 'contact_variants.json', {}) or {}
    var[f'{sys.argv[1]}_postproc_nms'] = dict(pooled=cells['pooled'], chosen={k: list(v) for k, v in chosen.items()},
                                             deploy=list(allbest), **{v: cells[v] for v in LIT})
    save_json(var, RESULTS / 'contact_variants.json')
