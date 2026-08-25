from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from rainpulse_algo.radar.qc_worker import execute_basic_qc
from rainpulse_algo.radar.worker import execute_fmt_decode

from .domain_contracts import (
    AnalysisMosaicRequested,
    NowcastInputRequested,
    RadarDecodeRequested,
    RadarGridRequested,
    RadarQCRequested,
)
from .runtime import TaskHandler, WorkerResult


def _execute_radar_grid(request: RadarGridRequested) -> WorkerResult:
    # Raster I/O has native system-library dependencies that only the real
    # grid profile needs. Keep synthetic/decode/QC workers isolated from them.
    from rainpulse_algo.radar.grid_worker import execute_radar_grid

    return execute_radar_grid(request)


def _synthetic_executor(stage: str) -> Callable[[Any], WorkerResult]:
    def execute(request: Any) -> WorkerResult:
        payload = request.payload.model_dump(mode="json")
        data = json.dumps(
            {
                "schema_version": "1.0",
                "synthetic_contract_fixture": True,
                "stage": stage,
                "event_type": request.event_type,
                "run_id": str(request.run_id),
                "job_id": str(request.job_id),
                "input_metadata": payload,
                "notice": "No radar array or meteorological algorithm was executed.",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        input_count = len(payload.get("inputs", payload.get("input_uris", [1])))
        return WorkerResult(
            data=data,
            metrics={"simulation": 1.0, "input_count": float(input_count)},
        )

    return execute


HANDLERS = {
    "radar-decode-fmt": TaskHandler(
        profile="radar-decode-fmt",
        subject="rainpulse.jobs.requested.radar_decode",
        consumer="rainpulse-radar-decode-cma-rstm-2-0-0",
        request_model=RadarDecodeRequested,
        executor=execute_fmt_decode,
        asset_type="normalized_radar_volume",
        artifact_name="volume.zarr",
        ack_wait_seconds=300,
    ),
    "radar-decode-synthetic": TaskHandler(
        profile="radar-decode-synthetic",
        subject="rainpulse.jobs.requested.radar_decode_synthetic",
        consumer="rainpulse-radar-decode-synthetic",
        request_model=RadarDecodeRequested,
        executor=_synthetic_executor("radar_decode"),
        asset_type="normalized_radar_volume",
        artifact_name="volume.zarr",
    ),
    "radar-qc-synthetic": TaskHandler(
        profile="radar-qc-synthetic",
        subject="rainpulse.jobs.requested.radar_qc_synthetic",
        consumer="rainpulse-radar-qc-synthetic",
        request_model=RadarQCRequested,
        executor=_synthetic_executor("radar_qc"),
        asset_type="qc_radar_volume",
        artifact_name="volume.zarr",
    ),
    "radar-qc-basic": TaskHandler(
        profile="radar-qc-basic",
        subject="rainpulse.jobs.requested.radar_qc",
        consumer="rainpulse-radar-qc-rp008-basic-v1",
        request_model=RadarQCRequested,
        executor=execute_basic_qc,
        asset_type="qc_radar_volume",
        artifact_name="volume.zarr",
        ack_wait_seconds=300,
    ),
    "radar-grid-synthetic": TaskHandler(
        profile="radar-grid-synthetic",
        subject="rainpulse.jobs.requested.radar_grid_synthetic",
        consumer="rainpulse-radar-grid-synthetic",
        request_model=RadarGridRequested,
        executor=_synthetic_executor("radar_grid"),
        asset_type="radar_grid",
        artifact_name="grid.zarr",
    ),
    "radar-grid-hybrid": TaskHandler(
        profile="radar-grid-hybrid",
        subject="rainpulse.jobs.requested.radar_grid",
        consumer="rainpulse-radar-grid-hybrid-scan-1-0-0",
        request_model=RadarGridRequested,
        executor=_execute_radar_grid,
        asset_type="radar_grid",
        artifact_name="grid.zarr",
        ack_wait_seconds=900,
    ),
    "mosaic-qpe-synthetic": TaskHandler(
        profile="mosaic-qpe-synthetic",
        subject="rainpulse.jobs.requested.analysis_mosaic",
        consumer="rainpulse-mosaic-qpe-synthetic",
        request_model=AnalysisMosaicRequested,
        executor=_synthetic_executor("mosaic_qpe"),
        asset_type="radar_analysis",
        artifact_name="analysis.zarr",
    ),
    "nowcast-input-synthetic": TaskHandler(
        profile="nowcast-input-synthetic",
        subject="rainpulse.jobs.requested.nowcast_input",
        consumer="rainpulse-nowcast-input-synthetic",
        request_model=NowcastInputRequested,
        executor=_synthetic_executor("nowcast_input"),
        asset_type="nowcast_input",
        artifact_name="input.zarr",
    ),
}


def handler_for_profile(profile: str) -> TaskHandler:
    try:
        return HANDLERS[profile]
    except KeyError as error:
        supported = ", ".join(["simulation", *sorted(HANDLERS)])
        raise ValueError(
            f"unsupported worker profile {profile!r}; choose one of {supported}"
        ) from error
