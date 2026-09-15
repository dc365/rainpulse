"""Read-only published QC comparison inside a worker container.

/out/before-qc-uris.json: saved scan_id/qc_uri list before replay.
/out/current-qc.json: successful replay scan_id/radar_id/qc_uri list.
Checks per-object SHA256 for selected lowest-cut fields, raw and geometry
identity; this is not a complete-volume byte comparison or truth evaluation.
"""
import json,sys,hashlib
from pathlib import Path
import numpy as np,zarr
from zarr.storage import MemoryStore
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment,parse_s3_uri
p=Path('/out');old={x['scan_id']:x['qc_uri']for x in json.load(open(p/'before-qc-uris.json'))};reader=ArtifactObjectReader(minio_client_from_environment());reports=[]
def load(uri):
 client=minio_client_from_environment();bucket,prefix=parse_s3_uri(uri)
 def get(key):
  r=client.get_object(bucket,key)
  try:return r.read()
  finally:r.close();r.release_conn()
 marker=json.loads(get(prefix+'/_SUCCESS.json'));s=MemoryStore()
 fields={'DBZH_RAW','DBZH_QC','DBZH_USABLE','QC_ACTION','RFI_QUARANTINE_MASK','QPE_ELIGIBLE_MASK','RFI_CANDIDATE_MASK','azimuth','range'}
 for item in marker['objects']:
  key=item['key'];parts=key.split('/')
  if key in {'.zattrs','.zgroup','sweep_000/.zattrs','sweep_000/.zgroup'} or (len(parts)>2 and parts[0]=='sweep_000' and parts[1] in fields):
   data=get(prefix+'/'+marker['data_prefix']+'/'+key)
   assert hashlib.sha256(data).hexdigest()==item['sha256']
   s[key]=data
 return zarr.open_group(s,mode='r')
for row in json.load(open(p/'current-qc.json')):
 scan=row['scan_id'];before=old.get(scan)
 if not before:continue
 try:
  a,b=load(before),load(row['qc_uri']);x,y=a['sweep_000'],b['sweep_000']
  assert a.attrs['scan_id']==b.attrs['scan_id']==scan
  np.testing.assert_equal(x['DBZH_RAW'][:],y['DBZH_RAW'][:])
  for coordinate in ['azimuth','range']:np.testing.assert_equal(x[coordinate][:],y[coordinate][:])
  report={**row,'before_uri':before,'fields':{}}
  for k in ['DBZH_QC','DBZH_USABLE','QC_ACTION','RFI_QUARANTINE_MASK','QPE_ELIGIBLE_MASK']:
   u,v=x[k][:],y[k][:];report['fields'][k]=int((~((u==v)|(np.isnan(u)&np.isnan(v)))).sum())
  raw=y['DBZH_RAW'][:];visible=np.isfinite(y['DBZH_QC'][:])&(raw>=10)
  quarantine=y['RFI_QUARANTINE_MASK'][:]==1;eligible=y['QPE_ELIGIBLE_MASK'][:]==1
  candidates=y['RFI_CANDIDATE_MASK'][:]==1 if 'RFI_CANDIDATE_MASK'in y else np.zeros(raw.shape,bool)
  report['visible_quarantined']=int((visible&quarantine).sum())
  report['quarantined_eligible']=int((quarantine&eligible).sum())
  report['radial_candidate_eligible']=int((candidates&eligible&(raw>=10)).sum())
  reports.append(report);print(scan,report['fields'],report['radial_candidate_eligible'],flush=True)
 except Exception as e:reports.append({**row,'error':str(e)});print(scan,str(e),flush=True)
 (p/'qc-effect.json').write_text(json.dumps(reports,indent=2))
