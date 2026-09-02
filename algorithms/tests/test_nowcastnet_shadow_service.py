from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from rainpulse_algo.nowcast.nowcastnet_shadow import (
    FixedROI,
    NowcastNetShadowProfile,
    ShadowActivation,
)
from rainpulse_algo.nowcast.nowcastnet_shadow_service import (
    AnalysisReference,
    parse_analysis_catalog,
    probe_sequence,
    select_latest_complete_sequence,
)


def profile() -> NowcastNetShadowProfile:
    return NowcastNetShadowProfile(
        profile_version="fujian-nowcastnet-shadow-v1",
        source_model_profile="rp026-nowcastnet-offline-v1",
        grid_id="fuzhou_118_123_25_27_0p01deg_v1",
        grid_config_version="fuzhou-grid-0p01deg-v1",
        input_frames=9,
        timestep_minutes=10,
        output_lead_minutes=tuple(range(10, 121, 10)),
        missing_policy="reject_any_missing",
        spatial_multiple=32,
        roi=FixedROI(y_start=0, x_start=0, height=32, width=64),
        activation=ShadowActivation(
            input_probe_enabled=True,
            inference_enabled=False,
            product_publication_enabled=False,
            operational_eligible=False,
            spatial_shape_validated=False,
        ),
    )


def references(*, missing_minutes: set[int] | None = None) -> list[AnalysisReference]:
    issue = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    missing = missing_minutes or set()
    result = []
    for offset in range(0, 85, 5):
        if offset in missing:
            continue
        at = issue - timedelta(minutes=offset)
        result.append(
            AnalysisReference(
                analysis_id=f"analysis-{offset}",
                analysis_time=at,
                grid_id="fuzhou_118_123_25_27_0p01deg_v1",
                analysis_uri=f"s3://rainpulse/analysis-{offset}.zarr",
            )
        )
    return result


def test_selects_latest_nine_exact_ten_minute_frames() -> None:
    issue, selected = select_latest_complete_sequence(references(), profile=profile())
    assert issue == datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    assert len(selected) == 9
    assert [item.analysis_time.minute for item in selected] == [40, 50, 0, 10, 20, 30, 40, 50, 0]


def test_missing_required_frame_stays_ineligible() -> None:
    status = probe_sequence(
        references(missing_minutes={40}),
        profile=profile(),
        loader=lambda _reference: (
            np.ones((64, 96), dtype="float32"),
            np.ones((64, 96), dtype="uint8"),
        ),
        checked_at=datetime(2026, 9, 1, 2, 1, tzinfo=UTC),
    )
    assert status.status == "input_ineligible"
    assert status.reason == "missing_required_frame"


def test_complete_input_reports_shape_validation_gate() -> None:
    status = probe_sequence(
        references(),
        profile=profile(),
        loader=lambda _reference: (
            np.ones((64, 96), dtype="float32"),
            np.ones((64, 96), dtype="uint8"),
        ),
        checked_at=datetime(2026, 9, 1, 2, 1, tzinfo=UTC),
    )
    assert status.status == "input_ineligible"
    assert status.reason == "spatial_shape_not_validated"
    assert status.frame_count == 9
    assert status.common_valid_ratio == 1


def test_catalog_rejects_other_grids_and_duplicate_times() -> None:
    payload = {
        "items": [
            {
                "analysis_id": "a",
                "analysis_time": "2026-09-01T02:00:00Z",
                "grid_id": "fuzhou_118_123_25_27_0p01deg_v1",
                "analysis_uri": "s3://rainpulse/a",
            },
            {
                "analysis_id": "duplicate",
                "analysis_time": "2026-09-01T02:00:00Z",
                "grid_id": "fuzhou_118_123_25_27_0p01deg_v1",
                "analysis_uri": "s3://rainpulse/b",
            },
            {
                "analysis_id": "other",
                "analysis_time": "2026-09-01T02:00:00Z",
                "grid_id": "other-grid",
                "analysis_uri": "s3://rainpulse/c",
            },
        ]
    }
    parsed = parse_analysis_catalog(
        payload, grid_id="fuzhou_118_123_25_27_0p01deg_v1"
    )
    assert [item.analysis_id for item in parsed] == ["a"]
