"""Read-only post-QC replay, not a full Worker rerun or original four-site CR.

Requires recovered bound snapshots (manifest.json + sweep_*.npz per case) and
cases.jsonl. Exact embedded manifest and numeric digests are verified. Missing
per-moment source masks are explicitly reported; finite/bounded fallback is used.
"""
import argparse
from pathlib import Path
import hashlib
import json
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"algorithms/rainpulse_algo/radar/qc_engine"))
from volume_review.near_measurement.config import NearMeasurementConfig as C
from volume_review.near_measurement.core import evaluate
from volume_review.near_measurement.disposition import apply,CR
from volume_review.data import array_digest
from volume_review.sampling import polar_targets
E=6371000.*4/3


def settings(backend):
    common=dict(mode="experiment",depolarization_backend=backend)
    return {"audit":C(depolarization_backend=backend),
            "nonmet_cr":C(**common,uncertainty_policy="diagnostic_only"),
            **{f"strict_snr{int(t)}":C(**common,uncertainty_snr_db=t) for t in (3.,6.,8.,10.)},
            "quarantine_snr8":C(**common,nonmet_policy="quarantine")}


def metrics(before,after,domain):
    changed=domain&np.isfinite(before)&(~np.isfinite(after)|(after!=before))
    out={"changed":int(changed.sum()),"unavailable":int((changed&~np.isfinite(after)).sum()),
         "lower_remaining_measurement":int((changed&np.isfinite(after)).sum())}
    for t in (-10,0,10,20,30,35):
        m=domain&np.isfinite(before)&(before>=t)&((before<30) if t<30 else True)
        lost=m&(~np.isfinite(after)|(after<t))
        out[f"threshold_{t}"]={"baseline":int(m.sum()),"lost":int(lost.sum()),"fraction":float(lost.sum()/max(1,m.sum()))}
    return out


