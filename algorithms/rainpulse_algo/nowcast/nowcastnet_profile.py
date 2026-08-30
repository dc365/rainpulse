from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml


class NowcastNetConfigError(ValueError):
    """Raised when the RP-026 NowcastNet profile is inconsistent or not ready."""


@dataclass(frozen=True)
class NowcastNetSource:
    paper_doi: str
    official_capsule_doi: str
    official_source_reviewed: bool
    license_approved: bool


@dataclass(frozen=True)
class NowcastNetArtifact:
    capsule_archive_sha256: str
    weights_uri: str | None
    weights_sha256: str | None
    weights_reviewed: bool


@dataclass(frozen=True)
class NowcastNetProtocol:
    input_field: str
    units: str
    input_frames: int
    total_frames: int
    output_frames: int
    timestep_minutes: int
    input_height: int
    input_width: int
    input_channels: int
    rain_rate_cap_mm_h: float
    ensemble_members: int
    missing_policy: str
    output_negative_policy: str
    preprocess_protocol_verified: bool


@dataclass(frozen=True)
class NowcastNetRuntime:
    python_version: str
    torch_version: str
    torch_cuda_version: str
    target_compute_capability: str
    compatibility_patch_sha256: str
    environment_reviewed: bool


@dataclass(frozen=True)
class NowcastNetActivation:
    offline_inference_enabled: bool
    realtime_shadow_enabled: bool
    product_publication_enabled: bool
    operational_eligible: bool


