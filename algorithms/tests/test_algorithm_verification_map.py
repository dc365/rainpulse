from __future__ import annotations

import hashlib
import json
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from rainpulse_algo.diagnostics.png import png_dimensions
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.verification.map_bundle import (
    build_probabilistic_verification_map_bundle,
    build_verification_map_bundle,
    load_verification_map_profile,
    write_verification_map_bundle,
)
from rainpulse_algo.verification.probability_map_bundle import (
    build_probability_verification_map_bundle,
    load_probability_verification_map_profile,
    write_probability_verification_map_bundle,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "verification" / "algorithm-map-v1.yaml"
PROBABILITY_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "algorithm-probability-map-v1.yaml"
)


def _grid() -> RegularLatLonGrid:
    return RegularLatLonGrid(
        grid_id="verification_grid",
        config_version="verification-grid-v1",
        west=118.0,
        east=118.03,
        south=25.0,
        north=25.02,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=4,
        latitude_count=3,
        reference_latitude_deg=25.01,
        ancillary_domain_id="verification-domain",
    )


def test_map_bundle_preserves_grid_states_identity_and_north_up_png(tmp_path: Path) -> None:
    profile = load_verification_map_profile(PROFILE_PATH)
    grid = _grid()
    issue_time = datetime(2021, 8, 10, 17, 0, tzinfo=UTC)
    truth = np.asarray(
        [[[0.0, 0.1, 5.0, np.nan], [1.0, 2.5, 10.0, 25.0], [50.0, 100.0, 200.0, 0.0]]],
        dtype="float32",
    )
    truth_valid = np.isfinite(truth).astype("uint8")
    forecasts = {
        model: (truth.copy(), truth_valid.copy()) for model in ("lk", "persistence", "translation")
    }
    velocity = np.zeros((2, *grid.shape), dtype="float32")
    velocity[0] = 1.0
    motion_valid = np.ones(grid.shape, dtype="uint8")

    manifest, objects = build_verification_map_bundle(
        profile=profile,
        verification_profile_version="generic-verification-v1",
        case_id="wet_case",
        truth_kind="observed_rate",
        issue_time=issue_time,
        lead_minutes=(10,),
        grid=grid,
        truth_rate=truth,
        truth_valid=truth_valid,
        forecasts=forecasts,
        velocity_pixels_per_step=velocity,
        motion_valid_mask=motion_valid,
        motion_fallback_used=False,
        motion_fallback_reason=None,
    )

    assert manifest["grid"]["pixel_edge_bounds"] == [117.995, 24.995, 118.035, 25.025]
    assert manifest["grid"]["width"] == 4
    assert manifest["grid"]["height"] == 3
    assert len(manifest["layers"]) == 4
    assert 0 < len(manifest["motion"]["vectors"]) <= 200
    truth_layer = next(layer for layer in manifest["layers"] if layer["role"] == "truth")
    assert truth_layer["valid_cell_count"] == 11
    assert truth_layer["missing_cell_count"] == 1
    assert truth_layer["no_rain_cell_count"] == 2
    assert truth_layer["rain_cell_count"] == 9
    data = objects[truth_layer["object_path"]]
    assert png_dimensions(data) == (4, 3)
    assert hashlib.sha256(data).hexdigest() == truth_layer["sha256"]
    rows = _decode_rgba_rows(data)
    assert tuple(rows[0, 0]) == (238, 138, 45, 218)  # north row starts at 50 mm/h
    assert tuple(rows[-1, 0]) == (220, 230, 226, 56)  # valid zero is visible
    assert tuple(rows[-1, -1]) == (0, 0, 0, 0)  # missing stays transparent

    issue_directory = write_verification_map_bundle(tmp_path, manifest, objects)
    persisted = json.loads((issue_directory / "manifest.json").read_text())
    assert persisted == manifest
    assert issue_directory.stat().st_mode & 0o777 == 0o755
    assert not (tmp_path / ".temporary").exists()


def test_map_profile_keeps_one_palette_and_limits_motion_payload() -> None:
    profile = load_verification_map_profile(PROFILE_PATH)

    assert profile.profile_version == "algorithm-verification-map-v1"
    assert profile.palette_version == "rainfall-operational-v1"
    assert profile.rain_threshold_mm_h == 0.1
    assert profile.maximum_motion_vectors == 200
    assert profile.sample_step_pixels == 25


