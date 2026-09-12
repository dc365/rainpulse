from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
from rainpulse_algo.radar.qc_input import open_qc_input

from .test_radar_qc import synthetic_normalized_fixture

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/qc/fujian-qc-opensource-v1.yaml"
FLAGS = ROOT / "configs/qc/flag-definitions-v2.yaml"


def field_case():
    shape = (360, 160)
    dbzh = np.full(shape, -15.0, dtype="float32")
    dbzh[40:75, 25:70] = 38.0
    moments = {
        "RHOHV": np.full(shape, 0.99, dtype="float32"),
        "ZDR": np.ones(shape, dtype="float32"),
        "PHIDP": np.broadcast_to(np.arange(shape[1]) * 0.3, shape).astype("float32"),
        "SNR": np.full(shape, 25.0, dtype="float32"),
    }
    return dbzh, moments


def test_adapter_preserves_native_order_and_readonly_source():
    dbzh, moments = field_case()
    order = np.roll(np.arange(360), 77)
    source = synthetic_normalized_fixture(
        dbzh[order],
        azimuth_deg=order,
        moments={name: value[order] for name, value in moments.items()},
    )
    before = dict(source)
    native = adapt_sweep(open_qc_input(source).root, "sweep_000", OpenSourceQCProfile())
    np.testing.assert_array_equal(native.restore(native.fields["DBZH"]), dbzh[order])
    assert native.full_ppi
    assert not native.fields["DBZH"].flags.writeable
    assert source == before


def test_sector_cannot_wrap_support_across_north_or_outer_boundary():
    az = np.r_[np.arange(350, 360), np.arange(0, 20)]
    objects = synthetic_normalized_fixture(np.full((30, 40), 20, dtype="float32"), azimuth_deg=az)
    native = adapt_sweep(open_qc_input(objects).root, "sweep_000", OpenSourceQCProfile())
    support = native.support(np.ones(native.shape, bool), 2, 2)
    assert not native.full_ppi
    assert not support[:2].any() and not support[-2:].any()
    assert support[4:-4, 4:-4].all()


def test_invalid_rho_is_unavailable_not_clipped_or_imputed():
    dbzh, moments = field_case()
    moments["RHOHV"][50, 50] = 1.5
    objects = synthetic_normalized_fixture(dbzh, moments=moments)
    native = adapt_sweep(open_qc_input(objects).root, "sweep_000", OpenSourceQCProfile())
    assert not native.field_available["RHOHV"][50, 50]
    assert native.fields["RHOHV"][50, 50] == 1.5


def test_profile_is_strict_and_does_not_accept_station_deletion_rules():
    with pytest.raises(ValueError):
        OpenSourceQCProfile.model_validate({"rfi": {"radars": ["z9598"]}})
    with pytest.raises(ValueError):
        OpenSourceQCProfile.model_validate({"phase": {"window_gates": 6}})


def test_real_libraries_keep_uniform_rain_missing_and_no_rain_distinct():
    profile = load_qc_profile(PROFILE, FLAGS)
    dbzh, moments = field_case()
    dbzh[0:10, 5:20] = np.nan
    objects = synthetic_normalized_fixture(dbzh, moments=moments)
    before = dict(objects)
    result = apply_basic_qc(objects, profile)
    sweep = result.sweeps[0]
    assert objects == before
    assert np.all(sweep.optional_qc_fields["QC_ACTION"][0:10, 5:20] == 3)
    assert sweep.optional_qc_fields["QC_ACTION"][150, 80] == 0
    assert sweep.dbzh_qc[150, 80] == -15.0
    assert sweep.optional_qc_fields["REFLECTIVITY_TRUST_MASK"][50:65, 35:60].all()
    assert result.summary["engine"] == "open_source"
    assert result.summary["operational_eligible"] is False
    assert result.summary["libraries"] == {"arm_pyart": "2.2.5", "wradlib": "2.9.5"}


def test_qc_store_roundtrip_and_reject_flag_invariant():
    import zarr
    from zarr.storage import MemoryStore

    from rainpulse_algo.radar.qc_engine.validation import validate_sweep
    from rainpulse_algo.radar.qc_zarr import build_validated_qc_zarr_store

    profile = load_qc_profile(PROFILE, FLAGS)
    dbzh, moments = field_case()
    objects = synthetic_normalized_fixture(dbzh, moments=moments)
    result = apply_basic_qc(objects, profile)
    encoded, _ = build_validated_qc_zarr_store(
        objects,
        result,
        asset_id="00000000-0000-4000-8000-000000000001",
        normalized_volume_uri="s3://rainpulse/test/normalized.zarr",
    )
    store = MemoryStore()
    store.update(encoded)
    root = zarr.open_group(store=store, mode="a")
    assert root.attrs["qc_engine"] == "open_source"
    assert root.attrs["qc_parameters_sha256"] == profile.parameters_hash
    group = root["sweep_000"]
    group["QC_ACTION"][50, 50] = 2
    with pytest.raises(ValueError):
        validate_sweep(group, root.attrs)


