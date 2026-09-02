from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import cv2
import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.diagnostics.png import PNG_SIGNATURE, png_dimensions
from rainpulse_algo.diagnostics.profile import load_diagnostic_profile
from rainpulse_algo.diagnostics.renderer import (
    DiagnosticInputError,
    _scalar_rgba,
    build_diagnostic_bundle,
    validate_diagnostic_bundle,
)
from rainpulse_algo.radar.analysis_zarr import build_radar_analysis_zarr_store
from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store

from .test_qpe import ANALYSIS_ID, mosaic_fixture
from .test_qpe import profile as qpe_profile
from .test_radar_qc import FLAG_CONFIG, QC_CONFIG, normalized_fixture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_CONFIG = (
    REPOSITORY_ROOT
    / "configs"
    / "diagnostics"
    / "rp012-operational-diagnostics-v2.yaml"
)
LEGACY_DIAGNOSTIC_CONFIG = (
    REPOSITORY_ROOT
    / "configs"
    / "diagnostics"
    / "rp012-operational-diagnostics-v1.yaml"
)
SCAN_ID = UUID("10000000-0000-4000-8000-000000000004")
JOB_ID = UUID("83000000-0000-4000-8000-000000000001")


def analysis_fixture() -> dict[str, bytes]:
    mosaic = mosaic_fixture(operational_eligible=False)
    mosaic_store = MemoryStore()
    mosaic_store.update(mosaic)
    root = zarr.open_group(store=mosaic_store, mode="a")
    root.attrs.update(
        {
            "coordinate_centre_bounds": [118.0, 25.0, 118.01, 25.01],
            "pixel_edge_bounds": [117.995, 24.995, 118.015, 25.015],
            "longitude_interval_deg": 0.01,
            "latitude_interval_deg": 0.01,
        }
    )
    mosaic = {str(key): bytes(value) for key, value in mosaic_store.items()}
    return build_radar_analysis_zarr_store(
        mosaic,
        mosaic_uri="s3://rainpulse/analysis/mosaic/fixture/mosaic.zarr",
        analysis_id=ANALYSIS_ID,
        profile=qpe_profile(),
        asset_id="77000000-0000-4000-8000-000000000001",
    )


def qc_fixture(tmp_path: Path) -> dict[str, bytes]:
    normalized = normalized_fixture(tmp_path)
    result = apply_basic_qc(normalized, load_qc_profile(QC_CONFIG, FLAG_CONFIG))
    return build_qc_zarr_store(
        normalized,
        result,
        asset_id="50000000-0000-4000-8000-000000000001",
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9598/fixture/volume.zarr",
        provenance={"scan_id": str(SCAN_ID)},
    )


def flag_definitions() -> dict[str, int]:
    return {
        "RADIAL_INTERFERENCE": 8,
        "MISSING": 4096,
        "LOW_QUALITY": 16384,
    }


def test_profile_freezes_every_grid_and_polar_layer() -> None:
    profile = load_diagnostic_profile(DIAGNOSTIC_CONFIG)

    assert profile.profile_version == "rp012-operational-diagnostics-v2"
    assert profile.renderer_version == "radar-diagnostic-renderer-1.1.0"
    assert len(profile.layers) == 11
    assert profile.grid_render.missing_alpha == 0
    assert profile.polar_render.sweep_selection == "lowest_dbzh_sweep"


def test_scalar_render_keeps_valid_zero_visible_and_missing_transparent() -> None:
    rgba = _scalar_rgba(
        np.array([[0.0, np.nan]], dtype="float32"),
        np.array([[True, False]]),
        ((0.0, "#dce9ee"), (1.0, "#4ba3f2")),
    )

    assert rgba[0, 0, 3] == 255
    assert rgba[0, 1, 3] == 0


def test_bundle_renders_real_grid_and_polar_pngs(tmp_path: Path) -> None:
    objects = build_diagnostic_bundle(
        analysis_fixture(),
        [("z9598", SCAN_ID, qc_fixture(tmp_path))],
        analysis_uri="s3://rainpulse/analysis/fixture/analysis.zarr",
        analysis_id=ANALYSIS_ID,
        job_id=JOB_ID,
        profile=load_diagnostic_profile(DIAGNOSTIC_CONFIG),
        flag_definitions=flag_definitions(),
    )
    validation = validate_diagnostic_bundle(objects)
    manifest = validation["manifest"]
    layers = {item["layer_id"]: item for item in manifest["layers"]}

    assert validation["layer_count"] == 11
    assert validation["grid_layer_count"] == 7
    assert validation["radar_count"] == 1
    assert layers["grid-rate-qpe"]["bounds"] == [117.995, 24.995, 118.015, 25.015]
    assert layers["grid-rate-qpe"]["width"] == 4
    assert layers["grid-rate-qpe"]["height"] == 4
    assert layers["radar-z9598-dbzh-raw"]["width"] == 640
    assert layers["radar-z9598-dbzh-raw"]["scope"] == "polar"
    assert layers["radar-z9598-dbzh-raw"]["sweep_number"] == 0
    for layer in manifest["layers"]:
        data = objects[layer["object_path"]]
        assert data.startswith(PNG_SIGNATURE)
        assert png_dimensions(data) == (layer["width"], layer["height"])


