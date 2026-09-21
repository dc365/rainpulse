from dataclasses import replace
import numpy as np
import pytest
from fusion_helpers import scene,cfg
from volume_review.clutter_fusion.context import derive,ContextMetadata,sample_ground
from volume_review.clutter_fusion.engine import evaluate_volume


def doppler(s,velocity=.1):
    fields=dict(s.fields);fields['VR']=np.full(s.shape,velocity,'float32');fields['SW']=np.full(s.shape,.3,'float32')
    return replace(s,fields=fields,available={k:np.isfinite(v) for k,v in fields.items()})


def test_unknown_upper_is_never_negative_evidence():
    s=scene(kind='smooth');d=scene(name='sweep_001',el=2.)
    f=dict(d.fields);f['DBZH']=np.full(d.shape,np.nan,'float32');av=dict(d.available);av['DBZH']=np.zeros(d.shape,bool)
    d=replace(d,fields=f,available=av)
    out=derive(0,[s,d],cfg())
    assert not out['CF_UPPER_MEASURED_MASK'].any();assert np.isnan(out['CF_UPPER_DROP_DB']).all()
    a=evaluate_volume([s,d],cfg())[0].arrays
    assert not a['CF_NONMET_SUPPORTED_MASK'].any()


@pytest.mark.parametrize('policy',['diagnostic_only','verified_beam'])
def test_vertical_requires_beam_receipt_for_actions(policy):
    s=scene(kind='smooth',z=18.);d=scene(kind='smooth',name='sweep_001',el=2.5,z=0.)
    c=cfg(vertical_policy=policy);out=derive(0,[s,d],c)
    assert out['CF_UPPER_MEASURED_MASK'].any();assert not out['CF_UPPER_ACTION_AVAILABLE_MASK'].any()
    m=[ContextMetadata(beam_width_deg=.5,beam_source='test-measured-beam')]*2
    out=derive(0,[s,d],c,m)
    assert bool(out['CF_UPPER_ACTION_AVAILABLE_MASK'].any())==(policy=='verified_beam')


def test_shallow_weather_is_not_deleted_by_upper_missing_or_measured_drop():
    s=scene(kind='smooth',rho=.99,zdr=.2,z=12.)
    d=scene(kind='smooth',name='sweep_001',el=2.5,z=-5.,rho=.99,zdr=.2)
    m=[ContextMetadata(beam_width_deg=.5,beam_source='test')]*2
    out=evaluate_volume([s,d],cfg(vertical_policy='verified_beam'),metadata=m)
    assert not out[0].arrays['CF_NONMET_SUPPORTED_MASK'].any()


@pytest.mark.parametrize('verified',[False,True])
def test_doppler_pair_requires_separate_contract(verified):
    s=scene();d=doppler(scene(name='sweep_001',el=.6,time=30.))
    c=cfg(paired_doppler_policy='verified_pair')
    meta=[ContextMetadata(doppler_pair_verified=verified,waveform='wave1',nyquist_ms=25.,verification_id='test-contract')]*2
    out=derive(0,[s,d],c,meta)
    assert out['CF_DOPPLER_PAIRED_MASK'].any()
    assert bool(out['CF_DOPPLER_ACTION_AVAILABLE_MASK'].any())==verified


@pytest.mark.parametrize('fault',['stale','height','unknown_time','waveform','nyquist'])
def test_bad_donor_not_used(fault):
    s=scene();d=doppler(scene(name='sweep_001',el=.5,time=20.))
    meta=[ContextMetadata(doppler_pair_verified=True,waveform='wave1',nyquist_ms=25.,verification_id='test')]*2
    if fault=='stale':d=replace(d,ray_time_s=np.full(d.shape[0],1000.))
    if fault=='height':d=replace(d,elevation=np.full(d.shape[0],10.))
    if fault=='unknown_time':d=replace(d,ray_time_s=None)
    if fault=='waveform':meta[1]=replace(meta[1],waveform='wave2')
    if fault=='nyquist':meta[1]=replace(meta[1],nyquist_ms=30.)
    out=derive(0,[s,d],cfg(paired_doppler_policy='verified_pair'),meta)
    assert not out['CF_DOPPLER_ACTION_AVAILABLE_MASK'].any()


def test_native_doppler_priority():
    s=doppler(scene(),velocity=5.);d=doppler(scene(name='sweep_001'),velocity=0.)
    out=derive(0,[s,d],cfg())
    mask=out['CF_DOPPLER_ACTION_AVAILABLE_MASK']==1
    assert mask.any() and np.all(out['CF_DOPPLER_V_MS'][mask]==5.)
    assert not out['CF_DOPPLER_PAIRED_MASK'].any()


def test_no_ray_or_range_extrapolation():
    s=scene();r,g,ok,h=sample_ground(s,np.array([0.,180.,5.]),np.array([10000.,10000.,100000.]))
    assert list(ok)==[True,False,False]
