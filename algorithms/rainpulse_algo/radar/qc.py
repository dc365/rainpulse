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

from .qc_metrics import polar_mask_area_km2

# A constant-power transmitter/interference signal grows with range after the
# radar's range correction and can occupy a contiguous fan of neighbouring
# rays.  Immediate-neighbour differencing cannot see the interior of that fan.
# These deliberately conservative limits describe the Z9591 long-range
# saturation signature: hundreds of consecutive gates, most of a full ray
# above convective reflectivity, and a pronounced increase towards far range.
_SATURATED_RADIAL_MINIMUM_VALID_FRACTION = 0.80
_SATURATED_RADIAL_MINIMUM_HIGH_DBZH = 45.0
_SATURATED_RADIAL_MINIMUM_HIGH_FRACTION = 0.60
_SATURATED_RADIAL_MINIMUM_HIGH_RUN = 400
_SATURATED_RADIAL_MINIMUM_RANGE_GROWTH_DB = 12.0
_SATURATED_RADIAL_SIGNATURE_VERSION = "long-range-saturated-radial-v2"

INTERFERENCE_TYPE_CODES = {
    "none": 0,
    "narrow": 1,
    "broad": 2,
    "intermittent": 3,
    "short_range": 4,
    "reverse": 5,
}


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
class DualPolFuzzyConfig:
    enabled: bool
    mode: Literal["diagnostic_only", "quality_index"]
    zdr_plausible_range_db: tuple[float, float]
    zdr_transition_db: float
    phidp_step_range_deg: tuple[float, float]
    weights: dict[str, float]


@dataclass(frozen=True)
class VerticalConsistencyConfig:
    enabled: bool
    mode: Literal["diagnostic_only", "radial_evidence"]
    minimum_dbzh: float
    support_tolerance_db: float
    maximum_range_m: float


@dataclass(frozen=True)
class RadialMorphologyConfig:
    enabled: bool
    mode: Literal["diagnostic_only", "quality_index"]
    candidate_difference_db: float
    minimum_segment_gates: int
    intermittent_minimum_segments: int
    short_range_max_m: float
    reverse_minimum_drop_db: float
    diagnostic_probability: float


@dataclass(frozen=True)
class RadialInterferenceConfig:
    minimum_valid_gate_fraction: float
    minimum_consecutive_gates: int
    neighbour_difference_db: float
    low_quality_probability: float
    flag_probability: float
    morphology: RadialMorphologyConfig


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
    dual_pol_fuzzy: DualPolFuzzyConfig
    vertical_consistency: VerticalConsistencyConfig
    radial_interference: RadialInterferenceConfig
    static_ground_clutter: StaticGroundClutterConfig
    sea_ap: SeaAPConfig
    quality_index: QualityIndexConfig
    flag_masks: dict[str, np.uint32]


@dataclass(frozen=True)
class SaturatedRadialEvidence:
    high_gate_fraction: float
    longest_high_run: int
    range_growth_db: float
    peak_dbzh: float

    def value(self) -> dict[str, float | int]:
        return {
            "high_gate_fraction": self.high_gate_fraction,
            "longest_high_run": self.longest_high_run,
            "range_growth_db": self.range_growth_db,
            "peak_dbzh": self.peak_dbzh,
        }


