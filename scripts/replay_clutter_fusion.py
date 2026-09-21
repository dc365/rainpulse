#!/usr/bin/env python3
"""Read-only qc-case comparison against isolated NMR-core, NOT operational QC.

No PNG labels, location rules or background inferred. --portable-reader supports
only the explicit existing Zarr-v2/Blosc contract. Use a NEW output directory.
"""
from pathlib import Path
from dataclasses import replace
import argparse,gc,json,resource,sys,time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"algorithms/rainpulse_algo/radar/qc_engine"))
from receiver_domain_case_io import load_sweep,read_array,path_under,content_digest
from volume_review.clutter_fusion.config import ClutterFusionConfig as C
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.disposition import apply,CR
from volume_review.clutter_fusion.classifier import protections
from volume_review.clutter_fusion.context import sample_ground
from volume_review.near_measurement.config import NearMeasurementConfig
from volume_review.near_measurement.core import evaluate as near_evaluate
from volume_review.receipts import npz_bytes


def load(path,portable):
    s,order=load_sweep(path,portable);fields=dict(s.fields);avail=dict(s.available)
    for k in ("VR","SW"):
        if not (path/k/'.zarray').exists():continue
        value=read_array(path/k,portable);valid=np.isfinite(value);codes=path/(k+'_RAW_CODE')
        if (codes/'.zarray').exists():
            attrs=json.loads((codes/'.zattrs').read_text());code=read_array(codes,portable)
            reserved=list(attrs.get('reserved_codes',[]))
            if 'absent_moment_code' in attrs:reserved.append(attrs['absent_moment_code'])
            valid &= ~np.isin(code,reserved)
        if k=='SW':valid &= value>=0
        fields[k]=value[order];avail[k]=valid[order]
    return replace(s,fields=fields,available=avail)


def parent_fields(s,n):
    z=s.fields['DBZH'];obs=s.observed
    a={"DBZH_RAW":z,"DBZH_QC":z,"DBZH_USABLE":np.where(obs,z,np.nan).astype('float32'),
       "VALID_MASK":obs.astype('uint8'),"REFLECTIVITY_TRUST_MASK":obs.astype('uint8'),
       "QPE_ELIGIBLE_MASK":obs.astype('uint8'),"QC_ACTION":np.where(obs,0,3).astype('uint8'),
       "QUALITY_INDEX":np.full(s.shape,.8,'float32'),"QC_FLAGS":np.zeros(s.shape,'uint32'),
       "LOW_QUALITY_MASK":np.zeros(s.shape,'uint8'),"CR_UNCERTAIN_MASK":np.zeros(s.shape,'uint8')}
    mask=(n['NMR_NONMET_CANDIDATE_MASK']==1)|(n['NMR_LOW_SNR_UNCERTAIN_MASK']==1)
    a[CR]=(obs&~mask).astype('uint8');a['CR_QUALIFICATION_REASON']=a[CR].astype('uint16')
    a['NMR_WEATHER_PROXY_MASK']=n['NMR_WEATHER_PROXY_MASK'];a['NMR_PREVIOUS_WEATHER_MASK']=n['NMR_PREVIOUS_WEATHER_MASK']
    return a


def change(before,after,domain):
    result={}
    for lower in (-10.,0.,10.):
        base=domain&np.isfinite(before)&(before>=lower)&(before<30.)
        loss=base&(~np.isfinite(after)|(after<lower));changed=base&(~np.isfinite(after)|(after<before-1e-6))
        result[str(int(lower))]={"baseline_cells":int(base.sum()),"coverage_loss_cells":int(loss.sum()),
             "coverage_loss_fraction":float(loss.sum()/max(1,base.sum())),"value_changed_cells":int(changed.sum()),
             "unavailable_cells":int((base&~np.isfinite(after)).sum())}
    return result


