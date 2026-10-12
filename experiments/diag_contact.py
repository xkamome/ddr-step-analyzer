"""診斷：同一支影片內（前半訓練、後半測試）vs 跨影片，看瓶頸在特徵還是泛化。"""
import sys, numpy as np, lightgbm as lgb
from common import LIT, pool, score_events, load_json, RESULTS
from learn_contact import load, PARAMS, ROUNDS, events_from_prob, gt_events
from eval_contact import tol_frames
pose = sys.argv[1]
res = {}
for v in LIT:
    D = load(v, pose)
    n = len(D['F']); h = n // 2
    m = D['play'].copy(); m[h:] = False
    X = D['F'][m].reshape(-1, D['F'].shape[2]); y = D['y'][m].reshape(-1)
    mdl = lgb.train(PARAMS, lgb.Dataset(X, y.astype(int)), ROUNDS)
    P = mdl.predict(D['F'].reshape(-1, D['F'].shape[2])).reshape(n, 4); P[~D['play']] = 0
    gt = [e for e in gt_events(v) if e[0] >= h]
    best = max(((score_events(gt, [e for e in events_from_prob(P, on, off) if e[0] >= h], D['fps'], tol_frames(D['fps'])), on, off)
                for on in (0.2, 0.3, 0.4, 0.5) for off in (0.1, 0.15, 0.25) if off < on), key=lambda r: r[0]['f1'])
    res[v] = best[0]; print(v, 'within-video 2nd half', best[0]['f1'], best[0]['precision'], best[0]['recall'], best[1:], flush=True)
print('pooled within', pool(list(res.values())))
