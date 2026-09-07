import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import yaml
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.attenuation import (
    ATTENUATION_CORRECTION_SHADOW_FIELD,
    ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD,
    ATTENUATION_SHADOW_MODULE_NAME,
    ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD,
    DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD,
    SPECIFIC_ATTENUATION_SHADOW_FIELD,
    AttenuationApplicabilityConfig,
    AttenuationCoefficientConfig,
    AttenuationProfile,
    load_attenuation_profile,
)
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.fmt import decode_fmt_volume
from rainpulse_algo.radar.health import assess_volume_health, load_radar_health_config
from rainpulse_algo.radar.phase_processing import (
    KDP_SHADOW_AVAILABLE_MASK_FIELD,
    KDP_SHADOW_FIELD,
    KDP_SHADOW_UNCERTAINTY_FIELD,
    PHASE_PROCESSING_MODULE_NAME,
    PHIDP_SHADOW_CORRECTED_FIELD,
    PHIDP_SHADOW_SEGMENT_INDEX_FIELD,
    PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD,
    PHIDP_SHADOW_UNWRAPPED_FIELD,
    load_phase_processing_profile,
)
from rainpulse_algo.radar.qc import (
    INTERFERENCE_TYPE_CODES,
    QCConfigError,
    QCInputError,
    _cross_radar_consistency_by_ray,
    _detect_radial_interference,
    _dual_pol_meteorological_probability,
    _higher_elevation_radial_extent_fractions,
    _long_range_saturated_radial_evidence,
    _radial_probability,
    _temporal_radial_persistence,
    _vertical_consistency_probabilities,
    apply_basic_qc,
    audit_long_range_saturated_radials,
    load_qc_profile,
)
from rainpulse_algo.radar.qc_geometry import (
    CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD,
    CROSS_RADAR_TRUSTED_SUPPORT_FIELD,
    VERTICAL_CONSISTENCY_AVAILABLE_MASK_FIELD,
    VERTICAL_HEIGHT_DIFFERENCE_M_FIELD,
    RadarBeamContext,
)
from rainpulse_algo.radar.qc_worker import _execute_basic_qc, _radial_candidates_by_sweep
from rainpulse_algo.radar.qc_zarr import (
    QCZarrWriteSettings,
    build_qc_zarr_store,
    build_validated_qc_zarr_store,
    validate_qc_zarr_store,
)
from rainpulse_algo.radar.zarr_volume import build_zarr_store
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_fmt_decoder import HEALTH_CONFIG, make_config, make_fmt_fixture
from .test_object_store import FakeMinio
from .test_radar_grid import FakeRasterDataset, dem_source_fixture, write_dem_runtime_fixture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp008-basic-v1.yaml"
RP042_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp042-fujian-evidence-v1.yaml"
RP043_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp043-fujian-radial-closure-v1.yaml"
RP047_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp047-fujian-radial-evidence-v1.yaml"
EVIDENCE_V2_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "fujian-qc-evidence-v2.yaml"
PHASE_PROCESSING_PROFILE = (
    REPOSITORY_ROOT / "configs" / "verification" / "fujian-phidp-kdp-shadow-v1.yaml"
)
ATTENUATION_PROFILE = (
    REPOSITORY_ROOT / "configs" / "verification" / "fujian-kdp-attenuation-shadow-v1.yaml"
)
FLAG_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"


def _linear_phidp_deg(
    range_m: np.ndarray,
    *,
    phi0_deg: float,
    kdp_deg_per_km: float,
) -> np.ndarray:
    return phi0_deg + 2.0 * kdp_deg_per_km * (range_m / 1000.0)


def _configured_attenuation_profile(
    *,
    coefficient_a: float = 0.04,
    exponent_b: float = 1.0,
    minimum_kdp_deg_per_km: float = 0.0,
    maximum_specific_attenuation_db_per_km: float = 5.0,
    maximum_correction_db: float = 20.0,
    maximum_blockage_fraction: float = 0.30,
    corrected_reflectivity_cap_dbz: float = 75.0,
) -> AttenuationProfile:
    return AttenuationProfile(
        profile_version="test-kdp-attenuation-shadow-v1",
        artifact_contract_version="1.0",
        source_normalized_radar_volume_contract_version="1.0",
        source_phase_processing_profile_version="fujian-phidp-kdp-shadow-v1",
        radar_band="S",
        method_name="kdp_path_integral",
        integration_scheme="two_way_trapezoidal",
        coefficients=AttenuationCoefficientConfig(
            source="explicit_shadow_coefficients",
            coefficient_a=coefficient_a,
            exponent_b=exponent_b,
            minimum_kdp_deg_per_km=minimum_kdp_deg_per_km,
            maximum_specific_attenuation_db_per_km=(maximum_specific_attenuation_db_per_km),
            maximum_correction_db=maximum_correction_db,
        ),
        applicability=AttenuationApplicabilityConfig(
            maximum_blockage_fraction=maximum_blockage_fraction,
            corrected_reflectivity_cap_dbz=corrected_reflectivity_cap_dbz,
            unavailable_segment_policy="split_and_reset",
        ),
        shadow_processing_enabled=True,
        worker_integration_enabled=False,
        required_gate=(
            "verified_s_band_attenuation_coefficients_and_"
            "independent_calibration_reference_required"
        ),
    )


def normalized_fixture(tmp_path: Path) -> dict[str, bytes]:
    radar_config = load_radar_config(make_config(tmp_path))
    volume = decode_fmt_volume(make_fmt_fixture(tmp_path, noise=(8717, 8744)), radar_config)
    health = assess_volume_health(volume, radar_config, load_radar_health_config(HEALTH_CONFIG))
    return build_zarr_store(
        volume,
        radar_config,
        asset_id=UUID("44444444-4444-4444-8444-444444444444"),
        source_uri="file:///fixtures/z9598.bin.bz2",
        health=health,
        provenance={"scan_id": "10000000-0000-4000-8000-000000000004"},
    )


def open_store(objects: dict[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update(objects)
    return zarr.open_group(store=store, mode="r")


def synthetic_normalized_fixture(
    dbzh: np.ndarray,
    *,
    azimuth_deg: np.ndarray | None = None,
    range_m: np.ndarray | None = None,
    elevation_deg: float = 0.5,
    moments: Mapping[str, np.ndarray] | None = None,
) -> dict[str, bytes]:
    ray_count, gate_count = dbzh.shape
    azimuth = (
        np.asarray(azimuth_deg, dtype="float32")
        if azimuth_deg is not None
        else np.linspace(0.0, 360.0, ray_count, endpoint=False, dtype="float32")
    )
    ranges = (
        np.asarray(range_m, dtype="float32")
        if range_m is not None
        else np.arange(250.0, 250.0 * (gate_count + 1), 250.0, dtype="float32")
    )
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.normalized-radar-volume",
            "asset_id": "11111111-1111-4111-8111-111111111111",
            "scan_id": "10000000-0000-4000-8000-000000000099",
            "radar_id": "z9999",
            "radar_config_version": "z9999-test-v1",
        }
    )
    root.create_dataset("sweep_number", data=np.array([0], dtype="int16"))
    root.create_dataset("sweep_start_ray_index", data=np.array([0], dtype="int32"))
    root.create_dataset(
        "sweep_end_ray_index",
        data=np.array([ray_count - 1], dtype="int32"),
    )
    sweep = root.create_group("sweep_000")
    sweep.create_dataset("azimuth", data=azimuth)
    sweep.create_dataset(
        "elevation",
        data=np.full(ray_count, elevation_deg, dtype="float32"),
    )
    sweep.create_dataset("ray_time", data=np.arange(ray_count, dtype="float64"))
    sweep.create_dataset(
        "horizontal_noise",
        data=np.zeros(ray_count, dtype="float32"),
    )
    sweep.create_dataset(
        "vertical_noise",
        data=np.zeros(ray_count, dtype="float32"),
    )
    sweep.create_dataset("range", data=ranges)
    sweep.create_dataset("DBZH", data=np.asarray(dbzh, dtype="float32"))
    for name, values in (moments or {}).items():
        array = np.asarray(values, dtype="float32")
        if array.shape != dbzh.shape:
            raise ValueError(f"synthetic moment {name} must match DBZH shape")
        sweep.create_dataset(name, data=array)
    objects = {str(key): bytes(value) for key, value in store.items()}
    objects["health/summary.json"] = json.dumps(
        {
            "schema_version": "1.0",
            "radar_id": "z9999",
            "health": "HEALTHY",
        }
    ).encode()
    return objects


def synthetic_multisweep_normalized_fixture(
    sweeps: tuple[dict[str, object], ...],
    *,
    root_attrs: Mapping[str, object] | None = None,
) -> dict[str, bytes]:
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    attrs = {
        "contract_name": "rainpulse.normalized-radar-volume",
        "asset_id": "11111111-1111-4111-8111-111111111112",
        "scan_id": "10000000-0000-4000-8000-000000000098",
        "radar_id": "z9999",
        "radar_config_version": "z9999-test-v1",
    }
    if root_attrs is not None:
        attrs.update(dict(root_attrs))
    root.attrs.update(attrs)
    ray_counts = [len(np.asarray(sweep["azimuth"], dtype="float32")) for sweep in sweeps]
    sweep_numbers = np.arange(len(sweeps), dtype="int16")
    starts = np.cumsum(np.array([0, *ray_counts[:-1]], dtype="int32"))
    ends = starts + np.array(ray_counts, dtype="int32") - 1
    root.create_dataset("sweep_number", data=sweep_numbers)
    root.create_dataset("sweep_start_ray_index", data=starts)
    root.create_dataset("sweep_end_ray_index", data=ends)
    for sweep_index, sweep_spec in enumerate(sweeps):
        azimuth = np.asarray(sweep_spec["azimuth"], dtype="float32")
        ranges = np.asarray(sweep_spec["range"], dtype="float32")
        dbzh = np.asarray(sweep_spec["dbzh"], dtype="float32")
        elevation_spec = np.asarray(sweep_spec["elevation"], dtype="float32")
        if dbzh.shape != (len(azimuth), len(ranges)):
            raise ValueError("synthetic multisweep DBZH must match azimuth and range")
        if elevation_spec.ndim == 0:
            elevation = np.full(len(azimuth), float(elevation_spec), dtype="float32")
        elif elevation_spec.shape == (len(azimuth),):
            elevation = elevation_spec.astype("float32", copy=False)
        else:
            raise ValueError("synthetic multisweep elevation must be scalar or per-ray")
        group = root.create_group(f"sweep_{sweep_index:03d}")
        group.create_dataset("azimuth", data=azimuth)
        group.create_dataset("elevation", data=elevation)
        group.create_dataset("ray_time", data=np.arange(len(azimuth), dtype="float64"))
        group.create_dataset(
            "horizontal_noise",
            data=np.zeros(len(azimuth), dtype="float32"),
        )
        group.create_dataset(
            "vertical_noise",
            data=np.zeros(len(azimuth), dtype="float32"),
        )
        group.create_dataset("range", data=ranges)
        group.create_dataset("DBZH", data=dbzh)
    objects = {str(key): bytes(value) for key, value in store.items()}
    objects["health/summary.json"] = json.dumps(
        {
            "schema_version": "1.0",
            "radar_id": str(attrs["radar_id"]),
            "health": "HEALTHY",
        }
    ).encode()
    return objects


