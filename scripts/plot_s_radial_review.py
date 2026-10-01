#!/usr/bin/env python3
"""Read-only source-stage S-radar replay and repeatable full/zoom comparison plots.

No Worker restart, remote file write, product publication or raw modification.
Use --render-only to redraw saved NPZ snapshots without server access.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'rainpulse_algo.radar.qc_engine.review_extension.radial_revision'
MODULES = ('config', 'discontinuous', 'source_envelope', 'raw_families', 'family_joint', 'source_ledger', 'raw_fans', 'source_window', 'fan_joint', 'fan_states', 'source_footprint', 'engine', 'validation')

WORKER = r'''
import base64,io,json,os,sys,types,hashlib
from pathlib import Path
import numpy as np,yaml,zarr
from zarr.storage import MemoryStore
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment,artifact_sha256
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
p=json.loads(PAYLOAD)
def emit_packet(packet):
 view=memoryview(packet.encode())
 while view:
  try:written=os.write(sys.stdout.fileno(),view)
  except InterruptedError:continue
  if written<=0:raise RuntimeError('snapshot transport stopped before packet completion')
  view=view[written:]
for name,source in p['modules'].items():
 full=p['prefix']+'.'+name
 m=types.ModuleType(full);m.__package__=p['prefix'];sys.modules[full]=m
 exec(compile(source,'<local-candidate:'+name+'>','exec'),m.__dict__)
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.config import RadialRevisionConfig
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.engine import evaluate
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.validation import validate_revision_fields
profile_text=Path(os.environ['RAINPULSE_RADAR_QC_CONFIG']).read_text()
profile=OpenSourceQCProfile.model_validate(yaml.safe_load(profile_text))
c=profile.generalization.broad_source.source_review.radial_revision.model_dump(mode='json')
f=dict(c.get('fragment_line') or {});f.update(residual_objects_enabled=True,discontinuous_tracks_enabled=True,source_envelope_enabled=True)
f['raw_fragment_families_enabled']=bool(p.get('raw_families') or p.get('family_joint') or p.get('source_ledger') or p.get('raw_fans') or p.get('fan_joint') or p.get('window_source') or p.get('source_footprint'))
f['family_joint_enabled']=bool(p.get('family_joint'))
f['source_ledger_enabled']=bool(p.get('source_ledger') or p.get('raw_fans') or p.get('fan_joint') or p.get('window_source') or p.get('source_footprint'))
f['raw_fan_families_enabled']=bool(p.get('raw_fans') or p.get('fan_joint') or p.get('source_footprint'))
f['source_footprint_enabled']=bool(p.get('source_footprint'))
f['fan_joint_enabled']=bool(p.get('fan_joint'))
f['fan_power_states_enabled']=bool(p.get('power_states'))
f['source_window_tracks_enabled']=bool(p.get('window_source'))
c.update(mode='experiment_quarantine',fragment_line=f);cfg=RadialRevisionConfig.model_validate(c)
reader=ArtifactObjectReader(minio_client_from_environment(),max_workers=2)
for row_index,row in enumerate(p['rows']):
 raw=reader.open(row['normalized_uri']).load();qc=reader.open(row['qc_uri']).load()
 root=open_qc_input(raw).root;store=MemoryStore();store.update(qc);qroot=zarr.open_group(store=store,mode='r')
 name='sweep_%03d'%row['sweep'];n=adapt_sweep(root,name,profile);q=qroot[name]
 get=lambda k:q[k][:][n.original_indices]
 weather=get('SRC_REVIEW_WEATHER_PROTECTED_MASK');conflicts=get('SRC_REVIEW_CONFLICT_MASK')
 seed=get('SRC_REVIEW_TARGET_MATCH_MASK');delta=get('SRC_REVIEW_RESIDUAL_DB')
 prior_cfg=cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(update={'source_envelope_enabled':False,'discontinuous_tracks_enabled':False,'raw_fragment_families_enabled':False,'family_joint_enabled':False,'source_ledger_enabled':False,'raw_fan_families_enabled':False,'fan_joint_enabled':False,'fan_power_states_enabled':False,'source_footprint_enabled':False,'source_window_tracks_enabled':False})})
 prior,_=evaluate(n,prior_cfg,seed,delta,weather=weather,conflicts=conflicts)
 fields,detail=evaluate(n,cfg,seed,delta,weather=weather,conflicts=conflicts)
 footprint_baseline=fields
 if p.get('source_footprint'):
  footprint_cfg=cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(update={'source_footprint_enabled':False})})
  footprint_baseline,_=evaluate(n,footprint_cfg,seed,delta,weather=weather,conflicts=conflicts)
 validate_revision_fields(fields,n.field_available['DBZH'],seed,(weather==1)|(conflicts==1))
 before=get('DBZH_QC');visible=(get('QPE_ELIGIBLE_MASK')==1)&np.isfinite(before)&(before>=5)
 added=visible&(fields['RV2_ACTION_PROPOSAL_MASK']==1)
 candidate=visible&(fields.get('RV2_DISCONTINUOUS_CANDIDATE_MASK',np.zeros(n.shape))==1)
 held=candidate&~added
 assert not np.any(added&((weather==1)|(conflicts==1)|~n.field_available['DBZH']))
 meta={**row,'elevation_deg':float(np.median(n.elevation)),
       'scope':'source_stage_projection_not_full_QC_or_Web_product',
       'independent_weather_truth':False,'context':'stored weather/conflict masks; independent availability not reconstructed',
       'profile_sha256':hashlib.sha256(profile_text.encode()).hexdigest(),
       'raw_artifact_sha256':artifact_sha256(raw),'qc_artifact_sha256':artifact_sha256(qc),
       'source_footprint_extra_visible':int((visible&(fields['RV2_ACTION_PROPOSAL_MASK']==1)&(footprint_baseline['RV2_ACTION_PROPOSAL_MASK']==0)).sum()),
       'source_footprint_visible':int((visible&(fields.get('RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK',np.zeros(n.shape))==1)).sum()),
       'step34_extra_visible':int((visible&(fields['RV2_ACTION_PROPOSAL_MASK']==1)&(prior['RV2_ACTION_PROPOSAL_MASK']==0)).sum()),
       'discontinuous_candidate_visible':int(candidate.sum()),
       'raw_family_nomination_visible':int((visible&(fields.get('RV2_RAW_FAMILY_MASK',np.zeros(n.shape))==1)).sum()),
       'strict_discontinuous_actions_visible':int((visible&(fields['RV2_DISCONTINUOUS_MASK']==1)).sum()),
       'retained_visible_total':int((visible&~added).sum()),
       'held_measured_flanks':int((held&(fields['RV2_DISCONTINUOUS_MEASURED_MASK']==1)).sum()),
       'held_both_windows':int((held&(fields['RV2_DISCONTINUOUS_WINDOW_BITS']==3)).sum()),
       'config':c,'module_sha256':p['sha'],'new_visible_quarantine':int(added.sum()),
       'held_discontinuous_candidates':int(held.sum()),'envelope_visible':int((visible&(fields['RV2_ENVELOPE_MASK']==1)).sum()),
       'raw_unchanged':bool(np.array_equal(n.fields['DBZH'],get('DBZH_RAW'),equal_nan=True))}
 assert meta['raw_unchanged']
 diagnostic={}
 if p.get('diagnostics'):
  meta['stored_qc_flag_definition_version']=qroot.attrs.get('flag_definition_version')
  flag_path=os.environ.get('RAINPULSE_QC_FLAG_DEFINITIONS')
  if flag_path:
   flag_text=Path(flag_path).read_text();flag_doc=yaml.safe_load(flag_text)
   if flag_doc['definition_version']==meta['stored_qc_flag_definition_version']:
    meta['stored_qc_flag_definitions']={entry['name']:int(entry['mask']) for entry in flag_doc['flags']}
    meta['stored_qc_flag_definitions_sha256']=hashlib.sha256(flag_text.encode()).hexdigest()
  meta['stored_qc_array_inventory']=sorted(key for key in q if hasattr(q[key],'shape'))
  if 'QC_FLAGS' in q:meta['stored_qc_flags_attributes']=dict(q['QC_FLAGS'].attrs)
  for key in q:
   if (hasattr(q[key],'shape') and q[key].shape==n.shape and
       (key=='QC_FLAGS' or key.startswith(('RFI_','VOR_','RDR_')))):
    diagnostic['STORED_'+key]=get(key)
  meta['normalized_moment_inventory']=[{'sweep':key,'elevation_deg':float(np.median(root[key]['elevation'][:])),'moments':[moment for moment in ('DBZH','VR','SW','RHOHV','ZDR','PHIDP','SNR') if moment in root[key]]} for key in sorted(root) if key.startswith('sweep_') and key[6:].isdigit()]
  diagnostic.update({'MOMENT_'+k:v for k,v in n.fields.items() if k!='DBZH'})
  diagnostic.update({'AVAILABLE_'+k:v for k,v in n.field_available.items()})
  diagnostic.update(fields)
  diagnostic.update(SEED=seed,RESIDUAL_DB=delta,WEATHER=weather,CONFLICTS=conflicts,GEOMETRY_GOOD=n.geometry_good,GAP_AFTER=n.gap_after)
  meta['radial_detail']=detail
 buf=io.BytesIO();np.savez_compressed(buf,RAW=n.fields['DBZH'],QC=before,BEFORE=visible,ADDED=added,HELD=held,
   AZIMUTH=n.azimuth,RANGE=n.ranges,RAY_TIME=n.ray_time,ELEVATION=n.elevation,
   ENVELOPE=fields['RV2_ENVELOPE_MASK'],METADATA=np.array(json.dumps(meta)),**diagnostic)
 data=buf.getvalue();encoded=base64.b64encode(data).decode();chunks=[encoded[k:k+3072] for k in range(0,len(encoded),3072)]
 # Each packet stays below PIPE_BUF and includes sequence/whole-artifact SHA.
 # A broken transport must fail, never silently publish a partial NPZ.
 header={'index':row_index,'bytes':len(data),'chunks':len(chunks),'sha256':hashlib.sha256(data).hexdigest()}
 emit_packet('SNAPSHOT_META '+json.dumps(header)+'\n')
 for seq,chunk in enumerate(chunks):
  emit_packet('SNAPSHOT_CHUNK '+str(row_index)+' '+str(seq)+' '+chunk+'\n')
'''


def collect(args):
    query="""select coalesce(json_agg(t),'[]') from (select distinct on(s.radar_id,s.scan_id)
    s.radar_id,s.scan_id,s.volume_start_time,r.normalized_uri,r.qc_uri
    from radar_scans s join radar_scan_runs r using(scan_id)
    where s.radar_id in (SITES) and s.volume_start_time >= 'DAY'
    and s.volume_start_time < 'NEXTDAY' and r.qc_uri is not null
    order by s.radar_id,s.scan_id,r.updated_at desc) t"""
    from datetime import date,timedelta,datetime,timezone
    import re
    day=date.fromisoformat(args.date)
    if any(not re.fullmatch(r'[A-Za-z0-9]+',str(c[0])) for c in args.case):raise ValueError('Invalid radar ID')
    start=datetime.fromisoformat(str(day)+'T00:00:00+08:00').astimezone(timezone.utc)-timedelta(minutes=6)
    end=start+timedelta(days=1,minutes=12)
    query=query.replace('SITES',','.join("'"+c[0].lower()+"'" for c in args.case)).replace('NEXTDAY',end.isoformat()).replace('DAY',start.isoformat())
    cmd=shlex.join(['docker','exec','rainpulse-postgres-1','psql','-U','rainpulse','-d','rainpulse','-At','-c',query])
    rows=json.loads(subprocess.check_output(['ssh','-o','BatchMode=yes',args.host,cmd],text=True))
    from datetime import datetime,timedelta,date
    day=date.fromisoformat(args.date)
    from s_web_identity import resolve_frames
    web_rows=[] if args.offline_selection else resolve_frames(args.web_base,args.date,args.case)
    web_lookup={(r['site'],r['local_time'],r['sweep']):r for r in web_rows}
    (args.output/'web-frame-identity.json').write_text(json.dumps(web_rows,indent=2)+'\n')
    selected=[]
    exact={(site.lower(),time):scan for site,time,scan in (args.scan_id or [])}
    for site,time,sweep in args.case:
        pinned=exact.get((site.lower(),time))
        web=web_lookup.get((site.lower(),time,int(sweep)))
        if web is not None:
            if pinned is not None and pinned!=web['web_scan_id']:
                raise ValueError('Explicit scan differs from Web; offline research requires --offline-selection')
            pinned=web['web_scan_id']
        if pinned is None:raise ValueError('Offline research requires explicit --scan-id; nearest-time guessing is forbidden')
        candidates=[r for r in rows if r['radar_id']==site.lower() and r['scan_id']==pinned]
        if len(candidates)!=1:raise ValueError(f'Pinned Web/research scan has no unique QC input: {site} {time}')
        row=candidates[0]
        selected.append(dict(row,local_date=args.date,local_time=time,sweep=int(sweep),zoom=args.zoom,
            selection_method='verified_Web_raw_QC_scan_pair' if web else 'explicit_offline_scan_not_Web_verified',
            web_frame_identity=web,baseline_relation='latest_QC_for_scan_not_Web_image_generation_verified'))
    sources={n:(ROOT/'algorithms'/Path(*PREFIX.split('.'))/(n+'.py')).read_text() for n in MODULES}
    # Keep each captured response bounded: two large diagnostic volumes in
    # one docker/SSH stdout stream were observed to lose chunks. Every volume
    # has a separate manifest, verified size/SHA and atomic local file write.
    receipts=[]
    for row in selected:
        receipts.append(collect_one(args,row,sources))
        (args.output/'transport-diagnostic.json').write_text(json.dumps(receipts,indent=2)+'\n')


def collect_one(args,row,sources):
    payload=json.dumps(dict(rows=[row],diagnostics=args.diagnostics,raw_families=args.raw_families,family_joint=args.family_joint,source_ledger=args.source_ledger,raw_fans=args.raw_fans,fan_joint=args.fan_joint,power_states=args.power_states,source_footprint=args.source_footprint,window_source=args.window_source,modules=sources,prefix=PREFIX,sha={n:hashlib.sha256(s.encode()).hexdigest() for n,s in sources.items()}))
    code=WORKER.replace('PAYLOAD',repr(payload))
    result=subprocess.run(['ssh','-o','BatchMode=yes',args.host,shlex.join(['docker','exec','-i',args.worker,'python','-'])],input=code,text=True,capture_output=True,timeout=600)
    if result.returncode:raise RuntimeError(result.stderr[-6000:])
    headers={};packets={}
    for line in result.stdout.splitlines():
        if line.startswith('SNAPSHOT_META '):
            meta=json.loads(line.split(' ',1)[1]);index=meta['index']
            if index in headers:raise ValueError('Duplicate snapshot header')
            headers[index]=meta
        elif line.startswith('SNAPSHOT_CHUNK '):
            _,index,seq,chunk=line.split(' ',3);key=(int(index),int(seq))
            if key in packets:raise ValueError('Duplicate snapshot chunk')
            packets[key]=chunk
    diagnostics={'stdout_bytes':len(result.stdout.encode()),'stderr_bytes':len(result.stderr.encode()),'headers':headers,'packets':{str(index):{'received':len([k for k in packets if k[0]==index]),'missing_first_20':[seq for seq in range(header['chunks']) if (index,seq) not in packets][:20]} for index,header in headers.items()}}
    diagnostics['case']={'radar_id':row['radar_id'],'local_time':row['local_time'],'sweep':row['sweep'],'scan_id':row['scan_id']}
    diagnostic_stem=f"{row['radar_id']}_{row['local_time'].replace(':','')}_sweep_{row['sweep']:03d}"
    (args.output/(diagnostic_stem+'.transport.json')).write_text(json.dumps(diagnostics,indent=2)+'\n')
    if set(headers)!={0}:raise ValueError('Incomplete snapshot manifest')
    for index,row in enumerate([row]):
        header=headers[index]
        expected={(index,seq) for seq in range(header['chunks'])}
        if {key for key in packets if key[0]==index}!=expected:raise ValueError('Incomplete snapshot chunks')
        data=base64.b64decode(''.join(packets[(index,seq)] for seq in range(header['chunks'])),validate=True)
        if len(data)!=header['bytes'] or hashlib.sha256(data).hexdigest()!=header['sha256']:
            raise ValueError('Snapshot transport size/SHA mismatch')
        stem=f"{row['radar_id']}_{row['local_time'].replace(':','')}_sweep_{row['sweep']:03d}"
        temporary=args.output/(stem+'.npz.tmp');temporary.write_bytes(data)
        temporary.replace(args.output/(stem+'.npz'))

    return diagnostics



def render(directory, zoom_override=None):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.lines import Line2D
    cmap=LinearSegmentedColormap.from_list('radar', ['#18a8dc','#04d000','#078800','#ffc000','#ee746d','#d000b4','#b597ee'],N=256)
    rows=[]
    for path in sorted(directory.glob('*.npz')):
        with np.load(path,allow_pickle=False) as z:a={k:z[k] for k in z.files}
        m=json.loads(str(a['METADATA']));rows.append(m)
        az=np.deg2rad(a['AZIMUTH'])[:,None];r=a['RANGE'][None,:]/1000
        x=r*np.sin(az);y=r*np.cos(az)
        before=a['BEFORE'];added=a['ADDED'];held=a['HELD']
        full=float(np.max(r))+10
        zoom=zoom_override or m.get('zoom') or ((-430,-260,120,210) if m['radar_id']=='z9591' else (-460,20,-460,-50))
        fig,axes=plt.subplots(2,4,figsize=(19,10),layout='constrained')
        for line,bounds in zip(axes,[(-full,full,-full,full),zoom]):
            region=(x>=bounds[0])&(x<=bounds[1])&(y>=bounds[2])&(y<=bounds[3])
            for ax,v,title,data in zip(line,[np.isfinite(a['RAW'])&(a['RAW']>=5),before,before&~added,before&~added],['Raw','Stored QC','New source-stage projection','Changes / retained candidates'],[a['RAW'],a['QC'],a['QC'],a['QC']]):
                use=v&region
                im=ax.scatter(x[use],y[use],c=data[use],s=.8,cmap=cmap,vmin=5,vmax=70,rasterized=True)
                ax.set(xlim=bounds[:2],ylim=bounds[2:],aspect='equal',title=title,xlabel='East (km)',ylabel='North (km)');ax.grid(alpha=.2)
                if title.startswith('Changes'):
                    for mask,color in [(added,'#e32220'),(held,'#9015cc')]:
                        use=mask&region;ax.scatter(x[use],y[use],s=5,color=color,rasterized=True)
        axes[0,3].legend(handles=[Line2D([],[],marker='o',ls='',color='#e32220',label='New quarantine proposal'),Line2D([],[],marker='o',ls='',color='#9015cc',label='Unanchored candidate retained')],fontsize=8)
        fig.colorbar(im,ax=axes[:,:3],label='Reflectivity (dBZ)',shrink=.55)
        fig.suptitle(f"{m['radar_id'].upper()} {m['local_date']} {m['local_time']} CST | elevation {m['elevation_deg']:.2f} deg\nRead-only source-stage projection; not full Worker or Web output | new quarantine {int(added.sum())}, retained candidates {int(held.sum())}")
        fig.savefig(directory/(path.stem+'.png'),dpi=160);plt.close(fig)
        print(path.stem, 'new:',int(added.sum()),'held:',int(held.sum()),flush=True)
    (directory/'report.json').write_text(json.dumps(rows,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default='yons@192.168.28.105');p.add_argument('--worker',default='rainpulse-radar-qc-worker-1')
    p.add_argument('--web-base',default='http://192.168.28.105:4173',help='Resolve exact Web raw/QC frame identities')
    p.add_argument('--offline-selection',action='store_true',help='Explicit standalone research scans; requires --scan-id and never guesses from time')
    p.add_argument('--scan-id',nargs=3,action='append',metavar=('SITE','CST_HH:MM','SCAN_UUID'),help='Pin exact Web input scan identity instead of nearest preceding start')
    p.add_argument('--family-joint',action='store_true',help='Enable experimental source/polar family qualification (also nominates RAW families)')
    p.add_argument('--source-ledger',action='store_true',help='Freeze complete original sources and geometric links; this flag adds no actions')
    p.add_argument('--raw-fans',action='store_true',help='Nominate broad RAW families; no added actions')
    p.add_argument('--fan-joint',action='store_true',help='Qualify original-source models on broad RAW families')
    p.add_argument('--power-states',action='store_true',help='Diagnose original power states; never adds actions')
    p.add_argument('--source-footprint',action='store_true',help='Track residuals inside original-source angular footprint; experimental source-stage projection')
    p.add_argument('--window-source',action='store_true',help='Qualify variable-width original-source continuations')
    p.add_argument('--raw-families',action='store_true',help='Add bounded RAW short-fragment nominations; this flag adds no actions')
    p.add_argument('--diagnostics',action='store_true',help='Save raw moments, source references and all candidate diagnostics for offline investigation')
    p.add_argument('--date',default='2026-08-28');p.add_argument('--zoom',type=float,nargs=4,metavar=('WEST_KM','EAST_KM','SOUTH_KM','NORTH_KM'))
    p.add_argument('--case',nargs=3,action='append',metavar=('SITE','CST_HH:MM','SWEEP'))
    p.add_argument('--output',type=Path,required=True);p.add_argument('--render-only',action='store_true')
    a=p.parse_args();a.case=a.case or [('z9591','10:24',2),('z9598','08:42',0)]
    a.fan_joint = a.fan_joint or a.power_states
    if not a.render_only:a.output.mkdir(parents=True,exist_ok=False);collect(a)
    if a.zoom and (a.zoom[0]>=a.zoom[1] or a.zoom[2]>=a.zoom[3]):raise ValueError("Zoom bounds must increase")
    render(a.output,a.zoom)

if __name__=='__main__':main()
