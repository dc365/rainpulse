from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import numpy as np
import pytest
from .conftest import load, Group
B=load("background")


def samples(n=200, one_day=False):
    result=[]
    for i in range(n):
        t=datetime(2026,8,1,tzinfo=timezone.utc)+timedelta(days=0 if one_day else i//10,minutes=i if one_day else i%10)
        result.append({
            "radar_id":"synthetic","radar_config_version":"cfg1","scan_strategy_id":"vcp1","hardware_config_version":"hw1",
            "reviewed_clear_air":True,"case_category":"clear_sky","scan_id":f"s{i}","sweep_name":"sweep_000",
            "input_sha256":hashlib.sha256(f"raw{i}".encode()).hexdigest(),"observed_at_utc":t.isoformat(),"clear_air_review_id":"human-clear-1",
            "azimuth_deg":np.array([0.,1.,2.]),"range_m":np.array([1000.,2000.]),"elevation_deg":np.array([.5,.5,.5]),
            "dbzh":np.full((3,2),20.,"float32"),"observed_mask":np.ones((3,2),"uint8"),"no_echo_mask":np.zeros((3,2),"uint8")})
    return result


def root_for(s):
    return Group({"sweep_number":np.array([0]),"sweep_000":Group({"azimuth":s["azimuth_deg"],"range":s["range_m"],"elevation":s["elevation_deg"],"DBZH":s["dbzh"]})},attrs={k:s[k] for k in B.IDENTITY_FIELDS}|{"scan_id":"target","volume_end_time_utc":"2026-09-17T08:24:00+00:00"})


def test_no_echo_nan_counts_in_denominator_missing_nan_does_not():
    ss=samples()
    for i,s in enumerate(ss):
        if i%2:
            s["dbzh"][0]=np.nan;s["no_echo_mask"][0,0]=1;s["observed_mask"][0,1]=0
    a,m=B.build_background(ss)
    assert a["sweep_000__observed_count"][0,0]==200
    assert a["sweep_000__no_echo_count"][0,0]==100
    assert a["sweep_000__ground_clutter"][0,0]==.5
    assert a["sweep_000__observed_count"][0,1]==100
    assert np.isnan(a["sweep_000__ground_clutter"][0,1])
    assert m["denominator"]=="explicit_observed_including_valid_no_echo"


def test_200_correlated_scans_one_day_do_not_meet_date_coverage():
    a,_=B.build_background(samples(one_day=True))
    assert not a["sweep_000__qualified_mask"].any()
    assert (a["sweep_000__day_count"]==1).all()


def test_gate_specific_missing_days_not_global_date_count():
    ss=samples()
    for s in ss[100:]:s["observed_mask"][0,0]=0
    a,_=B.build_background(ss)
    assert a["sweep_000__day_count"][0,0]==10
    assert a["sweep_000__qualified_mask"][0,0]==0


def test_daily_balance_not_oversampled_day_domination():
    ss=samples()
    # Ten scans/day and per-day frequencies remain independently visible.
    for s in ss[:100]:s["no_echo_mask"][:]=1;s["dbzh"][:]=np.nan
    a,_=B.build_background(ss)
    np.testing.assert_allclose(a["sweep_000__frequency"],.5)


def test_verified_asset_load_receipt_and_exact_geometry():
    ss=samples(); a,m=B.build_background(ss)
    loaded=B.verify_background(a,root_for(ss[0]),m["asset_content_sha256"],B.ASSET_VERSION)
    assert loaded["sweep_000"]["nonprecip_background_receipt"]["verified"]
    assert np.all(a["sweep_000__confidence_lower_bound"]>.8)


@pytest.mark.parametrize("mutation",["sha","hardware","geometry","version","future"])
def test_loader_refuses_wrong_identity_or_future(mutation):
    ss=samples();a,m=B.build_background(ss);root=root_for(ss[0]);sha=m["asset_content_sha256"];version=B.ASSET_VERSION
    if mutation=="sha":sha="0"*64
    if mutation=="hardware":root.attrs["hardware_config_version"]="new"
    if mutation=="geometry":root["sweep_000"]["azimuth"]=root["sweep_000"]["azimuth"]+.00001
    if mutation=="version":version="legacy-v1"
    if mutation=="future":root.attrs["volume_end_time_utc"]="2026-08-05T00:00:00+00:00"
    with pytest.raises(ValueError):B.verify_background(a,root,sha,version)


@pytest.mark.parametrize("field",["observed_mask","no_echo_mask","input_sha256","hardware_config_version","scan_strategy_id","clear_air_review_id"])
def test_required_denominator_and_provenance_not_inferred(field):
    ss=samples();ss[0].pop(field)
    with pytest.raises(ValueError):B.build_background(ss)


def test_duplicate_source_not_independent_observation():
    ss=samples();ss[1]["input_sha256"]=ss[0]["input_sha256"]
    with pytest.raises(ValueError):B.build_background(ss)


def test_unreviewed_weather_cannot_train_static_map():
    ss=samples();ss[0]["reviewed_clear_air"]=False
    with pytest.raises(ValueError):B.build_background(ss)


def test_chronological_stream_required():
    ss=samples();ss[1],ss[10]=ss[10],ss[1]
    with pytest.raises(ValueError):B.build_background(ss)


def test_no_echo_subset_observed_is_required():
    ss=samples();ss[0]["observed_mask"][0,0]=0;ss[0]["no_echo_mask"][0,0]=1
    with pytest.raises(ValueError):B.build_background(ss)


def test_detected_nan_is_not_implicitly_valid_no_echo():
    ss=samples();ss[0]["dbzh"][0,0]=np.nan
    with pytest.raises(ValueError):B.build_background(ss)


def test_digest_determinism_and_immutable_publish(tmp_path):
    ss=samples();a,m=B.build_background(ss);b,n=B.build_background(ss)
    assert B.digest(a)==B.digest(b)
    path=tmp_path/"background.npz";receipt=B.write_asset(path,a)
    with np.load(path,allow_pickle=False) as x:assert B.digest({k:x[k] for k in x.files})==receipt["asset_content_sha256"]
    with pytest.raises(FileExistsError):B.write_asset(path,a)
