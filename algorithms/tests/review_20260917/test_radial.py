import importlib
import numpy as np
import pytest
from .conftest import load, Native
C=load("config").SourceReviewConfig
multi=load("multiscale").multiscale_radials
source=load("source").source_additions


def line(value=25., start=10, stop=80):
    n=Native(); n.fields["DBZH"][6,start:stop]=value; return n


def test_weak_line_is_not_subject_to_35_dbz_gate():
    n=line(); ev=multi(n,C())
    assert ev.arrays["SRC_REVIEW_CANDIDATE_MASK"][6,10:80].all()
    old=importlib.import_module("rp_review_tests.engine.radial_opening").radial_opening
    assert not old(n.fields["DBZH"],n.field_available["DBZH"],1000).any()


@pytest.mark.parametrize("length,expected",[(4,0),(5,1),(10,3),(20,7),(50,15)])
def test_physical_scale_bits(length,expected):
    n=line(start=20,stop=20+length); ev=multi(n,C())
    assert np.all(ev.arrays["SRC_REVIEW_SCALE_BITS"][6,20:20+length]==expected)


def test_discontinuous_identity_does_not_fill_missing():
    n=line(stop=20); n.fields["DBZH"][6,23:35]=25
    n.fields["DBZH"][6,20:23]=np.nan; n.field_available["DBZH"][6,20:23]=False
    ev=multi(n,C()); a=ev.arrays; ids=a["SRC_REVIEW_OBJECT_ID"]
    assert ids[6,12]==ids[6,24]>0
    assert not a["SRC_REVIEW_CANDIDATE_MASK"][6,20:23].any()
    assert not ids[6,20:23].any()
    assert ev.summary["filled_gates"]==0


def test_weather_barrier_prevents_identity_link():
    n=line(stop=20); n.fields["DBZH"][6,23:35]=25
    weather=np.zeros(n.shape,"uint8"); weather[6,21]=1
    a=multi(n,C(),weather=weather).arrays
    assert a["SRC_REVIEW_OBJECT_ID"][6,12]!=a["SRC_REVIEW_OBJECT_ID"][6,24]


def test_link_span_is_bounded_not_transitive_growth():
    n=Native()
    for lo in range(5,100,8): n.fields["DBZH"][6,lo:lo+6]=25
    a=multi(n,C(maximum_identity_span_m=20000)).arrays
    for ident in np.unique(a["SRC_REVIEW_OBJECT_ID"]):
        if ident:
            gates=np.where(a["SRC_REVIEW_OBJECT_ID"][6]==ident)[0]
            assert (gates[-1]-gates[0]+1)*1000<=20000


def test_parallel_bundle_uses_outside_flanks():
    n=Native(); n.fields["DBZH"][5:8,10:70]=25
    a=multi(n,C()).arrays
    assert a["SRC_REVIEW_CANDIDATE_MASK"][5:8,20:60].all()
    assert (a["SRC_REVIEW_BUNDLE_RAYS"][6,20:60]>=3).all()


def test_absent_flank_is_not_zero_reflectivity():
    n=line(); n.field_available["DBZH"][3:6]=False
    a=multi(n,C()).arrays
    assert not a["SRC_REVIEW_CANDIDATE_MASK"].any()


def test_sector_edges_do_not_wrap():
    n=line(); n.fields["DBZH"][0]=25; n.fields["DBZH"][6]=0
    assert not multi(n,C()).arrays["SRC_REVIEW_CANDIDATE_MASK"].any()


def test_geometry_gap_does_not_supply_a_flank():
    n=line(); n.gap_after[5]=True
    assert not multi(n,C()).arrays["SRC_REVIEW_CANDIDATE_MASK"].any()


def test_resource_limit_explicit_abstention():
    n=line(stop=20); n.fields["DBZH"][6,40:50]=25
    ev=multi(n,C(maximum_objects=1))
    assert ev.summary["status"]=="resource_limit_abstained"
    assert not ev.arrays["SRC_REVIEW_CANDIDATE_MASK"].any()


def test_no_reference_no_action_but_retains_weak_candidate():
    n=line(); q,a,_=source(n,C(),np.zeros(n.shape,"uint8"),np.full(n.shape,np.nan))
    assert not q.any(); assert a["SRC_REVIEW_CANDIDATE_MASK"].any()


@pytest.mark.parametrize("residual,expected",[(0.,True),(2.5,True),(2.6,False),(np.nan,False)])
def test_source_residual_gate(residual,expected):
    n=line(); q,_,_=source(n,C(),np.ones(n.shape,"uint8"),np.full(n.shape,residual))
    assert bool(q.any())==expected


def test_original_narrow_is_actually_called_and_preserved():
    n=line(45.,5,110); ref=np.ones(n.shape,"uint8"); residual=np.zeros(n.shape,"float32")
    original=importlib.import_module("rp_review_tests.engine.narrow_source").narrow_source
    expected,_=original(n,ref,residual)
    q,a,_=source(n,C(multiscale_enabled=False),ref,residual)
    assert expected.any(); np.testing.assert_array_equal(q,expected)
    np.testing.assert_array_equal(a["SRC_REVIEW_NARROW_MASK"],expected.astype("uint8"))


def test_weather_never_becomes_a_source_action():
    n=line(); q,_,_=source(n,C(),np.ones(n.shape),np.zeros(n.shape),weather=np.ones(n.shape,"uint8"))
    assert not q.any()


@pytest.mark.parametrize("field",["reference","weather","conflicts"])
def test_nan_mask_is_rejected_not_cast_to_true(field):
    n=line(); values=dict(reference=np.ones(n.shape),residual=np.zeros(n.shape))
    values[field]=np.full(n.shape,np.nan)
    with pytest.raises(ValueError): source(n,C(),**values)


def test_target_pol_mismatch_blocks_source_match():
    n=Native(SNR=25.,PHIDP=0.,ZDR=0.,RHOHV=.8)
    fit=(0.,0.,np.array([-1.,1.]),np.array([-.1,.1]),np.array([.79,.81]),np.array([24.,26.]))
    matched,_=load("source").target_reference(n,6,fit,np.zeros(n.shape),np.ones(n.shape[1],bool),maximum_residual_db=2.5)
    assert matched.all()
    n.fields["RHOHV"][6,5]=.98
    matched,_=load("source").target_reference(n,6,fit,np.zeros(n.shape),np.ones(n.shape[1],bool),maximum_residual_db=2.5)
    assert not matched[5] and matched[6]


def test_source_contract_validator_rejects_filled_gate():
    n=line(); q,a,_=source(n,C(),np.ones(n.shape,"uint8"),np.zeros(n.shape,"float32"))
    a["SRC_REVIEW_REFERENCE_FOLD_ID"]=np.ones(n.shape,"uint32")
    load("source_validation").validate_source_fields(a,np.ones(n.shape,bool))
    a["SRC_REVIEW_QUALIFIED_MASK"][0,0]=1
    with pytest.raises(ValueError): load("source_validation").validate_source_fields(a,np.ones(n.shape,bool))
