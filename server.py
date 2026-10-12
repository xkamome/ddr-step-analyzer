"""DDR 踩點／重心分析 — 本機伺服器。

啟動：python server.py  →  http://127.0.0.1:8765
  --no-browser   不自動開瀏覽器
  --no-auto-exit 不自動結束（開發用）

瀏覽器分頁每 4 秒回報一次；所有分頁關閉（或 90 秒沒回報）且沒有影片在分析時，10 秒後自動結束。
"""
from __future__ import annotations

import json
import queue
import re
import shutil
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ddrpose import analyze as A
from ddrpose import pose as P

ROOT = Path(__file__).parent
DATA = ROOT / 'data'
UPLOADS = DATA / '_uploads'
DATA.mkdir(exist_ok=True)

PORT = 8765
URL = f'http://127.0.0.1:{PORT}'

app = FastAPI()
jobs: dict[str, dict] = {}
work: queue.Queue[str] = queue.Queue()
lock = threading.Lock()
current = {'vid': None}                  # 正在分析的影片
clients: dict[str, float] = {}           # 瀏覽器分頁 ID → 最後回報時間
seen = {'any': False}


def _job_file(vid):
    return DATA / vid / 'job.json'


def _set(vid, **kw):
    with lock:
        jobs.setdefault(vid, {}).update(kw)
        _job_file(vid).write_text(json.dumps(jobs[vid], ensure_ascii=False), encoding='utf-8')


def _worker():
    while True:
        vid = work.get()
        current['vid'] = vid
        d = DATA / vid
        try:
            src = jobs[vid]['source']
            _set(vid, status='preview', progress=0)
            P.make_preview(src, d)
            _set(vid, status='pose', progress=0)
            P.extract(src, d, jobs[vid].get('quality', 'accurate'),
                      progress=lambda i, n, eta: _set(vid, progress=round(i / max(n, 1), 3), eta=round(eta)))
            _set(vid, status='analyze')
            A.analyze(d)
            _set(vid, status='done', progress=1)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _set(vid, status='error', error=str(e))
        finally:
            current['vid'] = None
            work.task_done()


def _new_id(name: str) -> str:
    base = re.sub(r'[^0-9A-Za-z_-]+', '_', Path(name).stem).strip('_') or 'video'
    vid, i = base, 2
    while (DATA / vid).exists():
        vid, i = f'{base}_{i}', i + 1
    return vid


def _enqueue(source: Path, quality: str) -> str:
    vid = _new_id(source.name)
    (DATA / vid).mkdir(parents=True)
    _set(vid, source=str(source), name=source.name, quality=quality, status='queued', progress=0)
    work.put(vid)
    return vid


def _status(d: Path) -> dict:
    job = jobs.get(d.name)
    if job is None and _job_file(d.name).exists():
        job = json.loads(_job_file(d.name).read_text(encoding='utf-8'))
    job = dict(job or {})
    if (d / 'pose.npz').exists() and job.get('status') is None:
        job['status'] = 'done'          # 由命令列跑完的（沒有 job.json）
    elif not job:
        job['status'] = 'processing'   # 由命令列在跑
    if (d / 'meta.json').exists():
        meta = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
        job.setdefault('name', Path(meta['source']).name)
        job['duration'] = meta.get('duration')
    job['id'] = d.name
    job['manual'] = 'points' in _read_calib(d)
    return job


OPT_KEYS = ('lights', 'hidden_arms')     # calib.json 裡與板位無關的每支影片設定（重設校正時保留）


def _read_calib(d: Path) -> dict:
    f = d / 'calib.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}


def _write_calib(d: Path, cal: dict):
    if cal:
        (d / 'calib.json').write_text(json.dumps(cal), encoding='utf-8')
    else:
        (d / 'calib.json').unlink(missing_ok=True)


def _opts_only(d: Path) -> dict:
    return {k: v for k, v in _read_calib(d).items() if k in OPT_KEYS}


# ───────────── API ─────────────

class AddReq(BaseModel):
    path: str
    quality: str = 'accurate'


class CalibReq(BaseModel):
    points: dict[str, list[float]]
    foot_offset: list[float] | None = None     # 手動的腳位置偏移（踏板座標，格）


class OptsReq(BaseModel):
    lights: bool = True                        # 踏板會亮燈
    hidden_arms: list[str] = []                # 不在畫面裡的手：'L' / 'R'


