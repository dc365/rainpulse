from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.analysis_zarr import validate_radar_analysis_zarr_store
from rainpulse_algo.radar.qc_zarr import validate_qc_zarr_store

from .png import encode_rgba_png, png_dimensions
from .profile import DiagnosticProfile


class DiagnosticInputError(ValueError):
    pass


REFLECTIVITY_STOPS = (
    (-10.0, "#9dd9ff"),
    (0.0, "#4ba3f2"),
    (10.0, "#1d73d0"),
    (20.0, "#3ca85b"),
    (30.0, "#9acb3c"),
    (40.0, "#efd23a"),
    (50.0, "#ee8a2d"),
    (60.0, "#cf453b"),
    (70.0, "#862f82"),
)
RATE_STOPS = (
    (0.0, "#dce9ee"),
    (0.1, "#9dd9ff"),
    (1.0, "#4ba3f2"),
    (2.5, "#2a79c7"),
    (5.0, "#3ca85b"),
    (10.0, "#9acb3c"),
    (25.0, "#efd23a"),
    (50.0, "#ee8a2d"),
    (100.0, "#cf453b"),
    (200.0, "#862f82"),
)
QUALITY_STOPS = (
    (0.0, "#9d4f32"),
    (0.25, "#ca7d3d"),
    (0.5, "#c8b56a"),
    (0.75, "#4e9b88"),
    (0.9, "#126f63"),
)
BEAM_STOPS = (
    (0.0, "#e1e7e3"),
    (500.0, "#b8d6cd"),
    (1000.0, "#72aa9c"),
    (2000.0, "#d1b56d"),
    (3000.0, "#c47f46"),
    (5000.0, "#984b36"),
)
SOURCE_COLORS = ("#15786b", "#2f72a8", "#a56e2d", "#8a4f83", "#667650")
FLAG_COLORS = (
    "#7d8582",
    "#b66b35",
    "#a14d41",
    "#83518d",
    "#4d78a4",
    "#1b7569",
)


