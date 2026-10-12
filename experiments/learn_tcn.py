"""落地判定：時間卷積網路（TCN）。每塊板一條序列（權重共用），輸出「這一幀是踩下去的瞬間」機率。
所有影片先重取樣到 30 fps；留一支法（依遊玩分組）；門檻用其他組選。

用法：experiments/py.sh experiments/learn_tcn.py halpe|rtmw [--final]
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import torch
import torch.nn as nn

from common import CACHE, DATA, GROUP, LIT, RESULTS, UNSCORED, heartbeat, load_json, pool, save_json, score_events
from contact_features import load_video_features
from eval_contact import prepare_run, tol_frames
from learn_contact import gt_events, onset_target, peaks_from_prob

torch.set_num_threads(4)
FPS0 = 30.0


def load(vid, pose):
    d = prepare_run(vid, pose)
    a = json.loads((DATA / vid / 'analysis.json').read_text(encoding='utf-8'))
    pts = {k: v for k, v in a['calibration']['points'].items() if k in 'LDUR'}
    F, names, play, fps = load_video_features(d, d / 'pose.npz', pts, smooth=False, offs_s=(0.0,))
    n = len(F)
    T = n / fps
    idx = np.clip(np.round(np.arange(0, T, 1 / FPS0) * fps).astype(int), 0, n - 1)   # 30 fps 網格 → 原始幀
    D = dict(F=F, names=names, play=play, fps=fps, idx=idx, y=None)
    if vid in LIT:
        g = np.load(CACHE / vid / 'gt.npz')
        D['y'] = onset_target(g['onsets'], len(g['pressed']), fps, win_s=0.05)[:n]
    return D


class TCN(nn.Module):
    def __init__(self, c_in, h=64, dil=(1, 2, 4, 8)):
        super().__init__()
        self.inp = nn.Conv1d(c_in, h, 5, padding=2)
        self.blocks = nn.ModuleList([nn.Sequential(nn.ReLU(), nn.Conv1d(h, h, 3, padding=d, dilation=d),
                                                   nn.ReLU(), nn.Conv1d(h, h, 1)) for d in dil])
        self.drop = nn.Dropout(0.2)
        self.out = nn.Conv1d(h, 1, 1)

    def forward(self, x):                  # x: (B, C, T)
        z = self.inp(x)
        for b in self.blocks:
            z = z + b(self.drop(z))
        return self.out(torch.relu(z)).squeeze(1)


def seqs(D, mu=None, sd=None):
    """→ X (4, C, T30)、Y (4, T30)、M (4, T30)（遊玩幀）。"""
    F = D['F'][D['idx']]                                   # (T30, 4, C)
    X = np.transpose(F, (1, 2, 0)).astype(np.float32)      # (4, C, T30)
    if mu is not None:
        X = (X - mu[None, :, None]) / sd[None, :, None]
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    X = np.clip(X, -8, 8)
    M = np.repeat(D['play'][D['idx']][None], 4, 0)
    Y = None if D['y'] is None else np.transpose(D['y'][D['idx']], (1, 0)).astype(np.float32)
    return X, Y, M


def norm_stats(Ds):
    A = np.concatenate([D['F'][D['play']].reshape(-1, D['F'].shape[2]) for D in Ds])
    mu = np.nanmean(A, 0)
    sd = np.nanstd(A, 0) + 1e-6
    return np.nan_to_num(mu), np.nan_to_num(sd, nan=1.0)


def train(Ds, steps=1000, win=256, bs=32, seed=0):
    torch.manual_seed(seed); rng = np.random.default_rng(seed)
    mu, sd = norm_stats(Ds)
    data = [seqs(D, mu, sd) for D in Ds]
    C = data[0][0].shape[1]
    net = TCN(C)
    opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
    lens = np.array([X.shape[2] * 4 for X, _, _ in data], float)
    pw = torch.tensor(8.0)
    for it in range(steps):
        xb, yb, mb = [], [], []
        for _ in range(bs):
            k = rng.choice(len(data), p=lens / lens.sum())
            X, Y, M = data[k]
            p = rng.integers(4)
            s = rng.integers(0, max(1, X.shape[2] - win))
            xb.append(X[p, :, s:s + win]); yb.append(Y[p, s:s + win]); mb.append(M[p, s:s + win])
        L = min(x.shape[1] for x in xb)
        xb = torch.tensor(np.stack([x[:, :L] for x in xb])); yb = torch.tensor(np.stack([y[:L] for y in yb]))
        mb = torch.tensor(np.stack([m[:L] for m in mb]).astype(np.float32))
        logit = net(xb)
        loss = (nn.functional.binary_cross_entropy_with_logits(logit, yb, pos_weight=pw, reduction='none') * mb).sum() / mb.sum().clamp(min=1)
        opt.zero_grad(); loss.backward(); opt.step(); sched.step()
    net.eval()
    return net, mu, sd


def predict(model, D):
    net, mu, sd = model
    X, _, M = seqs(D, mu, sd)
    with torch.no_grad():
        P30 = torch.sigmoid(net(torch.tensor(X))).numpy().T     # (T30, 4)
    P30[~M.T] = 0
    # 回到原始幀：每個原始幀取最近的 30 fps 格
    n = len(D['F'])
    j = np.clip(np.round(np.arange(n) / D['fps'] * FPS0).astype(int), 0, len(P30) - 1)
    P = P30[j]
    P[~D['play']] = 0
    return P


def to_ev(P, th, fps):
    return peaks_from_prob(P, th[0], th[1], fps)


def run(pose, final=False, seeds=(0,)):
    t0 = time.time()
    data = {v: load(v, pose) for v in LIT + UNSCORED}
    print(f'load {time.time() - t0:.0f}s, C={data[LIT[0]]["F"].shape[2]}', flush=True)
    oof = {}
    for g in sorted(set(GROUP[v] for v in LIT)):
        tr = [data[v] for v in LIT if GROUP[v] != g]
        models = [train(tr, seed=s) for s in seeds]
        for v in [v for v in LIT if GROUP[v] == g]:
            oof[v] = np.mean([predict(m, data[v]) for m in models], 0)
        heartbeat()
        print(f'fold {g} {time.time() - t0:.0f}s', flush=True)
    grid = [(thr, sep) for thr in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8) for sep in (0.07, 0.1)]
    cells, chosen = {}, {}
    for v in LIT:
        others = [u for u in LIT if GROUP[u] != GROUP[v]]
        best = max(grid, key=lambda th: pool([score_events(gt_events(u), to_ev(oof[u], th, data[u]['fps']),
                                                           data[u]['fps'], tol_frames(data[u]['fps'])) for u in others])['f1'])
        chosen[v] = best
        cells[v] = score_events(gt_events(v), to_ev(oof[v], best, data[v]['fps']), data[v]['fps'], tol_frames(data[v]['fps']))
        np.save(CACHE / v / f'oof_{pose}_tcn.npy', oof[v].astype(np.float32))
        print(v, cells[v], best, flush=True)
    cells['pooled'] = pool([cells[v] for v in LIT])
    cells['_thresholds'] = {v: list(c) for v, c in chosen.items()}
    allbest = max(grid, key=lambda th: pool([score_events(gt_events(u), to_ev(oof[u], th, data[u]['fps']),
                                                          data[u]['fps'], tol_frames(data[u]['fps'])) for u in LIT])['f1'])
    cells['_final_threshold'] = list(allbest)
    cells['_variant'] = dict(model='tcn', smooth=False, target='onset', seeds=len(seeds))
    print('pooled', cells['pooled'], f'{time.time() - t0:.0f}s', flush=True)
    if final:
        models = [train([data[v] for v in LIT], seed=s) for s in seeds]
        (CACHE / 'models').mkdir(parents=True, exist_ok=True)
        for i, (net, mu, sd) in enumerate(models):
            torch.save(dict(state=net.state_dict(), mu=mu, sd=sd, names=data[LIT[0]]['names'], thr=list(allbest)),
                       CACHE / 'models' / f'tcn_{pose}_{i}.pt')
        det = {}
        for v in UNSCORED:
            P = np.mean([predict(m, data[v]) for m in models], 0)
            ev = to_ev(P, allbest, data[v]['fps'])
            play_s = data[v]['play'].sum() / data[v]['fps']
            det[v] = dict(n_events=len(ev), per_min=round(len(ev) / max(play_s, 1) * 60, 1))
            np.save(CACHE / v / f'pred_tcn_{pose}.npy', np.array(ev, int).reshape(-1, 2))
        cells['_unscored'] = det
    var = load_json(RESULTS / 'contact_variants.json', {}) or {}
    var[f'{pose}_tcn'] = {k: cells[k] for k in cells}
    save_json(var, RESULTS / 'contact_variants.json')
    return cells


if __name__ == '__main__':
    sys.stdout.reconfigure(errors='replace')
    run(sys.argv[1], final='--final' in sys.argv)