def test_probabilistic_map_bundle_renders_common_support_ensemble_means() -> None:
    profile = load_verification_map_profile(PROFILE_PATH)
    grid = _grid()
    truth = np.full((1, *grid.shape), 5.0, dtype="float32")
    truth_valid = np.ones_like(truth, dtype="uint8")
    nowcastnet = np.stack((truth, truth * 3), axis=0)
    nowcastnet_valid = np.ones_like(nowcastnet, dtype="uint8")
    nowcastnet_valid[1, 0, 0, 0] = 0
    steps = np.stack((truth * 2, truth * 4), axis=0)
    steps_valid = np.ones_like(steps, dtype="uint8")
    deterministic = {
        "lk": (truth.copy(), truth_valid.copy()),
        "persistence": (truth.copy(), truth_valid.copy()),
        "phase_correlation": (truth.copy(), truth_valid.copy()),
    }

    manifest, _ = build_probabilistic_verification_map_bundle(
        profile=profile,
        verification_profile_version="rp026-mrms-nowcastnet-v1",
        case_id="wet_case",
        truth_kind="observed_mrms_preciprate_10min",
        issue_time=datetime(2025, 3, 4, 6, tzinfo=UTC),
        lead_minutes=(10,),
        grid=grid,
        truth_rate=truth,
        truth_valid=truth_valid,
        nowcastnet_members=nowcastnet,
        nowcastnet_member_valid=nowcastnet_valid,
        steps_members=steps,
        steps_member_valid=steps_valid,
        deterministic_forecasts=deterministic,
        velocity_pixels_per_step=np.zeros((2, *grid.shape), dtype="float32"),
        motion_valid_mask=np.ones(grid.shape, dtype="uint8"),
        motion_fallback_used=False,
        motion_fallback_reason=None,
    )

    assert manifest["operational_eligible"] is False
    assert [layer.get("model") for layer in manifest["layers"]] == [
        None,
        "nowcastnet",
        "steps",
        "lk",
        "persistence",
        "phase_correlation",
    ]
    candidate = next(layer for layer in manifest["layers"] if layer.get("model") == "nowcastnet")
    assert candidate["valid_cell_count"] == grid.latitude_count * grid.longitude_count - 1
    assert candidate["missing_cell_count"] == 1


def test_probability_map_bundle_renders_truth_and_raw_ensemble_frequencies(
    tmp_path: Path,
) -> None:
    profile = load_probability_verification_map_profile(PROBABILITY_PROFILE_PATH)
    grid = _grid()
    truth = np.asarray([[[0, 1, 5, 10], [20, 50, 2, 0], [6, 9, 12, 3]]], dtype="float32")
    truth_valid = np.ones_like(truth, dtype="uint8")
    truth_valid[0, 0, 0] = 0
    nowcastnet = np.stack((truth, truth * 2, truth * 3, truth * 4), axis=0)
    nowcastnet_valid = np.ones_like(nowcastnet, dtype="uint8")
    nowcastnet_valid[3, 0, 1, 1] = 0
    steps = np.stack((truth, truth * 2), axis=0)
    steps_valid = np.ones_like(steps, dtype="uint8")

    manifest, objects = build_probability_verification_map_bundle(
        profile=profile,
        verification_profile_version="rp026-mrms-nowcastnet-v1",
        case_id="wet_case",
        truth_kind="observed_mrms_preciprate_10min",
        issue_time=datetime(2025, 3, 4, 6, tzinfo=UTC),
        lead_minutes=(10,),
        thresholds_mm_h=(1, 5, 10, 20, 50),
        grid=grid,
        truth_rate=truth,
        truth_valid=truth_valid,
        nowcastnet_members=nowcastnet,
        nowcastnet_member_valid=nowcastnet_valid,
        steps_members=steps,
        steps_member_valid=steps_valid,
    )

    assert manifest["calibration_status"] == "raw_ensemble_relative_frequency_uncalibrated"
    assert manifest["operational_eligible"] is False
    assert manifest["product_publication_enabled"] is False
    assert manifest["thresholds_mm_h"] == [1.0, 5.0, 10.0, 20.0, 50.0]
    assert len(manifest["layers"]) == 15
    selected = [
        layer
        for layer in manifest["layers"]
        if layer["lead_minutes"] == 10 and layer["threshold_mm_h"] == 5
    ]
    assert [layer.get("model") for layer in selected] == [None, "nowcastnet", "steps"]
    assert selected[0]["event_cell_count"] == 7
    assert selected[0]["missing_cell_count"] == 1
    assert selected[1]["event_cell_count"] == 8
    assert selected[1]["missing_cell_count"] == 1
    candidate_rows = _decode_rgba_rows(objects[selected[1]["object_path"]])
    assert tuple(candidate_rows[0, 3]) == (81, 69, 164, 218)  # 75% at 3 mm/h
    assert tuple(candidate_rows[1, 1]) == (0, 0, 0, 0)  # any invalid member is missing
    issue_directory = write_probability_verification_map_bundle(tmp_path, manifest, objects)
    assert json.loads((issue_directory / "manifest.json").read_text()) == manifest


def test_probability_map_profile_has_an_independent_percent_palette() -> None:
    profile = load_probability_verification_map_profile(PROBABILITY_PROFILE_PATH)

    assert profile.palette_version == "raw-exceedance-probability-v1"
    assert profile.probability_stops[-1].minimum == 100
    assert profile.probability_stops[-1].color == "#b31945"


def _decode_rgba_rows(data: bytes) -> np.ndarray:
    position = 8
    compressed = bytearray()
    width = height = 0
    while position < len(data):
        size = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + size]
        if kind == b"IHDR":
            width, height = struct.unpack(">II", payload[:8])
        elif kind == b"IDAT":
            compressed.extend(payload)
        position += 12 + size
    scanlines = zlib.decompress(bytes(compressed))
    rows = np.frombuffer(scanlines, dtype="uint8").reshape(height, 1 + width * 4)
    assert np.all(rows[:, 0] == 0)
    return rows[:, 1:].reshape(height, width, 4)
