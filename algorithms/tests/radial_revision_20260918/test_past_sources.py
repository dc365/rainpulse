import copy
import numpy as np
import pytest
from .conftest import Native, load


def fixture():
    n = Native(np.full((5, 160), 20.), fields={'RHOHV':.6, 'SNR':20.})
    parent = np.ones(n.shape, 'uint32')
    gates = np.arange(20, 120)
    targets = np.arange(30, 110)
    bounded = np.zeros(n.shape, bool); bounded[2, targets] = True
    source = dict(current_row=2, original_gates=gates.tolist(), target_gates=targets.tolist(),
                  support_m=100000., start_m=n.ranges[20], end_m=n.ranges[119])
    reference = dict(scan_id='past-1',sha256='a'*64,strictly_past=True,status='evaluated',
        graph_degraded=False, bounded_sources=[source],
        bounded_remaining_indices=np.flatnonzero(bounded).tolist(),
        strong_remaining_indices=np.flatnonzero(bounded).tolist())
    return n,parent,[reference],bounded


def run(n,parent,refs,blocked=None):
    return load('radial_revision.past_sources').qualify(n,np.zeros(n.shape,bool) if blocked is None else blocked,parent,refs)


def test_joint_current_polar_and_frozen_past_bounds_without_action():
    n,parent,refs,bounded=fixture()
    raw=n.fields['DBZH'].copy()
    out,report=run(n,parent,refs)
    assert np.array_equal(out['RV2_PAST_SOURCE_QUALIFIED_MASK'],bounded)
    assert report['actions']==0 and not report['recursive_growth']
    assert np.array_equal(raw,n.fields['DBZH'])


def test_current_weather_not_rejected_despite_two_past_sources():
    n,parent,refs,_=fixture()
    second=copy.deepcopy(refs[0]);second.update(scan_id='past-2',sha256='b'*64)
    n.fields['RHOHV'][:]=.99
    out,_=run(n,parent,refs+[second])
    assert not out['RV2_PAST_SOURCE_QUALIFIED_MASK'].any()


def test_finite_but_unavailable_polar_missing_and_low_snr_abstain():
    for key in ('RHOHV','SNR','DBZH'):
        n,parent,refs,_=fixture();n.field_available[key][:]=False
        out,_=run(n,parent,refs)
        assert not out['RV2_PAST_SOURCE_QUALIFIED_MASK'].any()
    n,parent,refs,_=fixture();n.fields['SNR'][:]=3.
    out,_=run(n,parent,refs);assert not out['RV2_PAST_SOURCE_QUALIFIED_MASK'].any()


def test_protected_strip_splits_window_and_no_current_parent_no_qualification():
    n,parent,refs,_=fixture()
    blocked=np.zeros(n.shape,bool);blocked[2,60:80]=True
    out,_=run(n,parent,refs,blocked)
    assert not out['RV2_PAST_SOURCE_QUALIFIED_MASK'][blocked].any()
    # Windows beside the short remaining strip cannot borrow support across it.
    blocked[2,:60]=True;blocked[2,85:]=True
    out,_=run(n,parent,refs,blocked);assert not out['RV2_PAST_SOURCE_QUALIFIED_MASK'].any()
    out,_=run(n,np.zeros_like(parent),refs);assert not out['RV2_PAST_SOURCE_CANDIDATE_MASK'].any()


def test_reject_duplicate_future_forged_source_and_recursive_extent():
    n,parent,refs,_=fixture()
    with pytest.raises(ValueError):run(n,parent,refs+refs)
    for mutation in ('future','support','extent','claims'):
        bad=copy.deepcopy(refs)
        if mutation=='future':bad[0]['strictly_past']=False
        if mutation=='support':bad[0]['bounded_sources'][0]['support_m']=1.
        if mutation=='extent':bad[0]['bounded_sources'][0]['target_gates'].append(140)
        if mutation=='claims':bad[0]['bounded_remaining_indices']=[]
        with pytest.raises(ValueError):run(n,parent,bad)


def test_degraded_stage_a_cannot_supply_source_and_local_support_needs_both_windows():
    n,parent,refs,_=fixture();refs[0]['graph_degraded']=True
    out,_=run(n,parent,refs);assert not out['RV2_PAST_SOURCE_CANDIDATE_MASK'].any()
    n,parent,refs,_=fixture();n.field_available['RHOHV'][:]=False
    n.field_available['RHOHV'][2,70:78]=True
    out,_=run(n,parent,refs);assert not out['RV2_PAST_SOURCE_QUALIFIED_MASK'].any()
