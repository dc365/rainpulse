#!/usr/bin/env python3
"""Publish a clearly isolated synthetic RadarAnalysis sequence for RP-013 acceptance.

This utility is not an operational data producer. It creates three deterministic,
contract-valid frames below ``rp013-acceptance/synthetic`` and emits the SQL needed
to register those frames as acceptance-only analysis cycles.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid, load_grid_config
from rainpulse_algo.radar.analysis_zarr import validate_radar_analysis_zarr_store
from rainpulse_algo.radar.mosaic_zarr import REQUIRED_FIELDS as MOSAIC_FIELDS
from rainpulse_algo.worker.contracts import (
    CompletedAsset,
    JobCompleted,
    JobCompletedPayload,
)
from rainpulse_algo.worker.object_store import (
    AtomicObjectPublisher,
    artifact_sha256,
    minio_client_from_environment,
)

ISSUE_TIME = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
ANALYSIS_IDS = (
    UUID("81300000-0000-4000-8000-000000000001"),
    UUID("81300000-0000-4000-8000-000000000002"),
    UUID("81300000-0000-4000-8000-000000000003"),
)
RUN_IDS = (
    UUID("81300000-0000-4000-8000-000000000101"),
    UUID("81300000-0000-4000-8000-000000000102"),
    UUID("81300000-0000-4000-8000-000000000103"),
)
RAW_ASSET_IDS = (
    UUID("81300000-0000-4000-8000-000000000201"),
    UUID("81300000-0000-4000-8000-000000000202"),
    UUID("81300000-0000-4000-8000-000000000203"),
)
CONFIG_VERSION = "rp013-synthetic-acceptance-v1"
QPE_CONFIG_VERSION = "rp011-basic-qpe-v1"
QPE_ALGORITHM_VERSION = "basic-zr-qpe-1.0.0"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    configured_grid = os.getenv("RAINPULSE_GRID_CONFIG")
    parser.add_argument(
        "--grid-config",
        type=Path,
        default=Path(configured_grid) if configured_grid else None,
    )
    parser.add_argument(
        "--emit-sql",
        action="store_true",
        help="emit idempotent PostgreSQL fixture registration SQL",
    )
    arguments = parser.parse_args()
    if arguments.grid_config is None:
        parser.error("--grid-config or RAINPULSE_GRID_CONFIG is required")

    grid = load_grid_config(arguments.grid_config.resolve(strict=True))
    client = minio_client_from_environment()
    publisher = AtomicObjectPublisher(client)
    records = []
    for index, (analysis_id, run_id, raw_asset_id) in enumerate(
        zip(ANALYSIS_IDS, RUN_IDS, RAW_ASSET_IDS, strict=True)
    ):
        analysis_time = ISSUE_TIME - timedelta(
            minutes=5 * (len(ANALYSIS_IDS) - 1 - index)
        )
        objects = build_synthetic_analysis(
            analysis_id=analysis_id,
            raw_asset_id=raw_asset_id,
            analysis_time=analysis_time,
            grid=grid,
            data_age_minutes=0.75 - index * 0.25,
        )
        validation = validate_radar_analysis_zarr_store(objects)
        output_prefix = (
            f"s3://rainpulse/rp013-acceptance/synthetic/analysis/{analysis_id}/"
        )
        asset_uri = output_prefix + "analysis.zarr"
        bundle_sha256 = artifact_sha256(objects)
        bundle_size = sum(len(value) for value in objects.values())
        now = datetime.now(UTC)
        completion = JobCompleted(
            event_id=uuid5(analysis_id, "rp013-synthetic-fixture-completed"),
            occurred_at=now,
            run_id=run_id,
            job_id=analysis_id,
            trace_id=uuid5(analysis_id, "rp013-synthetic-fixture-trace"),
            payload=JobCompletedPayload(
                status="succeeded",
                started_at=now,
                finished_at=now,
                runtime_ms=0,
                assets=[
                    CompletedAsset(
                        asset_type="radar_analysis",
                        uri=asset_uri,
                        sha256=bundle_sha256,
                        size_bytes=bundle_size,
                        media_type="application/vnd+zarr",
                    )
                ],
                metrics={},
                diagnostics={"synthetic_acceptance_fixture": True},
            ),
        )
        published = publisher.publish(
            output_prefix=output_prefix,
            job_id=analysis_id,
            data=None,
            objects=objects,
            completion=completion,
            artifact_name="analysis.zarr",
        )
        records.append(
            {
                "analysis_id": str(analysis_id),
                "run_id": str(run_id),
                "analysis_time": analysis_time.isoformat(),
                "analysis_uri": published.asset_uri,
                "valid_coverage_ratio": validation["valid_cell_count"]
                / (grid.shape[0] * grid.shape[1]),
                "mean_quality_index": _mean_valid_quality(objects),
                "sha256": published.sha256,
                "size_bytes": published.size_bytes,
                "synthetic_acceptance_fixture": True,
            }
        )

    if arguments.emit_sql:
        print(registration_sql(records, grid))
    else:
        print(json.dumps(records, indent=2, sort_keys=True))


def build_synthetic_analysis(
    *,
    analysis_id: UUID,
    raw_asset_id: UUID,
    analysis_time: datetime,
    grid: RegularLatLonGrid,
    data_age_minutes: float,
) -> dict[str, bytes]:
    shape = grid.shape
    valid = np.ones(shape, dtype="uint8")
    missing_columns = max(1, round(shape[1] * 0.05))
    valid[:, -missing_columns:] = 0
    missing = valid == 0

    low_quality = np.zeros(shape, dtype="uint8")
    low_quality[:, 50:100] = valid[:, 50:100]
    no_rain = np.zeros(shape, dtype=bool)
    no_rain[:, :50] = valid[:, :50] == 1

    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.radar-analysis",
            "contract_version": "1.2",
            "asset_id": str(analysis_id),
            "analysis_id": str(analysis_id),
            "analysis_time": analysis_time.isoformat(),
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "coordinate_sha256": grid.coordinate_sha256,
            "crs": "EPSG:4326",
            "registration": "point",
            "profile_version": "rp010-qi-mosaic-v1",
            "mosaic_algorithm_version": "qi-mosaic-1.0.0",
            "flag_definition_version": "qc-flags-v1",
            "input_mosaic_uri": (
                "s3://rainpulse/rp013-acceptance/synthetic/mosaic/"
                f"{analysis_id}/mosaic.zarr"
            ),
            "input_asset_ids": [str(raw_asset_id)],
            "qc_pipeline_versions": ["basic-polar-qc-1.0.0"],
            "qpe_config_version": QPE_CONFIG_VERSION,
            "qpe_algorithm_version": QPE_ALGORITHM_VERSION,
            "qpe_maximum_rate_mm_h": 300.0,
            "operational_eligible": True,
            "operational_reasons": [],
            "synthetic_acceptance_fixture": True,
            "acceptance_config_version": CONFIG_VERSION,
        }
    )
    root.create_dataset("lat", data=grid.latitude)
    root.create_dataset("lon", data=grid.longitude)

    for name, dtype in MOSAIC_FIELDS.items():
        if dtype == np.dtype("float32"):
            values = _floating_field(
                name,
                shape=shape,
                no_rain=no_rain,
                low_quality=low_quality == 1,
                data_age_minutes=data_age_minutes,
            )
            values[missing] = np.nan
        elif name == "QC_FLAGS":
            values = np.zeros(shape, dtype=dtype)
            values[missing] = np.uint32(4096)
        elif name in {"SOURCE_RADAR", "CONTRIBUTOR_COUNT", "VALID_MASK"}:
            values = valid.astype(dtype)
        elif name == "LOW_QUALITY_MASK":
            values = low_quality.astype(dtype)
        else:
            values = np.zeros(shape, dtype=dtype)
        root.create_dataset(name, data=values)

    rate = np.full(shape, 2.0, dtype="float32")
    rate[no_rain] = 0.0
    rate[missing] = np.nan
    root.create_dataset("RATE_QPE", data=rate)

    valid_count = int(np.count_nonzero(valid))
    rain_count = int(np.count_nonzero((rate > 0) & ~missing))
    no_rain_count = valid_count - rain_count
    store["qpe/summary.json"] = json.dumps(
        {
            "analysis_id": str(analysis_id),
            "qpe_config_version": QPE_CONFIG_VERSION,
            "qpe_algorithm_version": QPE_ALGORITHM_VERSION,
            "input_mosaic_uri": root.attrs["input_mosaic_uri"],
            "valid_cell_count": valid_count,
            "missing_cell_count": int(np.count_nonzero(missing)),
            "rain_cell_count": rain_count,
            "no_rain_cell_count": no_rain_count,
            "capped_cell_count": 0,
            "mean_rate_mm_h": float(np.mean(rate[~missing])),
            "maximum_observed_rate_mm_h": float(np.max(rate[~missing])),
            "synthetic_acceptance_fixture": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def registration_sql(records: list[dict[str, object]], grid: RegularLatLonGrid) -> str:
    statements = ["BEGIN;"]
    for record in records:
        statements.append(
            "INSERT INTO workflow_runs (run_id, run_type, created_at) VALUES "
            f"('{record['run_id']}', 'analysis_cycle', CURRENT_TIMESTAMP) "
            "ON CONFLICT (run_id) DO NOTHING;"
        )
        statements.append(
            "INSERT INTO analysis_cycles ("
            "analysis_id, run_id, analysis_time, grid_id, config_version, status, "
            "degraded_reason, radar_count, valid_coverage_ratio, mean_quality_index, "
            "analysis_uri, created_at, updated_at) VALUES ("
            f"'{record['analysis_id']}', '{record['run_id']}', "
            f"'{record['analysis_time']}', '{grid.grid_id}', '{CONFIG_VERSION}', "
            f"'ANALYSIS_READY', NULL, 1, {record['valid_coverage_ratio']}, "
            f"{record['mean_quality_index']}, '{record['analysis_uri']}', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT (analysis_id) DO NOTHING;"
        )
    statements.extend(
        [
            "COMMIT;",
            "SELECT analysis_id, analysis_time, config_version, status, analysis_uri "
            "FROM analysis_cycles "
            f"WHERE config_version = '{CONFIG_VERSION}' ORDER BY analysis_time;",
        ]
    )
    return "\n".join(statements)


def _floating_field(
    name: str,
    *,
    shape: tuple[int, int],
    no_rain: np.ndarray,
    low_quality: np.ndarray,
    data_age_minutes: float,
) -> np.ndarray:
    value = {
        "DBZH_QC": 25.0,
        "REF_NOWCAST": 25.0,
        "QUALITY_INDEX": 0.82,
        "SOURCE_ELEVATION": 0.5,
        "BEAM_HEIGHT": 1200.0,
        "TERRAIN_HEIGHT": 80.0,
        "BLOCKAGE_RATE": 0.05,
        "DATA_AGE": data_age_minutes,
    }.get(name, 0.8)
    values = np.full(shape, value, dtype="float32")
    if name in {"DBZH_QC", "REF_NOWCAST"}:
        values[no_rain] = 5.0
    elif name == "QUALITY_INDEX":
        values[low_quality] = 0.35
    return values


def _mean_valid_quality(objects: Mapping[str, bytes]) -> float:
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    valid = root["VALID_MASK"][:] == 1
    return float(np.mean(root["QUALITY_INDEX"][:][valid]))


if __name__ == "__main__":
    main()
