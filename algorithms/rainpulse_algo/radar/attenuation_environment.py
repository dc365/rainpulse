"""Explicit, integrity-checked inputs for offline attenuation applicability."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .attenuation import AttenuationInputError


@dataclass(frozen=True)
class AttenuationEnvironment:
    temperature_c: float | None
    blockage_by_sweep: dict[str, np.ndarray]
    provenance: dict[str, Any]


def load_environment_manifest(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0" or not isinstance(raw.get("scans"), list):
        raise AttenuationInputError("invalid attenuation environment manifest")
    result = {}
    for entry in raw["scans"]:
        required = (
            "scan_id",
            "radar_id",
            "volume_end_time_utc",
            "source_uri",
            "npz_path",
            "sha256",
        )
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(k), str) or not entry[k] for k in required
        ):
            raise AttenuationInputError("environment record missing required provenance")
        if entry["scan_id"] in result:
            raise AttenuationInputError("duplicate environment scan_id")
        result[entry["scan_id"]] = {
            **entry,
            "npz_path": str((path.parent / entry["npz_path"]).resolve()),
        }
    return result


def read_environment(
    entry: dict[str, Any], *, radar_id: str, scan_id: str, volume_end_time_utc: str
) -> AttenuationEnvironment:
    if entry["scan_id"] != scan_id or entry["radar_id"].lower() != radar_id.lower():
        raise AttenuationInputError("environment scan/radar identity differs")
    if _utc(entry["volume_end_time_utc"]) != _utc(volume_end_time_utc):
        raise AttenuationInputError("environment volume_end_time_utc differs")
    data = Path(entry["npz_path"]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        raise AttenuationInputError("environment sha256 differs")
    with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
        temperature = None
        if "temperature_c" in arrays:
            values = np.asarray(arrays["temperature_c"], dtype="float64")
            if values.size != 1 or not np.isfinite(values).all():
                raise AttenuationInputError("environment temperature_c must be a finite scalar")
            temperature = float(values.item())
        blockage = {
            key.removesuffix("__blockage_fraction"): np.asarray(arrays[key], dtype="float32").copy()
            for key in arrays.files
            if key.endswith("__blockage_fraction")
        }
    return AttenuationEnvironment(
        temperature,
        blockage,
        {
            "source_uri": entry["source_uri"],
            "sha256": digest,
            "scan_id": scan_id,
            "radar_id": radar_id,
            "volume_end_time_utc": volume_end_time_utc,
            "temperature_c": temperature,
        },
    )


def _utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise AttenuationInputError("environment timestamps must include timezone")
    return stamp.astimezone(UTC)
