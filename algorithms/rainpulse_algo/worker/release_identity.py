"""Small, non-secret startup identities exposed through the existing healthz."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def file_hash(path: str) -> str:
    with open(path, "rb") as stream:
        if os.fstat(stream.fileno()).st_size > 4 * 1024 * 1024:
            raise ValueError("release configuration exceeds 4 MiB")
        return hashlib.file_digest(stream, "sha256").hexdigest()


def capture_release_identity(profile: str) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": 1, "profile": profile, "pid": os.getpid()}
    if profile != "radar-qc-basic":
        return result
    fields = {
        "qc_config_sha256": "RAINPULSE_RADAR_QC_CONFIG",
        "qc_flags_sha256": "RAINPULSE_QC_FLAG_DEFINITIONS",
    }
    for key, variable in fields.items():
        path = os.getenv(variable, "")
        try:
            result[key] = file_hash(path)
        except (OSError, ValueError):
            # Health remains backwards compatible; release verification refuses
            # this identity until real config files and the QC profile exist.
            result[key] = None
    runtime = Path(__file__).with_name("runtime.py")
    try:
        result["runtime_sha256"] = file_hash(str(runtime))
    except (OSError, ValueError):
        result["runtime_sha256"] = None
    return result


def report_release_identity(startup: dict[str, Any]) -> dict[str, Any]:
    current = capture_release_identity(str(startup.get("profile", "")))
    result = dict(startup)
    result["config_unchanged"] = all(
        startup.get(key) is not None and startup.get(key) == current.get(key)
        for key in ("qc_config_sha256", "qc_flags_sha256", "runtime_sha256")
    ) if startup.get("profile") == "radar-qc-basic" else True
    return result
