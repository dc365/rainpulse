"""Full RAW subbands inside mixed parents, with held-out native windows."""

import numpy as np
import pytest

from .conftest import Native, load
from .test_variable_morphology import fixture as weather_fixture


def fixture():
    ranges = np.arange(0.0, 460000.0, 500.0)
    z = np.full((71, len(ranges)), np.nan, "float32")
    cols = (ranges >= 60000.0) & (ranges < 440000.0)
    z[22:49, cols] = 24.0
    # Local merged contamination connects another original object, without
    # changing the stable band's long-range original exterior.
    z[49:63, (ranges >= 160000.0) & (ranges < 180000.0)] = 20.0
    n = Native(z, dr=500.0, start=0.0, fields={"SNR": np.where(np.isfinite(z), 8.0, -2.0)})
    return n


def run(native):
    m = load("radial_revision.unified_objects")
    return m.evaluate(native, np.zeros(native.shape, bool), subbands_enabled=True)


def test_mixed_original_parent_recovers_band_with_no_old_source_ids():
    n = fixture()
    raw = n.fields["DBZH"].copy()
    arrays, report = run(n)
    hit = arrays["RV2_UNIFIED_PROPOSAL_MASK"] == 1
    assert hit[22:49, (n.ranges >= 220000) & (n.ranges < 400000)].all()
    assert not hit[:, (n.ranges >= 160000) & (n.ranges < 180000)].any()
    assert np.array_equal(n.fields["DBZH"], raw, equal_nan=True)
    assert any(o["nomination_kind"] == "subband" and o["confirmed"] for o in report["objects"])


def test_sparse_target_does_not_train_reference_or_expand_original_range():
    n = fixture()
    cols = (n.ranges >= 240000) & (n.ranges < 260000)
    n.fields["DBZH"][22:49, cols] = np.nan
    target_col = int(np.flatnonzero(n.ranges == 250000)[0])
    n.fields["DBZH"][23, target_col] = 12.0
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    n.fields["SNR"][22:49, cols] = 8.0
    arrays, report = run(n)
    assert arrays["RV2_UNIFIED_PROPOSAL_MASK"][23, target_col] == 1
    assert not arrays["RV2_UNIFIED_PROPOSAL_MASK"][~n.field_available["DBZH"]].any()
    proof = [o for o in report["objects"] if o["nomination_kind"] == "subband"]
    assert proof
    for obj in proof:
        for w in obj["reference_windows"]:
            assert all(abs(b - w["target_block"]) > 1 for b in w["blocks"])


@pytest.mark.parametrize("case", ["unknown", "nonquiet", "weather_shoulder"])
def test_missing_or_wet_exterior_is_not_dry_evidence(case):
    n = fixture()
    if case == "unknown":
        n.fields["SNR"][[21, 49]] = np.nan
        n.field_available["SNR"][[21, 49]] = False
    elif case == "nonquiet":
        n.fields["SNR"][[21, 49]] = 8.0
    else:
        n.fields["DBZH"][21] = 23.0
        n.field_available["DBZH"][21] = True
        n.fields["RHOHV"] = np.full(n.shape, np.nan, "float32")
        n.fields["RHOHV"][21] = 0.99
        n.field_available["RHOHV"] = np.isfinite(n.fields["RHOHV"])
        n.fields["SNR"][21] = 20.0
    arrays, _ = run(n)
    assert arrays["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"][23, 500] == 0


@pytest.mark.parametrize("kind", ["constant_km", "curved"])
def test_complete_parent_weather_cannot_restart_as_far_subband(kind):
    arrays, _ = run(weather_fixture(kind))
    assert not arrays["RV2_UNIFIED_PROPOSAL_MASK"].any()


def test_subband_retains_local_positive_weather_and_true_angular_gap():
    n = fixture()
    n.fields["RHOHV"] = np.full(n.shape, np.nan, "float32")
    n.fields["RHOHV"][30, 600] = 0.99
    n.fields["SNR"][30, 600] = 20.0
    n.field_available["RHOHV"] = np.isfinite(n.fields["RHOHV"])
    arrays, _ = run(n)
    assert arrays["RV2_UNIFIED_PROPOSAL_MASK"][30, 600] == 0
    n = fixture()
    n.gap_after[34] = True
    arrays, _ = run(n)
    assert not arrays["RV2_UNIFIED_PROPOSAL_MASK"].any()


def test_local_merge_cannot_hide_curved_complete_weather_history():
    n = weather_fixture("curved")
    cols = (n.ranges >= 160000) & (n.ranges < 180000)
    n.fields["DBZH"][25:45, cols] = 20.0
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    n.fields["SNR"][:] = np.where(n.field_available["DBZH"], 8.0, -2.0)
    arrays, _ = run(n)
    assert not arrays["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"].any()


def test_subband_rotation_across_north_preserves_native_membership():
    n = fixture()
    baseline, _ = run(n)
    n.azimuth = (n.azimuth + 330.0) % 360
    rotated, _ = run(n)
    assert np.array_equal(
        baseline["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"], rotated["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"]
    )


def test_half_degree_geometry_uses_same_angular_fan():
    n = fixture()
    z = np.repeat(n.fields["DBZH"], 2, axis=0)
    finer = Native(z, dr=500.0, start=0.0, fields={"SNR": np.repeat(n.fields["SNR"], 2, axis=0)})
    finer.azimuth = np.arange(finer.shape[0]) * 0.5
    baseline, _ = run(n)
    arrays, _ = run(finer)
    assert np.array_equal(
        np.repeat(baseline["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"], 2, axis=0),
        arrays["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"],
    )


def test_original_fork_inside_fan_remains_abstention():
    n = fixture()
    cols = n.ranges >= 240000
    n.fields["DBZH"][30:34, cols] = np.nan
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    n.fields["SNR"][30:34, cols] = -2.0
    arrays, report = run(n)
    assert not arrays["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"].any()
    assert any("ambiguous_fork_or_merge" in o["holds"] for o in report["objects"])


def test_nomination_providers_have_distinct_ids_and_replay_bound_options():
    n = fixture()
    arrays, report = run(n)
    identities = [o["id"] for o in report["objects"]]
    assert len(identities) == len(set(identities))
    m = load("radial_revision.unified_objects")
    m.validate(arrays, n, np.zeros(n.shape, bool), subbands_enabled=True)
    with pytest.raises(ValueError, match="field set differs"):
        m.validate(arrays, n, np.zeros(n.shape, bool), subbands_enabled=False)


def test_subband_work_limit_returns_no_partial_result():
    m = load("radial_revision.native_subbands")
    n = fixture()
    with pytest.raises(load("radial_revision.geometry").ResourceLimit):
        m.nominate(n, np.zeros(n.shape, bool), (), 1.0, maximum_work=10)


def test_target_only_boundary_cannot_train_its_held_out_template():
    n = fixture()
    n.fields["DBZH"][49:63] = np.nan
    n.fields["SNR"][:] = np.where(np.isfinite(n.fields["DBZH"]), 8.0, -2.0)
    cols = (n.ranges >= 260000) & (n.ranges < 280000)
    n.fields["DBZH"][21:50, cols] = 24.0
    n.fields["SNR"][21:50, cols] = 8.0
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    arrays, _ = run(n)
    assert not arrays["RV2_UNIFIED_SUBBAND_PROPOSAL_MASK"][[21, 49]][:, cols].any()