def run(root,output,backend="numpy_reference",save_arrays=False):
    root,output=Path(root),Path(output)
    if output.exists():raise ValueError("new output directory required")
    output.mkdir(parents=True)
    cases=[json.loads(x) for x in (root/'cases.jsonl').read_text().splitlines() if x.strip()]
    configs=settings(backend);x=(np.arange(600)+.5)*500.-150000
    xx,yy=np.meshgrid(x,x[::-1]);ground=np.hypot(xx,yy);angle=np.rad2deg(np.arctan2(xx,yy))%360
    near=(ground>=2000)&(ground<=75000);outside=(ground>=76000)&(ground<=140000)
    results=[]
    for c in cases:
        start=time.monotonic();directory=root/c['case_id'];manifest=json.loads((directory/'manifest.json').read_text())
        products={v:np.full(ground.shape,np.nan,'float32') for v in ('baseline',*configs)}
        winners={v:{k:np.full(ground.shape,-1,'int32') for k in ('sweep','ray','gate')} for v in products}
        totals={v:{k:0 for k in ('nonmet_candidate_gates','uncertainty_candidate_gates','cr_loss_gates',
                                'qpe_loss_gates','cr_nonmet_loss_gates','cr_uncertainty_only_loss_gates')} for v in configs}
        records=[];cache=[]
        for rec in manifest['sweeps']:
            payload=(directory/(rec['sweep']+'.npz')).read_bytes()
            if hashlib.sha256(payload).hexdigest()!=rec['sha256']:raise ValueError('snapshot SHA mismatch')
            with np.load(directory/(rec['sweep']+'.npz'),allow_pickle=False) as z:a={k:z[k] for k in z.files}
            before=array_digest(a)
            if before!=rec['numeric_sha256']:raise ValueError('numeric SHA mismatch')
            az,r,el=a['azimuth'].astype(float),a['range'].astype(float),a['elevation'].astype(float)
            f={k:v for k,v in a.items() if v.shape==a['DBZH_RAW'].shape}
            # These LIGHT snapshots lack the two fields. Only explicit-contract
            # reconstructions are used, and these are identified in the report.
            f.setdefault('DBZH_USABLE',np.where(f['QPE_ELIGIBLE_MASK']==1,f['DBZH_QC'],np.nan).astype('float32'))
            f.setdefault('LOW_QUALITY_MASK',((f['VALID_MASK']==1)&(f['QUALITY_INDEX']<.5)).astype('uint8'))
            masks={'baseline':f[CR]==1};deltas={}
            for v,cfg in configs.items():
                evidence=evaluate(f,az,r,cfg)
                p,delta=apply(f,evidence.arrays,cfg,low_quality_flag=manifest['flag_definitions']['LOW_QUALITY'])
                masks[v]=p[CR]==1;deltas[v]=delta
                for k in totals[v]:totals[v][k]+=delta[k]
                if v=='audit':
                    for k in f:assert np.array_equal(f[k],p[k],equal_nan=True),k
                if v!='quarantine_snr8':assert np.array_equal(f['QPE_ELIGIBLE_MASK'],p['QPE_ELIGIBLE_MASK'])
                assert not np.any(masks[v]&~masks['baseline'])
                assert np.array_equal(f['DBZH_RAW'],p['DBZH_RAW'],equal_nan=True)
            number=int(rec['sweep'][-3:]);row,_,_=polar_targets(az,r,ground,angle)
            arc=ground/E;den=np.cos(np.deg2rad(el[row])+arc);slant=E*np.sin(arc)/np.maximum(den,1e-6)
            row,gate,ok=polar_targets(az,r,slant,angle);ok&=den>0;values=f['DBZH_QC'][row,gate]
            for v in products:
                valid=ok&masks[v][row,gate]&(f['VALID_MASK'][row,gate]==1)&np.isfinite(values)
                win=valid&(~np.isfinite(products[v])|(values>products[v]));products[v][win]=values[win]
                winners[v]['sweep'][win]=number;winners[v]['ray'][win]=row[win];winners[v]['gate'][win]=gate[win]
            cache.append((number,f['DBZH_QC'],masks));assert before==array_digest(a)
            records.append({'sweep':rec['sweep'],'elevation_deg':float(np.median(el)),
                            'original_snapshot_sha256':rec['sha256'],'deltas':deltas})
        base=products['baseline']
        for v,z in products.items():
            assert np.array_equal(np.isfinite(z),winners[v]['sweep']>=0)
            for number,raw,masks in cache:
                m=winners[v]['sweep']==number;rr=winners[v]['ray'][m];gg=winners[v]['gate'][m]
                assert np.array_equal(z[m],raw[rr,gg]);assert masks[v][rr,gg].all()
            assert not np.any(np.isfinite(z)&(~np.isfinite(base)|(z>base)))
        result={'case_id':c['case_id'],'site':c['site'],'analysis_time_utc':c['analysis_time_utc'],
                'stored_pipeline_version':c['qc_pipeline_version'],'snapshot_count':len(records),
                'scope':'single_site_relative_all_sweeps_CR_not_four_site_product',
                'availability':'finite_physically_bounded_snapshot_fallback_not_original_moment_masks',
                'projection_notes':['DBZH_USABLE reconstructed using explicit QPE contract',
                    'LOW_QUALITY_MASK reconstructed using frozen QI<0.5; no full QC validation claimed'],
                'backend':backend,'experiments':{},'sweeps':records,
                'checks':{'snapshot_hashes':True,'raw_unchanged':True,'no_ineligible_revived':True,
                          'all_winners_reconstruct':True,'audit_unchanged':True,'max_never_increased':True}}
        for v,z in products.items():
            if v=='baseline':continue
            changed=np.isfinite(base)&(~np.isfinite(z)|(base!=z))
            result['experiments'][v]={**totals[v],**metrics(base,z,near),'strong35_changed':int((changed&(base>=35)).sum()),
                                     'outside76km_changed':int((changed&outside).sum())}
        result['elapsed_seconds']=round(time.monotonic()-start,3);results.append(result)
        if save_arrays:np.savez_compressed(output/(c['case_id']+'_local_CR.npz'),x_m=x,**products,
                              **{v+'_'+k:a for v,d in winners.items() for k,a in d.items()})
        (output/'replay-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False))
        print(c['case_id'],result['elapsed_seconds'],{k:round(v['threshold_-10']['fraction']*100,2) for k,v in result['experiments'].items()},flush=True)
    (output/'configurations.json').write_text(json.dumps({k:v.model_dump(mode='json') for k,v in configs.items()},indent=2))
    return results

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--snapshots',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--backend',choices=('wradlib','numpy_reference'),default='numpy_reference')
    p.add_argument('--save-arrays',action='store_true');a=p.parse_args();run(a.snapshots,a.output,a.backend,a.save_arrays)
