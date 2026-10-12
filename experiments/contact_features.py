"""落地判定模型的特徵（只用 numpy/scipy＋analyze.py 的工具，之後可直接搬進 ddrpose）。

每一列＝(幀 t, 箭頭板 p)。特徵以「秒」為時間單位、以小腿長為長度單位，跨 fps／解析度可比。
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from ddrpose.analyze import (ARROWS, LEG, _apply_h, _h_from_points, _interp_nan, _load_motion, _play_mask,
                             _warp, fix_left_right, prep_keypoints)

PANEL_KEYS = 'LDUR'
EXTRA = False        # v2 特徵：踏板座標速度、垂直加速度、膝角
FLOW = False         # 腳部光流特徵（cache/VID/flow.npz）
OFFS = (-0.2, -0.133, -0.067, 0.0, 0.067, 0.133, 0.2)   # 秒


def _shift(a, k):
    """a[t+k]（邊界取端點）。"""
    n = len(a)
    idx = np.clip(np.arange(n) + k, 0, n - 1)
    return a[idx]


def _vel(y, fps):
    v = np.gradient(_interp_nan(y.copy())) * fps
    return v


def per_foot(X, S, rel, Mot, H, fps):
    """每隻腳的逐幀量：踏板座標（腳尖、腳跟、腳踝）、影像速度、離地高度代理、信心。"""
    n = len(X)
    out = {}
    shank = {s: np.linalg.norm(X[:, j['knee']] - X[:, j['ankle']], axis=1) for s, j in LEG.items()}
    sh = np.nanmedian(np.r_[shank['L'], shank['R']])
    sh_loc = {s: np.where(np.isfinite(shank[s]), shank[s], sh) for s in LEG}
    sh_loc = {s: np.maximum(uniform_filter1d(v, int(fps) | 1), 0.3 * sh) for s, v in sh_loc.items()}
    for s, j in LEG.items():
        toe = (X[:, j['bt']] + X[:, j['st']]) / 2
        heel = X[:, j['heel']]
        ank = X[:, j['ankle']]
        g = (heel + X[:, j['bt']] + X[:, j['st']]) / 3
        L = sh_loc[s]
        d = {}
        for name, p in (('toe', toe), ('heel', heel), ('ank', ank)):
            ok = np.isfinite(p).all(1)
            q = np.full((n, 2), np.nan)
            if ok.any():
                ref = _warp(Mot[ok], p[ok])
                q[ok] = _apply_h(H, ref)
            d[f'{name}_pad'] = q
        vx, vy = _vel(g[:, 0], fps) / L, _vel(g[:, 1], fps) / L
        d['vx'], d['vy'] = vx, vy
        d['speed'] = np.hypot(vx, vy)
        d['acc'] = np.abs(np.gradient(d['speed']) * fps)
        # 離地高度代理：1 秒視窗內最低（y 最大）位置減目前 y（影像 y 向下）
        gy = _interp_nan(g[:, 1].copy())
        w = int(fps) | 1
        low = maximum_filter1d(gy, w)
        d['lift'] = (low - gy) / L
        d['tilt'] = (heel[:, 1] - toe[:, 1]) / L          # 腳跟比腳尖高 → 正
        # 踏板座標上的腳速度（格/秒）：不受拍攝方向影響
        tp_ = d['toe_pad']
        pvx, pvy = _vel(tp_[:, 0], fps), _vel(tp_[:, 1], fps)
        d['pspeed'] = np.clip(np.hypot(pvx, pvy), 0, 30)
        d['ay'] = np.gradient(vy) * fps
        kn = X[:, j['knee']]
        u_, w_ = X[:, j['hip']] - kn, ank - kn
        d['knee'] = np.degrees(np.arccos(np.clip((u_ * w_).sum(1) / (np.linalg.norm(u_, axis=1) * np.linalg.norm(w_, axis=1) + 1e-9), -1, 1)))
        d['conf'] = np.nanmin(np.c_[S[:, j['heel']], S[:, j['bt']], S[:, j['ankle']]], 1)
        d['rel'] = (rel[:, j['heel']] & rel[:, j['bt']]).astype(float)
        out[s] = d
    return out


def panel_dist(feet_pad, c):
    """腳（腳跟→腳尖線段延伸）到板中心的 Chebyshev 距離 − 0.5（≤0 表示在板內）。"""
    hl, tp = feet_pad
    v = tp - hl
    best = np.full(len(hl), np.nan)
    for a in np.linspace(-0.2, 1.2, 8):
        p = hl + a * v
        dd = np.maximum(np.abs(p[:, 0] - c[0]), np.abs(p[:, 1] - c[1])) - 0.5
        best = np.fmin(best, dd)
    return best


def prep_raw(K, S, valid, thr=0.3, max_gap=10):
    """和 prep_keypoints 一樣補缺點，但不平滑（短促的落地訊號會被 Savitzky–Golay 抹掉）。"""
    from scipy.ndimage import distance_transform_edt
    n = len(K)
    X = np.full((n, K.shape[1], 2), np.nan)
    rel = np.zeros((n, K.shape[1]), bool)
    for j in range(K.shape[1]):
        m = valid & (S[:, j] >= thr)
        if m.sum() < 2:
            continue
        for d in (0, 1):
            y = K[:, j, d].astype(float).copy()
            y[~m] = np.nan
            X[:, j, d] = _interp_nan(y)
        rel[:, j] = distance_transform_edt(~m) <= max_gap
    return X, rel


def build_features(K, S, valid, Mot, mot_ok, pts, fps, smooth=True, offs_s=OFFS, flow=None):
    """回傳 (F (N,4,D), names, play (N,), X)：每幀每板的特徵。"""
    n = len(K)
    play = _play_mask(K, S, valid, Mot, mot_ok, fps)
    valid = valid & play
    S = np.where(play[:, None], S, 0.0)
    K, S, _ = fix_left_right(K, S, valid)
    X, rel = prep_keypoints(K, S, valid) if smooth else prep_raw(K, S, valid)
    H = _h_from_points({k: np.asarray(v, float) for k, v in pts.items()})
    pf = per_foot(X, S, rel, Mot, H, fps)
    cols, names = [], []

    def add(name, arr):           # arr: (N,) 或 (N,4)
        a = np.asarray(arr, float)
        if a.ndim == 1:
            a = np.repeat(a[:, None], 4, 1)
        cols.append(a); names.append(name)

    offs = [int(round(o * fps)) for o in offs_s]
    for s in LEG:
        d = pf[s]
        dist = np.stack([panel_dist((d['heel_pad'], d['toe_pad']), ARROWS[k]) for k in PANEL_KEYS], 1)
        for k_, o in zip(offs_s, offs):
            tag = f'{s}{k_:+.3f}'
            add(f'dist_{tag}', _shift(dist, o))
            add(f'speed_{tag}', _shift(d['speed'], o))
            add(f'vy_{tag}', _shift(d['vy'], o))
            add(f'lift_{tag}', _shift(d['lift'], o))
            if EXTRA:
                add(f'pspeed_{tag}', _shift(d['pspeed'], o))
                add(f'ay_{tag}', _shift(d['ay'], o))
        for name in ('toe_pad', 'heel_pad', 'ank_pad'):
            rel_ = np.stack([d[name] - np.asarray(ARROWS[k]) for k in PANEL_KEYS], 1)   # (N,4,2)
            add(f'{name}x_{s}', rel_[..., 0]); add(f'{name}y_{s}', rel_[..., 1])
        for name in ('vx', 'acc', 'tilt', 'conf', 'rel') + (('knee',) if EXTRA else ()):
            add(f'{name}_{s}', d[name])
        if flow is not None:
            fl = flow[:, 0 if s == 'L' else 1].astype(float)          # (N,4) dx, dy, mag, low
            fm = np.nan_to_num(fl[:, 2], nan=np.nanmedian(fl[:, 2]))
            fy = np.nan_to_num(fl[:, 1], nan=0.0)
            fl_low = np.nan_to_num(fl[:, 3], nan=np.nanmedian(fl[:, 3]))
            for k_, o in zip(offs_s, offs):
                add(f'fmag_{s}{k_:+.3f}', _shift(fm, o))
                add(f'fdy_{s}{k_:+.3f}', _shift(fy, o))
                add(f'flow_{s}{k_:+.3f}', _shift(fl_low, o))
        # 速度的局部最小（落地瞬間）
        sp = d['speed']
        add(f'speedmin_{s}', sp - np.minimum.reduce([_shift(sp, o) for o in offs]))
    # 兩腳中哪一隻比較近
    dl = np.stack([panel_dist((pf['L']['heel_pad'], pf['L']['toe_pad']), ARROWS[k]) for k in PANEL_KEYS], 1)
    dr = np.stack([panel_dist((pf['R']['heel_pad'], pf['R']['toe_pad']), ARROWS[k]) for k in PANEL_KEYS], 1)
    add('dist_min', np.fmin(dl, dr)); add('dist_diff', dl - dr)
    for i, k in enumerate(PANEL_KEYS):
        oh = np.zeros((n, 4)); oh[:, i] = 1
        add(f'panel_{k}', oh)
    F = np.stack(cols, 2).astype(np.float32)
    return F, names, play, X


def load_video_features(vid_dir, pose_file, pts, **kw):
    """從 analyze 用的資料夾讀資料並算特徵。"""
    import json
    from pathlib import Path
    vid_dir = Path(vid_dir)
    meta = json.loads((vid_dir / 'meta.json').read_text(encoding='utf-8'))
    z = np.load(pose_file)
    t, K, S, valid = z['t'], z['kpts'].astype(float), z['scores'].astype(float), z['valid']
    fps = 1.0 / np.median(np.diff(t))
    Mot, ref, mot_ok = _load_motion(vid_dir, len(t), meta['width'])
    flow = None
    if FLOW:
        ff = vid_dir.parent / 'flow.npz'
        flow = np.load(ff)['flow'][:len(t)]
    F, names, play, X = build_features(K, S, valid, Mot, mot_ok, pts, fps, flow=flow, **kw)
    return F, names, play, fps