@dataclass(frozen=True)
class NowcastNetProfile:
    profile_version: str
    model_id: str
    model_version: str
    forecast_output_contract_version: str
    source: NowcastNetSource
    artifact: NowcastNetArtifact
    protocol: NowcastNetProtocol
    runtime: NowcastNetRuntime
    activation: NowcastNetActivation

    def offline_readiness_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.source.official_source_reviewed:
            blockers.append("official_source_review_required")
        if not self.source.license_approved:
            blockers.append("license_approval_required")
        if not self.artifact.weights_uri or not self.artifact.weights_sha256:
            blockers.append("versioned_weights_artifact_required")
        if not self.artifact.weights_reviewed:
            blockers.append("weights_review_required")
        if not self.protocol.preprocess_protocol_verified:
            blockers.append("preprocess_protocol_verification_required")
        if not self.runtime.environment_reviewed:
            blockers.append("runtime_environment_review_required")
        if not self.activation.offline_inference_enabled:
            blockers.append("offline_inference_disabled")
        return tuple(blockers)

    def require_offline_ready(self) -> None:
        blockers = self.offline_readiness_blockers()
        if blockers:
            raise NowcastNetConfigError(
                "NowcastNet offline inference is blocked: " + ", ".join(blockers)
            )

    def weights_path(self) -> Path:
        uri = self.artifact.weights_uri
        if not uri:
            raise NowcastNetConfigError("NowcastNet weights URI is not configured")
        parsed = urlparse(uri)
        if (
            parsed.scheme != "file"
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise NowcastNetConfigError(
                "RP-026 offline weights URI must be an absolute local file URI"
            )
        return Path(unquote(parsed.path))


def load_nowcastnet_profile(path: str | Path) -> NowcastNetProfile:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        if raw["schema_version"] != "1.0":
            raise NowcastNetConfigError("unsupported NowcastNet profile schema")
        source = raw["source"]
        artifact = raw["artifact"]
        protocol = raw["protocol"]
        runtime = raw["runtime"]
        activation = raw["activation"]
        profile = NowcastNetProfile(
            profile_version=str(raw["profile_version"]),
            model_id=str(raw["model_id"]),
            model_version=str(raw["model_version"]),
            forecast_output_contract_version=str(raw["forecast_output_contract_version"]),
            source=NowcastNetSource(
                paper_doi=str(source["paper_doi"]),
                official_capsule_doi=str(source["official_capsule_doi"]),
                official_source_reviewed=bool(source["official_source_reviewed"]),
                license_approved=bool(source["license_approved"]),
            ),
            artifact=NowcastNetArtifact(
                capsule_archive_sha256=str(artifact["capsule_archive_sha256"]),
                weights_uri=(str(artifact["weights_uri"]) if artifact["weights_uri"] else None),
                weights_sha256=(
                    str(artifact["weights_sha256"]) if artifact["weights_sha256"] else None
                ),
                weights_reviewed=bool(artifact["weights_reviewed"]),
            ),
            protocol=NowcastNetProtocol(
                input_field=str(protocol["input_field"]),
                units=str(protocol["units"]),
                input_frames=int(protocol["input_frames"]),
                total_frames=int(protocol["total_frames"]),
                output_frames=int(protocol["output_frames"]),
                timestep_minutes=int(protocol["timestep_minutes"]),
                input_height=int(protocol["input_height"]),
                input_width=int(protocol["input_width"]),
                input_channels=int(protocol["input_channels"]),
                rain_rate_cap_mm_h=float(protocol["rain_rate_cap_mm_h"]),
                ensemble_members=int(protocol["ensemble_members"]),
                missing_policy=str(protocol["missing_policy"]),
                output_negative_policy=str(protocol["output_negative_policy"]),
                preprocess_protocol_verified=bool(protocol["preprocess_protocol_verified"]),
            ),
            runtime=NowcastNetRuntime(
                python_version=str(runtime["python_version"]),
                torch_version=str(runtime["torch_version"]),
                torch_cuda_version=str(runtime["torch_cuda_version"]),
                target_compute_capability=str(runtime["target_compute_capability"]),
                compatibility_patch_sha256=str(runtime["compatibility_patch_sha256"]),
                environment_reviewed=bool(runtime["environment_reviewed"]),
            ),
            activation=NowcastNetActivation(
                offline_inference_enabled=bool(activation["offline_inference_enabled"]),
                realtime_shadow_enabled=bool(activation["realtime_shadow_enabled"]),
                product_publication_enabled=bool(activation["product_publication_enabled"]),
                operational_eligible=bool(activation["operational_eligible"]),
            ),
        )
    except NowcastNetConfigError:
        raise
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise NowcastNetConfigError(
            f"invalid NowcastNet profile {profile_path}: {exc}"
        ) from exc
    _validate_profile(profile)
    return profile


def _validate_profile(profile: NowcastNetProfile) -> None:
    if profile.model_id != "nowcastnet":
        raise NowcastNetConfigError("model ID must be nowcastnet")
    if profile.forecast_output_contract_version != "1.2":
        raise NowcastNetConfigError("NowcastNet requires ForecastOutput contract 1.2")
    if profile.source.paper_doi != "10.1038/s41586-023-06184-4":
        raise NowcastNetConfigError("NowcastNet paper identity differs from RP-026")
    if profile.source.official_capsule_doi != "10.24433/CO.0832447.v1":
        raise NowcastNetConfigError("NowcastNet official capsule identity differs from RP-026")
    expected_protocol = NowcastNetProtocol(
        input_field="RATE_QPE",
        units="mm h-1",
        input_frames=9,
        total_frames=29,
        output_frames=20,
        timestep_minutes=10,
        input_height=512,
        input_width=512,
        input_channels=2,
        rain_rate_cap_mm_h=128.0,
        ensemble_members=4,
        missing_policy="reject_any_missing",
        output_negative_policy="clip_to_zero_with_diagnostic",
        preprocess_protocol_verified=profile.protocol.preprocess_protocol_verified,
    )
    if profile.protocol != expected_protocol:
        raise NowcastNetConfigError("NowcastNet protocol differs from the RP-026 boundary")
    expected_runtime = NowcastNetRuntime(
        python_version="3.13",
        torch_version="2.12.1+cu132",
        torch_cuda_version="13.2",
        target_compute_capability="12.0",
        compatibility_patch_sha256=(
            "7a42637adacb6d37ffec1b559d6a31ba05c45338e01fb2d054448f3c0dfe7f32"
        ),
        environment_reviewed=profile.runtime.environment_reviewed,
    )
    if profile.runtime != expected_runtime:
        raise NowcastNetConfigError("NowcastNet runtime differs from the RP-026 boundary")
    if not profile.artifact.capsule_archive_sha256:
        raise NowcastNetConfigError("capsule archive SHA-256 is required")
    for name, value in (
        ("capsule archive", profile.artifact.capsule_archive_sha256),
        ("weights", profile.artifact.weights_sha256),
    ):
        if value and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise NowcastNetConfigError(f"{name} SHA-256 is invalid")
    if profile.artifact.weights_uri and not profile.artifact.weights_sha256:
        raise NowcastNetConfigError("weights URI requires a SHA-256")
    if profile.artifact.weights_uri:
        expected_weights_path = Path(
            "/opt/rainpulse/nowcastnet/official-v1/data/checkpoints/mrms_model.ckpt"
        )
        if profile.weights_path() != expected_weights_path:
            raise NowcastNetConfigError(
                "NowcastNet weights URI differs from the frozen RP-026 runtime path"
            )
    if profile.activation.realtime_shadow_enabled:
        raise NowcastNetConfigError("RP-026 cannot enable realtime NowcastNet shadow inference")
    if profile.activation.product_publication_enabled:
        raise NowcastNetConfigError("RP-026 cannot enable NowcastNet product publication")
    if profile.activation.operational_eligible:
        raise NowcastNetConfigError("RP-026 cannot be operationally eligible")
