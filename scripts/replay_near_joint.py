#!/usr/bin/env python3
"""Frozen raw replay of NR current/causal path against isolated NMR+CF.

NOT live parent QC: no background/DEM assets, previous NP/RDR protections or
full Worker are present in sanitized qc-case inputs. Existing near_clutter is
also measured using an explicit CF texture proxy, as a separate diagnostic only;
its counts are NOT composed into the reported incremental CR result. Real
Py-ART/Gabella evidence and NP disposition must be replayed in the full Worker.
"""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace as NS
import argparse,gc,json,sys,time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from replay_clutter_fusion import load,parent_fields,change
from receiver_domain_case_io import path_under,content_digest
from volume_review.clutter_fusion.config import ClutterFusionConfig as C
from volume_review.clutter_fusion.near_revision_config import NearRevisionConfig as N
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.disposition import apply,CR
from volume_review.clutter_fusion.classifier import protections
from volume_review.clutter_fusion.context import sample_ground
from volume_review.clutter_fusion.near_runtime import RuntimeContext
from volume_review.clutter_fusion.causal_temporal import PastSweep
from volume_review.near_measurement.config import NearMeasurementConfig
from volume_review.near_measurement.core import evaluate as near_evaluate
from volume_review.receipts import npz_bytes
from review_extension.near_clutter import candidates
from review_extension.config import NonPrecipConfig
from review_extension.near_reliability import NearReliabilityConfig