def test_basic_qc_preserves_geometry_missing_and_no_rain_states(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)

    result = apply_basic_qc(normalized, profile)
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000001"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9598/scan/volume.zarr",
        provenance={"job_id": "50000000-0000-4000-8000-000000000002"},
    )
    summary = validate_qc_zarr_store(objects)
    root = open_store(objects)
    sweep = root["sweep_000"]

    assert root.attrs["contract_name"] == "rainpulse.qc-radar-volume"
    assert root.attrs["qc_profile"] == "rp008-basic-v1"
    assert summary["sweep_count"] == 2
    assert summary["ray_count"] == 4
    assert np.array_equal(root["sweep_start_ray_index"][:], np.array([0, 2]))
    assert sweep["DBZH_RAW"][0, 3] == pytest.approx(0.0)
    assert sweep["DBZH_QC"][0, 3] == pytest.approx(0.0)
    assert sweep["VALID_MASK"][0, 3] == 1
    assert sweep["VALID_MASK"][0, 0] == 0
    assert sweep["QC_FLAGS"][0, 0] & np.uint32(4096)
    assert np.isnan(sweep["P_AP"][:]).all()
    assert np.isnan(sweep["P_SEA_CLUTTER"][:]).all()
    assert np.isnan(sweep["QI_BLOCKAGE"][:]).all()
    assert "P_METEO_DUAL_POL" not in sweep
    assert "P_VERTICAL_CONSISTENCY" not in sweep
    assert "INTERFERENCE_TYPE" not in sweep
    assert result.summary["no_rain_gate_count"] > 0
    assert result.module_status("static_ground_clutter") == "skipped"
    assert result.module_status("sea_ap") == "skipped"
    assert "qc/summary.json" in objects


def test_basic_qc_preserves_sweep_when_dbzh_moment_is_absent(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    store = MemoryStore()
    store.update(normalized)
    root = zarr.open_group(store=store, mode="a")
    expected_shape = (
        len(root["sweep_001/azimuth"]),
        len(root["sweep_001/range"]),
    )
    del root["sweep_001/DBZH"]
    normalized = {str(key): bytes(value) for key, value in store.items()}

    result = apply_basic_qc(normalized, load_qc_profile(QC_CONFIG, FLAG_CONFIG))
    missing_sweep = result.sweeps[1]

    assert missing_sweep.dbzh_raw.shape == expected_shape
    assert np.isnan(missing_sweep.dbzh_raw).all()
    assert np.isnan(missing_sweep.dbzh_qc).all()
    assert np.count_nonzero(missing_sweep.valid_mask) == 0
    assert np.all(missing_sweep.qc_flags & np.uint32(4096))

    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000003"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9598/scan/volume.zarr",
    )
    assert validate_qc_zarr_store(objects)["sweep_count"] == 2


def test_qc_zarr_sparse_empty_chunks_round_trip() -> None:
    normalized = synthetic_normalized_fixture(np.full((96, 1024), np.nan, dtype="float32"))
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)

    result = apply_basic_qc(normalized, profile)
    objects, validation = build_validated_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000013"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9999/sparse/volume.zarr",
        write_settings=QCZarrWriteSettings(layout="64x512", write_empty_chunks=False),
    )
    root = open_store(objects)
    metadata = json.loads(objects["sweep_000/DBZH_QC/.zarray"])

    assert validation["field_chunk_layout"] == "64x512"
    assert validation["write_empty_chunks"] is False
    assert "fill_value" in metadata
    assert tuple(metadata["chunks"]) == (64, 512)
    assert "sweep_000/DBZH_QC/0.0" not in objects
    assert "sweep_000/VALID_MASK/0.0" not in objects
    assert np.isnan(root["sweep_000/DBZH_QC"][:]).all()
    assert np.count_nonzero(root["sweep_000/VALID_MASK"][:]) == 0