def build_diagnostic_bundle(
    analysis_objects: Mapping[str, bytes],
    radar_inputs: Sequence[tuple[str, UUID, Mapping[str, bytes]]],
    *,
    analysis_uri: str,
    analysis_id: UUID,
    job_id: UUID,
    profile: DiagnosticProfile,
    flag_definitions: Mapping[str, int],
) -> dict[str, bytes]:
    validate_radar_analysis_zarr_store(analysis_objects)
    analysis = _open_group(analysis_objects)
    if analysis.attrs.get("analysis_id") != str(analysis_id):
        raise DiagnosticInputError("RadarAnalysis identity differs from diagnostic request")
    if analysis.attrs.get("contract_version") != profile.radar_analysis_contract_version:
        raise DiagnosticInputError("RadarAnalysis contract differs from diagnostic profile")
    if analysis.attrs.get("flag_definition_version") != profile.flag_definition_version:
        raise DiagnosticInputError("RadarAnalysis flag definition differs from profile")

    objects: dict[str, bytes] = {}
    layers: list[dict[str, Any]] = []
    grid_scale = profile.grid_render.pixel_scale
    valid = analysis["VALID_MASK"][:] == 1
    low = analysis["LOW_QUALITY_MASK"][:] == 1
    bounds = list(analysis.attrs["pixel_edge_bounds"])
    grid_specs = (
        ("grid-dbzh-qc", "质控反射率", "DBZH_QC", "scalar", "dBZ", REFLECTIVITY_STOPS),
        ("grid-rate-qpe", "瞬时雨强", "RATE_QPE", "scalar", "mm/h", RATE_STOPS),
        ("grid-quality-index", "综合质量指数", "QUALITY_INDEX", "scalar", "1", QUALITY_STOPS),
        ("grid-beam-height", "波束高度", "BEAM_HEIGHT", "scalar", "m", BEAM_STOPS),
    )
    for layer_id, title, field, rendering, unit, stops in grid_specs:
        rgba = _scalar_rgba(analysis[field][:], valid, stops)
        rgba = _north_up_scaled(rgba, grid_scale)
        layers.append(
            _store_layer(
                objects,
                layer_id=layer_id,
                title=title,
                scope="grid",
                field=field,
                rendering=rendering,
                unit=unit,
                rgba=rgba,
                palette_version=profile.palette_version,
                legend=_numeric_legend(stops, unit),
                bounds=bounds,
            )
        )

    source_codes = {
        str(name): int(code) for name, code in analysis.attrs.get("radar_source_codes", {}).items()
    }
    source_values = analysis["SOURCE_RADAR"][:]
    source_rgba = np.zeros((*source_values.shape, 4), dtype=np.uint8)
    source_legend: list[dict[str, Any]] = []
    for index, (radar_id, code) in enumerate(sorted(source_codes.items())):
        color = SOURCE_COLORS[index % len(SOURCE_COLORS)]
        source_rgba[source_values == code] = _hex_rgba(color)
        source_legend.append({"label": radar_id.upper(), "color": color, "code": code})
    blended_code = int(analysis.attrs.get("blended_source_code", 65535))
    source_rgba[source_values == blended_code] = _hex_rgba("#8a4f83")
    source_legend.append({"label": "多雷达融合", "color": "#8a4f83", "code": blended_code})
    source_rgba[~valid, 3] = 0
    layers.append(
        _store_layer(
            objects,
            layer_id="grid-source-radar",
            title="来源雷达",
            scope="grid",
            field="SOURCE_RADAR",
            rendering="categorical",
            unit=None,
            rgba=_north_up_scaled(source_rgba, grid_scale),
            palette_version=profile.palette_version,
            legend=source_legend,
            bounds=bounds,
        )
    )
    layers.append(
        _store_layer(
            objects,
            layer_id="grid-qc-flags",
            title="质控标志",
            scope="grid",
            field="QC_FLAGS",
            rendering="flags",
            unit=None,
            rgba=_north_up_scaled(
                _flag_rgba(analysis["QC_FLAGS"][:], valid, flag_definitions), grid_scale
            ),
            palette_version=profile.palette_version,
            legend=_flag_legend(flag_definitions),
            bounds=bounds,
        )
    )
    state_rgba = np.zeros((*valid.shape, 4), dtype=np.uint8)
    state_rgba[valid & ~low] = _hex_rgba("#15786b", 190)
    state_rgba[low] = _hex_rgba("#d18338", 220)
    layers.append(
        _store_layer(
            objects,
            layer_id="grid-state-mask",
            title="有效与低质量状态",
            scope="grid",
            field="VALID_MASK+LOW_QUALITY_MASK",
            rendering="state",
            unit=None,
            rgba=_north_up_scaled(state_rgba, grid_scale),
            palette_version=profile.palette_version,
            legend=[
                {"label": "有效", "color": "#15786b", "code": 1},
                {"label": "低质量", "color": "#d18338", "code": 2},
            ],
            bounds=bounds,
        )
    )

    seen_radars: set[str] = set()
    for radar_id, scan_id, qc_objects in radar_inputs:
        normalized_id = _slug(radar_id)
        if normalized_id in seen_radars:
            raise DiagnosticInputError("diagnostic radar IDs must be unique")
        seen_radars.add(normalized_id)
        validate_qc_zarr_store(qc_objects)
        qc = _open_group(qc_objects)
        if qc.attrs.get("scan_id") != str(scan_id) or qc.attrs.get("radar_id") != radar_id:
            raise DiagnosticInputError("QCRadarVolume identity differs from diagnostic request")
        if qc.attrs.get("contract_version") != profile.qc_radar_volume_contract_version:
            raise DiagnosticInputError("QCRadarVolume contract differs from diagnostic profile")
        group, sweep_number = _lowest_dbzh_sweep(qc)
        maximum_range_km = float(np.max(group["range"][:]) / 1000.0)
        elevation_deg = float(np.nanmedian(group["elevation"][:]))
        polar_specs = (
            ("dbzh-raw", "原始反射率", "DBZH_RAW", "scalar", "dBZ", REFLECTIVITY_STOPS),
            ("dbzh-qc", "质控后反射率", "DBZH_QC", "scalar", "dBZ", REFLECTIVITY_STOPS),
            (
                "quality-index",
                "极坐标质量指数",
                "QUALITY_INDEX",
                "scalar",
                "1",
                QUALITY_STOPS,
            ),
        )
        for suffix, title, field, rendering, unit, stops in polar_specs:
            field_valid = np.isfinite(group[field][:])
            rgba = _scalar_rgba(group[field][:], field_valid, stops)
            projected = _polar_to_ppi(
                rgba,
                group["azimuth"][:],
                group["range"][:],
                profile.polar_render.image_size,
            )
            layers.append(
                _store_layer(
                    objects,
                    layer_id=f"radar-{normalized_id}-{suffix}",
                    title=f"{radar_id.upper()} · {title}",
                    scope="polar",
                    field=field,
                    rendering=rendering,
                    unit=unit,
                    rgba=projected,
                    palette_version=profile.palette_version,
                    legend=_numeric_legend(stops, unit),
                    radar_id=radar_id,
                    scan_id=str(scan_id),
                    sweep_number=sweep_number,
                    elevation_deg=elevation_deg,
                    maximum_range_km=maximum_range_km,
                )
            )
        polar_valid = group["VALID_MASK"][:] == 1
        layers.append(
            _store_layer(
                objects,
                layer_id=f"radar-{normalized_id}-qc-flags",
                title=f"{radar_id.upper()} · 质控标志",
                scope="polar",
                field="QC_FLAGS",
                rendering="flags",
                unit=None,
                rgba=_polar_to_ppi(
                    _flag_rgba(group["QC_FLAGS"][:], polar_valid, flag_definitions),
                    group["azimuth"][:],
                    group["range"][:],
                    profile.polar_render.image_size,
                ),
                palette_version=profile.palette_version,
                legend=_flag_legend(flag_definitions),
                radar_id=radar_id,
                scan_id=str(scan_id),
                sweep_number=sweep_number,
                elevation_deg=elevation_deg,
                maximum_range_km=maximum_range_km,
            )
        )

    created_at = datetime.now(UTC).isoformat()
    manifest = {
        "contract_version": profile.bundle_contract_version,
        "job_id": str(job_id),
        "analysis_id": str(analysis_id),
        "analysis_time": str(analysis.attrs["analysis_time"]),
        "analysis_uri": analysis_uri,
        "grid_id": str(analysis.attrs["grid_id"]),
        "diagnostic_config_version": profile.profile_version,
        "renderer_version": profile.renderer_version,
        "palette_version": profile.palette_version,
        "flag_definition_version": profile.flag_definition_version,
        "operational_eligible": bool(analysis.attrs["operational_eligible"]),
        "operational_reasons": list(analysis.attrs.get("operational_reasons", [])),
        "layers": layers,
        "created_at": created_at,
    }
    objects["manifest.json"] = json.dumps(
        manifest, separators=(",", ":"), sort_keys=True
    ).encode()
    validate_diagnostic_bundle(objects)
    return objects


