#!/usr/bin/env python3
"""Read-only complete normalized S volumes; no stored-QC or weather-truth claim."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import uuid

ROOT=Path(__file__).resolve().parents[1]
MODULE='rainpulse_algo.radar.qc_engine.review_extension.radial_revision.fragment_constellation'


def validate_plan(plan):
    if not isinstance(plan,list) or not 1<=len(plan)<=16:
        raise ValueError('requires 1–16 frozen normalized volumes')
    seen=set()
    for row in plan:
        if not re.fullmatch('z[0-9]+',row['radar_id']):raise ValueError('invalid S station')
        scan=str(uuid.UUID(row['scan_id']))
        uri=f"s3://rainpulse/radar/normalized/{row['radar_id']}/{scan}/volume.zarr"
        if row['normalized_uri']!=uri or scan in seen:raise ValueError('ambiguous normalized input identity')
        seen.add(scan)
    return plan


WORKER=r'''
import json,os,re,types,sys,hashlib,numpy as np,yaml,io,base64
from pathlib import Path
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment,artifact_sha256
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
p=json.loads(PAYLOAD)
m=types.ModuleType(p['module']);m.__package__=p['module'].rsplit('.',1)[0]
exec(compile(p['source'],'<frozen-local-S-morphology>','exec'),m.__dict__)
profile_text=Path(os.environ['RAINPULSE_RADAR_QC_CONFIG']).read_text()
profile=OpenSourceQCProfile.model_validate(yaml.safe_load(profile_text))
reader=ArtifactObjectReader(minio_client_from_environment(),max_workers=2)
results=[]
def emit(line):
 view=memoryview((line+'\n').encode())
 while view:
  written=os.write(sys.stdout.fileno(),view)
  if written<=0:raise RuntimeError('preview transport interrupted')
  view=view[written:]
for row in p['plan']:
 raw=reader.open(row['normalized_uri']).load();root=open_qc_input(raw).root
 assert root.attrs['scan_id']==row['scan_id'] and root.attrs['radar_id']==row['radar_id']
 cuts=[]
 for name in sorted(k for k in root if re.fullmatch(r'sweep_[0-9]{3}',k)):
  g=root[name]
  if 'DBZH' not in g:continue
  n=adapt_sweep(root,name,profile)
  original=n.fields['DBZH'].copy()
  # Upstream weather/conflict context is absent: this is source-only research.
  blocked=np.zeros(n.shape,bool)
  try:
   arrays,detail=m.detect(n,blocked,segment_evidence=True)
   m.validate(arrays,n,blocked,segment_evidence=True)
   strong=arrays[m.PREFIX+'STRONG_MASK']==1
   weather=arrays[m.PREFIX+'WEATHER_VETO_MASK']==1
   assert not (strong&weather).any()
   assert np.array_equal(original,n.fields['DBZH'],equal_nan=True)
   if p['preview'] and strong.any():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    az=np.deg2rad(n.azimuth[:,None]);r=n.ranges[None,:]/1000
    x=r*np.sin(az);y=r*np.cos(az);measured=n.field_available['DBZH']&np.isfinite(original)&(original>=5)
    fig,axes=plt.subplots(1,2,figsize=(12,6),layout='constrained')
    full=float(r.max())+10
    for ax,title in zip(axes,['Full original RAW','Research proposals in red; no product changes']):
     ax.scatter(x[measured],y[measured],c=original[measured],s=1,cmap='turbo',vmin=5,vmax=70,rasterized=True)
     ax.set(xlim=(-full,full),ylim=(-full,full),aspect='equal',title=title,xlabel='East (km)',ylabel='North (km)')
     ax.grid(alpha=.2)
    axes[1].scatter(x[strong],y[strong],color='red',s=5)
    fig.suptitle(row['radar_id']+' '+row['scan_id'][:8]+' '+name+' | source-only morphology; weather truth unavailable')
    buf=io.BytesIO();fig.savefig(buf,format='png',dpi=120);plt.close(fig)
    data=buf.getvalue();encoded=base64.b64encode(data).decode();chunks=[encoded[i:i+3072] for i in range(0,len(encoded),3072)]
    filename=row['radar_id']+'_'+row['scan_id']+'_'+name+'.png'
    emit('NORMALIZED_PREVIEW_META '+json.dumps(dict(filename=filename,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data),chunks=len(chunks))))
    for i,chunk in enumerate(chunks):emit('NORMALIZED_PREVIEW_CHUNK '+filename+' '+str(i)+' '+chunk)
   cuts.append(dict(sweep=name,elevation_deg=float(np.median(n.elevation)),status='EVALUATED',
    native_shape=list(n.shape),measured_gates=int(n.field_available['DBZH'].sum()),
    observed_weather_gates=int(weather.sum()),candidate_gates=int((arrays[m.PREFIX+'MASK']==1).sum()),
    strong_gates=int(strong.sum()),objects=len(detail['objects']),work=detail['work'],raw_unchanged=True))
  except m.ResourceLimit as error:
   cuts.append(dict(sweep=name,status='RESOURCE_ABSTAIN',reason=str(error)))
 results.append(dict(**row,raw_artifact_sha256=artifact_sha256(raw),cuts=cuts))
print('NORMALIZED_AUDIT '+json.dumps(dict(scope='complete_normalized_source_only_no_product_actions',
 upstream_weather_context=False,independent_weather_truth=False,product_writes=False,action_gates=0,
 active_profile_sha256=hashlib.sha256(profile_text.encode()).hexdigest(),volumes=results),allow_nan=False))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--host',default='yons@192.168.28.105')
    p.add_argument('--worker',default='rainpulse-radar-qc-worker-1')
    p.add_argument('--preview-dir',type=Path,help='Save hash-verified full RAW/proposal PNGs for cuts with proposals')
    args=p.parse_args()
    if args.output.exists():raise ValueError('output must be new')
    if args.preview_dir and args.preview_dir.exists():raise ValueError('preview directory must be new')
    plan_text=args.plan.read_bytes();plan=validate_plan(json.loads(plan_text))
    source=(ROOT/'algorithms'/Path(*MODULE.split('.'))).with_suffix('.py').read_text()
    payload=dict(plan=plan,module=MODULE,source=source,preview=bool(args.preview_dir))
    command=shlex.join(['docker','exec','-i',args.worker,'python','-'])
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',args.host,command],
        input=WORKER.replace('PAYLOAD',repr(json.dumps(payload))),text=True,capture_output=True,timeout=600)
    if result.returncode:raise RuntimeError(result.stderr[-5000:])
    lines=[x[len('NORMALIZED_AUDIT '):] for x in result.stdout.splitlines() if x.startswith('NORMALIZED_AUDIT ')]
    if len(lines)!=1:raise ValueError('incomplete normalized audit receipt')
    report=json.loads(lines[0]);report.update(plan_sha256=hashlib.sha256(plan_text).hexdigest(),
        detector_sha256=hashlib.sha256(source.encode()).hexdigest(),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if args.preview_dir:
        headers={};chunks={}
        for line in result.stdout.splitlines():
            if line.startswith('NORMALIZED_PREVIEW_META '):
                header=json.loads(line[len('NORMALIZED_PREVIEW_META '):]);name=header['filename']
                if not re.fullmatch(r'z[0-9]+_[0-9a-f-]{36}_sweep_[0-9]{3}\.png',name) or name in headers:
                    raise ValueError('invalid preview manifest')
                headers[name]=header
            elif line.startswith('NORMALIZED_PREVIEW_CHUNK '):
                _,name,seq,chunk=line.split(' ',3);key=(name,int(seq))
                if key in chunks:raise ValueError('duplicate preview packet')
                chunks[key]=chunk
        args.preview_dir.mkdir(parents=True,exist_ok=False);previews=[]
        for name,header in headers.items():
            expected={(name,i) for i in range(header['chunks'])}
            if {key for key in chunks if key[0]==name}!=expected:raise ValueError('incomplete preview packets')
            data=base64.b64decode(''.join(chunks[(name,i)] for i in range(header['chunks'])),validate=True)
            if len(data)!=header['bytes'] or hashlib.sha256(data).hexdigest()!=header['sha256']:
                raise ValueError('preview SHA or size mismatch')
            with (args.preview_dir/name).open('xb') as f:f.write(data)
            previews.append(header)
        if set(chunks)-{(name,i) for name,h in headers.items() for i in range(h['chunks'])}:
            raise ValueError('unbound preview packets')
        report['previews']=previews
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:json.dump(report,f,indent=2,allow_nan=False);f.write('\n')
    for v in report['volumes']:
        print(v['radar_id'],v['scan_id'],'cuts',len(v['cuts']),'strong',sum(c.get('strong_gates',0) for c in v['cuts']))


if __name__=='__main__':main()
