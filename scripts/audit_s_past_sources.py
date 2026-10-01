#!/usr/bin/env python3
"""Read-only independent past-scan evidence audit; never changes QC products."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

import numpy as np

WORKER = r'''
import hashlib,json,os
from pathlib import Path
from datetime import datetime
import numpy as np,yaml
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment,artifact_sha256
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.profile import load_open_source_profile
from rainpulse_algo.radar.qc_engine.standalone_evidence import stage_a
from rainpulse_algo.radar.qc_engine.decision import Action,Reason
from rainpulse_algo.radar.qc_geometry import nearest_azimuth_matches
reader=ArtifactObjectReader(minio_client_from_environment(),max_workers=2)
text=Path(os.environ['RAINPULSE_RADAR_QC_CONFIG']).read_text()
profile=load_open_source_profile(os.environ['RAINPULSE_RADAR_QC_CONFIG'],os.environ['RAINPULSE_QC_FLAG_DEFINITIONS'])
stamp=lambda x:datetime.fromisoformat(str(x).replace('Z','+00:00'))
rows=json.loads(PAYLOAD);out=[]
for row in rows:
 current_raw=reader.open(row['normalized_uri']).load()
 if artifact_sha256(current_raw)!=row['normalized_sha256']:raise ValueError('current input SHA differs')
 root=open_qc_input(current_raw).root
 if str(root.attrs['scan_id'])!=row['scan_id']:raise ValueError('current scan differs')
 current=adapt_sweep(root,row['sweep'],profile)
 if current.shape!=tuple(row['shape']) or not np.array_equal(current.azimuth,row['azimuth']) or not np.array_equal(current.ranges,row['ranges']):raise ValueError('snapshot native geometry differs')
 scope=np.zeros(current.shape,bool);scope.flat[row['remaining_indices']]=True
 current_evidence=stage_a(current,profile)
 current_reason=current_evidence.decision.arrays['QC_DECISION_REASON']
 current_strong=(current_evidence.decision.arrays['QC_ACTION']==Action.REJECT)&((current_reason&int(Reason.RADIAL_AND_POLARIMETRIC))!=0)
 current_degraded='graph_degradation' in current_evidence.summary
 if current_degraded:current_strong[:]=False
 votes=np.zeros(current.shape,'uint8');samples=votes.copy();references=[];seen=set()
 for entry in row['context']['artifacts']:
  if entry.get('role')!='temporal' or entry.get('status')!='available':continue
  if entry['sha256'] in seen:continue
  seen.add(entry['sha256'])
  raw=reader.open(entry['uri']).load()
  if artifact_sha256(raw)!=entry['sha256']:raise ValueError('reference SHA differs')
  health=json.loads(raw.get('health/summary.json',b'{}'))
  if health.get('health') not in {'HEALTHY','DEGRADED'}:raise ValueError('reference health unavailable')
  past_root=open_qc_input(raw).root
  if str(past_root.attrs['scan_id'])!=entry['scan_id'] or str(past_root.attrs['radar_id']).lower()!=row['radar_id']:raise ValueError('reference identity differs')
  end=stamp(past_root.attrs['volume_end_time_utc']);now=stamp(root.attrs['volume_end_time_utc'])
  if not end<now or end>stamp(row['context']['decision_cutoff_utc']):raise ValueError('reference is not causal')
  if past_root.attrs.get('ingest_available_at_utc') and stamp(past_root.attrs['ingest_available_at_utc'])>stamp(row['context']['decision_cutoff_utc']):raise ValueError('reference ingest after cutoff')
  if (now-end).total_seconds()>profile.context.max_age_seconds:raise ValueError('reference too old')
  past=adapt_sweep(past_root,row['sweep'],profile)
  record={'scan_id':entry['scan_id'],'sha256':entry['sha256'],'strictly_past':True}
  if not np.array_equal(past.ranges,current.ranges) or past.audit.get('cut_metadata')!=current.audit.get('cut_metadata'):
   record['status']='incompatible_cut';references.append(record);continue
  evidence=stage_a(past,profile)
  degraded='graph_degradation' in evidence.summary
  reason=evidence.decision.arrays['QC_DECISION_REASON']
  # Restrict evidence to actual local radial+polarimetric rejection. Recurrence,
  # generic clutter rejection and missing capability are not confirmation.
  strong=(evidence.decision.arrays['QC_ACTION']==Action.REJECT)&((reason&int(Reason.RADIAL_AND_POLARIMETRIC))!=0)&past.field_available['DBZH']&past.geometry_good[:,None]
  if degraded:strong[:]=False
  ix,delta,ok=nearest_azimuth_matches(current.azimuth,past.azimuth)
  ok &= delta<=min(current.audit['azimuth_spacing_deg'],past.audit['azimuth_spacing_deg'])*0.45
  ok &= current.geometry_good&past.geometry_good[ix]&(np.abs(current.elevation-past.elevation[ix])<=0.1)
  measured=ok[:,None]&past.field_available['DBZH'][ix]&current.field_available['DBZH']
  matched=measured&strong[ix]
  bounded=np.zeros(current.shape,bool);sources=[]
  dr=float(np.median(np.diff(past.ranges)))
  # Freeze source runs using ONLY independently rejected original past gates.
  # Missing current targets or newly linked fragments cannot extend these runs.
  for rr in np.flatnonzero((scope&measured).any(axis=1)):
   original=np.flatnonzero(strong[ix[rr]])
   if not len(original):continue
   for gates in np.split(original,np.flatnonzero(np.diff(past.ranges[original])>60000.)+1):
    if len(gates)*dr<10000. or np.ptp(past.ranges[gates])+dr<60000. or len(np.unique((past.ranges[gates]//20000.).astype(int)))<3:continue
    targets=np.flatnonzero(scope[rr]&measured[rr]&(current.ranges>=past.ranges[gates[0]])&(current.ranges<=past.ranges[gates[-1]]))
    if not len(targets):continue
    near=np.min(abs(current.ranges[targets,None]-past.ranges[gates][None,:]),axis=1)
    targets=targets[near<=120000.]
    if not len(targets):continue
    bounded[rr,targets]=True
    sources.append({'current_row':int(rr),'past_row':int(ix[rr]),'past_azimuth_deg':float(past.azimuth[ix[rr]]),
     'original_gates':gates.tolist(),'start_m':float(past.ranges[gates[0]]),'end_m':float(past.ranges[gates[-1]]),
     'support_m':float(len(gates)*dr),'target_gates':targets.tolist()})
  samples+=measured.astype('uint8');votes+=matched.astype('uint8')
  record.update(status='evaluated',stage_a_identity=evidence.identity,
    graph_degraded=degraded,
    measured_remaining=int((scope&measured).sum()),strong_radial_remaining=int((scope&matched).sum()))
  record.update(strong_remaining_indices=np.flatnonzero(scope&matched).tolist(),
    measured_remaining_indices=np.flatnonzero(scope&measured).tolist(),bounded_sources=sources,
    bounded_remaining_indices=np.flatnonzero(scope&bounded).tolist())
  references.append(record)
 out.append(dict(scan_id=row['scan_id'],time=row['local_time'],radar_id=row['radar_id'],
  normalized_sha256=row['normalized_sha256'],sweep=row['sweep'],
  current_stage_a_identity=current_evidence.identity,current_stage_a_degraded=current_degraded,
  current_stage_a_strong_remaining=int((scope&current_strong).sum()),
  current_stage_a_strong_remaining_indices=np.flatnonzero(scope&current_strong).tolist(),
  remaining=int(scope.sum()),measured_past=int((scope&(samples>0)).sum()),
  one_strong_past=int((scope&(votes>0)).sum()),two_strong_past=int((scope&(votes>=2)).sum()),references=references,
  profile_parameters_hash=profile.parameters_hash,frozen_profile_matches=profile.parameters_hash==row['context']['parameters_hash'],
  profile_sha256=hashlib.sha256(text.encode()).hexdigest(),action_count=0,
  interpretation='diagnostic overlap only; past contamination does not prove current contamination'))
print(json.dumps(out))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('inventory',type=Path)
    p.add_argument('snapshots',type=Path)
    p.add_argument('--time',help='Optional HH:MM fixed case')
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--host',default='yons@192.168.28.105')
    p.add_argument('--worker',default='rainpulse-radar-qc-worker-1')
    a=p.parse_args()
    if a.output.exists():raise ValueError('refuse to overwrite receipt')
    inventory={x['scan_id']:x for x in json.loads(a.inventory.read_text())['cases']}
    rows=[]
    for path in sorted(a.snapshots.glob('*.npz')):
        with np.load(path,allow_pickle=False) as data:
            meta=json.loads(str(data['METADATA']))
            if a.time and meta['local_time']!=a.time:continue
            case=inventory[meta['scan_id']]
            context=case['qc_context']['context_identity']
            r,az=data['RANGE'][None,:],data['AZIMUTH'][:,None]
            roi=((r>=250000)&(az>=285)&(az<=340)) if meta['radar_id']=='z9591' else ((r>=100000)&(az>=160)&(az<=280))
            scope=data['BEFORE']&~data['ADDED']&roi
            rows.append({**{k:meta[k] for k in ('scan_id','radar_id','local_time','normalized_uri')},
                'normalized_sha256':case['normalized_sha256'],'sweep':'sweep_%03d'%meta['sweep'],
                'shape':list(scope.shape),'azimuth':data['AZIMUTH'].tolist(),'ranges':data['RANGE'].tolist(),
                'remaining_indices':np.flatnonzero(scope).tolist(),'context':context})
    if not rows:raise ValueError('no matching snapshots')
    command=shlex.join(['docker','exec','-i',a.worker,'python','-'])
    result=subprocess.run(['ssh','-o','BatchMode=yes',a.host,command],input=WORKER.replace('PAYLOAD',repr(json.dumps(rows))),text=True,capture_output=True,timeout=1200)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    observed=json.loads(result.stdout)
    if [x['scan_id'] for x in observed]!=[x['scan_id'] for x in rows]:raise ValueError('result identity differs')
    receipt={'read_only':True,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'inventory_sha256':hashlib.sha256(a.inventory.read_bytes()).hexdigest(),'cases':observed}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(receipt,indent=2)+'\n')
    for x in observed:print(json.dumps({k:x[k] for k in ('radar_id','time','remaining','measured_past','one_strong_past','two_strong_past','frozen_profile_matches','action_count')}))


if __name__=='__main__':main()