def validate_diagnostic_bundle(objects: Mapping[str, bytes]) -> dict[str, Any]:
    if "manifest.json" not in objects:
        raise DiagnosticInputError("diagnostic bundle has no manifest")
    try:
        manifest = json.loads(objects["manifest.json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise DiagnosticInputError("diagnostic manifest is invalid JSON") from error
    if manifest.get("contract_version") != "1.0" or not manifest.get("layers"):
        raise DiagnosticInputError("diagnostic manifest identity or layers are invalid")
    layer_ids: set[str] = set()
    grid_fields: set[str] = set()
    polar_fields: dict[str, set[str]] = {}
    for layer in manifest["layers"]:
        layer_id = layer.get("layer_id")
        object_path = layer.get("object_path")
        if (
            not isinstance(layer_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,127}", layer_id)
            or layer_id in layer_ids
            or object_path != f"layers/{layer_id}.png"
            or object_path not in objects
        ):
            raise DiagnosticInputError("diagnostic layer identity or path is invalid")
        layer_ids.add(layer_id)
        width, height = png_dimensions(objects[object_path])
        if [width, height] != [layer.get("width"), layer.get("height")]:
            raise DiagnosticInputError("diagnostic PNG dimensions differ from manifest")
        if layer.get("scope") == "grid":
            if not isinstance(layer.get("bounds"), list) or len(layer["bounds"]) != 4:
                raise DiagnosticInputError("grid diagnostic layer lacks frozen bounds")
            grid_fields.add(str(layer.get("field")))
        elif layer.get("scope") == "polar":
            radar_id = layer.get("radar_id")
            if not radar_id or layer.get("scan_id") is None:
                raise DiagnosticInputError("polar diagnostic layer lacks radar identity")
            polar_fields.setdefault(str(radar_id), set()).add(str(layer.get("field")))
        else:
            raise DiagnosticInputError("diagnostic scope is invalid")
    if grid_fields != {
        "DBZH_QC",
        "RATE_QPE",
        "QUALITY_INDEX",
        "SOURCE_RADAR",
        "BEAM_HEIGHT",
        "QC_FLAGS",
        "VALID_MASK+LOW_QUALITY_MASK",
    }:
        raise DiagnosticInputError("diagnostic bundle is missing a frozen grid layer")
    required_polar = {"DBZH_RAW", "DBZH_QC", "QUALITY_INDEX", "QC_FLAGS"}
    if not polar_fields or any(fields != required_polar for fields in polar_fields.values()):
        raise DiagnosticInputError("diagnostic bundle is missing a frozen polar layer")
    return {
        "layer_count": len(layer_ids),
        "grid_layer_count": len(grid_fields),
        "radar_count": len(polar_fields),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
        "manifest": manifest,
    }


def _open_group(objects: Mapping[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    return zarr.open_group(store=store, mode="r")


def _lowest_dbzh_sweep(root: zarr.Group) -> tuple[zarr.Group, int]:
    for sweep in root["sweep_number"][:]:
        number = int(sweep)
        group = root[f"sweep_{number:03d}"]
        if "DBZH_RAW" in group and np.any(np.isfinite(group["DBZH_RAW"][:])):
            return group, number
    raise DiagnosticInputError("QCRadarVolume has no finite DBZH sweep")


def _scalar_rgba(
    values: np.ndarray,
    valid: np.ndarray,
    stops: Sequence[tuple[float, str]],
) -> np.ndarray:
    data = np.asarray(values)
    mask = np.asarray(valid, dtype=bool) & np.isfinite(data)
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    thresholds = np.asarray([item[0] for item in stops], dtype=np.float64)
    colors = np.asarray([_hex_rgba(item[1]) for item in stops], dtype=np.uint8)
    indices = np.searchsorted(thresholds, data, side="right") - 1
    indices = np.clip(indices, 0, len(stops) - 1)
    rgba[mask] = colors[indices[mask]]
    return rgba


def _flag_rgba(
    values: np.ndarray,
    valid: np.ndarray,
    definitions: Mapping[str, int],
) -> np.ndarray:
    flags = np.asarray(values, dtype=np.uint32)
    rgba = np.zeros((*flags.shape, 4), dtype=np.uint8)
    ordered = sorted(definitions.items(), key=lambda item: item[1])
    for index, (_, mask) in enumerate(ordered):
        active = (flags & np.uint32(mask)) != 0
        rgba[active & valid] = _hex_rgba(FLAG_COLORS[index % len(FLAG_COLORS)], 225)
    unflagged = valid & (flags == 0)
    rgba[unflagged] = _hex_rgba("#94a09c", 90)
    return rgba


def _polar_to_ppi(
    polar_rgba: np.ndarray,
    azimuth: np.ndarray,
    ranges: np.ndarray,
    size: int,
) -> np.ndarray:
    if polar_rgba.shape[:2] != (len(azimuth), len(ranges)):
        raise DiagnosticInputError("polar field geometry differs from coordinates")
    coordinate = np.linspace(-1.0, 1.0, size, dtype=np.float32)
    x, y_from_south = np.meshgrid(coordinate, coordinate)
    y = -y_from_south
    radius = np.sqrt(x * x + y * y)
    inside = radius <= 1.0
    angle = np.mod(np.degrees(np.arctan2(x, y)), 360.0)

    sorted_indices = np.argsort(azimuth)
    sorted_azimuth = np.asarray(azimuth, dtype=np.float64)[sorted_indices]
    positions = np.searchsorted(sorted_azimuth, angle)
    left = (positions - 1) % len(sorted_azimuth)
    right = positions % len(sorted_azimuth)
    left_distance = np.abs((angle - sorted_azimuth[left] + 180.0) % 360.0 - 180.0)
    right_distance = np.abs((angle - sorted_azimuth[right] + 180.0) % 360.0 - 180.0)
    ray_index = sorted_indices[np.where(left_distance <= right_distance, left, right)]

    source_range = np.asarray(ranges, dtype=np.float64)
    target_range = radius * float(np.max(source_range))
    gate_index = np.searchsorted(source_range, target_range)
    gate_index = np.clip(gate_index, 0, len(source_range) - 1)
    previous = np.clip(gate_index - 1, 0, len(source_range) - 1)
    use_previous = np.abs(target_range - source_range[previous]) <= np.abs(
        target_range - source_range[gate_index]
    )
    gate_index = np.where(use_previous, previous, gate_index)
    output = polar_rgba[ray_index, gate_index].copy()
    output[~inside, 3] = 0
    return output


def _north_up_scaled(rgba: np.ndarray, scale: int) -> np.ndarray:
    result = np.flipud(rgba)
    if scale > 1:
        result = np.repeat(np.repeat(result, scale, axis=0), scale, axis=1)
    return result


def _store_layer(
    objects: dict[str, bytes],
    *,
    layer_id: str,
    title: str,
    scope: str,
    field: str,
    rendering: str,
    unit: str | None,
    rgba: np.ndarray,
    palette_version: str,
    legend: list[dict[str, Any]],
    bounds: list[float] | None = None,
    radar_id: str | None = None,
    scan_id: str | None = None,
    sweep_number: int | None = None,
    elevation_deg: float | None = None,
    maximum_range_km: float | None = None,
) -> dict[str, Any]:
    object_path = f"layers/{layer_id}.png"
    objects[object_path] = encode_rgba_png(rgba)
    layer: dict[str, Any] = {
        "layer_id": layer_id,
        "title": title,
        "scope": scope,
        "field": field,
        "rendering": rendering,
        "unit": unit,
        "object_path": object_path,
        "width": int(rgba.shape[1]),
        "height": int(rgba.shape[0]),
        "palette_version": palette_version,
        "legend": legend,
    }
    optional = {
        "bounds": bounds,
        "radar_id": radar_id,
        "scan_id": scan_id,
        "sweep_number": sweep_number,
        "elevation_deg": elevation_deg,
        "maximum_range_km": maximum_range_km,
    }
    layer.update({key: value for key, value in optional.items() if value is not None})
    return layer


def _numeric_legend(
    stops: Sequence[tuple[float, str]], unit: str
) -> list[dict[str, Any]]:
    return [
        {"label": f"≥ {value:g} {unit}", "color": color, "value": value}
        for value, color in stops
    ]


def _flag_legend(definitions: Mapping[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "label": name,
            "color": FLAG_COLORS[index % len(FLAG_COLORS)],
            "code": mask,
        }
        for index, (name, mask) in enumerate(sorted(definitions.items(), key=lambda item: item[1]))
    ]


def _hex_rgba(value: str, alpha: int = 255) -> np.ndarray:
    raw = value.lstrip("#")
    return np.asarray([int(raw[index : index + 2], 16) for index in (0, 2, 4)] + [alpha])


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not result:
        raise DiagnosticInputError("radar ID cannot form a diagnostic layer ID")
    return result
