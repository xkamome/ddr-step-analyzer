"""端到端（留一支法）比模型單獨偵測低（0.52 vs 0.59）——找出模型的踩點在合併進 analyze.py 時去哪了。
每個模型事件分類：加入（from_model 步）／被骨架同板步擋掉／被丟掉（腳離板 >1 格或算不出腳位置）。
並離線比較合併策略：現行（骨架＋模型補）、只用模型事件、模型事件＋只保留和模型一致的骨架步。

用法：experiments/py.sh experiments/diag_merge.py   （先跑 eval_nolight.py B 產生留一支法模型）
"""
from __future__ import annotations

import json
import subprocess
import sys

from common import GROUP, LIT, RESULTS, ROOT, load_json, pool, save_json, score_events
from eval_contact import SYM2IDX, tol_frames
from eval_nolight import MODELS, pool_timing, timing_cell
from learn_contact import gt_events
from test_integration import PROD_PY, prep, user_calib

RUNNER = r'''
import sys, json
sys.path.insert(0, sys.argv[1])
sys.stdout.reconfigure(errors='replace')
import ddrpose.contact as C
_orig, _det = C.load_model, C.detect_presses
C.load_model = lambda path=None: _orig(sys.argv[4])
def det(*a, **k):
    ev, P = _det(*a, **k)
    json.dump([[int(f), p] for f, p in ev], open(sys.argv[5], 'w'))
    return ev, P
C.detect_presses = det
from ddrpose.analyze import analyze
r = analyze(sys.argv[2], json.loads(sys.argv[3]))
print(json.dumps({'n_steps': len(r['steps']), 'model_only': r['calibration'].get('model_only_steps')}))
'''
PK = 'LDUR'


def main():
    sys.stdout.reconfigure(errors='replace')
    out, agg = {}, {}
    for v in LIT:
        g = GROUP[v]
        d = prep(v, 'nolight_loo')
        evf = d / 'model_events.json'
        p = subprocess.run([str(PROD_PY), '-X', 'utf8', '-c', RUNNER, str(ROOT), str(d),
                            json.dumps(user_calib(v, lights=False)), str(MODELS / f'contact_{g}.npz'), str(evf)],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
        if p.returncode != 0:
            print(v, 'FAIL', p.stderr[-800:]); continue
        a = json.loads((d / 'analysis.json').read_text(encoding='utf-8'))
        fps, gt, tol = a['fps'], gt_events(v), tol_frames(a['fps'])
        ev = [(f, PK.index(k)) for f, k in json.loads(evf.read_text())]
        steps = [(int(s['frame']), SYM2IDX.get(s.get('panel')), bool(s.get('from_model'))) for s in a['steps']]
        model_frames = {(f, k) for f, k, m in steps if m}
        skel = [(f, k) for f, k, m in steps if not m and k is not None]
        fate = {'added': 0, 'skipped_by_skeleton': 0, 'dropped': 0}
        for f, k in ev:
            if (f, k) in model_frames:
                fate['added'] += 1
            elif any(sk == k and abs(sf - f) <= 6 for sf, sk in skel):
                fate['skipped_by_skeleton'] += 1
            else:
                fate['dropped'] += 1
        cur = [(f, k) for f, k, m in steps if k is not None]
        agree = [(f, k) for f, k in skel if any(k == mk and abs(f - mf) <= 6 for mf, mk in ev)]
        pol = {'current': cur, 'model_events_only': ev,
               'model_plus_agreeing_skeleton': sorted(agree + [e for e in ev if not any(
                   e[1] == k and abs(e[0] - f) <= 6 for f, k in agree)])}
        r = dict(n_model_events=len(ev), fate=fate)
        for name, pe in pol.items():
            c = score_events(gt, pe, fps, tol)
            r[name] = dict(f1=c['f1'], precision=c['precision'], recall=c['recall'])
            agg.setdefault(name, ([], []))
            agg[name][0].append(c); agg[name][1].append(timing_cell(gt, pe, fps))
        out[v] = r
        print(v, r, flush=True)
    out['pooled'] = {k: dict(**{m: pool(c)[m] for m in ('f1', 'precision', 'recall')}, timing=pool_timing(t))
                     for k, (c, t) in agg.items()}
    print('pooled', out['pooled'])
    res = load_json(RESULTS / 'nolight.json', {}) or {}
    res['merge_diag'] = out
    save_json(res, RESULTS / 'nolight.json')


if __name__ == '__main__':
    main()
