from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class PystepsStepsConfigError(ValueError):
    """Raised when an RP-022 pySTEPS-STEPS profile is inconsistent."""


@dataclass(frozen=True)
class StepsSequenceConfig:
    minimum_frames: int
    maximum_frames: int
    timestep_minutes: int


@dataclass(frozen=True)
class StepsEnsembleConfig:
    member_count: int
    random_seed: int
    num_workers: int
    cascade_levels: int
    autoregressive_order: int
    transformation: str
    precipitation_threshold_mm_h: float
    minimum_trackable_precipitation_pixels: int
    decomposition_method: str
    bandpass_filter_method: str
    precipitation_noise_method: str
    noise_stddev_adjustment: str
    velocity_perturbation_method: str
    probability_matching_method: str
    mask_method: str
    domain: str


@dataclass(frozen=True)
class StepsSupportConfig:
    input_missing_policy: str
    output_support_policy: str


@dataclass(frozen=True)
class StepsProbabilityConfig:
    event_operator: str
    calibration_status: str
    rain_rate_thresholds_mm_h: tuple[float, ...]
    quantiles: tuple[float, ...]


@dataclass(frozen=True)
class PystepsStepsProfile:
    profile_version: str
    model_id: str
    model_version: str
    pysteps_version: str
    nowcast_input_contract_version: str
    forecast_output_contract_version: str
    grid_id: str
    grid_config_version: str
    sequence: StepsSequenceConfig
    ensemble: StepsEnsembleConfig
    support: StepsSupportConfig
    probability_products: StepsProbabilityConfig