def test_radial_interference_flags_without_erasing_observation(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    store = MemoryStore()
    store.update(normalized)
    root = zarr.open_group(store=store, mode="a")
    values = root["sweep_000/DBZH"][:]
    values[0, :] = 35.0
    values[1, :] = 0.0
    root["sweep_000/DBZH"][:] = values
    normalized = {str(key): bytes(value) for key, value in store.items()}
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    radial = replace(
        profile.radial_interference,
        minimum_valid_gate_fraction=0.5,
        minimum_consecutive_gates=3,
        neighbour_difference_db=10.0,
    )
    profile = replace(profile, radial_interference=radial)

    result = apply_basic_qc(normalized, profile)
    flagged = result.sweeps[0]

    assert np.all(flagged.p_radial_interference[0] >= 0.8)
    assert np.all(flagged.qc_flags[0] & np.uint32(8))
    assert np.all(flagged.valid_mask[0] == 1)
    assert np.all(flagged.dbzh_qc[0] == 35.0)
    assert result.summary["radial_interference_ray_count"] >= 1


def test_radial_interference_detects_adjacent_long_range_saturated_rays(
    tmp_path: Path,
) -> None:
    """A contiguous interference fan must not hide behind similar neighbours."""
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((12, 600), np.nan, dtype="float32")

    # Ordinary meteorological echoes have limited radial support in this fixture.
    dbzh[:, :180] = 30.0
    # Z9591-like constant-power interference rises with range after calibration.
    dbzh[4:10, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    valid = np.isfinite(dbzh)

    probability, flagged_count = _radial_probability(
        dbzh,
        valid,
        profile.radial_interference,
    )

    assert flagged_count == 6
    assert np.all(probability[4:10, :] >= profile.radial_interference.flag_probability)
    assert np.nanmax(probability[:4, :]) == 0.0
    assert np.nanmax(probability[10:, :]) == 0.0


def test_radial_interference_detects_two_thirds_high_long_range_ray(
    tmp_path: Path,
) -> None:
    """The observed Z9591 boundary ray is still interference at 66.7% high gates."""
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((12, 600), np.nan, dtype="float32")

    dbzh[:, :180] = 30.0
    dbzh[6, :] = np.linspace(35.0, 65.0, 600, dtype="float32")
    valid = np.isfinite(dbzh)

    probability, flagged_count = _radial_probability(
        dbzh,
        valid,
        profile.radial_interference,
    )

    assert flagged_count == 1
    assert np.all(probability[6, :] >= profile.radial_interference.flag_probability)
    assert np.nanmax(probability[:6, :]) == 0.0
    assert np.nanmax(probability[7:, :]) == 0.0


def test_rp043_radial_fan_closure_fills_bounded_boundary_ray() -> None:
    """A near-threshold ray between hard fan seeds must not remain as a hole."""
    profile = load_qc_profile(RP043_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((24, 600), 5.0, dtype="float32")
    dbzh[10, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    dbzh[12, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    # This boundary ray has strong range growth but misses the legacy 400-gate
    # saturated run. Similar contaminated neighbours also suppress its local
    # difference, matching the observed 10:25 and 10:45 CST holes.
    dbzh[11, :] = np.linspace(30.0, 70.0, 600, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(600, dtype="float32") * 250.0,
    )

    assert np.all(detection.probability[11] >= profile.radial_interference.flag_probability)
    assert np.all(detection.interference_type[11] == INTERFERENCE_TYPE_CODES["broad"])


def test_rp043_multiscale_promotion_confirms_discontinuous_spike() -> None:
    """Sparse longitudinal spikes require wider azimuth context for promotion."""
    profile = load_qc_profile(RP043_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((100, 600), 5.0, dtype="float32")
    dbzh[50, :] = np.nan
    dbzh[50, :50] = 20.0
    dbzh[50, 350:] = np.linspace(35.0, 65.0, 250, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(600, dtype="float32") * 250.0,
    )

    typed = detection.interference_type[50] != INTERFERENCE_TYPE_CODES["none"]
    assert np.count_nonzero(typed) >= 100
    assert np.all(detection.probability[50, typed] >= profile.radial_interference.flag_probability)


def test_rp043_does_not_expand_fan_closure_into_continuous_precipitation() -> None:
    profile = load_qc_profile(RP043_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((24, 600), 5.0, dtype="float32")
    # A spatially continuous rain band spans several rays but only part of the
    # range axis. It has neither seeded fan boundaries nor far-range growth.
    dbzh[8:16, 180:300] = 48.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(600, dtype="float32") * 250.0,
    )

    assert detection.flagged_ray_count == 0
    assert not np.any(
        np.nan_to_num(detection.probability[8:16], nan=0.0)
        >= profile.radial_interference.flag_probability
    )


def test_rp047_promotes_adjacent_weak_long_range_rays_with_vertical_evidence() -> None:
    """The 08:20-like pair is invisible to immediate-neighbour differencing."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10:12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, np.nan, dtype="float32")
    higher_extent[10:12] = 0.23

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
    )

    assert detection.flagged_ray_count == 2
    assert np.all(detection.probability[10:12] >= profile.radial_interference.flag_probability)


def test_rp047_closes_four_ray_hole_inside_confirmed_fan() -> None:
    """The 08:35-like four-ray hole is closed only between hard fan seeds."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    dbzh[8, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    dbzh[13, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    for ray_index in range(9, 13):
        dbzh[ray_index, :384] = (20.0 * np.log10(ranges[:384] / 1_000.0) - 12.0).astype("float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    for ray_index in range(9, 13):
        ray_valid = np.isfinite(dbzh[ray_index])
        assert np.all(
            detection.probability[ray_index, ray_valid]
            >= profile.radial_interference.flag_probability
        )
    # The two outer gap rays are independently caught by their seed contrast;
    # the two interior rays are the actual fan-closure regression.
    for ray_index in range(10, 12):
        ray_valid = np.isfinite(dbzh[ray_index])
        assert np.all(
            detection.interference_type[ray_index, ray_valid] == INTERFERENCE_TYPE_CODES["broad"]
        )


def test_rp047_extends_confirmed_fan_into_open_truncated_edge() -> None:
    """A 14:25-like open fan edge is strong geometry, not a weak candidate."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    dbzh[10:13, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    # The contaminated tail reaches only 80% of the nominal range.  Its fixed
    # far quartile is therefore incomplete, but the observed support remains
    # a long, high-occupancy, range-growing radial next to the hard fan.
    dbzh[13, :480] = np.linspace(35.0, 58.0, 480, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        cross_radar_consistency=np.ones(24, dtype="float32"),
    )

    tail_valid = np.isfinite(dbzh[13])
    assert np.all(
        detection.probability[13, tail_valid] >= profile.radial_interference.flag_probability
    )
    assert np.all(detection.interference_type[13, tail_valid] == INTERFERENCE_TYPE_CODES["broad"])


def test_rp047_hard_flags_sparse_full_range_saturated_ray() -> None:
    """The observed 15:30 ray is sparse but retains a stable transmitter signature."""
    gate_count = 920
    gate_index = np.arange(1, gate_count + 1, dtype="float32")
    values = np.full(gate_count, np.nan, dtype="float32")
    observed = np.r_[np.arange(20), np.arange(gate_count - 375, gate_count)]
    values[observed] = 20.0 * np.log10(gate_index[observed]) - 10.0

    evidence = _long_range_saturated_radial_evidence(values, np.isfinite(values))

    assert evidence is not None
    assert evidence.longest_high_run >= 350
    assert evidence.high_gate_fraction >= 0.60


def test_rp047_closes_severely_truncated_hole_inside_confirmed_fan() -> None:
    """The observed 08:40 interior ray reaches 54% of full-range fan boundaries."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(1_000, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 1_000), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    dbzh[10, :] = np.linspace(46.0, 66.0, 1_000, dtype="float32")
    dbzh[12, :] = np.linspace(46.0, 66.0, 1_000, dtype="float32")
    dbzh[11, :540] = 20.0 * np.log10(ranges[:540] / 1_000.0) + 10.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    ray_valid = np.isfinite(dbzh[11])
    assert np.all(
        detection.probability[11, ray_valid] >= profile.radial_interference.flag_probability
    )


def test_rp047_extends_fan_into_sparse_truncated_open_edge() -> None:
    """The observed 08:40 open edge has 60% support and 34% high gates."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    dbzh[10:13, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    dbzh[13, :360] = 20.0 * np.log10(ranges[:360] / 1_000.0) + 10.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    ray_valid = np.isfinite(dbzh[13])
    assert np.all(
        detection.probability[13, ray_valid] >= profile.radial_interference.flag_probability
    )


def test_rp047_completes_fragmented_residuals_on_a_seeded_radial() -> None:
    """08:40-like gaps must not leave pieces of a confirmed transmitter ray."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(1_000, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 1_000), np.nan, dtype="float32")
    dbzh[[9, 11], :] = 5.0
    transmitter = 20.0 * np.log10(ranges / 1_000.0) + 20.0
    dbzh[10, 50:350] = transmitter[50:350]
    for start in (760, 820, 900):
        dbzh[10, start : start + 8] = transmitter[start : start + 8]
    vertical = np.ones(dbzh.shape, dtype="float32")
    vertical[10] = 0.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        vertical_consistency=vertical,
    )

    ray_valid = np.isfinite(dbzh[10])
    assert np.all(
        detection.probability[10, ray_valid] >= profile.radial_interference.flag_probability
    )


def test_rp047_removes_only_far_fragments_beside_a_confirmed_fan() -> None:
    """15:30-like far fragments inherit a hard fan seed without erasing rain."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(1_000, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 1_000), np.nan, dtype="float32")
    transmitter = 20.0 * np.log10(ranges / 1_000.0) + 20.0
    dbzh[11:14] = transmitter
    dbzh[10, :350] = 30.0
    dbzh[10, 820:850] = transmitter[820:850]
    dbzh[10, 860:] = transmitter[860:]

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    assert np.all(
        detection.probability[10, 820:850] >= profile.radial_interference.flag_probability
    )
    assert np.all(detection.probability[10, 860:] >= profile.radial_interference.flag_probability)
    assert np.all(detection.probability[10, :350] < profile.radial_interference.flag_probability)


def test_rp047_removes_seeded_stable_segment_that_crosses_far_range_boundary() -> None:
    """A stable transmitter segment must not escape because it starts before 200 km."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(1_000, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 1_000), np.nan, dtype="float32")
    transmitter = 20.0 * np.log10(ranges / 1_000.0) + 20.0
    dbzh[11:14] = transmitter
    # Mirrors the residual 08:40 ray: the coherent segment begins around
    # 90 km, crosses 200 km, and is too sparse for the open-edge detector.
    dbzh[10, 360:900] = transmitter[360:900]
    dbzh[10, 900:960] = 20.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    ray_valid = np.isfinite(dbzh[10])
    assert np.all(
        detection.probability[10, ray_valid] >= profile.radial_interference.flag_probability
    )


def test_rp047_keeps_seeded_but_power_unstable_high_segment() -> None:
    """Fan adjacency alone must not turn an unstable rain-like segment into hard QC."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(1_000, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 1_000), np.nan, dtype="float32")
    transmitter = 20.0 * np.log10(ranges / 1_000.0) + 20.0
    dbzh[11:14] = transmitter
    dbzh[10, 360:900] = np.tile(
        np.array([45.0, 65.0], dtype="float32"),
        270,
    )

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    assert np.all(detection.probability[10, 360:900] < profile.radial_interference.flag_probability)


def test_rp047_sparse_saturated_detector_rejects_unstable_power() -> None:
    gate_count = 920
    values = np.full(gate_count, np.nan, dtype="float32")
    values[:20] = 20.0
    values[-375:] = np.tile(
        np.array([46.0, 62.0], dtype="float32"),
        188,
    )[:375]

    evidence = _long_range_saturated_radial_evidence(values, np.isfinite(values))

    assert evidence is None


def test_rp047_does_not_promote_broad_long_range_precipitation() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    broad_echo = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[5:17, :] = broad_echo.astype("float32")
    higher_extent = np.full(24, 0.23, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
    )

    assert detection.flagged_ray_count == 0


def test_rp047_weak_geometry_without_context_stays_diagnostic() -> None:
    """Weak geometry lowers QI but does not become a hard removal by itself."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10, :] = power_stable.astype("float32")
    dbzh[12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.60, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
    )

    assert detection.flagged_ray_count == 0
    assert np.all(
        detection.probability[[10, 12]]
        == profile.radial_interference.morphology.diagnostic_probability
    )
    assert profile.radial_interference.morphology.mode == "quality_index"


def test_rp047_temporal_persistence_is_second_independent_evidence() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10, :] = power_stable.astype("float32")
    dbzh[12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.60, dtype="float32")
    temporal = np.full(24, np.nan, dtype="float32")
    temporal[[10, 12]] = 2.0 / 3.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
        temporal_persistence=temporal,
    )

    assert detection.context_promoted_ray_count == 2
    assert np.all(detection.probability[[10, 12]] >= profile.radial_interference.flag_probability)


def test_rp047_cross_radar_absence_promotes_weak_candidate() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10:12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.60, dtype="float32")
    cross_radar = np.full(24, np.nan, dtype="float32")
    cross_radar[10:12] = 0.05

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
        cross_radar_consistency=cross_radar,
    )

    assert detection.context_promoted_ray_count == 2


def test_rp047_cross_radar_support_vetoes_weak_hard_removal() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10:12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.20, dtype="float32")
    temporal = np.full(24, 1.0, dtype="float32")
    cross_radar = np.full(24, 0.95, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
        temporal_persistence=temporal,
        cross_radar_consistency=cross_radar,
    )

    assert detection.flagged_ray_count == 0
    assert detection.cross_radar_vetoed_ray_count == 2
    assert np.all(
        detection.probability[10:12]
        == profile.radial_interference.morphology.diagnostic_probability
    )


def test_rp047_cross_radar_support_also_vetoes_local_morphology_candidate() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((7, 40), 5.0, dtype="float32")
    dbzh[3, 10:30] = 35.0
    vertical = np.full(dbzh.shape, np.nan, dtype="float32")
    vertical[3, 10:30] = 0.0
    cross_radar = np.full(7, np.nan, dtype="float32")
    cross_radar[3] = 0.95

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(40, dtype="float32") * 250.0,
        vertical_consistency=vertical,
        cross_radar_consistency=cross_radar,
    )

    assert detection.flagged_ray_count == 0
    assert detection.cross_radar_vetoed_ray_count == 1
    assert np.all(
        detection.probability[3, 10:30]
        == profile.radial_interference.morphology.diagnostic_probability
    )


def test_rp047_strong_geometry_ignores_cross_radar_veto() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((12, 600), np.nan, dtype="float32")
    dbzh[:, :180] = 20.0
    dbzh[6, :] = np.linspace(46.0, 66.0, 600, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=(np.arange(600, dtype="float32") + 1.0) * 250.0,
        cross_radar_consistency=np.ones(12, dtype="float32"),
    )

    assert detection.flagged_ray_count == 1
    assert detection.cross_radar_vetoed_ray_count == 0


def test_rp047_temporal_context_aligns_azimuth_and_requires_two_scans() -> None:
    azimuth = np.array([0.0, 90.0, 180.0, 270.0], dtype="float32")
    shifted = np.array([359.5, 89.5, 179.5, 269.5], dtype="float32")
    persistence = _temporal_radial_persistence(
        azimuth,
        (
            (
                shifted,
                np.array([True, True, False, False]),
                np.array([True, True, False, False]),
            ),
            (
                shifted,
                np.array([True, False, False, True]),
                np.array([True, True, False, True]),
            ),
            (
                shifted,
                np.array([False, True, False, True]),
                np.array([False, True, False, True]),
            ),
        ),
        minimum_context_scans=2,
        maximum_context_scans=3,
    )

    np.testing.assert_allclose(
        persistence,
        [1.0, 2.0 / 3.0, np.nan, 1.0],
        equal_nan=True,
    )
    unavailable = _temporal_radial_persistence(
        azimuth,
        ((shifted, np.ones(4, dtype=bool)),),
        minimum_context_scans=2,
        maximum_context_scans=3,
    )
    assert np.all(np.isnan(unavailable))


