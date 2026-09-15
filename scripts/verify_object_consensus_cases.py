#!/usr/bin/env python3
"""Re-run real cases and check non-leakage/reproducibility; not a skill test."""
from pathlib import Path
import argparse, json, sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms'))
import numpy as np
from rainpulse_algo.radar.qc_engine.object_consensus import (Config, Policy, RawScan, infer, baseline_from_npz, apply_policy)
from rainpulse_algo.radar.qc_engine.object_consensus.io import read_case, sha_file, write_json

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--input',required=True,type=Path)
p.add_argument('--output',required=True,type=Path)
a=p.parse_args()
if a.output.exists():raise ValueError('refuse overwrite')
rows=[]
for item in json.loads((a.input/'manifest.json').read_text()):
 d=read_case(a.input,item)
 raw=RawScan.from_arrays(d,full_ppi=item['full_ppi'],phase_period=item['phase_period_deg'])
 e=infer(raw);base=baseline_from_npz(d)
 audit=apply_policy(raw,e,base)
 for k in base:assert base[k].tobytes()==audit.arrays[k].tobytes(),k
 changed=dict(d)
 rng=np.random.default_rng(92)
 for k in list(changed):
  if k.startswith('baseline_') or k=='review_roi':
   changed[k]=rng.integers(0,2,size=changed[k].shape).astype(changed[k].dtype)
 other=RawScan.from_arrays(changed,full_ppi=item['full_ppi'],phase_period=item['phase_period_deg'])
 e2=infer(other)
 assert e.identity==e2.identity
 for k in e.arrays:assert np.array_equal(e.arrays[k],e2.arrays[k],equal_nan=True),k
 for fold in e.folds:
  low,high=fold['target_bounds_m'];margin=Config().guard_blocks*Config().block_m
  for lo,hi in fold['reference_intervals']:
   rr=raw.ranges[lo:hi]
   assert not np.any((rr>=low-margin)&(rr<high+margin))
   assert np.all(e.arrays['domain_id'][fold['ray'],lo:hi]==fold['domain_id'])
 exp=apply_policy(raw,e,base,Policy(mode='experiment_quarantine',allow_coherent_quarantine=True,acknowledge_uncalibrated_model=True))
 assert not np.any(exp.added_quarantine & ~raw.observed)
 assert not np.any(exp.added_quarantine & (e.arrays['state']!=5))
 assert not np.any(exp.added_quarantine & (e.arrays['bracketed_reference_mask']!=1))
 assert exp.summary['confirmed_additions']==0
 assert sha_file(a.input/(item['case']+'.npz'))==item['sha256']
 rows.append({'case':item['case'],'input_unchanged':True,'roi_baseline_anchor_independence':True,
              'audit_byte_identical':True,'repeated_evidence_identical':True,'no_missing_action':True,
              'references_in_same_raw_domain_and_outside_target_guard':True,
              'model_evidence_sha256':e.identity['evidence_sha256']})
 print(item['case'],'all real-array invariants passed',flush=True)
a.output.parent.mkdir(parents=True,exist_ok=True)
write_json(a.output,{'status':'passed','truth_skill_not_tested':True,'cases':rows})