def test_bundle_masks_hard_reject_flags_from_business_reflectivity(tmp_path: Path) -> None:
    qc_objects = qc_fixture(tmp_path)
    qc_store = MemoryStore()
    qc_store.update(qc_objects)
    qc_root = zarr.open_group(store=qc_store, mode="a")
    sweep = qc_root["sweep_000"]
    for field in ("DBZH_RAW", "DBZH_QC"):
        values = sweep[field][:]
        values[0, :] = 60.0
        sweep[field][:] = values
    flags = sweep["QC_FLAGS"][:]
    flags[0, :] |= np.uint32(flag_definitions()["RADIAL_INTERFERENCE"])
    sweep["QC_FLAGS"][:] = flags
    valid = sweep["VALID_MASK"][:]
    valid[0, :] = 1
    sweep["VALID_MASK"][:] = valid
    qc_objects = {str(key): bytes(value) for key, value in qc_store.items()}

    objects = build_diagnostic_bundle(
        analysis_fixture(),
        [("z9598", SCAN_ID, qc_objects)],
        analysis_uri="s3://rainpulse/analysis/fixture/analysis.zarr",
        analysis_id=ANALYSIS_ID,
        job_id=JOB_ID,
        profile=load_diagnostic_profile(DIAGNOSTIC_CONFIG),
        flag_definitions=flag_definitions(),
    )
    manifest = json.loads(objects["manifest.json"])
    layers = {item["layer_id"]: item for item in manifest["layers"]}
    raw_layer = layers["radar-z9598-dbzh-raw"]
    business_layer = layers["radar-z9598-dbzh-qc"]

    raw = cv2.imdecode(
        np.frombuffer(objects[raw_layer["object_path"]], dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    business = cv2.imdecode(
        np.frombuffer(objects[business_layer["object_path"]], dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )

    assert business_layer["title"] == "Z9598 · 业务质控反射率"
    assert np.count_nonzero(business[:, :, 3]) < np.count_nonzero(raw[:, :, 3])
    assert "radar-z9598-qc-flags" in layers


def test_legacy_renderer_keeps_its_original_business_reflectivity_semantics(
    tmp_path: Path,
) -> None:
    qc_objects = qc_fixture(tmp_path)
    qc_store = MemoryStore()
    qc_store.update(qc_objects)
    qc_root = zarr.open_group(store=qc_store, mode="a")
    sweep = qc_root["sweep_000"]
    for field in ("DBZH_RAW", "DBZH_QC"):
        values = sweep[field][:]
        values[0, :] = 60.0
        sweep[field][:] = values
    flags = sweep["QC_FLAGS"][:]
    flags[0, :] |= np.uint32(flag_definitions()["RADIAL_INTERFERENCE"])
    sweep["QC_FLAGS"][:] = flags
    valid = sweep["VALID_MASK"][:]
    valid[0, :] = 1
    sweep["VALID_MASK"][:] = valid
    qc_objects = {str(key): bytes(value) for key, value in qc_store.items()}

    objects = build_diagnostic_bundle(
        analysis_fixture(),
        [("z9598", SCAN_ID, qc_objects)],
        analysis_uri="s3://rainpulse/analysis/fixture/analysis.zarr",
        analysis_id=ANALYSIS_ID,
        job_id=JOB_ID,
        profile=load_diagnostic_profile(LEGACY_DIAGNOSTIC_CONFIG),
        flag_definitions=flag_definitions(),
    )
    manifest = json.loads(objects["manifest.json"])
    layers = {item["layer_id"]: item for item in manifest["layers"]}
    business_layer = layers["radar-z9598-dbzh-qc"]
    business = cv2.imdecode(
        np.frombuffer(objects[business_layer["object_path"]], dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )

    assert business_layer["title"] == "Z9598 · 质控后反射率"
    assert np.count_nonzero(business[:, :, 3]) > 0


def test_bundle_validation_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    objects = build_diagnostic_bundle(
        analysis_fixture(),
        [("z9598", SCAN_ID, qc_fixture(tmp_path))],
        analysis_uri="s3://rainpulse/analysis/fixture/analysis.zarr",
        analysis_id=ANALYSIS_ID,
        job_id=JOB_ID,
        profile=load_diagnostic_profile(DIAGNOSTIC_CONFIG),
        flag_definitions=flag_definitions(),
    )
    manifest = json.loads(objects["manifest.json"])
    manifest["layers"][0]["object_path"] = "../secret"
    objects["manifest.json"] = json.dumps(manifest).encode()

    with pytest.raises(DiagnosticInputError, match="identity or path"):
        validate_diagnostic_bundle(objects)
