from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import yaml

from rainpulse_algo.diagnostics.png import encode_rgba_png, png_dimensions
from rainpulse_algo.grid import RegularLatLonGrid

from .map_bundle import VerificationMapError


@dataclass(frozen=True)
class ProbabilityPaletteStop:
    minimum: float
    color: str


@dataclass(frozen=True)
class ProbabilityVerificationMapProfile:
    profile_version: str
    renderer_version: str
    bundle_contract_version: str
    projection: str
    palette_version: str
    valid_no_event_color: str
    valid_no_event_alpha: int
    event_alpha: int
    probability_stops: tuple[ProbabilityPaletteStop, ...]


def load_probability_verification_map_profile(
    path: Path,
) -> ProbabilityVerificationMapProfile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        palette = raw["palette"]
        profile = ProbabilityVerificationMapProfile(
            profile_version=str(raw["profile_version"]),
            renderer_version=str(raw["renderer_version"]),
            bundle_contract_version=str(raw["bundle_contract_version"]),
            projection=str(raw["projection"]),
            palette_version=str(palette["version"]),
            valid_no_event_color=str(palette["valid_no_event_color"]),
            valid_no_event_alpha=int(palette["valid_no_event_alpha"]),
            event_alpha=int(palette["event_alpha"]),
            probability_stops=tuple(
                ProbabilityPaletteStop(float(item["minimum"]), str(item["color"]))
                for item in palette["probability_percent"]
            ),
        )
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise VerificationMapError(f"invalid probability map profile {path}: {exc}") from exc

    if raw.get("schema_version") != "1.0" or profile.bundle_contract_version != "1.0":
        raise VerificationMapError("unsupported probability map profile or bundle contract")
    if profile.projection != "EPSG:4326":
        raise VerificationMapError("probability maps currently require EPSG:4326")
    if not (0 <= profile.valid_no_event_alpha <= 255 and 1 <= profile.event_alpha <= 255):
        raise VerificationMapError("probability map alpha is outside uint8 bounds")
    minima = tuple(stop.minimum for stop in profile.probability_stops)
    if (
        not minima
        or minima[0] <= 0
        or minima[-1] != 100
        or any(right <= left for left, right in zip(minima, minima[1:], strict=False))
    ):
        raise VerificationMapError("probability palette stops are invalid")
    for color in (
        profile.valid_no_event_color,
        *(stop.color for stop in profile.probability_stops),
    ):
        _hex_rgb(color)
    return profile