def test_rp047_cross_radar_consistency_uses_only_observed_overlap() -> None:
    dbzh = np.full((2, 100), 25.0, dtype="float32")
    neighbour = np.full((2, 100), np.nan, dtype="float32")
    neighbour[0, :80] = 5.0
    neighbour[1, :79] = 25.0

    consistency = _cross_radar_consistency_by_ray(
        dbzh,
        np.isfinite(dbzh),
        (neighbour,),
        echo_threshold_dbzh=10.0,
        minimum_overlap_gates=80,
    )

    assert consistency[0] == 0.0
    assert np.isnan(consistency[1])


def test_radial_morphology_classifies_interrupted_and_short_range_segments(
    tmp_path: Path,
) -> None:
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    morphology = replace(
        profile.radial_interference.morphology,
        enabled=True,
        minimum_segment_gates=4,
        intermittent_minimum_segments=3,
        short_range_max_m=3_000.0,
    )
    radial = replace(profile.radial_interference, morphology=morphology)
    dbzh = np.full((7, 40), 5.0, dtype="float32")
    dbzh[2, 4:10] = 35.0
    dbzh[2, 15:21] = 35.0
    dbzh[2, 27:33] = 35.0
    dbzh[5, 2:10] = 38.0
    ranges = np.arange(40, dtype="float32") * 250.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        radial,
        ranges_m=ranges,
    )

    assert detection.type_ray_counts["intermittent"] == 1
    assert detection.type_ray_counts["short_range"] == 1
    assert np.all(detection.interference_type[2, 4:10] == INTERFERENCE_TYPE_CODES["intermittent"])
    assert np.all(detection.interference_type[5, 2:10] == INTERFERENCE_TYPE_CODES["short_range"])
    assert np.all(detection.probability[2, 10:15] == 0.0)


def test_radial_morphology_classifies_reverse_range_spike(tmp_path: Path) -> None:
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    morphology = replace(
        profile.radial_interference.morphology,
        enabled=True,
        minimum_segment_gates=8,
        reverse_minimum_drop_db=12.0,
    )
    radial = replace(profile.radial_interference, morphology=morphology)
    dbzh = np.full((7, 80), 5.0, dtype="float32")
    dbzh[3, :] = np.linspace(45.0, 10.0, 80, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        radial,
        ranges_m=np.arange(80, dtype="float32") * 250.0,
    )

    assert detection.type_ray_counts["reverse"] == 1
    assert (
        np.count_nonzero(detection.interference_type[3] == INTERFERENCE_TYPE_CODES["reverse"]) >= 8
    )


def test_radial_morphology_keeps_legacy_high_confidence_flags() -> None:
    profile = load_qc_profile(RP042_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((7, 100), 5.0, dtype="float32")
    dbzh[3, :] = 35.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(100, dtype="float32") * 250.0,
    )

    assert detection.flagged_ray_count >= 1
    assert np.all(detection.probability[3] >= profile.radial_interference.flag_probability)


def test_rp042_zarr_writes_optional_evidence_fields(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    profile = load_qc_profile(RP042_QC_CONFIG, FLAG_CONFIG)

    result = apply_basic_qc(normalized, profile)
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000042"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9598/scan/volume.zarr",
    )
    sweep = open_store(objects)["sweep_000"]

    assert "P_METEO_DUAL_POL" in sweep
    assert "P_VERTICAL_CONSISTENCY" in sweep
    assert "INTERFERENCE_TYPE" in sweep


