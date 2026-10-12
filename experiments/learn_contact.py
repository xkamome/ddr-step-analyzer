"""方法 learned_<pose>：用亮燈當標籤訓練「這一幀這塊板有沒有被踩」（LightGBM），
留一支法（依遊玩分組）評分＝模擬沒有燈的機台。

用法：experiments/py.sh experiments/learn_contact.py halpe|rtmw
輸出：results/contact.json 的 learned_<pose>；cache/models/contact_<pose>.txt（全部資料訓練，給整合用）
"""
from __future__ import annotations

import json
import sys
import time

import lightgbm as lgb
import numpy as np

from common import CACHE, DATA, GROUP, LIT, RESULTS, UNSCORED, heartbeat, load_json, pool, save_json, score_events
from contact_features import load_video_features
from eval_contact import prepare_run, tol_frames

PARAMS = dict(objective='binary', learning_rate=0.05, num_leaves=31, min_data_in_leaf=50,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1,
              num_threads=4)
ROUNDS = 400


def events_from_prob(P, on=0.5, off=0.3, smooth=3):
    """P (N,4) → [(frame, panel)]：每塊板機率由低於 off 升到超過 on 的那一幀。"""
    from scipy.ndimage import uniform_filter1d
    if smooth > 1:
        P = uniform_filter1d(P, smooth, axis=0)
    ev = []
    for p in range(P.shape[1]):
        state = False
        for i, v in enumerate(P[:, p]):
            if not state and v > on:
                ev.append((i, p)); state = True
            elif state and v < off:
                state = False
    return sorted(ev)


def peaks_from_prob(P, thr=0.5, sep_s=0.1, fps=30.0, smooth=1, valley=None):
    """onset 目標：每塊板機率的局部最大值（> thr，間隔 ≥ sep_s）→ [(frame, panel)]。
    valley：同一塊板兩個峰之間的最低點要 < valley × 較低的峰，才算兩次踩（否則只留較高的峰）。"""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    if smooth > 1:
        P = uniform_filter1d(P, smooth, axis=0)
    w = max(3, int(round(sep_s * fps)) * 2 + 1)
    ev = []
    for p in range(P.shape[1]):
        x = P[:, p]
        mx = maximum_filter1d(x, w)
        idx = np.where((x >= mx) & (x > thr))[0]
        last = -10 ** 9
        pk = []
        for i in idx:
            if i - last >= w // 2:
                pk.append(int(i)); last = i
        if valley is not None and pk:
            merged = [pk[0]]
            for i in pk[1:]:
                j = merged[-1]
                if x[j:i + 1].min() >= valley * min(x[i], x[j]):
                    if x[i] > x[j]:
                        merged[-1] = i
                else:
                    merged.append(i)
            pk = merged
        ev += [(i, p) for i in pk]
    return sorted(ev)


def onset_target(onsets, n, fps, win_s=0.05):
    y = np.zeros((n, 4), bool)
    w = max(1, int(round(win_s * fps)))
    for f, p in onsets:
        y[max(0, f - w):f + w + 1, p] = True
    return y


VARIANT = dict(smooth=True, target='pressed', offs='narrow')
OFFS_WIDE = tuple(np.round(np.arange(-0.3, 0.301, 1 / 30), 3))


def load(vid, pose):
    d = prepare_run(vid, pose)
    a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
    pts = {k: v for k, v in a['calibration']['points'].items() if k in 'LDUR'}
    kw = dict(smooth=VARIANT['smooth'])
    if VARIANT['offs'] == 'wide':
        kw['offs_s'] = OFFS_WIDE
    F, names, play, fps = load_video_features(d, d / 'pose.npz', pts, **kw)
    y = None
    if vid in LIT:
        g = np.load(CACHE / vid / 'gt.npz')
        y = g['pressed'] if VARIANT['target'] == 'pressed' else onset_target(g['onsets'], len(g['pressed']), fps)
        if len(y) != len(F):
            m = min(len(y), len(F))
            print(f'  {vid}: frame count mismatch pose={len(F)} gt={len(y)} → trim to {m}')
            F, play, y = F[:m], play[:m], y[:m]
    return dict(F=F, names=names, play=play, fps=fps, y=y)


def rows(D):
    m = D['play']
    X = D['F'][m].reshape(-1, D['F'].shape[2])
    y = D['y'][m].reshape(-1).astype(int) if D['y'] is not None else None
    return X, y


def train(Ds):
    Xs, ys = zip(*[rows(D) for D in Ds])
    X, y = np.concatenate(Xs), np.concatenate(ys)
    return lgb.train(PARAMS, lgb.Dataset(X, y, free_raw_data=True), ROUNDS)


def predict(model, D):
    N, P, Dm = D['F'].shape
    prob = model.predict(D['F'].reshape(-1, Dm)).reshape(N, P)
    prob[~D['play']] = 0.0
    return prob


def gt_events(vid):
    z = np.load(CACHE / vid / 'gt.npz')
    return [(int(f), int(p)) for f, p in z['onsets']]


