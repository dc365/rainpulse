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


class VerificationMapError(ValueError):
    """Raised when a verification map profile or bundle is invalid."""


@dataclass(frozen=True)
class PaletteStop:
    minimum: float
    color: str


@dataclass(frozen=True)
class VerificationMapProfile:
    profile_version: str
    renderer_version: str
    bundle_contract_version: str
    projection: str
    palette_version: str
    valid_no_rain_color: str
    valid_no_rain_alpha: int
    rain_alpha: int
    rain_threshold_mm_h: float
    rain_rate_stops: tuple[PaletteStop, ...]
    maximum_motion_vectors: int
    sample_step_pixels: int
    motion_unit: str


def load_verification_map_profile(path: Path) -> VerificationMapProfile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        palette = raw["palette"]
        motion = raw["motion_vectors"]
        profile = VerificationMapProfile(
            profile_version=str(raw["profile_version"]),
            renderer_version=str(raw["renderer_version"]),
            bundle_contract_version=str(raw["bundle_contract_version"]),
            projection=str(raw["projection"]),
            palette_version=str(palette["version"]),
            valid_no_rain_color=str(palette["valid_no_rain_color"]),
            valid_no_rain_alpha=int(palette["valid_no_rain_alpha"]),
            rain_alpha=int(palette["rain_alpha"]),
            rain_threshold_mm_h=float(palette["rain_threshold_mm_h"]),
            rain_rate_stops=tuple(
                PaletteStop(float(item["minimum"]), str(item["color"]))
                for item in palette["rain_rate"]
            ),
            maximum_motion_vectors=int(motion["maximum_count"]),
            sample_step_pixels=int(motion["sample_step_pixels"]),
            motion_unit=str(motion["unit"]),
        )
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise VerificationMapError(f"invalid verification map profile {path}: {exc}") from exc

    if raw.get("schema_version") != "1.0" or profile.bundle_contract_version != "1.0":
        raise VerificationMapError("unsupported verification map profile or bundle contract")
    if profile.projection != "EPSG:4326":
        raise VerificationMapError("verification maps currently require EPSG:4326")
    if not (0 <= profile.valid_no_rain_alpha <= 255 and 1 <= profile.rain_alpha <= 255):
        raise VerificationMapError("verification map alpha is outside uint8 bounds")
    if profile.rain_threshold_mm_h < 0:
        raise VerificationMapError("verification rain threshold cannot be negative")
    if not 1 <= profile.maximum_motion_vectors <= 200 or profile.sample_step_pixels < 1:
        raise VerificationMapError("verification motion-vector limits are invalid")
    minima = tuple(stop.minimum for stop in profile.rain_rate_stops)
    if (
        not minima
        or minima[0] != profile.rain_threshold_mm_h
        or any(right <= left for left, right in zip(minima, minima[1:], strict=False))
    ):
        raise VerificationMapError("verification rain palette stops are invalid")
    for color in (profile.valid_no_rain_color, *(stop.color for stop in profile.rain_rate_stops)):
        _hex_rgb(color)
    return profile


