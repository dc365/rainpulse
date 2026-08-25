from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from minio import Minio

from rainpulse_algo.worker.domain_contracts import AnalysisDiagnosticsRequestedV1
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .profile import DiagnosticConfigError, DiagnosticProfile, load_diagnostic_profile
from .renderer import build_diagnostic_bundle, validate_diagnostic_bundle


def execute_analysis_diagnostics(request: AnalysisDiagnosticsRequestedV1) -> WorkerResult:
    return _execute_analysis_diagnostics(request, minio_client_from_environment())


def _execute_analysis_diagnostics(
    request: AnalysisDiagnosticsRequestedV1,
    client: Minio,
) -> WorkerResult:
    profile = load_diagnostic_profile(_required_file("RAINPULSE_DIAGNOSTIC_CONFIG"))
    flag_version, flag_definitions = _load_flag_definitions(
        _required_file("RAINPULSE_QC_FLAG_DEFINITIONS")
    )
    _validate_request(request, profile, flag_version)
    reader = ArtifactObjectReader(client)
    analysis_objects = reader.load(request.payload.input_uri)
    radar_inputs = [
        (item.radar_id, item.scan_id, reader.load(item.qc_uri))
        for item in request.payload.radar_inputs
    ]
    objects = build_diagnostic_bundle(
        analysis_objects,
        radar_inputs,
        analysis_uri=request.payload.input_uri,
        analysis_id=request.payload.analysis_id,
        job_id=request.job_id,
        profile=profile,
        flag_definitions=flag_definitions,
    )
    validation = validate_diagnostic_bundle(objects)
    manifest = validation["manifest"]
    return WorkerResult(
        objects=objects,
        diagnostics={"analysis_diagnostics": manifest},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "object_count": float(validation["object_count"]),
            "layer_count": float(validation["layer_count"]),
            "grid_layer_count": float(validation["grid_layer_count"]),
            "radar_count": float(validation["radar_count"]),
        },
    )


def _validate_request(
    request: AnalysisDiagnosticsRequestedV1,
    profile: DiagnosticProfile,
    flag_version: str,
) -> None:
    expected = (
        (
            "diagnostic_config_version",
            request.payload.diagnostic_config_version,
            profile.profile_version,
        ),
        ("renderer_version", request.payload.renderer_version, profile.renderer_version),
        (
            "flag_definition_version",
            request.payload.flag_definition_version,
            profile.flag_definition_version,
        ),
        ("mounted_flag_definition_version", flag_version, profile.flag_definition_version),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise DiagnosticConfigError(f"requested {name} differs from mounted configuration")


def _load_flag_definitions(path: Path) -> tuple[str, dict[str, int]]:
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        version = str(raw["definition_version"])
        definitions = {str(item["name"]): int(item["mask"]) for item in raw["flags"]}
    except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as error:
        raise DiagnosticConfigError(f"invalid QC flag definitions: {error}") from error
    if len(definitions) != len(raw["flags"]):
        raise DiagnosticConfigError("QC flag names must be unique")
    return version, definitions


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise DiagnosticConfigError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise DiagnosticConfigError(f"{name} must identify a file")
    return path
