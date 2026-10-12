"""背景重跑骨架：RTMW-x（2D 133 點）＋ RTMW3D-x（3D 133 點），OpenVINO 內顯。
追蹤邏輯與 ddrpose/pose.py 相同（每 10 幀重新偵測、IoU 追蹤、下半身分數選框）。
逐支影片存 experiments/cache/<vid>/pose_rtmw.npz、pose3d.npz；每 500 幀存 .partial 可續跑。

用法：experiments/py.sh experiments/extract_poses.py [vid ...]   （預設 7 支全部）
"""
from __future__ import annotations

import sys
import time

import cv2
import numpy as np

from common import ALL, CACHE, EXP, heartbeat, source_path, wb_to_halpe

sys.stdout.reconfigure(errors='replace')
DET_URL = ('https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/'
           'yolox_m_8xb8-300e_humanart-c2c7a14a.zip')
RTMW_URL = ('https://download.openmmlab.com/mmpose/v1/projects/rtmw/onnx_sdk/'
            'rtmw-dw-x-l_simcc-cocktail14_270e-384x288_20231122.zip')
RTMW3D_URL = ('https://huggingface.co/Soykaf/RTMW3D-x/resolve/main/onnx/'
              'rtmw3d-x_8xb64_cocktail14-384x288-b0a0eab7_20240626.onnx')
DET_EVERY = 10
# 下半身（COCO-WholeBody）：臀膝踝＋腳趾腳跟
LOWER_WB = list(range(11, 17)) + list(range(17, 23))
LOG = EXP / '_logs' / 'extract.log'


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _bbox_from_kpts(k, s, w, h, thr=0.3, expand=1.25):
    m = s > thr
    if m.sum() < 4:
        return None
    pts = k[m]
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) / 2 * expand + 20, (y1 - y0) / 2 * expand + 20
    return np.array([max(0, cx - hw), max(0, cy - hh), min(w, cx + hw), min(h, cy + hh)])


def load_models(device):
    from rtmlib import YOLOX, RTMPose, RTMPose3d
    det = YOLOX(DET_URL, model_input_size=(640, 640), backend='openvino', device=device)
    pose = RTMPose(RTMW_URL, model_input_size=(288, 384), backend='openvino', device=device)
    pose3d = RTMPose3d(RTMW3D_URL, model_input_size=(288, 384), backend='openvino', device=device)
    return det, pose, pose3d


def extract(vid, models):
    out = CACHE / vid
    out.mkdir(parents=True, exist_ok=True)
    f2, f3, fp = out / 'pose_rtmw.npz', out / 'pose3d.npz', out / 'partial.npz'
    if f2.exists() and f3.exists():
        log(f'{vid}: already done')
        return
    det, pose, pose3d = models
    src = source_path(vid)
    cap = cv2.VideoCapture(str(src))
    n_est = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    keys = ('t', 'k133', 's133', 'box', 'valid', 'k3d', 's3d', 'k3d2d')
    acc = {k: [] for k in keys}
    start = 0
    if fp.exists():
        z = np.load(fp)
        acc = {k: list(z[k]) for k in keys}
        start = len(acc['t'])
        log(f'{vid}: resume at frame {start}')
    prev_box = None
    if start:
        b = acc['box'][-1]
        prev_box = None if not acc['valid'][-1] else np.asarray(b)
    i = 0
    t0 = time.time()
    w = h = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i < start:
            i += 1
            continue
        h, w = frame.shape[:2]
        acc['t'].append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
        cands = []
        if prev_box is None or i % DET_EVERY == 0:
            boxes = det(frame)
            cands = [b[:4] for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) > 5000]
        if prev_box is not None:
            if cands:
                best = max(cands, key=lambda b: _iou(b, prev_box))
                cands = [best] if _iou(best, prev_box) > 0.3 else [prev_box]
            else:
                cands = [prev_box]
        best = None
        for b in cands:
            k, s = pose(frame, bboxes=[b])
            score = float(s[0][LOWER_WB].mean())
            if best is None or score > best[2]:
                best = (k[0], s[0], score, np.asarray(b, float))
        if best is not None and best[2] > 0.25:
            k3, s3, _, k3_2d = pose3d(frame, bboxes=[best[3]])
            acc['k133'].append(best[0].astype(np.float32)); acc['s133'].append(best[1].astype(np.float32))
            acc['box'].append(best[3].astype(np.float32)); acc['valid'].append(True)
            acc['k3d'].append(k3[0].astype(np.float32)); acc['s3d'].append(s3[0].astype(np.float32))
            acc['k3d2d'].append(k3_2d[0].astype(np.float32))
            prev_box = _bbox_from_kpts(best[0][:23], best[1][:23], w, h)
        else:
            acc['k133'].append(np.zeros((133, 2), np.float32)); acc['s133'].append(np.zeros(133, np.float32))
            acc['box'].append(np.zeros(4, np.float32)); acc['valid'].append(False)
            acc['k3d'].append(np.zeros((133, 3), np.float32)); acc['s3d'].append(np.zeros(133, np.float32))
            acc['k3d2d'].append(np.zeros((133, 2), np.float32))
            prev_box = None
        i += 1
        if (i - start) % 100 == 0:
            el = time.time() - t0
            log(f'{vid}: {i}/{n_est} frames, {el / (i - start):.3f}s/frame')
            heartbeat_bg()
        if (i - start) % 500 == 0:
            np.savez(fp, **{k: np.asarray(v) for k, v in acc.items()})
    cap.release()
    A = {k: np.asarray(v) for k, v in acc.items()}
    t = A['t'] - A['t'][0]
    K, S = wb_to_halpe(A['k133'], A['s133'])
    np.savez_compressed(f2, t=t, kpts=K, scores=S, valid=A['valid'], k133=A['k133'], s133=A['s133'], box=A['box'])
    np.savez_compressed(f3, t=t, k3d=A['k3d'], s3d=A['s3d'], k2d=A['k3d2d'], valid=A['valid'])
    if fp.exists():
        fp.unlink()
    log(f'{vid}: DONE {len(t)} frames in {time.time() - t0:.0f}s')


def heartbeat_bg():
    (EXP / '_logs' / 'extract_alive').touch()


if __name__ == '__main__':
    vids = sys.argv[1:] or ALL
    device = 'gpu'
    models = load_models(device)
    log(f'models loaded on {device}')
    for v in vids:
        try:
            extract(v, models)
        except Exception as e:  # 一支失敗不影響其他支
            import traceback
            log(f'{v}: ERROR {e!r}\n{traceback.format_exc()}')
    log('ALL DONE')