def test_phase_processing_shadow_is_explicitly_skipped_without_profile() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    range_m = (np.arange(24, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((2, len(range_m)), 20.0, dtype="float32")
    phidp = np.broadcast_to(
        _linear_phidp_deg(range_m, phi0_deg=30.0, kdp_deg_per_km=1.0),
        dbzh.shape,
    ).astype("float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={"PHIDP": phidp},
    )

    result = apply_basic_qc(normalized, profile)

    assert result.module_status(PHASE_PROCESSING_MODULE_NAME) == "skipped"
    assert result.summary["module_statuses"][PHASE_PROCESSING_MODULE_NAME] == "skipped"
    record = next(item for item in result.modules if item.name == PHASE_PROCESSING_MODULE_NAME)
    assert record.reason == "phase_processing_shadow_profile_unconfigured"
    assert KDP_SHADOW_FIELD not in result.sweeps[0].optional_qc_fields


def test_phase_processing_shadow_writes_diagnostics_without_changing_operational_qc() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    phase_profile = load_phase_processing_profile(PHASE_PROCESSING_PROFILE)
    range_m = (np.arange(48, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((3, len(range_m)), 25.0, dtype="float32")
    phidp = np.broadcast_to(
        _linear_phidp_deg(range_m, phi0_deg=35.0, kdp_deg_per_km=1.25),
        dbzh.shape,
    ).astype("float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={
            "PHIDP": phidp,
            "RHOHV": np.full(dbzh.shape, 0.98, dtype="float32"),
        },
    )

    reference = apply_basic_qc(normalized, profile)
    result = apply_basic_qc(
        normalized,
        profile,
        phase_processing_profile=phase_profile,
    )

    reference_sweep = reference.sweeps[0]
    sweep = result.sweeps[0]
    assert np.allclose(sweep.dbzh_qc, reference_sweep.dbzh_qc, equal_nan=True)
    assert np.allclose(sweep.quality_index, reference_sweep.quality_index, equal_nan=True)
    assert np.array_equal(sweep.qc_flags, reference_sweep.qc_flags)
    assert np.array_equal(sweep.low_quality_mask, reference_sweep.low_quality_mask)

    optional = sweep.optional_qc_fields
    for name in (
        PHIDP_SHADOW_UNWRAPPED_FIELD,
        PHIDP_SHADOW_CORRECTED_FIELD,
        KDP_SHADOW_FIELD,
        KDP_SHADOW_UNCERTAINTY_FIELD,
        KDP_SHADOW_AVAILABLE_MASK_FIELD,
        PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD,
        PHIDP_SHADOW_SEGMENT_INDEX_FIELD,
    ):
        assert name in optional
    assert np.nanmedian(optional[KDP_SHADOW_FIELD][0, 6:-6]) == pytest.approx(1.25, abs=0.05)
    assert np.nanmax(optional[KDP_SHADOW_UNCERTAINTY_FIELD][0, 6:-6]) < 0.05
    assert np.count_nonzero(optional[KDP_SHADOW_AVAILABLE_MASK_FIELD]) > 0
    assert np.count_nonzero(optional[PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD]) == dbzh.size
    assert np.nanmin(optional[PHIDP_SHADOW_SEGMENT_INDEX_FIELD]) == 0
    assert result.module_status(PHASE_PROCESSING_MODULE_NAME) == "applied"
    assert result.summary["phase_processing_shadow_available_gate_count"] > 0

    objects, validation = build_validated_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000143"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9999/phase-shadow/volume.zarr",
    )
    root = open_store(objects)
    record = next(
        item
        for item in root.attrs["module_provenance"]
        if item["name"] == PHASE_PROCESSING_MODULE_NAME
    )

    assert validation["sweep_count"] == 1
    assert record["status"] == "applied"
    assert record["version"] == phase_profile.profile_version
    assert KDP_SHADOW_FIELD in root["sweep_000"]
    assert root["sweep_000"][KDP_SHADOW_FIELD].dtype == np.dtype("float32")


def test_phase_processing_shadow_keeps_fail_closed_fields_when_phidp_is_missing() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    phase_profile = load_phase_processing_profile(PHASE_PROCESSING_PROFILE)
    dbzh = np.full((2, 16), 20.0, dtype="float32")
    normalized = synthetic_normalized_fixture(dbzh)

    result = apply_basic_qc(
        normalized,
        profile,
        phase_processing_profile=phase_profile,
    )

    optional = result.sweeps[0].optional_qc_fields
    assert np.isnan(optional[PHIDP_SHADOW_UNWRAPPED_FIELD]).all()
    assert np.isnan(optional[PHIDP_SHADOW_CORRECTED_FIELD]).all()
    assert np.isnan(optional[KDP_SHADOW_FIELD]).all()
    assert np.isnan(optional[KDP_SHADOW_UNCERTAINTY_FIELD]).all()
    assert np.count_nonzero(optional[KDP_SHADOW_AVAILABLE_MASK_FIELD]) == 0
    assert np.count_nonzero(optional[PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD]) == 0
    assert np.array_equal(
        optional[PHIDP_SHADOW_SEGMENT_INDEX_FIELD],
        np.full(dbzh.shape, -1, dtype="int32"),
    )
    assert result.module_status(PHASE_PROCESSING_MODULE_NAME) == "skipped"
    record = next(item for item in result.modules if item.name == PHASE_PROCESSING_MODULE_NAME)
    assert record.reason == "phidp_field_unavailable"


def test_attenuation_shadow_is_explicitly_skipped_without_profile() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    phase_profile = load_phase_processing_profile(PHASE_PROCESSING_PROFILE)
    range_m = (np.arange(24, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((2, len(range_m)), 20.0, dtype="float32")
    phidp = np.broadcast_to(
        _linear_phidp_deg(range_m, phi0_deg=30.0, kdp_deg_per_km=1.0),
        dbzh.shape,
    ).astype("float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={"PHIDP": phidp},
    )

    result = apply_basic_qc(
        normalized,
        profile,
        phase_processing_profile=phase_profile,
    )

    assert result.module_status(ATTENUATION_SHADOW_MODULE_NAME) == "skipped"
    record = next(item for item in result.modules if item.name == ATTENUATION_SHADOW_MODULE_NAME)
    assert record.reason == "attenuation_shadow_profile_unconfigured"
    assert ATTENUATION_CORRECTION_SHADOW_FIELD not in result.sweeps[0].optional_qc_fields


def test_attenuation_shadow_writes_diagnostics_without_changing_operational_qc() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    phase_profile = load_phase_processing_profile(PHASE_PROCESSING_PROFILE)
    attenuation_profile = _configured_attenuation_profile(coefficient_a=0.04, exponent_b=1.0)
    range_m = (np.arange(48, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((3, len(range_m)), 25.0, dtype="float32")
    phidp = np.broadcast_to(
        _linear_phidp_deg(range_m, phi0_deg=35.0, kdp_deg_per_km=1.25),
        dbzh.shape,
    ).astype("float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={
            "PHIDP": phidp,
            "RHOHV": np.full(dbzh.shape, 0.98, dtype="float32"),
        },
    )

    reference = apply_basic_qc(
        normalized,
        profile,
        phase_processing_profile=phase_profile,
    )
    result = apply_basic_qc(
        normalized,
        profile,
        phase_processing_profile=phase_profile,
        attenuation_profile=attenuation_profile,
        blockage_by_sweep={"sweep_000": np.zeros(dbzh.shape, dtype="float32")},
    )

    reference_sweep = reference.sweeps[0]
    sweep = result.sweeps[0]
    assert np.allclose(sweep.dbzh_qc, reference_sweep.dbzh_qc, equal_nan=True)
    assert np.allclose(sweep.quality_index, reference_sweep.quality_index, equal_nan=True)
    assert np.array_equal(sweep.qc_flags, reference_sweep.qc_flags)
    assert np.array_equal(sweep.low_quality_mask, reference_sweep.low_quality_mask)
    assert np.allclose(
        sweep.qi_components["QI_ATTENUATION"],
        reference_sweep.qi_components["QI_ATTENUATION"],
        equal_nan=True,
    )
    assert np.allclose(
        sweep.qi_components["QI_CALIBRATION"],
        reference_sweep.qi_components["QI_CALIBRATION"],
        equal_nan=True,
    )

    optional = sweep.optional_qc_fields
    for name in (
        SPECIFIC_ATTENUATION_SHADOW_FIELD,
        ATTENUATION_CORRECTION_SHADOW_FIELD,
        DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD,
        ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD,
        ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD,
    ):
        assert name in optional
    assert np.nanmedian(optional[SPECIFIC_ATTENUATION_SHADOW_FIELD][0, 6:-6]) == pytest.approx(
        0.05,
        abs=0.01,
    )
    assert optional[ATTENUATION_CORRECTION_SHADOW_FIELD][0, 12] > 0.0
    assert (
        optional[DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD][0, 12]
        > optional[DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD][0, 1]
        >= dbzh[0, 1]
    )
    assert np.count_nonzero(optional[ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD]) > 0
    assert np.nanmin(optional[ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD]) == 0
    assert result.module_status(ATTENUATION_SHADOW_MODULE_NAME) == "applied"
    assert result.summary["attenuation_shadow_available_gate_count"] > 0

    objects, validation = build_validated_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000146"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9999/attenuation-shadow/volume.zarr",
    )
    root = open_store(objects)
    record = next(
        item
        for item in root.attrs["module_provenance"]
        if item["name"] == ATTENUATION_SHADOW_MODULE_NAME
    )

    assert validation["sweep_count"] == 1
    assert record["status"] == "applied"
    assert record["version"] == attenuation_profile.profile_version
    assert ATTENUATION_CORRECTION_SHADOW_FIELD in root["sweep_000"]
    assert root["sweep_000"][ATTENUATION_CORRECTION_SHADOW_FIELD].dtype == np.dtype("float32")


def test_attenuation_shadow_keeps_fail_closed_fields_when_kdp_shadow_is_unavailable() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    attenuation_profile = _configured_attenuation_profile()
    dbzh = np.full((2, 16), 20.0, dtype="float32")
    normalized = synthetic_normalized_fixture(dbzh)

    result = apply_basic_qc(
        normalized,
        profile,
        attenuation_profile=attenuation_profile,
    )

    optional = result.sweeps[0].optional_qc_fields
    assert np.isnan(optional[SPECIFIC_ATTENUATION_SHADOW_FIELD]).all()
    assert np.isnan(optional[ATTENUATION_CORRECTION_SHADOW_FIELD]).all()
    assert np.isnan(optional[DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD]).all()
    assert np.count_nonzero(optional[ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD]) == 0
    assert np.array_equal(
        optional[ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD],
        np.full(dbzh.shape, -1, dtype="int32"),
    )
    assert result.module_status(ATTENUATION_SHADOW_MODULE_NAME) == "skipped"
    record = next(item for item in result.modules if item.name == ATTENUATION_SHADOW_MODULE_NAME)
    assert record.reason == "kdp_shadow_field_unavailable"


def test_attenuation_shadow_keeps_fail_closed_fields_when_coefficients_are_unconfigured() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    phase_profile = load_phase_processing_profile(PHASE_PROCESSING_PROFILE)
    attenuation_profile = load_attenuation_profile(ATTENUATION_PROFILE)
    range_m = (np.arange(24, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((2, len(range_m)), 20.0, dtype="float32")
    phidp = np.broadcast_to(
        _linear_phidp_deg(range_m, phi0_deg=30.0, kdp_deg_per_km=1.0),
        dbzh.shape,
    ).astype("float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={"PHIDP": phidp},
    )

    result = apply_basic_qc(
        normalized,
        profile,
        phase_processing_profile=phase_profile,
        attenuation_profile=attenuation_profile,
    )

    optional = result.sweeps[0].optional_qc_fields
    assert np.isnan(optional[SPECIFIC_ATTENUATION_SHADOW_FIELD]).all()
    assert np.isnan(optional[ATTENUATION_CORRECTION_SHADOW_FIELD]).all()
    assert np.isnan(optional[DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD]).all()
    assert np.count_nonzero(optional[ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD]) == 0
    assert result.module_status(ATTENUATION_SHADOW_MODULE_NAME) == "skipped"
    record = next(item for item in result.modules if item.name == ATTENUATION_SHADOW_MODULE_NAME)
    assert record.reason == "attenuation_coefficients_unconfigured"
    assert np.isnan(result.sweeps[0].qi_components["QI_ATTENUATION"]).all()
    assert np.isnan(result.sweeps[0].qi_components["QI_CALIBRATION"]).all()


def test_evidence_v2_zarr_writes_geometry_aware_vertical_fields() -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    normalized = synthetic_multisweep_normalized_fixture(
        (
            {
                "dbzh": np.array([[30.0, 25.0]], dtype="float32"),
                "azimuth": np.array([0.0], dtype="float32"),
                "range": np.array([10_000.0, 20_000.0], dtype="float32"),
                "elevation": 0.5,
            },
            {
                "dbzh": np.array([[28.0, 20.0]], dtype="float32"),
                "azimuth": np.array([0.2], dtype="float32"),
                "range": np.array([10_000.0, 20_000.0], dtype="float32"),
                "elevation": 1.2,
            },
        )
    )

    result = apply_basic_qc(
        normalized,
        profile,
        radar_beam_context=RadarBeamContext(
            radar_id="z9999",
            longitude_deg=117.0,
            latitude_deg=27.0,
            antenna_altitude_m=100.0,
            beam_width_vertical_deg=1.0,
            altitude_datum_status="verified_egm2008",
        ),
    )
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000142"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9999/geometry/volume.zarr",
    )
    sweep = open_store(objects)["sweep_000"]

    assert "P_VERTICAL_CONSISTENCY" in sweep
    assert VERTICAL_CONSISTENCY_AVAILABLE_MASK_FIELD in sweep
    assert VERTICAL_HEIGHT_DIFFERENCE_M_FIELD in sweep
    assert sweep[VERTICAL_CONSISTENCY_AVAILABLE_MASK_FIELD][0, 0] == 1
    assert sweep["P_VERTICAL_CONSISTENCY"][0, 0] > 0.8
    assert np.isfinite(sweep[VERTICAL_HEIGHT_DIFFERENCE_M_FIELD][0, 0])
    assert result.summary["vertical_consistency_geometry_aware"] is True
    assert result.summary["vertical_consistency_verified_vertical_datum"] is True


def test_evidence_v2_worker_writes_trusted_cross_radar_support_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    azimuth = np.linspace(0.0, 360.0, 24, endpoint=False, dtype="float32")
    ranges = (np.arange(120, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 120), 20.0, dtype="float32")
    current = synthetic_multisweep_normalized_fixture(
        (
            {
                "dbzh": dbzh,
                "azimuth": azimuth,
                "range": ranges,
                "elevation": 0.5,
            },
        ),
        root_attrs={
            "scan_id": "10000000-0000-4000-8000-000000000145",
            "radar_id": "z9598",
            "radar_config_version": "z9598-test-v1",
            "site_longitude_deg": 117.0805588,
            "site_latitude_deg": 27.0086117,
            "antenna_altitude_m": 1692.0,
            "altitude_datum": "EPSG:3855",
            "volume_end_time_utc": "2026-08-24T03:00:20+00:00",
            "radar_health": "HEALTHY",
            "scan_completeness": 1.0,
        },
    )
    cross_radar = synthetic_multisweep_normalized_fixture(
        (
            {
                "dbzh": dbzh,
                "azimuth": azimuth,
                "range": ranges,
                "elevation": 0.8,
            },
        ),
        root_attrs={
            "scan_id": "10000000-0000-4000-8000-000000000146",
            "radar_id": "z9593",
            "radar_config_version": "z9593-test-v1",
            "site_longitude_deg": 117.0805588,
            "site_latitude_deg": 27.0086117,
            "antenna_altitude_m": 1692.0,
            "altitude_datum": "EPSG:3855",
            "volume_end_time_utc": "2026-08-24T03:00:10+00:00",
            "radar_health": "HEALTHY",
            "scan_completeness": 1.0,
        },
    )
    client = FakeMinio()

    def publish(prefix: str, objects: dict[str, bytes]) -> None:
        manifest = []
        for key, value in objects.items():
            client.objects[("rainpulse", f"{prefix}/{key}")] = value
            manifest.append(
                {
                    "key": key,
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "size_bytes": len(value),
                }
            )
        client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
            {
                "schema_version": "1.0",
                "sha256": artifact_sha256(objects),
                "size_bytes": sum(map(len, objects.values())),
                "objects": sorted(manifest, key=lambda item: item["key"]),
            }
        ).encode()

    current_prefix = "radar/normalized/z9598/evidence-v2-current/volume.zarr"
    cross_prefix = "radar/normalized/z9593/evidence-v2-cross/volume.zarr"
    publish(current_prefix, current)
    publish(cross_prefix, cross_radar)

    source = dem_source_fixture()
    ancillary_root = tmp_path / "ancillary-runtime"
    tile_path = write_dem_runtime_fixture(ancillary_root, source)
    ancillary_config_path = tmp_path / "ancillary-source.yaml"
    ancillary_config_path.write_text(
        yaml.safe_dump(
            {
                "domain_id": source.domain_id,
                "config_version": source.config_version,
                "bounds": {
                    "west": source.bounds.west,
                    "east": source.bounds.east,
                    "south": source.bounds.south,
                    "north": source.bounds.north,
                },
                "dem": {
                    "asset_version": source.dem.asset_version,
                    "base_url": source.dem.base_url,
                    "planned_tile_count": source.dem.planned_tile_count,
                    "storage_prefix": source.dem.storage_prefix,
                    "native_resolution_arc_seconds": source.dem.native_resolution_arc_seconds,
                    "max_uncovered_land_area_km2_per_tile": (
                        source.dem.max_uncovered_land_area_km2_per_tile
                    ),
                },
                "coastline": {
                    "asset_version": source.coastline.asset_version,
                    "source_url": source.coastline.source_url,
                    "source_sha256": source.coastline.source_sha256,
                    "storage_prefix": source.coastline.storage_prefix,
                },
            },
            sort_keys=False,
        )
    )

    radar_config_dir = tmp_path / "radars"
    radar_config_dir.mkdir(parents=True, exist_ok=True)
    current_config = yaml.safe_load(make_config(tmp_path).read_text())
    current_config["ancillary"]["dem_asset_version"] = source.dem.asset_version
    current_config["site"]["altitude_datum"] = "EPSG:3855"
    (radar_config_dir / "z9598.yaml").write_text(yaml.safe_dump(current_config, sort_keys=False))
    neighbour_config = dict(current_config)
    neighbour_config["radar_id"] = "z9593"
    neighbour_config["config_version"] = "z9593-test-v1"
    (radar_config_dir / "z9593.yaml").write_text(yaml.safe_dump(neighbour_config, sort_keys=False))

    def fake_open(path: Path) -> FakeRasterDataset:
        assert path == tile_path
        return FakeRasterDataset(
            np.zeros((16, 16), dtype="float32"),
            (117.0, 27.0, 118.0, 28.0),
        )

    import rainpulse_algo.radar.dem as dem_module

    monkeypatch.setattr(dem_module.rasterio, "open", fake_open)
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(EVIDENCE_V2_QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    monkeypatch.setenv("RAINPULSE_RADAR_CONFIG_DIR", str(radar_config_dir))
    monkeypatch.setenv("RAINPULSE_ANCILLARY_CONFIG", str(ancillary_config_path))
    monkeypatch.setenv("RAINPULSE_ANCILLARY_ROOT", str(ancillary_root))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000145",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000145",
            "job_id": "30000000-0000-4000-8000-000000000146",
            "trace_id": "10000000-0000-4000-8000-000000000147",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000145",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{current_prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/evidence-v2/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "fujian-qc-evidence-v2",
                "qc_pipeline_version": "fujian-qc-evidence-2.0.1",
                "flag_definition_version": "qc-flags-v1",
                "cross_radar_context": [
                    {"radar_id": "z9593", "input_uri": f"s3://rainpulse/{cross_prefix}"}
                ],
            },
        }
    )

    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    root = open_store(result.objects)
    sweep = root["sweep_000"]
    context = result.diagnostics["radar_qc"]["radial_context"]

    assert CROSS_RADAR_TRUSTED_SUPPORT_FIELD in sweep
    assert CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD in sweep
    assert np.count_nonzero(sweep[CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD][:]) > 0
    assert np.nanmax(sweep[CROSS_RADAR_TRUSTED_SUPPORT_FIELD][:]) == pytest.approx(1.0)
    assert context["cross_radar_trusted_reference_count"] == 1
    assert result.diagnostics["radar_qc"]["cross_radar_trusted_available_gate_count"] > 0

    # Frozen replay must run the same geometry-aware v2 path, including real arrays.
    from rainpulse_algo.radar import radial_audit
    from rainpulse_algo.worker.object_store import ArtifactObjectReader

    replay_results = []

    def capture_qc(*args, **kwargs):
        replay = apply_basic_qc(*args, **kwargs)
        replay_results.append(replay)
        return replay

    monkeypatch.setattr(radial_audit, "apply_basic_qc", capture_qc)
    replay_scan = {
        "scan_id": str(request.payload.scan_id),
        "radar_id": request.payload.radar_id,
        "volume_start_time": "2026-08-24T02:55:00+00:00",
        "volume_end_time": "2026-08-24T03:00:20+00:00",
        "normalized_uri": request.payload.input_uri,
        "temporal_context": [],
        "cross_radar_context": [item.model_dump() for item in request.payload.cross_radar_context],
    }
    replay_record = radial_audit._audit_scan(
        replay_scan,
        profile=load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG),
        audit_mode="evidence",
        reader=ArtifactObjectReader(client),
        client=client,
        ancillary=None,
    )
    assert replay_record["status"] == "completed", replay_record
    assert replay_record["context_fingerprint"] == context["context_fingerprint"]
    replay = replay_results[0].sweeps[0]
    for name, values in {
        "QC_FLAGS": replay.qc_flags,
        "VALID_MASK": replay.valid_mask,
        "QUALITY_INDEX": replay.quality_index,
        CROSS_RADAR_TRUSTED_SUPPORT_FIELD: replay.optional_qc_fields[
            CROSS_RADAR_TRUSTED_SUPPORT_FIELD
        ],
    }.items():
        np.testing.assert_array_equal(sweep[name][:], values)


def test_radial_morphology_preserves_full_legacy_ray_when_segments_are_partial() -> None:
    profile = load_qc_profile(RP042_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((7, 100), 5.0, dtype="float32")
    dbzh[3, :60] = 35.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(100, dtype="float32") * 250.0,
    )

    assert np.all(detection.probability[3] >= profile.radial_interference.flag_probability)


def test_vertical_consistency_matches_next_distinct_elevation() -> None:
    low = {
        "dbzh": np.array([[30.0, 25.0], [20.0, 15.0]], dtype="float32"),
        "azimuth": np.array([0.0, 180.0], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0], dtype="float32"),
        "elevation": 0.5,
    }
    high = {
        "dbzh": np.array([[28.0, 20.0], [np.nan, np.nan]], dtype="float32"),
        "azimuth": np.array([0.2, 180.2], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0], dtype="float32"),
        "elevation": 1.5,
    }

    probabilities = _vertical_consistency_probabilities(
        (low, high),
        minimum_dbzh=10.0,
        support_tolerance_db=12.0,
        maximum_range_m=100_000.0,
    )

    assert probabilities[0][0, 0] > 0.8
    assert probabilities[0][1, 0] == 0.0
    assert np.isnan(probabilities[1]).all()


