from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
import zarr
from zarr.storage import MemoryStore


# A constant-power transmitter/interference signal grows with range after the
# radar's range correction and can occupy a contiguous fan of neighbouring
# rays.  Immediate-neighbour differencing cannot see the interior of that fan.
# These deliberately conservative limits describe the Z9591 long-range
# saturation signature: hundreds of consecutive gates, most of a full ray
# above convective reflectivity, and a pronounced increase towards far range.
_SATURATED_RADIAL_MINIMUM_VALID_FRACTION = 0.80
_SATURATED_RADIAL_MINIMUM_HIGH_DBZH = 45.0
_SATURATED_RADIAL_MINIMUM_HIGH_FRACTION = 0.70
_SATURATED_RADIAL_MINIMUM_HIGH_RUN = 400
_SATURATED_RADIAL_MINIMUM_RANGE_GROWTH_DB = 12.0


class QCConfigError(ValueError):
    pass


class QCInputError(ValueError):
    pass


@dataclass(frozen=True)
class HealthGateConfig:
    reject_states: tuple[str, ...]
    degraded_quality_multiplier: float


@dataclass(frozen=True)
class EchoConfig:
    dbzh_valid_range_dbz: tuple[float, float]
    no_rain_below_dbz: float
    low_snr_db: float
    snr_meteo_range_db: tuple[float, float]
    rhohv_meteo_range: tuple[float, float]
    fallback_meteo_probability: float


@dataclass(frozen=True)
class RadialInterferenceConfig:
    minimum_valid_gate_fraction: float
    minimum_consecutive_gates: int
    neighbour_difference_db: float
    low_quality_probability: float
    flag_probability: float


@dataclass(frozen=True)
class StaticGroundClutterConfig:
    asset_uri: str | None
    asset_version: str | None
    flag_probability: float


@dataclass(frozen=True)
class SeaAPConfig:
    coastline_asset_uri: str | None
    asset_version: str | None
    flag_probability: float


@dataclass(frozen=True)
class QualityIndexConfig:
    aggregation: Literal["product"]
    components: tuple[str, ...]
    minimum_range_quality: float
    low_quality_threshold: float
    unavailable_component_policy: Literal["skip_and_record"]


@dataclass(frozen=True)
class BasicQCProfile:
    profile_version: str
    pipeline_version: str
    flag_definition_version: str
    health_gate: HealthGateConfig
    echo: EchoConfig
    radial_interference: RadialInterferenceConfig
    static_ground_clutter: StaticGroundClutterConfig
    sea_ap: SeaAPConfig
    quality_index: QualityIndexConfig
    flag_masks: dict[str, np.uint32]


