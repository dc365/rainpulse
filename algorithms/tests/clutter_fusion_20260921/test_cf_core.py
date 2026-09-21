from dataclasses import replace
import numpy as np
import pytest
from fusion_helpers import scene,cfg,baseline
from volume_review.data import Sweep
from volume_review.clutter_fusion.features import texture,extract
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.disposition import apply,CR
from volume_review.clutter_fusion.classifier import decide,EchoClass,protections


def ev(s,c=None,**kw):return evaluate_volume([s],c or cfg(),**kw)[0].arrays


def test_bio_pattern_no_texture_requirement_but_not_quantitative_on_one_family():
    s=scene();a=ev(s)
    assert a['CF_BIO_PATTERN_MASK'].sum()>100
    assert np.nanmax(a['CF_Z_TEXTURE_DB'])<1e-6
    assert a['CF_NONMET_SUPPORTED_MASK'].any()
    assert not a['CF_QUARANTINE_SUPPORTED_MASK'].any()
    out,d=apply(baseline(s),a,cfg(mode='quarantine'),low_quality_flag=1024)
    assert d['cr_loss_gates']>100 and d['qpe_loss_gates']==0
    np.testing.assert_equal(out['DBZH_RAW'],s.fields['DBZH'])


def test_ground_requires_joint_measured_features():
    a=ev(scene(kind='ground',zdr=.2))
    assert a['CF_GROUND_PATTERN_MASK'].any()
    assert (a['CF_FAMILY_COUNT'][a['CF_QUARANTINE_SUPPORTED_MASK']==1]>=2).all()


@pytest.mark.parametrize('kw',[
    {'kind':'smooth','rho':.99,'zdr':.2},
    {'kind':'smooth','rho':.7,'zdr':.2},
    {'snr':2.}, {'z':35.}, {'z':-20.},
    {'missing':('RHOHV',)}, {'missing':('PHIDP',)}, {'missing':('ZDR',)},
    {'zdr':8.}, {'zdr':-8.}, {'missing':('SNR',)}])
def test_single_features_low_signal_tail_no_rain_not_deleted(kw):
    a=ev(scene(**kw));assert not a['CF_NONMET_SUPPORTED_MASK'].any()


def test_zero_velocity_weather_preserved():
    s=scene(kind='smooth',rho=.99,zdr=.2)
    f=dict(s.fields);f['VR']=np.zeros(s.shape,'float32');f['SW']=np.full(s.shape,.1,'float32')
    s=replace(s,fields=f,available={k:np.isfinite(v) for k,v in f.items()})
    a=ev(s);assert a['CF_WEATHER_PROXY_MASK'].any();assert not a['CF_NONMET_SUPPORTED_MASK'].any()


def test_phase_wrap_and_steady_gradient_not_noise():
    c=cfg();v=np.tile([359.,1.],(5,20));t,_=texture(v,np.ones(v.shape,bool),(3,5),c,circular=True)
    assert np.nanmedian(t)<1.1
    assert np.std(v)>170
    s=scene(kind='smooth',rho=.99,zdr=.2);f=dict(s.fields)
    f['PHIDP']=np.broadcast_to((np.arange(s.shape[1])*8.)%360,s.shape).astype('float32')
    s=replace(s,fields=f);a=ev(s)
    assert np.nanmax(a['CF_PHI_JITTER_DEG'])<.01
    assert not a['CF_NONMET_SUPPORTED_MASK'].any()


@pytest.mark.parametrize('protect',['hard','local','legacy'])
def test_explicit_protections(protect):
    s=scene(kind='ground',zdr=.2);p=[np.zeros(s.shape,bool) for _ in range(3)]
    p[['hard','local','legacy'].index(protect)][:]=True
    a=ev(s,protections=[tuple(p)]);assert not a['CF_NONMET_SUPPORTED_MASK'].any()
    assert not a['CF_MIXED_ACTION_MASK'].any()
    a=ev(s,cfg(local_conflict_policy='cr_withhold'),protections=[tuple(p)])
    assert bool(a['CF_MIXED_ACTION_MASK'].any())==(protect=='local')
    assert not a['CF_QUARANTINE_SUPPORTED_MASK'].any()