def test_higher_elevation_extent_matches_nearest_azimuth() -> None:
    low = {
        "dbzh": np.ones((2, 4), dtype="float32"),
        "azimuth": np.array([0.0, 180.0], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0, 3_000.0, 4_000.0], dtype="float32"),
        "elevation": 0.5,
    }
    high = {
        "dbzh": np.array(
            [[20.0, 20.0, np.nan, np.nan], [20.0, 20.0, 20.0, 20.0]],
            dtype="float32",
        ),
        "azimuth": np.array([180.2, 0.2], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0, 3_000.0, 4_000.0], dtype="float32"),
        "elevation": 1.5,
    }

    extents = _higher_elevation_radial_extent_fractions((low, high))

    assert extents[0] == pytest.approx(np.array([1.0, 0.5], dtype="float32"))
    assert np.isnan(extents[1]).all()


def test_dual_pol_fuzzy_probability_is_diagnostic_and_missing_aware(
    tmp_path: Path,
) -> None:
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    fuzzy = replace(profile.dual_pol_fuzzy, enabled=True)
    dbzh = np.array([[25.0, 25.0, np.nan]], dtype="float32")
    valid = np.isfinite(dbzh)
    probability = _dual_pol_meteorological_probability(
        dbzh,
        valid,
        np.zeros_like(valid),
        snr=np.array([[20.0, 2.0, np.nan]], dtype="float32"),
        rhohv=np.array([[0.99, 0.55, np.nan]], dtype="float32"),
        zdr=np.array([[0.5, 7.8, np.nan]], dtype="float32"),
        phidp=np.array([[40.0, 200.0, np.nan]], dtype="float32"),
        config=fuzzy,
        echo=profile.echo,
    )

    assert probability[0, 0] > 0.8
    assert probability[0, 1] < 0.3
    assert np.isnan(probability[0, 2])


def test_radial_audit_reports_the_same_two_thirds_signature(tmp_path: Path) -> None:
    """The read-only audit must select the exact residual-ray shape QC rejects."""
    store = MemoryStore()
    root = zarr.group(store=store)
    root.attrs["contract_name"] = "rainpulse.normalized-radar-volume"
    root.create_dataset("sweep_number", data=np.array([0], dtype="int16"))
    sweep = root.create_group("sweep_000")
    gate_count = 600
    values = np.full((2, gate_count), 30.0, dtype="float32")
    values[1, :] = np.linspace(35.0, 65.0, gate_count, dtype="float32")
    sweep.create_dataset("DBZH", data=values)
    sweep.create_dataset("azimuth", data=np.array([10.0, 63.52], dtype="float32"))
    normalized = {str(key): bytes(value) for key, value in store.items()}

    audit = audit_long_range_saturated_radials(
        normalized,
        load_qc_profile(QC_CONFIG, FLAG_CONFIG),
    )

    assert audit["saturated_ray_count"] == 1
    assert audit["sweeps"][0]["saturated_ray_count"] == 1
    evidence = audit["sweeps"][0]["rays"][0]
    assert evidence["ray_index"] == 1
    assert evidence["high_gate_fraction"] == pytest.approx(2 / 3, abs=0.02)
    assert evidence["range_growth_db"] >= 12.0


