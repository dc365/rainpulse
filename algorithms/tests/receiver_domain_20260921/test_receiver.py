from dataclasses import replace
from types import SimpleNamespace as NS
import json
import numpy as np
import pytest
from volume_review.data import Sweep, ResourceLimit
from volume_review.config import VolumeReviewConfig
from volume_review.receiver_domain.config import ReceiverDomainConfig as C
from volume_review.receiver_domain.core import evaluate, fit_fold
from volume_review.receiver_domain.disposition import apply, CR
from volume_review.receiver_domain.validation import validate_serialized
from volume_review.receiver_domain.integration import attributes


def scene(shift=0.):
    r = np.arange(50125., 350000., 500.)
    az = (np.arange(9.)+10.+shift) % 360
    snr = np.broadcast_to((10.+np.sin(r/20000.))[None, :], (9, len(r))).copy()
    snr[4] = 40.
    z = snr+20*np.log10(r[None, :]/1000.)+.01*r[None, :]/1000.-50.
    z[4, (r > 160000.) & (r < 180000.)] = np.nan
    phase = np.full_like(z, 40.); phase[4] = 276.
    zdr = np.full_like(z, .5); zdr[4] = -.5
    fields = dict(DBZH=z, SNR=snr, RHOHV=np.full_like(z, .98), ZDR=zdr, PHIDP=phase)
    good = np.ones(9, bool); gaps = np.zeros(9, bool); gaps[-1] = True
    return Sweep("sweep_000", az, np.full(9, .5), r, fields,
                 {k: np.isfinite(v) for k,v in fields.items()}, good, gaps)


def altered(s, key, where, value):
    f = {k: v.copy() for k,v in s.fields.items()}; f[key][where] = value
    a = {k: v.copy() for k,v in s.available.items()}; a[key] = np.isfinite(f[key])
    return replace(s, fields=f, available=a)


def baseline(s):
    obs = s.observed; z=s.fields['DBZH']
    return {"DBZH_RAW": z.copy(), "DBZH_QC": z.copy(), "VALID_MASK": obs.astype('uint8'),
        "REFLECTIVITY_TRUST_MASK": obs.astype('uint8'), "PHIDP_TRUST_MASK": obs.astype('uint8'),
        "QPE_ELIGIBLE_MASK": obs.astype('uint8'), CR: obs.astype('uint8'),
        "QC_ACTION": np.where(obs,0,3).astype('uint8'), "QC_FLAGS": np.zeros(s.shape,'uint32'),
        "QUALITY_INDEX": np.where(obs,.9,np.nan).astype('float32'), "LOW_QUALITY_MASK": np.zeros(s.shape,'uint8'),
        "CR_UNCERTAIN_MASK": np.zeros(s.shape,'uint8'), "CR_QUALIFICATION_REASON": np.zeros(s.shape,'uint16'),
        "DBZH_USABLE": z.copy(), "KDP_OS": np.where(obs,.4,np.nan).astype('float32'),
        "KDP_OS_AVAILABLE_MASK": obs.astype('uint8')}


def test_raw_and_missing_invariant():
    s=scene(); before=s.digest; e=evaluate(s,C())
    assert e.arrays['RDR_FULL_MATCH_MASK'].any() and s.digest==before
    assert not (e.arrays['RDR_FULL_MATCH_MASK'].astype(bool)&~s.observed).any()
    assert e.summary['confirmed_gates']==0 and e.summary['filled_gates']==0
    assert any(m['snr_reference_without_dbzh']>0 for m in e.models)


