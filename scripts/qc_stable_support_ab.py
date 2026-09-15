"""Offline paired QC run. Only stable positive support differs; no publication."""
import argparse, copy, hashlib, json
from pathlib import Path
import numpy as np
import zarr
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.runner import run_open_source_qc
from rainpulse_algo.radar.qc_engine.fingerprints import context_arrays_identity
from rainpulse_algo.diagnostics.renderer import BUSINESS_HARD_REJECT_FLAG_NAMES


def main():
 p=argparse.ArgumentParser();p.add_argument('--frozen',type=Path,required=True);p.add_argument('--sensitivity',type=Path,required=True);p.add_argument('--repo',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 status=json.loads((a.sensitivity/'status.json').read_text())
 if status['status']!='complete':raise ValueError('height experiment incomplete')
 sid=status['scan'];folder=a.frozen/sid/'normalized.zarr'
 objects={str(f.relative_to(folder)):f.read_bytes() for f in folder.rglob('*') if f.is_file()
          and (not f.relative_to(folder).parts[0].startswith('sweep_') or f.relative_to(folder).parts[0] in ('sweep_000','sweep_number'))}
 store=zarr.storage.MemoryStore();store.update(objects)
 memory_root=zarr.open_group(store=store,mode='a')
 memory_root.create_dataset('sweep_number',data=np.array([0],dtype='int16'),overwrite=True)
 objects=dict(store)
 root=zarr.open_group(str(folder),mode='r');q=zarr.open_group(str(a.frozen/sid/'qc.zarr'),mode='r')
 if str(root.attrs['scan_id'])!=sid:raise ValueError('source identity mismatch')
 support=np.load(a.sensitivity/'combined.npz')['stable_echo']
 if support.shape!=root['sweep_000']['DBZH'].shape or support.dtype!=np.bool_:raise ValueError('support geometry mismatch')
 profile=load_qc_profile(a.repo/'configs/qc/fujian-qc-evidence-graph-v7.yaml',a.repo/'configs/qc/flag-definitions-v2.yaml')
 if status['profile_hash']!=profile.parameters_hash:raise ValueError('profile changed')
 context={}
 for num in (0,):
  name=f'sweep_{int(num):03d}';g=q[name];fields={}
  for k in ('TEMPORAL_CANDIDATE_PERSISTENCE','TEMPORAL_RFI_SAMPLE_COUNT'):
   if k in g:fields[k]=g[k][:]
  if 'V7_CROSS_RADAR_SUPPORT_SCORE' in g:fields['WEATHER_SUPPORT_SCORE']=g['V7_CROSS_RADAR_SUPPORT_SCORE'][:]
  context[name]=fields
 variant=copy.deepcopy(context);shape=support.shape
 old=variant['sweep_000'].get('WEATHER_SUPPORT_SCORE',np.full(shape,np.nan))
 variant['sweep_000']['WEATHER_SUPPORT_SCORE']=np.where(support,1.,old).astype('float32')
 # No negative donor votes, no fabricated temporal arrays or converted heights.
 report={'scan':sid,'status':'running','operational_eligible':False,'scope':'lowest_cut_controlled_replay_not_exact_historical_job','context_before':context_arrays_identity(context),'context_after':context_arrays_identity(variant),'source_support_sha256':hashlib.sha256((a.sensitivity/'combined.npz').read_bytes()).hexdigest(),'runs':[]}
 results=[]
 for label,ctx in [('baseline',context),('stable_support',variant)]:
  result=run_open_source_qc(objects,profile,radial_context=ctx)
  s=next(s for s in result.sweeps if s.name=='sweep_000');results.append(s)
  report['runs'].append(label);(a.out/'status.json').write_text(json.dumps(report,indent=2))
 before,after=results;np.testing.assert_equal(before.dbzh_raw,after.dbzh_raw)
 hard=np.bitwise_or.reduce([profile.flag_masks[k] for k in BUSINESS_HARD_REJECT_FLAG_NAMES])
 def visible(s):return np.isfinite(s.dbzh_qc)&((s.qc_flags&hard)==0)&(s.optional_qc_fields['QPE_ELIGIBLE_MASK']==1)
 b,c=visible(before),visible(after);az=root['sweep_000']['azimuth'][:];r=root['sweep_000']['range'][:]
 roi=(az[:,None]>=200)&(az[:,None]<=280)&(r[None,:]>100000)&(before.dbzh_raw>=10)
 report.update(status='complete',stable_support_gates=int(support.sum()),stable_support_in_residual_window=int((support&roi).sum()),new_visible_ge10=int((~b&c&(before.dbzh_raw>=10)).sum()),removed_visible_ge10=int((b&~c&(before.dbzh_raw>=10)).sum()),roi_before=int((b&roi).sum()),roi_after=int((c&roi).sum()),action_changed=int((before.optional_qc_fields['QC_ACTION']!=after.optional_qc_fields['QC_ACTION']).sum()))
 np.savez_compressed(a.out/'comparison.npz',raw=before.dbzh_raw,before_visible=b,after_visible=c,stable_support=support,azimuth=az,range=r)
 import matplotlib;matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 theta=np.deg2rad(az)[:,None];x=r[None,:]/1000*np.sin(theta);y=r[None,:]/1000*np.cos(theta)
 fig,axes=plt.subplots(1,3,figsize=(15,5),constrained_layout=True)
 for ax,m,title in zip(axes,[np.isfinite(before.dbzh_raw),b,c],['Raw','Paired baseline','Stable positive support']):
  m=m&(before.dbzh_raw>=-5);im=ax.scatter(x[m],y[m],c=before.dbzh_raw[m],s=.4,vmin=-10,vmax=70,cmap='turbo');ax.set(title=title,xlim=(-460,460),ylim=(-460,460),aspect='equal')
 fig.colorbar(im,ax=axes,label='dBZ');fig.suptitle('Z9598 08:35 / offline engineering experiment');fig.savefig(a.out/'comparison.png',dpi=140);plt.close(fig)
 (a.out/'status.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if not k.startswith('context')}))
if __name__=='__main__':main()