def test_unavailable_radar_health_is_a_hard_qc_gate(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    health = json.loads(normalized["health/summary.json"])
    health["health"] = "UNAVAILABLE"
    normalized["health/summary.json"] = json.dumps(health).encode()

    with pytest.raises(QCInputError, match="UNAVAILABLE"):
        apply_basic_qc(normalized, load_qc_profile(QC_CONFIG, FLAG_CONFIG))


def test_versioned_ancillary_probabilities_set_flags_but_do_not_delete_echo(
    tmp_path: Path,
) -> None:
    normalized = normalized_fixture(tmp_path)
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    profile = replace(
        profile,
        static_ground_clutter=replace(
            profile.static_ground_clutter,
            asset_uri="file:///fixtures/clutter.npz",
            asset_version="synthetic-clutter-v1",
        ),
        sea_ap=replace(
            profile.sea_ap,
            coastline_asset_uri="file:///fixtures/coastline.npz",
            asset_version="synthetic-coastline-v1",
        ),
    )
    shape = open_store(normalized)["sweep_000/DBZH"].shape
    clutter = np.zeros(shape, dtype="float32")
    sea = np.zeros(shape, dtype="float32")
    ap = np.zeros(shape, dtype="float32")
    clutter[0, 3] = 0.9
    sea[0, 4] = 0.8
    ap[1, 3] = 0.75

    result = apply_basic_qc(
        normalized,
        profile,
        ancillary_maps={"sweep_000": {"ground_clutter": clutter, "sea_clutter": sea, "ap": ap}},
    )
    sweep = result.sweeps[0]

    assert sweep.qc_flags[0, 3] & np.uint32(1)
    assert sweep.qc_flags[0, 4] & np.uint32(2)
    assert sweep.qc_flags[1, 3] & np.uint32(4)
    assert sweep.valid_mask[0, 3] == 1
    assert np.isfinite(sweep.dbzh_qc[0, 3])
    assert result.module_status("static_ground_clutter") == "applied"
    assert result.module_status("sea_ap") == "applied"


def test_real_qc_worker_reads_verified_normalized_artifact_and_builds_qc_zarr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    client = FakeMinio()
    prefix = "radar/normalized/z9598/scan/volume.zarr"
    manifest = []
    for key, value in normalized.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(normalized),
            "size_bytes": sum(map(len, normalized.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000001",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000002",
            "job_id": "30000000-0000-4000-8000-000000000002",
            "trace_id": "10000000-0000-4000-8000-000000000003",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/scan/rp008-basic-1.0.4/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "rp008-basic-v1",
                "qc_pipeline_version": "rp008-basic-1.0.4",
                "flag_definition_version": "qc-flags-v1",
            },
        }
    )

    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    validation = validate_qc_zarr_store(result.objects or {})

    assert validation["sweep_count"] == 2
    assert result.metrics["mean_quality_index"] > 0
    assert result.diagnostics["radar_qc"]["health_state"] == "DEGRADED"


def test_qc_worker_validates_completed_qc_zarr_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    client = FakeMinio()
    prefix = "radar/normalized/z9598/scan/volume.zarr"
    manifest = []
    for key, value in normalized.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(normalized),
            "size_bytes": sum(map(len, normalized.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000031",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000032",
            "job_id": "30000000-0000-4000-8000-000000000033",
            "trace_id": "10000000-0000-4000-8000-000000000034",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/scan/rp008-basic-1.0.4/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "rp008-basic-v1",
                "qc_pipeline_version": "rp008-basic-1.0.4",
                "flag_definition_version": "qc-flags-v1",
            },
        }
    )

    import rainpulse_algo.radar.qc_worker as qc_worker_module
    import rainpulse_algo.radar.qc_zarr as qc_zarr_module

    validation_calls = 0
    original_validate = qc_zarr_module.validate_qc_zarr_store

    def validate_once(objects: dict[str, bytes]) -> dict[str, int | float]:
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(objects)

    monkeypatch.setattr(qc_zarr_module, "validate_qc_zarr_store", validate_once)
    monkeypatch.setattr(qc_worker_module, "validate_qc_zarr_store", validate_once, raising=False)

    _execute_basic_qc(request, client)  # type: ignore[arg-type]

    assert validation_calls == 1


def test_qc_worker_reports_stage_observability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    client = FakeMinio()
    prefix = "radar/normalized/z9598/scan/observability/volume.zarr"
    manifest = []
    for key, value in normalized.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(normalized),
            "size_bytes": sum(map(len, normalized.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000041",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000042",
            "job_id": "30000000-0000-4000-8000-000000000043",
            "trace_id": "10000000-0000-4000-8000-000000000044",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/scan/rp008-basic-observability/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "rp008-basic-v1",
                "qc_pipeline_version": "rp008-basic-1.0.4",
                "flag_definition_version": "qc-flags-v1",
            },
        }
    )

    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    observability = result.observability

    assert observability["input_bytes"] == sum(len(value) for value in normalized.values())
    assert observability["output_bytes"] == sum(len(value) for value in result.objects.values())
    assert observability["object_count"] == len(result.objects)
    assert observability["context_age_seconds"] == 0.0
    assert observability["cache_hit"] == 0
    assert observability["cache_miss"] == 0
    assert observability["rss_bytes"] > 0
    for key in (
        "input_read_ms",
        "context_ms",
        "qc_core_ms",
        "serialize_validate_ms",
    ):
        assert observability[key] >= 0.0


def test_worker_shadow_profiles_preserve_operational_qi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    range_m = (np.arange(48, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((3, len(range_m)), 25.0, dtype="float32")
    phidp = np.broadcast_to(
        _linear_phidp_deg(range_m, phi0_deg=35.0, kdp_deg_per_km=1.25),
        dbzh.shape,
    ).astype("float32")
    normalized = synthetic_normalized_fixture(
        dbzh,
        range_m=range_m,
        moments={
            "PHIDP": phidp,
            "RHOHV": np.full(dbzh.shape, 0.98, dtype="float32"),
        },
    )
    normalized_attrs = json.loads(normalized[".zattrs"])
    normalized_attrs["volume_end_time_utc"] = "2026-08-24T03:00:20+00:00"
    normalized_attrs["radar_health"] = "HEALTHY"
    normalized_attrs["scan_completeness"] = 1.0
    normalized[".zattrs"] = json.dumps(normalized_attrs).encode()
    client = FakeMinio()
    prefix = "radar/normalized/z9999/evidence-v2-shadow-runtime/volume.zarr"
    manifest = []
    for key, value in normalized.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(normalized),
            "size_bytes": sum(map(len, normalized.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(EVIDENCE_V2_QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000151",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000151",
            "job_id": "30000000-0000-4000-8000-000000000152",
            "trace_id": "10000000-0000-4000-8000-000000000153",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000099",
                "radar_id": "z9999",
                "input_uri": f"s3://rainpulse/{prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9999/evidence-v2-shadow-runtime/",
                "radar_config_version": "z9999-test-v1",
                "qc_profile": "fujian-qc-evidence-v2",
                "qc_pipeline_version": "fujian-qc-evidence-2.0.1",
                "flag_definition_version": "qc-flags-v1",
            },
        }
    )

    baseline = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    baseline_root = open_store(baseline.objects)
    baseline_sweep = baseline_root["sweep_000"]

    monkeypatch.setenv(
        "RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE",
        str(PHASE_PROCESSING_PROFILE),
    )
    monkeypatch.setenv(
        "RAINPULSE_RADAR_ATTENUATION_PROFILE",
        str(ATTENUATION_PROFILE),
    )
    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    root = open_store(result.objects)
    sweep = root["sweep_000"]
    records = {item["name"]: item for item in root.attrs["module_provenance"]}

    assert PHIDP_SHADOW_UNWRAPPED_FIELD not in baseline_sweep
    assert KDP_SHADOW_FIELD not in baseline_sweep
    assert ATTENUATION_CORRECTION_SHADOW_FIELD not in baseline_sweep
    assert PHIDP_SHADOW_UNWRAPPED_FIELD in sweep
    assert KDP_SHADOW_FIELD in sweep
    assert ATTENUATION_CORRECTION_SHADOW_FIELD in sweep
    assert np.count_nonzero(sweep[KDP_SHADOW_AVAILABLE_MASK_FIELD][:]) > 0
    assert np.allclose(sweep["DBZH_QC"][:], baseline_sweep["DBZH_QC"][:], equal_nan=True)
    assert np.allclose(
        sweep["QUALITY_INDEX"][:],
        baseline_sweep["QUALITY_INDEX"][:],
        equal_nan=True,
    )
    assert np.allclose(
        sweep["QI_ATTENUATION"][:],
        baseline_sweep["QI_ATTENUATION"][:],
        equal_nan=True,
    )
    assert np.allclose(
        sweep["QI_CALIBRATION"][:],
        baseline_sweep["QI_CALIBRATION"][:],
        equal_nan=True,
    )
    assert np.isnan(sweep["QI_ATTENUATION"][:]).all()
    assert np.isnan(sweep["QI_CALIBRATION"][:]).all()
    assert np.isnan(sweep[SPECIFIC_ATTENUATION_SHADOW_FIELD][:]).all()
    assert np.isnan(sweep[ATTENUATION_CORRECTION_SHADOW_FIELD][:]).all()
    assert np.isnan(sweep[DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD][:]).all()
    assert np.count_nonzero(sweep[ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD][:]) == 0
    assert np.array_equal(
        sweep[ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD][:],
        np.full(dbzh.shape, -1, dtype="int32"),
    )
    assert records[PHASE_PROCESSING_MODULE_NAME]["status"] == "applied"
    assert records[PHASE_PROCESSING_MODULE_NAME]["version"] == "fujian-phidp-kdp-shadow-v1"
    assert records[ATTENUATION_SHADOW_MODULE_NAME]["status"] == "skipped"
    assert records[ATTENUATION_SHADOW_MODULE_NAME]["version"] == "fujian-kdp-attenuation-shadow-v1"
    assert (
        records[ATTENUATION_SHADOW_MODULE_NAME]["reason"] == "attenuation_coefficients_unconfigured"
    )


def test_worker_rejects_mismatched_runtime_phase_and_attenuation_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attenuation_profile = yaml.safe_load(ATTENUATION_PROFILE.read_text())
    attenuation_profile["source_phase_processing_profile_version"] = "other-phase-profile-v1"
    attenuation_profile_path = tmp_path / "attenuation-mismatch.yaml"
    attenuation_profile_path.write_text(
        yaml.safe_dump(attenuation_profile, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(EVIDENCE_V2_QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    monkeypatch.setenv(
        "RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE",
        str(PHASE_PROCESSING_PROFILE),
    )
    monkeypatch.setenv(
        "RAINPULSE_RADAR_ATTENUATION_PROFILE",
        str(attenuation_profile_path),
    )
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000154",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000154",
            "job_id": "30000000-0000-4000-8000-000000000155",
            "trace_id": "10000000-0000-4000-8000-000000000156",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000099",
                "radar_id": "z9999",
                "input_uri": "s3://rainpulse/radar/normalized/z9999/mismatch/volume.zarr",
                "output_prefix": "s3://rainpulse/radar/qc/z9999/mismatch/",
                "radar_config_version": "z9999-test-v1",
                "qc_profile": "fujian-qc-evidence-v2",
                "qc_pipeline_version": "fujian-qc-evidence-2.0.1",
                "flag_definition_version": "qc-flags-v1",
            },
        }
    )

    with pytest.raises(
        QCConfigError,
        match="source_phase_processing_profile_version differs",
    ):
        _execute_basic_qc(request, FakeMinio())  # type: ignore[arg-type]


def test_v2_radial_candidates_by_sweep_avoid_full_qc_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.zeros((5, 120), dtype="float32")
    dbzh[2, :] = 35.0
    normalized = synthetic_normalized_fixture(dbzh)
    reference = apply_basic_qc(normalized, profile)
    threshold = profile.radial_interference.morphology.diagnostic_probability
    expected = {
        sweep.name: (
            np.linspace(0.0, 360.0, 5, endpoint=False, dtype="float32"),
            np.any(
                np.nan_to_num(sweep.p_radial_interference, nan=0.0) >= threshold,
                axis=1,
            ),
            np.ones(5, dtype=bool),
        )
        for sweep in reference.sweeps
    }

    def fail_full_qc(*args: object, **kwargs: object) -> object:
        raise AssertionError("full apply_basic_qc path should not run for v2 context candidates")

    import rainpulse_algo.radar.qc_worker as qc_worker_module

    monkeypatch.setattr(qc_worker_module, "apply_basic_qc", fail_full_qc)

    candidates = _radial_candidates_by_sweep(normalized, profile)

    assert set(candidates) == {"sweep_000"}
    assert np.array_equal(candidates["sweep_000"].azimuth_deg, expected["sweep_000"][0])
    assert np.array_equal(
        candidates["sweep_000"].candidate_mask_by_ray,
        expected["sweep_000"][1],
    )
    assert np.array_equal(
        candidates["sweep_000"].observed_mask_by_ray,
        expected["sweep_000"][2],
    )


def test_qc_worker_records_selected_temporal_and_cross_radar_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    temporal_a, temporal_b = dict(normalized), dict(normalized)
    for index, objects in enumerate((temporal_a, temporal_b)):
        attrs = json.loads(objects[".zattrs"])
        attrs["scan_id"] = f"10000000-0000-4000-8000-00000000010{index}"
        attrs["volume_end_time_utc"] = f"2026-06-15T08:{59 - index * 5}:00+00:00"
        objects[".zattrs"] = json.dumps(attrs).encode()
    cross_radar = dict(normalized)
    cross_attrs = json.loads(cross_radar[".zattrs"])
    cross_attrs["radar_id"] = "z9593"
    cross_radar[".zattrs"] = json.dumps(cross_attrs).encode()
    client = FakeMinio()

    def publish(prefix: str, objects: dict[str, bytes]) -> None:
        manifest = []
        for key, value in objects.items():
            client.objects[("rainpulse", f"{prefix}/{key}")] = value
            manifest.append(
                {
                    "key": key,
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "size_bytes": len(value),
                }
            )
        client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
            {
                "schema_version": "1.0",
                "sha256": artifact_sha256(objects),
                "size_bytes": sum(map(len, objects.values())),
                "objects": sorted(manifest, key=lambda item: item["key"]),
            }
        ).encode()

    current_prefix = "radar/normalized/z9598/current/volume.zarr"
    temporal_a_prefix = "radar/normalized/z9598/temporal-a/volume.zarr"
    temporal_b_prefix = "radar/normalized/z9598/temporal-b/volume.zarr"
    cross_prefix = "radar/normalized/z9593/aligned/volume.zarr"
    for prefix, objects in (
        (current_prefix, normalized),
        (temporal_a_prefix, temporal_a),
        (temporal_b_prefix, temporal_b),
        (cross_prefix, cross_radar),
    ):
        publish(prefix, objects)

    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(RP047_QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000021",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-28T02:30:20Z",
            "run_id": "10000000-0000-4000-8000-000000000022",
            "job_id": "30000000-0000-4000-8000-000000000023",
            "trace_id": "10000000-0000-4000-8000-000000000024",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{current_prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/current/rp047/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "rp047-fujian-radial-evidence-v1",
                "qc_pipeline_version": "rp047-fujian-radial-evidence-1.5.0",
                "flag_definition_version": "qc-flags-v1",
                "temporal_context": [
                    {"radar_id": "z9598", "input_uri": f"s3://rainpulse/{temporal_a_prefix}"},
                    {"radar_id": "z9598", "input_uri": f"s3://rainpulse/{temporal_b_prefix}"},
                ],
                "cross_radar_context": [
                    {"radar_id": "z9593", "input_uri": f"s3://rainpulse/{cross_prefix}"},
                ],
            },
        }
    )

    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    context = result.diagnostics["radar_qc"]["radial_context"]

    assert context["temporal_requested_count"] == 2
    assert context["temporal_available_count"] == 2
    assert context["cross_radar_requested_count"] == 1
    assert context["cross_radar_available_count"] == 1
    assert result.metrics["radial_temporal_context_volume_count"] == 2.0


