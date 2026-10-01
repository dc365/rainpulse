"""Morphology acts on native boundaries, not a station/time or stable power."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.polar_morphology import MorphologyPolicy, detect
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit, Sweep


def scene(kind="line", *, dr=250.0, da=1.0, elevation=0.5, bearing=100.0, missing_shoulders=False):
    az = np.arange(0.0, 360.0, da)
    r = np.arange(dr, 60000.0 + dr / 2, dr)
    shape = (len(az), len(r))
    z = np.zeros(shape, "float32")
    sn = np.full(shape, -2.0, "float32")
    diff = (az[:, None] - bearing + 180.0) % 360.0 - 180.0
    if kind == "line":
        body = (abs(diff) <= da / 2) & (r[None, :] >= 5000) & (r[None, :] <= 55000)
    elif kind == "fan":
        body = (abs(diff) <= 6) & (r[None, :] >= 5000) & (r[None, :] <= 55000)
    elif kind == "broken":
        body = (
            (abs(diff) <= 2)
            & (r[None, :] >= 5000)
            & (r[None, :] <= 55000)
            & ((r[None, :] % 5000) < 2200)
        )
    elif kind == "curved":
        body = (abs(diff - (r[None, :] / 5000)) <= 2) & (r[None, :] >= 5000) & (r[None, :] <= 55000)
    elif kind == "fixed_km":
        body = (
            (abs(diff) <= np.degrees(np.arctan2(2500, r))[None, :])
            & (r[None, :] >= 5000)
            & (r[None, :] <= 55000)
        )
    elif kind == "blob":
        body = (diff / 8) ** 2 + ((r[None, :] - 30000) / 6000) ** 2 <= 1
    else:
        raise ValueError(kind)
    values = np.broadcast_to(20 + 11 * np.sin(r / 1300), shape)
    z[body] = values[body]
    sn[body] = 15.0
    ok = np.ones(shape, bool)
    fields = {"DBZH": z, "SNR": sn, "RHOHV": np.full(shape, 0.99, "float32")}
    avail = {key: ok.copy() for key in fields}
    if missing_shoulders:
        avail["DBZH"][~body] = False
        avail["SNR"][~body] = False
    s = Sweep(
        "native",
        az,
        np.full(len(az), elevation),
        r,
        fields,
        avail,
        np.ones(len(az), bool),
        np.zeros(len(az), bool),
        np.arange(len(az)) * 0.1,
    )
    return s, body


@pytest.mark.parametrize("kind", ["line", "fan", "broken"])
@pytest.mark.parametrize("dr,da", [(75.0, 0.5), (250.0, 1.0), (1000.0, 2.0)])
def test_variable_power_shapes_complete(kind, dr, da):
    s, body = scene(kind, dr=dr, da=da)
    before = s.digest
    ev = detect(s, MorphologyPolicy())
    assert np.all(ev.mask[body])
    assert not ev.mask[~body].any()
    assert ev.objects and ev.record["status"] == "EVALUATED"
    assert s.digest == before


@pytest.mark.parametrize("elevation", [0.47, 1.41, 3.36, 14.55])
def test_seam_and_elevation(elevation):
    s, body = scene("fan", bearing=359.0, elevation=elevation)
    ev = detect(s, MorphologyPolicy())
    assert np.all(ev.mask[body])


@pytest.mark.parametrize("kind", ["curved", "fixed_km", "blob"])
def test_weather_counterexamples(kind):
    s, body = scene(kind)
    ev = detect(s, MorphologyPolicy())
    assert body.any() and not ev.mask.any()


def test_missing_flanks_are_not_quiet():
    s, _ = scene("fan", missing_shoulders=True)
    assert not detect(s, MorphologyPolicy()).mask.any()


def test_geometry_and_weather_are_barriers():
    s, body = scene("fan")
    gap = s.gap_after.copy()
    gap[100] = True
    assert not detect(replace(s, gap_after=gap), MorphologyPolicy()).mask.any()
    hard = np.zeros(s.shape, bool)
    hard[:, (s.ranges >= 24000) & (s.ranges <= 36000)] = True
    ev = detect(s, MorphologyPolicy(), protected=hard)
    assert not ev.mask[hard].any()
    assert not ev.mask.any()  # each unprotected span falls below 20 km


def test_resource_limit_is_atomic():
    s, _ = scene("fan")
    with pytest.raises(ResourceLimit):
        detect(s, MorphologyPolicy(maximum_work=1))


def test_explicit_no_echo_shoulders_support_without_snr():
    s, body = scene("fan")
    available = dict(s.available)
    available["SNR"] = np.zeros(s.shape, bool)
    available["DBZH"] = body.copy()
    s = replace(s, available=available, no_echo=~body)
    assert np.all(detect(s, MorphologyPolicy()).mask[body])


@pytest.mark.parametrize("mode", ["audit", "cr_only", "quarantine"])
def test_shape_enters_sole_finalizer_without_rf_or_doppler_claim(mode):
    from rainpulse_algo.multiband.model import Sweep as XCut
    from rainpulse_algo.multiband.model import Volume
    from rainpulse_algo.multiband.quality import x_qc

    from .helpers import config, fixture, station

    native, body = scene("fan")
    parent, _ = fixture("empty")
    fields = {key: value.copy() for key, value in native.fields.items()}
    fields.update(
        OBSERVED_MASK=np.ones(native.shape, "uint8"), NO_ECHO_MASK=np.zeros(native.shape, "uint8")
    )
    cut = XCut(
        0, native.azimuth, native.ranges, native.elevation, 1787875200.0 + native.ray_time_s, fields
    )
    vol = Volume(parent.metadata, [cut])
    before = cut.fields["DBZH"].copy()
    cfg = config(
        mode=mode,
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
        morphology=MorphologyPolicy().model_dump(),
    )
    out = x_qc(vol, station(cfg), "b" * 64)
    f = out.sweeps[0].fields
    assert f["XQC_MORPHOLOGY_MASK"][body].all()
    assert not f["XQC_SOURCE_KIND"].any()
    assert not out.sweeps[0].xqc_diagnostics["geometry"]["doppler_action_verified"]
    if mode == "audit":
        assert not f["XQC_REJECTED_MASK"].any()
    else:
        assert f["XQC_WITHHELD_MASK"][body].all()
        assert not f["REFLECTIVITY_ELIGIBLE_FOR_CR"][body].any()
        if mode == "quarantine":
            assert (f["QC_ACTION"][body] == 3).all()
    np.testing.assert_array_equal(cut.fields["DBZH"], before)


def test_long_fixed_km_rainband_cannot_restart_as_a_far_radial_tail():
    s, _ = scene("line")
    r = np.arange(250.0, 250001.0, 250.0)
    az = s.azimuth
    diff = (az[:, None] - 100.0 + 180.0) % 360.0 - 180.0
    body = (abs(diff) <= np.degrees(np.arctan2(2500.0, r))[None, :]) & (r[None, :] >= 5000)
    z = np.zeros(body.shape, "float32")
    z[body] = np.broadcast_to(20 + 11 * np.sin(r / 1300), body.shape)[body]
    sn = np.where(body, 15.0, -2.0).astype("float32")
    available = np.ones(body.shape, bool)
    s = replace(
        s,
        ranges=r,
        fields={"DBZH": z, "SNR": sn},
        available={"DBZH": available, "SNR": available},
        no_echo=np.zeros(body.shape, bool),
    )
    ev = detect(s, MorphologyPolicy())
    assert body.any() and not ev.mask.any()


def test_core_resource_abstention_preserves_other_evidence_and_never_partial_shape_actions():
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut

    from .helpers import config, fixture

    v, _ = fixture("flat")
    c = config(
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
        morphology={"maximum_work": 1},
    )
    ev = evaluate_cut(v.sweeps[0], v.metadata, c)
    assert ev.record["status"] == "DEGRADED_MORPHOLOGY_RESOURCE_LIMIT"
    assert not ev.arrays["XQC_MORPHOLOGY_MASK"].any()
    assert not ev.arrays["XQC_PROPOSED_MASK"].any()


def test_morphology_keeps_existing_action_budget():
    from rainpulse_algo.multiband.xqc_v2.core import Reason, evaluate_cut

    from .helpers import config, fixture

    v, _ = fixture("flat", rays=360)
    c = config(
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
        maximum_new_exclusion_fraction=0.00001,
        morphology=MorphologyPolicy().model_dump(),
    )
    ev = evaluate_cut(v.sweeps[0], v.metadata, c)
    assert ev.arrays["XQC_MORPHOLOGY_MASK"].any()
    assert ev.record["status"] == "ACTION_BUDGET_ABSTAINED"
    assert not ev.arrays["XQC_QUARANTINE_MASK"].any()
    assert np.any(ev.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET))


def test_native_edge_quantization_does_not_discard_a_persistent_radial_core():
    s, body = scene("line")
    z = s.fields["DBZH"].copy()
    sn = s.fields["SNR"].copy()
    neighbor = (s.ranges >= 5000) & (s.ranges <= 55000) & ((s.ranges // 5000) % 2 == 0)
    z[99, neighbor] = z[100, neighbor]
    sn[99, neighbor] = 15.0
    body[99, neighbor] = True
    s = replace(s, fields={**s.fields, "DBZH": z, "SNR": sn})
    ev = detect(s, MorphologyPolicy())
    assert ev.mask[body].all()


def test_joined_original_fan_must_be_retested_not_permanently_poisoned_by_track_history():
    s, body = scene("fan")
    z = s.fields["DBZH"].copy()
    sn = s.fields["SNR"].copy()
    prefix = (s.ranges >= 5000) & (s.ranges < 10000)
    z[100, prefix] = 0.0
    sn[100, prefix] = -2.0
    body[100, prefix] = False
    s = replace(s, fields={**s.fields, "DBZH": z, "SNR": sn})
    ev = detect(s, MorphologyPolicy())
    stable = body & (s.ranges[None, :] >= 10000)
    assert ev.mask[stable].all()
    assert not ev.mask[~body].any()


def test_missing_reflectivity_with_high_snr_is_not_a_quiet_range_bridge():
    s, _ = scene("line")
    available = dict(s.available)
    available["DBZH"] = s.available["DBZH"].copy()
    unknown = (s.ranges >= 15000) & (s.ranges < 20000)
    available["DBZH"][100, unknown | (s.ranges > 35000)] = False
    s = replace(s, available=available)
    assert not detect(s, MorphologyPolicy()).mask.any()


def test_unknown_gap_cannot_hide_across_physical_bucket_boundaries():
    s, _ = scene("line")
    available = dict(s.available)
    available["DBZH"] = s.available["DBZH"].copy()
    available["DBZH"][100, (s.ranges >= 14250) & (s.ranges < 15750)] = False
    ev = detect(replace(s, available=available), MorphologyPolicy())
    assert not any(o["range_begin_m"] < 14250 and o["range_end_m"] >= 15750 for o in ev.objects)


def test_irregular_native_edge_uses_local_footprint_uncertainty():
    s, body = scene("fan", bearing=104.0)
    keep = s.azimuth != 99.0
    fields = {k: v[keep].copy() for k, v in s.fields.items()}
    row = int(np.flatnonzero(s.azimuth[keep] == 98.0)[0])
    absent = (s.ranges >= 10000) & (s.ranges < 20000)
    fields["DBZH"][row, absent] = 0.0
    fields["SNR"][row, absent] = -2.0
    body = body[keep]
    body[row, absent] = False
    native = Sweep(
        "irregular",
        s.azimuth[keep],
        s.elevation[keep],
        s.ranges,
        fields,
        {k: v[keep] for k, v in s.available.items()},
        s.good[keep],
        s.gap_after[keep],
        s.ray_time_s[keep],
    )
    ev = detect(native, MorphologyPolicy())
    assert ev.mask[body].all()


def test_published_json_schema_accepts_explicit_morphology_policy():
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/x-qc-v2.schema.json"
        ).read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(
        {"morphology": MorphologyPolicy().model_dump(mode="json")}
    )


def test_default_morphology_protects_local_weather_proxy():
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut

    from .helpers import config, fixture

    v, _ = fixture("empty", rays=360, gates=240, dr=250.0)
    cut = v.sweeps[0]
    f = cut.fields
    body = (
        (abs((cut.azimuth_deg[:, None] - 104 + 180) % 360 - 180) <= 6)
        & (cut.range_m[None, :] >= 5000)
        & (cut.range_m[None, :] <= 55000)
    )
    f["DBZH"][body] = 25.0
    f["SNRH"][body] = 20.0
    f["RHOHV"][:] = 0.99
    f["ZDR"][:] = 0.3
    f["PHIDP"][:] = np.linspace(20, 22, 240)
    c = config(
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
        morphology={},
    )
    ev = evaluate_cut(cut, v.metadata, c)
    local = ev.arrays["XQC_LOCAL_WEATHER_MASK"] == 1
    assert local.any() and not (local & (ev.arrays["XQC_MORPHOLOGY_MASK"] == 1)).any()
    joint = config(**{**c.model_dump(), "morphology": {"local_weather_policy": "joint_review"}})
    reviewed = evaluate_cut(cut, v.metadata, joint)
    assert (local & (reviewed.arrays["XQC_MORPHOLOGY_MASK"] == 1)).any()
    assert reviewed.arrays["XQC_PROPOSED_MASK"].any()
    assert not reviewed.arrays["XQC_QUARANTINE_MASK"].any()
    assert reviewed.record["module_records"]["morphology"]["local_proxy_conflict_gates"] > 0


@pytest.mark.parametrize(
    "key,limit",
    [("maximum_objects", 20000), ("maximum_sweep_gates", 2000000), ("maximum_work", 50000000)],
)
def test_policy_cannot_override_hard_resource_ceiling(key, limit):
    with pytest.raises(ValueError):
        MorphologyPolicy(**{key: limit + 1})


def test_incremental_morphology_budget_does_not_withdraw_accepted_baseline(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import radial_source
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut

    from .helpers import config, fixture

    v, _ = fixture("flat", rays=360)
    prior = np.zeros(v.sweeps[0].fields["DBZH"].shape, bool)
    prior[0, :100] = True
    monkeypatch.setattr(
        radial_source,
        "detect",
        lambda *args, **kwargs: (prior.copy(), {"status": "EVALUATED", "source_gates": 100}),
    )
    cfg = config(
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
        maximum_new_exclusion_fraction=0.001,
    )
    baseline = evaluate_cut(v.sweeps[0], v.metadata, cfg)
    enhanced = evaluate_cut(
        v.sweeps[0], v.metadata, cfg.model_copy(update={"morphology": MorphologyPolicy()})
    )
    assert baseline.arrays["XQC_QUARANTINE_MASK"].sum() == 100
    assert enhanced.arrays["XQC_MORPHOLOGY_MASK"].any()
    np.testing.assert_array_equal(
        enhanced.arrays["XQC_QUARANTINE_MASK"], baseline.arrays["XQC_QUARANTINE_MASK"]
    )
    np.testing.assert_array_equal(
        enhanced.arrays["XQC_PROPOSED_MASK"], baseline.arrays["XQC_PROPOSED_MASK"]
    )
    assert enhanced.record["status"] == "DEGRADED_MORPHOLOGY_ACTION_BUDGET"
    assert enhanced.record["module_records"]["morphology"]["withheld_candidate_gates"] > 0


def test_missing_one_flank_cannot_be_compensated_by_the_other_flank():
    s, _ = scene("fan")
    available = {k: v.copy() for k, v in s.available.items()}
    unknown = np.arange(s.shape[1]) % 4 == 0
    available["DBZH"][91:94, unknown] = False
    available["SNR"][91:94, unknown] = False
    assert not detect(replace(s, available=available), MorphologyPolicy()).mask.any()
    sparse = np.arange(s.shape[1]) % 10 == 0
    available["DBZH"][91:94] = ~sparse
    available["SNR"][91:94] = ~sparse
    assert detect(replace(s, available=available), MorphologyPolicy()).mask.any()


@pytest.mark.parametrize(
    "bad",
    [
        {"scales_m": [1000]},
        {"scales_m": [5000, 5000]},
        {"scales_m": [2000, 4000, 6000, 8000, 10000]},
        {"levels_dbz": [5, 10, 15, 20, 25, 30, 35]},
        {"levels_dbz": [5, 5]},
        {"levels_dbz": [4]},
    ],
)
def test_published_schema_rejects_invalid_morphology_scales_and_levels(bad):
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/x-qc-v2.schema.json"
        ).read_text()
    )
    assert list(jsonschema.Draft202012Validator(schema).iter_errors({"morphology": bad}))


def test_missing_immediate_flank_uses_nearest_measured_exterior_without_treating_missing_as_quiet():
    s, body = scene("fan")
    available = {k: v.copy() for k, v in s.available.items()}
    available["DBZH"][93] = False
    available["SNR"][93] = False
    assert detect(replace(s, available=available), MorphologyPolicy()).mask[body].all()
    # The next actually measured shoulder is strong, so an even farther quiet
    # sample must never replace it to manufacture a contrast.
    fields = {k: v.copy() for k, v in s.fields.items()}
    fields["DBZH"][92] = fields["DBZH"][94]
    fields["SNR"][92] = 15.0
    assert not detect(replace(s, available=available, fields=fields), MorphologyPolicy()).mask.any()
    fields["SNR"][92] = -2.0
    assert not detect(replace(s, available=available, fields=fields), MorphologyPolicy()).mask.any()


def expanding_scene(*, bearing=100.0, drift=False, shrinking=False):
    s, _ = scene("line")
    offset = (s.azimuth[:, None] - bearing + 180) % 360 - 180
    progress = np.clip(np.floor((s.ranges - 5000) / 10000) / 4, 0, 1)[None, :]
    halfwidth = (6 - 5 * progress) if shrinking else (1 + 5 * progress)
    center = progress * 8 if drift else 0
    body = (
        (abs(offset - center) <= halfwidth)
        & (s.ranges[None, :] >= 5000)
        & (s.ranges[None, :] < 55000)
    )
    fields = {
        "DBZH": np.where(body, 20, 0).astype("float32"),
        "SNR": np.where(body, 15, -2).astype("float32"),
    }
    return replace(s, fields=fields, available={k: np.ones(s.shape, bool) for k in fields}), body


@pytest.mark.parametrize("bearing", [100.0, 359.0])
def test_expanding_fan_uses_complete_center_history_without_looser_fixed_edges(bearing):
    s, body = expanding_scene(bearing=bearing)
    assert not detect(s, MorphologyPolicy()).mask.any()
    p = MorphologyPolicy(version="x-polar-morphology-20261002-v2", expanding_fans_enabled=True)
    before = s.digest
    ev = detect(s, p)
    assert ev.mask[body].all() and not ev.mask[~body].any()
    assert any(o["kind"] == "expanding_fan" for o in ev.objects)
    assert s.digest == before


@pytest.mark.parametrize("kind", ["fixed_km", "curved", "blob"])
def test_expanding_mode_still_rejects_weather_counterexamples(kind):
    s, _ = scene(kind)
    p = MorphologyPolicy(version="x-polar-morphology-20261002-v2", expanding_fans_enabled=True)
    assert not detect(s, p).mask.any()


@pytest.mark.parametrize("kwargs", [{"drift": True}, {"shrinking": True}])
def test_expanding_mode_rejects_bending_or_narrowing_whole_envelopes(kwargs):
    s, _ = expanding_scene(**kwargs)
    p = MorphologyPolicy(version="x-polar-morphology-20261002-v2", expanding_fans_enabled=True)
    assert not detect(s, p).mask.any()


def test_expanding_mode_does_not_bypass_missing_flanks_weather_or_resource_barriers():
    s, body = expanding_scene()
    p = MorphologyPolicy(version="x-polar-morphology-20261002-v2", expanding_fans_enabled=True)
    avail = {k: v.copy() for k, v in s.available.items()}
    for v in avail.values():
        v[~body] = False
    assert not detect(replace(s, available=avail), p).mask.any()
    hard = np.ones(s.shape, bool)
    assert not detect(s, p, protected=hard).mask.any()
    with pytest.raises(ResourceLimit):
        detect(s, p.model_copy(update={"maximum_work": 1}))


def test_expanding_policy_requires_distinct_version_and_contract():
    import json
    from pathlib import Path

    import jsonschema
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MorphologyPolicy(expanding_fans_enabled=True)
    p = MorphologyPolicy(version="x-polar-morphology-20261002-v2", expanding_fans_enabled=True)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/x-qc-v2.schema.json"
        ).read_text()
    )
    jsonschema.Draft202012Validator(schema).validate({"morphology": p.model_dump(mode="json")})


@pytest.mark.parametrize("bearing,side", [(100.0, 1), (359.0, -1)])
def test_asymmetric_expanding_fan_requires_original_stable_edge(bearing, side):
    s, _ = expanding_scene(bearing=bearing)
    offset = (s.azimuth[:, None] - bearing + 180) % 360 - 180
    grow = np.clip(np.floor((s.ranges - 5000) / 10000) / 4, 0, 1)[None, :] * 8
    body = (side * offset >= -1) & (side * offset <= 1 + grow)
    body &= (s.ranges[None, :] >= 5000) & (s.ranges[None, :] < 55000)
    s = replace(
        s,
        fields={
            "DBZH": np.where(body, 20, 0).astype("float32"),
            "SNR": np.where(body, 15, -2).astype("float32"),
        },
    )
    v2 = MorphologyPolicy(version="x-polar-morphology-20261002-v2", expanding_fans_enabled=True)
    assert not detect(s, v2).mask.any()
    v3 = MorphologyPolicy(
        version="x-polar-morphology-20261002-v3",
        expanding_fans_enabled=True,
        anchored_fans_enabled=True,
    )
    ev = detect(s, v3)
    assert ev.mask[body].all() and not ev.mask[~body].any()
    assert any(o["kind"] == "anchored_expanding_fan" for o in ev.objects)
    assert all(
        min(o["left_edge_excursion_native_footprints"], o["right_edge_excursion_native_footprints"])
        <= 1.05
        for o in ev.objects
    )


@pytest.mark.parametrize("kind", ["curved", "fixed_km", "blob"])
def test_anchor_branch_does_not_accept_weather_by_following_a_far_tail(kind):
    s, _ = scene(kind)
    p = MorphologyPolicy(
        version="x-polar-morphology-20261002-v3",
        expanding_fans_enabled=True,
        anchored_fans_enabled=True,
    )
    assert not detect(s, p).mask.any()


def test_anchor_identity_and_observed_weather_barriers_remain_required():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MorphologyPolicy(version="x-polar-morphology-20261002-v2", anchored_fans_enabled=True)
    with pytest.raises(ValidationError):
        MorphologyPolicy(version="x-polar-morphology-20261002-v3", anchored_fans_enabled=True)
    s, body = expanding_scene()
    p = MorphologyPolicy(
        version="x-polar-morphology-20261002-v3",
        expanding_fans_enabled=True,
        anchored_fans_enabled=True,
    )
    protected = np.zeros(s.shape, bool)
    protected[:, (s.ranges >= 24000) & (s.ranges < 36000)] = True
    ev = detect(s, p, protected=protected)
    assert not ev.mask[protected].any()
    available = {k: v.copy() for k, v in s.available.items()}
    for v in available.values():
        v[~body] = False
    assert not detect(replace(s, available=available), p).mask.any()


def test_anchor_policy_json_contract_requires_both_version_and_parent_branch():
    import json
    from pathlib import Path

    import jsonschema

    p = MorphologyPolicy(
        version="x-polar-morphology-20261002-v3",
        expanding_fans_enabled=True,
        anchored_fans_enabled=True,
    )
    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/x-qc-v2.schema.json"
        ).read_text()
    )
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate({"morphology": p.model_dump(mode="json")})
    for key, value in [
        ("version", "x-polar-morphology-20261002-v2"),
        ("expanding_fans_enabled", False),
    ]:
        invalid = p.model_dump(mode="json")
        invalid[key] = value
        with pytest.raises(jsonschema.ValidationError):
            validator.validate({"morphology": invalid})


def test_anchor_does_not_follow_both_moving_edges_even_if_width_increases():
    s, _ = expanding_scene(drift=True)
    p = MorphologyPolicy(
        version="x-polar-morphology-20261002-v3",
        expanding_fans_enabled=True,
        anchored_fans_enabled=True,
    )
    assert not detect(s, p).mask.any()
