from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest

from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_texture import (
    TEXTURE_AZIMUTH_HALF_WINDOW_DEG,
    TEXTURE_MIN_SUPPORT_FRACTION,
    TEXTURE_RANGE_WINDOW_METERS,
    build_texture_diagnostics,
)
from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store

from .test_radar_qc import FLAG_CONFIG, RP042_QC_CONFIG, open_store, synthetic_normalized_fixture


def test_dbzh_texture_is_zero_for_constant_field() -> None:
    diagnostics = build_texture_diagnostics(
        azimuth_deg=np.array([0.0, 1.0, 2.0], dtype="float32"),
        range_m=np.arange(250.0, 250.0 * 10, 250.0, dtype="float32"),
        fields={"DBZH": np.full((3, 9), 20.0, dtype="float32")},
    )

    texture = diagnostics.fields["DBZH_TEXTURE"]
    support = diagnostics.fields["DBZH_TEXTURE_SUPPORT_RATE"]
    available = diagnostics.fields["DBZH_TEXTURE_AVAILABLE_MASK"]

    assert np.allclose(texture, 0.0)
    assert np.allclose(support, 1.0)
    assert np.array_equal(available, np.ones((3, 9), dtype="uint8"))


def test_phidp_circular_variance_detects_nonzero_wrapped_steps() -> None:
    diagnostics = build_texture_diagnostics(
        azimuth_deg=np.array([0.0], dtype="float32"),
        range_m=np.arange(250.0, 250.0 * 9, 250.0, dtype="float32"),
        fields={
            "PHIDP": np.array(
                [[0.0, 0.0, 120.0, 120.0, 0.0, 0.0, 120.0, 120.0]],
                dtype="float32",
            )
        },
    )

    variance = diagnostics.fields["PHIDP_CIRCULAR_VARIANCE"]
    support = diagnostics.fields["PHIDP_CIRCULAR_VARIANCE_SUPPORT_RATE"]
    available = diagnostics.fields["PHIDP_CIRCULAR_VARIANCE_AVAILABLE_MASK"]

    assert support[0, 3] == pytest.approx(1.0)
    assert available[0, 3] == 1
    assert variance[0, 3] > 0.0


def test_texture_wraps_azimuth_smoothly_across_zero() -> None:
    diagnostics = build_texture_diagnostics(
        azimuth_deg=np.array([359.0, 0.0, 1.0], dtype="float32"),
        range_m=np.array([1_000.0], dtype="float32"),
        fields={"DBZH": np.array([[0.0], [10.0], [20.0]], dtype="float32")},
    )

    texture = diagnostics.fields["DBZH_TEXTURE"]
    support = diagnostics.fields["DBZH_TEXTURE_SUPPORT_RATE"]
    available = diagnostics.fields["DBZH_TEXTURE_AVAILABLE_MASK"]

    assert support[1, 0] == pytest.approx(1.0)
    assert available[1, 0] == 1
    assert texture[1, 0] == pytest.approx(1.4826 * 10.0)


def test_texture_does_not_cross_large_azimuth_gap() -> None:
    diagnostics = build_texture_diagnostics(
        azimuth_deg=np.array([359.0, 20.0, 21.0], dtype="float32"),
        range_m=np.array([1_000.0], dtype="float32"),
        fields={"DBZH": np.array([[0.0], [10.0], [20.0]], dtype="float32")},
    )

    texture = diagnostics.fields["DBZH_TEXTURE"]
    support = diagnostics.fields["DBZH_TEXTURE_SUPPORT_RATE"]

    assert support[0, 0] == pytest.approx(1.0)
    assert texture[0, 0] == pytest.approx(0.0)


def test_texture_marks_insufficient_support_unavailable() -> None:
    diagnostics = build_texture_diagnostics(
        azimuth_deg=np.array([0.0], dtype="float32"),
        range_m=np.arange(250.0, 250.0 * 8, 250.0, dtype="float32"),
        fields={
            "DBZH": np.array(
                [[np.nan, np.nan, 1.0, 2.0, 3.0, np.nan, np.nan]],
                dtype="float32",
            )
        },
    )

    texture = diagnostics.fields["DBZH_TEXTURE"]
    support = diagnostics.fields["DBZH_TEXTURE_SUPPORT_RATE"]
    available = diagnostics.fields["DBZH_TEXTURE_AVAILABLE_MASK"]

    assert support[0, 3] == pytest.approx(3.0 / 7.0)
    assert support[0, 3] < TEXTURE_MIN_SUPPORT_FRACTION
    assert available[0, 3] == 0
    assert np.isnan(texture[0, 3])