def test_qc_worker_freezes_context_identity_and_skip_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    temporal_good = dict(normalized)
    good_attrs = json.loads(temporal_good[".zattrs"])
    good_attrs["scan_id"] = "10000000-0000-4000-8000-000000000107"
    good_attrs["volume_end_time_utc"] = "2026-06-15T09:00:00+00:00"
    temporal_good[".zattrs"] = json.dumps(good_attrs).encode()
    temporal_stale = dict(normalized)
    temporal_stale_attrs = json.loads(temporal_stale[".zattrs"])
    temporal_stale_attrs["scan_id"] = "10000000-0000-4000-8000-000000000105"
    temporal_stale_attrs["volume_end_time_utc"] = "1900-01-01T00:00:00+00:00"
    temporal_stale[".zattrs"] = json.dumps(temporal_stale_attrs).encode()
    cross_radar = dict(normalized)
    cross_attrs = json.loads(cross_radar[".zattrs"])
    cross_attrs["scan_id"] = "10000000-0000-4000-8000-000000000106"
    cross_attrs["radar_id"] = "z9593"
    cross_radar[".zattrs"] = json.dumps(cross_attrs).encode()
    client = FakeMinio()

    def publish(prefix: str, objects: dict[str, bytes]) -> None:
        manifest = []
        for key, value in objects.items():
            client.objects[("rainpulse", f"{prefix}/{key}")] = value
            manifest.append(
                {
                    "key": key,
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "size_bytes": len(value),
                }
            )
        client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
            {
                "schema_version": "1.0",
                "sha256": artifact_sha256(objects),
                "size_bytes": sum(map(len, objects.values())),
                "objects": sorted(manifest, key=lambda item: item["key"]),
            }
        ).encode()

    current_prefix = "radar/normalized/z9598/current-freeze/volume.zarr"
    temporal_good_prefix = "radar/normalized/z9598/temporal-freeze-good/volume.zarr"
    temporal_stale_prefix = "radar/normalized/z9598/temporal-freeze-stale/volume.zarr"
    cross_prefix = "radar/normalized/z9593/cross-freeze/volume.zarr"
    for prefix, objects in (
        (current_prefix, normalized),
        (temporal_good_prefix, temporal_good),
        (temporal_stale_prefix, temporal_stale),
        (cross_prefix, cross_radar),
    ):
        publish(prefix, objects)

    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(RP047_QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000121",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-28T02:30:20Z",
            "run_id": "10000000-0000-4000-8000-000000000122",
            "job_id": "30000000-0000-4000-8000-000000000123",
            "trace_id": "10000000-0000-4000-8000-000000000124",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{current_prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/current/rp047-freeze/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "rp047-fujian-radial-evidence-v1",
                "qc_pipeline_version": "rp047-fujian-radial-evidence-1.5.0",
                "flag_definition_version": "qc-flags-v1",
                "temporal_context": [
                    {"radar_id": "z9598", "input_uri": f"s3://rainpulse/{temporal_good_prefix}"},
                    {"radar_id": "z9598", "input_uri": f"s3://rainpulse/{temporal_stale_prefix}"},
                ],
                "cross_radar_context": [
                    {"radar_id": "z9593", "input_uri": f"s3://rainpulse/{cross_prefix}"},
                ],
            },
        }
    )

    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    context = result.diagnostics["radar_qc"]["radial_context"]
    artifacts = context["artifacts"]

    assert len(artifacts) == 4
    assert artifacts[0]["role"] == "current"
    assert artifacts[0]["used"] is True
    assert artifacts[0]["artifact_sha256"] == artifact_sha256(normalized)
    assert artifacts[1]["role"] == "temporal"
    assert artifacts[1]["used"] is True
    assert artifacts[1]["skip_reason"] is None
    assert artifacts[2]["role"] == "temporal"
    assert artifacts[2]["used"] is False
    assert artifacts[2]["skip_reason"] == "time_out_of_window"
    assert artifacts[3]["role"] == "cross_radar"
    assert artifacts[3]["used"] is True
    assert context["temporal_available_count"] == 1
    assert context["temporal_used_count"] == 1
    assert context["cross_radar_available_count"] == 1
    assert context["cross_radar_used_count"] == 1
    assert isinstance(context["context_fingerprint"], str)
    assert len(context["context_fingerprint"]) == 64

    root = open_store(result.objects)
    assert root.attrs["context_fingerprint"] == context["context_fingerprint"]


def test_qc_decodes_each_dbzh_once_per_task(tmp_path: Path, monkeypatch) -> None:
    objects = normalized_fixture(tmp_path)
    reads = {}
    original = zarr.Array.__getitem__

    def count_read(array, selection):
        if array.path.endswith("/DBZH"):
            reads[array.path] = reads.get(array.path, 0) + 1
        return original(array, selection)

    monkeypatch.setattr(zarr.Array, "__getitem__", count_read)
    apply_basic_qc(objects, load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG))
    assert reads
    assert all(count == 1 for count in reads.values()), reads


def test_qc_attenuation_does_not_claim_applied_without_blockage() -> None:
    ranges = (np.arange(48, dtype="float32") + 1) * 250
    dbzh = np.full((3, 48), 25.0, dtype="float32")
    objects = synthetic_normalized_fixture(
        dbzh,
        range_m=ranges,
        moments={
            "PHIDP": np.broadcast_to(
                _linear_phidp_deg(ranges, phi0_deg=35.0, kdp_deg_per_km=1.25), dbzh.shape
            ).copy()
        },
    )
    result = apply_basic_qc(
        objects,
        load_qc_profile(EVIDENCE_V2_QC_CONFIG, FLAG_CONFIG),
        phase_processing_profile=load_phase_processing_profile(PHASE_PROCESSING_PROFILE),
        attenuation_profile=_configured_attenuation_profile(),
    )
    record = next(item for item in result.modules if item.name == ATTENUATION_SHADOW_MODULE_NAME)
    assert record.status == "skipped"
    assert record.reason == "blockage_unavailable"
    assert not np.any(result.sweeps[0].optional_qc_fields[ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD])
