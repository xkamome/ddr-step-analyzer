"""分析：左右腳校正 → 平滑 → 落地偵測 → 踏板校正（自動／手動）→ 重心 → 指標 → 建議。

踏板座標（單位：格）：中央板中心為 (0,0)，x 往玩家右側為正，y 往後（↓）為正。
  ← (-1,0)   → (1,0)   ↑ (0,-1)   ↓ (0,1)；踏板範圍 |x|,|y| ≤ 1.5。
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt, median_filter, uniform_filter1d
from scipy.signal import savgol_filter

PANEL_CM = 28        # 街機單人踏板約 85 cm 見方 → 一格約 28 cm（估）
SHANK_CM = 38        # 換算抬腳高度用的小腿長假設（估）
FOOT_CM = 25         # 腳跟到腳尖長度假設（估），換算腳跟離地高度用
HEEL_UP_CM = 2.5     # 腳跟離地超過此高度視為「踮腳（前腳掌著地）」

LEG = {'L': dict(hip=11, knee=13, ankle=15, bt=20, st=22, heel=24),
       'R': dict(hip=12, knee=14, ankle=16, bt=21, st=23, heel=25)}
SWAP_PAIRS = [(13, 14), (15, 16), (20, 21), (22, 23), (24, 25)]
ARROWS = {'L': (-1.0, 0.0), 'D': (0.0, 1.0), 'U': (0.0, -1.0), 'R': (1.0, 0.0)}
ARROW_SYM = {'L': '←', 'D': '↓', 'U': '↑', 'R': '→'}
CELL_NAME = {(-1, 0): '←', (1, 0): '→', (0, -1): '↑', (0, 1): '↓', (0, 0): '中央'}

# 高階玩家（15 級以上）門檻；[良好上限, 注意上限]，超過注意上限即「需改善」
TH = {
    'com_sway': (0.22, 0.35),        # 重心左右晃動標準差（格）
    'com_bias': (0.12, 0.25),        # 重心平均偏離中線（格）
    'com_fwd_bias': (0.15, 0.30),    # 重心前後平均偏離（格，側面視角）
    'bounce_pct': (2.0, 3.5),        # 骨盆上下彈跳標準差（% 腿長）
    'lift_cm': (7.0, 11.0),          # 抬腳高度中位數（cm，估）
    'edge_pct': (12.0, 25.0),        # 落點貼近板緣的比例（%）
    'spread_cm': (6.0, 9.0),         # 同一個箭頭的落點散佈（cm）
    'offpanel_pct': (5.0, 12.0),     # 踩在角落／板外的比例（%）
}


# ───────────────────────── 基本工具 ─────────────────────────

warnings.filterwarnings('ignore', category=RuntimeWarning)

def _interp_nan(y):
    m = ~np.isnan(y)
    if m.sum() < 2:
        return y
    idx = np.arange(len(y))
    return np.interp(idx, idx[m], y[m])


def _smooth(y, win=5, poly=2):
    if len(y) < win or np.isnan(y).all():
        return y
    return savgol_filter(y, win, poly)


def _runs(mask):
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1)); start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def _apply_h(H, pts):
    pts = np.asarray(pts, float).reshape(-1, 2)
    ph = np.c_[pts, np.ones(len(pts))] @ H.T
    return ph[:, :2] / ph[:, 2:3]


def _warp(M, pts):
    """逐幀套用 3x3 矩陣：M (n,3,3)，pts (n,2)。"""
    ph = np.einsum('nij,nj->ni', M, np.c_[pts, np.ones(len(pts))])
    return ph[:, :2] / ph[:, 2:3]


def _cross_pt(p1, p2, p3, p4):
    """直線 p1p2 與 p3p4 的交點（平行時回傳 None）。"""
    l1 = np.cross([*p1, 1], [*p2, 1])
    l2 = np.cross([*p3, 1], [*p4, 1])
    x = np.cross(l1, l2)
    if abs(x[2]) < 1e-9:
        return None
    return x[:2] / x[2]


def _level(v, key, higher_is_worse=True):
    good, warn = TH[key]
    if not higher_is_worse:
        v = -v; good, warn = -good, -warn
    return 'good' if v <= good else ('warn' if v <= warn else 'bad')


def _r(x, d=3):
    if x is None:
        return None
    if isinstance(x, (float, np.floating)) and not np.isfinite(x):
        return None
    return round(float(x), d)


# ───────────────────────── 骨架前處理 ─────────────────────────

def fix_left_right(K, S, valid, prior=0.15):
    """Viterbi：讓左右腳標籤在時間上連續（修正模型偶發的左右腳互換）。"""
    idx = np.where(valid)[0]
    swapped = np.zeros(len(K), bool)
    if len(idx) < 2:
        return K, S, swapped
    L = [a for a, _ in SWAP_PAIRS]; R = [b for _, b in SWAP_PAIRS]
    scale = max(float(np.median(np.linalg.norm(K[idx, 13] - K[idx, 15], axis=1))), 1.0)
    A, B = K[idx][:, L], K[idx][:, R]
    m = len(idx)
    cost = np.zeros((m, 2)); back = np.zeros((m, 2), int)
    cost[0] = [0, prior]
    for i in range(1, m):
        d_same = (np.linalg.norm(A[i] - A[i - 1], axis=1).mean() +
                  np.linalg.norm(B[i] - B[i - 1], axis=1).mean()) / scale
        d_cross = (np.linalg.norm(A[i] - B[i - 1], axis=1).mean() +
                   np.linalg.norm(B[i] - A[i - 1], axis=1).mean()) / scale
        for s in (0, 1):
            c0 = cost[i - 1, 0] + (d_same if s == 0 else d_cross)
            c1 = cost[i - 1, 1] + (d_cross if s == 0 else d_same)
            back[i, s] = 0 if c0 <= c1 else 1
            cost[i, s] = min(c0, c1) + prior * s
    st = np.zeros(m, int); st[-1] = int(np.argmin(cost[-1]))
    for i in range(m - 1, 0, -1):
        st[i - 1] = back[i, st[i]]
    swapped[idx] = st.astype(bool)
    return _swap_legs(K, S, swapped) + (swapped,)


def _swap_legs(K, S, mask):
    K2, S2 = K.copy(), S.copy()
    for a, b in SWAP_PAIRS:
        K2[mask, a], K2[mask, b] = K[mask, b], K[mask, a]
        S2[mask, a], S2[mask, b] = S[mask, b], S[mask, a]
    return K2, S2


def prep_keypoints(K, S, valid, thr=0.3, max_gap=10):
    """低信心點以線性內插補齊、Savitzky–Golay 平滑；rel 標示距離可信幀 ≤ max_gap。"""
    n = len(K)
    X = np.full((n, 26, 2), np.nan)
    rel = np.zeros((n, 26), bool)
    for j in range(26):
        m = valid & (S[:, j] >= thr)
        if m.sum() < 2:
            continue
        for d in (0, 1):
            y = K[:, j, d].astype(float).copy()
            y[~m] = np.nan
            X[:, j, d] = _smooth(_interp_nan(y))
        rel[:, j] = distance_transform_edt(~m) <= max_gap
    return X, rel


# ───────────────────────── 垂直方向（消失點） ─────────────────────────

def estimate_vertical(bg, scale):
    """用背景中的長直線（扶桿、機台邊）估計垂直消失點（齊次座標，原始解析度）。
    bg 是對齊後的背景圖（預覽解析度，scale = 預覽/原始）；失敗回傳 None（視為影像垂直）。"""
    if bg is None:
        return None
    gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    h = gray.shape[0]
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, 60, minLineLength=int(0.15 * h), maxLineGap=25)
    if lines is None:
        return None
    L, wts = [], []
    for x1, y1, x2, y2 in lines.reshape(-1, 4).astype(float):
        if np.degrees(np.arctan2(abs(x2 - x1), abs(y2 - y1))) < 15:
            L.append(np.cross([x1 / scale, y1 / scale, 1.0], [x2 / scale, y2 / scale, 1.0]))
            wts.append(np.hypot(x2 - x1, y2 - y1))
    if len(L) < 3:
        return None
    L = np.array(L); L /= np.linalg.norm(L[:, :2], axis=1, keepdims=True)
    _, _, vt = np.linalg.svd(L * np.array(wts)[:, None])
    vp = vt[-1]
    # 各線與消失點的一致性：夾角殘差太大就放棄
    if abs(vp[2]) > 1e-9:
        q = vp[:2] / vp[2]
        res = np.abs(L @ np.r_[q, 1]) / (np.linalg.norm(q) + 1e-9)   # 約等於夾角（弧度）
        if np.median(res) > 0.02:
            return None
    return vp / np.linalg.norm(vp)


def _vert_dir(p, vp):
    """在點 p 往地面的單位方向（vp 為齊次座標的垂直消失點）。"""
    if vp is None:
        return np.array([0.0, 1.0])
    if abs(vp[2]) < 1e-6:
        d = vp[:2].copy()
    else:
        d = vp[:2] / vp[2] - p
    d = d / (np.linalg.norm(d) + 1e-9)
    return d if d[1] > 0 else -d


# ───────────────────────── 落地偵測 ─────────────────────────

def detect_contacts(g, S_loc, fps, enter=1.0, exit_=2.2, min_len=2):
    n = len(g)
    ok = ~np.isnan(g).any(1)
    gx, gy = _interp_nan(g[:, 0].copy()), _interp_nan(g[:, 1].copy())
    win = 7 if n >= 7 else (n // 2) * 2 - 1
    vx = savgol_filter(gx, win, 2, deriv=1) * fps
    vy = savgol_filter(gy, win, 2, deriv=1) * fps
    v = np.hypot(vx, vy) / S_loc
    planted = np.zeros(n, bool)
    state = False
    for i in range(n):
        if not ok[i]:
            state = False
        elif state:
            state = v[i] <= exit_
        else:
            state = v[i] < enter
        planted[i] = state
    runs = [r for r in _runs(planted) if r[1] - r[0] + 1 >= min_len]
    # 兩段落地之間只隔 1–2 幀且位置幾乎不變 → 視為同一次（雜訊）
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= 3:
            p0, p1 = g[merged[-1][1]], g[r[0]]
            if np.linalg.norm(p1 - p0) < 0.15 * S_loc[r[0]]:
                merged[-1] = (merged[-1][0], r[1]); continue
        merged.append(r)
    planted[:] = False
    for a, b in merged:
        planted[a:b + 1] = True
    return v, merged, planted


# ───────────────────────── 踏板校正 ─────────────────────────

def _forward_dir(X, planted):
    vs = []
    for side, j in LEG.items():
        toe = (X[:, j['bt']] + X[:, j['st']]) / 2
        v = toe - X[:, j['heel']]
        m = planted[side] & ~np.isnan(v).any(1)
        v = v[m]
        n = np.linalg.norm(v, axis=1, keepdims=True)
        vs.append(v[n[:, 0] > 1] / n[n[:, 0] > 1])
    v = np.concatenate(vs) if vs else np.zeros((0, 2))
    if len(v) < 5:
        return np.array([0.0, -1.0])
    f = v.mean(0)
    return f / (np.linalg.norm(f) + 1e-9)


def _is_side(P, f):
    Ld = np.array([f[1], -f[0]])
    rel = P - np.median(P, 0)
    a, b = rel @ Ld, rel @ f
    sa = np.percentile(a, 90) - np.percentile(a, 10)
    sb = np.percentile(b, 90) - np.percentile(b, 10)
    return sa < 0.25 * sb


def _h_from_points(pts):
    src = np.float32([pts[k] for k in 'LDUR'])
    dst = np.float32([ARROWS[k] for k in 'LDUR'])
    return cv2.getPerspectiveTransform(src, dst)


def auto_homography(P, f):
    """由所有落點分佈推估四個箭頭在畫面上的位置，再算單應矩陣（影像 → 踏板）。"""
    Ld = np.array([f[1], -f[0]])   # 玩家左方（在畫面中的方向）
    c = np.median(P, 0)
    pts = {}
    for _ in range(8):
        rel = P - c
        a, b = rel @ Ld, rel @ f
        sa = np.percentile(np.abs(a), 80) + 1e-6
        sb = np.percentile(np.abs(b), 80) + 1e-6
        an, bn = a / sa, b / sb
        r = np.hypot(an, bn)
        th = np.degrees(np.arctan2(bn, an))
        outer = r > 0.45
        sect = {'L': outer & (np.abs(th) < 45),
                'U': outer & (th >= 45) & (th < 135),
                'R': outer & (np.abs(th) >= 135),
                'D': outer & (th > -135) & (th <= -45)}
        pts = {k: (np.median(P[m], 0) if m.sum() >= 3 else None) for k, m in sect.items()}
        pts = _fill_missing(pts, c)
        if pts is None:
            return None
        c2 = _cross_pt(pts['L'], pts['R'], pts['U'], pts['D'])
        if c2 is None:
            c2 = np.mean([pts[k] for k in 'LDUR'], 0)
        if np.linalg.norm(c2 - c) < 1:
            break
        c = c2
    H = _h_from_points(pts)
    # 以踏板座標重新分群，迭代修正四個箭頭的位置
    for _ in range(3):
        Q = _apply_h(H, P)
        cell = np.clip(np.round(Q), -1, 1)
        new = {}
        for k, (ax, ay) in ARROWS.items():
            m = (cell[:, 0] == ax) & (cell[:, 1] == ay)
            new[k] = np.median(P[m], 0) if m.sum() >= 3 else pts[k]
        pts = new
        H = _h_from_points(pts)
    return {k: v.tolist() for k, v in pts.items()}


def _fill_missing(pts, c):
    miss = [k for k, v in pts.items() if v is None]
    if len(miss) > 1:
        return None
    opp = {'L': 'R', 'R': 'L', 'U': 'D', 'D': 'U'}
    for k in miss:
        pts[k] = 2 * c - pts[opp[k]]
    return pts


def _kmeans1d(x, k=3, it=30):
    c = np.percentile(x, np.linspace(10, 90, k))
    for _ in range(it):
        lab = np.argmin(np.abs(x[:, None] - c[None]), 1)
        c2 = np.array([x[lab == i].mean() if (lab == i).any() else c[i] for i in range(k)])
        if np.allclose(c2, c):
            break
        c = c2
    return np.sort(c)


# ───────────────────────── 重心 ─────────────────────────

def compute_com(X, rel, S, W, H, hidden=()):
    """Winter 人體分段質量模型（總和 1.0）：
      軀幹 0.497（骨盆→肩膀中點 50%）、頭頸 0.081（沿軀幹方向、肩膀上方 30% 軀幹長）、
      上臂 0.028／前臂 0.016／手 0.006（每側）、大腿 0.100／小腿 0.0465／腳 0.0145（每側）。
    看不到（畫面外、貼邊、疊到另一隻手上）的手臂不用它的點：改用看得到那隻手臂的角度對軀幹中線鏡像；
    兩隻都看不到就當作自然下垂。只有手腕看得到（手肘被畫面切掉）時，手腕用實際點、手肘取肩膀與手腕中點。
    hidden：使用者指定不在畫面裡的手臂（'L'/'R'），完全不用它的點。
    某側肩膀不可靠時，軀幹方向改用另一側「肩→髖」。
    回傳 (com, mode, 手臂是否為推估 {L,R: (N,) bool})。"""
    n = len(X)
    ok = lambda j: rel[:, j] & (S[:, j] >= 0.5)
    edge = lambda j: ((X[:, j, 0] < 0.02 * W) | (X[:, j, 0] > 0.98 * W) | (X[:, j, 1] < 0.015 * H) | (X[:, j, 1] > 0.985 * H))
    hipL, hipR = X[:, 11], X[:, 12]
    hip = (hipL + hipR) / 2
    sh_ok = {'L': ok(5) & ~edge(5), 'R': ok(6) & ~edge(6)}
    # 軀幹向量（骨盆→肩膀中點）
    tv = np.full((n, 2), np.nan)
    both = sh_ok['L'] & sh_ok['R']
    tv[both] = ((X[:, 5] + X[:, 6]) / 2 - hip)[both]
    only = {'L': sh_ok['L'] & ~sh_ok['R'], 'R': sh_ok['R'] & ~sh_ok['L']}
    tv[only['L']] = (X[:, 5] - hipL)[only['L']]
    tv[only['R']] = (X[:, 6] - hipR)[only['R']]
    mode = 'trunk' if np.isfinite(tv[:, 0]).mean() > 0.3 else 'pelvis'
    if mode == 'trunk':
        tv = np.c_[_interp_nan(tv[:, 0].copy()), _interp_nan(tv[:, 1].copy())]
    else:
        thigh = float(np.nanmedian(np.linalg.norm(X[:, 11] - X[:, 13], axis=1)))
        tv = np.tile([0.0, -1.1 * (thigh if np.isfinite(thigh) else 100.0)], (n, 1))
    shm = hip + tv
    # 左肩→右肩向量（兩肩都可靠時量，其餘內插）
    sw = np.full((n, 2), np.nan)
    sw[both] = (X[:, 6] - X[:, 5])[both]
    if np.isfinite(sw[:, 0]).sum() >= 2:
        sw = np.c_[_interp_nan(sw[:, 0].copy()), _interp_nan(sw[:, 1].copy())]
    else:
        sw = np.tile([0.6 * np.nanmedian(np.linalg.norm(tv, axis=1)), 0.0], (n, 1))
    sh = {'L': np.where(sh_ok['L'][:, None], X[:, 5], shm - sw / 2),
          'R': np.where(sh_ok['R'][:, None], X[:, 6], shm + sw / 2)}
    shw = np.linalg.norm(sw, axis=1) + 1e-6
    # 手臂：可靠 → 實際點；只有手腕可靠 → 手腕實際點＋手肘取肩膀與手腕中點（扶桿時手臂接近伸直）；
    # 不可靠或指定不在畫面 → 另一隻手臂對軀幹中線鏡像；都不可靠 → 自然下垂
    ARMJ = {'L': (7, 9), 'R': (8, 10)}
    wr_ok, own_e = {}, {}
    for s_, (el, wr) in ARMJ.items():
        o = 'R' if s_ == 'L' else 'L'
        o_wr = ARMJ[o][1]
        # 兩手腕疊在一起：通常是模型把看不到的手畫到看得到的手上 → 只判信心較低的那隻不可靠
        # （另一隻已指定不在畫面時不用比，它的點本來就是模型猜的）
        collapsed = (np.linalg.norm(X[:, wr] - X[:, o_wr], axis=1) < 0.35 * shw) & (S[:, wr] < S[:, o_wr]) & (o not in hidden)
        wr_ok[s_] = ok(wr) & ~edge(wr) & (S[:, wr] >= 0.55) & ~collapsed & (s_ not in hidden)
        el_ok = wr_ok[s_] & ok(el) & ~edge(el) & sh_ok[s_]
        own_e[s_] = np.where(el_ok[:, None], X[:, el], (sh[s_] + X[:, wr]) / 2)
    ax = tv / (np.linalg.norm(tv, axis=1, keepdims=True) + 1e-9)
    refl = lambda v: 2 * (v * ax).sum(1, keepdims=True) * ax - v
    elbow, wrist, est = {}, {}, {}
    for s_, (el, wr) in ARMJ.items():
        o = 'R' if s_ == 'L' else 'L'
        owr = ARMJ[o][1]
        e_m = sh[s_] + refl(own_e[o] - sh[o])
        w_m = e_m + refl(X[:, owr] - own_e[o])
        e_h = sh[s_] - 0.45 * tv                          # 自然下垂
        w_h = e_h - 0.40 * tv
        use_m = ~wr_ok[s_] & wr_ok[o]
        elbow[s_] = np.where(wr_ok[s_][:, None], own_e[s_], np.where(use_m[:, None], e_m, e_h))
        wrist[s_] = np.where(wr_ok[s_][:, None], X[:, wr], np.where(use_m[:, None], w_m, w_h))
        est[s_] = ~wr_ok[s_]
    com = 0.497 * (hip + 0.5 * tv) + 0.081 * (shm + 0.3 * tv)
    for s_ in 'LR':
        e, w_ = elbow[s_], wrist[s_]
        com = com + 0.028 * (sh[s_] + 0.436 * (e - sh[s_])) + 0.016 * (e + 0.43 * (w_ - e)) + 0.006 * w_
    for side, j in LEG.items():
        hp, kn, an = X[:, j['hip']], X[:, j['knee']], X[:, j['ankle']]
        foot = (X[:, j['heel']] + X[:, j['bt']] + X[:, j['st']]) / 3
        com = com + 0.100 * (hp + 0.433 * (kn - hp)) + 0.0465 * (kn + 0.433 * (an - kn)) + 0.0145 * foot
    return com, mode, est


def ground_at_feet_depth(p, gL, gR, vp):
    """點 p 沿垂直方向往下，落在與兩腳（平均）同深度的位置。只有沿畫面水平方向的分量是量得到的。"""
    n = len(p)
    out = np.full((n, 2), np.nan)
    m = np.where(np.isnan(gL).any(1)[:, None], gR, np.where(np.isnan(gR).any(1)[:, None], gL, (gL + gR) / 2))
    for i in range(n):
        if np.isnan(p[i]).any() or np.isnan(m[i]).any():
            continue
        d = _vert_dir(p[i], vp)
        out[i] = p[i] + d * ((m[i] - p[i]) @ d)
    return out


def ground_under(p, gL, gR, vp, S_loc):
    """點 p 沿垂直方向落到地面的位置：與兩腳連線的交點（假設重心投影落在兩腳之間）。"""
    n = len(p)
    out = np.full((n, 2), np.nan)
    for i in range(n):
        if np.isnan(p[i]).any():
            continue
        d = _vert_dir(p[i], vp)
        a, b = gL[i], gR[i]
        if np.isnan(a).any() and np.isnan(b).any():
            continue
        if np.isnan(a).any() or np.isnan(b).any():
            q = b if np.isnan(a).any() else a
            out[i] = p[i] + d * ((q - p[i]) @ d); continue
        seg = b - a
        # 腳連線幾乎和垂直線平行 → 退回到兩腳平均深度
        cross = seg[0] * d[1] - seg[1] * d[0]
        if abs(cross) < 0.25 * np.linalg.norm(seg) or np.linalg.norm(seg) < 0.2 * S_loc[i]:
            m = (a + b) / 2
            out[i] = p[i] + d * ((m - p[i]) @ d); continue
        # 解 p + s·d = a + t·seg
        M = np.array([[d[0], -seg[0]], [d[1], -seg[1]]])
        s, t = np.linalg.solve(M, a - p[i])
        t = np.clip(t, -0.3, 1.3)
        q = a + t * seg
        out[i] = p[i] + d * ((q - p[i]) @ d)
    return out


# ───────────────────────── 腳的覆蓋範圍與亮燈擬合 ─────────────────────────

def _foot_samples(hl, tp, eh=0.0, et=0.0, n=9):
    """腳跟→腳尖（各自往外延伸 eh、et 倍腳長）上等距取 n 點。"""
    hl, tp = np.asarray(hl, float), np.asarray(tp, float)
    v = tp - hl
    a, b = hl - eh * v, tp + et * v
    return a + np.linspace(0, 1, n)[:, None] * (b - a)


def classify_foot(hp, tp, eh=0.0, et=0.0, target=None, from_ext=True, heel_up=False):
    """整隻腳壓到哪塊箭頭板（高手常只用腳跟踩 ↓、腳尖踩 ↑，所以不能只看前腳掌）。
    target：已知（亮燈）的箭頭，改為計算腳在該板上的位置與使用部位。"""
    foot = _foot_samples(hp, tp, eh, et)
    ts = np.linspace(0, 1, len(foot))
    cells = [(int(np.round(x)), int(np.round(y))) if max(abs(x), abs(y)) <= 1.5 else None for x, y in foot]
    hits = {}
    for (x, y), cl, tt in zip(foot, cells, ts):
        if heel_up and tt < 0.45:
            continue                      # 腳跟離地時只有前腳掌能施力
        if cl in CELL_NAME and cl != (0, 0):
            hits.setdefault(cl, []).append((0.5 - max(abs(x - cl[0]), abs(y - cl[1])), x, y, tt))
    out = {'heel_ext': [_r(foot[0][0]), _r(foot[0][1])], 'toe_ext': [_r(foot[-1][0]), _r(foot[-1][1])]}
    if target is not None:
        cl = next(tuple(int(v) for v in ARROWS[k]) for k, sym in ARROW_SYM.items() if sym == target)
        out['panel'] = target
        if cl not in hits and heel_up:
            # 亮燈的板只有腳跟碰得到 → 以亮燈為準：腳跟其實有踩下去
            return {**classify_foot(hp, tp, eh, et, target=target, heel_up=False), 'heel_conflict': True}
        if cl not in hits:
            # 亮燈了，但骨架上的腳沒有碰到那塊板（多半是骨架點偏差或遮擋）
            dd = np.maximum(np.abs(foot[:, 0] - cl[0]), np.abs(foot[:, 1] - cl[1])) - 0.5
            j = int(np.argmin(dd))
            out.update(depth=_r(-dd[j]), cover=0.0, part='未碰到', pad=[_r(foot[j][0]), _r(foot[j][1])])
            return out
    elif hits:
        cl = max(hits, key=lambda k: (len(hits[k]), max(h[0] for h in hits[k])))
        out['panel'] = CELL_NAME[cl]
    else:
        out['panel'] = '板外' if all(c is None for c in cells) else ('中央' if (0, 0) in cells else '角落')
        return out
    on = np.array([[h[1], h[2]] for h in hits[cl]])
    tt = np.array([h[3] for h in hits[cl]])
    cover = len(on) / len(foot)
    part = '全腳' if cover >= 0.75 else ('腳跟' if tt.mean() < 0.4 else ('腳尖' if tt.mean() > 0.6 else '腳掌中段'))
    out.update(depth=_r(max(h[0] for h in hits[cl])), cover=_r(cover), part=part,
               pad=[_r(on[:, 0].mean()), _r(on[:, 1].mean())])
    return out


def fit_foot_and_grid(samples, pts, refine_grid=True, offset=(0.0, 0.0)):
    """以亮燈步為標準答案：網格搜尋腳跟／腳尖延伸量；自動校正時再以座標下降微調四個箭頭點
    （每點最多移動 0.3 格）。回傳 (eh, et, pts, [調整前一致率, 調整後一致率])。"""
    heels = np.array([s_[0] for s_ in samples]); toes = np.array([s_[1] for s_ in samples])
    syms = [s_[2] for s_ in samples]
    sym2k = {sym: i for i, sym in enumerate(ARROW_SYM[k] for k in 'LDUR')}
    lit = np.array([sym2k[x] for x in syms])
    centers = np.array([ARROWS[k] for k in 'LDUR'])
    ts = np.linspace(0, 1, 9)

    def score(P, eh, et):
        H = _h_from_points(P)
        hp, tp = _apply_h(H, heels) + offset, _apply_h(H, toes) + offset
        v = tp - hp
        a, b = hp - eh * v, tp + et * v
        F = a[:, None] + ts[None, :, None] * (b - a)[:, None]            # (m, 9, 2)
        cnt, soft = [], []
        for c in centers:
            dinf = np.maximum(np.abs(F[..., 0] - c[0]), np.abs(F[..., 1] - c[1]))
            cnt.append((dinf <= 0.5).sum(1))
            soft.append((1 / (1 + np.exp((dinf - 0.5) / 0.05))).mean(1))
        cnt = np.array(cnt).T; soft = np.array(soft).T                    # (m, 4)
        pred = np.where(cnt.max(1) > 0, cnt.argmax(1), -1)
        agree = (pred == lit)
        return agree.sum() + 0.05 * soft[np.arange(len(lit)), lit].sum(), agree.mean()

    def best_ext(P):
        best = None
        for eh in np.arange(0, 0.81, 0.1):
            for et in np.arange(0, 0.51, 0.1):
                sc, ag = score(P, eh, et)
                sc -= 0.3 * (eh + et)                   # 同分時偏好較小的延伸
                if best is None or sc > best[0]:
                    best = (sc, float(eh), float(et), ag)
        return best

    P0 = {k: np.asarray(v, float) for k, v in pts.items() if k in 'LDUR'}
    agree0 = score(P0, 0.0, 0.0)[1]
    _, eh, et, _ = best_ext(P0)
    P = {k: v.copy() for k, v in P0.items()}
    if refine_grid:
        panel_px = np.linalg.norm(P0['U'] - P0['D']) / 2
        lim = 0.3 * panel_px
        for _round in range(2):
            for step in (0.08 * panel_px, 0.04 * panel_px, 0.02 * panel_px):
                cur = score(P, eh, et)[0]
                for _ in range(8):
                    improved = False
                    for k in 'LDUR':
                        for d in range(2):
                            for sgn in (1, -1):
                                Q = {kk: vv.copy() for kk, vv in P.items()}
                                Q[k][d] += sgn * step
                                if np.linalg.norm(Q[k] - P0[k]) > lim:
                                    continue
                                sc = score(Q, eh, et)[0]
                                if sc > cur + 1e-6:
                                    P, cur, improved = Q, sc, True
                    if not improved:
                        break
            _, eh, et, _ = best_ext(P)
    agree1 = score(P, eh, et)[1]
    return eh, et, P, [_r(agree0 * 100, 1), _r(agree1 * 100, 1)]


# ───────────────────────── 腳跟離地 ─────────────────────────

def heel_lift_series(X, S, shank, planted):
    """每幀每腳的原始訊號：腳跟→腳尖在畫面上的傾角 pitch（正值＝腳跟比腳尖高）、
    踝關節角度 ank（膝–踝–腳尖）、ok（腳在畫面上沒被壓縮、點的信心夠）。
    另附整段的「踩平」基準（落地幀的 15 百分位），當作沒有分板基準時的備援。

    腳跟離地高度 ≈ 腳長 × tan(pitch − 踩平基準)。因為透視，不同位置的板「踩平」時的傾角不同，
    所以正式計算用每隻腳、每塊板各自的基準（見 analyze 的三階段流程）。"""
    out = {}
    for s_, j in LEG.items():
        hl = X[:, j['heel']]; tp = (X[:, j['bt']] + X[:, j['st']]) / 2
        an, kn = X[:, j['ankle']], X[:, j['knee']]
        v = tp - hl
        pitch = np.degrees(np.arctan2(v[:, 1], np.abs(v[:, 0]) + 1e-6))
        u, w = kn - an, tp - an
        ank = np.degrees(np.arccos(np.clip((u * w).sum(1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(w, axis=1) + 1e-9), -1, 1)))
        ratio = np.linalg.norm(v, axis=1) / (shank[s_] * FOOT_CM / SHANK_CM + 1e-6)   # 1 ≈ 腳與畫面平行
        ok = np.isfinite(pitch) & (ratio > 0.6) & (S[:, j['heel']] > 0.5) & (S[:, j['bt']] > 0.5)
        good = ok & planted[s_]
        flat = float(np.percentile(pitch[good], 15)) if good.sum() >= 20 else np.nan
        flat_ank = float(np.percentile(ank[good], 15)) if good.sum() >= 20 else np.nan
        out[s_] = dict(pitch=pitch, ank=ank, ok=ok, flat=flat, flat_ank=flat_ank)
    return out


def step_confidence(st, S, frame, foot):
    """踩法判斷的可信度：亮燈確認、腳部點信心、腳的朝向可量、腳跟指標一致、壓進板內的深度。"""
    j = LEG[foot]
    sc = float(np.mean([S[frame, j['heel']], S[frame, j['bt']], S[frame, j['ankle']]]))
    pts = 0
    pts += 1 if st.get('lit') else 0
    pts += 1 if sc >= 0.6 else 0
    pts += 1 if st.get('_yaw_ok') else 0
    pts += 1 if st.get('_agree') else 0
    pts += 1 if (st.get('depth') or 0) >= 0.08 else 0
    pts -= 1 if st.get('from_light') else 0
    return 'high' if pts >= 4 else ('medium' if pts >= 2 else 'low')


# ───────────────────────── 主流程 ─────────────────────────

def analyze(vid_dir: str | Path, calib: dict | None = None, _swap_all: bool = False,
            _refined: bool = False):
    vid_dir = Path(vid_dir)
    meta = json.loads((vid_dir / 'meta.json').read_text(encoding='utf-8'))
    z = np.load(vid_dir / 'pose.npz')
    t, K, S, valid = z['t'], z['kpts'].astype(float), z['scores'].astype(float), z['valid']
    n = len(t)
    fps = 1.0 / np.median(np.diff(t)) if n > 1 else 30.0
    if calib is None and (vid_dir / 'calib.json').exists():
        calib = json.loads((vid_dir / 'calib.json').read_text(encoding='utf-8'))
    # 每支影片的設定：lights=False → 這台踏板不會亮燈（例：舊白框體，喇叭燈的反光會被誤認成亮燈），
    # 不用亮燈找板位、也不以亮燈修正板位；hidden_arms → 不在畫面裡的手（骨架點是模型猜的）
    use_lights = (calib or {}).get('lights', True) is not False
    hidden_arms = {s for s in (calib or {}).get('hidden_arms', []) if s in LEG}

    if _swap_all:
        K, S = _swap_legs(K, S, np.ones(len(K), bool))
    Mot, ref_frame, mot_ok = _load_motion(vid_dir, n, meta['width'])
    # 非遊玩段（拿起手機拍結算畫面、選歌等）不分析：當作沒抓到人
    play = _play_mask(K, S, valid, Mot, mot_ok, fps)
    valid = valid & play
    S = np.where(play[:, None], S, 0.0)
    K, S, swapped = fix_left_right(K, S, valid)
    X, rel = prep_keypoints(K, S, valid)

    shank = {s: np.linalg.norm(X[:, j['knee']] - X[:, j['ankle']], axis=1) for s, j in LEG.items()}
    thigh = {s: np.linalg.norm(X[:, j['hip']] - X[:, j['knee']], axis=1) for s, j in LEG.items()}
    S_glob = float(np.nanmedian(np.r_[shank['L'], shank['R']]))
    S_loc = {}
    for s in LEG:
        v = shank[s].copy(); v[np.isnan(v)] = S_glob
        S_loc[s] = np.maximum(median_filter(v, size=int(fps) | 1), 0.3 * S_glob)
    S_mean = (S_loc['L'] + S_loc['R']) / 2
    leg_px = float(np.nanmedian(np.r_[shank['L'] + thigh['L'], shank['R'] + thigh['R']]))

    g, heel, toe = {}, {}, {}
    for s, j in LEG.items():
        heel[s] = X[:, j['heel']]
        toe[s] = (X[:, j['bt']] + X[:, j['st']]) / 2
        g[s] = (heel[s] + X[:, j['bt']] + X[:, j['st']]) / 3
        bad = ~(rel[:, j['heel']] & rel[:, j['bt']])
        g[s][bad] = np.nan

    speed, runs, planted = {}, {}, {}
    for s in LEG:
        speed[s], runs[s], planted[s] = detect_contacts(g[s], S_loc[s], fps)

    f = _forward_dir(X, planted)

    # 每次落地的接觸點：前腳掌（腳跟→腳尖 60%）
    contacts = []
    for s in LEG:
        for a, b in runs[s]:
            k = slice(a, min(a + 4, b + 1))
            hl, tp = np.nanmedian(heel[s][k], 0), np.nanmedian(toe[s][k], 0)
            if np.isnan(hl).any() or np.isnan(tp).any():
                continue
            Mi = Mot[a:a + 1]
            img = hl + 0.6 * (tp - hl)
            contacts.append(dict(foot=s, a=a, b=b, img=img, ref=_warp(Mi, img[None])[0],
                                 heel=_warp(Mi, hl[None])[0], toe=_warp(Mi, tp[None])[0],
                                 hl_img=hl, tp_img=tp, size=float(np.nanmedian(shank[s][a:b + 1]))))
    contacts.sort(key=lambda c: c['a'])
    # 校正與踏板對應都在「參考幀」座標下進行（已扣除鏡頭晃動）
    P = np.array([c['ref'] for c in contacts]) if contacts else np.zeros((0, 2))

    # ── 踏板校正 ──
    source = (calib or {}).get('_source') or ('manual' if calib and calib.get('points') else 'auto')
    stage = (calib or {}).get('_stage', 0)       # 0: 取得亮燈板位 → 1: 分板腳跟基準＋擬合 → 2: 最終
    foot_ext = tuple((calib or {}).get('_foot_ext', (0.0, 0.0)))
    view = {'mode': 'none'}
    pts = None
    if len(P) >= 12:
        side = _is_side(P, f)
        if source != 'auto' and calib and calib.get('points'):
            # 手動校正，或上一階段自動取得的板位（auto-glow／auto-feet 要沿用，不能在下一階段弄丟）
            pts = {k: np.asarray(v, float) for k, v in calib['points'].items()}
            side = np.linalg.norm(pts['L'] - pts['R']) < 0.25 * np.linalg.norm(pts['U'] - pts['D'])
        if source == 'auto':
            foot_pts = auto_homography(P, f) if not side else None
            gp = None
            if use_lights and (vid_dir / 'preview.mp4').exists():
                from .glow import glow_map, glow_points
                Fg, gs, Fr_glow = glow_map(vid_dir, Mot, meta['width'], play)
                gp = glow_points(Fg, gs, P, f, init_pts=foot_pts)
            if gp is not None:
                gpts = {k: np.asarray(gp[k]) for k in 'LDUR'}
                if np.linalg.norm(gpts['L'] - gpts['R']) >= 0.25 * np.linalg.norm(gpts['U'] - gpts['D']):
                    pts, side, source = gpts, False, 'auto-glow'
            if pts is None and foot_pts is not None:
                pts, source = {k: np.asarray(v) for k, v in foot_pts.items()}, 'auto-feet'
            if pts is None and side:
                source = 'auto-feet'
        if side:
            view = {'mode': 'side'}
        else:
            view = {'mode': 'homography'} if pts else {'mode': 'none'}

    vp = _load_vertical(vid_dir, Mot, meta)

    # 腳跟離地：估計高度，並把腳跟的投影從「遠處」拉回它正下方的地面
    HL = heel_lift_series(X, S, shank, planted)
    heel_base = (calib or {}).get('_heel_base', {})          # '左右腳+箭頭' → [pitch 基準, 踝角基準]
    contact_panel = (calib or {}).get('_contact_panel', {})  # '腳:幀' → 上一階段的亮燈板位

    def heel_info(side_, a, b, panel=None):
        H_ = HL[side_]
        k = slice(a, min(a + 4, b + 1))
        pm = float(np.nanmedian(H_['pitch'][k])) if np.isfinite(H_['pitch'][k]).any() else np.nan
        am = float(np.nanmedian(H_['ank'][k])) if np.isfinite(H_['ank'][k]).any() else np.nan
        yaw_ok = bool(H_['ok'][k].mean() >= 0.5)
        base = heel_base.get(f'{side_}{panel}') or [H_['flat'], H_['flat_ank']]
        if stage == 0 or not np.isfinite(pm) or not np.isfinite(base[0]):
            return np.nan, yaw_ok, False, pm, am
        lift = FOOT_CM * np.tan(np.radians(np.clip(pm - base[0], 0, 60)))
        agree = bool(np.isfinite(am) and np.isfinite(base[1]) and ((lift > HEEL_UP_CM) == (am - base[1] > 6)))
        return lift, yaw_ok, agree, pm, am

    def heel_ground(side_, a, h):
        hf = heel[side_][a]
        if not np.isfinite(h) or h <= HEEL_UP_CM or np.isnan(hf).any():
            return hf
        return hf + _vert_dir(hf, vp) * (h / SHANK_CM * S_loc[side_][a])

    # 腳部點（腳跟、腳尖）其實在鞋底上方幾公分；鏡頭低時，高出地面的點投影到地上會被推遠。
    # sole_h：沿垂直方向往下扣回的高度（cm），在第 1 階段用亮燈擬合；foot_off：使用者手動的腳位置偏移（格）。
    sole_h = float((calib or {}).get('_sole_h', 0.0))
    foot_off = np.asarray((calib or {}).get('foot_offset') or [0.0, 0.0], float)

    def drop(p, side_, a, h_cm):
        if not np.isfinite(h_cm) or h_cm <= 0 or np.isnan(p).any():
            return p
        return p + _vert_dir(p, vp) * (h_cm / SHANK_CM * S_loc[side_][a])

    def contact_ref(c, h_cm):
        """接觸點（腳跟、腳尖、前腳掌）扣掉高度後的參考幀座標。"""
        s_, a = c['foot'], c['a']
        hl = drop(c['hl_img'], s_, a, h_cm + (c['heel_lift'] if c.get('heel_up') else 0.0))
        tp = drop(c['tp_img'], s_, a, h_cm)
        Mi = Mot[a:a + 1]
        return _warp(Mi, hl[None])[0], _warp(Mi, tp[None])[0], _warp(Mi, (hl + 0.6 * (tp - hl))[None])[0]

    for c in contacts:
        h, yaw_ok, agree, pm, am = heel_info(c['foot'], c['a'], c['b'], contact_panel.get(f"{c['foot']}:{c['a']}"))
        c.update(heel_lift=h, yaw_ok=yaw_ok, agree=agree, pitch=pm, ank=am,
                 heel_up=bool(np.isfinite(h) and h > HEEL_UP_CM))
        c['heel'], c['toe'], c['ref'] = contact_ref(c, sole_h)

    # 投影函式：影像地面點 → 踏板座標
    to_pad = None
    com_axis = None
    if view['mode'] == 'homography':
        H = _h_from_points(pts)
        to_pad = lambda q: _apply_h(H, q)
        # 單一鏡頭只能量到重心在「畫面水平方向」的位置；沿畫面垂直線移動（深度）量不到。
        # 在踏板中心附近，把畫面垂直方向換到踏板座標，得到量不到的方向 w，可觀測軸 = w 的垂直方向。
        c_img = _apply_h(np.linalg.inv(H), [[0.0, 0.0]])[0]
        dv = _vert_dir(c_img, vp)
        w = _apply_h(H, [c_img + dv * 20])[0] - _apply_h(H, [c_img])[0]
        w = w / (np.linalg.norm(w) + 1e-9)
        com_axis = np.array([w[1], -w[0]])
        # 全域左右檢查：左腳落點平均應在踏板左側
        xs = {s: np.median(_apply_h(H, np.array([c['ref'] for c in contacts if c['foot'] == s])), 0)[0]
              for s in LEG if any(c['foot'] == s for c in contacts)}
        if len(xs) == 2 and xs['L'] > xs['R'] and not _swap_all:
            return analyze(vid_dir, calib, _swap_all=True)
    elif view['mode'] == 'side':
        cam_near = 'R' if f[0] > 0 else 'L'   # 玩家面向畫面右側 → 鏡頭在玩家右邊
        b_all = (P - np.median(P, 0)) @ f
        c0 = np.median(P, 0)
        if pts is not None:
            bU, bD = (pts['U'] - c0) @ f, (pts['D'] - c0) @ f
            if bU < bD:                       # 使用者指定的 ↑ 在後面 → 以使用者為準，前方反向
                f = -f; cam_near = 'R' if f[0] > 0 else 'L'
                b_all = -b_all; bU, bD = -bU, -bD
            bM = (bU + bD) / 2
        else:
            bD, bM, bU = _kmeans1d(b_all, 3)
        # 左右：由腳的大小（離鏡頭近 → 大）推估，以中間列的落點為基準
        yc = np.interp([(p - c0) @ f for p in P], [bD, bM, bU], [1, 0, -1])
        zmid = {s: np.median([1 / c['size'] for c, y in zip(contacts, yc) if c['foot'] == s and abs(y) < 0.5])
                for s in LEG if any(c['foot'] == s and abs(y) < 0.5 for c, y in zip(contacts, yc))}
        if len(zmid) == 2:
            near_by_size = 'L' if zmid['L'] < zmid['R'] else 'R'
            if near_by_size != cam_near and not _swap_all:
                return analyze(vid_dir, calib, _swap_all=True)
            zL, zR = zmid['L'], zmid['R']
        else:
            zL = zR = None

        def to_pad(q, sizes=None):
            q = np.asarray(q, float).reshape(-1, 2)
            y = np.interp((q - c0) @ f, [bD, bM, bU], [1, 0, -1], left=np.nan, right=np.nan)
            # 線性外插
            bq = (q - c0) @ f
            y = np.where(bq > bU, -1 - (bq - bU) / (bU - bM), y)
            y = np.where(bq < bD, 1 + (bD - bq) / (bM - bD), y)
            if sizes is None or zL is None or abs(zR - zL) < 1e-9:
                x = np.full(len(q), np.nan)
            else:
                x = -1 + 2 * (1 / np.asarray(sizes) - zL) / (zR - zL)
            return np.c_[x, y]
        com_axis = np.array([0.0, 1.0])
        if pts is None:
            pts = {'U': (c0 + bU * f).tolist(), 'D': (c0 + bD * f).tolist(),
                   'L': (c0 + bM * f).tolist(), 'R': (c0 + bM * f).tolist()}
            pts = {k: np.asarray(v) for k, v in pts.items()}

    # ── 每次落地 → 踏板位置 ──
    steps = []
    for c in contacts:
        hup = bool(np.isfinite(c['heel_lift']) and c['heel_lift'] > HEEL_UP_CM)
        st = dict(t=_r(t[c['a']]), frame=int(c['a']), end=int(c['b']), foot=c['foot'],
                  dur=_r(t[c['b']] - t[c['a']]), img=[_r(v, 1) for v in c['img']],
                  heel_lift=_r(c['heel_lift'], 1), heel_up=hup, _yaw_ok=c['yaw_ok'], _agree=c['agree'],
                  _pitch=c['pitch'], _ank=c['ank'])
        if to_pad is not None:
            if view['mode'] == 'side':
                q = to_pad([c['ref'], c['heel'], c['toe']], sizes=[c['size']] * 3)
            else:
                q = to_pad([c['ref'], c['heel'], c['toe']]) + foot_off
            p = q[0]
            st['pad'] = [_r(p[0]), _r(p[1])]
            st['heel_pad'] = [_r(q[1][0]), _r(q[1][1])]
            st['toe_pad'] = [_r(q[2][0]), _r(q[2][1])]
            if view['mode'] == 'homography':
                st.update(classify_foot(q[1], q[2], *foot_ext, heel_up=hup))
            else:
                x, y = p
                if abs(y) > 1.5:
                    st['panel'] = '板外'
                elif abs(y) > 0.5:
                    st['panel'] = '↑' if y < 0 else '↓'
                elif np.isfinite(x):
                    st['panel'] = '←' if x < -0.5 else ('→' if x > 0.5 else '中央')
                else:
                    st['panel'] = '中間列'
        steps.append(st)

    # ── 踏板亮燈：機台實際判定踩到哪一板 ──
    # 1) 每步找出剛亮起的箭頭板（lit）；2) 用這些「標準答案」校正腳的有效長度
    #    （骨架的腳跟點通常比鞋跟內縮，用腳跟踩 ↓ 會判不到），自動校正時也微調九宮格；
    # 3) 以亮燈為準修正每步的板位，並補回「有亮燈、骨架卻沒偵測到落地」的步。
    lights, fit_info, n_light_only = None, None, 0
    if use_lights and view['mode'] == 'homography' and (vid_dir / 'preview.mp4').exists():
        from .glow import glow_map, lit_at_step, light_onsets, panel_lights
        Fg, gs, Fr_glow = glow_map(vid_dir, Mot, meta['width'], play)
        lights = panel_lights(Fr_glow, gs, np.linalg.inv(_h_from_points(pts)))
        for st in steps:
            fp = [st['heel_pad'], st['toe_pad']] if st.get('heel_pad') and st['heel_pad'][0] is not None else None
            st['lit'] = lit_at_step(lights, st['frame'], fp)
        lit_idx = [i for i, st in enumerate(steps) if st['lit']]
        if stage == 1 and len(lit_idx) >= 20:
            # 用亮燈（機台判定的板）搜尋腳部點離地高度：0–10 cm，取與亮燈最一致的
            best_h = None
            for h_try in (0.0, 2.0, 4.0, 6.0, 8.0, 10.0):
                smp = []
                for i in lit_idx:
                    hr, tr, _ = contact_ref(contacts[i], h_try)
                    smp.append((hr, tr, steps[i]['lit']))
                _, _, _, fit_h = fit_foot_and_grid(smp, pts, refine_grid=False, offset=foot_off)
                if best_h is None or fit_h[1] > best_h[1] + 0.5:
                    best_h = (h_try, fit_h[1])
            sole_h = best_h[0]
            samples = []
            for i in lit_idx:
                hr, tr, _ = contact_ref(contacts[i], sole_h)
                samples.append((hr, tr, steps[i]['lit']))
            eh, et, pts2, fit = fit_foot_and_grid(samples, pts, refine_grid=source != 'manual', offset=foot_off)
            cal2 = {**calib, 'points': {k: v.tolist() for k, v in pts2.items()}, '_foot_ext': [eh, et],
                    '_fit': fit, '_stage': 2, '_sole_h': sole_h}
            return analyze(vid_dir, cal2, _swap_all=_swap_all)
        fit_info = (calib or {}).get('_fit')
        for st in steps:
            st['pose_panel'] = st.get('panel')
            if st['lit'] and st.get('heel_pad') and st['heel_pad'][0] is not None:
                st.update(classify_foot(st['heel_pad'], st['toe_pad'], *foot_ext, target=st['lit'],
                                        heel_up=st.get('heel_up', False)))
        # 有亮燈但骨架沒抓到落地 → 補一步（歸給離那塊板最近的腳）
        for fi, k in light_onsets(lights):
            sym = ARROW_SYM[k]
            if any(st.get('lit') == sym and abs(st['frame'] - fi) <= 6 for st in steps):
                continue
            best = None
            for side_ in LEG:
                h_ = heel_info(side_, fi, fi + 3, sym)[0]
                hl = drop(heel_ground(side_, fi, h_), side_, fi, sole_h)
                tp = drop(toe[side_][fi], side_, fi, sole_h)
                if np.isnan(hl).any() or np.isnan(tp).any():
                    continue
                q = to_pad(_warp(Mot[[fi, fi]], np.array([hl, tp]))) + foot_off
                foot = _foot_samples(q[0], q[1], *foot_ext)
                cx, cy = ARROWS[k]
                d = float(np.min(np.maximum(np.abs(foot[:, 0] - cx), np.abs(foot[:, 1] - cy))))
                if best is None or d < best[0]:
                    best = (d, side_, q)
            if best is None or best[0] > 1.0:
                continue
            _, side_, q = best
            h_, yaw_ok, agree, pm_, am_ = heel_info(side_, fi, fi + 3, sym)
            hup = bool(np.isfinite(h_) and h_ > HEEL_UP_CM)
            off = fi
            while off + 1 < n and lights[k][off + 1] > 0.2:
                off += 1
            st = dict(t=_r(t[fi]), frame=int(fi), end=int(off), foot=side_, dur=_r(t[off] - t[fi]),
                      img=[_r(v, 1) for v in (g[side_][fi] if np.isfinite(g[side_][fi]).all() else heel[side_][fi])],
                      heel_pad=[_r(q[0][0]), _r(q[0][1])], toe_pad=[_r(q[1][0]), _r(q[1][1])],
                      lit=sym, pose_panel=None, from_light=True,
                      heel_lift=_r(h_, 1), heel_up=hup, _yaw_ok=yaw_ok, _agree=agree, _pitch=pm_, _ank=am_)
            st.update(classify_foot(q[0], q[1], *foot_ext, target=sym, heel_up=hup))
            steps.append(st)
            n_light_only += 1
        if stage == 0 and len(lit_idx) >= 20:  # (亮燈補步之後才算，樣本較多)
            # 每隻腳、每塊（亮燈）板各自的「踩平」基準：該組落地傾角的 20 百分位
            groups = {}
            for st in steps:
                if st.get('lit') and st['_yaw_ok'] and np.isfinite(st['_pitch']):
                    groups.setdefault(f"{st['foot']}{st['lit']}", []).append((st['_pitch'], st['_ank']))
            hb = {k: [float(np.percentile([x[0] for x in v], 20)), float(np.nanpercentile([x[1] for x in v], 20))]
                  for k, v in groups.items() if len(v) >= 8}
            cal1 = {**(calib or {}), 'points': {k: np.asarray(v).tolist() for k, v in pts.items() if k in 'LDUR'},
                    '_source': source, '_stage': 1, '_heel_base': hb,
                    '_contact_panel': {f"{st['foot']}:{st['frame']}": st['lit'] for st in steps
                                       if st.get('lit') and not st.get('from_light')}}
            return analyze(vid_dir, cal1, _swap_all=_swap_all)
        steps.sort(key=lambda st: st['frame'])
        # 同一隻腳的步不重疊：結束幀截到下一步開始前
        for side_ in LEG:
            ss = [st for st in steps if st['foot'] == side_]
            for a_, b_ in zip(ss[:-1], ss[1:]):
                if a_['end'] >= b_['frame']:
                    a_['end'] = max(a_['frame'], b_['frame'] - 1)
                    a_['dur'] = _r(t[a_['end']] - t[a_['frame']])

    for st in steps:
        if st.pop('heel_conflict', False):
            st['heel_up'] = False; st['_agree'] = False
        st['conf'] = step_confidence(st, S, min(st['frame'], n - 1), st['foot'])
        for k in ('_yaw_ok', '_agree', '_pitch', '_ank'):
            st.pop(k, None)

    # ── 擺動期：抬腳高度 ──
    swings = []
    for s in LEG:
        R = runs[s]
        for (a0, b0), (a1, b1) in zip(R[:-1], R[1:]):
            if t[a1] - t[b0] > 1.5 or a1 - b0 < 2:
                continue
            seg = np.arange(b0, a1 + 1)
            low = np.nanmax(np.c_[heel[s][seg, 1], toe[s][seg, 1]], 1)   # 腳最低點（y 最大）
            base = np.interp(seg, [b0, a1], [low[0], low[-1]])
            lift = np.nanmax(base - low) / S_loc[s][seg].mean()
            if np.isfinite(lift):
                swings.append(dict(foot=s, t0=_r(t[b0]), t1=_r(t[a1]), lift=_r(lift),
                                   lift_cm=_r(lift * SHANK_CM, 1)))

    # ── 重心 ──
    com, com_mode, arm_est = compute_com(X, rel, S, meta['width'], meta['height'], hidden_arms)
    hip = (X[:, 11] + X[:, 12]) / 2
    # 重心的地面深度基準用「扣掉鞋底高度」後的腳，才會和俯視圖上的腳一致
    gL = np.array([drop(g['L'][i_], 'L', i_, sole_h) for i_ in range(n)]).reshape(-1, 2)
    gR = np.array([drop(g['R'][i_], 'R', i_, sole_h) for i_ in range(n)]).reshape(-1, 2)
    com_g = ground_at_feet_depth(com, gL, gR, vp)
    hip_g = ground_under(hip, gL, gR, vp, S_mean)
    hip_h = np.linalg.norm(hip - hip_g, axis=1) / S_mean
    com_pad = np.full((n, 2), np.nan)
    foot_pad = {s: np.full((n, 2), np.nan) for s in LEG}
    if to_pad is not None:
        ok = ~np.isnan(com_g).any(1)
        com_pad[ok] = to_pad(_warp(Mot[ok], com_g[ok])) + (foot_off if view['mode'] == 'homography' else 0)
        for s in LEG:
            m = ~np.isnan(g[s]).any(1)
            gr = _warp(Mot[m], g[s][m])
            if view['mode'] == 'side':
                sz = uniform_filter1d(np.nan_to_num(shank[s], nan=S_glob), 9)
                foot_pad[s][m] = to_pad(gr, sizes=sz[m])
            else:
                gd = np.array([drop(g[s][i_], s, i_, sole_h) for i_ in np.where(m)[0]]).reshape(-1, 2)
                foot_pad[s][m] = to_pad(_warp(Mot[m], gd)) + foot_off

    # 單應轉換在遠離踏板處會被放大成離譜的值（例如偵測錯位的幀）→ 超出合理範圍的幀視為無效
    com_pad[~(np.abs(com_pad) <= 2.5).all(1)] = np.nan
    for s in LEG:
        foot_pad[s][~(np.abs(foot_pad[s]) <= 3.0).all(1)] = np.nan

    # ── 膝蓋角度（2D） ──
    knee = {}
    for s, j in LEG.items():
        u = X[:, j['hip']] - X[:, j['knee']]; w = X[:, j['ankle']] - X[:, j['knee']]
        cosang = (u * w).sum(1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(w, axis=1) + 1e-9)
        knee[s] = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
    side_view = abs(f[0]) > abs(f[1])

    # ── 扶桿（手腕長時間固定不動） ──
    hold, hold_mask = {}, {}
    for s, wr in (('L', 9), ('R', 10)):
        if s in hidden_arms:
            continue
        m = rel[:, wr] & (S[:, wr] > 0.5)
        if m.mean() < 0.3:
            continue
        w = X[:, wr]
        win = int(2 * fps) | 1
        mx = median_filter(np.nan_to_num(w[:, 0]), size=win)
        my = median_filter(np.nan_to_num(w[:, 1]), size=win)
        still = np.hypot(w[:, 0] - mx, w[:, 1] - my) < 0.25 * S_glob
        hold[s] = float((still & m).sum() / m.sum())
        hold_mask[s] = still & m

    # 重心沿可觀測軸的位置（格），以及相對兩腳中點的位置
    com_obs = np.full(n, np.nan); com_rel = np.full(n, np.nan)
    axis_info = None
    if com_axis is not None:
        if abs(com_axis[0]) >= abs(com_axis[1]):
            com_axis = com_axis if com_axis[0] > 0 else -com_axis
        else:
            com_axis = com_axis if com_axis[1] > 0 else -com_axis
        axis_info = _axis_names(com_axis)
        com_obs = com_pad @ com_axis
        both = planted['L'] & planted['R']
        fm = (foot_pad['L'] + foot_pad['R']) / 2
        if view['mode'] == 'side':
            rel_v = com_pad[:, 1] - fm[:, 1]
        else:
            rel_v = com_obs - fm @ com_axis
        com_rel[both] = rel_v[both]

    from .events import com_events
    events, ev_stats = com_events(t, fps, com_obs, com_rel, steps, hold_mask, axis_info, PANEL_CM)

    metrics, advice = _metrics_and_advice(
        t, fps, view, steps, swings, com_obs, com_rel, axis_info, hip_h, leg_px / S_glob, knee, planted,
        side_view, hold, source, com_mode, play_s=play.sum() / fps, use_lights=use_lights)
    _heel_and_event_advice(metrics, advice, steps, events, ev_stats, axis_info)

    grid = _grid_lines(pts, view) if pts is not None else []
    result = {
        'id': vid_dir.name, 'meta': meta, 'fps': _r(fps, 2),
        'view': {**view, 'com_axis': [_r(v) for v in com_axis] if com_axis is not None else None,
                 'com_axis_names': axis_info,
                 'side_view': bool(side_view), 'forward_img': [_r(f[0]), _r(f[1])],
                 'com_mode': com_mode, 'arm_est_pct': {s: _r(float(arm_est[s].mean()) * 100, 1) for s in LEG}, 'vp': [_r(v, 6) for v in vp] if vp is not None else None,
                 'ref_frame': int(ref_frame),
                 'camera_drift_px': _r(float(np.nanmax(np.linalg.norm(
                     _warp(Mot, np.tile([meta['width'] / 2, meta['height'] * 0.8], (n, 1)))
                     - [meta['width'] / 2, meta['height'] * 0.8], axis=1))), 1)},
        'calibration': {'source': source, 'heel_base': heel_base,
                        'points': {k: [_r(v[0], 1), _r(v[1], 1)] for k, v in pts.items()} if pts else None,
                        'grid': grid, 'foot_ext': list(foot_ext), 'fit': fit_info,
                        'sole_h': _r(sole_h, 1), 'foot_offset': [_r(foot_off[0]), _r(foot_off[1])],
                        'light_only_steps': n_light_only,
                        'lights': use_lights, 'hidden_arms': sorted(hidden_arms)},
        'frames': {
            't': [_r(v) for v in t],
            'kpts': np.round(np.nan_to_num(np.where(rel[..., None], X, np.nan), nan=-1), 1).tolist(),
            'score': np.round(S, 2).tolist(),
            'com': _pts(com), 'com_ground': _pts(com_g), 'com_pad': _pts(com_pad),
            'com_obs': [_r(v) for v in com_obs], 'com_rel': [_r(v) for v in com_rel],
            'hip_h': [_r(v) for v in hip_h],
            'foot_pad': {s: _pts(foot_pad[s]) for s in LEG},
            'ground': {s: _pts(g[s]) for s in LEG},
            'M': np.round(Mot.reshape(n, 9), 7).tolist(),
            'lights': {ARROW_SYM[k]: [_r(v, 2) for v in a] for k, a in lights.items()} if lights else None,
            'planted': {s: planted[s].astype(int).tolist() for s in LEG},
            'knee': {s: [_r(v, 1) for v in knee[s]] for s in LEG},
            'arm_est': {s: arm_est[s].astype(int).tolist() for s in LEG},
        },
        'steps': steps, 'swings': swings, 'metrics': metrics, 'advice': advice,
        'events': events, 'event_stats': ev_stats,
    }
    (vid_dir / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


def _load_motion(vid_dir, n, width):
    """回傳 (M, 參考幀, ok)。舊版（逐幀補償）的 motion.npz 會重算，連帶依賴它的亮燈圖與背景也重算。"""
    from . import stabilize
    f = vid_dir / 'motion.npz'
    if f.exists():
        z = np.load(f)
        if len(z['M']) == n and int(z.get('ver', 1)) == stabilize.VERSION:
            return z['M'], int(z['ref']), z['ok']
    if (vid_dir / 'preview.mp4').exists():
        for dep in ('glow.npz', 'vertical.json'):
            (vid_dir / dep).unlink(missing_ok=True)
        r = stabilize.compute_motion(vid_dir, width)
        if r is not None:
            return r[0], r[1], np.load(f)['ok']
    return np.tile(np.eye(3), (n, 1, 1)), 0, np.ones(n, bool)


PLAY_GAP_S = 3.0     # 遊玩中短暫抓不到腳（遮擋）不算中斷
PLAY_MIN_S = 5.0     # 太短的片段不算遊玩


def _play_mask(K, S, valid, Mot, mot_ok, fps):
    """遊玩中的幀：有抓到人、鏡頭對得上參考幀、雙腳在平常站的範圍內（參考幀座標，約 1.5 條腿長內）。"""
    n = len(K)
    feet = [LEG[s][p] for s in LEG for p in ('heel', 'bt', 'st')]
    fs = S[:, feet].min(1) >= 0.3
    foot = np.where(fs[:, None], K[:, feet].mean(1), np.nan)
    leg = np.nanmedian(np.linalg.norm(K[:, 11] - K[:, 15], axis=1)[valid]) if valid.any() else np.nan
    raw = valid & mot_ok & fs
    if raw.sum() < fps * PLAY_MIN_S or not np.isfinite(leg):
        return valid.copy()
    ref = np.full((n, 2), np.nan)
    ref[raw] = _warp(Mot[raw], foot[raw])
    d = np.linalg.norm(ref - np.nanmedian(ref[raw], 0), axis=1)
    raw &= d < 1.5 * leg
    # 補短缺口、去掉太短的片段
    play = raw.copy()
    idx = np.where(raw)[0]
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= PLAY_GAP_S * fps:
            play[a:b] = True
    out = np.zeros(n, bool)
    i = 0
    while i < n:
        if play[i]:
            j = i
            while j < n and play[j]:
                j += 1
            if j - i >= PLAY_MIN_S * fps:
                out[i:j] = True
            i = j
        else:
            i += 1
    return out if out.any() else valid.copy()


def _load_vertical(vid_dir, Mot, meta):
    from . import stabilize
    f = vid_dir / 'vertical.json'
    if f.exists():
        v = json.loads(f.read_text(encoding='utf-8'))['vp']
        return None if v is None else np.asarray(v)
    vp, bg = None, None
    if (vid_dir / 'preview.mp4').exists():
        bg, s = stabilize.stabilized_background(vid_dir, Mot, (meta['width'], meta['height']))
        if bg is not None:
            cv2.imwrite(str(vid_dir / 'background.jpg'), bg)
            vp = estimate_vertical(bg, s)
    f.write_text(json.dumps({'vp': None if vp is None else vp.tolist()}), encoding='utf-8')
    return vp


def _preview_width(vid_dir):
    cap = cv2.VideoCapture(str(vid_dir / 'preview.mp4'))
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    cap.release()
    return w or 1


def _pts(a):
    return [[_r(p[0], 3), _r(p[1], 3)] if np.isfinite(p).all() else None for p in a]


def _grid_lines(pts, view):
    """踏板格線在畫面上的位置（給介面疊圖）。"""
    if view['mode'] != 'homography':
        return []
    Hinv = np.linalg.inv(_h_from_points(pts))
    lines = []
    for v in (-1.5, -0.5, 0.5, 1.5):
        s = np.linspace(-1.5, 1.5, 13)
        for q in (np.c_[np.full_like(s, v), s], np.c_[s, np.full_like(s, v)]):
            lines.append([[_r(p[0], 1), _r(p[1], 1)] for p in _apply_h(Hinv, q)])
    return lines


# ───────────────────────── 指標與建議 ─────────────────────────

CAUSE_TXT = {
    'zh': {'crossover': '交叉步', 'jump': '跳', 'same_foot': '同一腳連踩', 'wide': '大跨距',
           'bar_release': '手離開扶桿', 'fast': '比平常密集'},
    'ja': {'crossover': 'クロス（交差）', 'jump': 'ジャンプ', 'same_foot': '同じ足で連続', 'wide': '大きな踏み替え',
           'bar_release': 'バーから手を離した', 'fast': '普段より高密度'},
}
# 後端短標籤的日文（方向、腳的部位）；中文即原字串
JA = {'左右': '左右', '前後': '前後', '斜向（↖↘）': '斜め（↖↘）', '斜向（↗↙）': '斜め（↗↙）',
      '右': '右', '左': '左', '後': '後ろ', '前': '前',
      '右後（→↓ 之間）': '右後ろ（→↓ の間）', '左前（←↑ 之間）': '左前（←↑ の間）',
      '右前（→↑ 之間）': '右前（→↑ の間）', '左後（←↓ 之間）': '左後ろ（←↓ の間）',
      '腳尖': 'つま先', '腳跟': 'かかと', '全腳': '足裏全体', '腳掌中段': '土踏まず付近', '未碰到': '未接触'}
ja = lambda s: JA.get(s, s)


def _adv(A, area, level, title, evidence, fix):
    """一則建議；title / evidence / fix 皆為 (中文, 日文)。area 為中文代碼，介面負責翻譯。"""
    A.append(dict(area=area, level=level, title=dict(zh=title[0], ja=title[1]),
                  evidence=dict(zh=evidence[0], ja=evidence[1]), fix=dict(zh=fix[0], ja=fix[1])))


def _part_summary(parts):
    """{箭頭: {部位: 步數}} → 每個箭頭一句（前兩名部位），(中文列表, 日文列表)。"""
    dz, dj = [], []
    for arrow in ('←', '↓', '↑', '→'):
        pp = parts.get(arrow)
        if pp:
            tot = sum(pp.values())
            top = sorted(pp.items(), key=lambda kv: -kv[1])[:2]
            dz.append(f"{arrow} {tot} 步（" + '、'.join(f'{k} {v}' for k, v in top) + '）')
            dj.append(f"{arrow} {tot} 歩（" + '・'.join(f'{ja(k)} {v}' for k, v in top) + '）')
    return dz, dj


AUTO_NOTE = ('（這是用腳印分群做的自動校正：落點是和自己的平均落點比，不是和實際箭頭中心比；要看絕對偏移請手動校正）',
             '（足跡クラスタによる自動補正のため、ずれは自分の平均着地点との比較です。矢印の中心からの絶対的なずれを見るには手動補正してください）')


def _heel_and_event_advice(M, A, steps, events, ev_stats, axis):
    order = {'bad': 0, 'warn': 1, 'info': 2, 'good': 3}
    # 腳跟離地（只算中／高信心）
    conf = [s for s in steps if s.get('conf') in ('high', 'medium') and s.get('heel_lift') is not None]
    M['conf_counts'] = {c: sum(1 for s in steps if s.get('conf') == c) for c in ('high', 'medium', 'low')}
    if len(conf) >= 20:
        per = {}
        for s in conf:
            k = s.get('panel')
            if k in ARROW_SYM.values():
                per.setdefault(k, []).append(s['heel_up'])
        M['heel_up_pct'] = _r(100 * np.mean([s['heel_up'] for s in conf]), 1)
        M['heel_up_by_arrow'] = {k: dict(n=len(v), pct=_r(100 * np.mean(v), 1)) for k, v in per.items() if len(v) >= 5}
        flat = [k for k, v in M['heel_up_by_arrow'].items() if v['pct'] < 30]
        txt = ' '.join(f"{k} {v['pct']:.0f}%" for k, v in M['heel_up_by_arrow'].items())
        p = M['heel_up_pct']
        _adv(A, '踩點', 'info',
             ('腳跟離地（踮腳踩）比例', 'かかとが浮いている割合（前足部で踏む）'),
             (f'可信的 {len(conf)} 步中，{p:.0f}% 落地時腳跟離地超過 {HEEL_UP_CM} cm；各箭頭：{txt}。',
              f'信頼できる {len(conf)} 歩のうち {p:.0f}% は、着地時にかかとが {HEEL_UP_CM} cm 以上浮いていました。矢印別：{txt}。'),
             ('高密度時用前腳掌踩（腳跟不落地）比較快、也比較不震；'
              + (f'{"、".join(flat)} 大多整隻腳踩平，這幾個方向可以試著只用前腳掌點板。' if flat else '各方向都有用前腳掌踩，維持即可。'),
              '高密度では前足部で踏む（かかとを下ろさない）方が速く、衝撃も少なくなります。'
              + (f'{"・".join(flat)} はほとんど足裏全体で踏んでいるので、前足部だけでパネルをタッチするよう試してみましょう。'
                 if flat else 'どの方向も前足部で踏めているので、この調子で。')))
    # 重心事件
    if events:
        M['events'] = len(events)
        cc = sorted(ev_stats.get('causes', {}).items(), key=lambda kv: -kv[1])[:3]
        pats = (ev_stats.get('patterns') or [])[:3]
        mx = max(e['dev_cm'] for e in events)
        top_c = ('、'.join(f"{CAUSE_TXT['zh'][k]} {v} 次" for k, v in cc), '・'.join(f"{CAUSE_TXT['ja'][k]} {v} 回" for k, v in cc))
        top_p = ('、'.join(f"{q['pattern']}（平均 {q['mean_cm']} cm，平常的 {q['ratio']} 倍，{q['n']} 次）" for q in pats),
                 '・'.join(f"{q['pattern']}（平均 {q['mean_cm']} cm・普段の {q['ratio']} 倍・{q['n']} 回）" for q in pats))
        _adv(A, '重心', 'info',
             (f'重心大幅移動 {len(events)} 處', f'重心の大きな移動 {len(events)} 件'),
             (f'以你慣用的姿勢為基準（{axis["axis"]}方向），挑出偏離最明顯的 {len(events)} 段；最大偏離 {mx:.0f} cm。'
              f'常見原因：{top_c[0] or "無明顯共通點"}。' + (f'最容易讓重心跑掉的三步配置：{top_p[0]}。' if pats else ''),
              f'いつもの姿勢を基準に（{ja(axis["axis"])}方向）、ずれが最も大きかった {len(events)} 区間を抽出しました。最大のずれは {mx:.0f} cm。'
              f'主な原因：{top_c[1] or "共通点なし"}。' + (f'重心が崩れやすい 3 歩の並び：{top_p[1]}。' if pats else '')),
             ('到「重心事件」分頁逐一點開看慢動作。交叉步時先轉髖、再出腳；大跨距（←→、↑↓）時讓腳去、骨盆留在中央；'
              '同一腳連踩換板時，另一腳先穩住承重再移動。',
              '「重心イベント」タブで一つずつスロー再生して確認しましょう。クロスではまず腰を回してから足を出す。'
              '大きな踏み替え（←→・↑↓）では足だけを出して骨盤は中央に残す。同じ足で続けてパネルを変えるときは、'
              '反対の足でしっかり体重を支えてから動かします。'))
    A.sort(key=lambda a: order[a['level']])


def _axis_names(a):
    """可觀測軸的中文名稱：(軸名, 正向名, 負向名)。踏板座標 x 往右、y 往後為正。"""
    ax, ay = a
    if abs(ax) >= 0.92:
        return {'axis': '左右', 'pos': '右', 'neg': '左'}
    if abs(ay) >= 0.92:
        return {'axis': '前後', 'pos': '後', 'neg': '前'}
    # 斜向（已調整為 ax > 0 或 ay > 0 的方向）
    if (ax > 0) == (ay > 0):
        return {'axis': '斜向（↖↘）', 'pos': '右後（→↓ 之間）', 'neg': '左前（←↑ 之間）'}
    return {'axis': '斜向（↗↙）', 'pos': '右前（→↑ 之間）' if ax > 0 else '左後（←↓ 之間）',
            'neg': '左後（←↓ 之間）' if ax > 0 else '右前（→↑ 之間）'}


def _metrics_and_advice(t, fps, view, steps, swings, com_obs, com_rel, axis, hip_h, leg_ratio, knee,
                        planted, side_view, hold, source, com_mode, play_s=None, use_lights=True):
    M, A = {}, []
    dur = float(play_s) if play_s is not None else (float(t[-1] - t[0]) if len(t) > 1 else 0)
    M['duration_s'] = _r(dur, 1)
    M['steps'] = len(steps)
    M['steps_per_s'] = _r(len(steps) / dur, 2) if dur else None
    M['calibration'] = source
    M['com_mode'] = com_mode
    note = AUTO_NOTE if source == 'auto-feet' else ('', '')

    # 重心：沿可觀測軸
    v = com_obs[np.isfinite(com_obs)]
    if axis and len(v) > fps * 5:
        sway = float(np.std(v)); bias = float(np.median(v))
        center = float(np.mean(np.abs(v) < 0.5) * 100)
        ax_zh, ax_ja = axis['axis'], ja(axis['axis'])
        M['com_axis'] = ax_zh
        M['com_sway'] = _r(sway); M['com_sway_cm'] = _r(sway * PANEL_CM, 1)
        M['com_bias'] = _r(bias); M['com_center_pct'] = _r(center, 1)
        lv = _level(sway, 'com_sway')
        cm = sway * PANEL_CM
        _adv(A, '重心', lv, (f'重心{ax_zh}晃動', f'重心の揺れ（{ax_ja}）'),
             (f'這個鏡位能量到重心的{ax_zh}方向。重心在這個方向的標準差 {sway:.2f} 格（約 {cm:.0f} cm），{center:.0f}% 的時間在中央板範圍內。',
              f'このカメラ位置では重心の{ax_ja}方向を測定できます。この方向の標準偏差は {sway:.2f} マス（約 {cm:.0f} cm）、{center:.0f}% の時間は中央パネルの範囲内でした。'),
             ('腳去踩、身體不要跟過去：想像肚臍固定在中央板正上方，用髖關節把腿送出去。'
              '練習：放慢速度踩交替串，看介面的重心曲線是否能維持在中線 ±0.2 格內。',
              '足だけを出して、体はついていかないこと。へそが中央パネルの真上に固定されているイメージで、股関節から脚を送り出します。'
              '練習：交互踏みをゆっくり踏み、画面の重心グラフが中心線 ±0.2 マス以内に収まるか確認しましょう。')
             if lv != 'good' else ('重心大多留在中央，維持即可。', '重心はほぼ中央に保たれています。この調子で。'))
        lvb = _level(abs(bias), 'com_bias')
        if lvb != 'good':
            d = axis['pos'] if bias > 0 else axis['neg']
            cm = abs(bias) * PANEL_CM
            _adv(A, '重心', lvb, (f'重心偏{d}', f'重心が{ja(d)}寄り'),
                 (f'整段重心平均偏{d} {abs(bias):.2f} 格（約 {cm:.0f} cm），以踏板中心為基準。',
                  f'曲全体で重心が平均 {abs(bias):.2f} マス（約 {cm:.0f} cm）{ja(d)}寄りでした（パネル中心基準）。'),
                 ('平常站的位置就偏離中央，出腳到另一側時距離變長、也比較晚回來。把「預備位置」重新定在中央板正上方，兩腳平均受力。' + note[0],
                  '普段の立ち位置が中央からずれていると、反対側へ踏むときの距離が長くなり、戻りも遅れます。'
                  '「構え」の位置を中央パネルの真上に置き直し、両足に均等に体重を乗せましょう。' + note[1]))
    r = com_rel[np.isfinite(com_rel)]
    if axis and len(r) > fps * 3:
        rb = float(np.median(r))
        M['com_rel_feet'] = _r(rb)
        lv = _level(abs(rb), 'com_fwd_bias')
        cm = abs(rb) * PANEL_CM
        if axis['axis'] == '前後' or view['mode'] == 'side':
            back = rb > 0
            _adv(A, '重心', lv, ('重心相對雙腳（前後）', '両足に対する重心（前後）'),
                 (f'兩腳都著地時，重心平均在兩腳中點偏{"後" if back else "前"} {abs(rb):.2f} 格（約 {cm:.0f} cm）。',
                  f'両足が着地しているとき、重心は両足の中点より平均 {abs(rb):.2f} マス（約 {cm:.0f} cm）{"後ろ" if back else "前"}にありました。'),
                 (('重心落在腳跟後面，↑ 會踩不深、要用腳跟硬撐；上半身微微前傾，把重心放到前腳掌。',
                   '重心がかかとより後ろにあると、↑ を深く踏めず、かかとで踏ん張ることになります。上半身をわずかに前傾させ、重心を母指球に乗せましょう。')
                  if back else
                  ('重心壓在腳尖前面，↓ 容易來不及回收；骨盆往後收一點，回到腳掌正上方。',
                   '重心がつま先より前にあると、↓ の戻りが間に合わなくなりがちです。骨盤を少し後ろに引き、足裏の真上に戻しましょう。'))
                 if lv != 'good' else ('雙腳著地時重心大致在腳掌上方，平衡良好。', '両足着地時の重心はほぼ足裏の上にあり、バランスは良好です。'))
        else:
            d = axis['pos'] if rb > 0 else axis['neg']
            _adv(A, '重心', lv, ('重心相對雙腳', '両足に対する重心'),
                 (f'兩腳都著地時，重心平均在兩腳中點偏{d} {abs(rb):.2f} 格（約 {cm:.0f} cm，沿{axis["axis"]}方向）。',
                  f'両足が着地しているとき、重心は両足の中点より平均 {abs(rb):.2f} マス（約 {cm:.0f} cm）{ja(d)}寄りでした（{ja(axis["axis"])}方向）。'),
                 ('雙腳著地時重心應該在兩腳之間；長期偏向一側，代表那一側的腳承重較多、另一腳比較「虛」，換腳時會慢半拍。練習時刻意讓兩腳平均受力。',
                  '両足着地時の重心は両足の間にあるのが理想です。いつも片側に寄っていると、その側の足に体重がかかり、反対の足が「浮いた」状態になって'
                  '踏み替えが半拍遅れます。練習では両足に均等に体重を乗せることを意識しましょう。')
                 if lv != 'good' else ('雙腳著地時重心大致在兩腳之間，平衡良好。', '両足着地時の重心はほぼ両足の間にあり、バランスは良好です。'))

    # 上下彈跳
    h = hip_h[np.isfinite(hip_h)]
    if len(h) > fps * 5:
        hd = h - uniform_filter1d(h, int(fps) | 1)
        bounce = float(np.std(hd) / leg_ratio * 100)
        M['bounce_pct'] = _r(bounce, 2)
        lv = _level(bounce, 'bounce_pct')
        _adv(A, '省力', lv, ('骨盆上下彈跳', '骨盤の上下動'),
             (f'骨盆高度（去除慢速變化後）標準差約為腿長的 {bounce:.1f}%。',
              f'骨盤の高さ（ゆっくりした変化を除いたもの）の標準偏差は、脚の長さの約 {bounce:.1f}% でした。'),
             ('每一步都把身體撐起來會很耗體力。膝蓋保持微彎當彈簧，髖關節高度固定，用小腿與腳踝發力。'
              '練習：長串 stream 時盯著介面的「骨盆高度」曲線，讓它盡量平。',
              '一歩ごとに体を持ち上げるとスタミナを大きく消耗します。膝を軽く曲げてバネにし、股関節の高さを一定に保ち、ふくらはぎと足首で踏みましょう。'
              '練習：長い乱打で、画面の「骨盤の高さ」グラフがなるべく平らになるよう意識します。')
             if lv != 'good' else ('骨盆高度穩定，出力效率不錯。', '骨盤の高さが安定しており、効率よく踏めています。'))

    # 抬腳高度
    if swings:
        lifts = np.array([s['lift_cm'] for s in swings if s['lift_cm'] is not None])
        if len(lifts) >= 10:
            med = float(np.median(lifts)); p90 = float(np.percentile(lifts, 90))
            M['lift_cm_median'] = _r(med, 1); M['lift_cm_p90'] = _r(p90, 1)
            for s in LEG:
                ls = [w['lift_cm'] for w in swings if w['foot'] == s and w['lift_cm'] is not None]
                M[f'lift_cm_{s}'] = _r(np.median(ls), 1) if ls else None
            lv = _level(med, 'lift_cm')
            L_, R_ = M['lift_cm_L'], M['lift_cm_R']
            _adv(A, '省力', lv, ('抬腳高度', '足の上げ高さ'),
                 (f'每次移動腳的抬起高度中位數約 {med:.0f} cm，較高的 10% 約 {p90:.0f} cm（左 {L_} / 右 {R_} cm，以小腿長 {SHANK_CM} cm 換算，估計值）。',
                  f'足を動かすときの上げ高さは中央値で約 {med:.0f} cm、高い方の 10% で約 {p90:.0f} cm でした（左 {L_} / 右 {R_} cm、すね {SHANK_CM} cm として換算した推定値）。'),
                 ('腳離板 3–5 cm 就夠了。高密度段改用「貼地滑出去」的感覺，而不是抬腿再踩下；腳抬越高，落地越晚，也越累。',
                  'パネルから 3〜5 cm 浮けば十分です。高密度の区間では、脚を上げて踏み下ろすのではなく「床をすべらせて出す」感覚で。'
                  '足を高く上げるほど着地が遅れ、疲れも増えます。')
                 if lv != 'good' else ('抬腳高度低，動作精簡。', '足の上げ高さは低く、無駄のない動きです。'))
            if L_ and R_:
                hi, lo = max(L_, R_), min(L_, R_)
                if hi - lo > 3 and hi > 1.4 * lo:
                    w = '左' if L_ > R_ else '右'
                    _adv(A, '省力', 'warn', (f'{w}腳抬得比較高', f'{w}足の方が高く上がっている'),
                         (f'左 {L_} cm / 右 {R_} cm。', f'左 {L_} cm / 右 {R_} cm。'),
                         (f'{w}腳可能是比較不熟練或習慣「踢」的那隻；單獨用{w}腳練低抬腳的踩踏。',
                          f'{w}足は、慣れていない足か「蹴る」癖のある足かもしれません。{w}足だけで、低く踏む練習をしてみましょう。'))

    # 膝蓋（側面視角才可靠）
    if side_view:
        ks = []
        for s in LEG:
            m = planted[s] & np.isfinite(knee[s])
            ks.append(knee[s][m])
        k = np.concatenate(ks)
        if len(k) > 30:
            med = float(np.median(k))
            M['knee_stance_deg'] = _r(med, 1)
            if med > 170:
                _adv(A, '省力', 'warn', ('膝蓋偏直', '膝が伸びぎみ'),
                     (f'落地時膝蓋角度中位數 {med:.0f}°（180° 為完全伸直）。', f'着地時の膝角度は中央値 {med:.0f}°（180° で完全に伸びた状態）。'),
                     ('膝蓋鎖直落地衝擊直接傳到膝蓋和腰，也比較難快速換腳；保持約 150–165° 的微彎。',
                      '膝を伸ばしきって着地すると衝撃が膝や腰に直接伝わり、素早い踏み替えも難しくなります。150〜165° くらいの軽い曲げを保ちましょう。'))
            elif med < 135:
                _adv(A, '省力', 'warn', ('蹲得偏低', '腰を落としすぎ'),
                     (f'落地時膝蓋角度中位數 {med:.0f}°。', f'着地時の膝角度は中央値 {med:.0f}°。'),
                     ('太低的重心要靠大腿一直出力撐著，長曲後段容易沒力；稍微站高一點，約 150–165°。',
                      '重心が低すぎると太ももで支え続けることになり、長い曲の後半で脚が持ちません。少し高めに立ち、150〜165° くらいを目安に。'))
            else:
                _adv(A, '省力', 'good', ('膝蓋彎曲程度', '膝の曲がり具合'),
                     (f'落地時膝蓋角度中位數 {med:.0f}°，在合理範圍。', f'着地時の膝角度は中央値 {med:.0f}° で、適正範囲です。'),
                     ('維持即可。', 'この調子で。'))

    # 踩點
    if view['mode'] == 'homography':
        st = [s for s in steps if 'panel' in s]
        if len(st) >= 20:
            cnt = {}
            for s in st:
                cnt[s['panel']] = cnt.get(s['panel'], 0) + 1
            M['panel_counts'] = cnt
            off = sum(v for k, v in cnt.items() if k in ('角落', '板外')) / len(st) * 100
            M['offpanel_pct'] = _r(off, 1)
            arrow_steps = [s for s in st if s['panel'] in ARROW_SYM.values()]
            edge = [s for s in arrow_steps if s.get('depth') is not None and s['depth'] < 0.12]
            edge_pct = len(edge) / max(1, len(arrow_steps)) * 100
            M['edge_pct'] = _r(edge_pct, 1)
            spreads = {}
            for k, sym in ARROW_SYM.items():
                ps = np.array([s['pad'] for s in arrow_steps if s['panel'] == sym])
                if len(ps) >= 5:
                    spreads[sym] = dict(n=len(ps), spread_cm=_r(float(np.sqrt(np.var(ps, 0).sum())) * PANEL_CM, 1),
                                        offset=[_r(ps[:, 0].mean() - ARROWS[k][0]), _r(ps[:, 1].mean() - ARROWS[k][1])])
            M['arrow_spread'] = spreads
            lit = [s for s in st if s.get('lit')]
            if len(lit) >= 10:
                posed = [s for s in lit if s.get('pose_panel')]
                agree = sum(1 for s in posed if s['pose_panel'] == s['lit']) / max(1, len(posed)) * 100
                M['lit_steps'] = len(lit); M['lit_agree_pct'] = _r(agree, 1)
                M['light_only_steps'] = n_lo = sum(1 for s in lit if s.get('from_light'))
                lc, mism, parts = {}, {}, {}
                for s in lit:
                    lc[s['lit']] = lc.get(s['lit'], 0) + 1
                    if s.get('pose_panel') and s['pose_panel'] != s['lit']:
                        key = f"亮 {s['lit']}／骨架 {s['pose_panel']}"
                        mism[key] = mism.get(key, 0) + 1
                    if s.get('part'):
                        parts.setdefault(s['lit'], {}).setdefault(s['part'], 0)
                        parts[s['lit']][s['part']] += 1
                M['lit_counts'] = lc
                M['lit_mismatch'] = dict(sorted(mism.items(), key=lambda kv: -kv[1])[:6])
                M['press_part'] = parts
                M['lit_untouched'] = sum(1 for s in lit if s.get('part') == '未碰到')
                dz, dj = _part_summary(parts)
                _adv(A, '踩點', 'info', ('用腳的哪個部位踩（以踏板亮燈為準）', 'どの部位で踏んだか（パネル点灯基準）'),
                     ('；'.join(dz) + f'。骨架判斷與亮燈一致 {agree:.0f}%' + (f'，另有 {n_lo} 步是只靠亮燈才抓到的' if n_lo else '') + '。',
                      '；'.join(dj) + f'。骨格判定と点灯の一致率は {agree:.0f}%' + (f'。ほかに点灯だけで検出した歩が {n_lo} 歩あります' if n_lo else '') + '。'),
                     ('板位以機台亮燈為準，骨架負責判斷是腳跟、腳尖還是整隻腳。腳跟踩 ↓、腳尖踩 ↑ 是高階常見的省力腳法；'
                      '如果 ←→ 也常只用腳尖或腳跟邊緣踩，代表身體沒有轉過去，容易踩不穩。',
                      'パネルの判定は筐体の点灯を基準にし、かかと・つま先・足全体のどこで踏んだかを骨格から判定しています。'
                      'かかとで ↓、つま先で ↑ を踏むのは上級者によくある省エネの踏み方です。←→ もつま先やかかとの端だけで踏むことが多い場合は、'
                      '体の向きが追いついておらず、踏みが不安定になりがちです。'))
            elif not use_lights:
                # 不亮燈的機台：板位與部位都只靠骨架（腳長用預設，沒有亮燈校正）→ 參考值
                parts = {}
                for s in arrow_steps:
                    if s.get('part'):
                        parts.setdefault(s['panel'], {}).setdefault(s['part'], 0)
                        parts[s['panel']][s['part']] += 1
                if parts:
                    M['press_part'] = parts
                    dz, dj = _part_summary(parts)
                    _adv(A, '踩點', 'info', ('用腳的哪個部位踩（骨架判斷・參考值）', 'どの部位で踏んだか（骨格判定・参考値）'),
                         ('；'.join(dz) + '。這支影片設定為「踏板不亮燈」，板位與部位都只靠骨架判斷、沒有亮燈確認，信心較低。',
                          '；'.join(dj) + '。この動画は「パネルが点灯しない」設定のため、パネルも部位も骨格だけで判定しており、点灯による確認がなく信頼度は低めです。'),
                         ('腳跟踩 ↓、腳尖踩 ↑ 是高階常見的省力腳法；如果 ←→ 也常只用腳尖或腳跟邊緣踩，代表身體沒有轉過去，容易踩不穩。',
                          'かかとで ↓、つま先で ↑ を踏むのは上級者によくある省エネの踏み方です。←→ もつま先やかかとの端だけで踏むことが多い場合は、'
                          '体の向きが追いついておらず、踏みが不安定になりがちです。'))
            lv = _level(off, 'offpanel_pct')
            _adv(A, '踩點', lv, ('踩到非箭頭區', '矢印以外の場所を踏んだ'),
                 (f'{off:.0f}% 的落地整隻腳都沒壓到箭頭板，而是落在角落或踏板外（只踩中央板的不計）。',
                  f'着地の {off:.0f}% は足全体が矢印パネルに触れておらず、角やパネル外でした（中央パネルだけのものは除く）。'),
                 ('角落沒有感應器，踩到等於浪費一步又拉開重心；落腳時看準箭頭板中心，交叉步時尤其注意不要踩到斜角。' + note[0],
                  '角にはセンサーがないので、踏んでも一歩ぶんの無駄になり、重心も崩れます。着地のときは矢印パネルの中心を狙い、'
                  '特にクロスでは斜めの角を踏まないように注意しましょう。' + note[1])
                 if lv != 'good' else ('幾乎都踩在有效的板上。', 'ほぼすべて有効なパネルを踏めています。'))
            lv = _level(edge_pct, 'edge_pct')
            ecm = 0.12 * PANEL_CM
            _adv(A, '踩點', lv, ('落點貼近板緣', 'パネルの端を踏んでいる'),
                 (f'踩箭頭的步數中，有 {edge_pct:.0f}% 腳只壓進板內不到 0.12 格（約 {ecm:.0f} cm）。',
                  f'矢印を踏んだ歩のうち {edge_pct:.0f}% は、パネルの内側に 0.12 マス（約 {ecm:.0f} cm）未満しか入っていませんでした。'),
                 ('貼邊踩容易沒感應或誤踩隔壁板。踩 ↑ 時可讓腳跟留在中央、腳掌前半落在箭頭中間；踩 ↓ 時反過來，腳尖留在中央。' + note[0],
                  '端を踏むと反応しなかったり、隣のパネルを誤って踏んだりしやすくなります。↑ はかかとを中央に残して前足部を矢印の中央に、'
                  '↓ は逆につま先を中央に残して踏みましょう。' + note[1])
                 if lv != 'good' else ('落點大多在板子內側，安全。', '着地はほぼパネルの内側で、安全です。'))
            if spreads:
                worst = max(spreads.items(), key=lambda kv: kv[1]['spread_cm'])[0]
                lv = _level(spreads[worst]['spread_cm'], 'spread_cm')
                _adv(A, '踩點', lv, ('落點穩定度', '着地位置の安定性'),
                     ('各箭頭落點散佈：' + '、'.join(f'{k} ±{v["spread_cm"]:.0f} cm（{v["n"]} 步）' for k, v in spreads.items()) + '。',
                      '各矢印の着地のばらつき：' + '・'.join(f'{k} ±{v["spread_cm"]:.0f} cm（{v["n"]} 歩）' for k, v in spreads.items()) + '。'),
                     (f'{worst} 的落點最分散，代表每次踩的位置不固定，通常是重心沒回中、出腳的起點不一樣。先把重心回中做好，落點自然會集中。',
                      f'{worst} の着地が最もばらついています。毎回違う位置を踏んでいるということで、たいていは重心が中央に戻っておらず、'
                      '足を出す起点が毎回違うのが原因です。まず重心を中央に戻すことを意識すれば、着地も自然にそろってきます。')
                     if lv != 'good' else ('各箭頭落點集中，腳法穩定。', '各矢印の着地がそろっていて、足さばきが安定しています。'))
    elif view['mode'] == 'side':
        st = [s for s in steps if 'pad' in s and s['pad'][1] is not None]
        if len(st) >= 20:
            ys = np.array([s['pad'][1] for s in st])
            M['row_counts'] = {'↑': int((ys < -0.5).sum()), '中間': int((np.abs(ys) <= 0.5).sum()),
                               '↓': int((ys > 0.5).sum())}
            outside = float(np.mean(np.abs(ys) > 1.5) * 100)
            M['offpanel_pct'] = _r(outside, 1)
            for k, sel in (('↑', ys < -0.5), ('↓', ys > 0.5)):
                if sel.sum() >= 5:
                    M[f'reach_{k}'] = _r(float(np.median(np.abs(ys[sel]))))
            rc, ru, rd = M['row_counts'], M.get('reach_↑') or 0, M.get('reach_↓') or 0
            _adv(A, '踩點', 'info', ('前後落點（側面視角）', '前後の着地（真横からの視点）'),
                 (f'↑ 列 {rc["↑"]} 步、中間列 {rc["中間"]} 步、↓ 列 {rc["↓"]} 步；{outside:.0f}% 超出踏板前後範圍。'
                  f'↑ 落點深度中位數 {ru:.2f} 格、↓ {rd:.2f} 格（1.0 = 箭頭中心）。',
                  f'↑ 列 {rc["↑"]} 歩・中段 {rc["中間"]} 歩・↓ 列 {rc["↓"]} 歩。{outside:.0f}% はパネルの前後範囲外。'
                  f'↑ の着地の深さは中央値 {ru:.2f} マス、↓ は {rd:.2f} マス（1.0 = 矢印の中心）。'),
                 ('這個鏡位幾乎和地面平行，只能判斷前後；左右是由腳的遠近大小推估，僅供參考。想看完整的四方向落點，建議從玩家後上方斜拍（手機高度約腰部）。',
                  'このカメラ位置は床とほぼ平行なので、前後しか判定できません。左右は足の見かけの大きさからの推定で、参考値です。'
                  '四方向すべての着地を見るには、プレイヤーの斜め後ろ上方（スマホを腰の高さ）から撮影してください。'))

    # 扶桿
    if hold:
        M['hand_hold'] = {k: _r(v * 100, 1) for k, v in hold.items()}
        mx = max(hold.values())
        if mx > 0.5:
            hand = '右手' if hold.get('R', 0) >= hold.get('L', 0) else '左手'
            _adv(A, '其他', 'info', ('扶桿', 'バーの使用'),
                 (f'{hand}約 {mx * 100:.0f}% 的時間固定不動（推測扶著桿子）。',
                  f'{hand}が約 {mx * 100:.0f}% の時間ほぼ動いていませんでした（バーを持っていたと推定）。'),
                 ('高難度扶桿是正常戰術，可以省下維持平衡的力氣。但如果簡單段也一直扶，可能是在補償重心不穩；可以在低密度段試著放手，檢查重心曲線有沒有變亂。',
                  '高難度でバーを使うのは正当な戦術で、バランスを保つ力を節約できます。ただ、簡単な区間でもずっと持っている場合は、'
                  '重心の不安定さを補っている可能性があります。低密度の区間で手を離してみて、重心グラフが乱れないか確認しましょう。'))

    # 左右腳使用比例
    nL = sum(1 for s in steps if s['foot'] == 'L'); nR = len(steps) - nL
    if len(steps) >= 30:
        M['foot_ratio'] = {'L': nL, 'R': nR}
        ratio = nL / len(steps)
        if abs(ratio - 0.5) > 0.1:
            w = '左' if ratio > 0.5 else '右'
            _adv(A, '其他', 'info', (f'{w}腳出腳較多', f'{w}足で踏む回数が多い'),
                 (f'左腳 {nL} 步、右腳 {nR} 步。', f'左足 {nL} 歩・右足 {nR} 歩。'),
                 ('可能是譜面本身的配置，也可能是習慣用同一隻腳補拍；對照影片看是否有不必要的「墊步」。',
                  '譜面の配置によるものかもしれませんし、同じ足で拍を埋める癖かもしれません。動画を見て、不要な「つなぎの一歩」がないか確認しましょう。'))
    order = {'bad': 0, 'warn': 1, 'info': 2, 'good': 3}
    A.sort(key=lambda a: order[a['level']])
    return M, A


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(errors='replace')
    r = analyze(sys.argv[1])
    print(json.dumps({'view': r['view'], 'calibration': r['calibration']['points'],
                      'metrics': r['metrics']}, ensure_ascii=False, indent=1))
    for a in r['advice']:
        print(f"[{a['level']}] {a['area']} {a['title']['zh']}: {a['evidence']['zh']}")
