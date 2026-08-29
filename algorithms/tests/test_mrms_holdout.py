from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from rainpulse_algo.verification.mrms_holdout import (
    build_selection_evidence,
    load_holdout_region_catalog,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "mrms-holdout-regions-v1.yaml"
)


def _rows(month: str, region_ids: tuple[str, ...]) -> list[dict[str, object]]:
    year, month_number = (int(value) for value in month.split("-"))
    start = datetime(year, month_number, 1, tzinfo=UTC)
    values: list[dict[str, object]] = []
    for hour in range(24 * 20):
        valid_time = start + timedelta(hours=hour)
        for region_id in region_ids:
            rain_1 = 0.0
            rain_10 = 0.0
            if region_id == region_ids[0] and 5 * 24 + 8 <= hour <= 5 * 24 + 12:
                rain_1 = 0.08
                rain_10 = 0.03
            if region_id == region_ids[1] and 14 * 24 + 8 <= hour <= 14 * 24 + 12:
                rain_1 = 0.06
                rain_10 = 0.02
            values.append(
                {
                    "month": month,
                    "region_id": region_id,
                    "valid_time_utc": valid_time.isoformat().replace("+00:00", "Z"),
                    "valid_fraction": 1.0,
                    "rain_fraction_ge_0p1": rain_1,
                    "rain_fraction_ge_1": rain_1,
                    "rain_fraction_ge_10": rain_10,
                    "maximum_rate_mm_h": 40.0 if rain_10 else 0.0,
                }
            )
    return values


def test_catalog_freezes_ten_unique_regions_and_coordinate_hashes() -> None:
    catalog = load_holdout_region_catalog(CATALOG_PATH)

    assert catalog.catalog_version == "mrms-holdout-regions-v1"
    assert len(catalog.regions) == 10
    assert len({region.region_id for region in catalog.regions}) == 10
    assert all(region.grid.shape == (201, 501) for region in catalog.regions)
    assert len(catalog.catalog_sha256) == 64


def test_observation_only_selector_freezes_four_wet_two_dry_and_fifty_issues() -> None:
    catalog = load_holdout_region_catalog(CATALOG_PATH)
    regions = tuple(region.region_id for region in catalog.regions[:3])
    months = ("2024-06", "2025-01")
    rows = _rows(months[0], regions) + _rows(months[1], regions)

    evidence = build_selection_evidence(
        rows=rows,
        catalog=catalog,
        months=months,
        manifests=[{"month": month, "complete": True} for month in months],
        generated_at=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert evidence["model_forecast_or_skill_fields_read"] is False
    assert evidence["selected_case_count"] == 6
    assert evidence["selected_issue_count"] == 50
    assert sum(case["category"] == "wet" for case in evidence["selected_cases"]) == 4
    assert sum(case["category"] == "dry" for case in evidence["selected_cases"]) == 2
    assert len(evidence["screened_observation_statistics_sha256"]) == 64
    for month in months:
        selected = [
            case
            for case in evidence["selected_cases"]
            if case["anchor_time_utc"].startswith(month)
        ]
        wet_regions = {
            case["observation_screen"]["region_id"]
            for case in selected
            if case["category"] == "wet"
        }
        assert len(wet_regions) == 2


def test_split_v2_assigns_distinct_development_namespace_without_changing_selection() -> None:
    catalog = load_holdout_region_catalog(CATALOG_PATH)
    regions = tuple(region.region_id for region in catalog.regions[:3])
    rows = _rows("2022-01", regions)

    evidence = build_selection_evidence(
        rows=rows,
        catalog=catalog,
        months=("2022-01",),
        manifests=[{"month": "2022-01", "complete": True}],
        generated_at=datetime(2026, 8, 29, tzinfo=UTC),
        selection_role="development",
    )

    assert evidence["selection_protocol_version"] == "mrms-observation-split-v2"
    assert evidence["selection_role"] == "development"
    assert evidence["case_namespace"] == "development"
    assert all(case["case_id"].startswith("development_") for case in evidence["selected_cases"])
    assert evidence["model_forecast_or_skill_fields_read"] is False
