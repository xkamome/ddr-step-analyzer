"""標準答案：踏板亮燈。用 analysis.json 裡最終的板位（參考幀座標）＋ glow.npz 的逐幀亮燈圖，
算每幀四塊板的亮燈程度與「由暗轉亮」起點。

輸出 cache/<vid>/gt.npz（act (N,4) 依 LDUR、onsets (M,2) [frame, panel_idx]、pressed (N,4) bool）
     results/gt.json（每支影片統計）
"""
from __future__ import annotations

import json
import sys

import numpy as np

from common import CACHE, DATA, LIT, RESULTS, save_json

from ddrpose.analyze import _h_from_points
from ddrpose.glow import light_onsets, panel_lights

PANELS = 'LDUR'


def pressed_from(act, on=0.4, off=0.2):
    out = np.zeros(len(act), bool)
    state = False
    for i, v in enumerate(act):
        state = (v > off) if state else (v > on)
        out[i] = state
    return out


def build(vid):
    a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
    pts = {k: np.asarray(v, float) for k, v in a['calibration']['points'].items() if k in PANELS}
    z = np.load(DATA / vid / 'glow.npz')
    Fr, s = z['Fr'], float(z['scale'])
    lights = panel_lights(Fr, s, np.linalg.inv(_h_from_points(pts)))
    act = np.stack([lights[k] for k in PANELS], 1)
    ons = light_onsets(lights)
    onsets = np.array([[f, PANELS.index(k)] for f, k in ons], int).reshape(-1, 2)
    pressed = np.stack([pressed_from(act[:, j]) for j in range(4)], 1)
    out = CACHE / vid
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / 'gt.npz', act=act.astype(np.float32), onsets=onsets, pressed=pressed,
                        play=z['play'])
    fps = a['fps']
    play_s = z['play'].sum() / fps
    n_steps = len(a['steps'])
    n_lit = sum(1 for st in a['steps'] if st.get('lit'))
    return dict(n_onsets=len(onsets), fps=fps, n_frames=len(act), play_s=round(play_s, 1),
                onsets_per_min=round(len(onsets) / max(play_s, 1) * 60, 1),
                per_panel={k: int((onsets[:, 1] == i).sum()) for i, k in enumerate(PANELS)},
                analysis_steps=n_steps, analysis_lit_steps=n_lit,
                calib_source=a['calibration'].get('source'), view=a['view'].get('mode'))


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    res = {}
    for v in LIT:
        res[v] = build(v)
        print(v, res[v])
    save_json(res, RESULTS / 'gt.json')
