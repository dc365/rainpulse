#!/usr/bin/env python3
"""Inspect source ray/gate. Composite/web-map pixels are NOT accepted."""
from pathlib import Path
import argparse, gzip, json, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'algorithms'))
import numpy as np
from rainpulse_algo.radar.qc_engine.object_consensus.io import read_case, clean, sha_file
from rainpulse_algo.radar.qc_engine.object_consensus.engine import Reason
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--input',required=True,type=Path);p.add_argument('--results',required=True,type=Path)
p.add_argument('--case',required=True);p.add_argument('--ray',required=True,type=int);p.add_argument('--gate',required=True,type=int)
a=p.parse_args()
items=json.loads((a.input/'manifest.json').read_text())
item=next((x for x in items if x['case']==a.case),None)
if item is None:raise ValueError('unknown case')
d=read_case(a.input,item)
if not 0<=a.ray<item['shape'][0] or not 0<=a.gate<item['shape'][1]:raise ValueError('out of source coordinates')
root=a.results/a.case
inventory=json.loads((a.results/'files.sha256.json').read_text())
for name in ('evidence.npz','outcome.npz','folds.json.gz','summary.json'):
 if sha_file(root/name)!=inventory[f'{a.case}/{name}']:raise ValueError('result checksum mismatch')
s=json.loads((root/'summary.json').read_text())
if s['input_npz_sha256']!=item['sha256']:raise ValueError('results from different scan')
with np.load(root/'evidence.npz',allow_pickle=False) as n:
 e={k:clean(n[k][a.ray,a.gate]) for k in n.files}
with np.load(root/'outcome.npz',allow_pickle=False) as n:
 out={k:clean(n[k][a.ray,a.gate]) for k in n.files}
with gzip.open(root/'folds.json.gz','rt',encoding='utf-8') as f:folds=json.load(f)
fid=e['fold_id'];fold=next((x for x in folds if x['fold_id']==fid),None)
raw={k:clean(v[a.ray,a.gate]) for k,v in d.items() if v.ndim==2}
print(json.dumps({'ray':a.ray,'gate':a.gate,'range_m':float(d['range_m'][a.gate]),
 'raw_and_baseline':raw,'evidence':e,'outcome':out,'reference_fold':fold,
 'reason_names':[r.name for r in Reason if e['reason']&int(r)],'truth_label':None},ensure_ascii=False,indent=2,allow_nan=False))
