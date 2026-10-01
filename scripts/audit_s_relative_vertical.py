#!/usr/bin/env python3
"""Read-only real-volume replay of relative vertical positive weather support."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
WORKER=r'''
import json,os,types
from pathlib import Path
import numpy as np
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment,artifact_sha256
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.profile import load_open_source_profile
from rainpulse_algo.radar.qc_engine.standalone_evidence import stage_a
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.qc_geometry import radar_beam_context_from_config
p=json.loads(PAYLOAD)
module=types.ModuleType('candidate_relative_vertical');exec(compile(p['module'],'<candidate_relative_vertical>','exec'),module.__dict__)
profile=load_open_source_profile(os.environ['RAINPULSE_RADAR_QC_CONFIG'],os.environ['RAINPULSE_QC_FLAG_DEFINITIONS'])
reader=ArtifactObjectReader(minio_client_from_environment(),max_workers=2)
out=[]
for row in p['rows']:
 raw=reader.open(row['normalized_uri']).load()
 if artifact_sha256(raw)!=row['raw_sha256']:raise ValueError('current RAW artifact differs')
 root=open_qc_input(raw).root
 if root.attrs['scan_id']!=row['scan_id'] or str(root.attrs['radar_id']).lower()!=row['radar_id']:raise ValueError('volume identity differs')
 low=adapt_sweep(root,row['sweep'],profile)
 if not np.array_equal(low.azimuth,row['azimuth']) or not np.array_equal(low.ranges,row['ranges']):raise ValueError('native snapshot coordinates differ')
 high=[]
 for number in root['sweep_number'][:]:
  name='sweep_%03d'%number
  if 'DBZH' not in root[name]:continue
  n=adapt_sweep(root,name,profile)
  if np.median(n.elevation)>np.median(low.elevation)+.2:high.append(n)
 # Two nearest actual higher REF cuts; no fictitious matching Doppler cut.
 high=sorted(high,key=lambda n:float(np.median(n.elevation)))[:2]
 sweeps=[low]+high;donors=[np.zeros(low.shape,bool)];identities=[]
 for n in high:
  e=stage_a(n,profile);donors.append(e.donor_usable)
  identities.append({'cut':n.name,'stage_a_identity':e.identity,'donor_usable_gates':int(e.donor_usable.sum()),'graph_degraded':'graph_degradation' in e.summary})
 config_path=Path('/opt/rainpulse/configs/radars/fujian-1985-20260915')/(row['radar_id']+'.yaml')
 config_bytes=config_path.read_bytes();cfg=load_radar_config(config_path);beam=radar_beam_context_from_config(cfg)
 if cfg.radar_id.lower()!=row['radar_id']:raise ValueError('declared beam radar differs')
 scores,available,heights,report=module.support(sweeps,donors,beam_width_deg=beam.beam_width_horizontal_deg or beam.beam_width_vertical_deg,minimum_dbzh=profile.context.echo_threshold_dbz)
 scope=np.zeros(low.shape,bool);scope.flat[row['remaining_indices']]=True
 positive=scope&(available[0]==1)
 # Audit only: these gates are weather support, never removal proposals.
 out.append(dict(scan_id=row['scan_id'],radar_id=row['radar_id'],time=row['local_time'],
  normalized_sha256=artifact_sha256(raw),current_sweep=low.name,profile_parameters_hash=profile.parameters_hash,
  config_sha256=__import__('hashlib').sha256(config_bytes).hexdigest(),altitude_datum_status=beam.altitude_datum_status,
  remaining=int(scope.sum()),positive_remaining=int(positive.sum()),positive_remaining_indices=np.flatnonzero(positive).tolist(),
  positive_current_cut_total=int(available[0].sum()),donors=identities,report=report,action_count=0))
print(json.dumps(out))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots',type=Path)
    p.add_argument('--time',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--host',default='yons@192.168.28.105')
    p.add_argument('--worker',default='rainpulse-radar-qc-worker-1')
    a=p.parse_args()
    if a.output.exists():raise ValueError('refuse to overwrite receipt')
    rows=[]
    for path in a.snapshots.glob('*.npz'):
        with np.load(path,allow_pickle=False) as d:
            m=json.loads(str(d['METADATA']))
            if m['local_time']!=a.time:continue
            r,az=d['RANGE'][None,:],d['AZIMUTH'][:,None]
            roi=((r>=250000)&(az>=285)&(az<=340)) if m['radar_id']=='z9591' else ((r>=100000)&(az>=160)&(az<=280))
            scope=d['BEFORE']&~d['ADDED']&roi
            rows.append({**{k:m[k] for k in ('scan_id','radar_id','local_time','normalized_uri')},
                'sweep':'sweep_%03d'%m['sweep'],'raw_sha256':m['raw_artifact_sha256'],
                'azimuth':d['AZIMUTH'].tolist(),'ranges':d['RANGE'].tolist(),
                'remaining_indices':np.flatnonzero(scope).tolist(),'snapshot_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    if not rows:raise ValueError('no matching snapshots')
    module=(ROOT/'algorithms/rainpulse_algo/radar/qc_engine/relative_vertical.py').read_text()
    payload=json.dumps({'rows':rows,'module':module})
    command=shlex.join(['docker','exec','-i',a.worker,'python','-'])
    result=subprocess.run(['ssh','-o','BatchMode=yes',a.host,command],input=WORKER.replace('PAYLOAD',repr(payload)),text=True,capture_output=True,timeout=1200)
    if result.returncode:raise RuntimeError(result.stderr[-2000:])
    actual=json.loads(result.stdout)
    if [x['scan_id'] for x in actual]!=[x['scan_id'] for x in rows]:raise ValueError('receipt scan identity differs')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({'read_only':True,'module_sha256':hashlib.sha256(module.encode()).hexdigest(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'snapshots':rows,'cases':actual},indent=2)+'\n')
    for c in actual:print(json.dumps({k:c[k] for k in ('radar_id','time','remaining','positive_remaining','positive_current_cut_total','action_count')}))


if __name__=='__main__':main()