@app.get('/api/videos')
def list_videos():
    return [_status(d) for d in sorted(DATA.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if d.is_dir() and not d.name.startswith('_')]


@app.post('/api/videos')
def add_video(req: AddReq):
    src = Path(req.path.strip().strip('"'))
    if not src.is_file():
        raise HTTPException(400, f'ファイルが見つかりません／找不到檔案：{src}')
    if req.quality not in P.POSE_MODELS:
        raise HTTPException(400, 'quality は accurate か fast／quality 必須是 accurate 或 fast')
    return {'id': _enqueue(src, req.quality)}


@app.post('/api/upload')
async def upload(file: UploadFile = File(...), quality: str = Form('accurate')):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dst = UPLOADS / Path(file.filename or 'video.mp4').name
    with dst.open('wb') as out:
        shutil.copyfileobj(file.file, out, length=8 << 20)
    return {'id': _enqueue(dst, quality)}


def _vdir(vid: str) -> Path:
    d = DATA / vid
    if not re.fullmatch(r'[0-9A-Za-z_-]+', vid) or not d.is_dir():
        raise HTTPException(404, '動画が見つかりません／沒有這支影片')
    return d


@app.get('/api/videos/{vid}/analysis')
def get_analysis(vid: str):
    d = _vdir(vid)
    if not (d / 'pose.npz').exists():
        raise HTTPException(409, '骨格認識がまだ終わっていません／姿勢擷取尚未完成')
    f = d / 'analysis.json'
    if not f.exists() or f.stat().st_mtime < (d / 'pose.npz').stat().st_mtime:
        A.analyze(d)
    return FileResponse(f, media_type='application/json')


@app.post('/api/videos/{vid}/calibration')
def set_calibration(vid: str, req: CalibReq):
    d = _vdir(vid)
    if set(req.points) != set('LDUR') or any(len(v) != 2 for v in req.points.values()):
        raise HTTPException(400, 'L/D/U/R の 4 点が必要です／需要 L/D/U/R 四個點')
    cal = {**_opts_only(d), 'points': req.points}
    if req.foot_offset and len(req.foot_offset) == 2:
        cal['foot_offset'] = req.foot_offset
    _write_calib(d, cal)
    A.analyze(d)
    return FileResponse(d / 'analysis.json', media_type='application/json')


@app.delete('/api/videos/{vid}/calibration')
def reset_calibration(vid: str):
    d = _vdir(vid)
    _write_calib(d, _opts_only(d))
    A.analyze(d)
    return FileResponse(d / 'analysis.json', media_type='application/json')


@app.post('/api/videos/{vid}/options')
def set_options(vid: str, req: OptsReq):
    d = _vdir(vid)
    cal = {k: v for k, v in _read_calib(d).items() if k not in OPT_KEYS}
    if not req.lights:
        cal['lights'] = False
    arms = sorted({s for s in req.hidden_arms if s in ('L', 'R')})
    if arms:
        cal['hidden_arms'] = arms
    _write_calib(d, cal)
    A.analyze(d)
    return FileResponse(d / 'analysis.json', media_type='application/json')


@app.post('/api/heartbeat')
def heartbeat(id: str):
    clients[id] = time.time(); seen['any'] = True
    return {'ok': True, 'busy': current['vid'] is not None}


@app.post('/api/bye')
def bye(id: str):
    clients.pop(id, None)
    return {'ok': True}


@app.middleware('http')
async def no_cache_ui(request, call_next):
    # 介面檔（html/js/css）每次都向伺服器確認，更新後重新整理就生效
    resp = await call_next(request)
    if not request.url.path.startswith(('/data/', '/api/')):
        resp.headers['Cache-Control'] = 'no-cache'
    return resp


app.mount('/data', StaticFiles(directory=DATA), name='data')
app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')


def _watchdog(server):
    """沒有瀏覽器分頁、也沒有影片在分析時自動結束。"""
    started, empty_since = time.time(), None
    while not server.should_exit:
        time.sleep(2)
        now = time.time()
        alive = [c for c, ts in clients.items() if now - ts < 90]
        busy = current['vid'] is not None or not work.empty()
        if alive or busy:
            empty_since = None
            continue
        if not seen['any']:
            if now - started > 600:          # 10 分鐘都沒有開過網頁
                break
            continue
        empty_since = empty_since or now
        if now - empty_since > 10:           # 留 10 秒給「重新整理頁面」
            break
    print('ブラウザが閉じられたので終了します。／瀏覽器已關閉，伺服器結束。', flush=True)
    server.should_exit = True


def _already_running():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', PORT)) == 0


def main():
    sys.stdout.reconfigure(errors='replace')   # 主控台是 cp932 等編碼時，無法顯示的字元以 ? 代替
    if _already_running():
        print(f'すでに起動しています／已經在執行：{URL}', flush=True)
        if '--no-browser' not in sys.argv:
            webbrowser.open(URL)
        return
    # 伺服器重啟時，把沒跑完的工作重新排入
    for d in DATA.iterdir():
        jf = d / 'job.json'
        if d.is_dir() and jf.exists():
            job = json.loads(jf.read_text(encoding='utf-8'))
            jobs[d.name] = job
            if job.get('status') not in ('done', 'error') and not (d / 'pose.npz').exists():
                work.put(d.name)
    threading.Thread(target=_worker, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, log_level='warning'))
    if '--no-auto-exit' not in sys.argv:
        threading.Thread(target=_watchdog, args=(server,), daemon=True).start()
    if '--no-browser' not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
    print(f'DDR ステップ解析／DDR 踩點分析：{URL}', flush=True)
    print('このウィンドウは解析サーバーです。ブラウザを閉じると自動で終了します（解析中は完了まで待ちます）。', flush=True)
    print('這個視窗是分析伺服器；關閉瀏覽器後會自動結束（分析中會等完成）。', flush=True)
    server.run()


if __name__ == '__main__':
    main()
