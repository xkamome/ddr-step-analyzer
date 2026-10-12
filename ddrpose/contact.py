"""學習式落地判定：沒有踏板亮燈時，用骨架推估「哪一幀踩下哪一塊板」。

模型以有亮燈影片的亮燈起點當標籤訓練（experiments/learn_contact.py；依遊玩分組的留一支法評分），
存成 numpy 陣列（ddrpose/models/contact_gbdt.npz，含特徵設定與門檻），推論只用 numpy，不需要 lightgbm。

每一列＝(幀 t, 箭頭板 p)。特徵以「秒」為時間單位、以小腿長為長度單位，跨 fps／解析度可比。
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt, maximum_filter1d, uniform_filter1d

PANEL_KEYS = 'LDUR'
OFFS = (-0.2, -0.133, -0.067, 0.0, 0.067, 0.133, 0.2)   # 秒
MODEL = Path(__file__).parent / 'models' / 'contact_gbdt.npz'


def _shift(a, k):
    """a[t+k]（邊界取端點）。"""
    n = len(a)
    idx = np.clip(np.arange(n) + k, 0, n - 1)
    return a[idx]


def _vel(y, fps):
    from .analyze import _interp_nan
    return np.gradient(_interp_nan(y.copy())) * fps


def prep_raw(K, S, valid, thr=0.3, max_gap=10):
    """和 analyze.prep_keypoints 一樣補缺點，但不平滑（短促的落地訊號會被 Savitzky–Golay 抹掉）。"""
    from .analyze import _interp_nan
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


def per_foot(X, S, rel, Mot, H, fps):
    """每隻腳的逐幀量：踏板座標（腳尖、腳跟、腳踝）、影像速度、離地高度代理、信心。"""
    from .analyze import LEG, _apply_h, _interp_nan, _warp
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
                q[ok] = _apply_h(H, _warp(Mot[ok], p[ok]))
            d[f'{name}_pad'] = q
        vx, vy = _vel(g[:, 0], fps) / L, _vel(g[:, 1], fps) / L
        d['vx'], d['vy'] = vx, vy
        d['speed'] = np.hypot(vx, vy)
        d['acc'] = np.abs(np.gradient(d['speed']) * fps)
        # 離地高度代理：1 秒視窗內最低（y 最大）位置減目前 y（影像 y 向下）
        gy = _interp_nan(g[:, 1].copy())
        d['lift'] = (maximum_filter1d(gy, int(fps) | 1) - gy) / L
        d['tilt'] = (heel[:, 1] - toe[:, 1]) / L          # 腳跟比腳尖高 → 正
        # 踏板座標上的腳速度（格/秒）：不受拍攝方向影響
        tp_ = d['toe_pad']
        d['pspeed'] = np.clip(np.hypot(_vel(tp_[:, 0], fps), _vel(tp_[:, 1], fps)), 0, 30)
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
        best = np.fmin(best, np.maximum(np.abs(p[:, 0] - c[0]), np.abs(p[:, 1] - c[1])) - 0.5)
    return best


def build_features(K, S, valid, Mot, mot_ok, pts, fps, smooth=True, offs_s=OFFS, flow=None, extra=False):
    """回傳 (F (N,4,D), names, play (N,), X)：每幀每板的特徵。"""
    from .analyze import ARROWS, LEG, _h_from_points, _play_mask, fix_left_right, prep_keypoints
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
            if extra:
                add(f'pspeed_{tag}', _shift(d['pspeed'], o))
                add(f'ay_{tag}', _shift(d['ay'], o))
        for name in ('toe_pad', 'heel_pad', 'ank_pad'):
            rel_ = np.stack([d[name] - np.asarray(ARROWS[k]) for k in PANEL_KEYS], 1)   # (N,4,2)
            add(f'{name}x_{s}', rel_[..., 0]); add(f'{name}y_{s}', rel_[..., 1])
        for name in ('vx', 'acc', 'tilt', 'conf', 'rel') + (('knee',) if extra else ()):
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


def foot_flow(preview, K, S, valid, width):
    """腳部光流：以每隻腳（未平滑骨架）為中心取小區塊（縮到 ≤64 px），算前後幀 Farneback 光流。
    回傳 (N, 2 腳, 4) = [中位 dx, 中位 dy, 中位速度, 下半區塊中位速度]（小腿長/秒）。"""
    from .analyze import LEG, fix_left_right
    K, S, _ = fix_left_right(K, S, valid)
    X, _ = prep_raw(K, S, valid)
    tracks = {}
    for s, j in LEG.items():
        tracks[s] = ((X[:, j['heel']] + X[:, j['bt']] + X[:, j['st']]) / 3,
                     np.linalg.norm(X[:, j['knee']] - X[:, j['ankle']], axis=1))
    n = len(K)
    cap = cv2.VideoCapture(str(preview))
    sc = cap.get(cv2.CAP_PROP_FRAME_WIDTH) / width
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    shm = np.nanmedian(np.r_[tracks['L'][1], tracks['R'][1]]) * sc
    F = np.full((n, 2, 4), np.nan, np.float32)
    prev = None
    i = 0
    while i < n:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        if prev is not None:
            Hh, W = g.shape
            for si, s in enumerate('LR'):
                c, sh = tracks[s][0][i], tracks[s][1][i]
                if not np.isfinite(c).all():
                    continue
                L = (sh if np.isfinite(sh) else shm / sc) * sc
                r = int(max(10, 0.55 * L))
                x, y = int(c[0] * sc), int(c[1] * sc)
                x0, x1, y0, y1 = max(0, x - r), min(W, x + r), max(0, y - r), min(Hh, y + r)
                if x1 - x0 < 8 or y1 - y0 < 8:
                    continue
                a_, b_ = prev[y0:y1, x0:x1], g[y0:y1, x0:x1]
                z = 64.0 / max(a_.shape)
                if z < 1:
                    a_ = cv2.resize(a_, None, fx=z, fy=z, interpolation=cv2.INTER_AREA)
                    b_ = cv2.resize(b_, None, fx=z, fy=z, interpolation=cv2.INTER_AREA)
                else:
                    z = 1.0
                fl = cv2.calcOpticalFlowFarneback(a_, b_, None, 0.5, 2, 9, 3, 5, 1.1, 0) / z
                mag = np.hypot(fl[..., 0], fl[..., 1])
                k = fps / max(L, 1.0)
                F[i, si] = [np.median(fl[..., 0]) * k, np.median(fl[..., 1]) * k, np.median(mag) * k,
                            np.median(mag[mag.shape[0] // 2:]) * k]
        prev = g
        i += 1
    cap.release()
    return F


def gbdt_predict(m, X):
    """LightGBM（binary）樹模型的 numpy 推論。m：export_gbdt 存的陣列；X (N, D) → 機率 (N,)。"""
    feat, thr, left, right = m['feat'], m['thr'], m['left'], m['right']
    dleft, mtype, leaf = m['dleft'], m['mtype'], m['leaf']
    X = np.asarray(X, float)
    n = len(X)
    raw = np.zeros(n)
    rows = np.arange(n)
    for r in m['roots']:
        node = np.full(n, r, np.int64)
        while True:
            f = feat[node]
            act = f >= 0
            if not act.any():
                break
            ia = np.where(act)[0]
            nd = node[ia]
            x = X[rows[ia], f[ia]]
            isn = np.isnan(x)
            mt = mtype[nd]
            go_left = np.where(isn, 0.0, x) <= thr[nd]
            # 缺值：NaN 型 → 走預設方向；Zero 型 → NaN 或 0 走預設方向；None 型 → NaN 當 0 比較
            dflt = (isn & (mt == 2)) | ((isn | (x == 0)) & (mt == 1))
            go_left = np.where(dflt, dleft[nd], go_left)
            node[ia] = np.where(go_left, left[nd], right[nd])
        raw += leaf[node]
    return 1.0 / (1.0 + np.exp(-raw))


def peaks_from_prob(P, thr=0.5, sep_s=0.1, fps=30.0, valley=None):
    """每塊板機率的局部最大值（> thr，間隔 ≥ sep_s）→ [(frame, panel_idx)]。
    valley：同一塊板兩個峰之間的最低點要 < valley × 較低的峰，才算兩次踩（否則只留較高的峰）。"""
    w = max(3, int(round(sep_s * fps)) * 2 + 1)
    ev = []
    for p in range(P.shape[1]):
        x = P[:, p]
        idx = np.where((x >= maximum_filter1d(x, w)) & (x > thr))[0]
        last = -10 ** 9
        pk = []
        for i in idx:
            if i - last >= w // 2:
                pk.append(int(i)); last = i
        if valley is not None and pk:
            merged = [pk[0]]
            for i in pk[1:]:
                j = merged[-1]
                if x[j:i + 1].min() >= valley * min(x[i], x[j]):
                    if x[i] > x[j]:
                        merged[-1] = i
                else:
                    merged.append(i)
            pk = merged
        ev += [(i, p) for i in pk]
    return sorted(ev)


def load_model(path=MODEL):
    if not Path(path).exists():
        return None
    z = np.load(path)
    m = {k: z[k] for k in z.files if k != 'meta'}
    m['meta'] = json.loads(str(z['meta']))
    return m


def detect_presses(K, S, valid, Mot, mot_ok, pts, fps, preview=None, width=None, model=None):
    """→ (events [(frame, 'L'|'D'|'U'|'R')], P (N,4) 機率)；沒有模型時回傳 (None, None)。"""
    model = model or load_model()
    if model is None:
        return None, None
    cfg = model['meta']
    flow = None
    if cfg.get('flow'):
        if preview is None or not Path(preview).exists():
            return None, None
        flow = foot_flow(preview, K, S, valid, width)
    offs = tuple(cfg['offs']) if cfg.get('offs') else OFFS
    F, names, play, _ = build_features(K, S, valid, Mot, mot_ok, pts, fps, smooth=cfg.get('smooth', False),
                                       offs_s=offs, flow=flow, extra=cfg.get('extra', False))
    if names != cfg['names']:
        raise ValueError('contact model feature mismatch')
    n = len(F)
    P = gbdt_predict(model, F.reshape(-1, F.shape[2])).reshape(n, 4)
    P[~play] = 0.0
    th = cfg['threshold']
    ev = peaks_from_prob(P, th[0], th[1], fps, valley=th[2] if len(th) > 2 else None)
    return [(f, PANEL_KEYS[p]) for f, p in ev], P
