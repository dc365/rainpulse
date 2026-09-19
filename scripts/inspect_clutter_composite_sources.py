import argparse,json,io
import numpy as np
from rainpulse_algo.worker.object_store import minio_client_from_environment
parser=argparse.ArgumentParser(description='Read-only provenance of 75 km station regions, historical validation cycles')
parser.add_argument('output');args=parser.parse_args()
c=minio_client_from_environment();out=[]
for cycle in ['41541bd8-6b60-54ed-b577-4371defa29f4','21c0c6c5-63f7-5f5b-833a-dc3d70dad777']:
 objects=[o for o in c.list_objects('rainpulse',prefix=f'diagnostics/{cycle}/radar-diagnostic-renderer-1.5.1/',recursive=True) if o.object_name.endswith('volume_review/composite.json')]
 obj=max(objects,key=lambda o:o.last_modified);prefix=obj.object_name[:-len('composite.json')]
 def read(k):
  r=c.get_object('rainpulse',k)
  try:return r.read()
  finally:r.close();r.release_conn()
 meta=json.loads(read(obj.object_name));a=np.load(io.BytesIO(read(prefix+'composite.npz')))
 west,south,east,north=meta['bounds'];ny,nx=a['WINNER_SOURCE'].shape
 lon=west+(np.arange(nx)+.5)*(east-west)/nx;lat=north-(np.arange(ny)+.5)*(north-south)/ny
 for station,x,y in [('z9591',119.541,25.991),('z9598',117.081,27.009)]:
  roi=((lon[None,:]-x)*111*np.cos(np.deg2rad(y)))**2+((lat[:,None]-y)*111)**2<=75**2
  good=roi&np.isfinite(a['CR_TRUSTED']);counts=[]
  ids,nums=np.unique(a['WINNER_SOURCE'][good],return_counts=True)
  for sid,num in zip(ids,nums):
   if sid<0:continue
   src=meta['sources'][int(sid)];sel=good&(a['WINNER_SOURCE']==sid)
   counts.append(dict(source=src,pixels=int(num),median_dbzh=float(np.median(a['CR_TRUSTED'][sel])),median_height_m=float(np.median(a['WINNER_HEIGHT_ABOVE_RADAR_M'][sel]))))
  out.append(dict(cycle=cycle,region=station,radius_km=75,pixels=int(good.sum()),sources=sorted(counts,key=lambda v:-v['pixels']),object=obj.object_name))
json.dump(out,open(args.output,'w'),indent=2)
print('PROVENANCE_DONE',flush=True)