@pytest.mark.parametrize('fault',['missing','gap','bad_ray'])
def test_no_observation_created_or_gap_crossed(fault):
    s=scene();f={k:v.copy() for k,v in s.fields.items()};av={k:v.copy() for k,v in s.available.items()}
    if fault=='missing':
        f['DBZH'][4:7,20:70]=np.nan;av['DBZH'][4:7,20:70]=False;s=replace(s,fields=f,available=av)
    elif fault=='gap':
        gaps=s.gap_after.copy();gaps[5]=True;s=replace(s,gap_after=gaps)
    else:
        good=s.good.copy();good[5]=False;s=replace(s,good=good)
    before=s.digest;a=ev(s);assert before==s.digest
    if fault=='missing':assert not a['CF_NONMET_SUPPORTED_MASK'][4:7,20:70].any()
    else:assert not a['CF_NONMET_SUPPORTED_MASK'][4:7].any()


@pytest.mark.parametrize('mode',['audit','cr_withhold','quarantine'])
def test_disposition_audit_and_old_exclusions(mode):
    s=scene(kind='ground',zdr=.2);c=cfg(mode=mode);a=ev(s,c);b=baseline(s)
    b[CR][4,:]=0;b['QPE_ELIGIBLE_MASK'][4,:]=0
    out,d=apply(b,a,c,low_quality_flag=1024)
    assert not out[CR][4].any() and not out['QPE_ELIGIBLE_MASK'][4].any()
    for k in ('DBZH_RAW','DBZH_QC','VALID_MASK'):np.testing.assert_equal(out[k],b[k])
    if mode=='audit':
        for k in b:np.testing.assert_equal(out[k],b[k])
    elif mode=='cr_withhold':
        assert d['cr_loss_gates']>0 and d['qpe_loss_gates']==0
        for k in b:
            if k not in (CR,'CR_UNCERTAIN_MASK','CR_QUALIFICATION_REASON'):np.testing.assert_equal(out[k],b[k])
    else:assert d['qpe_loss_gates']>0


@pytest.mark.parametrize('limit',['maximum_volume_gates','maximum_sweep_gates','maximum_context_pairs'])
def test_resource_limit_returns_zero_whole_volume(limit):
    ss=[scene(),scene(name='sweep_001')];c=cfg(**{limit:1})
    out=evaluate_volume(ss,c)
    assert len(out)==2 and all(e.summary['status']=='RESOURCE_LIMIT_ABSTAINED' for e in out)
    assert all(not e.arrays['CF_NONMET_SUPPORTED_MASK'].any() for e in out)


def test_missing_evidence_does_not_renormalize_score():
    s=scene(kind='ground',zdr=.2);a=ev(s)
    before=a['CF_GROUND_SCORE'].copy()
    a['CF_DOPPLER_ACTION_AVAILABLE_MASK'][:]=0
    new=decide(a,cfg())
    assert np.all(new['CF_GROUND_SCORE']<=before+1e-6)
    assert not new['CF_GROUND_PATTERN_MASK'].any()


def test_np_unattributed_protection_not_peeled():
    s=scene();b=baseline(s);b['NP_WEATHER_PROTECTED_MASK']=np.ones(s.shape,'uint8')
    b['VOR_REASON']=np.full(s.shape,8,'uint16')
    h,l,p=protections(b,.7);assert p.all() and l.all() and not h.any()


def test_circular_rotation_and_array_roll_equivalence():
    s=scene();before=ev(s);shift=5
    rolled=replace(s,azimuth=np.roll(s.azimuth,shift),elevation=np.roll(s.elevation,shift),
        fields={k:np.roll(v,shift,axis=0) for k,v in s.fields.items()},
        available={k:np.roll(v,shift,axis=0) for k,v in s.available.items()},
        good=np.roll(s.good,shift),gap_after=np.roll(s.gap_after,shift),ray_time_s=np.roll(s.ray_time_s,shift))
    after=ev(rolled)
    np.testing.assert_equal(after['CF_NONMET_SUPPORTED_MASK'],np.roll(before['CF_NONMET_SUPPORTED_MASK'],shift,axis=0))
    rot=replace(s,azimuth=(s.azimuth+175)%360);after=ev(rot)
    np.testing.assert_equal(after['CF_NONMET_SUPPORTED_MASK'],before['CF_NONMET_SUPPORTED_MASK'])
