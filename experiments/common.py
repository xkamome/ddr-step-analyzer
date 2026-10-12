"""實驗共用：影片清單、路徑、讀資料、評分。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / 'experiments'
CACHE = EXP / 'cache'
RESULTS = EXP / 'results'
DATA = ROOT / 'data'
sys.path.insert(0, str(ROOT))

# 影片清單放在 videos.local.json（不進 git：影片是個人資料）。格式：
#   LIT：計分影片（亮燈可當標準答案）；UNSCORED：只跑不計分（不亮燈、或亮燈偵測不到的機台）；
#   ALL：全部；GROUP：留一支法的分組（同一次遊玩的不同畫面要同組）；SHORT：報告用短名
_V = json.loads((Path(__file__).with_name('videos.local.json')).read_text(encoding='utf-8'))
LIT, UNSCORED, ALL, GROUP, SHORT = _V['LIT'], _V['UNSCORED'], _V['ALL'], _V['GROUP'], _V['SHORT']

# COCO-WholeBody(133) → Halpe26 排列（analyze.py 用的索引）
WB2HALPE = {20: 17, 21: 20, 22: 18, 23: 21, 24: 19, 25: 22}


def meta(vid):
    return json.loads((DATA / vid / 'meta.json').read_text(encoding='utf-8'))


def source_path(vid):
    s = Path(meta(vid)['source'])
    return s if s.is_absolute() else ROOT / s


def wb_to_halpe(k133, s133):
    """(N,133,2),(N,133) → (N,26,2),(N,26)。頭頂點用鼻子往上外推（analyze 沒用到 17–19）。"""
    n = len(k133)
    K = np.zeros((n, 26, 2), np.float32)
    S = np.zeros((n, 26), np.float32)
    K[:, :17], S[:, :17] = k133[:, :17], s133[:, :17]
    for h, w in WB2HALPE.items():
        K[:, h], S[:, h] = k133[:, w], s133[:, w]
    K[:, 18] = (k133[:, 5] + k133[:, 6]) / 2
    S[:, 18] = np.minimum(s133[:, 5], s133[:, 6])
    K[:, 19] = (k133[:, 11] + k133[:, 12]) / 2
    S[:, 19] = np.minimum(s133[:, 11], s133[:, 12])
    K[:, 17] = k133[:, 0] + (k133[:, 0] - K[:, 18]) * 0.5
    S[:, 17] = S[:, 0]
    return K, S


def save_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=_np_default), encoding='utf-8')


def _np_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def heartbeat():
    (EXP / '_logs').mkdir(exist_ok=True)
    (EXP / '_logs' / 'heartbeat').touch()


# ───────────────────────── 評分 ─────────────────────────

def match_events(gt, pred, tol=4):
    """gt / pred：[(frame, panel)]。先只看時間做一對一配對（每個 gt 配最近、未用過、|Δ|≤tol 的預測，
    同板優先），回傳 (pairs[(gi, pi)], 未配到的 gt idx, 未配到的 pred idx)。"""
    gt = sorted(gt)
    pred = sorted(pred)
    used = set()
    pairs = []
    pf = np.array([p[0] for p in pred]) if pred else np.zeros(0)
    for gi, (gf, gk) in enumerate(gt):
        if len(pf) == 0:
            break
        lo, hi = np.searchsorted(pf, gf - tol), np.searchsorted(pf, gf + tol, side='right')
        cands = [pi for pi in range(lo, hi) if pi not in used]
        if not cands:
            continue
        cands.sort(key=lambda pi: (pred[pi][1] != gk, abs(pred[pi][0] - gf)))
        pi = cands[0]
        used.add(pi)
        pairs.append((gi, pi))
    mg = set(gi for gi, _ in pairs)
    return gt, pred, pairs, [i for i in range(len(gt)) if i not in mg], [i for i in range(len(pred)) if i not in used]


def score_events(gt, pred, fps, tol=4):
    """事件層級：正確＝時間配對且板位相同。"""
    gt, pred, pairs, _, _ = match_events(gt, pred, tol)
    ok = [(gi, pi) for gi, pi in pairs if gt[gi][1] == pred[pi][1]]
    tp = len(ok)
    prec = tp / len(pred) if pred else 0.0
    rec = tp / len(gt) if gt else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec > 0 else 0.0
    d = np.array([pred[pi][0] - gt[gi][0] for gi, pi in ok], float)
    return dict(f1=round(f1, 4), precision=round(prec, 4), recall=round(rec, 4),
                timing_mae_ms=round(float(np.abs(d).mean() * 1000 / fps), 1) if len(d) else float('nan'),
                timing_bias_ms=round(float(np.median(d) * 1000 / fps), 1) if len(d) else float('nan'),
                panel_acc=round(tp / len(pairs), 4) if pairs else 0.0,
                n_gt=len(gt), n_pred=len(pred), n_tp=tp, fps=round(float(fps), 2))


def pool(cells):
    """各影片結果合併（以事件數加總，不是平均）。"""
    tp = sum(c['n_tp'] for c in cells)
    ng = sum(c['n_gt'] for c in cells)
    npd = sum(c['n_pred'] for c in cells)
    prec = tp / npd if npd else 0.0
    rec = tp / ng if ng else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec > 0 else 0.0
    w = np.array([c['n_tp'] for c in cells], float)
    mae = np.array([c['timing_mae_ms'] for c in cells], float)
    bias = np.array([c['timing_bias_ms'] for c in cells], float)
    pa_n = sum(c['n_tp'] / c['panel_acc'] if c['panel_acc'] > 0 else 0 for c in cells)
    ok = np.isfinite(mae) & (w > 0)
    return dict(f1=round(f1, 4), precision=round(prec, 4), recall=round(rec, 4),
                timing_mae_ms=round(float((mae[ok] * w[ok]).sum() / w[ok].sum()), 1) if ok.any() else float('nan'),
                timing_bias_ms=round(float((bias[ok] * w[ok]).sum() / w[ok].sum()), 1) if ok.any() else float('nan'),
                panel_acc=round(tp / pa_n, 4) if pa_n else 0.0, n_gt=ng, n_pred=npd, n_tp=tp)
