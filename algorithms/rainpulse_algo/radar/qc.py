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
class RadialFanClosureConfig:
    enabled: bool
    extent_gap_enabled: bool
    maximum_gap_rays: int
    minimum_valid_gate_fraction: float
    minimum_high_dbzh: float
    minimum_high_gate_fraction: float
    minimum_high_run: int
    minimum_range_growth_db: float
    minimum_gap_valid_gate_fraction: float
    minimum_gap_consecutive_gates: int
    minimum_gap_range_extent_fraction: float
    minimum_gap_boundary_extent_ratio: float
    minimum_seed_fraction: float


@dataclass(frozen=True)
class RadialExtentPromotionConfig:
    enabled: bool
    minimum_valid_gate_fraction: float
    diagnostic_minimum_valid_gate_fraction: float
    minimum_consecutive_gates: int
    minimum_range_extent_fraction: float
    minimum_range_growth_db: float
    minimum_analysis_range_m: float
    maximum_power_iqr_db: float
    minimum_group_rays: int
    maximum_group_rays: int
    maximum_group_gap_rays: int
    maximum_hard_seed_distance_rays: int
    maximum_higher_elevation_extent_fraction: float


@dataclass(frozen=True)
class RadialContextFusionConfig:
    enabled: bool
    minimum_independent_evidence: int
    minimum_temporal_context_scans: int
    maximum_temporal_context_scans: int
    temporal_persistence_threshold: float
    cross_radar_max_time_offset_seconds: int
    cross_radar_echo_threshold_dbzh: float
    minimum_cross_radar_overlap_gates: int
    cross_radar_promotion_max_consistency: float
    cross_radar_veto_min_consistency: float


