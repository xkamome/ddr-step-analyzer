"""腳部光流：在預覽影片上，以每隻腳（現行骨架、未平滑）為中心取小區塊，算前後幀的 Farneback 光流。
骨架點會抖，但鞋子像素的實際移動很穩 → 給落地判定模型當額外特徵。

輸出 cache/<vid>/flow.npz：flow (N, 2 腳, 4) = [中位 dx, 中位 dy, 中位速度, 下半區塊中位速度]（以小腿長/秒為單位）
用法：experiments/py.sh experiments/flow_features.py [vid ...]
"""
from __future__ import annotations

import json
import sys
import time

import cv2
import numpy as np

from common import ALL, CACHE, DATA, heartbeat
from contact_features import prep_raw

from ddrpose.analyze import LEG, fix_left_right


def foot_tracks(vid):
    z = np.load(DATA / vid / 'pose.npz')
    K, S, valid = z['kpts'].astype(float), z['scores'].astype(float), z['valid']
    K, S, _ = fix_left_right(K, S, valid)
    X, rel = prep_raw(K, S, valid)
    out = {}
    for s, j in LEG.items():
        g = (X[:, j['heel']] + X[:, j['bt']] + X[:, j['st']]) / 3
        sh = np.linalg.norm(X[:, j['knee']] - X[:, j['ankle']], axis=1)
        out[s] = (g, sh)
    return out, len(K)


def run(vid):
    f = CACHE / vid / 'flow.npz'
    if f.exists():
        print(vid, 'exists'); return
    meta = json.loads((DATA / vid / 'meta.json').read_text(encoding='utf-8'))
    tracks, n = foot_tracks(vid)
    cap = cv2.VideoCapture(str(DATA / vid / 'preview.mp4'))
    pw = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    sc = pw / meta['width']
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    shm = np.nanmedian(np.r_[tracks['L'][1], tracks['R'][1]]) * sc
    F = np.full((n, 2, 4), np.nan, np.float32)
    prev = None
    t0 = time.time()
    i = 0
    while i < n:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        if prev is not None:
            H, W = g.shape
            for si, s in enumerate('LR'):
                c, sh = tracks[s][0][i], tracks[s][1][i]
                if not np.isfinite(c).all():
                    continue
                L = (sh if np.isfinite(sh) else shm / sc) * sc
                r = int(max(10, 0.55 * L))
                x, y = int(c[0] * sc), int(c[1] * sc)
                x0, x1, y0, y1 = max(0, x - r), min(W, x + r), max(0, y - r), min(H, y + r)
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
                low = mag[mag.shape[0] // 2:]
                k = fps / max(L, 1.0)
                F[i, si] = [np.median(fl[..., 0]) * k, np.median(fl[..., 1]) * k, np.median(mag) * k, np.median(low) * k]
        prev = g
        i += 1
        if i % 1000 == 0:
            heartbeat()
    cap.release()
    np.savez_compressed(f, flow=F)
    print(vid, f'{n} frames {time.time() - t0:.0f}s', flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    for v in sys.argv[1:] or ALL:
        run(v)
