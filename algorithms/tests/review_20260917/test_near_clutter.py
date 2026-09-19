import numpy as np
import pytest
from .conftest import Native, load, keep, serial

C = load("config").NonPrecipConfig
classify = load("nonprecip").classify
project = load("runtime").project


def run(**fields):
    values = dict(RHOHV=.6, ZDR=5., SNR=20.)
    values.update(fields)
    n = Native(z=np.full((12, 80), 20.), full=False, **values)
    cfg = C(near_enabled=True, quarantine_classes=("near_nonmet",), mode="experiment_quarantine")
    evidence = {"OS_GABELLA_CANDIDATE_MASK": np.ones(n.shape, "uint8")}
    return n, cfg, evidence


def test_measured_nonmet_works_without_velocity_or_history():
    n,c,e = run()
    r = classify(n,e,c)
    assert r.arrays["NP_PROPOSAL_MASK"][5,20]
    assert r.arrays["NP_EVIDENCE_FAMILY_COUNT"][5,20] == 2
    out,_ = project(n,keep(n),r,c,low_quality_flag=16384)
    q = out.arrays["NP_QUARANTINE_MASK"] == 1
    assert q.any()
    assert not out.arrays["QPE_ELIGIBLE_MASK"][q].any()
    assert not out.arrays["REFLECTIVITY_TRUST_MASK"][q].any()
    assert np.isnan(out.arrays["DBZH_USABLE"][q]).all()
    load('runtime').validate_nonprecip_fields(serial(out,n),np.ones(n.shape,bool),np.zeros(n.shape,bool),np.zeros(n.shape,bool))


@pytest.mark.parametrize('fields',[{'RHOHV':.99},{'SNR':3.},{'ZDR':1.}, {'RHOHV':np.nan}, {'SNR':np.nan}])
def test_incomplete_or_weather_like_measurements_abstain(fields):
    n,c,e=run(**fields)
    assert not classify(n,e,c).arrays['NP_PROPOSAL_MASK'].any()


def test_weather_support_and_strong_echo_are_retained():
    n,c,e=run()
    assert not classify(n,e,c,weather_support=np.ones(n.shape)).arrays['NP_PROPOSAL_MASK'].any()
    n.fields['DBZH'][:]=40
    assert not classify(n,e,c).arrays['NP_PROPOSAL_MASK'].any()


def test_no_texture_or_sparse_single_ray_is_not_enough():
    n,c,e=run()
    assert not classify(n,{},c).arrays['NP_PROPOSAL_MASK'].any()
    n.field_available['RHOHV'][:]=False
    n.field_available['RHOHV'][5]=True
    assert not classify(n,e,c).arrays['NP_PROPOSAL_MASK'].any()


def test_station_and_time_independent_but_range_and_geometry_bounded():
    n,c,e=run()
    before=classify(n,e,c).arrays['NP_PROPOSAL_MASK']
    n.attrs.update(radar_id='unseen_station',scan_id='unseen_time')
    assert np.array_equal(before,classify(n,e,c).arrays['NP_PROPOSAL_MASK'])
    n.ranges += 150000
    assert not classify(n,e,c).arrays['NP_PROPOSAL_MASK'].any()
    n.ranges -= 150000
    n.gap_after[:]=True
    assert not classify(n,e,c).arrays['NP_PROPOSAL_MASK'].any()


def test_default_profile_does_not_change_actions():
    n,c,e=run()
    assert not classify(n,e,C()).arrays['NP_PROPOSAL_MASK'].any()
