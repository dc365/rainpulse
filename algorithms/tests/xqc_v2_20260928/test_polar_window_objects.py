"""Mixed source distributions must survive normal points without deleting them."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.polar_windows import WindowPolicy, detect
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .test_polar_morphology import scene


def mixed(kind="line", *, da=1.0, dr=250.0, bearing=100.0, elevation=0.5):
    s, source = scene(kind, da=da, dr=dr, bearing=bearing, elevation=elevation)
    offset = (s.azimuth[:, None] - bearing + 180) % 360 - 180
    weather = (abs(offset) <= 60) & (s.ranges[None, :] >= 2500)
    # End on a complete physical window, not an isolated terminal gate.
    intermittent = source & (s.ranges[None, :] < 55000) & (np.arange(s.shape[1])[None, :] % 2 == 0)
    z = np.where(weather | source, 25.0, 0.0).astype("float32")
    fields = {
        "DBZH": z,
        "SNR": np.full(s.shape, 20.0, "float32"),
        "RHOHV": np.where(intermittent, 0.55, 0.98).astype("float32"),
        "ZDR": np.where(intermittent, -2.0, 0.5).astype("float32"),
    }
    return replace(
        s, fields=fields, available={k: np.ones(s.shape, bool) for k in fields}
    ), intermittent


@pytest.mark.parametrize("da,dr", [(0.5, 75.0), (1.0, 250.0), (2.0, 1000.0)])
@pytest.mark.parametrize("bearing,elevation", [(100.0, 0.47), (359.0, 14.55)])
def test_intermittent_joint_source_inside_wide_weather(da, dr, bearing, elevation):
    s, source = mixed(da=da, dr=dr, bearing=bearing, elevation=elevation)
    before = s.digest
    result = detect(s, WindowPolicy())
    assert result.mask[source].all()
    assert not result.mask[~source].any()
    assert result.record["diagnostic_only"] and s.digest == before


@pytest.mark.parametrize("kind", ["fixed_km", "curved", "blob"])
def test_weather_shaped_joint_anomaly_is_not_a_radial_source(kind):
    s, source = mixed(kind)
    assert source.any() and not detect(s, WindowPolicy()).mask.any()


@pytest.mark.parametrize("cause", ["missing", "unknown_polar", "weather", "gap"])
def test_measured_shoulders_and_supplied_barriers(cause):
    s, source = mixed()
    fields = {k: v.copy() for k, v in s.fields.items()}
    available = {k: v.copy() for k, v in s.available.items()}
    protected = np.zeros(s.shape, bool)
    if cause == "missing":
        available["RHOHV"][~source] = False
    elif cause == "unknown_polar":
        fields["RHOHV"][:] = 0.55
    elif cause == "weather":
        protected[source] = True
    elif cause == "gap":
        gap = s.gap_after.copy()
        gap[100] = True
        s = replace(s, gap_after=gap)
    s = replace(s, fields=fields, available=available)
    before = s.digest
    assert not detect(s, WindowPolicy(), protected=protected).mask.any()
    assert s.digest == before


def test_resource_failure_is_atomic():
    s, _ = mixed()
    with pytest.raises(ResourceLimit):
        detect(s, WindowPolicy(maximum_work=1))


def test_complete_evidence_is_not_truncated_to_fit():
    s, source = mixed()
    assert detect(s, WindowPolicy()).mask[source].all()
    with pytest.raises(ResourceLimit, match="evidence byte budget"):
        detect(s, WindowPolicy(maximum_evidence_bytes=4096))


def test_unknown_source_samples_are_not_members_or_filled():
    s, source = mixed()
    available = {k: v.copy() for k, v in s.available.items()}
    holes = source & (np.arange(s.shape[1])[None, :] % 20 == 0)
    available["ZDR"][holes] = False
    result = detect(replace(s, available=available), WindowPolicy())
    assert not result.mask[holes].any()
    assert result.mask.any()


def test_complete_fan_keeps_normal_holes_and_weather_parent():
    s, source = mixed("fan", bearing=359.0)
    result = detect(s, WindowPolicy())
    assert result.mask[source].all() and not result.mask[~source].any()


def test_oversize_negative_predecessor_cannot_restart_on_narrow_tail():
    s, _ = mixed()
    offsets = (s.azimuth[:, None] - 100 + 180) % 360 - 180
    half = np.where(s.ranges[None, :] < 30000, 35.0, 0.5)
    source = (abs(offsets) <= half) & (s.ranges[None, :] >= 5000)
    source &= s.ranges[None, :] < 55000
    source &= np.arange(s.shape[1])[None, :] % 2 == 0
    fields = {k: v.copy() for k, v in s.fields.items()}
    fields["RHOHV"][:] = 0.98
    fields["RHOHV"][source] = 0.55
    fields["ZDR"][:] = 0.5
    fields["ZDR"][source] = -2
    result = detect(replace(s, fields=fields), WindowPolicy())
    assert not result.mask.any()
    assert any(r["maximum_width_deg"] > 45 for r in result.record["review_objects"])


def test_supplied_counterexample_cannot_be_inherited_through_normal_holes():
    s, source = mixed()
    protected = np.zeros(s.shape, bool)
    protected[100, (s.ranges >= 20000) & (s.ranges < 40000) & ~source[100]] = True
    result = detect(s, WindowPolicy(), protected=protected)
    assert not result.mask.any()
    assert any(r["ambiguous"] for r in result.record["review_objects"])


def test_default_detector_does_not_call_a_production_writer():
    s, source = mixed()
    result = detect(s, WindowPolicy())
    assert result.mask[source].all()
    assert result.record["diagnostic_only"] is True
    assert "QC_ACTION" not in result.record and "DBZH_QC" not in result.record


def test_strong_gate_limit_does_not_erase_original_shape_history():
    s, source = mixed()
    fields = {k: v.copy() for k, v in s.fields.items()}
    strong = source & (s.ranges[None, :] >= 30000)
    fields["DBZH"][strong] = 50
    result = detect(replace(s, fields=fields), WindowPolicy())
    assert not result.mask[strong].any()
    assert result.mask[source & ~strong].all()
    assert result.record["objects"][0]["range_end_m"] >= 54000


def test_forked_original_histories_cannot_authorize_a_new_child_tail():
    s, _ = mixed()
    offset = (s.azimuth[:, None] - 100 + 180) % 360 - 180
    joined = np.floor(s.ranges[None, :] / 5000).astype(int) % 2 == 0
    body = np.where(joined, abs(offset) <= 5, abs(abs(offset) - 5) <= 0.5)
    body &= (s.ranges[None, :] >= 5000) & (s.ranges[None, :] < 55000)
    body &= np.arange(s.shape[1])[None, :] % 2 == 0
    fields = {k: v.copy() for k, v in s.fields.items()}
    fields["RHOHV"][:] = 0.98
    fields["ZDR"][:] = 0.5
    fields["RHOHV"][body] = 0.55
    fields["ZDR"][body] = -2
    result = detect(replace(s, fields=fields), WindowPolicy())
    assert not result.mask.any()
    assert any(r["parent_track_ids"] for r in result.record["review_objects"])
    assert max(r["windows"] for r in result.record["review_objects"]) <= 11


def test_broad_parent_does_not_veto_independent_radial_distribution():
    s, _ = mixed("fan")
    fields = {k: v.copy() for k, v in s.fields.items()}
    broad = (abs(s.azimuth[:, None] - 100) <= 40) & (s.ranges[None, :] < 55000)
    other = (s.azimuth[:, None] == 270) & (s.ranges[None, :] >= 5000)
    other &= s.ranges[None, :] < 55000
    intermittent = np.arange(s.shape[1])[None, :] % 2 == 0
    fields["RHOHV"][:] = 0.98
    fields["ZDR"][:] = 0.5
    fields["RHOHV"][(broad | other) & intermittent] = 0.55
    fields["ZDR"][(broad | other) & intermittent] = -2
    fields["DBZH"][other] = 25
    result = detect(replace(s, fields=fields), WindowPolicy())
    assert result.mask[other & intermittent].all()
    assert not result.mask[broad].any()


def test_successful_object_has_no_rejection_reasons():
    s, source = mixed()
    result = detect(s, WindowPolicy())
    assert result.mask[source].all()
    assert all(r["rejection_reasons"] == [] for r in result.record["objects"])


@pytest.mark.parametrize("cause", ["unavailable", "known_conflict"])
def test_reference_unknown_and_known_conflict_are_distinct(cause):
    s, source = mixed()
    fields = {k: v.copy() for k, v in s.fields.items()}
    available = {k: v.copy() for k, v in s.available.items()}
    if cause == "unavailable":
        available["RHOHV"][~source] = False
    else:
        fields["RHOHV"][:] = 0.55
    result = detect(replace(s, fields=fields, available=available), WindowPolicy())
    assert not result.mask.any()
    reasons = {reason for r in result.record["review_objects"] for reason in r["rejection_reasons"]}
    unknown, conflict = "REFERENCE_UNAVAILABLE_OR_PROTECTED", "REFERENCE_POLAR_CONFLICT"
    expected, opposite = (unknown, conflict) if cause == "unavailable" else (conflict, unknown)
    assert expected in reasons and opposite not in reasons
    assert "INSUFFICIENT_BILATERAL_REFERENCE" in reasons


def test_full_protected_history_reason_is_explicit():
    s, source = mixed()
    protected = np.zeros(s.shape, bool)
    protected[100, (s.ranges >= 20000) & (s.ranges < 40000) & ~source[100]] = True
    result = detect(s, WindowPolicy(), protected=protected)
    assert not result.mask.any()
    reasons = {reason for r in result.record["review_objects"] for reason in r["rejection_reasons"]}
    assert "PROTECTED_ORIGINAL_MEMBER" in reasons
    assert "FORK_OR_MERGE_ANCESTRY" not in reasons
