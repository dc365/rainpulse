from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import numpy as np
import pytest
from fusion_helpers import scene,cfg,baseline
from volume_review.data import Sweep
from volume_review.episode_background.data import Sample
from volume_review.episode_background.builder import build_episode
from volume_review.episode_background.config import BuildConfig,EpisodeConfig
from volume_review.episode_background.io import background_bytes,load_background
from volume_review.clutter_fusion.background import compare
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.disposition import apply,CR
T0=datetime(2026,9,18,tzinfo=timezone.utc)


def sample(i,rho=.7,zdr=.2):
    s=scene(kind='smooth',z=5.,rho=rho,zdr=zdr,snr=12.)
    return Sample('site_a',f'scan-{i}',s.name,'processor-v1',(T0+timedelta(minutes=6*i)).isoformat(),
        hashlib.sha256(f'scan{i}'.encode()).hexdigest(),s.azimuth,s.elevation,s.ranges,s.fields,s.available,s.good)


def build(rho=.7,zdr=.2):
    ss=[sample(i,rho,zdr) for i in range(20)]
    return build_episode(ss,BuildConfig(),reviewed_no_precipitation=True,review_receipt='a'*64)


def sweep(s):
    gap=np.zeros(len(s.azimuth),bool);gap[-1]=True
    return Sweep(s.sweep_id,s.azimuth,s.elevation,s.ranges,s.fields,s.available,s.geometry_good,gap,np.full(len(s.azimuth),0.))


def test_background_actually_enables_missing_zdr_path_and_single_disposition(tmp_path):
    model=build();s=sample(30);s=replace(s,fields={k:v for k,v in s.fields.items() if k!='ZDR'},
                                     available={k:v for k,v in s.available.items() if k!='ZDR'})
    c=cfg(mode='cr_withhold');raw=sweep(s)
    without=evaluate_volume([raw],c)[0].arrays;assert not without['CF_NONMET_SUPPORTED_MASK'].any()
    bg,receipt=compare(s,model,c);assert bg['CF_BG_CURRENT_NONMET_MASK'].any()
    e=evaluate_volume([raw],c,backgrounds=[(bg,receipt)])[0].arrays
    assert e['CF_NONMET_SUPPORTED_MASK'].any()
    out,delta=apply(baseline(raw),e,c,low_quality_flag=1024)
    assert delta['cr_loss_gates']>0 and delta['qpe_loss_gates']==0
    assert not any(k.startswith('EBG_') for k in out)
    data=background_bytes(model);path=tmp_path/'bg.npz';path.write_bytes(data)
    recovered=load_background(path,hashlib.sha256(data).hexdigest())
    bg2,_=compare(s,recovered,c)
    for k in bg:np.testing.assert_equal(bg[k],bg2[k])


def test_background_match_alone_never_deletes_weather():
    model=build(rho=.99);s=sample(30,rho=.99);raw=sweep(s);c=cfg(local_conflict_policy='cr_withhold')
    bg,receipt=compare(s,model,c);assert bg['CF_BG_MATCH_MASK'].any()
    a=evaluate_volume([raw],c,backgrounds=[(bg,receipt)])[0].arrays
    assert not a['CF_NONMET_SUPPORTED_MASK'].any() and not a['CF_MIXED_ACTION_MASK'].any()


@pytest.mark.parametrize('fault',['future','expired','station','processing','overlap'])
def test_bad_background_not_admitted(fault):
    model=build();s=sample(30)
    if fault=='future':s=replace(s,observed_at=(T0-timedelta(days=1)).isoformat())
    if fault=='expired':s=replace(s,observed_at=(T0+timedelta(days=5)).isoformat())
    if fault=='station':s=replace(s,radar_id='other')
    if fault=='processing':s=replace(s,processing_id='other')
    if fault=='overlap':s=sample(5)
    if fault=='overlap':
        with pytest.raises(ValueError,match='overlap'):compare(s,model,cfg())
    else:
        bg,_=compare(s,model,cfg());assert not bg['CF_BG_CURRENT_NONMET_MASK'].any()


def test_observed_tail_is_categorical_and_conflict_not_missing():
    model=build(zdr=8.);s=sample(30,zdr=8.)
    bg,_=compare(s,model,cfg());assert bg['CF_BG_TAIL_MATCH_MASK'].any()
    assert bg['CF_BG_CURRENT_NONMET_MASK'].any()
    bad=sample(30,zdr=-8.);bg,_=compare(bad,model,cfg());assert not bg['CF_BG_CURRENT_NONMET_MASK'].any()


def test_enhancement_protected_not_dBZ_background_subtraction():
    model=build();s=sample(30);f=dict(s.fields);f['DBZH']=f['DBZH']+5;s=replace(s,fields=f)
    bg,_=compare(s,model,cfg());assert bg['CF_BG_ENHANCEMENT_MASK'].any()
    assert not bg['CF_BG_CURRENT_NONMET_MASK'].any()
    np.testing.assert_equal(s.fields['DBZH'],10.)


def test_background_cannot_enable_second_action_chain():
    with pytest.raises(ValueError,match='evidence only'):
        cfg(background=EpisodeConfig(mode='experiment_cr'))
