#!/usr/bin/env python3
"""Synthetic end-to-end episode-to-fusion demo. NOT a deployable radar asset."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import json
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.data import Sweep
from volume_review.episode_background.data import Sample, sha, json_bytes
from volume_review.episode_background.config import BuildConfig
from volume_review.episode_background.builder import build_episode
from volume_review.episode_background.cli import cross_validate, _folder_transaction
from volume_review.episode_background.io import background_bytes, load_background
from volume_review.clutter_fusion.background import compare
from volume_review.clutter_fusion.config import ClutterFusionConfig
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.disposition import apply, CR


def main(output):
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    az=np.arange(12,dtype=float);ranges=2000.+np.arange(100)*250.
    shape=(len(az),len(ranges));good=np.ones(len(az),bool)
    def sample(scan,layer,target=False):
        f={k:np.full(shape,v,'float32') for k,v in {'DBZH':5.,'SNR':12.,'RHOHV':.7,'ZDR':.2,'PHIDP':2.}.items()}
        if target:
            del f['ZDR']
            f['RHOHV'][3:6,30:60]=.995  # Weak weather control: not a background-like low-rho target.
            f['DBZH'][7:9,40:60]=40.   # Strong weather control.
        stamp=start+timedelta(minutes=6*scan)
        return Sample('synthetic_station',f'synthetic-{scan}',f'sweep_{layer:03d}','synthetic-config',
            stamp.isoformat(),hashlib.sha256(f'{scan}-{layer}-raw'.encode()).hexdigest(),
            az,np.full(len(az),.5+layer),ranges,f,{k:np.isfinite(v) for k,v in f.items()},good)
    train=[sample(i,j) for i in range(20) for j in (0,1)]
    cfg=BuildConfig();model=build_episode(train,cfg,reviewed_no_precipitation=True,review_receipt='a'*64)
    manifest={'reviewed_no_precipitation':True,'review_receipt':'a'*64}
    folds=cross_validate(manifest,train,cfg)
    targets=[sample(30,j,True) for j in (0,1)]
    c=ClutterFusionConfig(mode='cr_withhold',depolarization_backend='numpy_reference')
    def write(folder):
        payload=background_bytes(model);asset=folder/'SYNTHETIC_ONLY_background.npz';asset.write_bytes(payload)
        checked=load_background(asset,sha(payload));ss=[];bgs=[]
        for t in targets:
            gaps=np.zeros(len(az),bool);gaps[-1]=True
            ss.append(Sweep(t.sweep_id,t.azimuth,t.elevation,t.ranges,t.fields,t.available,t.geometry_good,gaps,
                            np.full(len(az),datetime.fromisoformat(t.observed_at).timestamp())))
            bgs.append(compare(t,checked,c))
        without=evaluate_volume(ss,c);with_bg=evaluate_volume(ss,c,backgrounds=bgs);rows=[]
        for s,before,ev in zip(ss,without,with_bg,strict=True):
            obs=s.observed;z=s.fields['DBZH'];a={k:obs.astype('uint8') for k in (CR,'VALID_MASK','REFLECTIVITY_TRUST_MASK','QPE_ELIGIBLE_MASK')}
            a.update(DBZH_RAW=z.copy(),DBZH_QC=z.copy(),DBZH_USABLE=z.copy(),QC_ACTION=np.zeros(shape,'uint8'),
                QC_FLAGS=np.zeros(shape,'uint32'),QUALITY_INDEX=np.full(shape,.8,'float32'),
                LOW_QUALITY_MASK=np.zeros(shape,'uint8'),CR_UNCERTAIN_MASK=np.zeros(shape,'uint8'),CR_QUALIFICATION_REASON=obs.astype('uint16'))
            after,delta=apply(a,ev.arrays,c,low_quality_flag=1024)
            assert not before.arrays['CF_NONMET_SUPPORTED_MASK'][2:4,10:20].any()
            assert ev.arrays['CF_NONMET_SUPPORTED_MASK'][2:4,10:20].all()
            assert ev.arrays['CF_NONMET_SUPPORTED_MASK'].sum() > before.arrays['CF_NONMET_SUPPORTED_MASK'].sum()
            assert delta['cr_loss_gates']>0 and delta['qpe_loss_gates']==0
            assert (after[CR][3:6,30:60]==1).all() and (after[CR][7:9,40:60]==1).all()
            np.testing.assert_equal(after['DBZH_RAW'],a['DBZH_RAW'])
            rows.append({'sweep':s.name,'without_background_candidates':int(before.arrays['CF_NONMET_SUPPORTED_MASK'].sum()),
                'with_background_candidates':int(ev.arrays['CF_NONMET_SUPPORTED_MASK'].sum()),'disposition':delta,
                'weak_weather_control_gates_preserved':90,'strong_weather_control_gates_preserved':40})
        report={'schema':'rainpulse.synthetic-clutter-fusion-demo-v1','synthetic':True,'deployable_asset':False,
                'training_scans':20,'training_sweeps':40,'time_blocks':len(folds['folds']),
                'background_sha256':sha(payload),'sweeps':rows,'scores_are_probabilities':False,
                'real_data_detection_rate':None,'operational_eligible':False}
        (folder/'report.json').write_bytes(json_bytes(report));(folder/'time-block-cv.json').write_bytes(json_bytes(folds))
    _folder_transaction(output,write)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    main(p.parse_args().output)