@dataclass(frozen=True)
class RadialMultiscalePromotionConfig:
    enabled: bool
    echo_threshold_dbzh: float
    short_window_rays: int
    long_window_rays: int
    minimum_score_gate_fraction: float
    minimum_edge_jump_gate_fraction: float
    minimum_diagnostic_gates: int
    minimum_high_dbzh: float
    minimum_high_gates: int
    minimum_range_growth_db: float


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
    fan_closure: RadialFanClosureConfig
    radial_extent_promotion: RadialExtentPromotionConfig
    context_fusion: RadialContextFusionConfig
    multiscale_promotion: RadialMultiscalePromotionConfig


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
    weak_candidate_ray_count: int
    context_promoted_ray_count: int
    cross_radar_vetoed_ray_count: int


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
    fan_closure = morphology.get("fan_closure") or {}
    radial_extent = morphology.get("radial_extent_promotion") or {}
    context_fusion = morphology.get("context_fusion") or {}
    multiscale = morphology.get("multiscale_promotion") or {}
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
            zdr_plausible_range_db=_pair(dual_pol.get("zdr_plausible_range_db", [-2.0, 5.0])),
            zdr_transition_db=float(dual_pol.get("zdr_transition_db", 2.0)),
            phidp_step_range_deg=_pair(dual_pol.get("phidp_step_range_deg", [3.0, 30.0])),
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
                    morphology.get("candidate_difference_db", radial["neighbour_difference_db"])
                ),
                minimum_segment_gates=int(morphology.get("minimum_segment_gates", 12)),
                intermittent_minimum_segments=int(
                    morphology.get("intermittent_minimum_segments", 3)
                ),
                short_range_max_m=float(morphology.get("short_range_max_m", 60_000.0)),
                reverse_minimum_drop_db=float(morphology.get("reverse_minimum_drop_db", 12.0)),
                diagnostic_probability=float(morphology.get("diagnostic_probability", 0.65)),
                fan_closure=RadialFanClosureConfig(
                    enabled=bool(fan_closure.get("enabled", False)),
                    extent_gap_enabled=bool(fan_closure.get("extent_gap_enabled", False)),
                    maximum_gap_rays=int(fan_closure.get("maximum_gap_rays", 2)),
                    minimum_valid_gate_fraction=float(
                        fan_closure.get("minimum_valid_gate_fraction", 0.90)
                    ),
                    minimum_high_dbzh=float(fan_closure.get("minimum_high_dbzh", 45.0)),
                    minimum_high_gate_fraction=float(
                        fan_closure.get("minimum_high_gate_fraction", 0.50)
                    ),
                    minimum_high_run=int(fan_closure.get("minimum_high_run", 120)),
                    minimum_range_growth_db=float(fan_closure.get("minimum_range_growth_db", 20.0)),
                    minimum_gap_valid_gate_fraction=float(
                        fan_closure.get(
                            "minimum_gap_valid_gate_fraction",
                            fan_closure.get("minimum_valid_gate_fraction", 0.90),
                        )
                    ),
                    minimum_gap_consecutive_gates=int(
                        fan_closure.get("minimum_gap_consecutive_gates", 400)
                    ),
                    minimum_gap_range_extent_fraction=float(
                        fan_closure.get("minimum_gap_range_extent_fraction", 0.95)
                    ),
                    minimum_gap_boundary_extent_ratio=float(
                        fan_closure.get("minimum_gap_boundary_extent_ratio", 1.0)
                    ),
                    minimum_seed_fraction=float(fan_closure.get("minimum_seed_fraction", 0.30)),
                ),
                radial_extent_promotion=RadialExtentPromotionConfig(
                    enabled=bool(radial_extent.get("enabled", False)),
                    minimum_valid_gate_fraction=float(
                        radial_extent.get("minimum_valid_gate_fraction", 0.55)
                    ),
                    diagnostic_minimum_valid_gate_fraction=float(
                        radial_extent.get(
                            "diagnostic_minimum_valid_gate_fraction",
                            radial_extent.get("minimum_valid_gate_fraction", 0.55),
                        )
                    ),
                    minimum_consecutive_gates=int(
                        radial_extent.get("minimum_consecutive_gates", 350)
                    ),
                    minimum_range_extent_fraction=float(
                        radial_extent.get("minimum_range_extent_fraction", 0.95)
                    ),
                    minimum_range_growth_db=float(
                        radial_extent.get("minimum_range_growth_db", 12.0)
                    ),
                    minimum_analysis_range_m=float(
                        radial_extent.get("minimum_analysis_range_m", 50_000.0)
                    ),
                    maximum_power_iqr_db=float(radial_extent.get("maximum_power_iqr_db", 5.0)),
                    minimum_group_rays=int(radial_extent.get("minimum_group_rays", 2)),
                    maximum_group_rays=int(radial_extent.get("maximum_group_rays", 8)),
                    maximum_group_gap_rays=int(radial_extent.get("maximum_group_gap_rays", 0)),
                    maximum_hard_seed_distance_rays=int(
                        radial_extent.get("maximum_hard_seed_distance_rays", 8)
                    ),
                    maximum_higher_elevation_extent_fraction=float(
                        radial_extent.get("maximum_higher_elevation_extent_fraction", 0.40)
                    ),
                ),
                context_fusion=RadialContextFusionConfig(
                    enabled=bool(context_fusion.get("enabled", False)),
                    minimum_independent_evidence=int(
                        context_fusion.get("minimum_independent_evidence", 2)
                    ),
                    minimum_temporal_context_scans=int(
                        context_fusion.get("minimum_temporal_context_scans", 2)
                    ),
                    maximum_temporal_context_scans=int(
                        context_fusion.get("maximum_temporal_context_scans", 3)
                    ),
                    temporal_persistence_threshold=float(
                        context_fusion.get("temporal_persistence_threshold", 0.66)
                    ),
                    cross_radar_max_time_offset_seconds=int(
                        context_fusion.get("cross_radar_max_time_offset_seconds", 300)
                    ),
                    cross_radar_echo_threshold_dbzh=float(
                        context_fusion.get("cross_radar_echo_threshold_dbzh", 10.0)
                    ),
                    minimum_cross_radar_overlap_gates=int(
                        context_fusion.get("minimum_cross_radar_overlap_gates", 80)
                    ),
                    cross_radar_promotion_max_consistency=float(
                        context_fusion.get("cross_radar_promotion_max_consistency", 0.20)
                    ),
                    cross_radar_veto_min_consistency=float(
                        context_fusion.get("cross_radar_veto_min_consistency", 0.70)
                    ),
                ),
                multiscale_promotion=RadialMultiscalePromotionConfig(
                    enabled=bool(multiscale.get("enabled", False)),
                    echo_threshold_dbzh=float(multiscale.get("echo_threshold_dbzh", 30.0)),
                    short_window_rays=int(multiscale.get("short_window_rays", 4)),
                    long_window_rays=int(multiscale.get("long_window_rays", 40)),
                    minimum_score_gate_fraction=float(
                        multiscale.get("minimum_score_gate_fraction", 0.02)
                    ),
                    minimum_edge_jump_gate_fraction=float(
                        multiscale.get("minimum_edge_jump_gate_fraction", 0.10)
                    ),
                    minimum_diagnostic_gates=int(multiscale.get("minimum_diagnostic_gates", 100)),
                    minimum_high_dbzh=float(multiscale.get("minimum_high_dbzh", 45.0)),
                    minimum_high_gates=int(multiscale.get("minimum_high_gates", 100)),
                    minimum_range_growth_db=float(multiscale.get("minimum_range_growth_db", 12.0)),
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
    radial_context: dict[str, dict[str, np.ndarray]] | None = None,
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
    higher_elevation_extents = _volume_higher_elevation_radial_extents(root, profile)
    sweeps: list[QCSweep] = []
    radial_ray_count = 0
    radial_gate_count = 0
    radial_area_km2 = 0.0
    radial_weak_candidate_ray_count = 0
    radial_context_promoted_ray_count = 0
    radial_cross_radar_vetoed_ray_count = 0
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
        p_vertical = vertical_probabilities.get(name, np.full(dbzh.shape, np.nan, dtype="float32"))
        sweep_radial_context = (radial_context or {}).get(name, {})
        detection = _detect_radial_interference(
            dbzh,
            valid,
            profile.radial_interference,
            ranges_m=ranges,
            vertical_consistency=(
                p_vertical if profile.vertical_consistency.mode == "radial_evidence" else None
            ),
            higher_elevation_extent_fraction=higher_elevation_extents.get(name),
            temporal_persistence=sweep_radial_context.get("temporal_persistence"),
            cross_radar_consistency=sweep_radial_context.get("cross_radar_consistency"),
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
        radial_weak_candidate_ray_count += detection.weak_candidate_ray_count
        radial_context_promoted_ray_count += detection.context_promoted_ray_count
        radial_cross_radar_vetoed_ray_count += detection.cross_radar_vetoed_ray_count
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
            | (operational_radial >= profile.radial_interference.low_quality_probability)
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

    dual_pol_available = any(np.any(np.isfinite(sweep.p_meteo_dual_pol)) for sweep in sweeps)
    vertical_available = any(np.any(np.isfinite(sweep.p_vertical_consistency)) for sweep in sweeps)
    modules = _module_records(
        profile,
        clutter_available=clutter_available,
        sea_ap_available=sea_ap_available,
        dual_pol_available=dual_pol_available,
        vertical_available=vertical_available,
        radial_ray_count=radial_ray_count,
        radial_weak_candidate_ray_count=radial_weak_candidate_ray_count,
        radial_context_promoted_ray_count=radial_context_promoted_ray_count,
        radial_cross_radar_vetoed_ray_count=radial_cross_radar_vetoed_ray_count,
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
        "radial_weak_candidate_ray_count": radial_weak_candidate_ray_count,
        "radial_context_promoted_ray_count": radial_context_promoted_ray_count,
        "radial_cross_radar_vetoed_ray_count": radial_cross_radar_vetoed_ray_count,
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
                evidence = _long_range_saturated_radial_evidence(dbzh[ray_index], valid[ray_index])
                if evidence is None:
                    continue
                rays.append(
                    {
                        "ray_index": ray_index,
                        "azimuth_deg": (float(azimuth[ray_index]) if azimuth is not None else None),
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
        np.full(np.asarray(sweep["dbzh"]).shape, np.nan, dtype="float32") for sweep in sweeps
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


def _volume_higher_elevation_radial_extents(
    root: zarr.Group,
    profile: BasicQCProfile,
) -> dict[str, np.ndarray]:
    if not profile.radial_interference.morphology.radial_extent_promotion.enabled:
        return {}
    inputs: list[dict[str, Any]] = []
    names: list[str] = []
    for sweep_number in root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        group = root[name]
        if "DBZH" not in group:
            continue
        inputs.append(
            {
                "dbzh": group["DBZH"][:].astype("float32", copy=False),
                "azimuth": group["azimuth"][:],
                "range": group["range"][:],
                "elevation": float(np.nanmedian(group["elevation"][:])),
            }
        )
        names.append(name)
    values = _higher_elevation_radial_extent_fractions(tuple(inputs))
    return dict(zip(names, values, strict=True))


def _higher_elevation_radial_extent_fractions(
    sweeps: tuple[dict[str, Any], ...],
) -> tuple[np.ndarray, ...]:
    """Map the next reflectivity elevation's radial echo extent to each ray."""
    results = [
        np.full(np.asarray(sweep["dbzh"]).shape[0], np.nan, dtype="float32") for sweep in sweeps
    ]
    for low_index, low in enumerate(sweeps):
        higher = [
            item for item in sweeps if float(item["elevation"]) > float(low["elevation"]) + 0.2
        ]
        if not higher:
            continue
        high = min(higher, key=lambda item: float(item["elevation"]))
        high_extent = _radial_range_extent_fractions(
            np.isfinite(np.asarray(high["dbzh"])),
            np.asarray(high["range"], dtype="float64"),
        )
        ray_index = _nearest_azimuth_indices(
            np.asarray(low["azimuth"], dtype="float64"),
            np.asarray(high["azimuth"], dtype="float64"),
        )
        results[low_index] = high_extent[ray_index].astype("float32")
    return tuple(results)


def _radial_probability(
    dbzh: np.ndarray,
    valid: np.ndarray,
    config: RadialInterferenceConfig,
) -> tuple[np.ndarray, int]:
    detection = _detect_radial_interference(dbzh, valid, config)
    return detection.probability, detection.flagged_ray_count


def _validated_optional_ray_evidence(
    values: np.ndarray | None,
    ray_count: int,
    name: str,
) -> np.ndarray | None:
    if values is None:
        return None
    result = np.asarray(values, dtype="float32")
    if result.shape != (ray_count,):
        raise QCInputError(f"{name} differs from radial ray shape")
    finite = np.isfinite(result)
    if np.any(finite & ((result < 0.0) | (result > 1.0))):
        raise QCInputError(f"{name} is outside [0, 1]")
    return result


def _detect_radial_interference(
    dbzh: np.ndarray,
    valid: np.ndarray,
    config: RadialInterferenceConfig,
    *,
    ranges_m: np.ndarray | None = None,
    vertical_consistency: np.ndarray | None = None,
    higher_elevation_extent_fraction: np.ndarray | None = None,
    temporal_persistence: np.ndarray | None = None,
    cross_radar_consistency: np.ndarray | None = None,
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
            0,
            0,
            0,
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
    if higher_elevation_extent_fraction is not None and higher_elevation_extent_fraction.shape != (
        dbzh.shape[0],
    ):
        raise QCInputError("higher-elevation extent differs from radial ray shape")
    temporal_persistence = _validated_optional_ray_evidence(
        temporal_persistence,
        dbzh.shape[0],
        "temporal persistence",
    )
    cross_radar_consistency = _validated_optional_ray_evidence(
        cross_radar_consistency,
        dbzh.shape[0],
        "cross-radar consistency",
    )
    weak_candidate_rays = np.zeros(dbzh.shape[0], dtype=bool)
    context_promoted_rays = np.zeros(dbzh.shape[0], dtype=bool)
    cross_radar_vetoed_rays = np.zeros(dbzh.shape[0], dtype=bool)

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
            and float(np.median(np.abs(dbzh[ray_index, overlap] - baseline[overlap])))
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
        probability = config.flag_probability
        vertical_evidence = False
        if vertical_consistency is not None:
            support = vertical_consistency[ray_index, evidence]
            finite_support = support[np.isfinite(support)]
            if finite_support.size and float(np.median(finite_support)) <= 0.2:
                vertical_evidence = True
        if not legacy_detected:
            weak_candidate_rays[ray_index] = True
            probability = config.morphology.diagnostic_probability
            if config.morphology.context_fusion.enabled:
                context = config.morphology.context_fusion
                evidence_count = 1 + int(vertical_evidence)
                if (
                    temporal_persistence is not None
                    and np.isfinite(temporal_persistence[ray_index])
                    and temporal_persistence[ray_index] >= context.temporal_persistence_threshold
                ):
                    evidence_count += 1
                consistency = (
                    float(cross_radar_consistency[ray_index])
                    if cross_radar_consistency is not None
                    else np.nan
                )
                cross_radar_veto = (
                    np.isfinite(consistency)
                    and consistency >= context.cross_radar_veto_min_consistency
                )
                if cross_radar_veto:
                    cross_radar_vetoed_rays[ray_index] = True
                else:
                    if (
                        np.isfinite(consistency)
                        and consistency <= context.cross_radar_promotion_max_consistency
                    ):
                        evidence_count += 1
                    if evidence_count >= context.minimum_independent_evidence:
                        probability = config.flag_probability
                        context_promoted_rays[ray_index] = True
            elif vertical_evidence:
                probability = config.flag_probability
        _record_radial_type(
            probabilities,
            interference_type,
            ray_index,
            ray_valid if legacy_detected else evidence,
            type_name,
            probability,
        )

    if config.morphology.radial_extent_promotion.enabled:
        (
            extent_candidates,
            extent_promoted,
            extent_vetoed,
        ) = _promote_radial_extent_evidence(
            dbzh,
            valid,
            ranges,
            probabilities,
            interference_type,
            config,
            higher_elevation_extent_fraction,
            temporal_persistence,
            cross_radar_consistency,
        )
        weak_candidate_rays |= extent_candidates
        context_promoted_rays |= extent_promoted
        cross_radar_vetoed_rays |= extent_vetoed
    if config.morphology.fan_closure.enabled:
        _close_seeded_radial_fans(
            dbzh,
            valid,
            ranges,
            probabilities,
            interference_type,
            config,
        )
    if config.morphology.multiscale_promotion.enabled:
        _promote_multiscale_radial_evidence(
            dbzh,
            valid,
            probabilities,
            interference_type,
            config,
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
        int(np.count_nonzero(weak_candidate_rays)),
        int(np.count_nonzero(context_promoted_rays)),
        int(np.count_nonzero(cross_radar_vetoed_rays)),
    )


def _close_seeded_radial_fans(
    dbzh: np.ndarray,
    valid: np.ndarray,
    ranges_m: np.ndarray,
    probabilities: np.ndarray,
    interference_type: np.ndarray,
    config: RadialInterferenceConfig,
) -> None:
    """Fill only bounded holes inside an already confirmed radial fan."""
    closure = config.morphology.fan_closure
    hard_rays = np.any(
        np.nan_to_num(probabilities, nan=0.0) >= config.flag_probability,
        axis=1,
    )
    if np.count_nonzero(hard_rays) < 2:
        return
    for gap in _bounded_circular_gap_groups(hard_rays, closure.maximum_gap_rays):
        seed_fraction = 2.0 / (len(gap) + 2.0)
        if seed_fraction < closure.minimum_seed_fraction:
            continue
        left_boundary = int((int(gap[0]) - 1) % hard_rays.size)
        right_boundary = int((int(gap[-1]) + 1) % hard_rays.size)
        boundary_extent = min(
            _radial_range_extent_fraction(valid[left_boundary], ranges_m),
            _radial_range_extent_fraction(valid[right_boundary], ranges_m),
        )
        for ray_index in gap:
            ray_valid = valid[ray_index]
            boundary_signature = _has_radial_fan_boundary_signature(
                dbzh[ray_index],
                ray_valid,
                closure,
            )
            extent_signature = closure.extent_gap_enabled and _has_radial_gap_extent_signature(
                dbzh[ray_index],
                ray_valid,
                ranges_m,
                closure,
                boundary_extent,
            )
            if not boundary_signature and not extent_signature:
                continue
            _record_radial_type(
                probabilities,
                interference_type,
                int(ray_index),
                ray_valid,
                "broad",
                config.flag_probability,
            )


def _bounded_circular_gaps(hard_rays: np.ndarray, maximum_gap_rays: int) -> np.ndarray:
    bounded = np.zeros(hard_rays.shape, dtype=bool)
    for gap in _bounded_circular_gap_groups(hard_rays, maximum_gap_rays):
        bounded[gap] = True
    return bounded


def _bounded_circular_gap_groups(
    hard_rays: np.ndarray,
    maximum_gap_rays: int,
) -> tuple[np.ndarray, ...]:
    ray_count = hard_rays.size
    if ray_count == 0 or maximum_gap_rays <= 0:
        return ()
    groups: list[np.ndarray] = []
    for start in np.flatnonzero(hard_rays):
        interior: list[int] = []
        for offset in range(1, maximum_gap_rays + 2):
            ray_index = int((start + offset) % ray_count)
            if hard_rays[ray_index]:
                if interior:
                    groups.append(np.asarray(interior, dtype="int64"))
                break
            interior.append(ray_index)
    return tuple(groups)


def _has_radial_fan_boundary_signature(
    values: np.ndarray,
    valid: np.ndarray,
    config: RadialFanClosureConfig,
) -> bool:
    if float(np.mean(valid)) < config.minimum_valid_gate_fraction:
        return False
    high = valid & (values >= config.minimum_high_dbzh)
    valid_count = int(np.count_nonzero(valid))
    if valid_count == 0:
        return False
    if float(np.count_nonzero(high) / valid_count) < config.minimum_high_gate_fraction:
        return False
    if _longest_run(high) < config.minimum_high_run:
        return False
    growth = _near_to_far_growth(values, valid)
    return growth is not None and growth >= config.minimum_range_growth_db


def _has_radial_gap_extent_signature(
    values: np.ndarray,
    valid: np.ndarray,
    ranges_m: np.ndarray,
    config: RadialFanClosureConfig,
    boundary_extent_fraction: float,
) -> bool:
    if float(np.mean(valid)) < config.minimum_gap_valid_gate_fraction:
        return False
    if _longest_run(valid) < config.minimum_gap_consecutive_gates:
        return False
    extent = _radial_range_extent_fraction(valid, ranges_m)
    required_extent = min(
        config.minimum_gap_range_extent_fraction,
        boundary_extent_fraction * config.minimum_gap_boundary_extent_ratio,
    )
    if extent < required_extent:
        return False
    growth = _valid_support_growth(values, valid)
    return growth is not None and growth >= config.minimum_range_growth_db


def _promote_radial_extent_evidence(
    dbzh: np.ndarray,
    valid: np.ndarray,
    ranges_m: np.ndarray,
    probabilities: np.ndarray,
    interference_type: np.ndarray,
    config: RadialInterferenceConfig,
    higher_elevation_extent_fraction: np.ndarray | None,
    temporal_persistence: np.ndarray | None,
    cross_radar_consistency: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fuse independent context without letting weak geometry hard-flag alone."""
    promotion = config.morphology.radial_extent_promotion
    fusion = config.morphology.context_fusion
    ray_count = dbzh.shape[0]
    higher_extent_by_ray = (
        np.asarray(higher_elevation_extent_fraction, dtype="float32")
        if higher_elevation_extent_fraction is not None
        else np.full(ray_count, np.nan, dtype="float32")
    )
    temporal_by_ray = (
        temporal_persistence
        if temporal_persistence is not None
        else np.full(ray_count, np.nan, dtype="float32")
    )
    cross_radar_by_ray = (
        cross_radar_consistency
        if cross_radar_consistency is not None
        else np.full(ray_count, np.nan, dtype="float32")
    )
    hard_rays = np.any(
        np.nan_to_num(probabilities, nan=0.0) >= config.flag_probability,
        axis=1,
    )
    diagnostic_rays = np.any(
        (np.nan_to_num(probabilities, nan=0.0) >= config.morphology.diagnostic_probability)
        & (np.nan_to_num(probabilities, nan=0.0) < config.flag_probability),
        axis=1,
    )
    candidates = np.zeros(dbzh.shape[0], dtype=bool)
    strong_geometry = np.zeros(dbzh.shape[0], dtype=bool)
    vertical_evidence = np.zeros(dbzh.shape[0], dtype=bool)
    for ray_index in range(dbzh.shape[0]):
        if hard_rays[ray_index]:
            continue
        ray_valid = valid[ray_index]
        higher_extent = float(higher_extent_by_ray[ray_index])
        vertical_evidence[ray_index] = (
            np.isfinite(higher_extent)
            and higher_extent <= promotion.maximum_higher_elevation_extent_fraction
        )
        fan_context = diagnostic_rays[ray_index] and _has_circular_neighbour(
            hard_rays,
            ray_index,
            promotion.maximum_hard_seed_distance_rays,
        )
        minimum_valid_fraction = (
            promotion.diagnostic_minimum_valid_gate_fraction
            if fan_context
            else promotion.minimum_valid_gate_fraction
        )
        if float(np.mean(ray_valid)) < minimum_valid_fraction:
            continue
        if _longest_run(ray_valid) < promotion.minimum_consecutive_gates:
            continue
        if (
            _radial_range_extent_fraction(ray_valid, ranges_m)
            < promotion.minimum_range_extent_fraction
        ):
            continue
        growth = _near_to_far_growth(dbzh[ray_index], ray_valid)
        if growth is None or growth < promotion.minimum_range_growth_db:
            continue
        power_iqr = _range_corrected_power_iqr(
            dbzh[ray_index],
            ray_valid,
            ranges_m,
            promotion.minimum_analysis_range_m,
        )
        if power_iqr is None or power_iqr > promotion.maximum_power_iqr_db:
            continue
        candidates[ray_index] = True
        strong_geometry[ray_index] = fan_context

    accepted_candidates = np.zeros(ray_count, dtype=bool)
    context_promoted = np.zeros(ray_count, dtype=bool)
    cross_radar_vetoed = np.zeros(ray_count, dtype=bool)
    for group in _circular_true_groups(
        candidates,
        maximum_gap_rays=promotion.maximum_group_gap_rays,
    ):
        if not promotion.minimum_group_rays <= len(group) <= promotion.maximum_group_rays:
            continue
        for ray_index in group:
            ray_index = int(ray_index)
            accepted_candidates[ray_index] = True
            _record_radial_type(
                probabilities,
                interference_type,
                ray_index,
                valid[ray_index],
                "narrow",
                config.morphology.diagnostic_probability,
            )
            if strong_geometry[ray_index]:
                should_promote = True
            elif not fusion.enabled:
                should_promote = bool(vertical_evidence[ray_index])
            else:
                evidence_count = 1 + int(vertical_evidence[ray_index])
                persistence = float(temporal_by_ray[ray_index])
                if (
                    np.isfinite(persistence)
                    and persistence >= fusion.temporal_persistence_threshold
                ):
                    evidence_count += 1
                consistency = float(cross_radar_by_ray[ray_index])
                cross_radar_veto = (
                    np.isfinite(consistency)
                    and consistency >= fusion.cross_radar_veto_min_consistency
                )
                if cross_radar_veto:
                    cross_radar_vetoed[ray_index] = True
                    should_promote = False
                else:
                    if (
                        np.isfinite(consistency)
                        and consistency <= fusion.cross_radar_promotion_max_consistency
                    ):
                        evidence_count += 1
                    should_promote = evidence_count >= fusion.minimum_independent_evidence
                if should_promote:
                    context_promoted[ray_index] = True
            if not should_promote:
                continue
            _record_radial_type(
                probabilities,
                interference_type,
                ray_index,
                valid[ray_index],
                "narrow",
                config.flag_probability,
            )
    return accepted_candidates, context_promoted, cross_radar_vetoed


def _promote_multiscale_radial_evidence(
    dbzh: np.ndarray,
    valid: np.ndarray,
    probabilities: np.ndarray,
    interference_type: np.ndarray,
    config: RadialInterferenceConfig,
) -> None:
    """Confirm sparse longitudinal spikes with short/long azimuth context."""
    promotion = config.morphology.multiscale_promotion
    diagnostic = (
        np.nan_to_num(probabilities, nan=0.0) >= config.morphology.diagnostic_probability
    ) & (np.nan_to_num(probabilities, nan=0.0) < config.flag_probability)
    diagnostic_counts = np.count_nonzero(diagnostic, axis=1)
    if not np.any(diagnostic_counts >= promotion.minimum_diagnostic_gates):
        return

    echo_counts = np.count_nonzero(
        valid & (dbzh >= promotion.echo_threshold_dbzh),
        axis=1,
    ).astype("float64")
    edge_jump = np.maximum(
        np.abs(echo_counts - np.roll(echo_counts, 1)),
        np.abs(echo_counts - np.roll(echo_counts, -1)),
    )
    short_scale = _circular_window_mean(edge_jump, promotion.short_window_rays)
    long_scale = _circular_window_mean(edge_jump, promotion.long_window_rays)
    scale_score = short_scale - long_scale
    gate_count = dbzh.shape[1]
    minimum_edge_jump = promotion.minimum_edge_jump_gate_fraction * gate_count
    minimum_score = promotion.minimum_score_gate_fraction * gate_count

    for ray_index in np.flatnonzero(diagnostic_counts >= promotion.minimum_diagnostic_gates):
        if edge_jump[ray_index] < minimum_edge_jump:
            continue
        if scale_score[ray_index] < minimum_score:
            continue
        high = valid[ray_index] & (dbzh[ray_index] >= promotion.minimum_high_dbzh)
        if np.count_nonzero(high) < promotion.minimum_high_gates:
            continue
        growth = _near_to_far_growth(dbzh[ray_index], valid[ray_index])
        if growth is None or growth < promotion.minimum_range_growth_db:
            continue
        _record_radial_type(
            probabilities,
            interference_type,
            int(ray_index),
            diagnostic[ray_index],
            _interference_type_name(interference_type[ray_index], diagnostic[ray_index]),
            config.flag_probability,
        )


def _circular_window_mean(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return values.astype("float64", copy=True)
    total = np.zeros(values.shape, dtype="float64")
    for offset in range(-radius, radius + 1):
        total += np.roll(values, offset)
    return total / (2 * radius + 1)


def _circular_true_groups(
    values: np.ndarray,
    *,
    maximum_gap_rays: int = 0,
) -> tuple[np.ndarray, ...]:
    indices = np.flatnonzero(values)
    if indices.size == 0:
        return ()
    split_points = np.flatnonzero(np.diff(indices) > maximum_gap_rays + 1) + 1
    groups = [item for item in np.split(indices, split_points) if item.size]
    if len(groups) > 1 and int(groups[0][0] + values.size - groups[-1][-1] - 1) <= maximum_gap_rays:
        groups[0] = np.concatenate((groups[-1], groups[0]))
        groups.pop()
    return tuple(item.astype("int64", copy=False) for item in groups)


def _has_circular_neighbour(
    values: np.ndarray,
    ray_index: int,
    maximum_distance_rays: int,
) -> bool:
    for distance in range(1, maximum_distance_rays + 1):
        if values[(ray_index - distance) % values.size]:
            return True
        if values[(ray_index + distance) % values.size]:
            return True
    return False


def _radial_range_extent_fractions(
    valid: np.ndarray,
    ranges_m: np.ndarray,
) -> np.ndarray:
    if valid.ndim != 2 or ranges_m.shape != (valid.shape[1],):
        raise QCInputError("radial extent geometry differs from gate shape")
    maximum_range = float(np.nanmax(ranges_m)) if ranges_m.size else 0.0
    result = np.zeros(valid.shape[0], dtype="float32")
    if not np.isfinite(maximum_range) or maximum_range <= 0:
        return result
    for ray_index in range(valid.shape[0]):
        result[ray_index] = _radial_range_extent_fraction(valid[ray_index], ranges_m)
    return result


def _radial_range_extent_fraction(
    valid: np.ndarray,
    ranges_m: np.ndarray,
) -> float:
    indices = np.flatnonzero(valid)
    if indices.size == 0 or ranges_m.size == 0:
        return 0.0
    maximum_range = float(np.nanmax(ranges_m))
    if not np.isfinite(maximum_range) or maximum_range <= 0:
        return 0.0
    return float(ranges_m[int(indices[-1])] / maximum_range)


def _range_corrected_power_iqr(
    values: np.ndarray,
    valid: np.ndarray,
    ranges_m: np.ndarray,
    minimum_range_m: float,
) -> float | None:
    evidence = valid & np.isfinite(ranges_m) & (ranges_m >= minimum_range_m)
    if np.count_nonzero(evidence) < 2:
        return None
    ranges_km = np.maximum(ranges_m[evidence] / 1_000.0, 1e-3)
    power_proxy = values[evidence] - 20.0 * np.log10(ranges_km)
    lower, upper = np.percentile(power_proxy, [25.0, 75.0])
    return float(upper - lower)


def _interference_type_name(types: np.ndarray, mask: np.ndarray) -> str:
    codes, counts = np.unique(types[mask], return_counts=True)
    if codes.size == 0:
        return "narrow"
    code = int(codes[int(np.argmax(counts))])
    return next(
        (name for name, candidate in INTERFERENCE_TYPE_CODES.items() if candidate == code),
        "narrow",
    )


def _record_radial_type(
    probabilities: np.ndarray,
    interference_type: np.ndarray,
    ray_index: int,
    mask: np.ndarray,
    type_name: str,
    probability: float,
) -> None:
    replace = mask & (np.nan_to_num(probabilities[ray_index], nan=-1.0) < probability)
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
    baseline[previous_finite & ~next_finite] = previous_values[previous_finite & ~next_finite]
    baseline[next_finite & ~previous_finite] = next_values[next_finite & ~previous_finite]
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


def _near_to_far_growth(values: np.ndarray, valid: np.ndarray) -> float | None:
    quartile = max(values.size // 4, 1)
    near = values[:quartile][valid[:quartile]]
    far = values[-quartile:][valid[-quartile:]]
    if near.size == 0 or far.size == 0:
        return None
    return float(np.median(far) - np.median(near))


def _valid_support_growth(values: np.ndarray, valid: np.ndarray) -> float | None:
    supported = values[valid]
    if supported.size < 2:
        return None
    quartile = max(supported.size // 4, 1)
    return float(np.median(supported[-quartile:]) - np.median(supported[:quartile]))


def _rising_membership(values: np.ndarray, bounds: tuple[float, float]) -> np.ndarray:
    return np.clip((values - bounds[0]) / (bounds[1] - bounds[0]), 0.0, 1.0).astype("float32")


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


def _temporal_radial_persistence(
    azimuth: np.ndarray,
    context_scans: tuple[tuple[np.ndarray, np.ndarray], ...],
    *,
    minimum_context_scans: int,
    maximum_context_scans: int,
) -> np.ndarray:
    """Return same-azimuth candidate persistence across ordered nearby scans."""
    source_azimuth = np.asarray(azimuth, dtype="float64")
    if source_azimuth.ndim != 1:
        raise QCInputError("temporal context source azimuth must be one-dimensional")
    if minimum_context_scans < 1 or maximum_context_scans < minimum_context_scans:
        raise QCInputError("invalid temporal context scan limits")
    selected = context_scans[:maximum_context_scans]
    if len(selected) < minimum_context_scans:
        return np.full(source_azimuth.shape, np.nan, dtype="float32")
    aligned: list[np.ndarray] = []
    for context_azimuth, context_candidates in selected:
        target_azimuth = np.asarray(context_azimuth, dtype="float64")
        candidates = np.asarray(context_candidates, dtype=bool)
        if target_azimuth.ndim != 1 or candidates.shape != target_azimuth.shape:
            raise QCInputError("temporal context candidates differ from their azimuth shape")
        indices = _nearest_azimuth_indices(source_azimuth, target_azimuth)
        aligned.append(candidates[indices])
    return np.mean(np.stack(aligned, axis=0), axis=0, dtype="float64").astype("float32")


def _cross_radar_consistency_by_ray(
    dbzh: np.ndarray,
    valid: np.ndarray,
    reprojected_neighbour_dbzh: tuple[np.ndarray, ...],
    *,
    echo_threshold_dbzh: float,
    minimum_overlap_gates: int,
) -> np.ndarray:
    """Measure neighbour support only where another radar overlaps current echo."""
    current = np.asarray(dbzh, dtype="float32")
    current_valid = np.asarray(valid, dtype=bool)
    if current.ndim != 2 or current_valid.shape != current.shape:
        raise QCInputError("cross-radar current field differs from its valid mask")
    result = np.full(current.shape[0], np.nan, dtype="float32")
    if not reprojected_neighbour_dbzh:
        return result
    neighbours = []
    for field in reprojected_neighbour_dbzh:
        neighbour = np.asarray(field, dtype="float32")
        if neighbour.shape != current.shape:
            raise QCInputError("reprojected neighbour differs from current radar gate shape")
        neighbours.append(neighbour)
    stack = np.stack(neighbours, axis=0)
    neighbour_observed = np.any(np.isfinite(stack), axis=0)
    neighbour_echo = np.any(np.isfinite(stack) & (stack >= echo_threshold_dbzh), axis=0)
    current_echo = current_valid & np.isfinite(current) & (current >= echo_threshold_dbzh)
    for ray_index in range(current.shape[0]):
        overlap = current_echo[ray_index] & neighbour_observed[ray_index]
        overlap_count = int(np.count_nonzero(overlap))
        if overlap_count < minimum_overlap_gates:
            continue
        result[ray_index] = np.count_nonzero(
            overlap & neighbour_echo[ray_index]
        ) / overlap_count
    return result


def _nearest_azimuth_indices(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    if target.size == 0:
        raise QCInputError("vertical comparison target has no azimuths")
    difference = np.abs((source[:, None] - target[None, :] + 180.0) % 360.0 - 180.0)
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
    radial_weak_candidate_ray_count: int,
    radial_context_promoted_ray_count: int,
    radial_cross_radar_vetoed_ray_count: int,
    type_ray_counts: dict[str, int],
    ground_count: int,
    sea_count: int,
    ap_count: int,
) -> tuple[QCModuleRecord, ...]:
    return (
        QCModuleRecord("health_gate", profile.pipeline_version, "applied", (), (), None, {}),
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
            (None if dual_pol_available else "dual_pol_fuzzy_disabled_or_fields_unavailable"),
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
            {"diagnostic_only": float(profile.vertical_consistency.mode == "diagnostic_only")},
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
                "weak_candidate_ray_count": float(radial_weak_candidate_ray_count),
                "context_promoted_ray_count": float(radial_context_promoted_ray_count),
                "cross_radar_vetoed_ray_count": float(radial_cross_radar_vetoed_ray_count),
                **{f"{name}_ray_count": float(count) for name, count in type_ray_counts.items()},
                "morphology_diagnostic_only": float(
                    profile.radial_interference.morphology.mode == "diagnostic_only"
                ),
                "fan_closure_enabled": float(
                    profile.radial_interference.morphology.fan_closure.enabled
                ),
                "radial_extent_promotion_enabled": float(
                    profile.radial_interference.morphology.radial_extent_promotion.enabled
                ),
                "context_fusion_enabled": float(
                    profile.radial_interference.morphology.context_fusion.enabled
                ),
                "multiscale_promotion_enabled": float(
                    profile.radial_interference.morphology.multiscale_promotion.enabled
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
    closure = morphology.fan_closure
    if closure.maximum_gap_rays <= 0 or closure.minimum_high_run < 2:
        raise QCConfigError("invalid radial fan-closure dimensions")
    if not 0 <= closure.minimum_valid_gate_fraction <= 1:
        raise QCConfigError("invalid radial fan-closure valid fraction")
    if not 0 <= closure.minimum_high_gate_fraction <= 1:
        raise QCConfigError("invalid radial fan-closure high-gate fraction")
    if closure.minimum_range_growth_db <= 0:
        raise QCConfigError("invalid radial fan-closure range growth")
    if closure.minimum_gap_consecutive_gates < 2:
        raise QCConfigError("invalid radial fan-closure gap length")
    for value in (
        closure.minimum_gap_valid_gate_fraction,
        closure.minimum_gap_range_extent_fraction,
        closure.minimum_gap_boundary_extent_ratio,
        closure.minimum_seed_fraction,
    ):
        if not 0 <= value <= 1:
            raise QCConfigError("invalid radial fan-closure gap fraction")
    extent = morphology.radial_extent_promotion
    if extent.minimum_consecutive_gates < 2:
        raise QCConfigError("invalid radial extent-promotion gate count")
    if extent.minimum_group_rays < 2:
        raise QCConfigError("invalid radial extent-promotion group minimum")
    if extent.maximum_group_rays < extent.minimum_group_rays:
        raise QCConfigError("invalid radial extent-promotion group maximum")
    if extent.maximum_group_gap_rays < 0:
        raise QCConfigError("invalid radial extent-promotion group gap")
    if extent.maximum_hard_seed_distance_rays < 1:
        raise QCConfigError("invalid radial extent-promotion seed distance")
    for value in (
        extent.minimum_valid_gate_fraction,
        extent.diagnostic_minimum_valid_gate_fraction,
        extent.minimum_range_extent_fraction,
        extent.maximum_higher_elevation_extent_fraction,
    ):
        if not 0 <= value <= 1:
            raise QCConfigError("invalid radial extent-promotion fraction")
    if (
        extent.minimum_range_growth_db <= 0
        or extent.minimum_analysis_range_m <= 0
        or extent.maximum_power_iqr_db <= 0
    ):
        raise QCConfigError("invalid radial extent-promotion physical limit")
    context = morphology.context_fusion
    if not 2 <= context.minimum_independent_evidence <= 4:
        raise QCConfigError("radial context fusion requires two to four independent evidence types")
    if not 2 <= context.minimum_temporal_context_scans <= 3:
        raise QCConfigError("radial temporal context minimum must be two or three scans")
    if not context.minimum_temporal_context_scans <= context.maximum_temporal_context_scans <= 3:
        raise QCConfigError("radial temporal context maximum must follow its minimum")
    for value in (
        context.temporal_persistence_threshold,
        context.cross_radar_promotion_max_consistency,
        context.cross_radar_veto_min_consistency,
    ):
        if not 0 <= value <= 1:
            raise QCConfigError("invalid radial context fusion probability")
    if (
        context.cross_radar_promotion_max_consistency
        >= context.cross_radar_veto_min_consistency
    ):
        raise QCConfigError("cross-radar promotion threshold must be below veto threshold")
    if context.minimum_cross_radar_overlap_gates <= 0:
        raise QCConfigError("cross-radar overlap gate count must be positive")
    if context.cross_radar_max_time_offset_seconds <= 0:
        raise QCConfigError("cross-radar maximum time offset must be positive")
    multiscale = morphology.multiscale_promotion
    if (
        multiscale.short_window_rays <= 0
        or multiscale.long_window_rays <= multiscale.short_window_rays
    ):
        raise QCConfigError("radial multiscale windows must increase")
    if multiscale.minimum_diagnostic_gates < 2 or multiscale.minimum_high_gates < 2:
        raise QCConfigError("invalid radial multiscale gate counts")
    if not 0 <= multiscale.minimum_score_gate_fraction <= 1:
        raise QCConfigError("invalid radial multiscale score fraction")
    if not 0 <= multiscale.minimum_edge_jump_gate_fraction <= 1:
        raise QCConfigError("invalid radial multiscale edge fraction")
    if multiscale.minimum_range_growth_db <= 0:
        raise QCConfigError("invalid radial multiscale range growth")
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
