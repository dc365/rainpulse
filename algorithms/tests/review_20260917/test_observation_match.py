import numpy as np
from .conftest import Native,load
from .test_temporal_config import past

C=load('config').NonPrecipConfig
M=load('observation_match')


def pair():
    n=Native(z=np.full((12,80),20.),full=False)
    n.name='sweep_000';n.ray_time=np.datetime64('2026-09-18T00:00:00')+np.arange(12).astype('timedelta64[s]')
    d=n.clone();d.name='sweep_001';d.azimuth+=.1;d.ray_time+=np.timedelta64(30,'s')
    for k,v in [('VR',.5),('SW',.8)]:
        d.fields[k]=np.full(d.shape,v);d.field_available[k]=np.ones(d.shape,bool)
    return n,d,C(paired_doppler_enabled=True,temporal_spatial_matching=True)


def test_real_donor_fields_and_original_provenance():
    n,d,c=pair();out=M.paired_doppler(n,[d],c)
    assert out['NP_PAIRED_DOPPLER_AVAILABLE_MASK'].all()
    assert (out['NP_PAIRED_VR']==.5).all()
    assert (out['NP_PAIRED_AGE_SECONDS']==30).all()
    assert (out['NP_PAIRED_DONOR_SWEEP']==1).all()
    assert 'VR' not in n.fields


def test_stale_wrong_height_other_scan_and_missing_stay_unavailable():
    for change in ('time','elevation','scan','missing'):
        n,d,c=pair()
        if change=='time':d.ray_time+=np.timedelta64(100,'s')
        if change=='elevation':d.elevation+=1
        if change=='scan':d.attrs['scan_id']='other'
        if change=='missing':d.field_available['VR'][:]=False
        assert not M.paired_doppler(n,[d],c)['NP_PAIRED_DOPPLER_AVAILABLE_MASK'].any()


def test_native_valid_velocity_is_not_overwritten():
    n,d,c=pair()
    for k in ('VR','SW'):
        n.fields[k]=np.ones(n.shape)*5;n.field_available[k]=np.ones(n.shape,bool)
    assert not M.paired_doppler(n,[d],c)['NP_PAIRED_DOPPLER_AVAILABLE_MASK'].any()


def test_past_jittered_observation_can_support_but_missing_cannot():
    n,d,c=pair();d=past(n);d.azimuth+=.1
    d.fields['DBZH'][5,5]=np.nan;d.field_available['DBZH'][5,5]=False
    a,_=load('temporal').raw_recurrence(n,[d],c)
    assert a['NP_FIXED_SAMPLE_COUNT'][4,5]==1
    assert a['NP_FIXED_SAMPLE_COUNT'][5,5]==0


def test_far_or_ambiguous_nearest_rays_do_not_bridge_gap():
    n,d,c=pair();d.azimuth[:]=100
    assert not M.nearest(n,d,c)[2].any()


def test_zero_paired_velocity_does_not_alone_propose_clutter():
    n,d,c=pair();ctx=M.paired_doppler(n,[d],c)
    out=load('nonprecip').classify(n,{},c,context=ctx)
    assert not out.arrays['NP_PROPOSAL_MASK'].any()