def run(pose):
    t0 = time.time()
    data = {v: load(v, pose) for v in LIT + UNSCORED}
    heartbeat()
    names = data[LIT[0]]['names']
    print(f'features {len(names)}, load {time.time() - t0:.0f}s', flush=True)
    groups = sorted(set(GROUP[v] for v in LIT))
    oof = {}
    for g in groups:
        test = [v for v in LIT if GROUP[v] == g]
        model = train([data[v] for v in LIT if GROUP[v] != g])
        for v in test:
            oof[v] = predict(model, data[v])
        heartbeat()
        print(f'fold {g} done {time.time() - t0:.0f}s', flush=True)
    # 門檻：每支影片用「其他組」的 OOF 選 (on, off)，不偷看自己
    if VARIANT['target'] == 'pressed':
        grid = [(on, off) for on in (0.2, 0.3, 0.4, 0.5) for off in (0.1, 0.15, 0.25) if off < on]
        to_ev = lambda P, th, fps: events_from_prob(P, *th)
    else:
        grid = [(thr, sep, val) for thr in (0.15, 0.2, 0.25, 0.3, 0.4) for sep in (0.07, 0.1)
                for val in (None, 0.4, 0.6, 0.8)]
        to_ev = lambda P, th, fps: peaks_from_prob(P, th[0], th[1], fps, valley=th[2])
    cells, chosen = {}, {}
    for v in LIT:
        others = [u for u in LIT if GROUP[u] != GROUP[v]]
        best = None
        for th in grid:
            sc = pool([score_events(gt_events(u), to_ev(oof[u], th, data[u]['fps']), data[u]['fps'],
                                    tol_frames(data[u]['fps'])) for u in others])
            if best is None or sc['f1'] > best[0]:
                best = (sc['f1'], *th)
        chosen[v] = best[1:]
        ev = to_ev(oof[v], best[1:], data[v]['fps'])
        cells[v] = score_events(gt_events(v), ev, data[v]['fps'], tol_frames(data[v]['fps']))
        # 逐幀：被踩狀態的平衡正確率
        m = data[v]['play']
        yt, yp = data[v]['y'][m].reshape(-1), (oof[v][m] > best[1]).reshape(-1)
        cells[v]['frame_bal_acc'] = round(float((yp[yt].mean() + (~yp[~yt]).mean()) / 2), 4)
        np.save(CACHE / v / f'oof_{pose}_{VARIANT["target"]}.npy', oof[v].astype(np.float32)); np.save(CACHE / v / f'oof_{pose}_{VARIANT["target"]}_{"raw" if not VARIANT["smooth"] else "s"}_{VARIANT["offs"]}{"_flow" if VARIANT.get("flow") else ""}.npy', oof[v].astype(np.float32))
        print(v, cells[v], 'thr', chosen[v], flush=True)
    cells['pooled'] = pool([cells[v] for v in LIT])
    cells['_thresholds'] = {v: list(c) for v, c in chosen.items()}
    # 全部資料訓練的最終模型（整合用）；門檻取所有影片最常選的
    final = train([data[v] for v in LIT])
    (CACHE / 'models').mkdir(parents=True, exist_ok=True)
    final.save_model(str(CACHE / 'models' / f'contact_{pose}.txt'))
    imp = sorted(zip(final.feature_importance('gain'), names), reverse=True)[:25]
    cells['_top_features'] = [[n, round(float(g), 1)] for g, n in imp]
    vals = [tuple(c) for c in chosen.values()]
    thr = max(set(vals), key=vals.count)
    cells['_final_threshold'] = list(thr)
    det = {}
    for v in UNSCORED:
        ev = to_ev(predict(final, data[v]), thr, data[v]['fps'])
        play_s = data[v]['play'].sum() / data[v]['fps']
        det[v] = dict(n_events=len(ev), per_min=round(len(ev) / max(play_s, 1) * 60, 1))
        np.save(CACHE / v / f'pred_learned_{pose}.npy', np.array(ev, int).reshape(-1, 2))
    cells['_unscored'] = det
    cells['_variant'] = dict(VARIANT)
    import contact_features
    tag = f"{pose}_{'s' if VARIANT['smooth'] else 'raw'}_{VARIANT['target']}_{VARIANT['offs']}" + ('_x' if contact_features.EXTRA else '') + ('_flow' if contact_features.FLOW else '')
    VARIANT['extra'] = contact_features.EXTRA
    VARIANT['flow'] = contact_features.FLOW
    final.save_model(str(CACHE / 'models' / f'contact_{tag}.txt'))
    var = load_json(RESULTS / 'contact_variants.json', {}) or {}
    var[tag] = {k: cells[k] for k in ['pooled', '_thresholds', '_final_threshold', '_unscored', '_variant'] + LIT}
    save_json(var, RESULTS / 'contact_variants.json')
    if FINAL:
        out = load_json(RESULTS / 'contact.json', {}) or {}
        out[f'learned_{pose}'] = cells
        save_json(out, RESULTS / 'contact.json')
    print('pooled', cells['pooled'], 'unscored', det, f'{time.time() - t0:.0f}s')


FINAL = False

if __name__ == '__main__':
    import argparse
    sys.stdout.reconfigure(errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('pose')
    ap.add_argument('--raw', action='store_true')
    ap.add_argument('--target', default='pressed')
    ap.add_argument('--offs', default='narrow')
    ap.add_argument('--final', action='store_true')
    ap.add_argument('--extra', action='store_true')
    ap.add_argument('--flow', action='store_true')
    a = ap.parse_args()
    import contact_features
    contact_features.EXTRA = a.extra
    contact_features.FLOW = a.flow
    VARIANT.update(smooth=not a.raw, target=a.target, offs=a.offs)
    FINAL = a.final
    run(a.pose)
