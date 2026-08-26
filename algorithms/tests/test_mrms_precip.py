from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from affine import Affine

import rainpulse_algo.datasets.mrms_precip as mrms_precip_module
from rainpulse_algo.datasets.mrms_precip import (
    MRMSPrecipFrame,
    MRMSSourceState,
    build_mrms_validation_sequence,
    read_mrms_precip_frame,
)
from rainpulse_algo.grid import RegularLatLonGrid


def tiny_mrms_grid() -> RegularLatLonGrid:
    return RegularLatLonGrid(
        grid_id="tiny_mrms_grid_v1",
        config_version="tiny-mrms-grid-v1",
        west=-99.995,
        east=-99.975,
        south=30.005,
        north=30.015,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=3,
        latitude_count=2,
        reference_latitude_deg=30.01,
        ancillary_domain_id="mrms-validation-conus-v1",
    )


class FakeMRMSDataset:
    driver = "GRIB"
    count = 1
    width = 3
    height = 2
    transform = Affine(0.01, 0.0, -100.0, 0.0, -0.01, 30.02)

    def tags(self, band: int) -> dict[str, str]:
        assert band == 1
        return {
            "GRIB_DISCIPLINE": "209",
            "GRIB_ELEMENT": "PrecipRate",
            "GRIB_UNIT": "[mm/hr]",
            "GRIB_VALID_TIME": "1627783200",
        }

    def read(self, band: int, *, window) -> np.ndarray:
        assert band == 1
        assert window.width == 3
        assert window.height == 2
        return np.asarray(
            [
                [1.0, 0.0, -1.0],
                [-3.0, 2.0, 0.0],
            ],
            dtype="float32",
        )


def test_reader_crops_to_ascending_grid_and_preserves_mrms_source_states(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        mrms_precip_module.rasterio,
        "open",
        lambda _: nullcontext(FakeMRMSDataset()),
    )

    frame = read_mrms_precip_frame(
        Path("MRMS_PrecipRate_00.00_20210801-020000.grib2.gz"),
        tiny_mrms_grid(),
    )

    np.testing.assert_allclose(
        frame.rate_mm_h,
        np.asarray([[np.nan, 2.0, 0.0], [1.0, 0.0, np.nan]], dtype="float32"),
        equal_nan=True,
    )
    np.testing.assert_array_equal(frame.valid_mask, np.asarray([[0, 1, 1], [1, 1, 0]]))
    np.testing.assert_array_equal(
        frame.source_state,
        np.asarray(
            [
                [MRMSSourceState.NO_COVERAGE, MRMSSourceState.RAIN, MRMSSourceState.NO_RAIN],
                [MRMSSourceState.RAIN, MRMSSourceState.NO_RAIN, MRMSSourceState.MISSING],
            ],
            dtype="int8",
        ),
    )
    assert frame.valid_time.isoformat() == "2021-08-01T02:00:00+00:00"


def test_sequence_uses_only_past_ten_minute_frames_for_exact_five_minute_input() -> None:
    issue_time = datetime(2021, 8, 29, 18, 0, tzinfo=UTC)
    frames: dict[datetime, MRMSPrecipFrame] = {}
    for minutes_before, value in ((20, 0.0), (10, 2.0), (0, 4.0)):
        valid_time = issue_time - timedelta(minutes=minutes_before)
        rate = np.full(tiny_mrms_grid().shape, value, dtype="float32")
        frames[valid_time] = MRMSPrecipFrame(
            valid_time=valid_time,
            rate_mm_h=rate,
            valid_mask=np.ones(rate.shape, dtype="uint8"),
            source_state=np.full(
                rate.shape,
                MRMSSourceState.NO_RAIN if value == 0.0 else MRMSSourceState.RAIN,
                dtype="int8",
            ),
            source_path=f"frame-{valid_time:%H%M}.grib2.gz",
        )

    sequence = build_mrms_validation_sequence(frames, issue_time, tiny_mrms_grid())

    assert sequence.frame_times == tuple(
        issue_time - timedelta(minutes=minutes) for minutes in (20, 15, 10, 5, 0)
    )
    np.testing.assert_allclose(sequence.rate_mm_h[:, 0, 0], [0.0, 1.0, 2.0, 3.0, 4.0])
    np.testing.assert_allclose(
        sequence.reflectivity_dbz[:, 0, 0],
        [0.0, 23.0103, 27.8268, 30.6442, 32.6433],
        atol=1e-4,
    )
    np.testing.assert_array_equal(sequence.interpolated_frames, [0, 1, 0, 1, 0])
    np.testing.assert_allclose(sequence.data_age_minutes[:, 0, 0], [0.0, 5.0, 0.0, 5.0, 0.0])
    assert all(
        source_time <= issue_time
        for basis in sequence.source_basis_times
        for source_time in basis
    )
    assert sequence.operational_eligible is False
    assert sequence.reflectivity_provenance == "surrogate_from_mrms_rate_z200_r1p6"