@dataclass(frozen=True)
class RadialInterferenceDetection:
    probability: np.ndarray
    interference_type: np.ndarray
    flagged_ray_count: int
    type_ray_counts: dict[str, int]
    type_gate_counts: dict[str, int]


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
    p_meteo_dual_pol: np.ndarray
    p_vertical_consistency: np.ndarray
    interference_type: np.ndarray


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
    dual_pol = value.get("dual_pol_fuzzy") or {}
    vertical = value.get("vertical_consistency") or {}
    radial = value["radial_interference"]
    morphology = radial.get("morphology") or {}
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
        dual_pol_fuzzy=DualPolFuzzyConfig(
            enabled=bool(dual_pol.get("enabled", False)),
            mode=dual_pol.get("mode", "diagnostic_only"),
            zdr_plausible_range_db=_pair(
                dual_pol.get("zdr_plausible_range_db", [-2.0, 5.0])
            ),
            zdr_transition_db=float(dual_pol.get("zdr_transition_db", 2.0)),
            phidp_step_range_deg=_pair(
                dual_pol.get("phidp_step_range_deg", [3.0, 30.0])
            ),
            weights={
                name: float(weight)
                for name, weight in (
                    dual_pol.get("weights")
                    or {"rhohv": 0.55, "snr": 0.15, "zdr": 0.15, "phidp": 0.15}
                ).items()
            },
        ),
        vertical_consistency=VerticalConsistencyConfig(
            enabled=bool(vertical.get("enabled", False)),
            mode=vertical.get("mode", "diagnostic_only"),
            minimum_dbzh=float(vertical.get("minimum_dbzh", 10.0)),
            support_tolerance_db=float(vertical.get("support_tolerance_db", 15.0)),
            maximum_range_m=float(vertical.get("maximum_range_m", 150_000.0)),
        ),
        radial_interference=RadialInterferenceConfig(
            minimum_valid_gate_fraction=float(radial["minimum_valid_gate_fraction"]),
            minimum_consecutive_gates=int(radial["minimum_consecutive_gates"]),
            neighbour_difference_db=float(radial["neighbour_difference_db"]),
            low_quality_probability=float(radial["low_quality_probability"]),
            flag_probability=float(radial["flag_probability"]),
            morphology=RadialMorphologyConfig(
                enabled=bool(morphology.get("enabled", False)),
                mode=morphology.get("mode", "diagnostic_only"),
                candidate_difference_db=float(
                    morphology.get(
                        "candidate_difference_db", radial["neighbour_difference_db"]
                    )
                ),
                minimum_segment_gates=int(
                    morphology.get("minimum_segment_gates", 12)
                ),
                intermittent_minimum_segments=int(
                    morphology.get("intermittent_minimum_segments", 3)
                ),
                short_range_max_m=float(
                    morphology.get("short_range_max_m", 60_000.0)
                ),
                reverse_minimum_drop_db=float(
                    morphology.get("reverse_minimum_drop_db", 12.0)
                ),
                diagnostic_probability=float(
                    morphology.get("diagnostic_probability", 0.65)
                ),
            ),
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

    vertical_probabilities = _volume_vertical_consistency(root, profile)
    sweeps: list[QCSweep] = []
    radial_ray_count = 0
    radial_gate_count = 0
    radial_area_km2 = 0.0
    type_ray_counts = {name: 0 for name in INTERFERENCE_TYPE_CODES if name != "none"}
    type_gate_counts = {name: 0 for name in INTERFERENCE_TYPE_CODES if name != "none"}
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
        zdr = group["ZDR"][:] if "ZDR" in group else None
        phidp = group["PHIDP"][:] if "PHIDP" in group else None
        p_meteo = _meteorological_probability(dbzh, valid, no_rain, snr, rhohv, profile)
        p_meteo_dual_pol = _dual_pol_meteorological_probability(
            dbzh,
            valid,
            no_rain,
            snr=snr,
            rhohv=rhohv,
            zdr=zdr,
            phidp=phidp,
            config=profile.dual_pol_fuzzy,
            echo=profile.echo,
        )
        if profile.dual_pol_fuzzy.mode == "quality_index":
            available = np.isfinite(p_meteo_dual_pol)
            p_meteo[available] = p_meteo_dual_pol[available]
        low_snr = np.zeros(dbzh.shape, dtype=bool)
        if snr is not None:
            low_snr = valid & ~no_rain & np.isfinite(snr) & (snr < profile.echo.low_snr_db)
            flags[low_snr] |= profile.flag_masks["LOW_SNR"]

        ranges = group["range"][:]
        p_vertical = vertical_probabilities.get(
            name, np.full(dbzh.shape, np.nan, dtype="float32")
        )
        detection = _detect_radial_interference(
            dbzh,
            valid,
            profile.radial_interference,
            ranges_m=ranges,
            vertical_consistency=(
                p_vertical
                if profile.vertical_consistency.mode == "radial_evidence"
                else None
            ),
        )
        p_radial = detection.probability
        radial_flags = valid & (p_radial >= profile.radial_interference.flag_probability)
        flags[radial_flags] |= profile.flag_masks["RADIAL_INTERFERENCE"]
        radial_ray_count += detection.flagged_ray_count
        radial_gate_count += int(np.count_nonzero(radial_flags))
        radial_area_km2 += polar_mask_area_km2(
            radial_flags,
            ranges,
            group["azimuth"][:],
        )
        for type_name in type_ray_counts:
            type_ray_counts[type_name] += detection.type_ray_counts[type_name]
            type_gate_counts[type_name] += detection.type_gate_counts[type_name]

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
        operational_radial = p_radial
        if profile.radial_interference.morphology.mode == "diagnostic_only":
            operational_radial = np.where(
                p_radial >= profile.radial_interference.flag_probability,
                p_radial,
                0.0,
            )
        qi_interference = np.where(
            valid,
            1.0 - np.nan_to_num(operational_radial, nan=0.0),
            np.nan,
        )
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
            | (
                operational_radial
                >= profile.radial_interference.low_quality_probability
            )
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
                p_meteo_dual_pol=p_meteo_dual_pol.astype("float32"),
                p_vertical_consistency=p_vertical.astype("float32"),
                interference_type=detection.interference_type,
            )
        )
        missing_count += int(np.count_nonzero(missing))
        low_quality_count += int(np.count_nonzero(low_quality))
        valid_count += int(np.count_nonzero(valid))
        no_rain_count += int(np.count_nonzero(no_rain))

    dual_pol_available = any(
        np.any(np.isfinite(sweep.p_meteo_dual_pol)) for sweep in sweeps
    )
    vertical_available = any(
        np.any(np.isfinite(sweep.p_vertical_consistency)) for sweep in sweeps
    )
    modules = _module_records(
        profile,
        clutter_available=clutter_available,
        sea_ap_available=sea_ap_available,
        dual_pol_available=dual_pol_available,
        vertical_available=vertical_available,
        radial_ray_count=radial_ray_count,
        type_ray_counts=type_ray_counts,
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
        "radial_interference_gate_count": radial_gate_count,
        "radial_interference_area_km2": round(radial_area_km2, 6),
        "interference_type_ray_counts": type_ray_counts,
        "interference_type_gate_counts": type_gate_counts,
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


def audit_long_range_saturated_radials(
    normalized_objects: dict[str, bytes],
    profile: BasicQCProfile,
) -> dict[str, Any]:
    """Read normalized polar DBZH and report only the RP-040 saturation signature."""
    store = MemoryStore()
    store.update(normalized_objects)
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise QCInputError("radial audit input is not a NormalizedRadarVolume")

    sweep_results: list[dict[str, Any]] = []
    total = 0
    lower, upper = profile.echo.dbzh_valid_range_dbz
    for sweep_number in root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        group = root[name]
        rays: list[dict[str, Any]] = []
        if "DBZH" in group:
            dbzh = group["DBZH"][:].astype("float32", copy=False)
            valid = np.isfinite(dbzh) & (dbzh >= lower) & (dbzh <= upper)
            azimuth = group["azimuth"][:] if "azimuth" in group else None
            for ray_index in range(dbzh.shape[0]):
                evidence = _long_range_saturated_radial_evidence(
                    dbzh[ray_index], valid[ray_index]
                )
                if evidence is None:
                    continue
                rays.append(
                    {
                        "ray_index": ray_index,
                        "azimuth_deg": (
                            float(azimuth[ray_index]) if azimuth is not None else None
                        ),
                        **evidence.value(),
                    }
                )
        total += len(rays)
        sweep_results.append(
            {
                "sweep": name,
                "saturated_ray_count": len(rays),
                "rays": rays,
            }
        )

    return {
        "signature_version": _SATURATED_RADIAL_SIGNATURE_VERSION,
        "qc_profile": profile.profile_version,
        "qc_pipeline_version": profile.pipeline_version,
        "criteria": {
            "minimum_valid_gate_fraction": _SATURATED_RADIAL_MINIMUM_VALID_FRACTION,
            "minimum_high_dbzh": _SATURATED_RADIAL_MINIMUM_HIGH_DBZH,
            "minimum_high_gate_fraction": _SATURATED_RADIAL_MINIMUM_HIGH_FRACTION,
            "minimum_high_run": _SATURATED_RADIAL_MINIMUM_HIGH_RUN,
            "minimum_range_growth_db": _SATURATED_RADIAL_MINIMUM_RANGE_GROWTH_DB,
        },
        "saturated_ray_count": total,
        "sweeps": sweep_results,
    }


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


def _dual_pol_meteorological_probability(
    dbzh: np.ndarray,
    valid: np.ndarray,
    no_rain: np.ndarray,
    *,
    snr: np.ndarray | None,
    rhohv: np.ndarray | None,
    zdr: np.ndarray | None,
    phidp: np.ndarray | None,
    config: DualPolFuzzyConfig,
    echo: EchoConfig,
) -> np.ndarray:
    probability = np.full(dbzh.shape, np.nan, dtype="float32")
    if not config.enabled:
        return probability
    weighted = np.zeros(dbzh.shape, dtype="float32")
    weight_sum = np.zeros(dbzh.shape, dtype="float32")

    def add(values: np.ndarray | None, membership: np.ndarray, name: str) -> None:
        if values is None:
            return
        weight = config.weights[name]
        available = valid & np.isfinite(values) & np.isfinite(membership)
        weighted[available] += membership[available] * weight
        weight_sum[available] += weight

    if rhohv is not None:
        add(
            rhohv,
            _rising_membership(rhohv, echo.rhohv_meteo_range),
            "rhohv",
        )
    if snr is not None:
        add(snr, _rising_membership(snr, echo.snr_meteo_range_db), "snr")
    if zdr is not None:
        add(
            zdr,
            _trapezoid_membership(
                zdr,
                config.zdr_plausible_range_db,
                config.zdr_transition_db,
            ),
            "zdr",
        )
    if phidp is not None:
        step = _minimum_circular_neighbour_step(phidp, period=360.0)
        lower, upper = config.phidp_step_range_deg
        smoothness = 1.0 - np.clip((step - lower) / (upper - lower), 0.0, 1.0)
        add(phidp, smoothness.astype("float32"), "phidp")

    available = weight_sum > 0
    probability[available] = weighted[available] / weight_sum[available]
    probability[no_rain] = 1.0
    probability[~valid] = np.nan
    return probability


def _volume_vertical_consistency(
    root: zarr.Group,
    profile: BasicQCProfile,
) -> dict[str, np.ndarray]:
    if not profile.vertical_consistency.enabled:
        return {}
    inputs: list[dict[str, Any]] = []
    names: list[str] = []
    for sweep_number in root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        group = root[name]
        shape = (len(group["azimuth"]), len(group["range"]))
        dbzh = (
            group["DBZH"][:].astype("float32", copy=False)
            if "DBZH" in group
            else np.full(shape, np.nan, dtype="float32")
        )
        inputs.append(
            {
                "dbzh": dbzh,
                "azimuth": group["azimuth"][:],
                "range": group["range"][:],
                "elevation": float(np.nanmedian(group["elevation"][:])),
            }
        )
        names.append(name)
    values = _vertical_consistency_probabilities(
        tuple(inputs),
        minimum_dbzh=profile.vertical_consistency.minimum_dbzh,
        support_tolerance_db=profile.vertical_consistency.support_tolerance_db,
        maximum_range_m=profile.vertical_consistency.maximum_range_m,
    )
    return dict(zip(names, values, strict=True))


def _vertical_consistency_probabilities(
    sweeps: tuple[dict[str, Any], ...],
    *,
    minimum_dbzh: float,
    support_tolerance_db: float,
    maximum_range_m: float,
) -> tuple[np.ndarray, ...]:
    results = [
        np.full(np.asarray(sweep["dbzh"]).shape, np.nan, dtype="float32")
        for sweep in sweeps
    ]
    for low_index, low in enumerate(sweeps):
        higher = [
            (index, item)
            for index, item in enumerate(sweeps)
            if float(item["elevation"]) > float(low["elevation"]) + 0.2
        ]
        if not higher:
            continue
        _, high = min(higher, key=lambda item: float(item[1]["elevation"]))
        low_dbzh = np.asarray(low["dbzh"], dtype="float32")
        high_dbzh = np.asarray(high["dbzh"], dtype="float32")
        if low_dbzh.ndim != 2 or high_dbzh.ndim != 2:
            raise QCInputError("vertical consistency expects two-dimensional sweeps")
        ray_index = _nearest_azimuth_indices(
            np.asarray(low["azimuth"], dtype="float64"),
            np.asarray(high["azimuth"], dtype="float64"),
        )
        gate_index = _nearest_coordinate_indices(
            np.asarray(low["range"], dtype="float64"),
            np.asarray(high["range"], dtype="float64"),
        )
        matched = high_dbzh[ray_index[:, None], gate_index[None, :]]
        eligible = (
            np.isfinite(low_dbzh)
            & (low_dbzh >= minimum_dbzh)
            & (np.asarray(low["range"])[None, :] <= maximum_range_m)
        )
        difference = np.maximum(low_dbzh - matched, 0.0)
        consistency = 1.0 - np.clip(difference / support_tolerance_db, 0.0, 1.0)
        consistency[eligible & ~np.isfinite(matched)] = 0.0
        results[low_index][eligible] = consistency[eligible]
    return tuple(results)


def _radial_probability(
    dbzh: np.ndarray,
    valid: np.ndarray,
    config: RadialInterferenceConfig,
) -> tuple[np.ndarray, int]:
    detection = _detect_radial_interference(dbzh, valid, config)
    return detection.probability, detection.flagged_ray_count


def _detect_radial_interference(
    dbzh: np.ndarray,
    valid: np.ndarray,
    config: RadialInterferenceConfig,
    *,
    ranges_m: np.ndarray | None = None,
    vertical_consistency: np.ndarray | None = None,
) -> RadialInterferenceDetection:
    probabilities = np.zeros(dbzh.shape, dtype="float32")
    probabilities[~valid] = np.nan
    interference_type = np.zeros(dbzh.shape, dtype="uint8")
    type_ray_counts = {name: 0 for name in INTERFERENCE_TYPE_CODES if name != "none"}
    type_gate_counts = {name: 0 for name in INTERFERENCE_TYPE_CODES if name != "none"}
    if dbzh.shape[0] < 2:
        return RadialInterferenceDetection(
            probabilities,
            interference_type,
            0,
            type_ray_counts,
            type_gate_counts,
        )
    ranges = (
        np.asarray(ranges_m, dtype="float64")
        if ranges_m is not None
        else np.arange(dbzh.shape[1], dtype="float64")
    )
    if ranges.shape != (dbzh.shape[1],):
        raise QCInputError("radial interference range coordinate differs from gate shape")
    if vertical_consistency is not None and vertical_consistency.shape != dbzh.shape:
        raise QCInputError("vertical consistency differs from radial gate shape")

    for ray_index in range(dbzh.shape[0]):
        ray_valid = valid[ray_index]
        if _is_long_range_saturated_radial(dbzh[ray_index], ray_valid):
            _record_radial_type(
                probabilities,
                interference_type,
                ray_index,
                ray_valid,
                "broad",
                config.flag_probability,
            )
            continue
        baseline = _neighbour_baseline(dbzh, ray_index)
        overlap = ray_valid & np.isfinite(baseline)
        legacy_detected = (
            np.mean(ray_valid) >= config.minimum_valid_gate_fraction
            and _longest_run(ray_valid) >= config.minimum_consecutive_gates
            and np.any(overlap)
            and float(
                np.median(np.abs(dbzh[ray_index, overlap] - baseline[overlap]))
            )
            >= config.neighbour_difference_db
        )
        if legacy_detected and not config.morphology.enabled:
            _record_radial_type(
                probabilities,
                interference_type,
                ray_index,
                ray_valid,
                "narrow",
                config.flag_probability,
            )
            continue
        if not config.morphology.enabled or not np.any(overlap):
            if legacy_detected:
                _record_radial_type(
                    probabilities,
                    interference_type,
                    ray_index,
                    ray_valid,
                    "narrow",
                    config.flag_probability,
                )
            continue
        candidate = overlap & (
            dbzh[ray_index] - baseline >= config.morphology.candidate_difference_db
        )
        segments = _true_segments(candidate, config.morphology.minimum_segment_gates)
        if not segments:
            if legacy_detected:
                _record_radial_type(
                    probabilities,
                    interference_type,
                    ray_index,
                    ray_valid,
                    "narrow",
                    config.flag_probability,
                )
            continue
        evidence = np.zeros(candidate.shape, dtype=bool)
        for start, end in segments:
            evidence[start:end] = True
        drop = _near_to_far_drop(dbzh[ray_index], ray_valid)
        if (
            len(segments) == 1
            and np.count_nonzero(evidence) >= 0.5 * np.count_nonzero(ray_valid)
            and drop >= config.morphology.reverse_minimum_drop_db
        ):
            type_name = "reverse"
        elif len(segments) >= config.morphology.intermittent_minimum_segments:
            type_name = "intermittent"
        elif ranges[max(end for _, end in segments) - 1] <= config.morphology.short_range_max_m:
            type_name = "short_range"
        else:
            type_name = "narrow"
        probability = (
            config.flag_probability
            if legacy_detected
            else config.morphology.diagnostic_probability
        )
        if vertical_consistency is not None:
            support = vertical_consistency[ray_index, evidence]
            finite_support = support[np.isfinite(support)]
            if finite_support.size and float(np.median(finite_support)) <= 0.2:
                probability = config.flag_probability
        _record_radial_type(
            probabilities,
            interference_type,
            ray_index,
            ray_valid if legacy_detected else evidence,
            type_name,
            probability,
        )

    flagged_ray_count = int(
        np.count_nonzero(
            np.any(
                np.nan_to_num(probabilities, nan=0.0) >= config.flag_probability,
                axis=1,
            )
        )
    )
    for type_name, code in INTERFERENCE_TYPE_CODES.items():
        if type_name == "none":
            continue
        typed = interference_type == code
        type_ray_counts[type_name] = int(np.count_nonzero(np.any(typed, axis=1)))
        type_gate_counts[type_name] = int(np.count_nonzero(typed))
    return RadialInterferenceDetection(
        probabilities,
        interference_type,
        flagged_ray_count,
        type_ray_counts,
        type_gate_counts,
    )


def _record_radial_type(
    probabilities: np.ndarray,
    interference_type: np.ndarray,
    ray_index: int,
    mask: np.ndarray,
    type_name: str,
    probability: float,
) -> None:
    replace = mask & (
        np.nan_to_num(probabilities[ray_index], nan=-1.0) < probability
    )
    probabilities[ray_index, replace] = probability
    interference_type[ray_index, replace] = INTERFERENCE_TYPE_CODES[type_name]


def _neighbour_baseline(dbzh: np.ndarray, ray_index: int) -> np.ndarray:
    following = (ray_index + 1) % dbzh.shape[0]
    if dbzh.shape[0] == 2:
        return dbzh[following]
    previous = (ray_index - 1) % dbzh.shape[0]
    previous_values = dbzh[previous]
    next_values = dbzh[following]
    previous_finite = np.isfinite(previous_values)
    next_finite = np.isfinite(next_values)
    baseline = np.full(dbzh.shape[1], np.nan, dtype="float32")
    both = previous_finite & next_finite
    baseline[both] = (previous_values[both] + next_values[both]) / 2.0
    baseline[previous_finite & ~next_finite] = previous_values[
        previous_finite & ~next_finite
    ]
    baseline[next_finite & ~previous_finite] = next_values[
        next_finite & ~previous_finite
    ]
    return baseline


def _true_segments(values: np.ndarray, minimum_length: int) -> list[tuple[int, int]]:
    padded = np.concatenate(([False], np.asarray(values, dtype=bool), [False]))
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    return [
        (int(start), int(end))
        for start, end in zip(changes[::2], changes[1::2], strict=True)
        if end - start >= minimum_length
    ]


def _near_to_far_drop(values: np.ndarray, valid: np.ndarray) -> float:
    quartile = max(values.size // 4, 1)
    near = values[:quartile][valid[:quartile]]
    far = values[-quartile:][valid[-quartile:]]
    if near.size == 0 or far.size == 0:
        return 0.0
    return float(np.median(near) - np.median(far))


def _rising_membership(values: np.ndarray, bounds: tuple[float, float]) -> np.ndarray:
    return np.clip((values - bounds[0]) / (bounds[1] - bounds[0]), 0.0, 1.0).astype(
        "float32"
    )


def _trapezoid_membership(
    values: np.ndarray,
    plateau: tuple[float, float],
    transition: float,
) -> np.ndarray:
    lower, upper = plateau
    rising = np.clip((values - (lower - transition)) / transition, 0.0, 1.0)
    falling = np.clip(((upper + transition) - values) / transition, 0.0, 1.0)
    return np.minimum(rising, falling).astype("float32")


def _minimum_circular_neighbour_step(values: np.ndarray, *, period: float) -> np.ndarray:
    current = np.asarray(values, dtype="float32")
    previous = np.roll(current, 1, axis=1)
    following = np.roll(current, -1, axis=1)
    before = np.abs((current - previous + period / 2.0) % period - period / 2.0)
    after = np.abs((following - current + period / 2.0) % period - period / 2.0)
    before[:, 0] = np.nan
    after[:, -1] = np.nan
    return np.fmin(before, after).astype("float32")


def _nearest_azimuth_indices(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    if target.size == 0:
        raise QCInputError("vertical comparison target has no azimuths")
    difference = np.abs(
        (source[:, None] - target[None, :] + 180.0) % 360.0 - 180.0
    )
    return np.argmin(difference, axis=1)


def _nearest_coordinate_indices(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    if target.size == 0:
        raise QCInputError("vertical comparison target has no range gates")
    insertion = np.searchsorted(target, source)
    following = np.clip(insertion, 0, target.size - 1)
    previous = np.clip(insertion - 1, 0, target.size - 1)
    use_previous = np.abs(source - target[previous]) <= np.abs(source - target[following])
    return np.where(use_previous, previous, following)


def _is_long_range_saturated_radial(dbzh: np.ndarray, valid: np.ndarray) -> bool:
    """Detect a range-growing, nearly full-ray interference signature."""
    return _long_range_saturated_radial_evidence(dbzh, valid) is not None


def _long_range_saturated_radial_evidence(
    dbzh: np.ndarray,
    valid: np.ndarray,
) -> SaturatedRadialEvidence | None:
    if np.mean(valid) < _SATURATED_RADIAL_MINIMUM_VALID_FRACTION:
        return None
    high = valid & (dbzh >= _SATURATED_RADIAL_MINIMUM_HIGH_DBZH)
    valid_count = int(np.count_nonzero(valid))
    high_fraction = float(np.count_nonzero(high) / valid_count)
    if high_fraction < _SATURATED_RADIAL_MINIMUM_HIGH_FRACTION:
        return None
    high_run = _longest_run(high)
    if high_run < _SATURATED_RADIAL_MINIMUM_HIGH_RUN:
        return None

    gate_count = dbzh.shape[0]
    quartile = max(gate_count // 4, 1)
    near_values = dbzh[:quartile][valid[:quartile]]
    far_values = dbzh[-quartile:][valid[-quartile:]]
    if near_values.size == 0 or far_values.size == 0:
        return None
    range_growth = float(np.median(far_values) - np.median(near_values))
    if range_growth < _SATURATED_RADIAL_MINIMUM_RANGE_GROWTH_DB:
        return None
    return SaturatedRadialEvidence(
        high_gate_fraction=high_fraction,
        longest_high_run=high_run,
        range_growth_db=range_growth,
        peak_dbzh=float(np.max(dbzh[valid])),
    )


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
    dual_pol_available: bool,
    vertical_available: bool,
    radial_ray_count: int,
    type_ray_counts: dict[str, int],
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
            "dual_pol_fuzzy",
            profile.pipeline_version,
            "applied" if dual_pol_available else "skipped",
            ("DBZH", "RHOHV", "ZDR", "PHIDP", "SNR"),
            ("P_METEO_DUAL_POL",),
            (
                None
                if dual_pol_available
                else "dual_pol_fuzzy_disabled_or_fields_unavailable"
            ),
            {"diagnostic_only": float(profile.dual_pol_fuzzy.mode == "diagnostic_only")},
        ),
        QCModuleRecord(
            "vertical_consistency",
            profile.pipeline_version,
            "applied" if vertical_available else "skipped",
            ("DBZH", "azimuth", "range", "elevation"),
            ("P_VERTICAL_CONSISTENCY",),
            (
                None
                if vertical_available
                else "vertical_consistency_disabled_or_higher_sweep_unavailable"
            ),
            {
                "diagnostic_only": float(
                    profile.vertical_consistency.mode == "diagnostic_only"
                )
            },
        ),
        QCModuleRecord(
            "radial_interference",
            profile.pipeline_version,
            "applied",
            ("DBZH",),
            (
                "P_RADIAL_INTERFERENCE",
                "INTERFERENCE_TYPE",
                "QC_FLAGS",
                "QI_INTERFERENCE",
            ),
            None,
            {
                "flagged_ray_count": float(radial_ray_count),
                **{
                    f"{name}_ray_count": float(count)
                    for name, count in type_ray_counts.items()
                },
                "morphology_diagnostic_only": float(
                    profile.radial_interference.morphology.mode == "diagnostic_only"
                ),
            },
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
    morphology = profile.radial_interference.morphology
    if morphology.diagnostic_probability > profile.radial_interference.flag_probability:
        raise QCConfigError("radial diagnostic probability must not exceed flag probability")
    if not 0 <= morphology.diagnostic_probability <= 1:
        raise QCConfigError("invalid radial diagnostic probability")
    if profile.dual_pol_fuzzy.enabled:
        required_weights = {"rhohv", "snr", "zdr", "phidp"}
        if set(profile.dual_pol_fuzzy.weights) != required_weights:
            raise QCConfigError("dual-pol fuzzy weights are incomplete")
        if sum(profile.dual_pol_fuzzy.weights.values()) <= 0:
            raise QCConfigError("dual-pol fuzzy weights must contain positive evidence")
        if profile.dual_pol_fuzzy.zdr_transition_db <= 0:
            raise QCConfigError("dual-pol ZDR transition must be positive")
    if (
        profile.vertical_consistency.support_tolerance_db <= 0
        or profile.vertical_consistency.maximum_range_m <= 0
    ):
        raise QCConfigError("vertical consistency limits must be positive")
    if profile.static_ground_clutter.asset_uri and not profile.static_ground_clutter.asset_version:
        raise QCConfigError("configured clutter asset requires an asset version")
    if profile.sea_ap.coastline_asset_uri and not profile.sea_ap.asset_version:
        raise QCConfigError("configured coastline asset requires an asset version")
