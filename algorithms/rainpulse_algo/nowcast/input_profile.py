from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class NowcastInputConfigError(ValueError):
    """Raised when the RP-013 profile is incomplete or inconsistent."""


@dataclass(frozen=True)
class SequenceConfig:
    minimum_frames: int
    maximum_frames: int
    timestep_minutes: int
    selection: str


@dataclass(frozen=True)
class GateConfig:
    minimum_valid_coverage_ratio: float
    minimum_mean_quality_index: float
    maximum_data_age_minutes: float
    require_all_frames_operational_eligible: bool


@dataclass(frozen=True)
class NowcastInputProfile:
    profile_version: str
    builder_version: str
    nowcast_input_contract_version: str
    radar_analysis_contract_version: str
    grid_id: str
    grid_config_version: str
    sequence: SequenceConfig
    gates: GateConfig


def load_nowcast_input_profile(path: str | Path) -> NowcastInputProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise NowcastInputConfigError("unsupported NowcastInput profile schema")
    try:
        sequence = raw["sequence"]
        gates = raw["gates"]
        profile = NowcastInputProfile(
            profile_version=str(raw["profile_version"]),
            builder_version=str(raw["builder_version"]),
            nowcast_input_contract_version=str(
                raw["nowcast_input_contract_version"]
            ),
            radar_analysis_contract_version=str(
                raw["radar_analysis_contract_version"]
            ),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            sequence=SequenceConfig(
                minimum_frames=int(sequence["minimum_frames"]),
                maximum_frames=int(sequence["maximum_frames"]),
                timestep_minutes=int(sequence["timestep_minutes"]),
                selection=str(sequence["selection"]),
            ),
            gates=GateConfig(
                minimum_valid_coverage_ratio=float(
                    gates["minimum_valid_coverage_ratio"]
                ),
                minimum_mean_quality_index=float(
                    gates["minimum_mean_quality_index"]
                ),
                maximum_data_age_minutes=float(gates["maximum_data_age_minutes"]),
                require_all_frames_operational_eligible=bool(
                    gates["require_all_frames_operational_eligible"]
                ),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NowcastInputConfigError(
            f"invalid NowcastInput profile {profile_path}: {exc}"
        ) from exc
    _validate(profile)
    return profile


def _validate(profile: NowcastInputProfile) -> None:
    if profile.nowcast_input_contract_version != "1.2":
        raise NowcastInputConfigError("RP-013 requires NowcastInput contract 1.2")
    if profile.radar_analysis_contract_version != "1.2":
        raise NowcastInputConfigError("RP-013 requires RadarAnalysis contract 1.2")
    if profile.sequence != SequenceConfig(3, 6, 5, "latest_contiguous"):
        raise NowcastInputConfigError(
            "Phase-1 requires 3-6 latest contiguous frames at five-minute steps"
        )
    for name, value in (
        (
            "minimum_valid_coverage_ratio",
            profile.gates.minimum_valid_coverage_ratio,
        ),
        ("minimum_mean_quality_index", profile.gates.minimum_mean_quality_index),
    ):
        if not 0 <= value <= 1:
            raise NowcastInputConfigError(f"{name} must be within [0, 1]")
    if profile.gates.maximum_data_age_minutes <= 0:
        raise NowcastInputConfigError("maximum data age must be positive")
    if not profile.gates.require_all_frames_operational_eligible:
        raise NowcastInputConfigError(
            "RP-013 cannot admit upstream engineering-only analysis frames"
        )
