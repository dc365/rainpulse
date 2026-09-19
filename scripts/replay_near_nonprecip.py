import json,numpy as np
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment
from rainpulse_algo.diagnostics.renderer import _open_group
from rainpulse_algo.radar.qc_engine.adapters import NativeSweep
import argparse
from rainpulse_algo.radar.qc_engine.review_extension.config import NonPrecipConfig as C
from rainpulse_algo.radar.qc_engine.review_extension.nonprecip import classify
parser=argparse.ArgumentParser(description="Replay near-site classification against frozen QC assets; does not publish products")
parser.add_argument("inputs", help="JSON list of [case label, committed QC URI]")
args=parser.parse_args()
cfg=C(near_enabled=True,quarantine_classes=('near_nonmet',),mode='experiment_quarantine')
reader=ArtifactObjectReader(minio_client_from_environment())
for label,uri in json.load(open(args.inputs)):
 r=_open_group(reader.load(uri)); total=0
 for sn in r['sweep_number'][:]:
  g=r[f'sweep_{sn:03d}']
  if 'DBZH_RAW' not in g:continue
  fields={k:g[k+'_RAW'][:] for k in ('DBZH','RHOHV','ZDR','SNR','VR','SW') if k+'_RAW' in g}
  az=g['azimuth'][:];order=np.argsort(az);az=az[order];delta=(np.roll(az,-1)-az)%360
  gap=(delta>2)|(delta<=0);good=np.ones(len(az),bool)
  fields={k:v[order] for k,v in fields.items()};available={k:np.isfinite(v) for k,v in fields.items()}
  n=NativeSweep(str(sn),az,g['elevation'][:][order],g['range'][:],np.arange(len(az)),fields,available,order,not gap.any(),good,gap,dict(r.attrs),{})
  e={k:g[k][:][order] for k in ('OS_GABELLA_CANDIDATE_MASK','OS_DBZH_TEXTURE_CANDIDATE_MASK') if k in g}
  weather=g['NP_WEATHER_PROTECTED_MASK'][:][order].astype(float)
  x=classify(n,e,cfg,weather_support=weather)
  usable=g['REFLECTIVITY_TRUST_MASK'][:][order]==1
  add=(x.arrays['NP_PROPOSAL_MASK']==1)&usable
  total+=int(add.sum())
  print(json.dumps({'case':label,'sweep':int(sn),'texture_fields':list(e),'candidate':int(x.arrays['NP_NEAR_CANDIDATE_MASK'].sum()),'added':int(add.sum()),'protected_changed':int((add&(weather>0)).sum())}),flush=True)
 print(json.dumps({'case':label,'total_added':total}),flush=True)