def test_weather_support_does_not_resurrect_corrupted_sensor_values():
    from rainpulse_algo.radar.qc_engine.algorithms import library_evidence
    from rainpulse_algo.radar.qc_engine.decision import Action, Reason, decide

    dbzh, moments = field_case()
    moments["RHOHV"][:] = 0.3
    objects = synthetic_normalized_fixture(dbzh, moments=moments)
    profile = load_qc_profile(PROFILE, FLAGS)
    native = adapt_sweep(open_qc_input(objects).root, "sweep_000", profile)
    evidence = library_evidence(native, profile)
    # This is the smooth-low-rho counterexample to a universal fuzzy-score gate.
    assert evidence.arrays["METEO_SCORE"][55, 45] > 0.25
    radial = np.zeros(native.shape, bool)
    radial[50:60, 35:55] = True
    result = decide(
        native, evidence, profile, weather_support=np.ones(native.shape), rfi_candidate=radial
    )
    assert result.arrays["QC_ACTION"][55, 45] == Action.REJECT
    assert result.arrays["WEATHER_SUPPORTED_MASK"][55, 45] == 1
    assert result.arrays["QC_DECISION_REASON"][55, 45] & Reason.WEATHER_SUPPORT_CONFLICT
    assert result.arrays["QPE_ELIGIBLE_MASK"][55, 45] == 0
    assert np.isnan(result.arrays["DBZH_USABLE"][55, 45])
    # Healthy no-rain remains valid no-rain, even if the weak-signal polar values are low.
    assert result.arrays["QC_ACTION"][180, 80] == Action.KEEP


def test_missing_polarimetry_is_not_manufactured_or_hard_rejected():
    profile = load_qc_profile(PROFILE, FLAGS)
    dbzh, _ = field_case()
    result = apply_basic_qc(synthetic_normalized_fixture(dbzh), profile).sweeps[0]
    assert not result.optional_qc_fields["METEO_SCORE_AVAILABLE_MASK"].any()
    assert np.isnan(result.optional_qc_fields["METEO_SCORE"]).all()
    assert not np.any(result.optional_qc_fields["QC_ACTION"] == 2)
    assert result.optional_qc_fields["REFLECTIVITY_TRUST_MASK"][55, 45] == 1


def test_north_boundary_and_rotated_scan_give_same_library_decisions():
    profile = load_qc_profile(PROFILE, FLAGS)
    dbzh, moments = field_case()
    baseline = apply_basic_qc(synthetic_normalized_fixture(dbzh, moments=moments), profile)
    order = np.roll(np.arange(360), 137)
    rotated = apply_basic_qc(
        synthetic_normalized_fixture(
            dbzh[order],
            azimuth_deg=order,
            moments={key: values[order] for key, values in moments.items()},
        ),
        profile,
    )
    np.testing.assert_array_equal(baseline.sweeps[0].qc_flags[order], rotated.sweeps[0].qc_flags)


def test_phase_products_never_bridge_a_missing_or_rejected_segment():
    profile = load_qc_profile(PROFILE, FLAGS)
    dbzh, moments = field_case()
    dbzh[40:75, 46:49] = np.nan
    result = apply_basic_qc(synthetic_normalized_fixture(dbzh, moments=moments), profile).sweeps[0]
    mask = result.optional_qc_fields["KDP_OS_AVAILABLE_MASK"]
    assert not mask[:, 46:49].any()
    assert not np.isfinite(result.optional_qc_fields["KDP_OS"][:, 46:49]).any()
    assert not result.optional_qc_fields["ATTENUATION_OS_AVAILABLE_MASK"].any()


def test_supplement_is_disabled_in_open_source_baseline_and_local_in_candidate():
    from rainpulse_algo.radar.qc_engine.radial import local_radial_candidates

    profile = load_qc_profile(PROFILE, FLAGS)
    shape = (360, 320)
    ranges = (np.arange(shape[1]) + 1) * 250.0
    dbzh = np.full(shape, 0.0, dtype="float32")
    dbzh[100:130, 30:250] = 12 + 20 * np.log10(ranges[30:250] / 10000)
    native = adapt_sweep(
        open_qc_input(synthetic_normalized_fixture(dbzh)).root, "sweep_000", profile
    )
    mask, _ = local_radial_candidates(native, profile.rfi)
    assert not mask.any()
    mask, _ = local_radial_candidates(native, profile.rfi.model_copy(update={"enabled": True}))
    assert mask[110:120, 80:180].any()
    assert not mask[:, :30].any() and not mask[:, 250:].any()
    assert not mask[200:].any()


def test_field_geometry_mismatch_is_rejected_before_classification():
    import zarr
    from zarr.storage import MemoryStore

    dbzh, moments = field_case()
    objects = synthetic_normalized_fixture(dbzh, moments=moments)
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="a")
    root["sweep_000/RHOHV"].attrs["range_coordinates"] = [0, 1]
    zarr.consolidate_metadata(store)
    with pytest.raises(ValueError, match="co-registered"):
        adapt_sweep(open_qc_input(dict(store)).root, "sweep_000", OpenSourceQCProfile())


def test_context_causality_is_checked_against_actual_artifact_time():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from rainpulse_algo.radar.qc_engine.context import validate_context_identity

    current = SimpleNamespace(
        attrs={"radar_id": "a", "volume_end_time_utc": "2026-08-28T00:20:00Z"}
    )
    other = SimpleNamespace(attrs={"radar_id": "a", "volume_end_time_utc": "2026-08-28T00:25:00Z"})
    item = SimpleNamespace(radar_id="a", input_uri="s3://radar/a")
    reason = validate_context_identity(
        other,
        item,
        role="temporal",
        current_root=current,
        cutoff=datetime(2026, 8, 28, 0, 30, tzinfo=UTC),
        config=OpenSourceQCProfile().context,
    )
    assert reason == "future_context_disallowed"


def test_new_profile_schema_and_cause_flags_are_consistent():
    import json

    import yaml
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "configs/schemas/radar-qc-open-source.schema.json").read_text())
    for path in (PROFILE, PROFILE.with_name("fujian-qc-opensource-rfi-v1.yaml")):
        value = yaml.safe_load(path.read_text())
        Draft202012Validator(schema).validate(value)
        assert load_qc_profile(path, FLAGS).flag_masks["NON_METEOROLOGICAL"] == 32768
