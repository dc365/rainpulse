import copy
from types import SimpleNamespace
import numpy as np
import pytest
from .conftest import load, Native, keep, serial, history, recurrence
C=load("config").NonPrecipConfig
classify=load("nonprecip").classify
project=load("runtime").project


def ground(): return Native(z=np.full((8,12),20.,"float32"),VR=0.,SW=.5)
def result(n,cfg=None,**kw): return classify(n,{},cfg or C(),**kw)


def test_incomplete_pol_has_independent_history_doppler_temporal_path():
    n=ground(); r=result(n,background=history(n),context=recurrence(n))
    assert np.all(r.arrays["NP_CLASS"]==2)
    assert r.arrays["NP_PROPOSAL_MASK"].all()
    assert r.arrays["NP_CONFIRMED_MASK"].sum()==0


def test_zero_velocity_alone_is_not_clutter():
    n=ground(); assert not result(n).arrays["NP_PROPOSAL_MASK"].any()


def test_history_alone_is_not_clutter():
    n=Native(z=np.full((8,12),20.))
    r=result(n,background=history(n))
    assert not r.arrays["NP_PROPOSAL_MASK"].any()


def test_static_rain_weather_support_is_mixed_and_protected():
    n=ground(); r=result(n,background=history(n),context=recurrence(n),weather_support=np.ones(n.shape))
    assert np.all(r.arrays["NP_CLASS"]==6)
    assert not r.arrays["NP_PROPOSAL_MASK"].any()


def test_raw_high_rho_weather_protection():
    n=ground()
    for k,v in {"RHOHV":.99,"ZDR":1.,"SNR":20.}.items():
        n.fields[k]=np.full(n.shape,v,"float32");n.field_available[k]=np.ones(n.shape,bool)
    r=result(n,background=history(n),context=recurrence(n))
    assert not r.arrays["NP_PROPOSAL_MASK"].any()


def test_current_enhancement_over_background_is_mixed():
    n=ground(); n.fields["DBZH"][:]=30.
    r=result(n,background=history(n),context=recurrence(n))
    assert (r.arrays["NP_CLASS"]==6).all()


def test_actual_sea_location_is_not_deletion_mask():
    n=ground(); ctx={"NP_VERIFIED_MARINE_MASK":np.ones(n.shape),"NP_VERIFIED_LOW_BEAM_MASK":np.ones(n.shape)}
    r=result(n,context=ctx)
    assert not r.arrays["NP_PROPOSAL_MASK"].any()


def test_ap_missing_comparable_vertical_abstains():
    n=Native(z=np.full((8,12),20.),VR=0.,SW=.5,RHOHV=.6,ZDR=0.,SNR=20.)
    r=classify(n,{"OS_GABELLA_CANDIDATE_MASK":np.ones(n.shape)},C(quarantine_classes=("anomalous_propagation",)))
    assert not r.arrays["NP_PROPOSAL_MASK"].any()


def test_ap_requires_actual_combination_and_explicit_class_enable():
    n=Native(z=np.full((8,12),20.),VR=0.,SW=.5,RHOHV=.6,ZDR=0.,SNR=20.)
    ctx={"NP_VERTICAL_NEGATIVE_MASK":np.ones(n.shape),"NP_VERTICAL_NEGATIVE_AVAILABLE_MASK":np.ones(n.shape)}
    ev={"OS_GABELLA_CANDIDATE_MASK":np.ones(n.shape)}
    assert not classify(n,ev,C(),context=ctx).arrays["NP_PROPOSAL_MASK"].any()
    r=classify(n,ev,C(quarantine_classes=("anomalous_propagation",)),context=ctx)
    assert r.arrays["NP_PROPOSAL_MASK"].all()


def test_biology_remains_candidate_not_automatic_removal():
    n=Native(z=np.full((8,12),15.),VR=0.,SW=.5,RHOHV=.5,ZDR=5.,SNR=20.)
    r=result(n,C(quarantine_classes=("biological",)),context=recurrence(n))
    assert (r.arrays["NP_CLASS"]==5).all(); assert not r.arrays["NP_PROPOSAL_MASK"].any()


def test_invalid_vertical_mask_contract_fails():
    n=ground()
    with pytest.raises(ValueError): result(n,context={"NP_VERTICAL_NEGATIVE_MASK":np.ones(n.shape)})


def test_strong_small_cell_is_explicitly_retained():
    z=np.full((12,120),-20.,"float32"); z[6,1:3]=45
    n=Native(z=z,dr=100.,start=500.)
    r=result(n)
    assert r.arrays["NP_SMALL_STRONG_PROTECTED_MASK"][6,1:3].all()
    assert not r.arrays["NP_PROPOSAL_MASK"].any()