@dataclass(frozen=True)
class QCModuleRecord:
    name: str
    version: str
    status: Literal["applied", "skipped", "failed"]
    input_fields: tuple[str, ...]
    produced_variables: tuple[str, ...]
    reason: str | None
    metrics: dict[str, float]

    def value(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "input_fields": list(self.input_fields),
            "produced_variables": list(self.produced_variables),
            "reason": self.reason,
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class QCSweep:
    name: str
    dbzh_raw: np.ndarray
    dbzh_qc: np.ndarray
    optional_qc_fields: dict[str, np.ndarray]
    quality_index: np.ndarray
    qi_components: dict[str, np.ndarray]
    qc_flags: np.ndarray
    valid_mask: np.ndarray
    low_quality_mask: np.ndarray
    p_meteo: np.ndarray
    p_ap: np.ndarray
    p_sea_clutter: np.ndarray
    p_radial_interference: np.ndarray


@dataclass(frozen=True)
class QCResult:
    profile: BasicQCProfile
    sweeps: tuple[QCSweep, ...]
    modules: tuple[QCModuleRecord, ...]
    health: dict[str, Any]
    summary: dict[str, Any]
    created_at: datetime

    def module_status(self, name: str) -> str:
        return next(item.status for item in self.modules if item.name == name)

    def summary_bytes(self) -> bytes:
        return json.dumps(self.summary, separators=(",", ":"), sort_keys=True).encode()


def load_qc_profile(path: str | Path, flag_path: str | Path) -> BasicQCProfile:
    value = yaml.safe_load(Path(path).read_text())
    flags = yaml.safe_load(Path(flag_path).read_text())
    if value.get("schema_version") != "1.0":
        raise QCConfigError("unsupported radar QC schema version")
    if flags.get("definition_version") != value.get("flag_definition_version"):
        raise QCConfigError("QC flag definition version differs from the profile")
    masks = {item["name"]: np.uint32(item["mask"]) for item in flags.get("flags", [])}
    required_flags = {
        "GROUND_CLUTTER",
        "SEA_CLUTTER",
        "ANOMALOUS_PROPAGATION",
        "RADIAL_INTERFERENCE",
        "HARDWARE_ANOMALY",
        "LOW_SNR",
        "MISSING",
        "LOW_QUALITY",
    }
    if not required_flags <= set(masks):
        raise QCConfigError("QC flag definition is missing RP-008 flags")

    health = value["health_gate"]
    echo = value["echo"]
    radial = value["radial_interference"]
    clutter = value["static_ground_clutter"]
    sea_ap = value["sea_ap"]
    qi = value["quality_index"]
    profile = BasicQCProfile(
        profile_version=value["profile_version"],
        pipeline_version=value["pipeline_version"],
        flag_definition_version=value["flag_definition_version"],
        health_gate=HealthGateConfig(
            reject_states=tuple(health["reject_states"]),
            degraded_quality_multiplier=float(health["degraded_quality_multiplier"]),
        ),
        echo=EchoConfig(
            dbzh_valid_range_dbz=_pair(echo["dbzh_valid_range_dbz"]),
            no_rain_below_dbz=float(echo["no_rain_below_dbz"]),
            low_snr_db=float(echo["low_snr_db"]),
            snr_meteo_range_db=_pair(echo["snr_meteo_range_db"]),
            rhohv_meteo_range=_pair(echo["rhohv_meteo_range"]),
            fallback_meteo_probability=float(echo["fallback_meteo_probability"]),
        ),
        radial_interference=RadialInterferenceConfig(
            minimum_valid_gate_fraction=float(radial["minimum_valid_gate_fraction"]),
            minimum_consecutive_gates=int(radial["minimum_consecutive_gates"]),
            neighbour_difference_db=float(radial["neighbour_difference_db"]),
            low_quality_probability=float(radial["low_quality_probability"]),
            flag_probability=float(radial["flag_probability"]),
        ),
        static_ground_clutter=StaticGroundClutterConfig(
            asset_uri=clutter["asset_uri"],
            asset_version=clutter["asset_version"],
            flag_probability=float(clutter["flag_probability"]),
        ),
        sea_ap=SeaAPConfig(
            coastline_asset_uri=sea_ap["coastline_asset_uri"],
            asset_version=sea_ap["asset_version"],
            flag_probability=float(sea_ap["flag_probability"]),
        ),
        quality_index=QualityIndexConfig(
            aggregation=qi["aggregation"],
            components=tuple(qi["components"]),
            minimum_range_quality=float(qi["minimum_range_quality"]),
            low_quality_threshold=float(qi["low_quality_threshold"]),
            unavailable_component_policy=qi["unavailable_component_policy"],
        ),
        flag_masks=masks,
    )
    _validate_profile(profile)
    return profile


def apply_basic_qc(
    normalized_objects: dict[str, bytes],
    profile: BasicQCProfile,
    *,
    ancillary_maps: dict[str, dict[str, np.ndarray]] | None = None,
    created_at: datetime | None = None,
) -> QCResult:
    if "health/summary.json" not in normalized_objects:
        raise QCInputError("normalized volume has no RP-007 health summary")
    health = json.loads(normalized_objects["health/summary.json"])
    state = health.get("health")
    if state in profile.health_gate.reject_states:
        raise QCInputError(f"radar health state {state} is rejected by QC profile")
    if state not in {"HEALTHY", "DEGRADED"}:
        raise QCInputError(f"unsupported radar health state {state!r}")

    store = MemoryStore()
    store.update(normalized_objects)
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise QCInputError("QC input is not a NormalizedRadarVolume")
    if health.get("radar_id") != root.attrs.get("radar_id"):
        raise QCInputError("radar health identity differs from normalized volume")

    sweeps: list[QCSweep] = []
    radial_ray_count = 0
    missing_count = 0
    low_quality_count = 0
    valid_count = 0
    no_rain_count = 0
    ground_count = 0
    sea_count = 0
    ap_count = 0
    clutter_available = profile.static_ground_clutter.asset_uri is not None
    sea_ap_available = profile.sea_ap.coastline_asset_uri is not None

    for sweep_number in root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        group = root[name]
        if "DBZH" in group:
            dbzh = group["DBZH"][:].astype("float32", copy=True)
        else:
            dbzh = np.full(
                (len(group["azimuth"]), len(group["range"])),
                np.nan,
                dtype="float32",
            )
        valid = np.isfinite(dbzh)
        lower, upper = profile.echo.dbzh_valid_range_dbz
        out_of_range = valid & ((dbzh < lower) | (dbzh > upper))
        valid &= ~out_of_range
        missing = ~valid
        no_rain = valid & (dbzh < profile.echo.no_rain_below_dbz)
        flags = np.zeros(dbzh.shape, dtype="uint32")
        flags[missing] |= profile.flag_masks["MISSING"]
        flags[out_of_range] |= profile.flag_masks["HARDWARE_ANOMALY"]

        snr = group["SNR"][:] if "SNR" in group else None
        rhohv = group["RHOHV"][:] if "RHOHV" in group else None
        p_meteo = _meteorological_probability(dbzh, valid, no_rain, snr, rhohv, profile)
        low_snr = np.zeros(dbzh.shape, dtype=bool)
        if snr is not None:
            low_snr = valid & ~no_rain & np.isfinite(snr) & (snr < profile.echo.low_snr_db)
            flags[low_snr] |= profile.flag_masks["LOW_SNR"]

        p_radial, radial_rays = _radial_probability(dbzh, valid, profile.radial_interference)
        radial_flags = valid & (p_radial >= profile.radial_interference.flag_probability)
        flags[radial_flags] |= profile.flag_masks["RADIAL_INTERFERENCE"]
        radial_ray_count += radial_rays

        sweep_maps = (ancillary_maps or {}).get(name, {})
        if clutter_available:
            p_ground = _probability_map(sweep_maps.get("ground_clutter"), dbzh.shape, name)
            ground_flags = valid & (p_ground >= profile.static_ground_clutter.flag_probability)
            flags[ground_flags] |= profile.flag_masks["GROUND_CLUTTER"]
            ground_count += int(np.count_nonzero(ground_flags))
        else:
            p_ground = np.full(dbzh.shape, np.nan, dtype="float32")

        if sea_ap_available:
            p_sea = _probability_map(sweep_maps.get("sea_clutter"), dbzh.shape, name)
            p_ap = _probability_map(sweep_maps.get("ap"), dbzh.shape, name)
            sea_flags = valid & (p_sea >= profile.sea_ap.flag_probability)
            ap_flags = valid & (p_ap >= profile.sea_ap.flag_probability)
            flags[sea_flags] |= profile.flag_masks["SEA_CLUTTER"]
            flags[ap_flags] |= profile.flag_masks["ANOMALOUS_PROPAGATION"]
            sea_count += int(np.count_nonzero(sea_flags))
            ap_count += int(np.count_nonzero(ap_flags))
        else:
            p_sea = np.full(dbzh.shape, np.nan, dtype="float32")
            p_ap = np.full(dbzh.shape, np.nan, dtype="float32")

        qi_meteo = p_meteo.copy()
        for probability in (p_ground, p_sea, p_ap):
            available = np.isfinite(probability)
            qi_meteo[available] *= 1.0 - probability[available]
        qi_interference = np.where(valid, 1.0 - np.nan_to_num(p_radial, nan=0.0), np.nan)
        ranges = group["range"][:]
        range_quality = _range_quality(ranges, profile.quality_index.minimum_range_quality)
        qi_range = np.broadcast_to(range_quality, dbzh.shape).astype("float32", copy=True)
        qi_range[missing] = np.nan
        unavailable = np.full(dbzh.shape, np.nan, dtype="float32")
        components = {
            "QI_METEO": qi_meteo.astype("float32"),
            "QI_BLOCKAGE": unavailable.copy(),
            "QI_BEAM_HEIGHT": unavailable.copy(),
            "QI_ATTENUATION": unavailable.copy(),
            "QI_INTERFERENCE": qi_interference.astype("float32"),
            "QI_TIME": unavailable.copy(),
            "QI_CALIBRATION": unavailable.copy(),
            "QI_RANGE": qi_range,
        }
        quality = np.ones(dbzh.shape, dtype="float32")
        for component_name in profile.quality_index.components:
            component = components[component_name]
            available = np.isfinite(component)
            quality[available] *= component[available]
        if state == "DEGRADED":
            quality[valid] *= profile.health_gate.degraded_quality_multiplier
        quality[missing] = np.nan
        low_quality = valid & (
            (quality < profile.quality_index.low_quality_threshold)
            | low_snr
            | (p_radial >= profile.radial_interference.low_quality_probability)
        )
        flags[low_quality] |= profile.flag_masks["LOW_QUALITY"]
        dbzh_qc = dbzh.copy()
        dbzh_qc[missing] = np.nan
        optional = {
            output_name: group[source_name][:].astype("float32", copy=True)
            for source_name, output_name in (
                ("ZDR", "ZDR_QC"),
                ("PHIDP", "PHIDP_QC"),
                ("VR", "VR_QC"),
            )
            if source_name in group
        }
        sweeps.append(
            QCSweep(
                name=name,
                dbzh_raw=dbzh,
                dbzh_qc=dbzh_qc,
                optional_qc_fields=optional,
                quality_index=quality,
                qi_components=components,
                qc_flags=flags,
                valid_mask=valid.astype("uint8"),
                low_quality_mask=low_quality.astype("uint8"),
                p_meteo=p_meteo.astype("float32"),
                p_ap=p_ap.astype("float32"),
                p_sea_clutter=p_sea.astype("float32"),
                p_radial_interference=p_radial.astype("float32"),
            )
        )
        missing_count += int(np.count_nonzero(missing))
        low_quality_count += int(np.count_nonzero(low_quality))
        valid_count += int(np.count_nonzero(valid))
        no_rain_count += int(np.count_nonzero(no_rain))

    modules = _module_records(
        profile,
        clutter_available=clutter_available,
        sea_ap_available=sea_ap_available,
        radial_ray_count=radial_ray_count,
        ground_count=ground_count,
        sea_count=sea_count,
        ap_count=ap_count,
    )
    finite_quality = np.concatenate(
        [sweep.quality_index[np.isfinite(sweep.quality_index)] for sweep in sweeps]
    )
    mean_quality = float(finite_quality.mean()) if finite_quality.size else 0.0
    summary = {
        "schema_version": "1.0",
        "radar_id": root.attrs["radar_id"],
        "scan_id": root.attrs.get("scan_id"),
        "qc_profile": profile.profile_version,
        "qc_pipeline_version": profile.pipeline_version,
        "flag_definition_version": profile.flag_definition_version,
        "health_state": state,
        "mean_quality_index": round(mean_quality, 6),
        "valid_gate_count": valid_count,
        "missing_gate_count": missing_count,
        "low_quality_gate_count": low_quality_count,
        "no_rain_gate_count": no_rain_count,
        "radial_interference_ray_count": radial_ray_count,
        "ground_clutter_gate_count": ground_count,
        "sea_clutter_gate_count": sea_count,
        "ap_gate_count": ap_count,
        "module_statuses": {record.name: record.status for record in modules},
        "module_records": [record.value() for record in modules],
    }
    return QCResult(
        profile=profile,
        sweeps=tuple(sweeps),
        modules=modules,
        health=health,
        summary=summary,
        created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
    )


def _meteorological_probability(
    dbzh: np.ndarray,
    valid: np.ndarray,
    no_rain: np.ndarray,
    snr: np.ndarray | None,
    rhohv: np.ndarray | None,
    profile: BasicQCProfile,
) -> np.ndarray:
    total = np.zeros(dbzh.shape, dtype="float32")
    count = np.zeros(dbzh.shape, dtype="uint8")
    candidates = (
        (snr, profile.echo.snr_meteo_range_db),
        (rhohv, profile.echo.rhohv_meteo_range),
    )
    for values, bounds in candidates:
        if values is None:
            continue
        available = valid & np.isfinite(values)
        scaled = np.clip((values - bounds[0]) / (bounds[1] - bounds[0]), 0.0, 1.0)
        total[available] += scaled[available]
        count[available] += 1
    probability = np.full(dbzh.shape, profile.echo.fallback_meteo_probability, dtype="float32")
    available = count > 0
    probability[available] = total[available] / count[available]
    probability[no_rain] = 1.0
    probability[~valid] = np.nan
    return probability


def _radial_probability(
    dbzh: np.ndarray,
    valid: np.ndarray,
    config: RadialInterferenceConfig,
) -> tuple[np.ndarray, int]:
    probabilities = np.zeros(dbzh.shape, dtype="float32")
    probabilities[~valid] = np.nan
    if dbzh.shape[0] < 2:
        return probabilities, 0
    flagged_count = 0
    for ray_index in range(dbzh.shape[0]):
        ray_valid = valid[ray_index]
        if np.mean(ray_valid) < config.minimum_valid_gate_fraction:
            continue
        if _longest_run(ray_valid) < config.minimum_consecutive_gates:
            continue
        if _is_long_range_saturated_radial(dbzh[ray_index], ray_valid):
            probabilities[ray_index, ray_valid] = config.flag_probability
            flagged_count += 1
            continue
        neighbour_index = (ray_index + 1) % dbzh.shape[0]
        if dbzh.shape[0] > 2:
            previous = (ray_index - 1) % dbzh.shape[0]
            previous_values = dbzh[previous]
            next_values = dbzh[neighbour_index]
            previous_finite = np.isfinite(previous_values)
            next_finite = np.isfinite(next_values)
            baseline = np.full(dbzh.shape[1], np.nan, dtype="float32")
            both = previous_finite & next_finite
            baseline[both] = (previous_values[both] + next_values[both]) / 2.0
            only_previous = previous_finite & ~next_finite
            baseline[only_previous] = previous_values[only_previous]
            only_next = next_finite & ~previous_finite
            baseline[only_next] = next_values[only_next]
        else:
            baseline = dbzh[neighbour_index]
        overlap = ray_valid & np.isfinite(baseline)
        if not np.any(overlap):
            continue
        difference = float(np.median(np.abs(dbzh[ray_index, overlap] - baseline[overlap])))
        if difference < config.neighbour_difference_db:
            continue
        probabilities[ray_index, ray_valid] = config.flag_probability
        flagged_count += 1
    return probabilities, flagged_count


def _is_long_range_saturated_radial(dbzh: np.ndarray, valid: np.ndarray) -> bool:
    """Detect a range-growing, nearly full-ray interference signature."""
    if np.mean(valid) < _SATURATED_RADIAL_MINIMUM_VALID_FRACTION:
        return False
    high = valid & (dbzh >= _SATURATED_RADIAL_MINIMUM_HIGH_DBZH)
    valid_count = int(np.count_nonzero(valid))
    if np.count_nonzero(high) / valid_count < _SATURATED_RADIAL_MINIMUM_HIGH_FRACTION:
        return False
    if _longest_run(high) < _SATURATED_RADIAL_MINIMUM_HIGH_RUN:
        return False

    gate_count = dbzh.shape[0]
    quartile = max(gate_count // 4, 1)
    near_values = dbzh[:quartile][valid[:quartile]]
    far_values = dbzh[-quartile:][valid[-quartile:]]
    if near_values.size == 0 or far_values.size == 0:
        return False
    range_growth = float(np.median(far_values) - np.median(near_values))
    return range_growth >= _SATURATED_RADIAL_MINIMUM_RANGE_GROWTH_DB


def _range_quality(ranges: np.ndarray, minimum: float) -> np.ndarray:
    if ranges.size <= 1 or ranges[-1] <= ranges[0]:
        return np.ones(ranges.shape, dtype="float32")
    fraction = (ranges - ranges[0]) / (ranges[-1] - ranges[0])
    return (1.0 - (1.0 - minimum) * fraction).astype("float32")


def _probability_map(values: np.ndarray | None, shape: tuple[int, ...], sweep: str) -> np.ndarray:
    if values is None:
        return np.full(shape, np.nan, dtype="float32")
    result = np.asarray(values, dtype="float32")
    if result.shape != shape:
        raise QCInputError(f"ancillary probability map shape differs for {sweep}")
    if np.any(np.isfinite(result) & ((result < 0) | (result > 1))):
        raise QCInputError(f"ancillary probability map is outside [0, 1] for {sweep}")
    return result.copy()


def _module_records(
    profile: BasicQCProfile,
    *,
    clutter_available: bool,
    sea_ap_available: bool,
    radial_ray_count: int,
    ground_count: int,
    sea_count: int,
    ap_count: int,
) -> tuple[QCModuleRecord, ...]:
    return (
        QCModuleRecord(
            "health_gate", profile.pipeline_version, "applied", (), (), None, {}
        ),
        QCModuleRecord(
            "missing_and_echo_state",
            profile.pipeline_version,
            "applied",
            ("DBZH", "SNR", "RHOHV"),
            ("VALID_MASK", "P_METEO"),
            None,
            {},
        ),
        QCModuleRecord(
            "radial_interference",
            profile.pipeline_version,
            "applied",
            ("DBZH",),
            ("P_RADIAL_INTERFERENCE", "QC_FLAGS", "QI_INTERFERENCE"),
            None,
            {"flagged_ray_count": float(radial_ray_count)},
        ),
        QCModuleRecord(
            "static_ground_clutter",
            profile.pipeline_version,
            "applied" if clutter_available else "skipped",
            ("DBZH",),
            ("QC_FLAGS",),
            None if clutter_available else "clutter_map_asset_unavailable",
            {"flagged_gate_count": float(ground_count)},
        ),
        QCModuleRecord(
            "sea_ap",
            profile.pipeline_version,
            "applied" if sea_ap_available else "skipped",
            ("DBZH", "RHOHV", "VR", "SW"),
            ("P_SEA_CLUTTER", "P_AP", "QC_FLAGS"),
            None if sea_ap_available else "coastline_probability_asset_unavailable",
            {"sea_gate_count": float(sea_count), "ap_gate_count": float(ap_count)},
        ),
        QCModuleRecord(
            "quality_index",
            profile.pipeline_version,
            "applied",
            profile.quality_index.components,
            ("QUALITY_INDEX", "LOW_QUALITY_MASK"),
            None,
            {},
        ),
        QCModuleRecord(
            "dem_blockage",
            "rp009-deferred",
            "skipped",
            (),
            ("QI_BLOCKAGE", "QI_BEAM_HEIGHT"),
            "deferred_to_rp009",
            {},
        ),
        QCModuleRecord(
            "attenuation_and_calibration",
            "phase2-deferred",
            "skipped",
            (),
            ("QI_ATTENUATION", "QI_CALIBRATION"),
            "verified_calibration_and_phase_processing_unavailable",
            {},
        ),
    )


def _longest_run(values: np.ndarray) -> int:
    longest = 0
    current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def _pair(value: list[float]) -> tuple[float, float]:
    result = (float(value[0]), float(value[1]))
    if result[0] >= result[1]:
        raise QCConfigError("QC range lower bound must be below upper bound")
    return result


def _validate_profile(profile: BasicQCProfile) -> None:
    if not 0 <= profile.health_gate.degraded_quality_multiplier <= 1:
        raise QCConfigError("invalid degraded quality multiplier")
    if (
        profile.radial_interference.low_quality_probability
        > profile.radial_interference.flag_probability
    ):
        raise QCConfigError("radial low-quality probability must not exceed flag probability")
    if profile.static_ground_clutter.asset_uri and not profile.static_ground_clutter.asset_version:
        raise QCConfigError("configured clutter asset requires an asset version")
    if profile.sea_ap.coastline_asset_uri and not profile.sea_ap.asset_version:
        raise QCConfigError("configured coastline asset requires an asset version")
