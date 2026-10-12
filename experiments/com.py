"""重心三方法比較：winter2d（現行，只量得到畫面水平方向）、rtmw3d（RTMW3D 的 3D 骨架）、
bonelen（骨長＋弱透視：2D 骨架＋已知骨長反推深度大小，正負號取 RTMW3D）。

局部相機模型（不需要焦距）：踏板單應矩陣在雙腳中點的 Jacobian J（踏板格 → 參考幀像素）。
  a＝對應畫面水平方向的踏板方向、b＝與 a 垂直、往畫面上方（離相機）的方向；
  s＝|J a|（每格幾像素）、sinθ＝|J b 的 y 分量| / s（θ＝相機俯角）。
  身體點相對雙腳中點：橫向 l＝Δx/s、畫面縱向 v＝−Δy/s、光軸深度 z（2D 量不到）。
  地面深度 d＝v·sinθ＋z·cosθ、高度 h＝v·cosθ−z·sinθ。

驗證（有標準答案）：雙腳著地時兩腳在踏板上的位置由單應矩陣已知 → 兩腳的光軸深度差
  Δz_true＝(腳L−腳R)·b·cosθ；比較各方法推估的 Δz（winter2d＝0）。
合理性：雙腳著地且靜止時重心投影應在兩腳之間；單腳支撐時重心加速度應與（重心−支撐腳）同向（倒單擺）。

用法：experiments/py.sh experiments/com.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
from scipy.signal import savgol_filter

from common import CACHE, DATA, LIT, RESULTS, UNSCORED, heartbeat, save_json

from ddrpose.analyze import _apply_h, _h_from_points, _interp_nan, _warp

# COCO-WholeBody 索引
NOSE, LSH, RSH, LEL, REL, LWR, RWR, LHIP, RHIP, LKN, RKN, LAN, RAN = 0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16
LBT, LST, LHE, RBT, RST, RHE = 17, 18, 19, 20, 21, 22
BONES = [(LHIP, LKN), (LKN, LAN), (RHIP, RKN), (RKN, RAN), (LSH, LEL), (LEL, LWR), (RSH, REL), (REL, RWR),
         (LSH, LHIP), (RSH, RHIP), (LSH, RSH), (LHIP, RHIP)]


def winter_com(P):
    """P (N,133,k)：Winter 分段質量（和 analyze.compute_com 同權重，手臂用實際點）。"""
    hip = (P[:, LHIP] + P[:, RHIP]) / 2
    shm = (P[:, LSH] + P[:, RSH]) / 2
    tv = shm - hip
    com = 0.497 * (hip + 0.5 * tv) + 0.081 * (shm + 0.3 * tv)
    for sh, el, wr in ((LSH, LEL, LWR), (RSH, REL, RWR)):
        com = com + 0.028 * (P[:, sh] + 0.436 * (P[:, el] - P[:, sh])) + 0.016 * (P[:, el] + 0.43 * (P[:, wr] - P[:, el])) \
            + 0.006 * P[:, wr]
    for hp, kn, an, bt, st, he in ((LHIP, LKN, LAN, LBT, LST, LHE), (RHIP, RKN, RAN, RBT, RST, RHE)):
        foot = (P[:, he] + P[:, bt] + P[:, st]) / 3
        com = com + 0.100 * (P[:, hp] + 0.433 * (P[:, kn] - P[:, hp])) + 0.0465 * (P[:, kn] + 0.433 * (P[:, an] - P[:, kn])) \
            + 0.0145 * foot
    return com


def foot_pt(P, side):
    if side == 'L':
        return (P[:, LHE] + P[:, LBT] + P[:, LST]) / 3
    return (P[:, RHE] + P[:, RBT] + P[:, RST]) / 3


def local_camera(H, Hinv, feet_mid_pad):
    """每幀 (a, b, s, sinθ)：a、b 為踏板方向單位向量。"""
    n = len(feet_mid_pad)
    A = np.full((n, 2), np.nan); B = np.full((n, 2), np.nan); s = np.full(n, np.nan); sn = np.full(n, np.nan)
    e = 1e-3
    for i in np.where(np.isfinite(feet_mid_pad).all(1))[0]:
        q = feet_mid_pad[i]
        p0 = _apply_h(Hinv, [q])[0]
        J = np.c_[(_apply_h(Hinv, [q + [e, 0]])[0] - p0) / e, (_apply_h(Hinv, [q + [0, e]])[0] - p0) / e]
        a = np.linalg.solve(J, [1.0, 0.0]); a /= np.linalg.norm(a)
        b = np.array([-a[1], a[0]])
        if (J @ b)[1] > 0:          # 要往畫面上方（遠離相機）
            b = -b
        s[i] = np.linalg.norm(J @ a)
        sn[i] = np.clip(abs((J @ b)[1]) / s[i], 0.05, 1.0)
        A[i], B[i] = a, b
    return A, B, s, sn


def load(vid):
    a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
    fr = a['frames']
    n = len(fr['t'])
    fps = a['fps']
    pts = {k: np.asarray(v, float) for k, v in a['calibration']['points'].items() if k in 'LDUR'}
    H = _h_from_points(pts); Hinv = np.linalg.inv(H)
    Mot = np.asarray(fr['M'], float).reshape(n, 3, 3)
    nan2 = lambda x: np.array([[np.nan, np.nan] if p is None else p for p in x], float)
    com_pad = nan2(fr['com_pad'])
    com_img = nan2(fr['com'])
    kp = np.asarray(fr['kpts'], float)
    kp[kp < 0] = np.nan
    fpad = {s: nan2(fr['foot_pad'][s]) for s in 'LR'}
    planted = {s: np.asarray(fr['planted'][s], bool) for s in 'LR'}
    gimg = {s: nan2(fr['ground'][s]) for s in 'LR'}
    z3 = np.load(CACHE / vid / 'pose3d.npz')
    k3, k2, v3, s3 = z3['k3d'].astype(float), z3['k2d'].astype(float), z3['valid'], z3['s3d']
    m = min(n, len(k3))
    return dict(vid=vid, n=m, fps=fps, H=H, Hinv=Hinv, Mot=Mot[:m], com_pad=com_pad[:m],
                fpad={s: fpad[s][:m] for s in 'LR'}, planted={s: planted[s][:m] for s in 'LR'},
                gimg={s: gimg[s][:m] for s in 'LR'}, com_img=com_img[:m], kp=kp[:m],
                k3=k3[:m], k2=k2[:m], v3=v3[:m], s3=s3[:m], foot_offset=a['calibration'].get('foot_offset', [0, 0]))


def z_scale(k3, valid):
    """RTMW3D 的 z 單位 → 裁切圖像素：讓骨長最穩定的倍率。"""
    P = k3[valid]
    best = None
    for c in np.geomspace(20, 2000, 120):
        cv = []
        for a_, b_ in BONES[:8]:
            d = P[:, a_] - P[:, b_]
            L = np.sqrt(d[:, 0] ** 2 + d[:, 1] ** 2 + (c * d[:, 2]) ** 2)
            cv.append(np.std(L) / np.mean(L))
        sc = float(np.mean(cv))
        if best is None or sc < best[0]:
            best = (sc, c)
    return best[1], best[0]


PAIRS = [(5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 20), (18, 21), (19, 22), (1, 2), (3, 4)]


def align_lr(D):
    """RTMW3D 的左右腳標籤對齊現行骨架（analysis 的腳位置）：逐幀比較兩種配對的距離。"""
    k2, k3 = D['k2'].copy(), D['k3'].copy()
    fl = (k2[:, LHE] + k2[:, LBT] + k2[:, LST]) / 3
    fr = (k2[:, RHE] + k2[:, RBT] + k2[:, RST]) / 3
    gL, gR = D['gimg']['L'], D['gimg']['R']
    same = np.linalg.norm(fl - gL, axis=1) + np.linalg.norm(fr - gR, axis=1)
    swap = np.linalg.norm(fl - gR, axis=1) + np.linalg.norm(fr - gL, axis=1)
    sw = np.nan_to_num(swap) < np.nan_to_num(same)
    for a_, b_ in PAIRS:
        for arr in (k2, k3):
            tmp = arr[sw, a_].copy(); arr[sw, a_] = arr[sw, b_]; arr[sw, b_] = tmp
    D['k2'], D['k3'] = k2, k3
    return float(sw.mean())


def build(D, zsign=1.0):
    """回傳每個方法的 {'c': 重心地面位置（踏板格）, 'dd_feet': 兩腳地面深度差（格）}，以及共用量。"""
    n = D['n']
    valid = D['v3'] & np.isfinite(D['fpad']['L']).all(1) & np.isfinite(D['fpad']['R']).all(1)
    fm_pad = (D['fpad']['L'] + D['fpad']['R']) / 2
    A, B, s, sn = local_camera(D['H'], D['Hinv'], fm_pad)
    cs = np.sqrt(1 - sn ** 2)
    # 2D：RTMW3D 自己的影像座標，轉到參考幀
    K2 = np.full((n, 133, 2), np.nan)
    for j in range(23):
        K2[:, j] = _warp(D['Mot'], D['k2'][:, j])
    fmid2 = (foot_pt(K2, 'L') + foot_pt(K2, 'R')) / 2
    rel2 = K2[:, :23] - fmid2[:, None]
    l = rel2[..., 0] / s[:, None]
    v = -rel2[..., 1] / s[:, None]
    # RTMW3D 深度：z 單位 → 裁切像素 → 影像像素 → 格
    c3, cv3 = z_scale(D['k3'], D['v3'])
    xy3 = D['k3'][..., :2]
    ratio = np.full(n, np.nan)       # 裁切像素 / 影像像素
    num = sum(np.linalg.norm(xy3[:, a_] - xy3[:, b_], axis=1) for a_, b_ in BONES[8:12])
    den = sum(np.linalg.norm(D['k2'][:, a_] - D['k2'][:, b_], axis=1) for a_, b_ in BONES[8:12])
    ok = den > 1
    ratio[ok] = num[ok] / den[ok]
    z3 = zsign * (D['k3'][:, :23, 2] * c3) / ratio[:, None] / s[:, None]
    z3 = z3 - ((z3[:, LHE] + z3[:, LBT] + z3[:, LST]) / 3 + (z3[:, RHE] + z3[:, RBT] + z3[:, RST]) / 3)[:, None] / 2

    def ground(l_, v_, z_):
        d = v_ * sn + z_ * cs
        return l_, d

    out = {}
    # winter2d：現行（analysis.json 的 com_pad，深度＝兩腳深度）
    out['winter2d'] = dict(c=D['com_pad'], dd_feet=np.zeros(n))
    # winter2d_bv：現行 2D 重心，但「垂直方向」用身體本身的平均軸線（肩中點−腳踝中點），
    # 不用畫面垂直／消失點；深度一樣假設在兩腳深度
    kp = D['kp']
    neck2 = (kp[:, 5] + kp[:, 6]) / 2; ank2 = (kp[:, 15] + kp[:, 16]) / 2
    ax2 = neck2 - ank2
    okb = np.isfinite(ax2).all(1)
    upi = np.nanmedian(ax2[okb] / (np.linalg.norm(ax2[okb], axis=1, keepdims=True) + 1e-9), 0)
    upi /= np.linalg.norm(upi)
    eperp = np.array([-upi[1], upi[0]])
    if eperp[0] < 0:
        eperp = -eperp
    gmid = (D['gimg']['L'] + D['gimg']['R']) / 2
    ci = _warp(D['Mot'], D['com_img']); gi = _warp(D['Mot'], gmid)
    A2 = np.full((n, 2), np.nan); s2 = np.full(n, np.nan)
    e_ = 1e-3
    for i in np.where(np.isfinite(fm_pad).all(1))[0]:
        q = fm_pad[i]
        p0 = _apply_h(D['Hinv'], [q])[0]
        J = np.c_[(_apply_h(D['Hinv'], [q + [e_, 0]])[0] - p0) / e_, (_apply_h(D['Hinv'], [q + [0, e_]])[0] - p0) / e_]
        a2 = np.linalg.solve(J, eperp); a2 /= np.linalg.norm(a2)
        A2[i] = a2; s2[i] = np.linalg.norm(J @ a2)
    lat = ((ci - gi) @ eperp) / s2
    out['winter2d_bv'] = dict(c=fm_pad + lat[:, None] * A2, dd_feet=np.zeros(n))
    # rtmw3d
    P = np.stack([l, v, z3], -1)                          # (n,23,3)
    P133 = np.zeros((n, 133, 3)); P133[:, :23] = P
    cm = winter_com(P133)
    lc, dc = ground(cm[:, 0], cm[:, 1], cm[:, 2])
    c = fm_pad + lc[:, None] * A + dc[:, None] * B + np.asarray(D['foot_offset']) * 0
    dz = foot_pt(P133, 'L')[:, 2] - foot_pt(P133, 'R')[:, 2]
    # RTMW3D 用它自己的座標系：身體平均直立方向當作「上」，水平面上的深度軸（不假設模型知道相機俯角）
    Q = D['k3'][:, :23].copy(); Q[..., 2] *= c3 * zsign             # 裁切像素
    okq = D['v3']
    neck = (Q[:, LSH] + Q[:, RSH]) / 2; ank = (Q[:, LAN] + Q[:, RAN]) / 2
    upv = neck[okq] - ank[okq]
    u = np.nanmean(upv / (np.linalg.norm(upv, axis=1, keepdims=True) + 1e-9), 0); u /= np.linalg.norm(u)
    ex = np.array([1.0, 0, 0]) - u[0] * u; ex /= np.linalg.norm(ex)
    ed = np.array([0, 0, 1.0]) - (u[2] * u) - (ex[2] * ex); ed /= np.linalg.norm(ed)
    shank3 = np.nanmedian(np.r_[np.linalg.norm(Q[okq, LKN] - Q[okq, LAN], axis=1), np.linalg.norm(Q[okq, RKN] - Q[okq, RAN], axis=1)])
    ppp = shank3 / (38.0 / 28.0)                                    # 裁切像素 / 格
    Q133 = np.zeros((n, 133, 3)); Q133[:, :23] = Q
    cq = winter_com(Q133)
    fmq = (foot_pt(Q133, 'L') + foot_pt(Q133, 'R')) / 2
    lq = ((cq - fmq) @ ex) / ppp; dq = ((cq - fmq) @ ed) / ppp
    c = fm_pad + lq[:, None] * A + dq[:, None] * B
    c[~okq] = np.nan
    dd = ((foot_pt(Q133, 'L') - foot_pt(Q133, 'R')) @ ed) / ppp
    out['rtmw3d'] = dict(c=c, dd_feet=dd)
    # bonelen：骨長（2D 投影長的 95 百分位，Taylor 2000）＋深度大小由骨長反推、正負號取 RTMW3D
    chain = [(LHIP, RHIP, None)] + [(LHIP, LKN, None), (LKN, LAN, None), (RHIP, RKN, None), (RKN, RAN, None),
                                    (LAN, LHE, None), (LAN, LBT, None), (LAN, LST, None),
                                    (RAN, RHE, None), (RAN, RBT, None), (RAN, RST, None),
                                    (LHIP, LSH, None), (RHIP, RSH, None), (LSH, LEL, None), (LEL, LWR, None),
                                    (RSH, REL, None), (REL, RWR, None), (LSH, NOSE, None)]
    Pb = np.full((n, 23, 3), np.nan)
    Pb[..., 0], Pb[..., 1] = l, v
    root = (LHIP, RHIP)
    zb = np.full((n, 23), np.nan)
    zb[:, LHIP] = 0.0
    for a_, b_, _ in chain:
        dl = l[:, b_] - l[:, a_]; dv = v[:, b_] - v[:, a_]
        proj = np.hypot(dl, dv)
        L = np.nanpercentile(proj[valid], 95) if valid.any() else np.nan
        mag = np.sqrt(np.clip(L ** 2 - proj ** 2, 0, None))
        sg = np.sign(z3[:, b_] - z3[:, a_])
        sg[sg == 0] = 1
        zb[:, b_] = zb[:, a_] + sg * mag
    zb = zb - ((zb[:, LHE] + zb[:, LBT] + zb[:, LST]) / 3 + (zb[:, RHE] + zb[:, RBT] + zb[:, RST]) / 3)[:, None] / 2
    Pb[..., 2] = zb
    P133b = np.zeros((n, 133, 3)); P133b[:, :23] = Pb
    cmb = winter_com(P133b)
    lcb, dcb = ground(cmb[:, 0], cmb[:, 1], cmb[:, 2])
    fLb, fRb = foot_pt(P133b, 'L'), foot_pt(P133b, 'R')
    out['bonelen'] = dict(c=fm_pad + lcb[:, None] * A + dcb[:, None] * B,
                          dd_feet=ground(fLb[:, 0], fLb[:, 1], fLb[:, 2])[1] - ground(fRb[:, 0], fRb[:, 1], fRb[:, 2])[1])
    # 標準答案：兩腳在踏板上的地面深度差（單應矩陣，雙腳著地時可靠）
    dd_true = ((D['fpad']['L'] - D['fpad']['R']) * B).sum(1)
    shared = dict(A=A, B=B, s=s, sn=sn, fm_pad=fm_pad, dd_true=dd_true, valid=valid, z_scale=c3, z_cv=cv3,
                  up=u.tolist())
    return out, shared


def metrics(D, M, sh):
    n, fps = D['n'], D['fps']
    pl = D['planted']
    both = pl['L'] & pl['R'] & sh['valid']
    single = (pl['L'] ^ pl['R']) & sh['valid']
    res = {}
    win = int(0.3 * fps) | 1
    for name, m in M.items():
        c = m['c'].copy()
        ok = np.isfinite(c).all(1)
        cc = np.c_[_interp_nan(c[:, 0].copy()), _interp_nan(c[:, 1].copy())] if ok.sum() > win else c
        sm = savgol_filter(cc, win, 2, axis=0) if ok.sum() > win else cc
        acc = savgol_filter(cc, win, 2, deriv=2, axis=0) * fps ** 2 if ok.sum() > win else np.full_like(cc, np.nan)
        vel = savgol_filter(cc, win, 2, deriv=1, axis=0) * fps if ok.sum() > win else np.full_like(cc, np.nan)
        # 1) 雙腳著地且重心慢（<0.5 格/秒）：重心投影到兩腳連線的距離 ≤ 0.35 格（約 10 cm）
        st = both & ok & (np.linalg.norm(vel, axis=1) < 0.5)
        fL, fR = D['fpad']['L'], D['fpad']['R']
        seg = fR - fL
        tpar = np.clip(((sm - fL) * seg).sum(1) / ((seg ** 2).sum(1) + 1e-9), 0, 1)
        dist = np.linalg.norm(sm - (fL + tpar[:, None] * seg), axis=1)
        inside = float((dist[st] <= 0.35).mean() * 100) if st.any() else float('nan')
        # 2) 倒單擺：單腳支撐時 重心加速度 · (重心 − 支撐腳) 同號（分可觀測軸 a、深度軸 b）
        sup = np.where(pl['L'][:, None], fL, fR)
        rel = sm - sup
        sel = single & ok & np.isfinite(acc).all(1) & np.isfinite(rel).all(1)
        lip = {}
        for ax, E in (('obs', sh['A']), ('depth', sh['B'])):
            x = (rel[sel] * E[sel]).sum(1); y = (acc[sel] * E[sel]).sum(1)
            lip[ax] = float(np.corrcoef(x, y)[0, 1]) if sel.sum() > 30 and np.std(x) > 1e-6 else float('nan')
        # 2b) 單腳支撐時重心（可觀測軸）比較靠近支撐腳的比例；以及重心相對雙腳中點的變化幅度
        swg = np.where(pl['L'][:, None], fR, fL)
        dst = np.abs(((sm - sup) * sh['A']).sum(1)); dsw = np.abs(((sm - swg) * sh['A']).sum(1))
        s2 = single & ok & np.isfinite(dst) & np.isfinite(dsw) & (np.abs(((sup - swg) * sh['A']).sum(1)) > 0.5)
        stance_closer = float((dst[s2] < dsw[s2]).mean() * 100) if s2.any() else float('nan')
        relx = ((sm - sh['fm_pad']) * sh['A']).sum(1)
        spread = float(np.nanstd(relx[sh['valid'] & ok]))
        # 3) 抖動：重心減掉 0.3 秒平滑後的 RMS（格）
        jit = float(np.sqrt(np.nanmean(((cc - sm)[ok]) ** 2)))
        # 4) 兩腳深度差（有標準答案）
        fv = both & np.isfinite(m['dd_feet']) & np.isfinite(sh['dd_true'])
        e = m['dd_feet'][fv] - sh['dd_true'][fv]
        corr = float(np.corrcoef(m['dd_feet'][fv], sh['dd_true'][fv])[0, 1]) if fv.sum() > 30 and np.std(m['dd_feet'][fv]) > 1e-9 else 0.0
        res[name] = dict(inside_support_pct=round(inside, 1), n_static=int(st.sum()),
                         stance_closer_pct=round(stance_closer, 1), rel_obs_sd=round(spread, 3),
                         lipm_corr_obs=round(lip['obs'], 3), lipm_corr_depth=round(lip['depth'], 3),
                         jitter_panels=round(jit, 4), feet_dz_mae=round(float(np.abs(e).mean()), 3) if fv.any() else float('nan'),
                         feet_dz_corr=round(corr, 3), feet_dz_true_sd=round(float(np.std(sh['dd_true'][fv])), 3) if fv.any() else float('nan'),
                         n_both=int(fv.sum()))
    # 方法兩兩比對（沿 a、b 兩軸，重心相對雙腳中點）
    agree = {}
    names = list(M)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            p, q = names[i], names[j]
            r = {}
            for ax, E in (('obs', sh['A']), ('depth', sh['B'])):
                x = ((M[p]['c'] - sh['fm_pad']) * E).sum(1); y = ((M[q]['c'] - sh['fm_pad']) * E).sum(1)
                ok = np.isfinite(x) & np.isfinite(y) & sh['valid']
                r[f'corr_{ax}'] = round(float(np.corrcoef(x[ok], y[ok])[0, 1]), 3) if ok.sum() > 30 and np.std(x[ok]) > 1e-9 and np.std(y[ok]) > 1e-9 else float('nan')
                r[f'mad_{ax}'] = round(float(np.median(np.abs(x[ok] - y[ok]))), 3) if ok.any() else float('nan')
            agree[f'{p}~{q}'] = r
    return res, agree


def pooled(cells, key_w='n_both'):
    out = {}
    keys = [k for k in next(iter(cells.values())) if not k.startswith('n_')]
    for k in keys:
        vals = np.array([c[k] for c in cells.values()], float)
        w = np.array([max(c.get(key_w, 1), 1) for c in cells.values()], float)
        ok = np.isfinite(vals)
        out[k] = round(float((vals[ok] * w[ok]).sum() / w[ok].sum()), 4) if ok.any() else float('nan')
    return out


def main():
    sys.stdout.reconfigure(errors='replace')
    data = {v: load(v) for v in LIT + UNSCORED}
    swapped = {v: round(align_lr(data[v]), 3) for v in data}
    print('LR swapped fraction', swapped)
    # RTMW3D z 正負號是模型固定的慣例：用所有計分影片的兩腳深度相關性一次決定（不是逐支挑）
    tot = 0.0
    for v in LIT:
        M, sh = build(data[v], 1.0)
        fv = data[v]['planted']['L'] & data[v]['planted']['R'] & np.isfinite(M['rtmw3d']['dd_feet']) & np.isfinite(sh['dd_true'])
        tot += float(np.nansum(M['rtmw3d']['dd_feet'][fv] * sh['dd_true'][fv]))
    zsign = 1.0 if tot >= 0 else -1.0
    print('zsign', zsign)
    res = {m: {} for m in ('winter2d', 'winter2d_bv', 'rtmw3d', 'bonelen')}
    agree_all = {}
    info = {}
    for v in LIT + UNSCORED:
        M, sh = build(data[v], zsign)
        r, ag = metrics(data[v], M, sh)
        info[v] = dict(z_scale=round(sh['z_scale'], 1), z_bone_cv=round(sh['z_cv'], 3),
                       sin_pitch_med=round(float(np.nanmedian(sh['sn'])), 3), px_per_panel_med=round(float(np.nanmedian(sh['s'])), 1))
        for m_ in res:
            res[m_][v] = r[m_]
        agree_all[v] = ag
        np.savez_compressed(CACHE / v / 'com_methods.npz', **{f'{k}_c': M[k]['c'] for k in M},
                            fm_pad=sh['fm_pad'], A=sh['A'], B=sh['B'])
        print(v, {m_: (r[m_]['inside_support_pct'], r[m_]['feet_dz_corr'], r[m_]['feet_dz_mae'], r[m_]['lipm_corr_obs'], r[m_]['lipm_corr_depth'])
                  for m_ in r}, info[v], flush=True)
        heartbeat()
    for m_ in res:
        res[m_]['pooled'] = pooled({v: res[m_][v] for v in LIT})
    agree = {}
    for pair in agree_all[LIT[0]]:
        agree[pair] = pooled({v: {**agree_all[v][pair], 'n_both': 1} for v in LIT})
    res['agreement'] = agree
    res['_per_video_agreement'] = agree_all
    res['_info'] = dict(zsign=zsign, videos=info, lr_swapped=swapped)
    save_json(res, RESULTS / 'com.json')
    for m_ in ('winter2d', 'winter2d_bv', 'rtmw3d', 'bonelen'):
        print(m_, res[m_]['pooled'])
    print('agreement', agree)


if __name__ == '__main__':
    main()