def test_texture_is_consistent_for_same_physical_gradient_at_different_gate_spacing() -> None:
    ranges_250 = np.arange(0.0, 3_250.0, 250.0, dtype="float32")
    ranges_500 = np.arange(0.0, 3_500.0, 500.0, dtype="float32")
    dbzh_250 = (ranges_250 / 1_000.0)[None, :]
    dbzh_500 = (ranges_500 / 1_000.0)[None, :]

    texture_250 = build_texture_diagnostics(
        azimuth_deg=np.array([0.0], dtype="float32"),
        range_m=ranges_250,
        fields={"DBZH": dbzh_250.astype("float32")},
    ).fields["DBZH_TEXTURE"]
    texture_500 = build_texture_diagnostics(
        azimuth_deg=np.array([0.0], dtype="float32"),
        range_m=ranges_500,
        fields={"DBZH": dbzh_500.astype("float32")},
    ).fields["DBZH_TEXTURE"]

    assert texture_250[0, 6] == pytest.approx(texture_500[0, 3], abs=1e-6)


def test_qc_zarr_writes_texture_diagnostics_and_provenance() -> None:
    dbzh = np.array(
        [
            [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0],
            [11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0],
            [12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0],
        ],
        dtype="float32",
    )
    normalized = synthetic_normalized_fixture(
        dbzh,
        azimuth_deg=np.array([359.0, 0.0, 1.0], dtype="float32"),
        range_m=np.arange(250.0, 250.0 * 9, 250.0, dtype="float32"),
        moments={
            "RHOHV": np.full(dbzh.shape, 0.95, dtype="float32"),
            "ZDR": np.full(dbzh.shape, 0.2, dtype="float32"),
            "PHIDP": np.array(
                [
                    [0.0, 0.0, 120.0, 120.0, 0.0, 0.0, 120.0, 120.0],
                    [0.0, 0.0, 120.0, 120.0, 0.0, 0.0, 120.0, 120.0],
                    [0.0, 0.0, 120.0, 120.0, 0.0, 0.0, 120.0, 120.0],
                ],
                dtype="float32",
            ),
        },
    )
    profile = load_qc_profile(RP042_QC_CONFIG, FLAG_CONFIG)

    result = apply_basic_qc(normalized, profile)
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000051"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9999/texture/volume.zarr",
    )
    root = open_store(objects)
    sweep = root["sweep_000"]
    module_names = {item["name"] for item in root.attrs["module_provenance"]}

    assert result.module_status("polar_texture") == "applied"
    assert "polar_texture" in module_names
    assert sweep["DBZH_TEXTURE"].shape == dbzh.shape
    assert sweep["DBZH_TEXTURE_SUPPORT_RATE"].shape == dbzh.shape
    assert sweep["DBZH_TEXTURE_AVAILABLE_MASK"].dtype == np.dtype("uint8")
    assert sweep["RHOHV_TEXTURE"].shape == dbzh.shape
    assert sweep["ZDR_TEXTURE"].shape == dbzh.shape
    assert sweep["PHIDP_CIRCULAR_VARIANCE"].shape == dbzh.shape
    assert np.all(sweep["PHIDP_CIRCULAR_VARIANCE"][:] >= 0.0)
    assert np.all(sweep["PHIDP_CIRCULAR_VARIANCE"][:] <= 1.0)
    assert np.all(sweep["PHIDP_CIRCULAR_VARIANCE_SUPPORT_RATE"][:] <= 1.0)
    assert TEXTURE_RANGE_WINDOW_METERS == pytest.approx(1_750.0)
    assert TEXTURE_AZIMUTH_HALF_WINDOW_DEG == pytest.approx(1.5)
