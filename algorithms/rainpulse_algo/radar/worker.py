from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from rainpulse_algo.worker.domain_contracts import RadarDecodeRequested
from rainpulse_algo.worker.runtime import WorkerResult

from .config import RadarConfigError, load_radar_config
from .fmt import DECODER_VERSION, decode_fmt_volume
from .health import assess_volume_health, load_radar_health_config
from .zarr_volume import build_zarr_store


def execute_fmt_decode(request: RadarDecodeRequested) -> WorkerResult:
    if request.payload.decoder_version != DECODER_VERSION:
        raise RadarConfigError(
            f"requested decoder {request.payload.decoder_version!r} is not {DECODER_VERSION!r}"
        )
    input_path = _allowed_input_path(request.payload.input_uri)
    config_dir = _required_directory("RAINPULSE_RADAR_CONFIG_DIR")
    config = load_radar_config(config_dir / f"{request.payload.radar_id}.yaml")
    health_config = load_radar_health_config(
        _required_file("RAINPULSE_RADAR_HEALTH_CONFIG")
    )
    if config.radar_id != request.payload.radar_id:
        raise RadarConfigError("request radar_id differs from decoder configuration")
    if config.config_version != request.payload.radar_config_version:
        raise RadarConfigError("request radar_config_version differs from decoder configuration")
    if config.source["format"] != request.payload.source_format:
        raise RadarConfigError("request source_format differs from decoder configuration")

    volume = decode_fmt_volume(input_path, config)
    health = assess_volume_health(volume, config, health_config)
    objects = build_zarr_store(
        volume,
        config,
        asset_id=request.payload.asset_id,
        source_uri=request.payload.input_uri,
        health=health,
        provenance={
            "scan_id": str(request.payload.scan_id),
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
        },
    )
    return WorkerResult(
        objects=objects,
        diagnostics={"radar_health": health.value},
        metrics=health.metrics
        | {
            "input_size_bytes": float(volume.input_size_bytes),
            "output_size_bytes": float(sum(len(value) for value in objects.values())),
            "zarr_object_count": float(len(objects)),
            "sweep_count": float(len(volume.sweeps)),
            "ray_count": float(volume.ray_count),
            "field_count": float(len(volume.canonical_fields)),
            "decode_warning_count": float(len(volume.warnings)),
        },
    )


def _allowed_input_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("RP-006 decoder accepts only local file:// input URIs")
    path = Path(unquote(parsed.path)).resolve(strict=True)
    roots_value = os.getenv("RAINPULSE_RADAR_INPUT_ROOTS")
    if not roots_value:
        raise ValueError("RAINPULSE_RADAR_INPUT_ROOTS is required")
    roots = [Path(item).resolve(strict=True) for item in roots_value.split(os.pathsep) if item]
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("radar input path is outside the configured read-only roots")
    if not path.is_file():
        raise ValueError("radar input path is not a file")
    return path


def _required_directory(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise ValueError(f"{name} must identify a directory")
    return path


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{name} must identify a file")
    return path