def evaluate_case(root,case,output,portable=False):
    path=path_under(root,case['normalized_zarr']);actual=content_digest(path)
    if actual!=case['input_sha256']:raise ValueError('frozen input content hash mismatch')
    started=time.perf_counter();ss=[load(p,portable) for p in sorted(path.glob('sweep_[0-9][0-9][0-9]'))]
    raw_before=[s.digest for s in ss];c=C(mode='cr_withhold',depolarization_backend='numpy_reference')
    nc=NearMeasurementConfig(mode='experiment',depolarization_backend='numpy_reference',uncertainty_snr_db=8.)
    masks=[];protect=[]
    for s in ss:
        f={k+'_RAW':v for k,v in s.fields.items()};f['VALID_MASK']=s.observed.astype('uint8')
        nr=near_evaluate(f,s.azimuth,s.ranges,nc,available=s.available,geometry_good=s.good,gap_after=s.gap_after).arrays
        small={k:nr[k] for k in ('NMR_NONMET_CANDIDATE_MASK','NMR_LOW_SNR_UNCERTAIN_MASK','NMR_WEATHER_PROXY_MASK','NMR_PREVIOUS_WEATHER_MASK')}
        masks.append(small);protect.append(protections(parent_fields(s,small),.7))
    evidence=evaluate_volume(ss,c,protections=protect)
    grid=np.arange(-75000.,75001.,1000.);xx,yy=np.meshgrid(grid,grid[::-1]);distance=np.hypot(xx,yy);angle=np.rad2deg(np.arctan2(xx,yy))%360
    shape=xx.shape;maps={k:np.full(shape,np.nan,'float32') for k in ('RAW','NMR_CORE','NMR_PLUS_FUSION')}
    winning={k:np.full(shape,-1,'int32') for k in ('WINNER_SWEEP','WINNER_RAY','WINNER_GATE')}
    records=[];final_masks=[]
    for i,(s,ev,nm) in enumerate(zip(ss,evidence,masks,strict=True)):
        b=parent_fields(s,nm);after,delta=apply(b,ev.arrays,c,low_quality_flag=1024)
        audit,_=apply(b,ev.arrays,c.model_copy(update={'mode':'audit'}),low_quality_flag=1024)
        for k in b:
            if not np.array_equal(audit[k],b[k],equal_nan=True):raise AssertionError('audit mutated baseline '+k)
        quarantine,qd=apply(b,ev.arrays,c.model_copy(update={'mode':'quarantine'}),low_quality_flag=1024)
        if np.any((after[CR]==1)&(b[CR]==0)):raise AssertionError('old exclusion restored')
        if not np.array_equal(after['QPE_ELIGIBLE_MASK'],b['QPE_ELIGIBLE_MASK']):raise AssertionError('CR mode altered QPE')
        if np.any((ev.arrays['CF_NONMET_SUPPORTED_MASK']==1)&(s.fields['DBZH']>=30)):raise AssertionError('strong echo modified')
        final_masks.append(after[CR].copy());records.append({'sweep':s.name,'elevation_deg':float(np.median(s.elevation)),
                        'evidence':ev.summary,'cr_disposition':delta,'optional_quarantine':qd})
        row,gate,foot,_=sample_ground(s,angle,distance);obs=foot&s.observed[row,gate];z=s.fields['DBZH'][row,gate]
        for k,valid in [('RAW',obs),('NMR_CORE',obs&(b[CR][row,gate]==1)),('NMR_PLUS_FUSION',obs&(after[CR][row,gate]==1))]:
            vals=np.where(valid,z,np.nan)
            if k=='NMR_PLUS_FUSION':
                win=valid&(~np.isfinite(maps[k])|(z>maps[k]));winning['WINNER_SWEEP'][win]=i
                winning['WINNER_RAY'][win]=row[win];winning['WINNER_GATE'][win]=gate[win]
            maps[k]=np.fmax(maps[k],vals)
        del audit,after,quarantine,b
    assert raw_before==[s.digest for s in ss]
    valid=np.isfinite(maps['NMR_PLUS_FUSION'])
    for i,s in enumerate(ss):
        m=valid&(winning['WINNER_SWEEP']==i);r=winning['WINNER_RAY'][m];g=winning['WINNER_GATE'][m]
        assert (final_masks[i][r,g]==1).all()
        assert np.array_equal(maps['NMR_PLUS_FUSION'][m],s.fields['DBZH'][r,g])
    region=(distance>=2000)&(distance<=75000);strong=region&(maps['NMR_CORE']>=30)
    label=root.name.replace('qc-case-bjt','')+'_'+case['radar_id'];result={
        'label':label,'input_sha256':actual,'baseline':'isolated NMR core strict8, no operational QC/weather context',
        'config':c.model_dump(mode='json'),'config_sha256':c.digest,'background_status':'NO_ASSET_BOUND',
        'sweeps':records,'reflectivity_sweeps':int(sum(s.observed.any() for s in ss)),
        'incremental_cr':change(maps['NMR_CORE'],maps['NMR_PLUS_FUSION'],region),'raw_unchanged':True,
        'strong_control_unchanged':bool(np.array_equal(maps['NMR_CORE'][strong],maps['NMR_PLUS_FUSION'][strong])),
        'winner_reconstruction_passed':True,'elapsed_s':time.perf_counter()-started,
        'peak_rss_mb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,'not_a_truth_detection_rate':True,'operational_eligible':False}
    (output/(label+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    (output/(label+'.npz')).write_bytes(npz_bytes({**maps,**winning,'grid':grid}))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--portable-reader',action='store_true');a=p.parse_args()
    if a.output.exists():raise FileExistsError('use a new output directory')
    a.output.mkdir(parents=True);records=[]
    for case in json.loads((a.case/'review-manifest.json').read_text())['cases']:
        record=evaluate_case(a.case,case,a.output,a.portable_reader);records.append(record)
        print(record['label'],record['incremental_cr']['-10'],flush=True);gc.collect()
    (a.output/'summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
