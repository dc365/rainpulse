from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import copy
import numpy as np
import pytest
from .conftest import load, Native
C=load("config").NonPrecipConfig
T=load("temporal")


def past(n,minutes=6,ident="previous"):
    x=n.clone();x.attrs["scan_id"]=ident
    x.attrs["volume_end_time_utc"]=(datetime.fromisoformat(n.attrs["volume_end_time_utc"])-timedelta(minutes=minutes)).isoformat()
    return x


def test_strictly_past_raw_recurrence():
    n=Native();a,s=T.raw_recurrence(n,[past(n),past(n,12,"previous2")],C())
    assert (a["NP_FIXED_SAMPLE_COUNT"]==2).all()
    assert (a["NP_FIXED_MATCH_FRACTION"]==1).all()
    assert np.isnan(a["NP_ADVECTED_MATCH_FRACTION"]).all()


@pytest.mark.parametrize("minutes",[0,-1,30])
def test_future_equal_or_stale_time_abstains(minutes):
    n=Native();a,s=T.raw_recurrence(n,[past(n,minutes)],C())
    assert not a["NP_FIXED_SAMPLE_COUNT"].any();assert s["abstained"]["time"]==1


def test_temporal_missing_is_not_stationary_no_echo():
    n=Native();p=past(n);p.fields["DBZH"][0,0]=np.nan;p.field_available["DBZH"][0,0]=False
    a,_=T.raw_recurrence(n,[p],C())
    assert a["NP_FIXED_SAMPLE_COUNT"][0,0]==0 and np.isnan(a["NP_FIXED_MATCH_FRACTION"][0,0])


def test_temporal_geometry_difference_abstains():
    n=Native();p=past(n);p.elevation[:]=1.5
    a,s=T.raw_recurrence(n,[p],C())
    assert not a["NP_FIXED_SAMPLE_COUNT"].any();assert s["abstained"]["geometry"]==1


def test_temporal_duplicate_rejected():
    n=Native();p=past(n)
    with pytest.raises(ValueError):T.raw_recurrence(n,[p,p],C())


def test_temporal_four_references_exceed_frozen_limit():
    n=Native()
    with pytest.raises(ValueError):T.raw_recurrence(n,[past(n,i+1,str(i)) for i in range(4)],C())


def test_upper_no_echo_without_sensitivity_is_not_negative():
    z=np.full((3,2),10.,"float32");one=np.ones(z.shape,"uint8")
    a=T.verified_vertical_absence(z,one,one,one,np.full(z.shape,12.))
    assert not a["NP_VERTICAL_NEGATIVE_MASK"].any()
    a=T.verified_vertical_absence(z,one,one,one,np.full(z.shape,0.))
    assert a["NP_VERTICAL_NEGATIVE_MASK"].all()


@pytest.mark.parametrize("kw",[{"scales_m":(5000.,5000.)},{"scales_m":(np.nan,)},{"maximum_link_gap_m":50000.},{"bundle_half_widths":(2,1)},{"maximum_source_residual_db":3.},{"unexpected_flag":True}])
def test_source_config_rejects_invalid_or_loosened_bounds(kw):
    with pytest.raises(ValueError):load("config").SourceReviewConfig(**kw)


def test_disabled_extension_removes_only_new_none_fields_from_hash_input():
    old={"context":{"enabled":True},"static_ground_clutter":{"asset_uri":None},"generalization":{"broad_source":{"mode":"audit","radial_opening":True}}}
    new=copy.deepcopy(old);new.update(review_extension_version=None,nonprecip_review=None);new["generalization"]["broad_source"]["source_review"]=None
    assert load("profile_support").strip_absent_review_fields(new)==old


def test_enabled_extension_remains_in_profile_hash_input():
    value={"review_extension_version":"qc-review-20260917-v1","nonprecip_review":{"mode":"audit"}}
    assert load("profile_support").strip_absent_review_fields(copy.deepcopy(value))==value


def test_version_identity_cannot_impersonate_old_profile():
    profile=NS(generalization=NS(broad_source=NS(source_review=None)),nonprecip_review=C(),review_extension_version=None,pipeline_version="qc-opensource-7.3.6")
    with pytest.raises(ValueError):load("profile_support").validate_review_profile(profile)


def test_physical_area_varies_with_range_not_gate_count():
    n=Native(dr=1000.,start=1000.)
    a=load("physical").gate_areas_km2(n)
    assert a[6,100]>50*a[6,1]


def test_physical_components_do_not_join_across_sector_gap():
    n=Native();x=np.zeros(n.shape,bool);x[5:7,20:23]=True;n.gap_after[5]=True
    ids,_=load("physical").component_areas(n,x)
    assert ids[5,21]!=ids[6,21]
