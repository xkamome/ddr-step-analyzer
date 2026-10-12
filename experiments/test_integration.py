"""整合測試：main 分支（舊版）與目前分支（新版）的 analyze.py 在 cache 複本上各跑一次，比較
  1) 有燈影片：步數 F1（對亮燈起點）——新版不可變差；
  2) 沒燈模式（lights:false）：步數 F1（新版用的模型有在這些影片上訓練過 → 樣本內，只當功能檢查）；
  3) 重心：雙腳靜止時落在兩腳之間的比例。
不動 data/。輸出 results/integration.json。

用法：experiments/py.sh experiments/test_integration.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
import io
import os
import time
from pathlib import Path

import numpy as np

from common import ALL, CACHE, DATA, EXP, LIT, RESULTS, ROOT, heartbeat, load_json, save_json, score_events
from eval_contact import SYM2IDX, gt_events, tol_frames

MAIN_SRC = CACHE / 'main_src'
FILES = ('meta.json', 'pose.npz', 'motion.npz', 'vertical.json', 'glow.npz', 'preview.mp4', 'background.jpg')


def checkout_main():
    if (MAIN_SRC / 'ddrpose' / 'analyze.py').exists():
        return
    MAIN_SRC.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(['git', 'archive', 'main', 'ddrpose'], cwd=ROOT, capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(data)).extractall(MAIN_SRC)


def prep(vid, tag):
    d = CACHE / vid / f'integ_{tag}'
    d.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        if (DATA / vid / f).exists() and not (d / f).exists():
            shutil.copy2(DATA / vid / f, d / f)
    return d


RUNNER = r'''
import sys, json
sys.path.insert(0, sys.argv[1])
sys.stdout.reconfigure(errors='replace')
from ddrpose.analyze import analyze
calib = json.loads(sys.argv[3]) if sys.argv[3] != 'null' else None
r = analyze(sys.argv[2], calib)
print(json.dumps({'n_steps': len(r['steps']), 'model_only': r['calibration'].get('model_only_steps'),
                  'light_only': r['calibration'].get('light_only_steps')}))
'''


PROD_PY = Path(os.environ['LOCALAPPDATA']) / 'ddr-pose' / 'venv' / 'Scripts' / 'python.exe'


def run(src_root, d, calib):
    py = str(PROD_PY)          # 用正式版 venv（不含 lightgbm、numpy 2.3）跑，確認部署環境可用
    p = subprocess.run([py, '-X', 'utf8', '-c', RUNNER, str(src_root), str(d), json.dumps(calib)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
    if p.returncode != 0:
        return dict(ok=False, err=p.stderr[-800:])
    return dict(ok=True, **json.loads(p.stdout.strip().splitlines()[-1]))


def step_f1(d, vid):
    a = json.loads((d / 'analysis.json').read_text(encoding='utf-8'))
    pe = [(int(st['frame']), SYM2IDX[st['panel']]) for st in a['steps'] if st.get('panel') in SYM2IDX]
    return score_events(gt_events(vid), pe, a['fps'], tol_frames(a['fps']))


def com_inside(d):
    a = json.loads((d / 'analysis.json').read_text(encoding='utf-8'))
    fr = a['frames']
    nan2 = lambda x: np.array([[np.nan, np.nan] if p is None else p for p in x], float)
    c, fL, fR = nan2(fr['com_pad']), nan2(fr['foot_pad']['L']), nan2(fr['foot_pad']['R'])
    both = np.asarray(fr['planted']['L'], bool) & np.asarray(fr['planted']['R'], bool)
    fps = a['fps']
    from scipy.signal import savgol_filter
    from ddrpose.analyze import _interp_nan
    ok = np.isfinite(c).all(1) & np.isfinite(fL).all(1) & np.isfinite(fR).all(1)
    if ok.sum() < 50:
        return float('nan')
    cc = np.c_[_interp_nan(c[:, 0].copy()), _interp_nan(c[:, 1].copy())]
    win = int(0.3 * fps) | 1
    sm = savgol_filter(cc, win, 2, axis=0); vel = savgol_filter(cc, win, 2, deriv=1, axis=0) * fps
    st = both & ok & (np.linalg.norm(vel, axis=1) < 0.5)
    seg = fR - fL
    tp = np.clip(((sm - fL) * seg).sum(1) / ((seg ** 2).sum(1) + 1e-9), 0, 1)
    dist = np.linalg.norm(sm - (fL + tp[:, None] * seg), axis=1)
    return round(float((dist[st] <= 0.35).mean() * 100), 1) if st.any() else float('nan')


def user_calib(vid, lights=None):
    c = load_json(DATA / vid / 'calib.json', None)
    if c is None and lights is None:
        return None
    c = dict(c or {})
    if lights is not None:
        c['lights'] = lights
        if not c.get('points'):
            a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
            c['points'] = a['calibration']['points']
    return c


def main():
    sys.stdout.reconfigure(errors='replace')
    checkout_main()
    t0 = time.time()
    out = {'videos': {}, 'nolight': {}}
    for vid in ALL:
        r = {}
        for tag, src in (('before', MAIN_SRC), ('after', ROOT)):
            d = prep(vid, tag)
            res = run(src, d, user_calib(vid))
            r[f'{tag}_run'] = res
            if res['ok']:
                if vid in LIT:
                    r[f'f1_{tag}'] = step_f1(d, vid)['f1']
                r[f'com_inside_{tag}'] = com_inside(d)
            heartbeat()
            print(vid, tag, res, r.get(f'f1_{tag}'), r.get(f'com_inside_{tag}'), f'{time.time() - t0:.0f}s', flush=True)
        r['ok'] = r['before_run']['ok'] and r['after_run']['ok'] and r['after_run'].get('n_steps', 0) > 0
        out['videos'][vid] = r
        save_json(out, RESULTS / 'integration_partial.json')
    # 沒燈模式（lights:false）：新舊版
    for vid in LIT:
        r = {}
        for tag, src in (('before', MAIN_SRC), ('after', ROOT)):
            d = prep(vid, f'nolight_{tag}')
            res = run(src, d, user_calib(vid, lights=False))
            r[f'{tag}_run'] = res
            if res['ok']:
                r[f'f1_{tag}'] = step_f1(d, vid)
            heartbeat()
            print(vid, 'nolight', tag, res, (r.get(f'f1_{tag}') or {}).get('f1'), flush=True)
        out['nolight'][vid] = r
        save_json(out, RESULTS / 'integration_partial.json')
    out['note'] = ('nolight 的 after 是樣本內（部署模型用這 5 支影片訓練），只證明整合後流程正確；'
                   '沒燈的真實準確度看 contact.json 的 learned_halpe（留一支法）。')
    save_json(out, RESULTS / 'integration.json')
    (RESULTS / 'integration_partial.json').unlink(missing_ok=True)
    print('done', f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
