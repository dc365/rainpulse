from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from rainpulse_algo.radar.qc_worker import execute_basic_qc
from rainpulse_algo.radar.worker import execute_fmt_decode

from .domain_contracts import (
    AnalysisDiagnosticsRequestedV1,
    AnalysisMosaicRequestedV1,
    AnalysisMosaicRequestedV2,
    AnalysisQPERequestedV1,
    ForecastVerificationRequested,
    NowcastInputRequested,
    NowcastNetOfflineRequested,
    ProductBuildRequested,
    PystepsLKRequested,
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


def _execute_radar_mosaic(request: AnalysisMosaicRequestedV2) -> WorkerResult:
    from rainpulse_algo.radar.mosaic_worker import execute_radar_mosaic

    return execute_radar_mosaic(request)


def _execute_analysis_qpe(request: AnalysisQPERequestedV1) -> WorkerResult:
    from rainpulse_algo.radar.qpe_worker import execute_analysis_qpe

    return execute_analysis_qpe(request)


def _execute_analysis_diagnostics(
    request: AnalysisDiagnosticsRequestedV1,
) -> WorkerResult:
    from rainpulse_algo.diagnostics.worker import execute_analysis_diagnostics

    return execute_analysis_diagnostics(request)


def _execute_nowcast_input(request: NowcastInputRequested) -> WorkerResult:
    from rainpulse_algo.nowcast.input_worker import execute_nowcast_input

    return execute_nowcast_input(request)


def _execute_pysteps_lk(request: PystepsLKRequested) -> WorkerResult:
    from rainpulse_algo.nowcast.pysteps_worker import execute_pysteps_lk

    return execute_pysteps_lk(request)


def _execute_nowcastnet_offline(request: NowcastNetOfflineRequested) -> WorkerResult:
    from rainpulse_algo.nowcast.nowcastnet_worker import execute_nowcastnet_offline

    return execute_nowcastnet_offline(request)


def _execute_product_build(request: ProductBuildRequested) -> WorkerResult:
    from rainpulse_algo.products.worker import execute_product_build

    return execute_product_build(request)


def _execute_forecast_verification(
    request: ForecastVerificationRequested,
) -> WorkerResult:
    from rainpulse_algo.verification.worker import execute_forecast_verification

    return execute_forecast_verification(request)


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
        subject="rainpulse.jobs.requested.analysis_mosaic_synthetic",
        consumer="rainpulse-mosaic-qpe-synthetic",
        request_model=AnalysisMosaicRequestedV1,
        executor=_synthetic_executor("mosaic_qpe"),
        asset_type="radar_analysis",
        artifact_name="analysis.zarr",
    ),
    "analysis-mosaic-qi": TaskHandler(
        profile="analysis-mosaic-qi",
        subject="rainpulse.jobs.requested.analysis_mosaic",
        consumer="rainpulse-analysis-mosaic-qi-1-0-0",
        request_model=AnalysisMosaicRequestedV2,
        executor=_execute_radar_mosaic,
        asset_type="radar_mosaic",
        artifact_name="mosaic.zarr",
        ack_wait_seconds=300,
    ),
    "analysis-qpe-basic": TaskHandler(
        profile="analysis-qpe-basic",
        subject="rainpulse.jobs.requested.analysis_qpe",
        consumer="rainpulse-analysis-qpe-basic-zr-1-0-0",
        request_model=AnalysisQPERequestedV1,
        executor=_execute_analysis_qpe,
        asset_type="radar_analysis",
        artifact_name="analysis.zarr",
        ack_wait_seconds=300,
    ),
    "analysis-diagnostics": TaskHandler(
        profile="analysis-diagnostics",
        subject="rainpulse.jobs.requested.analysis_diagnostics",
        consumer="rainpulse-analysis-diagnostics-renderer-1-0-0",
        request_model=AnalysisDiagnosticsRequestedV1,
        executor=_execute_analysis_diagnostics,
        asset_type="analysis_diagnostic_bundle",
        artifact_name="diagnostics",
        media_type="application/vnd.rainpulse.diagnostic-bundle+json",
        ack_wait_seconds=300,
    ),
    "nowcast-input-synthetic": TaskHandler(
        profile="nowcast-input-synthetic",
        subject="rainpulse.jobs.requested.nowcast_input_synthetic",
        consumer="rainpulse-nowcast-input-synthetic",
        request_model=NowcastInputRequested,
        executor=_synthetic_executor("nowcast_input"),
        asset_type="nowcast_input",
        artifact_name="input.zarr",
    ),
    "nowcast-input": TaskHandler(
        profile="nowcast-input",
        subject="rainpulse.jobs.requested.nowcast_input",
        consumer="rainpulse-nowcast-input-builder-1-0-0",
        request_model=NowcastInputRequested,
        executor=_execute_nowcast_input,
        asset_type="nowcast_input",
        artifact_name="input.zarr",
        ack_wait_seconds=300,
    ),
    "pysteps-lk": TaskHandler(
        profile="pysteps-lk",
        subject="rainpulse.jobs.requested.pysteps_lk",
        consumer="rainpulse-pysteps-lk-1-0-0",
        request_model=PystepsLKRequested,
        executor=_execute_pysteps_lk,
        asset_type="forecast_output",
        artifact_name="forecast.zarr",
        ack_wait_seconds=900,
    ),
    "nowcastnet-offline": TaskHandler(
        profile="nowcastnet-offline",
        subject="rainpulse.jobs.requested.nowcastnet_offline",
        consumer="rainpulse-nowcastnet-offline-rp026-v1",
        request_model=NowcastNetOfflineRequested,
        executor=_execute_nowcastnet_offline,
        asset_type="nowcastnet_offline_output",
        artifact_name="nowcastnet-output.zarr",
        ack_wait_seconds=1800,
        max_deliveries=1,
    ),
    "product-builder": TaskHandler(
        profile="product-builder",
        subject="rainpulse.jobs.requested.product_build",
        consumer="rainpulse-product-builder-1-0-0",
        request_model=ProductBuildRequested,
        executor=_execute_product_build,
        asset_type="application_product_bundle",
        artifact_name="application-products",
        media_type="application/vnd.rainpulse.application-product-bundle+json",
        ack_wait_seconds=900,
    ),
    "forecast-verification": TaskHandler(
        profile="forecast-verification",
        subject="rainpulse.jobs.requested.forecast_verification",
        consumer="rainpulse-forecast-verification-rp031-v1",
        request_model=ForecastVerificationRequested,
        executor=_execute_forecast_verification,
        asset_type="forecast_verification_result",
        artifact_name="verification-result",
        media_type="application/vnd.rainpulse.forecast-verification-result+json",
        ack_wait_seconds=900,
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
