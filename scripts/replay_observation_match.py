import json,numpy as np
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment
from rainpulse_algo.diagnostics.renderer import _open_group
from rainpulse_algo.radar.qc_engine.adapters import NativeSweep
import argparse
from rainpulse_algo.radar.qc_engine.review_extension.config import NonPrecipConfig
from rainpulse_algo.radar.qc_engine.review_extension.observation_match import paired_doppler as pair
from rainpulse_algo.radar.qc_engine.review_extension.temporal import raw_recurrence as recurrence
parser=argparse.ArgumentParser(description="Offline observational matching coverage; no QC publication")
parser.add_argument('inputs');parser.add_argument('output');args=parser.parse_args()
cfg=NonPrecipConfig(paired_doppler_enabled=True,temporal_spatial_matching=True)
reader=ArtifactObjectReader(minio_client_from_environment());past={};results=[];volumes={}
for label,uri in sorted(json.load(open(args.inputs))):
 if not label.startswith(('z9591','z9598')):continue
 root=_open_group(reader.load(uri));sweeps=[]
 volume_end=max(np.max(root[f'sweep_{sn:03d}']['ray_time'][:]) for sn in root['sweep_number'][:])
 for sn in root['sweep_number'][:]:
  g=root[f'sweep_{sn:03d}'];az=g['azimuth'][:];order=np.argsort(az);az=az[order]
  fields={k:g[k+'_RAW'][:][order] for k in ('DBZH','RHOHV','ZDR','SNR','VR','SW') if k+'_RAW' in g}
  available={k:np.isfinite(v) for k,v in fields.items()};delta=(np.roll(az,-1)-az)%360
  attrs=dict(root.attrs)
  attrs['volume_end_time_utc']=str(volume_end)+'Z'  # Derived from all actual ray timestamps, not filename time.
  n=NativeSweep(f'sweep_{sn:03d}',az,g['elevation'][:][order],g['range'][:],g['ray_time'][:][order],fields,available,order,not (delta>2).any(),np.ones(len(az),bool),(delta>2)|(delta<=0),attrs,{})
  sweeps.append(n)
 volumes[str(root.attrs["scan_id"])]=sweeps
 for n in sweeps:
  if 'DBZH' not in n.fields:continue
  obs=n.field_available['DBZH'];ctx=pair(n,sweeps,cfg)
  av=ctx.get('NP_PAIRED_DOPPLER_AVAILABLE_MASK',np.zeros(n.shape))==1
  quiet=av&(abs(ctx.get('NP_PAIRED_VR',np.full(n.shape,np.nan)))<=1)&(ctx.get('NP_PAIRED_SW',np.full(n.shape,np.nan))<=1.5)
  rec=0;old=0;multi=0;stable=0
  key=(label.split()[0],n.name)
  if key in past:
   a,_=recurrence(n,past[key][-3:],cfg);rec=int((a['NP_FIXED_SAMPLE_COUNT']>0).sum())
   multi=int((a['NP_FIXED_SAMPLE_COUNT']>=2).sum());stable=int(((a['NP_FIXED_SAMPLE_COUNT']>=2)&(a['NP_FIXED_MATCH_FRACTION']>=.8)).sum())
   c=NonPrecipConfig();a,_=recurrence(n,past[key][-3:],c);old=int((a['NP_FIXED_SAMPLE_COUNT']>0).sum())
  past.setdefault(key,[]).append(n)
  past[key]=past[key][-3:]
  row=dict(case=label,sweep=n.name,paired_on_dbzh=int((av&obs).sum()),quiet_paired_on_dbzh=int((quiet&obs).sum()),temporal_matched=rec,temporal_exact=old,temporal_at_least_two=multi,temporal_stable_two=stable)
  results.append(row);print(json.dumps(row),flush=True)
open(args.output,'w').write(json.dumps(results,indent=2))
