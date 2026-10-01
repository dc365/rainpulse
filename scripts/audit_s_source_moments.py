#!/usr/bin/env python3
"""Read actual original-moment inventory for fixed scans; no product writes."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import numpy as np

WORKER = '''import json,zarr,numpy as np
from zarr.storage import MemoryStore
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment,artifact_sha256
reader=ArtifactObjectReader(minio_client_from_environment(),max_workers=2)
out=[]
for row in ROWS:
 raw=reader.open(row['normalized_uri']).load()
 store=MemoryStore();store.update(raw);root=zarr.open_group(store=store,mode='r')
 cuts=[]
 for key in sorted(root.group_keys()):
  if not key.startswith('sweep_'):continue
  s=root[key]
  cuts.append(dict(sweep=key,source_moments=list(s.attrs.get('source_moments',[])),
      normalized_arrays=sorted(s.array_keys()),range_max_m=float(s['range'][:].max())))
 item=dict(scan_id=row['scan_id'],radar_id=row['radar_id'],local_time=row['local_time'],
     normalized_uri=row['normalized_uri'],normalized_sha256=artifact_sha256(raw),cuts=cuts)
 if 'qc_uri' in row:
  qc=reader.open(row['qc_uri']).load();qs=MemoryStore();qs.update(qc);qroot=zarr.open_group(store=qs,mode='r')
  key='sweep_%03d'%row['sweep'];q=qroot[key];s=root[key]
  r=s['range'][:][None,:];az=s['azimuth'][:][:,None]
  roi=((r>=250000)&(az>=285)&(az<=340)) if row['radar_id']=='z9591' else ((r>=100000)&(az>=160)&(az<=280))
  observed=np.isfinite(s['DBZH'][:]);scope=roi&observed
  fields={}
  for name in ('RV2_INDEPENDENT_WEATHER_AVAILABLE_MASK','SRC_REVIEW_WEATHER_PROTECTED_MASK',
   'SRC_REVIEW_CONFLICT_MASK','V7_VERTICAL_SUPPORT_SCORE','V7_CROSS_RADAR_SUPPORT_SCORE',
   'WEATHER_SUPPORT_SCORE','TEMPORAL_RFI_SAMPLE_COUNT','TEMPORAL_CANDIDATE_PERSISTENCE'):
   if name not in q:fields[name]={'present':False};continue
   values=q[name][:]
   if values.shape!=scope.shape:raise ValueError('QC context shape differs from original cut')
   actual=scope&np.isfinite(values)
   fields[name]={'present':True,'finite_observed_roi':int(actual.sum()),
     'positive_observed_roi':int((actual&(values>0)).sum())}
  item['qc_context']=dict(qc_uri=row['qc_uri'],qc_sha256=artifact_sha256(qc),sweep=key,
    raw_observed_roi=int(scope.sum()),fields=fields,root_attribute_keys=sorted(qroot.attrs.keys()))
  context=qroot.attrs.get('radial_context')
  if isinstance(context,str):
   try:context=json.loads(context)
   except json.JSONDecodeError:pass
  item['qc_context']['context_identity']={k:context[k] for k in ('artifacts','decision_cutoff_utc','support_statistics','parameters_hash') if k in context} if isinstance(context,dict) else {'stored_type':type(context).__name__}
 out.append(item)
print(json.dumps(out))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--host',default='yons@192.168.28.105')
    p.add_argument('--worker',default='rainpulse-radar-qc-worker-1')
    p.add_argument('--qc-context',action='store_true',help='Also inspect actual persisted context fields in the fixed QC artifact')
    a=p.parse_args()
    if a.output.exists():raise ValueError('refuse to overwrite receipt')
    rows=[]
    for path in sorted(a.snapshots.glob('*.npz')):
        with np.load(path,allow_pickle=False) as data:meta=json.loads(str(data['METADATA']))
        row={k:meta[k] for k in ('scan_id','radar_id','local_time','normalized_uri')}
        if a.qc_context:row.update(qc_uri=meta['qc_uri'],sweep=meta['sweep'])
        rows.append(row)
    cmd=shlex.join(['docker','exec','-i',a.worker,'python','-'])
    result=subprocess.run(['ssh','-o','BatchMode=yes',a.host,cmd],input=WORKER.replace('ROWS',repr(rows)),text=True,capture_output=True,timeout=300)
    if result.returncode:raise RuntimeError(result.stderr[-1500:])
    observed=json.loads(result.stdout)
    if len(observed)!=len(rows) or [x['scan_id'] for x in observed]!=[x['scan_id'] for x in rows]:
        raise ValueError('inventory scan identity mismatch')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'read_only':True,'cases':observed},indent=2)+'\n')
    for row in observed:
        moments=sorted({m for cut in row['cuts'] for m in cut['source_moments']})
        print(json.dumps({'site':row['radar_id'],'time':row['local_time'],'source_moments':moments,
                         'SQI_present':any('SQI' in cut['source_moments'] for cut in row['cuts'])}))


if __name__=='__main__':main()
