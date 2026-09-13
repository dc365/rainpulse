"""Synthetic mechanism regression, NOT acceptance of the four real screenshot volumes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.algorithms import library_evidence
from rainpulse_algo.radar.qc_engine.decision import decide
from rainpulse_algo.radar.qc_engine.objects import radial_objects
from rainpulse_algo.radar.qc_input import open_qc_input

from .test_radar_qc import synthetic_normalized_fixture

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/qc/fujian-qc-rfi-objects-v2.yaml"
FLAGS = ROOT / "configs/qc/flag-definitions-v2.yaml"


def profile():
    return load_qc_profile(PROFILE, FLAGS)


def scene(
    *,
    missing_background=False,
    thin=False,
    dropouts=False,
    rho=0.3,
    snr=25.0,
    missing_pol=False,
    other_directions=False,
):
    shape = (360, 640)
    ranges = (np.arange(shape[1]) + 1) * 250.0
    footprint = np.zeros(shape, bool)
    footprint[100 : 101 if thin else 130, 80:500] = True
    if other_directions:
        footprint[200:215, 80:500] = True
        footprint[300:330, 80:500] = True
    dbzh = np.full(shape, np.nan if missing_background else -15.0, "float32")
    ramp = np.broadcast_to(12 + 20 * np.log10(ranges / 10000), shape)
    dbzh[footprint] = ramp[footprint]
    moments = {
        "RHOHV": np.full(shape, 0.99, "float32"),
        "ZDR": np.ones(shape, "float32"),
        "PHIDP": np.full(shape, 20.0, "float32"),
        "SNR": np.full(shape, snr, "float32"),
    }
    moments["RHOHV"][footprint] = rho
    if missing_background or thin:
        for value in moments.values():
            value[~footprint] = np.nan
    if dropouts:
        holes = footprint & (np.arange(shape[1])[None, :] % 30 == 0)
        dbzh[holes] = np.nan
        for value in moments.values():
            value[holes] = np.nan
    if missing_pol:
        for field in ("RHOHV", "ZDR", "PHIDP"):
            moments.pop(field)
    target = footprint.copy()
    target[:, :105] = False
    target[:, 475:] = False
    target &= np.isfinite(dbzh)
    obj = synthetic_normalized_fixture(dbzh, range_m=ranges, moments=moments)
    return obj, target


def evaluate(objects, *, configured=None, weather=None, temporal=None, samples=None):
    configured = configured or profile()
    native = adapt_sweep(open_qc_input(objects).root, "sweep_000", configured)
    ev = library_evidence(native, configured)
    radial = radial_objects(native, configured.rfi_objects)
    decision = decide(
        native,
        ev,
        configured,
        weather_support=weather,
        object_evidence=radial,
        temporal_persistence=temporal,
        temporal_samples=samples,
    )
    return native, ev, radial, decision


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(),
        dict(missing_background=True),
        dict(dropouts=True),
        dict(thin=True),
        dict(other_directions=True),
    ],
)
def test_mechanism_failures_are_rejected_without_zero_fill(kwargs):
    obj, target = scene(**kwargs)
    before = dict(obj)
    native, ev, radial, decision = evaluate(obj)
    assert np.mean(radial.candidate[target]) > 0.98
    assert np.mean(decision.arrays["QC_ACTION"][target] == 2) > 0.98
    assert not decision.arrays["QPE_ELIGIBLE_MASK"][target].any()
    assert not np.isfinite(decision.arrays["DBZH_USABLE"][~native.field_available["DBZH"]]).any()
    assert obj == before
    if kwargs.get("thin"):
        assert ev.arrays["OS_POL_RAW_MOMENT_COUNT"][target].min() == 3
        assert not ev.arrays["OS_POL_TEXTURE_MOMENT_COUNT"][target].any()


def test_high_correlation_weather_is_not_removed_by_geometry_alone():
    obj, target = scene(rho=0.99)
    _, _, _, result = evaluate(obj)
    assert not (result.arrays["QC_ACTION"][target] == 2).any()
    assert not result.arrays["RFI_QUARANTINE_MASK"][target].any()
    assert result.arrays["QPE_ELIGIBLE_MASK"][target].all()


def test_no_pol_or_low_snr_is_not_confirmed_as_rfi():
    for kwargs in (dict(missing_pol=True), dict(snr=1.0)):
        obj, target = scene(**kwargs)
        _, _, _, result = evaluate(obj)
        assert not (result.arrays["RFI_RISK_STATE"][target] == 3).any()
        if "snr" in kwargs:
            assert not result.arrays["QPE_ELIGIBLE_MASK"][target].any()


def test_quarantine_is_not_reported_as_confirmed_rejection():
    obj, target = scene(rho=0.88)
    _, _, _, result = evaluate(obj)
    assert result.arrays["RFI_QUARANTINE_MASK"][target].all()
    assert not (result.arrays["QC_ACTION"][target] == 2).any()
    assert not (result.flags[target] & (1 << 15)).any()
    assert not result.arrays["REFLECTIVITY_TRUST_MASK"][target].any()
    assert not result.arrays["QPE_ELIGIBLE_MASK"][target].any()


def test_time_evidence_changes_supported_decisions_but_never_vetoes_new_severe_rfi():
    obj, target = scene(rho=0.88)
    shape = target.shape
    count = np.full(shape, 3, "uint8")
    _, _, _, low = evaluate(obj, temporal=np.zeros(shape), samples=count)
    _, _, _, high = evaluate(obj, temporal=np.ones(shape), samples=count)
    assert not (low.arrays["QC_ACTION"][target] == 2).any()
    assert (high.arrays["QC_ACTION"][target] == 2).all()
    _, _, _, absent = evaluate(obj, temporal=np.ones(shape), samples=np.zeros(shape, "uint8"))
    assert not (absent.arrays["QC_ACTION"][target] == 2).any()
    severe, target = scene()
    _, _, _, result = evaluate(severe, temporal=np.zeros(shape), samples=count)
    assert (result.arrays["QC_ACTION"][target] == 2).all()


def test_weather_support_does_not_restore_polluted_measurement():
    obj, target = scene()
    _, _, _, result = evaluate(obj, weather=np.ones(target.shape))
    assert result.arrays["RFI_MIXED_MASK"][target].all()
    assert not result.arrays["QPE_ELIGIBLE_MASK"][target].any()


def test_long_gap_keeps_distinct_objects_and_missing_is_never_connected_as_measurement():
    obj, _ = scene(thin=True)
    native = adapt_sweep(open_qc_input(obj).root, "sweep_000", profile())
    fields = {k: v.copy() for k, v in native.fields.items()}
    available = {k: v.copy() for k, v in native.field_available.items()}
    for key in fields:
        fields[key][:, 270:300] = np.nan
        available[key][:, 270:300] = False
    native = replace(native, fields=fields, field_available=available)
    result = radial_objects(native, profile().rfi_objects)
    assert result.arrays["RFI_OBJECT_ID"][100, 150] != result.arrays["RFI_OBJECT_ID"][100, 400]
    assert not result.arrays["RFI_OBJECT_ID"][:, 270:300].any()


def test_confirmed_mask_and_quarantine_survive_serialization(tmp_path):
    from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store, validate_qc_zarr_store

    obj, target = scene(rho=0.88)
    result = apply_basic_qc(obj, profile())
    assert not result.sweeps[0].optional_qc_fields["QPE_ELIGIBLE_MASK"][target].any()
    stored = build_qc_zarr_store(obj, result, asset_id="review", normalized_volume_uri="local")
    assert validate_qc_zarr_store(stored)["valid_gate_count"] > 0


def test_v1_parameter_identity_is_unchanged():
    import hashlib
    import json

    old = load_qc_profile(ROOT / "configs/qc/fujian-qc-opensource-v1.yaml", FLAGS)
    data = old.model_dump(mode="json")
    data.pop("rfi_objects", None)
    assert (
        old.parameters_hash
        == hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_profile_requires_explicit_coordinated_versions():
    from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile

    for bad in (
        {"rfi_objects": {}},
        {"pipeline_version": "qc-opensource-2.0.0"},
        {"rfi_objects": {"station_ids": ["Z9598"]}},
    ):
        with pytest.raises(ValueError):
            OpenSourceQCProfile.model_validate(bad)


def test_north_seam_and_original_order_are_equivariant():
    from .test_radar_qc import synthetic_normalized_fixture

    obj, _ = scene()
    native, _, reference, _ = evaluate(obj)
    # Same measured rays, changed angular origin and storage order.
    order = np.roll(np.arange(360), 73)
    shifted = synthetic_normalized_fixture(
        native.fields["DBZH"][order],
        azimuth_deg=(native.azimuth[order] + 250) % 360,
        range_m=native.ranges,
        moments={k: v[order] for k, v in native.fields.items() if k != "DBZH"},
    )
    rotated, _, result, _ = evaluate(shifted)
    np.testing.assert_array_equal(rotated.restore(result.candidate), reference.candidate[order])
    assert len(result.records) == 1
    assert result.records[0]["azimuth_width_deg"] < 35


def test_sector_gap_and_duplicate_rays_never_join_objects():
    obj, _ = scene()
    native, _, _, _ = evaluate(obj)
    # Remove a middle group of angles from the fan; no interpolation across it.
    order = np.r_[np.arange(80, 112), np.arange(118, 150)]
    values = synthetic_normalized_fixture(
        native.fields["DBZH"][order],
        azimuth_deg=native.azimuth[order],
        range_m=native.ranges,
        moments={k: v[order] for k, v in native.fields.items() if k != "DBZH"},
    )
    _, _, result, _ = evaluate(values)
    assert len(result.records) == 2
    assert all(x["azimuth_width_deg"] < 20 for x in result.records)


def test_residual_propagation_is_bounded_and_stops_at_trusted_weather():
    obj, target = scene(rho=0.83, thin=True)
    native = adapt_sweep(open_qc_input(obj).root, "sweep_000", profile())
    fields = {k: v.copy() for k, v in native.fields.items()}
    fields["RHOHV"][100, 240:245] = 0.3
    fields["RHOHV"][100, 246] = 0.99  # cannot propagate through this healthy measurement
    native = replace(native, fields=fields)
    ev = library_evidence(native, profile())
    objects = radial_objects(native, profile().rfi_objects)
    result = decide(native, ev, profile(), object_evidence=objects)
    assert result.arrays["RFI_RESIDUAL_PROMOTED_MASK"][100, 245] == 1
    assert result.arrays["RFI_RESIDUAL_PROMOTED_MASK"][100, 247] == 0
    assert result.arrays["QC_ACTION"][100, 246] != 2
    assert result.arrays["RFI_QUARANTINE_MASK"][100, 260] == 1


def test_full_circle_and_small_low_rho_cells_are_not_radial_objects():
    obj, _ = scene()
    native, _, _, _ = evaluate(obj)
    fields = {k: v.copy() for k, v in native.fields.items()}
    for k in fields:
        fields[k][:] = fields[k][110]
    ring = replace(native, fields=fields)
    assert not radial_objects(ring, profile().rfi_objects).candidate.any()
    fields = {k: v.copy() for k, v in native.fields.items()}
    fields["DBZH"][:] = -15
    fields["DBZH"][100:106, 110:125] = 45.0
    small = replace(native, fields=fields)
    assert not radial_objects(small, profile().rfi_objects).candidate.any()


def test_resource_budget_fails_explicitly_not_with_a_partial_mask():
    obj, _ = scene(other_directions=True)
    native, _, _, _ = evaluate(obj)
    config = profile().rfi_objects.model_copy(update={"maximum_objects": 1})
    with pytest.raises(ValueError, match="budget"):
        radial_objects(native, config)


def test_long_range_axis_edge_does_not_leave_an_artificial_tail():
    obj, _ = scene(thin=True)
    native, _, _, _ = evaluate(obj)
    fields = {k: v.copy() for k, v in native.fields.items()}
    for k in fields:
        fields[k][100, 500:] = fields[k][100, 499]
    fields["DBZH"][100, 500:] = 12 + 20 * np.log10(native.ranges[500:] / 10000)
    avail = {k: np.isfinite(v) for k, v in fields.items()}
    native = replace(native, fields=fields, field_available=avail)
    result = radial_objects(native, profile().rfi_objects)
    assert result.candidate[100, -1]


def test_linked_real_observation_is_quarantined_without_becoming_an_immediate_seed():
    obj, _ = scene(thin=True, rho=0.88)
    native, _, _, _ = evaluate(obj)
    fields = {k: v.copy() for k, v in native.fields.items()}
    # A measured echo slightly below the ray shape is a link, not a fabricated measurement.
    fields["DBZH"][100, 260] -= 35
    fields["DBZH"][100, 260] = 6
    changed = replace(native, fields=fields)
    configured = profile().model_copy(
        update={"rfi_objects": profile().rfi_objects.model_copy(update={"local_window_m": 750.0})}
    )
    evidence = library_evidence(changed, configured)
    objects = radial_objects(changed, configured.rfi_objects)
    decision = decide(changed, evidence, configured, object_evidence=objects)
    linked = objects.arrays["RFI_LINKED_OBSERVATION_MASK"].astype(bool)
    assert linked.any()
    assert not (decision.arrays["QC_ACTION"][linked] == 2).any()
    assert decision.arrays["RFI_QUARANTINE_MASK"][linked].all()
    assert not decision.arrays["QPE_ELIGIBLE_MASK"][linked].any()


def test_serialized_temporal_and_object_evidence_rejects_inconsistent_identity():
    import zarr
    from zarr.storage import MemoryStore

    from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store, validate_qc_zarr_store

    obj, _ = scene(thin=True)
    result = apply_basic_qc(obj, profile())
    objects = build_qc_zarr_store(obj, result, asset_id="review", normalized_volume_uri="local")
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store, mode="a")
    root["sweep_000/TEMPORAL_CANDIDATE_PERSISTENCE"][100, 200] = 1.0
    zarr.consolidate_metadata(store)
    with pytest.raises(ValueError, match="zero samples"):
        validate_qc_zarr_store(dict(store))


def test_single_volume_review_exports_actual_v2_variant_and_separate_quarantine(tmp_path):
    from rainpulse_algo.radar.qc_engine.review import compare_case
    from rainpulse_algo.worker.object_store import artifact_sha256

    obj, _ = scene(rho=0.88)
    folder = tmp_path / "input.zarr"
    for key, value in obj.items():
        file = folder / key
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(value)
    report = compare_case(
        {
            "case_id": "synthetic-only",
            "partition": "development",
            "process_id": "synthetic",
            "normalized_zarr": "input.zarr",
            "input_sha256": artifact_sha256(obj),
            "rfi_objects": True,
        },
        manifest_root=tmp_path,
        inspect_rays=(110,),
    )
    assert report["profiles"]["rfi_objects_v2"]["pipeline_version"] == "qc-opensource-2.0.0"
    sweep = report["sweeps"][0]
    method = sweep["methods"]["rfi_objects_v2"]
    assert method["quarantined_observed_gates"] > 0
    assert method["quantitative_coverage_fraction"] < 1
    assert "measurement_metrics" not in method  # no fabricated labels/skill
    assert "rfi_objects_v2" in sweep["radials"][0]["variants"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"thin": True},
        {"dropouts": True},
        {"missing_background": True},
        {"other_directions": True},
    ],
)
def test_full_injected_footprint_edges_are_not_silently_left_quantitative(kwargs):
    obj, _ = scene(**kwargs)
    native, _, objects, result = evaluate(obj)
    whole = np.isfinite(native.fields["DBZH"]) & (native.fields["DBZH"] >= 5)
    assert objects.candidate[whole].all()
    assert not result.arrays["QPE_ELIGIBLE_MASK"][whole].any()
    # Boundary hypotheses are counted separately, NOT reported as 100% confirmed recall.
    assert result.arrays["RFI_BOUNDARY_OBSERVATION_MASK"].any()
    assert result.arrays["RFI_QUARANTINE_MASK"][whole].any()


def test_boundary_recovery_stops_at_unobserved_or_healthy_measurements():
    obj, _ = scene(thin=True)
    native, _, _, _ = evaluate(obj)
    fields = {k: v.copy() for k, v in native.fields.items()}
    fields["RHOHV"][100, 83] = 0.99
    changed = replace(native, fields=fields)
    evidence = radial_objects(changed, profile().rfi_objects)
    assert not evidence.arrays["RFI_BOUNDARY_OBSERVATION_MASK"][100, 83]
    assert not evidence.arrays["RFI_OBJECT_ID"][100, 82]
