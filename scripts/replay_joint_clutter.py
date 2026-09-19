"""Retrospective joint-evidence experiment. No production writes or raw edits."""
import argparse,json,runpy,sys,yaml
from pathlib import Path
import numpy as np
from rainpulse_algo.worker.object_store import ArtifactObjectReader,minio_client_from_environment
from rainpulse_algo.diagnostics.renderer import _open_group,BUSINESS_HARD_REJECT_FLAG_NAMES
from rainpulse_algo.radar.qc_engine.review_extension.config import NonPrecipConfig
from rainpulse_algo.radar.qc_engine.review_extension.temporal import raw_recurrence
from rainpulse_algo.radar.qc_engine.review_extension.observation_match import paired_doppler
from rainpulse_algo.radar.qc_engine.volume_review.composite import build_composite

class Overlay:
    def __init__(self,base,changes):self.base=base;self.changes=changes;self.attrs=base.attrs
    def __contains__(self,key):return key in self.changes or key in self.base
    def __getitem__(self,key):return self.changes[key] if key in self.changes else self.base[key]

p=argparse.ArgumentParser();p.add_argument('inputs');p.add_argument('backgrounds',type=Path);p.add_argument('output',type=Path);p.add_argument('--near-background',action='store_true');p.add_argument('--scored-evidence',action='store_true');p.add_argument('--weak-evidence',action='store_true');p.add_argument('--gates-only',action='store_true');p.add_argument('--configs',type=Path,default=Path('/opt/rainpulse/configs'));args=p.parse_args()
args.output.mkdir(parents=True,exist_ok=True)
# Reuse the exact observational replay and its retained native reference sweeps.
sys.argv=['matching',args.inputs,str(args.output/'matching.json')]
state=runpy.run_path(str(Path(__file__).with_name('replay_observation_match.py')))
past=state['past'];cfg=NonPrecipConfig(paired_doppler_enabled=True,temporal_spatial_matching=True)
reader=ArtifactObjectReader(minio_client_from_environment());cases={};report=[]
for label,uri in sorted(json.load(open(args.inputs))):
    if '00:06:' in label:continue
    station=label.split()[0];cycle=label.split(' ',1)[1]
    root=_open_group(reader.load(uri));changes={};stats={'case':label,'background_pol':0,'joint':0,'history_support':0,'doppler_support':0,'protected_changed':0,'strong_changed':0,'weak_candidate':0,'near_eligible':0,'near_snr_missing':0,'near_snr_below8':0,'weak_funnel':[]}
    if station in ('z9591','z9598'):
        bg=np.load(args.backgrounds/(station.upper()+'.npz'))
        sweeps=state['volumes'][str(root.attrs['scan_id'])]
        for n in sweeps:
            g=root[n.name];key=f'{int(n.name[-3:])+1:03d}'
            required=['DBZH_RAW','DBZH_QC','RHOHV_RAW','ZDR_RAW','SNR_RAW','NP_WEATHER_PROTECTED_MASK','REFLECTIVITY_ELIGIBLE_FOR_CR']
            if any(k not in g for k in required) or key+'_mean' not in bg:continue
            if abs(float(np.median(g['elevation'][:]))-float(bg[key+'_elevation']))>.2:raise ValueError('background elevation mismatch')
            z=g['DBZH_RAW'][:];rho=g['RHOHV_RAW'][:];zdr=g['ZDR_RAW'][:];snr=g['SNR_RAW'][:]
            ai=np.rint(g['azimuth'][:]).astype(int)%360;r=g['range'][:];ri=np.clip((r//1000).astype(int),0,74)
            mean=bg[key+'_mean'][ai[:,None],ri[None,:]];freq=bg[key+'_recurrence_lower'][ai[:,None],ri[None,:]];count=bg[key+'_observed_count'][ai[:,None],ri[None,:]]
            protected=g['NP_WEATHER_PROTECTED_MASK'][:]==1;eligible=g['REFLECTIVITY_ELIGIBLE_FOR_CR'][:]==1
            base=eligible&np.isfinite(g['DBZH_QC'][:])&(r[None,:]<75000)&(r[None,:]>=0)&(freq>=.8)&(count>=20)&np.isfinite(mean)&np.isfinite(snr)&(snr>=8)&(rho>=0)&(rho<=.85)&(abs(zdr)<=10)&((zdr< -1)|(zdr>4))&(z<=30)&(z<=mean+6)&~protected
            refs=[x for x in past[(station,n.name)] if x.attrs['volume_end_time_utc']<n.attrs['volume_end_time_utc']]
            rec,_=raw_recurrence(n,refs,cfg);history=(rec['NP_FIXED_SAMPLE_COUNT']>=2)&(rec['NP_FIXED_MATCH_FRACTION']>=.8)
            ctx=paired_doppler(n,sweeps,cfg)
            vr=n.fields.get('VR',np.full(n.shape,np.nan));sw=n.fields.get('SW',np.full(n.shape,np.nan))
            quiet=np.isfinite(vr)&np.isfinite(sw)&(abs(vr)<=1)&(sw>=0)&(sw<=1.5)
            if 'NP_PAIRED_DOPPLER_AVAILABLE_MASK' in ctx:quiet|=(ctx['NP_PAIRED_DOPPLER_AVAILABLE_MASK']==1)&(abs(ctx['NP_PAIRED_VR'])<=1)&(ctx['NP_PAIRED_SW']>=0)&(ctx['NP_PAIRED_SW']<=1.5)
            # NativeSweep fields are sorted; restore evidence to the source rows.
            h=np.zeros(z.shape,bool);q=h.copy();h[n.original_indices]=history;q[n.original_indices]=quiet
            remove=base&(h|q)
            near=eligible&np.isfinite(g['DBZH_QC'][:])&(r[None,:]>=0)&(r[None,:]<75000)
            stats['near_eligible']+=int(near.sum());stats['near_snr_missing']+=int((near&~np.isfinite(snr)).sum());stats['near_snr_below8']+=int((near&np.isfinite(snr)&(snr<8)).sum())
            if args.weak_evidence:
                coverage=bg[key+'_observed_fraction'][ai[:,None],ri[None,:]]
                p90=bg[key+'_p90'][ai[:,None],ri[None,:]]
                stats['weak_funnel'].append({'sweep':n.name,'gabella_available':'OS_GABELLA_CANDIDATE_MASK' in g,'texture_available':'OS_DBZH_TEXTURE_CANDIDATE_MASK' in g,'background_weak':int((near&(coverage>=.8)&(count>=20)&np.isfinite(p90)&(z<=10)&(z<=p90+3)).sum())})
                if all(k in g for k in ('OS_GABELLA_CANDIDATE_MASK','OS_DBZH_TEXTURE_CANDIDATE_MASK')):
                    structure=(g['OS_GABELLA_CANDIDATE_MASK'][:]==1)&(g['OS_DBZH_TEXTURE_CANDIDATE_MASK'][:]==1)
                    weak=near&(coverage>=.8)&(count>=20)&np.isfinite(p90)&np.isfinite(z)&(z<=10)&(z<=p90+3)&np.isfinite(snr)&(snr<8)&structure&~protected
                    stats['weak_funnel'][-1].update(structure=int((near&structure).sum()),structure_low_snr=int((near&structure&np.isfinite(snr)&(snr<8)).sum()),weak_before_weather_protection=int((weak| (near&(coverage>=.8)&(count>=20)&np.isfinite(p90)&(z<=10)&(z<=p90+3)&np.isfinite(snr)&(snr<8)&structure&protected)).sum()))
                    stats['weak_candidate']+=int(weak.sum());remove|=weak
            if args.scored_evidence:
                from rainpulse_algo.radar.qc_engine.review_extension.clutter_score import score_candidate
                coverage=bg[key+'_observed_fraction'][ai[:,None],ri[None,:]]
                p10=bg[key+'_p10'][ai[:,None],ri[None,:]];p90=bg[key+'_p90'][ai[:,None],ri[None,:]]
                background=(coverage>=.8)&(count>=20)&np.isfinite(p10)&np.isfinite(p90)&(z>=p10-3)&(z<=p90+3)
                low=np.isfinite(snr)&(snr<8)
                pol=np.isfinite(snr)&(snr>=8)&(rho>=0)&(rho<=.85)&(abs(zdr)<=10)&((zdr< -1)|(zdr>4))
                texture=np.zeros(z.shape,bool)
                for field in ('OS_GABELLA_CANDIDATE_MASK','OS_DBZH_TEXTURE_CANDIDATE_MASK'):
                    if field in g:texture |= g[field][:]==1
                evidence=dict(z=z,eligible=near,protected=protected,background=background,low_snr=low,
                    polarimetric=pol,temporal=h,doppler=q,texture=texture)
                remove,score,families=score_candidate(**evidence)
                stats.setdefault('score_ablations',{})
                for omitted in ('background','low_snr','polarimetric','temporal','doppler','texture'):
                    subset,_,_=score_candidate(**evidence,omit=omitted)
                    assert not (subset&~remove).any()
                    stats['score_ablations'][omitted]=stats['score_ablations'].get(omitted,0)+int(subset.sum())
                stats['scored']=stats.get('scored',0)+int(remove.sum())
            if args.near_background:
                from rainpulse_algo.radar.qc_engine.review_extension.near_background import candidate
                coverage=bg[key+'_observed_fraction'][ai[:,None],ri[None,:]]
                p10=bg[key+'_p10'][ai[:,None],ri[None,:]];p90=bg[key+'_p90'][ai[:,None],ri[None,:]]
                background=(coverage>=.8)&(count>=20)&np.isfinite(p10)&np.isfinite(p90)&(z>=p10-3)&(z<=p90+3)
                order=n.original_indices
                proposal,_,_=candidate(z[order],rho[order],snr[order],background[order],near[order],protected[order],n.azimuth,r)
                remove=np.zeros(z.shape,bool);remove[order]=proposal
                stats['near_background']=stats.get('near_background',0)+int(remove.sum())
                stats.setdefault('near_reasons',[]).append(dict(sweep=n.name,eligible=int(near.sum()),background=int((near&background).sum()),
                    reliable_low_rho=int((near&background&(snr>=8)&(rho>=0)&(rho<=.85)&(z<=20)).sum()),
                    protected_low_rho=int((near&background&(snr>=8)&(rho>=0)&(rho<=.85)&(z<=20)&protected).sum()),removed=int(remove.sum())))
            for k,v in [('background_pol',base),('joint',remove),('history_support',remove&h),('doppler_support',remove&q),('protected_changed',remove&protected),('strong_changed',remove&(z>=35))]:stats[k]+=int(v.sum())
            e=g['REFLECTIVITY_ELIGIBLE_FOR_CR'][:].copy();e[remove]=0
            changes[n.name]=Overlay(g,{'REFLECTIVITY_ELIGIBLE_FOR_CR':e})
    cases.setdefault(cycle,[]).append((root,Overlay(root,changes)))
    report.append(stats);print('JOINT',json.dumps(stats),flush=True)
if args.gates_only:
    (args.output/'gate-results.json').write_text(json.dumps(report,indent=2));sys.exit(0)
for cycle,pairs in cases.items():
    roots=[x[0] for x in pairs];altered=[x[1] for x in pairs]
    definition=yaml.safe_load((args.configs/'qc/flag-definitions-v2.yaml').read_text())
    if any(r.attrs['flag_definition_version']!=definition['definition_version'] for r in roots):raise ValueError('flag version mismatch')
    flags={f['name']:f['mask'] for f in definition['flags']}
    sites={r.attrs['radar_id']:yaml.safe_load((args.configs/'radars/fujian-20260828'/(r.attrs['radar_id']+'.yaml')).read_text())['site'] for r in roots}
    reject=0
    for name in BUSINESS_HARD_REJECT_FLAG_NAMES:reject|=int(flags.get(name,0))
    before=build_composite(roots,reject,sites=sites);after=build_composite(altered,reject,sites=sites)
    a=before.arrays['CR_TRUSTED'];b=after.arrays['CR_TRUSTED'];valid=np.isfinite(a);changed=valid&(~np.isfinite(b)|(a!=b))
    assert before.bounds==after.bounds
    assert not (np.isfinite(b)&~valid).any(), 'quarantine introduced coverage'
    assert not (np.isfinite(b)&valid&(b>a)).any(), 'quarantine increased reflectivity'
    result={'cycle':cycle,'changed_pixels':int(changed.sum()),'lost_pixels':int((valid&~np.isfinite(b)).sum()),'strong_pixels_changed':int((changed&(a>=35)).sum()),'bounds':before.bounds,'reject_mask':reject}
    report.append(result);print('COMPOSITE',json.dumps(result),flush=True)
    stem=cycle.replace(':','-').replace(' ','_');np.savez_compressed(args.output/(stem+'.npz'),before=a,after=b,changed=changed,bounds=before.bounds)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(13,4),layout='constrained');extent=[before.bounds[i] for i in (0,2,1,3)]
    for ax,data,title in zip(axes,[a,b,np.where(changed,1,np.nan)],['Current QC composite','Joint evidence candidate','Changed cells']):
        ax.imshow(data,extent=extent,origin='upper',cmap='turbo',vmin=0,vmax=50 if title!='Changed cells' else 1);ax.set(xlim=(116,121),ylim=(24.8,28.5),title=title)
    fig.suptitle(cycle+' UTC | retrospective background, not published');fig.savefig(args.output/(stem+'.png'),dpi=140);plt.close(fig)
(args.output/'results.json').write_text(json.dumps(report,indent=2))