def build_verification_map_bundle(
    *,
    profile: VerificationMapProfile,
    verification_profile_version: str,
    case_id: str,
    truth_kind: str,
    issue_time: datetime,
    lead_minutes: tuple[int, ...],
    grid: RegularLatLonGrid,
    truth_rate: np.ndarray,
    truth_valid: np.ndarray,
    forecasts: dict[str, tuple[np.ndarray, np.ndarray]],
    velocity_pixels_per_step: np.ndarray,
    motion_valid_mask: np.ndarray,
    motion_fallback_used: bool,
    motion_fallback_reason: str | None,
    motion_feature_count: int = 0,
    trackable_rain_pixel_count: int = 0,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build one issue's presentation-only map bundle without changing score arrays."""

    return _build_rate_verification_map_bundle(
        profile=profile,
        verification_profile_version=verification_profile_version,
        case_id=case_id,
        truth_kind=truth_kind,
        issue_time=issue_time,
        lead_minutes=lead_minutes,
        grid=grid,
        truth_rate=truth_rate,
        truth_valid=truth_valid,
        forecasts=forecasts,
        required_models=frozenset({"lk", "persistence", "translation"}),
        optional_models=frozenset({"phase_correlation"}),
        model_order=("lk", "persistence", "translation", "phase_correlation"),
        velocity_pixels_per_step=velocity_pixels_per_step,
        motion_valid_mask=motion_valid_mask,
        motion_fallback_used=motion_fallback_used,
        motion_fallback_reason=motion_fallback_reason,
        motion_feature_count=motion_feature_count,
        trackable_rain_pixel_count=trackable_rain_pixel_count,
    )


def build_probabilistic_verification_map_bundle(
    *,
    profile: VerificationMapProfile,
    verification_profile_version: str,
    case_id: str,
    truth_kind: str,
    issue_time: datetime,
    lead_minutes: tuple[int, ...],
    grid: RegularLatLonGrid,
    truth_rate: np.ndarray,
    truth_valid: np.ndarray,
    nowcastnet_members: np.ndarray,
    nowcastnet_member_valid: np.ndarray,
    steps_members: np.ndarray,
    steps_member_valid: np.ndarray,
    deterministic_forecasts: dict[str, tuple[np.ndarray, np.ndarray]],
    velocity_pixels_per_step: np.ndarray,
    motion_valid_mask: np.ndarray,
    motion_fallback_used: bool,
    motion_fallback_reason: str | None,
    motion_feature_count: int = 0,
    trackable_rain_pixel_count: int = 0,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Render ensemble-mean rate evidence without changing probability scores."""

    leads = tuple(int(value) for value in lead_minutes)
    member_shape = (len(leads), *grid.shape)

    def ensemble_mean(
        name: str,
        values: np.ndarray,
        valid: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        members = np.asarray(values, dtype="float32")
        support = np.asarray(valid) == 1
        if (
            members.ndim != 4
            or members.shape[0] < 1
            or members.shape[1:] != member_shape
            or support.shape != members.shape
        ):
            raise VerificationMapError(
                f"{name} ensemble dimensions differ from lead/grid dimensions"
            )
        common_support = np.all(support, axis=0)
        safe_values = np.where(support & np.isfinite(members), members, 0.0)
        mean = np.mean(safe_values, axis=0, dtype="float32")
        if np.any(~np.isfinite(members[support])) or np.any(members[support] < 0):
            raise VerificationMapError(f"{name} valid ensemble rates are invalid")
        return mean, common_support

    forecasts = {
        "nowcastnet": ensemble_mean(
            "NowcastNet", nowcastnet_members, nowcastnet_member_valid
        ),
        "steps": ensemble_mean("STEPS", steps_members, steps_member_valid),
        **deterministic_forecasts,
    }
    return _build_rate_verification_map_bundle(
        profile=profile,
        verification_profile_version=verification_profile_version,
        case_id=case_id,
        truth_kind=truth_kind,
        issue_time=issue_time,
        lead_minutes=leads,
        grid=grid,
        truth_rate=truth_rate,
        truth_valid=truth_valid,
        forecasts=forecasts,
        required_models=frozenset(
            {"nowcastnet", "steps", "lk", "persistence", "phase_correlation"}
        ),
        optional_models=frozenset(),
        model_order=("nowcastnet", "steps", "lk", "persistence", "phase_correlation"),
        velocity_pixels_per_step=velocity_pixels_per_step,
        motion_valid_mask=motion_valid_mask,
        motion_fallback_used=motion_fallback_used,
        motion_fallback_reason=motion_fallback_reason,
        motion_feature_count=motion_feature_count,
        trackable_rain_pixel_count=trackable_rain_pixel_count,
    )


def _build_rate_verification_map_bundle(
    *,
    profile: VerificationMapProfile,
    verification_profile_version: str,
    case_id: str,
    truth_kind: str,
    issue_time: datetime,
    lead_minutes: tuple[int, ...],
    grid: RegularLatLonGrid,
    truth_rate: np.ndarray,
    truth_valid: np.ndarray,
    forecasts: dict[str, tuple[np.ndarray, np.ndarray]],
    required_models: frozenset[str],
    optional_models: frozenset[str],
    model_order: tuple[str, ...],
    velocity_pixels_per_step: np.ndarray,
    motion_valid_mask: np.ndarray,
    motion_fallback_used: bool,
    motion_fallback_reason: str | None,
    motion_feature_count: int,
    trackable_rain_pixel_count: int,
) -> tuple[dict[str, Any], dict[str, bytes]]:

    issue = _utc(issue_time)
    leads = tuple(int(value) for value in lead_minutes)
    expected_shape = (len(leads), *grid.shape)
    truth_values = np.asarray(truth_rate, dtype="float32")
    truth_support = np.asarray(truth_valid) == 1
    if not leads or truth_values.shape != expected_shape or truth_support.shape != expected_shape:
        raise VerificationMapError("verification truth arrays differ from lead/grid dimensions")
    forecast_models = set(forecasts)
    if not required_models.issubset(forecast_models) or forecast_models - (
        required_models | optional_models
    ):
        raise VerificationMapError(
            "verification map forecast identities differ from the selected evidence contract"
        )

    normalized_forecasts: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for model, (values, valid) in forecasts.items():
        field = np.asarray(values, dtype="float32")
        support = np.asarray(valid) == 1
        if field.shape != expected_shape or support.shape != expected_shape:
            raise VerificationMapError(f"verification forecast shape is invalid for {model}")
        normalized_forecasts[model] = field, support

    velocity = np.asarray(velocity_pixels_per_step, dtype="float32")
    motion_valid = np.asarray(motion_valid_mask) == 1
    if velocity.shape != (2, *grid.shape) or motion_valid.shape != grid.shape:
        raise VerificationMapError("verification motion arrays differ from the grid")
    if np.any(~np.isfinite(velocity[motion_valid[np.newaxis, ...].repeat(2, axis=0)])):
        raise VerificationMapError("verification motion contains non-finite valid values")

    objects: dict[str, bytes] = {}
    layers: list[dict[str, Any]] = []
    sources: list[tuple[str, str, str | None, np.ndarray, np.ndarray]] = [
        ("truth", "truth", None, truth_values, truth_support),
    ]
    for model in model_order:
        if model not in normalized_forecasts:
            continue
        sources.append(
            (
                model.replace("_", "-"),
                "forecast",
                model,
                *normalized_forecasts[model],
            )
        )
    for lead_index, lead in enumerate(leads):
        valid_time = issue + timedelta(minutes=lead)
        for name, role, model, values, support in sources:
            asset_id = f"lead-{lead:03d}-{name}"
            object_path = f"layers/{asset_id}.png"
            png, counts = _render_rate_png(values[lead_index], support[lead_index], profile)
            objects[object_path] = png
            layers.append(
                {
                    "asset_id": asset_id,
                    "role": role,
                    "model": model,
                    "lead_minutes": lead,
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
        "operational_eligible": False,
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
            "rain_threshold_mm_h": profile.rain_threshold_mm_h,
            "valid_no_rain_color": profile.valid_no_rain_color,
            "stops": [
                {"minimum": stop.minimum, "color": stop.color} for stop in profile.rain_rate_stops
            ],
        },
        "motion": {
            "fallback_used": bool(motion_fallback_used),
            "fallback_reason": motion_fallback_reason,
            "feature_count": int(motion_feature_count),
            "trackable_rain_pixel_count": int(trackable_rain_pixel_count),
            "unit": profile.motion_unit,
            "vectors": _motion_vectors(velocity, motion_valid, grid, profile),
        },
        "lead_minutes": list(leads),
        "layers": layers,
    }
    _validate_bundle(manifest, objects)
    return manifest, objects


def write_verification_map_bundle(
    maps_root: Path,
    manifest: dict[str, Any],
    objects: dict[str, bytes],
) -> Path:
    """Atomically publish one validated issue bundle below the maps root."""

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
        raise VerificationMapError(f"verification map destination already exists: {destination}")

    temporary_root = maps_root / ".temporary"
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f"{case_id}-{issue_key}-", dir=temporary_root))
    try:
        for object_path, data in objects.items():
            target = temporary / _safe_object_path(object_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (temporary / "manifest.json").write_bytes(manifest_bytes)
        # mkdtemp uses 0700. The published bundle is consumed through a
        # read-only bind mount whose container UID may differ from the writer.
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


def write_verification_map_index(
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


def _render_rate_png(
    values: np.ndarray,
    valid: np.ndarray,
    profile: VerificationMapProfile,
) -> tuple[bytes, dict[str, int]]:
    field = np.asarray(values, dtype="float32")
    support = np.asarray(valid, dtype=bool)
    if field.ndim != 2 or field.shape != support.shape:
        raise VerificationMapError("verification map field and support must be matching 2-D arrays")
    if np.any(~np.isfinite(field[support])) or np.any(field[support] < 0):
        raise VerificationMapError("valid verification rates must be finite and non-negative")
    visible = support & np.isfinite(field)
    no_rain = visible & (field < profile.rain_threshold_mm_h)
    rain = visible & ~no_rain
    rgba = np.zeros((*field.shape, 4), dtype="uint8")
    rgba[no_rain, :3] = _hex_rgb(profile.valid_no_rain_color)
    rgba[no_rain, 3] = np.uint8(profile.valid_no_rain_alpha)
    if np.any(rain):
        minima = np.asarray([stop.minimum for stop in profile.rain_rate_stops], dtype="float32")
        colors = np.asarray(
            [_hex_rgb(stop.color) for stop in profile.rain_rate_stops], dtype="uint8"
        )
        indices = np.searchsorted(minima, field[rain], side="right") - 1
        rgba[rain, :3] = colors[np.clip(indices, 0, len(colors) - 1)]
        rgba[rain, 3] = np.uint8(profile.rain_alpha)
    png = encode_rgba_png(np.flipud(rgba))
    return png, {
        "valid_cell_count": int(np.count_nonzero(visible)),
        "no_rain_cell_count": int(np.count_nonzero(no_rain)),
        "rain_cell_count": int(np.count_nonzero(rain)),
        "missing_cell_count": int(field.size - np.count_nonzero(visible)),
    }


def _motion_vectors(
    velocity: np.ndarray,
    valid: np.ndarray,
    grid: RegularLatLonGrid,
    profile: VerificationMapProfile,
) -> list[dict[str, float]]:
    step = profile.sample_step_pixels
    row_start = min(step // 2, max(0, grid.latitude_count // 2))
    column_start = min(step // 2, max(0, grid.longitude_count // 2))
    vectors: list[dict[str, float]] = []
    for row in range(row_start, grid.latitude_count, step):
        for column in range(column_start, grid.longitude_count, step):
            if not valid[row, column]:
                continue
            u = float(velocity[0, row, column])
            v = float(velocity[1, row, column])
            longitude = float(grid.longitude[column])
            latitude = float(grid.latitude[row])
            vectors.append(
                {
                    "longitude": longitude,
                    "latitude": latitude,
                    "end_longitude": longitude + u * grid.longitude_interval_deg,
                    "end_latitude": latitude + v * grid.latitude_interval_deg,
                    "u_pixels_per_step": u,
                    "v_pixels_per_step": v,
                }
            )
            if len(vectors) >= profile.maximum_motion_vectors:
                return vectors
    return vectors


def _validate_bundle(manifest: dict[str, Any], objects: dict[str, bytes]) -> None:
    try:
        if manifest["contract_version"] != "1.0" or manifest["operational_eligible"] is not False:
            raise VerificationMapError("verification map identity is invalid")
        layers = manifest["layers"]
        if not isinstance(layers, list) or not layers:
            raise VerificationMapError("verification map bundle has no layers")
        expected_paths = {str(layer["object_path"]) for layer in layers}
        if expected_paths != set(objects):
            raise VerificationMapError("verification map object set differs from the manifest")
        for layer in layers:
            object_path = _safe_object_path(str(layer["object_path"]))
            data = objects[object_path.as_posix()]
            if layer["media_type"] != "image/png" or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise VerificationMapError("verification map asset is not a PNG")
            if len(data) != int(layer["size_bytes"]):
                raise VerificationMapError("verification map asset size differs from manifest")
            if hashlib.sha256(data).hexdigest() != layer["sha256"]:
                raise VerificationMapError("verification map asset SHA-256 differs from manifest")
            if png_dimensions(data) != (int(layer["width"]), int(layer["height"])):
                raise VerificationMapError("verification map asset dimensions differ from manifest")
        if len(manifest["motion"]["vectors"]) > 200:
            raise VerificationMapError("verification motion payload exceeds the contract limit")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, VerificationMapError):
            raise
        raise VerificationMapError(f"invalid verification map bundle: {exc}") from exc


def _safe_object_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise VerificationMapError("verification map object path is unsafe")
    return path


def _safe_segment(value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise VerificationMapError("verification map path segment is unsafe")


def _hex_rgb(value: str) -> np.ndarray:
    if len(value) != 7 or not value.startswith("#"):
        raise VerificationMapError(f"invalid verification map color {value}")
    try:
        return np.asarray([int(value[index : index + 2], 16) for index in (1, 3, 5)], dtype="uint8")
    except ValueError as exc:
        raise VerificationMapError(f"invalid verification map color {value}") from exc


def _rounded_bounds(values: tuple[float, float, float, float]) -> list[float]:
    return [round(float(value), 10) for value in values]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise VerificationMapError("verification map timestamps must be UTC")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