def evaluate_case(root,case,output,portable=False,past_info=None):
    path=path_under(root,case['normalized_zarr']);actual=content_digest(path)
    if actual!=case['input_sha256']:raise ValueError('raw artifact hash mismatch')
    attrs=json.loads((path/'.zattrs').read_text());start=time.perf_counter()
    ss=[load(p,portable) for p in sorted(path.glob('sweep_[0-9][0-9][0-9]'))];raw=[s.digest for s in ss]
    c=C(mode='cr_withhold',depolarization_backend='numpy_reference',near_revision=N(mode='cr_withhold'))
    nc=NearMeasurementConfig(mode='experiment',depolarization_backend='numpy_reference',uncertainty_snr_db=8.)
    inputs=[];ps=[]
    for s in ss:
        f={k+'_RAW':v for k,v in s.fields.items()};f['VALID_MASK']=s.observed.astype('uint8')
        nr=near_evaluate(f,s.azimuth,s.ranges,nc,available=s.available,geometry_good=s.good,gap_after=s.gap_after).arrays
        small={k:nr[k] for k in ('NMR_NONMET_CANDIDATE_MASK','NMR_LOW_SNR_UNCERTAIN_MASK','NMR_WEATHER_PROXY_MASK','NMR_PREVIOUS_WEATHER_MASK')}
        inputs.append(small);ps.append(protections(parent_fields(s,small),.7))
    context=None;temporal_status='NO_PAST_REQUESTED'
    if past_info is not None:
        pr,pc=past_info;ppath=path_under(pr,pc['normalized_zarr']);pa=json.loads((ppath/'.zattrs').read_text())
        if not attrs.get('radar_config_version') or not pa.get('radar_config_version'):
            temporal_status='PROCESSING_ID_REDACTED_ABSTAINED'
        else:
            ph=content_digest(ppath)
            if ph!=pc['input_sha256']:raise ValueError('past input digest mismatch')
            past=[]
            for x in sorted(ppath.glob('sweep_[0-9][0-9][0-9]')):
                s=load(x,portable);past.append(PastSweep(s,ph,pa['scan_id'],pa['radar_id'],pa['radar_config_version']))
            context=RuntimeContext(attrs['radar_id'],attrs['scan_id'],attrs['radar_config_version'],tuple(past))
            temporal_status='REQUESTED_VERIFIED_IDENTITIES'
    evs=evaluate_volume(ss,c,protections=ps,near_context=context)
    g=np.arange(-75000.,75001.,1000.);x,y=np.meshgrid(g,g[::-1]);r=np.hypot(x,y);az=np.rad2deg(np.arctan2(x,y))%360
    maps={k:np.full(x.shape,np.nan,'float32') for k in ('PARENT_CORE','NR_CURRENT_CAUSAL')}
    wins={k:np.full(x.shape,-1,'int32') for k in ('SWEEP','RAY','GATE')};masks=[];records=[]
    nearcfg=NonPrecipConfig(near_enabled=True,near_reliability=NearReliabilityConfig())
    tuned=nearcfg.model_copy(update={'near_maximum_rhohv':.85,'near_minimum_coverage':.7,'near_minimum_fraction':.6})
    for i,(s,e,n) in enumerate(zip(ss,evs,inputs,strict=True)):
        b=parent_fields(s,n)
        parent,_=apply(b,e.arrays,c.model_copy(update={'near_revision':c.near_revision.model_copy(update={'mode':'audit'})}),low_quality_flag=1024)
        after,delta=apply(b,e.arrays,c,low_quality_flag=1024)
        if np.any((after[CR]==1)&(parent[CR]==0)):raise AssertionError('old CR exclusion revived')
        if not np.array_equal(after['QPE_ELIGIBLE_MASK'],parent['QPE_ELIGIBLE_MASK']):raise AssertionError('NR touched QPE')
        masks.append(after[CR].copy())
        native=NS(shape=s.shape,ranges=s.ranges,azimuth=s.azimuth,geometry_good=s.good,gap_after=s.gap_after,
                  fields=s.fields,field_available=s.available)
        # This is intentionally NOT the production OS_GABELLA/Py-ART mask.
        texture=np.isfinite(e.arrays['CF_Z_TEXTURE_DB'])&(e.arrays['CF_Z_TEXTURE_DB']>=4.6)
        oldcandidate=candidates(native,nearcfg,texture)[0];tunecandidate=candidates(native,tuned,texture)[0]
        records.append({'sweep':s.name,'elevation_deg':float(np.median(s.elevation)),
          'near_revision_delta':delta,'near_revision_evidence':e.summary.get('near_revision'),
          'legacy_near_CF_texture_proxy_candidates':int(oldcandidate.sum()),'tuned_proxy_candidates':int(tunecandidate.sum()),
          'legacy_near_proxy_not_production_texture':True})
        rr,gg,foot,_=sample_ground(s,az,r);z=s.fields['DBZH'][rr,gg]
        for name,arr in [('PARENT_CORE',parent),('NR_CURRENT_CAUSAL',after)]:
            valid=foot&s.observed[rr,gg]&(arr[CR][rr,gg]==1)
            val=np.where(valid,z,np.nan)
            if name=='NR_CURRENT_CAUSAL':
                win=valid&(~np.isfinite(maps[name])|(z>maps[name]));wins['SWEEP'][win]=i;wins['RAY'][win]=rr[win];wins['GATE'][win]=gg[win]
            maps[name]=np.fmax(maps[name],val)
        del parent,after,b
    assert raw==[s.digest for s in ss]
    valid=np.isfinite(maps['NR_CURRENT_CAUSAL']);strong=(maps['PARENT_CORE']>=30)
    for i,s in enumerate(ss):
        m=valid&(wins['SWEEP']==i);rr,gg=wins['RAY'][m],wins['GATE'][m]
        assert (masks[i][rr,gg]==1).all()
        assert np.array_equal(maps['NR_CURRENT_CAUSAL'][m],s.fields['DBZH'][rr,gg])
    assert np.array_equal(maps['PARENT_CORE'][strong],maps['NR_CURRENT_CAUSAL'][strong])
    label=root.name.replace('qc-case-bjt','').replace('bjt','')+'_'+case['radar_id']
    report={'label':label,'input_sha256':actual,'raw_unchanged':True,'winner_reconstruction_passed':True,
       'strong_control_unchanged':True,'reflectivity_sweeps':sum(bool(s.observed.any()) for s in ss),
       'total_sweeps':len(ss),'config_sha256':c.digest,'config':c.model_dump(mode='json'),'sweeps':records,
       'incremental_cr':change(maps['PARENT_CORE'],maps['NR_CURRENT_CAUSAL'],(r>=2000)&(r<=75000)),
       'temporal_status':temporal_status,'background_status':'ASSET_NOT_PROVIDED','DEM_status':'ASSET_NOT_PROVIDED',
       'baseline':'isolated NMR strict8 plus CF current-volume core, not complete operational QC',
       'not_a_truth_detection_rate':True,'new_qpe_exclusion':0,'elapsed_s':time.perf_counter()-start,'operational_eligible':False}
    (output/(label+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    (output/(label+'.npz')).write_bytes(npz_bytes({**maps,**wins,'grid':g}))
    return {k:v for k,v in report.items() if k not in ('config','sweeps')}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',type=Path,required=True)
    p.add_argument('--past-case',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--portable-reader',action='store_true')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);out=[]
    past={c['radar_id']:(a.past_case,c) for c in json.loads((a.past_case/'review-manifest.json').read_text())['cases']} if a.past_case else {}
    for c in json.loads((a.case/'review-manifest.json').read_text())['cases']:
        r=evaluate_case(a.case,c,a.output,a.portable_reader,past.get(c['radar_id']));out.append(r)
        print(r['label'],r['incremental_cr']['0'],flush=True);gc.collect()
    (a.output/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