def test_audit_does_not_change_any_existing_array_flag_or_quality():
    n=ground(); b=keep(n); r=result(n,background=history(n),context=recurrence(n))
    out,_=project(n,b,r,C(mode="audit"),low_quality_flag=4)
    for key,value in b.arrays.items(): np.testing.assert_array_equal(out.arrays[key],value)
    np.testing.assert_array_equal(out.flags,b.flags);np.testing.assert_array_equal(out.quality,b.quality)


def test_action_quarantines_without_relabeling_rfi_or_missing():
    n=ground(); b=keep(n); r=result(n,background=history(n),context=recurrence(n))
    out,s=project(n,b,r,C(mode="experiment_quarantine"),low_quality_flag=4)
    assert out.arrays["NP_QUARANTINE_MASK"].all()
    assert not out.arrays["RFI_QUARANTINE_MASK"].any()
    assert not out.arrays["QPE_ELIGIBLE_MASK"].any()
    assert np.isnan(out.arrays["DBZH_USABLE"]).all()
    assert (out.arrays["QC_ACTION"]==1).all()
    assert s["review_required"] and s["new_confirmed_gates"]==0
    load("runtime").validate_nonprecip_fields(serial(out,n),np.ones(n.shape,bool),np.zeros(n.shape,bool),np.zeros(n.shape,bool))


def test_budget_does_not_restore_quarantined_cells():
    n=ground(); b=keep(n);r=result(n,background=history(n),context=recurrence(n))
    out,s=project(n,b,r,C(mode="experiment_quarantine",maximum_new_eligible_loss_fraction=0.),low_quality_flag=4)
    assert s["review_required"] and out.arrays["NP_QUARANTINE_MASK"].all()


def test_existing_reject_and_rfi_quarantine_are_immutable():
    n=ground(); b=keep(n)
    b.arrays["QC_ACTION"][0,0]=2; b.arrays["QC_ACTION"][0,1]=1
    b.arrays["RFI_QUARANTINE_MASK"][0,1]=1; b.arrays["RFI_RISK_STATE"][0,1]=2
    for key in load("runtime").TRUST_FIELDS: b.arrays[key][0,:2]=0
    b.arrays["DBZH_USABLE"][0,:2]=np.nan
    r=result(n,background=history(n),context=recurrence(n)); out,_=project(n,b,r,C(mode="experiment_quarantine"),low_quality_flag=4)
    assert not out.arrays["NP_QUARANTINE_MASK"][0,:2].any()
    np.testing.assert_array_equal(out.arrays["RFI_QUARANTINE_MASK"],b.arrays["RFI_QUARANTINE_MASK"])
    assert out.arrays["QC_ACTION"][0,0]==2


def test_missing_input_never_becomes_no_rain_or_quarantine():
    n=ground();n.fields["DBZH"][0,0]=np.nan;n.field_available["DBZH"][0,0]=False
    r=result(n,background=history(n),context=recurrence(n))
    out,_=project(n,keep(n),r,C(mode="experiment_quarantine"),low_quality_flag=4)
    assert out.arrays["NP_CLASS"][0,0]==7; assert out.arrays["QC_ACTION"][0,0]==3
    assert out.arrays["NP_QUARANTINE_MASK"][0,0]==0


def test_validator_detects_trust_leak():
    n=ground(); r=result(n,background=history(n),context=recurrence(n))
    out,_=project(n,keep(n),r,C(mode="experiment_quarantine"),low_quality_flag=4)
    out.arrays["VR_TRUST_MASK"][0,0]=1
    with pytest.raises(ValueError): load("runtime").validate_nonprecip_fields(serial(out,n),np.ones(n.shape,bool),np.zeros(n.shape,bool),np.zeros(n.shape,bool))


def test_absent_extension_returns_same_object():
    n=ground(); b=keep(n)
    out,s=load("runtime").apply_nonprecip_review(n,b,None,SimpleNamespace(nonprecip_review=None))
    assert out is b and s["status"]=="disabled"


def test_unverified_background_is_not_an_action_prior():
    n=ground();h=history(n);h["receipt"]["verified"]=False
    with pytest.raises(ValueError): result(n,background=h,context=recurrence(n))


def test_gabella_and_texture_remain_one_evidence_family():
    n=Native(z=np.full((8,12),20.))
    r=classify(n,{"OS_GABELLA_CANDIDATE_MASK":np.ones(n.shape),"OS_DBZH_TEXTURE_CANDIDATE_MASK":np.ones(n.shape)},C())
    assert (r.arrays["NP_EVIDENCE_FAMILY_COUNT"]==1).all()