def load_pysteps_steps_profile(path: str | Path) -> PystepsStepsProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise PystepsStepsConfigError("unsupported pySTEPS-STEPS profile schema")
    try:
        sequence = raw["sequence"]
        ensemble = raw["ensemble"]
        support = raw["support"]
        probability = raw["probability_products"]
        profile = PystepsStepsProfile(
            profile_version=str(raw["profile_version"]),
            model_id=str(raw["model_id"]),
            model_version=str(raw["model_version"]),
            pysteps_version=str(raw["pysteps_version"]),
            nowcast_input_contract_version=str(raw["nowcast_input_contract_version"]),
            forecast_output_contract_version=str(raw["forecast_output_contract_version"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            sequence=StepsSequenceConfig(
                minimum_frames=int(sequence["minimum_frames"]),
                maximum_frames=int(sequence["maximum_frames"]),
                timestep_minutes=int(sequence["timestep_minutes"]),
            ),
            ensemble=StepsEnsembleConfig(
                member_count=int(ensemble["member_count"]),
                random_seed=int(ensemble["random_seed"]),
                num_workers=int(ensemble["num_workers"]),
                cascade_levels=int(ensemble["cascade_levels"]),
                autoregressive_order=int(ensemble["autoregressive_order"]),
                transformation=str(ensemble["transformation"]),
                precipitation_threshold_mm_h=float(
                    ensemble["precipitation_threshold_mm_h"]
                ),
                minimum_trackable_precipitation_pixels=int(
                    ensemble.get("minimum_trackable_precipitation_pixels", 1)
                ),
                decomposition_method=str(ensemble["decomposition_method"]),
                bandpass_filter_method=str(ensemble["bandpass_filter_method"]),
                precipitation_noise_method=str(ensemble["precipitation_noise_method"]),
                noise_stddev_adjustment=str(ensemble["noise_stddev_adjustment"]),
                velocity_perturbation_method=str(
                    ensemble["velocity_perturbation_method"]
                ),
                probability_matching_method=str(ensemble["probability_matching_method"]),
                mask_method=str(ensemble["mask_method"]),
                domain=str(ensemble["domain"]),
            ),
            support=StepsSupportConfig(
                input_missing_policy=str(support["input_missing_policy"]),
                output_support_policy=str(support["output_support_policy"]),
            ),
            probability_products=StepsProbabilityConfig(
                event_operator=str(probability["event_operator"]),
                calibration_status=str(probability["calibration_status"]),
                rain_rate_thresholds_mm_h=tuple(
                    float(value) for value in probability["rain_rate_thresholds_mm_h"]
                ),
                quantiles=tuple(float(value) for value in probability["quantiles"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PystepsStepsConfigError(
            f"invalid pySTEPS-STEPS profile {profile_path}: {exc}"
        ) from exc
    _validate(profile)
    return profile


def _validate(profile: PystepsStepsProfile) -> None:
    if profile.model_id != "pysteps-steps":
        raise PystepsStepsConfigError("model ID must be pysteps-steps")
    if profile.pysteps_version != "1.21.5":
        raise PystepsStepsConfigError("pySTEPS-STEPS runtime version is frozen")
    if profile.nowcast_input_contract_version != "1.2":
        raise PystepsStepsConfigError("pySTEPS-STEPS requires NowcastInput contract 1.2")
    if profile.forecast_output_contract_version != "1.2":
        raise PystepsStepsConfigError("pySTEPS-STEPS requires ForecastOutput contract 1.2")
    if profile.sequence != StepsSequenceConfig(3, 6, 5):
        raise PystepsStepsConfigError("pySTEPS-STEPS requires 3-6 frames at five-minute steps")
    ensemble = profile.ensemble
    if not 2 <= ensemble.member_count <= 96:
        raise PystepsStepsConfigError("ensemble member count must be within [2, 96]")
    if not 0 <= ensemble.random_seed <= 2**32 - 1:
        raise PystepsStepsConfigError("ensemble random seed is outside uint32")
    if ensemble.num_workers != 1:
        raise PystepsStepsConfigError("RP-022 freezes one worker for reproducibility")
    if not 2 <= ensemble.cascade_levels <= 12 or ensemble.autoregressive_order != 2:
        raise PystepsStepsConfigError("invalid STEPS cascade or autoregressive order")
    if ensemble.transformation != "dB" or ensemble.precipitation_threshold_mm_h <= 0:
        raise PystepsStepsConfigError("STEPS precipitation transform is invalid")
    if ensemble.minimum_trackable_precipitation_pixels < 1:
        raise PystepsStepsConfigError("STEPS minimum trackable precipitation area is invalid")
    if ensemble.decomposition_method != "fft":
        raise PystepsStepsConfigError("STEPS decomposition must use fft")
    if ensemble.bandpass_filter_method not in {"gaussian", "uniform"}:
        raise PystepsStepsConfigError("unsupported STEPS bandpass filter")
    if ensemble.precipitation_noise_method not in {
        "parametric",
        "nonparametric",
        "ssft",
        "nested",
    }:
        raise PystepsStepsConfigError("unsupported STEPS precipitation noise")
    if ensemble.noise_stddev_adjustment not in {"auto", "fixed", "none"}:
        raise PystepsStepsConfigError("unsupported STEPS noise adjustment")
    if ensemble.velocity_perturbation_method != "bps":
        raise PystepsStepsConfigError("RP-022 requires BPS velocity perturbations")
    if ensemble.probability_matching_method not in {"cdf", "mean", "none"}:
        raise PystepsStepsConfigError("unsupported STEPS probability matching")
    if ensemble.mask_method not in {"obs", "sprog", "incremental"}:
        raise PystepsStepsConfigError("unsupported STEPS mask method")
    if ensemble.domain not in {"spatial", "spectral"}:
        raise PystepsStepsConfigError("unsupported STEPS compute domain")
    if profile.support != StepsSupportConfig(
        "reject_any_missing", "deterministic_support_intersect_all_members_finite"
    ):
        raise PystepsStepsConfigError("RP-022 support policy differs from the frozen gate")
    probability = profile.probability_products
    if probability.event_operator != "greater_than":
        raise PystepsStepsConfigError("probability event operator must be greater_than")
    if probability.calibration_status != "raw_ensemble_relative_frequency_uncalibrated":
        raise PystepsStepsConfigError("probability calibration status is invalid")
    if probability.rain_rate_thresholds_mm_h != (1.0, 5.0, 10.0, 20.0, 50.0):
        raise PystepsStepsConfigError("probability thresholds differ from RP-022")
    if probability.quantiles != (0.1, 0.5, 0.9):
        raise PystepsStepsConfigError("ensemble quantiles differ from RP-022")
