#!/usr/bin/env python3
"""Audit stored QC consumed by exact Web diagnostics, without algorithm replay."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import uuid
import numpy as np
from s_web_identity import resolve_frames
from audit_s_source_footprint import select_roi


def diagnostic_id(frame):
    match=re.fullmatch(r'/api/v1/diagnostics/([0-9a-f-]{36})/layers/[a-z0-9-]+',frame['image_url'])
    if not match:raise ValueError('unrecognized Web diagnostic frame')
    return str(uuid.UUID(match[1]))


def published_input(web,receipt):
    """A latest QC URI is insufficient: bind to the actual rendered job input."""
    raw=diagnostic_id(web['raw_frame']);qc=diagnostic_id(web['qc_frame'])
    if raw!=qc or receipt['job_id']!=qc or receipt['status']!='SUCCEEDED':
        raise ValueError('Web diagnostic generation mismatch or incomplete')
    inputs=receipt['request_payload']['payload']['radar_inputs']
    selected=[x for x in inputs if x['scan_id']==web['web_scan_id'] and x['radar_id']==web['site']]
    if len(selected)!=1:raise ValueError('diagnostic must consume unique exact Web station/scan')
    return selected[0]['qc_uri']


def evidence_selection(path,snapshot,shape):
    """Bind a diagnostic selection to its original snapshot; no QC authority."""
    with np.load(path,allow_pickle=False) as d:
        proof=json.loads(str(d['METADATA']))
        fields=[k for k in d.files if k.endswith('STRONG_MASK')]
        if len(fields)!=1:raise ValueError('unique strong evidence mask required')
        selected=np.asarray(d[fields[0]])
    digest=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if proof['input_snapshot_sha256']!=digest:raise ValueError('evidence snapshot mismatch')
    if selected.shape!=shape or not np.isfinite(selected).all() or not np.isin(selected,[0,1]).all():
        raise ValueError('invalid diagnostic evidence selection')
    return selected.astype(bool),dict(evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        diagnostic_selection_only=True,field=fields[0])


def target_selection(before,added,roi,mode='source-remaining'):
    """Select local proposals or residuals for observation, never QC authority."""
    masks=[np.asarray(x) for x in (before,added,roi)]
    if any(x.shape!=masks[0].shape or not np.isfinite(x).all() or
           not np.isin(x,[0,1]).all() for x in masks):
        raise ValueError('target selection requires matching finite binary masks')
    if mode not in ('source-remaining','source-added'):
        raise ValueError('unknown target selection mode')
    before,added,roi=[x.astype(bool) for x in masks]
    return before & (added if mode=='source-added' else ~added) & roi


def remote(host,worker,code,timeout=120):
    r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,
        shlex.join(['docker','exec','-i',worker,'python','-'])],input=code,text=True,
        capture_output=True,timeout=timeout)
    if r.returncode:raise RuntimeError(r.stderr[-3000:])
    lines=[line[len('PUBLISHED_AUDIT '):] for line in r.stdout.splitlines() if line.startswith('PUBLISHED_AUDIT ')]
    if len(lines)!=1:raise ValueError('missing or duplicate stored-product audit')
    return json.loads(lines[0])


WORKER=r'''
import json,os,hashlib,numpy as np,yaml,zarr
from pathlib import Path
from zarr.storage import MemoryStore
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment,artifact_sha256
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
p=json.loads(PAYLOAD)
profile_text=Path(os.environ['RAINPULSE_RADAR_QC_CONFIG']).read_text()
profile=OpenSourceQCProfile.model_validate(yaml.safe_load(profile_text))
reader=ArtifactObjectReader(minio_client_from_environment(),max_workers=2)
raw=reader.open(p['normalized_uri']).load();qc=reader.open(p['qc_uri']).load()
assert artifact_sha256(raw)==p['raw_sha256'],'original normalized artifact changed'
root=open_qc_input(raw).root;store=MemoryStore();store.update(qc)
qroot=zarr.open_group(store=store,mode='r');name='sweep_%03d'%p['sweep']
n=adapt_sweep(root,name,profile);q=qroot[name]
assert str(qroot.attrs['scan_id'])==p['scan_id'],'stored QC scan mismatch'
assert str(qroot.attrs['normalized_volume_uri'])==p['normalized_uri'],'stored QC normalized input mismatch'
rows=np.asarray(p['rows'],dtype=int);cols=np.asarray(p['cols'],dtype=int)
assert ((rows>=0)&(rows<n.shape[0])).all() and ((cols>=0)&(cols<n.shape[1])).all()
assert np.allclose(n.azimuth[rows],p['azimuth'],rtol=0,atol=.0001),'native angle mismatch'
assert np.allclose(n.ranges[cols],p['range_m'],rtol=0,atol=.001),'native range mismatch'
get=lambda k:np.asarray(q[k][:])[n.original_indices]
stored_raw=get('DBZH_RAW')
assert np.array_equal(stored_raw,n.fields['DBZH'],equal_nan=True),'RAW changed in published QC'
assert np.allclose(stored_raw[rows,cols],p['raw_dbzh'],rtol=0,atol=.0001),'target measurements changed'
values=get('DBZH_QC')[rows,cols];eligible=get('QPE_ELIGIBLE_MASK')[rows,cols]==1
visible=eligible&np.isfinite(values)&(values>=5)
target_records=[dict(row=int(row),column=int(col),azimuth_deg=float(n.azimuth[row]),
 range_m=float(n.ranges[col]),raw_dbzh=float(stored_raw[row,col]),
 qc_dbzh=float(value) if np.isfinite(value) else None,
 qpe_eligible=bool(ok),renderer_visible=bool(shown))
 for row,col,value,ok,shown in zip(rows,cols,values,eligible,visible)]
fields={}
for key in ('QC_FLAGS','RFI_QUARANTINE_MASK','QC_ACTION','RV2_ACTION_PROPOSAL_MASK',
            'RV2_SOURCE_LEDGER_LINK_MASK','RV2_MORPH_OBJECT_STRONG_MASK','RV2_MORPH_OBJECT_WEATHER_VETO_MASK'):
 if key in q:
  keys,counts=np.unique(get(key)[rows,cols],return_counts=True)
  fields[key]={str(k):int(v) for k,v in zip(keys,counts)}
attrs={k:qroot.attrs.get(k) for k in ('scan_id','radar_id','qc_profile','qc_pipeline_version',
    'decision_version','flag_definition_version','qc_parameters_sha256')}
report=dict(scope='exact_Web_consumed_stored_QC_not_replay',qc_uri=p['qc_uri'],
    raw_artifact_sha256=artifact_sha256(raw),qc_artifact_sha256=artifact_sha256(qc),
    active_profile_sha256=hashlib.sha256(profile_text.encode()).hexdigest(),
    active_parameters_sha256=profile.parameters_hash,
    stored_parameters_match_active=qroot.attrs.get('qc_parameters_sha256')==profile.parameters_hash,
    target_gates=len(rows),renderer_eligible_visible_gates=int(visible.sum()),target_records=target_records,
    raw_unchanged=True,stored_attributes=attrs,stored_target_fields=fields,
    product_writes=False,algorithm_replay=False,independent_weather_truth=False)
print('PUBLISHED_AUDIT '+json.dumps(report,allow_nan=False))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot',type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--host',default='yons@192.168.28.105')
    p.add_argument('--worker',default='rainpulse-radar-qc-worker-1')
    p.add_argument('--web-base',default='http://192.168.28.105:4173')
    p.add_argument('--azimuth',type=float,nargs=2);p.add_argument('--range-min',type=float,default=0.)
    p.add_argument('--evidence',type=Path,help='Restrict read-only audit to bound prototype strong nominations')
    p.add_argument('--selection',choices=('source-remaining','source-added'),default='source-remaining',
        help='Audit local source-stage residuals or already-proposed gates in actual published QC')
    args=p.parse_args()
    if args.output.exists():raise ValueError('output must be new')
    with np.load(args.snapshot,allow_pickle=False) as d:a={k:d[k] for k in d.files}
    meta=json.loads(str(a['METADATA']))
    previous=meta['web_frame_identity']
    web=resolve_frames(args.web_base,meta['local_date'],[(meta['radar_id'],meta['local_time'],meta['sweep'])])[0]
    if web['web_scan_id']!=meta['scan_id'] or previous['web_scan_id']!=meta['scan_id']:
        raise ValueError('original and current Web scan identities differ')
    job=diagnostic_id(web['qc_frame'])
    sql="SELECT row_to_json(t) FROM (SELECT d.job_id,d.analysis_id,d.status,j.request_payload FROM diagnostic_runs d JOIN jobs j USING(job_id) WHERE d.job_id='"+job+"') t"
    query=['docker','exec','rainpulse-postgres-1','psql','-U','rainpulse','-d','rainpulse','-At','-c',sql]
    receipt=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',args.host,shlex.join(query)],
        capture_output=True,text=True,check=True,timeout=30)
    diagnostic=json.loads(receipt.stdout);qc_uri=published_input(web,diagnostic)
    roi=select_roi(a['AZIMUTH'],a['RANGE'],range_min=args.range_min,
        azimuth_start=args.azimuth[0] if args.azimuth else None,azimuth_end=args.azimuth[1] if args.azimuth else None)
    target=target_selection(a['BEFORE'],a['ADDED'],roi,args.selection)
    evidence={}
    if args.evidence:
        selected,evidence=evidence_selection(args.evidence,args.snapshot,target.shape)
        target &= selected
    rows,cols=np.where(target)
    if not 0<len(rows)<=10000:raise ValueError('stored-product target audit requires 1–10000 gates')
    payload=dict(scan_id=meta['scan_id'],sweep=meta['sweep'],normalized_uri=meta['normalized_uri'],
        raw_sha256=meta['raw_artifact_sha256'],qc_uri=qc_uri,rows=rows.tolist(),cols=cols.tolist(),
        azimuth=a['AZIMUTH'][rows].tolist(),range_m=a['RANGE'][cols].tolist(),raw_dbzh=a['RAW'][rows,cols].tolist())
    result=remote(args.host,args.worker,WORKER.replace('PAYLOAD',repr(json.dumps(payload))))
    result.update(web_frame_identity=web,diagnostic_job_id=job,diagnostic_analysis_id=diagnostic['analysis_id'],
        selection={'mode':args.selection,'azimuth':args.azimuth,'range_min':args.range_min,**evidence},
        snapshot_sha256=hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