@pytest.mark.parametrize('block',[3,7,11,15])
def test_target_guard_cannot_change_frozen_reference(block):
    s=scene(); c=C(); m,status=fit_fold(s,4,block,c); assert m
    mask=abs((s.ranges//c.block_m).astype(int)-block)<=c.guard_blocks
    f={k: v.copy() for k,v in s.fields.items()}
    for v in f.values(): v[:,mask]=np.nan
    t=replace(s,fields=f,available={k:np.isfinite(v) for k,v in f.items()})
    q,status=fit_fold(t,4,block,c)
    assert q==m


def test_fold_nomination_not_whole_ray_stationarity():
    s=scene(); c=C(); target=(s.ranges//c.block_m).astype(int)==10
    original=evaluate(s,c)
    # Previously the whole-ray prefilter could silently remove all models.
    t=altered(s,'SNR',np.s_[:,target],90.)
    changed=evaluate(t,c)
    get=lambda e:[m for m in e.models if m['ray']==4 and m['target_block']==10][0]
    assert get(original)==get(changed)
    assert not changed.arrays['RDR_FULL_MATCH_MASK'][4,target].any()


@pytest.mark.parametrize('shift',[27.,180.,347.])
def test_rotation_and_north_seam(shift):
    a=evaluate(scene(),C()).arrays;b=evaluate(scene(shift),C()).arrays
    np.testing.assert_equal(a['RDR_FULL_MATCH_MASK'],b['RDR_FULL_MATCH_MASK'])


@pytest.mark.parametrize('shift',[17.,123.,359.])
def test_phase_has_no_absolute_target(shift):
    s=scene(); a=evaluate(s,C()).arrays
    f=dict(s.fields);f['PHIDP']=(f['PHIDP']+shift)%360
    b=evaluate(replace(s,fields=f),C()).arrays
    np.testing.assert_equal(a['RDR_FULL_MATCH_MASK'],b['RDR_FULL_MATCH_MASK'])


@pytest.mark.parametrize('k',['RHOHV','PHIDP','ZDR'])
def test_partial_is_not_quarantine(k):
    s=scene(); j=(s.ranges//20000).astype(int)==10
    s=altered(s,k,np.s_[4,j],np.nan); c=C(mode='quarantine')
    e=evaluate(s,c); o,_=apply(baseline(s),e.arrays,c,low_quality_flag=1024)
    assert e.arrays['RDR_PARTIAL_MATCH_MASK'][4,j].any()
    assert not o['RDR_QUARANTINE_MASK'][4,j].any()
    assert (o['QPE_ELIGIBLE_MASK'][4,j]==1).all()


def test_target_tail_cannot_take_partial_route():
    s=scene(); j=(s.ranges//20000).astype(int)==10
    s=altered(s,'ZDR',np.s_[4,j],7.5);e=evaluate(s,C())
    assert e.arrays['RDR_TARGET_TAIL_MASK'][4,j].all()
    assert not e.arrays['RDR_FULL_MATCH_MASK'][4,j].any()
    assert not e.arrays['RDR_PARTIAL_MATCH_MASK'][4,j].any()


def test_available_polar_conflict_veto():
    s=scene();j=(s.ranges//20000).astype(int)==10
    s=altered(s,'PHIDP',np.s_[4,j],40.); e=evaluate(s,C())
    assert not e.arrays['RDR_FULL_MATCH_MASK'][4,j].any()
    assert not e.arrays['RDR_PARTIAL_MATCH_MASK'][4,j].any()


@pytest.mark.parametrize('mode',['audit','cr_only','quarantine'])
def test_modes_and_recursive_validation(mode):
    s=scene();c=C(mode=mode);e=evaluate(s,c);b=baseline(s)
    o,diag=apply(b,e.arrays,c,low_quality_flag=1024);calls=[]
    def parent(g, attrs):
        for k in b: np.testing.assert_equal(g[k],b[k])
        calls.append(1)
    validate_serialized(o,attributes(c,1024),parent)
    assert calls==[1]
    if mode=='audit':
        for k in b:np.testing.assert_equal(o[k],b[k])
    if mode=='cr_only':
        assert (o[CR]<b[CR]).any();np.testing.assert_equal(o['QPE_ELIGIBLE_MASK'],b['QPE_ELIGIBLE_MASK'])
        np.testing.assert_equal(o['KDP_OS'],b['KDP_OS'])
    if mode=='quarantine':
        assert diag['qpe_loss_gates']>0 and o['RDR_DERIVED_INVALIDATION_MASK'].any()
        assert not np.isfinite(o['KDP_OS'][o['RDR_DERIVED_INVALIDATION_MASK']==1]).any()
    np.testing.assert_equal(o['DBZH_RAW'],b['DBZH_RAW'])
    np.testing.assert_equal(o['VALID_MASK'],b['VALID_MASK'])


def test_corrupted_delta_and_missing_before_rejected():
    s=scene();c=C(mode='quarantine');b=baseline(s);e=evaluate(s,c)
    o,_=apply(b,e.arrays,c,low_quality_flag=1024)
    q=np.argwhere(o['RDR_QUARANTINE_MASK']==1)[0]
    o[CR][tuple(q)]=1
    with pytest.raises(ValueError):validate_serialized(o,attributes(c,1024),lambda *_:None)
    o,_=apply(b,e.arrays,c,low_quality_flag=1024);del o['RDR_BEFORE_QC_FLAGS']
    with pytest.raises(ValueError):validate_serialized(o,attributes(c,1024),lambda *_:None)


def test_weather_origin_not_high_rho_absolute_veto():
    s=scene();local=np.zeros(s.shape,bool);local[4]=True
    conservative=evaluate(s,C(),local_coherence=local)
    joint=evaluate(s,C(local_policy='source_joint_review'),local_coherence=local)
    assert not conservative.arrays['RDR_SOURCE_MASK'].any()
    assert joint.arrays['RDR_SOURCE_MASK'].any() and joint.arrays['RDR_LOCAL_REVIEWED_MASK'].any()
    for kw in ['independent_weather','unknown_protection']:
        e=evaluate(s,C(local_policy='source_joint_review'),local_coherence=local,**{kw:local})
        assert not e.arrays['RDR_SOURCE_MASK'].any()


def test_explicit_snr_availability_not_finite_fallback():
    s=scene();a=dict(s.available);a['SNR']=np.zeros(s.shape,bool)
    e=evaluate(replace(s,available=a),C());assert not e.models


def test_no_measured_shoulders_no_source():
    s=scene();s=altered(s,'SNR',np.s_[:,:],40.)
    e=evaluate(s,C());assert not e.models


def test_geometry_gap_blocks_side():
    s=scene();g=s.gap_after.copy();g[4]=True
    e=evaluate(replace(s,gap_after=g),C());assert not e.arrays['RDR_FULL_MATCH_MASK'].any()


def test_resource_failure_is_atomic():
    with pytest.raises(ResourceLimit):evaluate(scene(),C(maximum_folds=1))


def test_disabled_serialization_preserves_parent_hash_input():
    cfg=VolumeReviewConfig();d=cfg.model_dump(mode='json')
    assert 'receiver_domain' not in d
    assert 'receiver_domain' not in json.loads(cfg.model_dump_json())
    assert 'receiver_domain' in VolumeReviewConfig(receiver_domain=C()).model_dump(mode='json')
    with pytest.raises(ValueError):VolumeReviewConfig(receiver_domain=C(mode='quarantine'))


def test_source_match_is_not_truth():
    e=evaluate(scene(),C());assert e.summary['confirmed_gates']==0
    assert not any(k in e.arrays for k in ['QC_ACTION','QC_FLAGS','RFI_RISK_STATE'])


def test_measured_target_shoulders_are_a_mixed_veto():
    s=scene();j=(s.ranges//20000).astype(int)==10
    s=altered(s,'SNR',np.s_[3,j],40.)
    c=C(mode='quarantine',local_policy='source_joint_review')
    e=evaluate(s,c);o,_=apply(baseline(s),e.arrays,c,low_quality_flag=1024)
    assert e.arrays['RDR_FULL_MATCH_MASK'][4,j].any()
    assert e.arrays['RDR_TARGET_SIDE_CONFLICT_MASK'][4,j].any()
    assert not o['RDR_QUARANTINE_MASK'][4,j].any()


def test_profile_generator_preserves_parent_and_refuses_overwrite(tmp_path):
    import sys,copy
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
    from make_receiver_domain_profiles import generate
    parent={'engine':'open_source','operational_eligible':False,'profile_version':'test-parent',
        'echo':{'no_rain_below_dbz':-10},'volume_review':{'phase':3,'mode':'experiment_quarantine'}}
    old=copy.deepcopy(parent);out=tmp_path/'profiles';made=generate(parent,out)
    assert len(made)==5 and old==parent
    with pytest.raises(ValueError):generate(parent,out)
