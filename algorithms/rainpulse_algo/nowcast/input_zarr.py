from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.radar.analysis_zarr import validate_radar_analysis_zarr_store

from .input_profile import NowcastInputProfile

CONTRACT_NAME = "rainpulse.nowcast-input"
CONTRACT_VERSION = "1.2"
REQUIRED_FIELDS = {
    "DBZH_QC": np.dtype("float32"),
    "RATE_QPE": np.dtype("float32"),
    "QUALITY_INDEX": np.dtype("float32"),
    "QC_FLAGS": np.dtype("uint32"),
    "VALID_MASK": np.dtype("uint8"),
    "LOW_QUALITY_MASK": np.dtype("uint8"),
    "DATA_AGE": np.dtype("float32"),
}
OPTIONAL_FIELDS = {"BEAM_HEIGHT": np.dtype("float32")}


class NowcastInputError(ValueError):
    """Raised when a RadarAnalysis sequence cannot safely trigger a model."""


def build_nowcast_input_zarr_store(
    frames: Sequence[Mapping[str, bytes]],
    *,
    analysis_ids: Sequence[UUID | str],
    input_uris: Sequence[str],
    issue_time: datetime,
    profile: NowcastInputProfile,
    grid: RegularLatLonGrid,
    asset_id: UUID | str,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    issue_time = _utc(issue_time)
    roots = _open_frames(frames)
    _validate_sequence_identity(
        roots,
        analysis_ids=analysis_ids,
        input_uris=input_uris,
        issue_time=issue_time,
        profile=profile,
        grid=grid,
    )

    valid = np.stack([root["VALID_MASK"][:] for root in roots]) == 1
    low = np.stack([root["LOW_QUALITY_MASK"][:] for root in roots])
    quality = np.stack([root["QUALITY_INDEX"][:] for root in roots])
    data_age = np.stack([root["DATA_AGE"][:] for root in roots])
    frame_coverages = np.mean(valid, axis=(1, 2))
    valid_quality = quality[valid]
    valid_age = data_age[valid]
    input_asset_ids = _ordered_unique(
        asset_id for root in roots for asset_id in root.attrs["input_asset_ids"]
    )
    summary = {
        "schema_version": "1.0",
        "issue_time_utc": issue_time.isoformat(),
        "grid_id": grid.grid_id,
        "profile_version": profile.profile_version,
        "preprocess_version": profile.builder_version,
        "analysis_ids": [str(value) for value in analysis_ids],
        "input_asset_ids": input_asset_ids,
        "input_uris": list(input_uris),
        "frame_count": len(roots),
        "timestep_minutes": profile.sequence.timestep_minutes,
        "valid_coverage_ratio": float(np.min(frame_coverages)),
        "mean_quality_index": (float(np.mean(valid_quality)) if valid_quality.size else 0.0),
        "max_data_age_minutes": (float(np.max(valid_age)) if valid_age.size else 0.0),
        "valid_cell_count": int(np.count_nonzero(valid)),
        "missing_cell_count": int(np.count_nonzero(~valid)),
        "low_quality_cell_count": int(np.count_nonzero(low)),
        "operational_eligible": True,
        "operational_reasons": [],
    }
    _enforce_gates(summary, roots, profile)

    output_store = MemoryStore()
    target = zarr.group(store=output_store, overwrite=True)
    qpe_config_version = str(roots[0].attrs["qpe_config_version"])
    target.attrs.update(
        {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "asset_id": str(asset_id),
            "crs": "EPSG:4326",
            "registration": "point",
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "coordinate_sha256": grid.coordinate_sha256,
            "longitude_interval_deg": grid.longitude_interval_deg,
            "latitude_interval_deg": grid.latitude_interval_deg,
            "grid_metric_version": grid.metric().version,
            "timestep_minutes": profile.sequence.timestep_minutes,
            "issue_time_utc": issue_time.isoformat(),
            "source_name": "RadarAnalysis",
            "source_version": str(roots[0].attrs["qpe_algorithm_version"]),
            "preprocess_version": profile.builder_version,
            "gate_config_version": profile.profile_version,
            "input_asset_ids": input_asset_ids,
            "analysis_ids": [str(value) for value in analysis_ids],
            "qc_pipeline_versions": _ordered_unique(
                version for root in roots for version in root.attrs["qc_pipeline_versions"]
            ),
            "qpe_config_version": qpe_config_version,
            "input_uris": list(input_uris),
            "frame_count": len(roots),
            "operational_eligible": True,
            "operational_reasons": [],
            "valid_coverage_ratio": summary["valid_coverage_ratio"],
            "mean_quality_index": summary["mean_quality_index"],
            "max_data_age_minutes": summary["max_data_age_minutes"],
            "created_at_utc": datetime.now(UTC).isoformat(),
        }
    )
    if provenance:
        target.attrs.update(dict(provenance))

    target.create_dataset(
        "time",
        data=np.asarray(
            [_analysis_time(root).replace(tzinfo=None) for root in roots],
            dtype="datetime64[ns]",
        ),
        chunks=(len(roots),),
    ).attrs.update({"standard_name": "time", "timezone": "UTC"})
    for name, values in (("lat", grid.latitude), ("lon", grid.longitude)):
        array = target.create_dataset(name, data=values, chunks=(min(512, len(values)),))
        array.attrs.update(
            {
                "standard_name": "latitude" if name == "lat" else "longitude",
                "units": "degrees_north" if name == "lat" else "degrees_east",
            }
        )
    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    fields = list(REQUIRED_FIELDS)
    if all("BEAM_HEIGHT" in root for root in roots):
        fields.append("BEAM_HEIGHT")
    for name in fields:
        values = np.stack([root[name][:] for root in roots])
        array = target.create_dataset(
            name,
            data=values,
            chunks=(1, min(128, values.shape[1]), min(256, values.shape[2])),
            compressor=compressor,
            fill_value=np.nan if np.issubdtype(values.dtype, np.floating) else 0,
        )
        array.attrs.update(_field_attributes(name))
    output_store["input/summary.json"] = json.dumps(
        summary, separators=(",", ":"), sort_keys=True
    ).encode()
    zarr.consolidate_metadata(output_store)
    objects = {str(key): bytes(value) for key, value in output_store.items()}
    validate_nowcast_input_zarr_store(objects)
    return objects


def validate_nowcast_input_zarr_store(
    objects: Mapping[str, bytes],
) -> dict[str, Any]:
    required_objects = {".zgroup", ".zattrs", "input/summary.json"}
    if not required_objects <= objects.keys():
        raise NowcastInputError("NowcastInput Zarr is missing metadata or summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise NowcastInputError("NowcastInput contract name is invalid")
    if root.attrs.get("contract_version") != CONTRACT_VERSION:
        raise NowcastInputError("NowcastInput contract version is invalid")
    if root.attrs.get("crs") != "EPSG:4326" or root.attrs.get("registration") != "point":
        raise NowcastInputError("NowcastInput spatial reference is invalid")
    times = root["time"][:].astype("datetime64[ns]")
    if not 3 <= len(times) <= 6:
        raise NowcastInputError("NowcastInput must contain 3-6 frames")
    expected_step = np.timedelta64(5, "m")
    if np.any(np.diff(times) != expected_step):
        raise NowcastInputError("NowcastInput time coordinate is not fixed at five minutes")
    issue_time = np.datetime64(
        _parse_time(str(root.attrs["issue_time_utc"])).replace(tzinfo=None), "ns"
    )
    if times[-1] != issue_time:
        raise NowcastInputError("NowcastInput final frame differs from issue time")
    latitude = root["lat"][:]
    longitude = root["lon"][:]
    if latitude.dtype != np.dtype("float32") or longitude.dtype != np.dtype("float32"):
        raise NowcastInputError("NowcastInput coordinates must be float32")
    if np.any(np.diff(latitude) <= 0) or np.any(np.diff(longitude) <= 0):
        raise NowcastInputError("NowcastInput coordinates must be strictly increasing")
    shape = (len(times), len(latitude), len(longitude))
    for name, dtype in REQUIRED_FIELDS.items():
        if name not in root or root[name].shape != shape or root[name].dtype != dtype:
            raise NowcastInputError(f"NowcastInput field {name} has invalid shape or dtype")
    valid = root["VALID_MASK"][:]
    low = root["LOW_QUALITY_MASK"][:]
    if np.any((valid != 0) & (valid != 1)) or np.any((low != 0) & (low != 1)):
        raise NowcastInputError("NowcastInput masks are not binary")
    if np.any(low > valid):
        raise NowcastInputError("NowcastInput low-quality mask includes missing cells")
    missing = valid == 0
    for name in ("DBZH_QC", "RATE_QPE", "QUALITY_INDEX", "DATA_AGE"):
        values = root[name][:]
        if np.any(~np.isnan(values[missing])):
            raise NowcastInputError(f"NowcastInput missing cells contain finite {name}")
    rate = root["RATE_QPE"][:]
    age = root["DATA_AGE"][:]
    quality = root["QUALITY_INDEX"][:]
    if np.any(~np.isfinite(rate[~missing])) or np.any(rate[~missing] < 0):
        raise NowcastInputError("NowcastInput valid rain rates are invalid")
    if np.any(~np.isfinite(age[~missing])) or np.any(age[~missing] < 0):
        raise NowcastInputError("NowcastInput valid data ages are invalid")
    if np.any(~np.isfinite(quality[~missing])) or np.any(
        (quality[~missing] < 0) | (quality[~missing] > 1)
    ):
        raise NowcastInputError("NowcastInput quality is outside [0, 1]")
    summary = json.loads(objects["input/summary.json"])
    if (
        summary.get("analysis_ids") != root.attrs.get("analysis_ids")
        or summary.get("input_asset_ids") != root.attrs.get("input_asset_ids")
        or summary.get("frame_count") != len(times)
        or summary.get("valid_cell_count") != int(np.count_nonzero(valid))
        or summary.get("missing_cell_count") != int(np.count_nonzero(missing))
        or summary.get("low_quality_cell_count") != int(np.count_nonzero(low))
        or summary.get("operational_eligible") is not True
        or summary.get("operational_reasons") != []
    ):
        raise NowcastInputError("NowcastInput summary differs from arrays or attributes")
    return {
        "shape": shape,
        "frame_count": len(times),
        "valid_cell_count": int(np.count_nonzero(valid)),
        "missing_cell_count": int(np.count_nonzero(missing)),
        "low_quality_cell_count": int(np.count_nonzero(low)),
        "valid_coverage_ratio": float(summary["valid_coverage_ratio"]),
        "mean_quality_index": float(summary["mean_quality_index"]),
        "max_data_age_minutes": float(summary["max_data_age_minutes"]),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _open_frames(frames: Sequence[Mapping[str, bytes]]) -> list[zarr.Group]:
    roots: list[zarr.Group] = []
    for objects in frames:
        validate_radar_analysis_zarr_store(objects)
        store = MemoryStore()
        store.update({key: bytes(value) for key, value in objects.items()})
        roots.append(zarr.open_group(store=store, mode="r"))
    return roots


def _validate_sequence_identity(
    roots: Sequence[zarr.Group],
    *,
    analysis_ids: Sequence[UUID | str],
    input_uris: Sequence[str],
    issue_time: datetime,
    profile: NowcastInputProfile,
    grid: RegularLatLonGrid,
) -> None:
    count = len(roots)
    if not profile.sequence.minimum_frames <= count <= profile.sequence.maximum_frames:
        raise NowcastInputError("RadarAnalysis frame count is outside configured bounds")
    if count != len(analysis_ids) or count != len(input_uris):
        raise NowcastInputError("RadarAnalysis identities, URIs and frames differ in count")
    if len({str(value) for value in analysis_ids}) != count or len(set(input_uris)) != count:
        raise NowcastInputError("RadarAnalysis identities and URIs must be unique")
    if grid.grid_id != profile.grid_id or grid.config_version != profile.grid_config_version:
        raise NowcastInputError("mounted grid differs from the NowcastInput profile")
    expected_times = [
        issue_time - timedelta(minutes=profile.sequence.timestep_minutes * offset)
        for offset in reversed(range(count))
    ]
    reference_qpe: str | None = None
    reference_algorithm: str | None = None
    for index, (root, analysis_id, expected_time) in enumerate(
        zip(roots, analysis_ids, expected_times, strict=True)
    ):
        expected = (
            ("analysis_id", str(root.attrs.get("analysis_id")), str(analysis_id)),
            ("grid_id", str(root.attrs.get("grid_id")), grid.grid_id),
            (
                "grid_config_version",
                str(root.attrs.get("grid_config_version")),
                grid.config_version,
            ),
            (
                "coordinate_sha256",
                str(root.attrs.get("coordinate_sha256")),
                grid.coordinate_sha256,
            ),
        )
        for name, actual, configured in expected:
            if actual != configured:
                raise NowcastInputError(
                    f"RadarAnalysis frame {index} {name} differs from the request/profile"
                )
        if _analysis_time(root) != expected_time:
            raise NowcastInputError(
                "RadarAnalysis frames are not an exact contiguous five-minute sequence"
            )
        if not np.array_equal(root["lat"][:], grid.latitude) or not np.array_equal(
            root["lon"][:], grid.longitude
        ):
            raise NowcastInputError("RadarAnalysis coordinates differ from immutable grid")
        for attr in ("input_asset_ids", "qc_pipeline_versions"):
            values = root.attrs.get(attr)
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(value, str) and value for value in values)
            ):
                raise NowcastInputError(f"RadarAnalysis is missing required {attr}")
        qpe = str(root.attrs.get("qpe_config_version", ""))
        algorithm = str(root.attrs.get("qpe_algorithm_version", ""))
        reference_qpe = reference_qpe or qpe
        reference_algorithm = reference_algorithm or algorithm
        if not qpe or qpe != reference_qpe or not algorithm or algorithm != reference_algorithm:
            raise NowcastInputError("RadarAnalysis frames mix QPE versions")


def _enforce_gates(
    summary: Mapping[str, Any],
    roots: Sequence[zarr.Group],
    profile: NowcastInputProfile,
) -> None:
    failures: list[str] = []
    gates = profile.gates
    if summary["valid_coverage_ratio"] < gates.minimum_valid_coverage_ratio:
        failures.append("valid_coverage_below_threshold")
    if summary["mean_quality_index"] < gates.minimum_mean_quality_index:
        failures.append("mean_quality_below_threshold")
    if summary["max_data_age_minutes"] > gates.maximum_data_age_minutes:
        failures.append("data_age_above_threshold")
    if gates.require_all_frames_operational_eligible and any(
        not bool(root.attrs.get("operational_eligible")) for root in roots
    ):
        failures.append("upstream_analysis_not_operational")
    if failures:
        raise NowcastInputError("NowcastInput gate rejected sequence: " + ",".join(failures))


def _analysis_time(root: zarr.Group) -> datetime:
    return _parse_time(str(root.attrs.get("analysis_time")))


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise NowcastInputError("analysis time must include a UTC offset")
    return parsed.astimezone(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise NowcastInputError("issue time must include a UTC offset")
    return value.astimezone(UTC)


def _ordered_unique(values: Sequence[str] | Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _field_attributes(name: str) -> dict[str, Any]:
    if name == "DBZH_QC":
        return {"units": "dBZ", "missing_value": "NaN"}
    if name == "RATE_QPE":
        return {"units": "mm h-1", "valid_min": 0.0, "missing_value": "NaN"}
    if name == "QUALITY_INDEX":
        return {"units": "1", "valid_range": [0.0, 1.0], "missing_value": "NaN"}
    if name == "DATA_AGE":
        return {"units": "min", "valid_min": 0.0, "missing_value": "NaN"}
    if name == "BEAM_HEIGHT":
        return {"units": "m", "missing_value": "NaN"}
    return {"units": "1"}