def build_probability_verification_map_bundle(
    *,
    profile: ProbabilityVerificationMapProfile,
    verification_profile_version: str,
    case_id: str,
    truth_kind: str,
    issue_time: datetime,
    lead_minutes: tuple[int, ...],
    thresholds_mm_h: tuple[float, ...],
    grid: RegularLatLonGrid,
    truth_rate: np.ndarray,
    truth_valid: np.ndarray,
    nowcastnet_members: np.ndarray,
    nowcastnet_member_valid: np.ndarray,
    steps_members: np.ndarray,
    steps_member_valid: np.ndarray,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build raw, uncalibrated threshold-exceedance probability evidence."""

    issue = _utc(issue_time)
    leads = tuple(int(value) for value in lead_minutes)
    thresholds = tuple(float(value) for value in thresholds_mm_h)
    expected = (len(leads), *grid.shape)
    truth = np.asarray(truth_rate, dtype="float32")
    truth_support = np.asarray(truth_valid) == 1
    if not leads or truth.shape != expected or truth_support.shape != expected:
        raise VerificationMapError("probability truth arrays differ from lead/grid dimensions")
    if (
        not thresholds
        or any(value <= 0 or not np.isfinite(value) for value in thresholds)
        or any(right <= left for left, right in zip(thresholds, thresholds[1:], strict=False))
    ):
        raise VerificationMapError("probability thresholds must be positive and increasing")
    if np.any(~np.isfinite(truth[truth_support])) or np.any(truth[truth_support] < 0):
        raise VerificationMapError("valid probability truth rates are invalid")

    ensembles: list[tuple[str, np.ndarray, np.ndarray]] = []
    for model, values, valid in (
        ("nowcastnet", nowcastnet_members, nowcastnet_member_valid),
        ("steps", steps_members, steps_member_valid),
    ):
        members = np.asarray(values, dtype="float32")
        member_support = np.asarray(valid) == 1
        if (
            members.ndim != 4
            or members.shape[0] < 1
            or members.shape[1:] != expected
            or member_support.shape != members.shape
        ):
            raise VerificationMapError(
                f"{model} probability members differ from lead/grid dimensions"
            )
        if np.any(~np.isfinite(members[member_support])) or np.any(members[member_support] < 0):
            raise VerificationMapError(f"valid {model} member rates are invalid")
        ensembles.append((model, members, np.all(member_support, axis=0)))

    objects: dict[str, bytes] = {}
    layers: list[dict[str, Any]] = []
    for lead_index, lead in enumerate(leads):
        valid_time = issue + timedelta(minutes=lead)
        for threshold in thresholds:
            threshold_id = _threshold_id(threshold)
            sources: list[tuple[str, str | None, np.ndarray, np.ndarray]] = [
                (
                    "truth",
                    None,
                    (truth[lead_index] >= threshold).astype("float32") * 100.0,
                    truth_support[lead_index],
                )
            ]
            for model, members, common_support in ensembles:
                probability = (
                    np.mean(
                        members[:, lead_index] >= threshold,
                        axis=0,
                        dtype="float32",
                    )
                    * 100.0
                )
                sources.append(("forecast", model, probability, common_support[lead_index]))
            for role, model, probability, support in sources:
                name = model or "truth"
                asset_id = f"lead-{lead:03d}-threshold-{threshold_id}-{name}"
                object_path = f"layers/{asset_id}.png"
                png, counts = _render_probability_png(probability, support, profile)
                objects[object_path] = png
                layers.append(
                    {
                        "asset_id": asset_id,
                        "role": role,
                        "model": model,
                        "lead_minutes": lead,
                        "threshold_mm_h": threshold,
                        "valid_time_utc": _format_utc(valid_time),
                        "object_path": object_path,
                        "media_type": "image/png",
                        "sha256": hashlib.sha256(png).hexdigest(),
                        "size_bytes": len(png),
                        "width": grid.longitude_count,
                        "height": grid.latitude_count,
                        **counts,
                    }
                )

    issue_key = issue.strftime("%Y%m%dT%H%M%SZ")
    manifest: dict[str, Any] = {
        "contract_version": profile.bundle_contract_version,
        "renderer_version": profile.renderer_version,
        "render_profile_version": profile.profile_version,
        "palette_version": profile.palette_version,
        "verification_profile_version": verification_profile_version,
        "case_id": case_id,
        "issue_key": issue_key,
        "issue_time_utc": _format_utc(issue),
        "truth_kind": truth_kind,
        "calibration_status": "raw_ensemble_relative_frequency_uncalibrated",
        "operational_eligible": False,
        "product_publication_enabled": False,
        "grid": {
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "projection": profile.projection,
            "fit_bounds": _rounded_bounds(grid.coordinate_centre_bounds),
            "pixel_edge_bounds": _rounded_bounds(grid.pixel_edge_bounds),
            "width": grid.longitude_count,
            "height": grid.latitude_count,
        },
        "palette": {
            "valid_no_event_color": profile.valid_no_event_color,
            "stops": [
                {"minimum": stop.minimum, "color": stop.color} for stop in profile.probability_stops
            ],
        },
        "lead_minutes": list(leads),
        "thresholds_mm_h": list(thresholds),
        "layers": layers,
    }
    _validate_bundle(manifest, objects)
    return manifest, objects


def write_probability_verification_map_bundle(
    maps_root: Path,
    manifest: dict[str, Any],
    objects: dict[str, bytes],
) -> Path:
    _validate_bundle(manifest, objects)
    case_id = str(manifest["case_id"])
    issue_key = str(manifest["issue_key"])
    _safe_segment(case_id)
    _safe_segment(issue_key)
    destination = maps_root / case_id / issue_key
    manifest_bytes = _json_bytes(manifest)
    if destination.is_dir():
        existing = destination / "manifest.json"
        if existing.is_file() and existing.read_bytes() == manifest_bytes:
            return destination
        raise VerificationMapError(f"probability map destination already exists: {destination}")
    temporary_root = maps_root / ".temporary"
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f"{case_id}-{issue_key}-", dir=temporary_root))
    try:
        for object_path, data in objects.items():
            target = temporary / _safe_object_path(object_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (temporary / "manifest.json").write_bytes(manifest_bytes)
        temporary.chmod(0o755)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        try:
            temporary_root.rmdir()
        except OSError:
            pass
    return destination


def write_probability_verification_map_index(
    maps_root: Path,
    *,
    verification_profile_version: str,
    renderer_version: str,
    issues: list[dict[str, Any]],
    layer_count: int,
) -> None:
    payload = {
        "contract_version": "1.0",
        "verification_profile_version": verification_profile_version,
        "renderer_version": renderer_version,
        "bundle_count": len(issues),
        "layer_count": layer_count,
        "issues": issues,
    }
    maps_root.mkdir(parents=True, exist_ok=True)
    temporary = maps_root / ".index.json.tmp"
    temporary.write_bytes(_json_bytes(payload))
    os.replace(temporary, maps_root / "index.json")


def _render_probability_png(
    values: np.ndarray,
    valid: np.ndarray,
    profile: ProbabilityVerificationMapProfile,
) -> tuple[bytes, dict[str, int]]:
    field = np.asarray(values, dtype="float32")
    support = np.asarray(valid, dtype=bool)
    if field.ndim != 2 or field.shape != support.shape:
        raise VerificationMapError("probability map field and support must be matching 2-D arrays")
    if np.any(~np.isfinite(field[support])) or np.any(
        (field[support] < 0) | (field[support] > 100)
    ):
        raise VerificationMapError("valid probabilities must be finite percentages from 0 to 100")
    visible = support & np.isfinite(field)
    no_event = visible & (field == 0)
    event = visible & ~no_event
    rgba = np.zeros((*field.shape, 4), dtype="uint8")
    rgba[no_event, :3] = _hex_rgb(profile.valid_no_event_color)
    rgba[no_event, 3] = np.uint8(profile.valid_no_event_alpha)
    if np.any(event):
        minima = np.asarray([stop.minimum for stop in profile.probability_stops], dtype="float32")
        colors = np.asarray(
            [_hex_rgb(stop.color) for stop in profile.probability_stops], dtype="uint8"
        )
        indices = np.searchsorted(minima, field[event], side="right") - 1
        rgba[event, :3] = colors[np.clip(indices, 0, len(colors) - 1)]
        rgba[event, 3] = np.uint8(profile.event_alpha)
    png = encode_rgba_png(np.flipud(rgba))
    return png, {
        "valid_cell_count": int(np.count_nonzero(visible)),
        "no_event_cell_count": int(np.count_nonzero(no_event)),
        "event_cell_count": int(np.count_nonzero(event)),
        "missing_cell_count": int(field.size - np.count_nonzero(visible)),
    }


def _validate_bundle(manifest: dict[str, Any], objects: dict[str, bytes]) -> None:
    try:
        if (
            manifest["contract_version"] != "1.0"
            or manifest["operational_eligible"] is not False
            or manifest["product_publication_enabled"] is not False
            or manifest["calibration_status"] != "raw_ensemble_relative_frequency_uncalibrated"
        ):
            raise VerificationMapError("probability map boundary is invalid")
        layers = manifest["layers"]
        if not isinstance(layers, list) or not layers:
            raise VerificationMapError("probability map bundle has no layers")
        expected_paths = {str(layer["object_path"]) for layer in layers}
        if expected_paths != set(objects):
            raise VerificationMapError("probability map objects differ from the manifest")
        for layer in layers:
            object_path = _safe_object_path(str(layer["object_path"]))
            data = objects[object_path.as_posix()]
            if layer["media_type"] != "image/png" or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise VerificationMapError("probability map asset is not a PNG")
            if len(data) != int(layer["size_bytes"]):
                raise VerificationMapError("probability map asset size differs from manifest")
            if hashlib.sha256(data).hexdigest() != layer["sha256"]:
                raise VerificationMapError("probability map asset digest differs from manifest")
            if png_dimensions(data) != (int(layer["width"]), int(layer["height"])):
                raise VerificationMapError("probability map asset dimensions differ from manifest")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, VerificationMapError):
            raise
        raise VerificationMapError(f"invalid probability map bundle: {exc}") from exc


def _threshold_id(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".").replace(".", "p")
    return text.zfill(3)


def _safe_object_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise VerificationMapError("probability map object path is unsafe")
    return path


def _safe_segment(value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise VerificationMapError("probability map path segment is unsafe")


def _hex_rgb(value: str) -> np.ndarray:
    if len(value) != 7 or not value.startswith("#"):
        raise VerificationMapError(f"invalid probability map color {value}")
    try:
        return np.asarray([int(value[index : index + 2], 16) for index in (1, 3, 5)], dtype="uint8")
    except ValueError as exc:
        raise VerificationMapError(f"invalid probability map color {value}") from exc


def _rounded_bounds(values: tuple[float, float, float, float]) -> list[float]:
    return [round(float(value), 10) for value in values]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise VerificationMapError("probability map timestamps must be UTC")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
