"""LightGBM 模型 → numpy 陣列（ddrpose/models/*.npz），正式版不用裝 lightgbm。
並檢查 numpy 推論與 lightgbm 結果一致。

用法：experiments/py.sh experiments/export_gbdt.py <model.txt> <out.npz> [names.json]
"""
from __future__ import annotations

import json
import sys

import lightgbm as lgb
import numpy as np

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from ddrpose.contact import gbdt_predict  # noqa: E402


def flatten(model):
    d = model.dump_model()
    feats, thrs, lefts, rights, dleft, mtype, leafv, roots = [], [], [], [], [], [], [], []
    MT = {'None': 0, 'Zero': 1, 'NaN': 2}

    def walk(node):
        idx = len(feats)
        feats.append(-1); thrs.append(0.0); lefts.append(-1); rights.append(-1)
        dleft.append(False); mtype.append(0); leafv.append(0.0)
        if 'leaf_value' in node:
            leafv[idx] = node['leaf_value']
            return idx
        assert node['decision_type'] == '<='
        feats[idx] = node['split_feature']; thrs[idx] = node['threshold']
        dleft[idx] = node['default_left']; mtype[idx] = MT[node['missing_type']]
        lefts[idx] = walk(node['left_child']); rights[idx] = walk(node['right_child'])
        return idx

    for t in d['tree_info']:
        roots.append(walk(t['tree_structure']))
    return dict(feat=np.array(feats, np.int32), thr=np.array(thrs, np.float64), left=np.array(lefts, np.int32),
                right=np.array(rights, np.int32), dleft=np.array(dleft, bool), mtype=np.array(mtype, np.int8),
                leaf=np.array(leafv, np.float64), roots=np.array(roots, np.int32), n_feat=np.int32(model.num_feature()))


if __name__ == '__main__':
    m = lgb.Booster(model_file=sys.argv[1])
    arr = flatten(m)
    extra = {}
    if len(sys.argv) > 3:
        extra = json.loads(open(sys.argv[3], encoding='utf-8').read())
    np.savez_compressed(sys.argv[2], **arr, meta=json.dumps(extra, ensure_ascii=False))
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, m.num_feature())) * 2
    X[rng.random(X.shape) < 0.05] = np.nan
    a = m.predict(X)
    b = gbdt_predict(dict(np.load(sys.argv[2])), X)
    print('max abs diff', float(np.abs(a - b).max()), 'trees', len(arr['roots']), 'nodes', len(arr['feat']))
