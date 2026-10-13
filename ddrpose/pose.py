"""姿勢擷取：RTMPose (Halpe26, 含腳跟/腳趾) 逐幀追蹤主要玩家。

輸出 data/<vid>/pose.npz：
  t      (N,)       每幀時間（秒，相對第一幀）
  kpts   (N,26,2)   影像座標（已套用旋轉後的畫面）
  scores (N,26)     信心度
  valid  (N,)       該幀是否有抓到人
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np

DET_URL = ('https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/'
           'yolox_m_8xb8-300e_humanart-c2c7a14a.zip')
POSE_MODELS = {
    # 準確：腳部點明顯較穩，CPU 約 0.4 秒/幀
    'accurate': ('https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/'
                 'rtmpose-x_simcc-body7_pt-body7-halpe26_700e-384x288-7fb6e239_20230606.zip',
                 (288, 384)),
    # 快速：約 0.02 秒/幀，遮擋時左右腳較容易混淆
    'fast': ('https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/'
             'rtmpose-m_simcc-body7_pt-body7-halpe26_700e-256x192-4d3e73dd_20230605.zip',
             (192, 256)),
}
DET_EVERY = 10          # 每 N 幀重新偵測一次人框，其餘幀用上一幀骨架推框
LOWER_BODY = list(range(11, 17)) + list(range(20, 26))  # 臀膝踝＋腳趾腳跟


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


def extract(video: str | Path, out_dir: str | Path, quality: str = 'accurate', progress=None):
    from rtmlib import YOLOX, RTMPose

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pose_url, pose_size = POSE_MODELS[quality]
    det = YOLOX(DET_URL, model_input_size=(640, 640), backend='onnxruntime', device='cpu')
    pose = RTMPose(pose_url, model_input_size=pose_size, backend='onnxruntime', device='cpu')

    cap = cv2.VideoCapture(str(video))
    n_est = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ts, K, S, V = [], [], [], []
    prev_box = None
    i = 0
    t_start = time.time()
    w = h = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        ts.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)

        cands = []
        if prev_box is None or i % DET_EVERY == 0:
            boxes = det(frame)
            cands = [b[:4] for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) > 5000]
        if prev_box is not None:
            # 追蹤：優先與上一幀重疊最多的框；沒有就沿用上一幀骨架推出的框
            if cands:
                best = max(cands, key=lambda b: _iou(b, prev_box))
                cands = [best] if _iou(best, prev_box) > 0.3 else [prev_box]
            else:
                cands = [prev_box]

        best = None
        for b in cands:
            k, s = pose(frame, bboxes=[b])
            score = float(s[0][LOWER_BODY].mean())
            if best is None or score > best[2]:
                best = (k[0], s[0], score)

        if best is not None and best[2] > 0.25:
            K.append(best[0]); S.append(best[1]); V.append(True)
            prev_box = _bbox_from_kpts(best[0], best[1], w, h)
        else:
            K.append(np.zeros((26, 2))); S.append(np.zeros(26)); V.append(False)
            prev_box = None
        i += 1
        if progress and i % 15 == 0:
            el = time.time() - t_start
            progress(i, n_est, el / i * max(0, n_est - i))

    cap.release()
    t = np.asarray(ts)
    t = t - t[0]
    np.savez_compressed(out_dir / 'pose.npz', t=t, kpts=np.asarray(K, np.float32),
                        scores=np.asarray(S, np.float32), valid=np.asarray(V))
    meta = {'source': str(video), 'width': w, 'height': h, 'frames': len(t),
            'duration': float(t[-1]) if len(t) else 0, 'quality': quality,
            'seconds': round(time.time() - t_start, 1)}
    (out_dir / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return meta


def ffmpeg_exe() -> str:
    """系統的 ffmpeg；沒有的話用 imageio-ffmpeg 內建的版本（pip 安裝即附）。"""
    import shutil
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError('ffmpeg が見つかりません／找不到 ffmpeg（請重新執行 setup.bat）／'
                           'ffmpeg not found (please run setup.bat again)') from e


def download_models():
    """預先下載所有模型（安裝時用，之後可離線）。"""
    from rtmlib import YOLOX, RTMPose
    YOLOX(DET_URL, model_input_size=(640, 640), backend='onnxruntime', device='cpu')
    for url, size in POSE_MODELS.values():
        RTMPose(url, model_input_size=size, backend='onnxruntime', device='cpu')


def make_preview(video: str | Path, out_dir: str | Path):
    """轉成瀏覽器可播的 H.264 mp4（旋轉燒進畫面、保留原時間戳）。"""
    import subprocess
    out = Path(out_dir) / 'preview.mp4'
    cmd = [ffmpeg_exe(), '-v', 'error', '-y', '-i', str(video), '-map', '0:v:0',
           '-vf', "scale='if(gt(iw,ih),1280,-2)':'if(gt(iw,ih),-2,1280)'",
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24', '-pix_fmt', 'yuv420p',
           '-fps_mode', 'passthrough', '-map', '0:a:0?', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(out)]
    subprocess.run(cmd, check=True)
    return out


if __name__ == '__main__':
    import argparse
    import sys
    if '--download-models' in sys.argv:
        download_models()
        print('models ok')
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('out_dir')
    ap.add_argument('--quality', default='accurate', choices=list(POSE_MODELS))
    a = ap.parse_args()
    make_preview(a.video, a.out_dir)
    print(extract(a.video, a.out_dir, a.quality,
                  progress=lambda i, n, eta: print(f'{i}/{n} eta {eta/60:.1f} min', flush=True)))
