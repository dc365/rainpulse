from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_RADAR_BAND = "S"
SUPPORTED_REFERENCE_ROLES = (
    "coefficient_fitting",
    "independent_validation",
)
SUPPORTED_TRUTH_KINDS = (
    "gauge_accumulation_1h",
    "reference_qpe_1h",
)
SUPPORTED_COEFFICIENT_STATE = "verified_shadow_use"
SUPPORTED_COEFFICIENT_SOURCE = "frozen_shadow_coefficient_table"


class CalibrationInputError(ValueError):
    """Raised when calibration inputs or provenance violate the C2-P3 boundary."""


@dataclass(frozen=True)
class CalibrationReferencePolicy:
    required_roles: tuple[str, ...]
    allowed_truth_kinds: tuple[str, ...]
    minimum_fitting_processes: int
    minimum_validation_processes: int


@dataclass(frozen=True)
class CalibrationProfile:
    profile_version: str
    artifact_contract_version: str
    source_relative_bias_artifact_contract_version: str
    source_attenuation_artifact_contract_version: str
    radar_band: str
    reference_policy: CalibrationReferencePolicy
    worker_integration_enabled: bool
    qi_calibration_enabled: bool
    required_gate: str


@dataclass(frozen=True)
class CalibrationReferenceEntry:
    process_id: str
    case_id: str
    role: str
    truth_kind: str
    radar_ids: tuple[str, ...]
    start_time_utc: str
    end_time_utc: str
    reference_uri: str
    source_sha256: str


@dataclass(frozen=True)
class CalibrationReferenceManifest:
    schema_version: str
    manifest_version: str
    source_calibration_profile_version: str
    generated_at: str
    radar_band: str
    references: tuple[CalibrationReferenceEntry, ...]
    fitting_process_ids: tuple[str, ...]
    validation_process_ids: tuple[str, ...]
    fitting_case_ids: tuple[str, ...]
    validation_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class CoefficientTableEntry:
    radar_id: str
    coefficient_state: str
    coefficient_a: float
    exponent_b: float
    applicable_temperature_range_c: tuple[float, float]
    fitted_process_ids: tuple[str, ...]
    validation_process_ids: tuple[str, ...]


@dataclass(frozen=True)
class CoefficientTable:
    schema_version: str
    table_version: str
    artifact_contract_version: str
    source_calibration_profile_version: str
    reference_manifest_version: str
    radar_band: str
    entries: tuple[CoefficientTableEntry, ...]


@dataclass(frozen=True)
class ResolvedAttenuationCoefficients:
    source: str
    coefficient_a: float
    exponent_b: float
    coefficient_table_version: str
    radar_id: str
    radar_band: str
    temperature_c: float


def load_calibration_profile(path: str | Path) -> CalibrationProfile:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        reference_policy = raw["reference_policy"]
        activation = raw["activation"]
        profile = CalibrationProfile(
            profile_version=str(raw["profile_version"]),
            artifact_contract_version=str(raw["artifact_contract_version"]),
            source_relative_bias_artifact_contract_version=str(
                raw["source_relative_bias_artifact_contract_version"]
            ),
            source_attenuation_artifact_contract_version=str(
                raw["source_attenuation_artifact_contract_version"]
            ),
            radar_band=str(raw["radar_band"]),
            reference_policy=CalibrationReferencePolicy(
                required_roles=tuple(str(item) for item in reference_policy["required_roles"]),
                allowed_truth_kinds=tuple(
                    str(item) for item in reference_policy["allowed_truth_kinds"]
                ),
                minimum_fitting_processes=int(reference_policy["minimum_fitting_processes"]),
                minimum_validation_processes=int(reference_policy["minimum_validation_processes"]),
            ),
            worker_integration_enabled=bool(activation["worker_integration_enabled"]),
            qi_calibration_enabled=bool(activation["qi_calibration_enabled"]),
            required_gate=str(activation["required_gate"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationInputError(
            f"invalid calibration profile {profile_path}: {error}"
        ) from error
    _validate_profile(profile)
    return profile


def load_calibration_reference_manifest(
    path: str | Path,
    *,
    profile: CalibrationProfile,
) -> CalibrationReferenceManifest:
    manifest_path = Path(path)
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        references = tuple(_reference_entry(item) for item in raw["references"])
        fitting_process_ids = _sorted_unique(
            entry.process_id
            for entry in references
            if entry.role == profile.reference_policy.required_roles[0]
        )
        validation_process_ids = _sorted_unique(
            entry.process_id
            for entry in references
            if entry.role == profile.reference_policy.required_roles[1]
        )
        fitting_case_ids = _sorted_unique(
            entry.case_id
            for entry in references
            if entry.role == profile.reference_policy.required_roles[0]
        )
        validation_case_ids = _sorted_unique(
            entry.case_id
            for entry in references
            if entry.role == profile.reference_policy.required_roles[1]
        )
        manifest = CalibrationReferenceManifest(
            schema_version=str(raw["schema_version"]),
            manifest_version=str(raw["manifest_version"]),
            source_calibration_profile_version=str(raw["source_calibration_profile_version"]),
            generated_at=str(raw["generated_at"]),
            radar_band=str(raw["radar_band"]),
            references=references,
            fitting_process_ids=fitting_process_ids,
            validation_process_ids=validation_process_ids,
            fitting_case_ids=fitting_case_ids,
            validation_case_ids=validation_case_ids,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationInputError(
            f"invalid calibration reference manifest {manifest_path}: {error}"
        ) from error
    _validate_reference_manifest(manifest, profile)
    return manifest


def load_coefficient_table(
    path: str | Path,
    *,
    profile: CalibrationProfile | None = None,
    reference_manifest: CalibrationReferenceManifest | None = None,
) -> CoefficientTable:
    table_path = Path(path)
    try:
        raw = yaml.safe_load(table_path.read_text(encoding="utf-8"))
        table = CoefficientTable(
            schema_version=str(raw["schema_version"]),
            table_version=str(raw["table_version"]),
            artifact_contract_version=str(raw["artifact_contract_version"]),
            source_calibration_profile_version=str(raw["source_calibration_profile_version"]),
            reference_manifest_version=str(raw["reference_manifest_version"]),
            radar_band=str(raw["radar_band"]),
            entries=tuple(_coefficient_entry(item) for item in raw["entries"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationInputError(
            f"invalid attenuation coefficient table {table_path}: {error}"
        ) from error
    _validate_coefficient_table(
        table,
        profile=profile,
        reference_manifest=reference_manifest,
    )
    return table


def resolve_attenuation_coefficients(
    table: CoefficientTable,
    *,
    radar_id: str,
    radar_band: str,
    temperature_c: float,
) -> ResolvedAttenuationCoefficients:
    if not radar_id:
        raise CalibrationInputError("radar_id must be non-empty")
    if radar_band != table.radar_band:
        raise CalibrationInputError("radar band differs from coefficient table")
    entry = next(
        (item for item in table.entries if item.radar_id.lower() == radar_id.lower()),
        None,
    )
    if entry is None:
        raise CalibrationInputError(f"no attenuation coefficients configured for radar {radar_id}")
    if entry.coefficient_state != SUPPORTED_COEFFICIENT_STATE:
        raise CalibrationInputError("unsupported coefficient state")
    minimum_temperature, maximum_temperature = entry.applicable_temperature_range_c
    if not minimum_temperature <= float(temperature_c) <= maximum_temperature:
        raise CalibrationInputError("temperature is outside the coefficient applicability range")
    return ResolvedAttenuationCoefficients(
        source=SUPPORTED_COEFFICIENT_SOURCE,
        coefficient_a=entry.coefficient_a,
        exponent_b=entry.exponent_b,
        coefficient_table_version=table.table_version,
        radar_id=entry.radar_id,
        radar_band=table.radar_band,
        temperature_c=float(temperature_c),
    )


def _reference_entry(value: Any) -> CalibrationReferenceEntry:
    item = _mapping(value, "reference entry")
    start_time = _parse_utc_datetime(item["start_time_utc"], "start_time_utc")
    end_time = _parse_utc_datetime(item["end_time_utc"], "end_time_utc")
    if end_time <= start_time:
        raise CalibrationInputError(
            "reference entry end_time_utc must be later than start_time_utc"
        )
    source_sha256 = str(item["source_sha256"])
    _validate_sha256(source_sha256, "reference entry source_sha256")
    return CalibrationReferenceEntry(
        process_id=_non_empty_string(item["process_id"], "reference entry process_id"),
        case_id=_non_empty_string(item["case_id"], "reference entry case_id"),
        role=_non_empty_string(item["role"], "reference entry role"),
        truth_kind=_non_empty_string(item["truth_kind"], "reference entry truth_kind"),
        radar_ids=_string_tuple(item["radar_ids"], "reference entry radar_ids"),
        start_time_utc=_format_utc(start_time),
        end_time_utc=_format_utc(end_time),
        reference_uri=_non_empty_string(item["reference_uri"], "reference entry reference_uri"),
        source_sha256=source_sha256,
    )


def _coefficient_entry(value: Any) -> CoefficientTableEntry:
    item = _mapping(value, "coefficient table entry")
    radar_id = _non_empty_string(item["radar_id"], "coefficient table entry radar_id")
    coefficient_state = _non_empty_string(
        item["coefficient_state"],
        "coefficient table entry coefficient_state",
    )
    temperature_range = _temperature_range(
        item["applicable_temperature_range_c"],
        "coefficient table entry applicable_temperature_range_c",
    )
    fitted_process_ids = _string_tuple(
        item["fitted_process_ids"],
        "coefficient table entry fitted_process_ids",
        unique=True,
    )
    validation_process_ids = _string_tuple(
        item["validation_process_ids"],
        "coefficient table entry validation_process_ids",
        unique=True,
    )
    if set(fitted_process_ids) & set(validation_process_ids):
        raise CalibrationInputError(
            "coefficient table fitted/validation process ids must be disjoint"
        )
    coefficient_a = float(item["coefficient_a"])
    exponent_b = float(item["exponent_b"])
    return CoefficientTableEntry(
        radar_id=radar_id,
        coefficient_state=coefficient_state,
        coefficient_a=coefficient_a,
        exponent_b=exponent_b,
        applicable_temperature_range_c=temperature_range,
        fitted_process_ids=fitted_process_ids,
        validation_process_ids=validation_process_ids,
    )


def _validate_profile(profile: CalibrationProfile) -> None:
    if not profile.profile_version:
        raise CalibrationInputError("calibration profile_version must be non-empty")
    if profile.artifact_contract_version != "1.0":
        raise CalibrationInputError("unsupported calibration artifact contract version")
    if profile.source_relative_bias_artifact_contract_version != "1.0":
        raise CalibrationInputError("unsupported source relative-bias artifact contract version")
    if profile.source_attenuation_artifact_contract_version != "1.0":
        raise CalibrationInputError("unsupported source attenuation artifact contract version")
    if profile.radar_band != SUPPORTED_RADAR_BAND:
        raise CalibrationInputError("current calibration shadow boundary is frozen for S band")
    if profile.reference_policy.required_roles != SUPPORTED_REFERENCE_ROLES:
        raise CalibrationInputError("unsupported calibration reference roles")
    if tuple(sorted(profile.reference_policy.allowed_truth_kinds)) != tuple(
        sorted(SUPPORTED_TRUTH_KINDS)
    ):
        raise CalibrationInputError("unsupported calibration truth kinds")
    if profile.reference_policy.minimum_fitting_processes <= 0:
        raise CalibrationInputError("minimum_fitting_processes must be positive")
    if profile.reference_policy.minimum_validation_processes <= 0:
        raise CalibrationInputError("minimum_validation_processes must be positive")
    if profile.worker_integration_enabled:
        raise CalibrationInputError("calibration worker integration must remain disabled")
    if profile.qi_calibration_enabled:
        raise CalibrationInputError("QI calibration must remain disabled")
    if not profile.required_gate:
        raise CalibrationInputError("required_gate must be non-empty")


def _validate_reference_manifest(
    manifest: CalibrationReferenceManifest,
    profile: CalibrationProfile,
) -> None:
    _validate_profile(profile)
    if manifest.schema_version != "1.0":
        raise CalibrationInputError("unsupported calibration reference manifest schema")
    if not manifest.manifest_version:
        raise CalibrationInputError("calibration reference manifest_version must be non-empty")
    if manifest.source_calibration_profile_version != profile.profile_version:
        raise CalibrationInputError(
            "calibration reference manifest source profile version differs from "
            "the selected profile"
        )
    if manifest.radar_band != profile.radar_band:
        raise CalibrationInputError("calibration reference manifest radar band differs")
    _parse_utc_datetime(manifest.generated_at, "generated_at")
    if not manifest.references:
        raise CalibrationInputError("calibration reference manifest must contain references")

    allowed_truth_kinds = set(profile.reference_policy.allowed_truth_kinds)
    required_roles = set(profile.reference_policy.required_roles)
    seen_roles = {entry.role for entry in manifest.references}
    if not required_roles <= seen_roles:
        raise CalibrationInputError("calibration reference manifest is missing required roles")
    for entry in manifest.references:
        if entry.role not in required_roles:
            raise CalibrationInputError(
                "calibration reference manifest contains an unsupported role"
            )
        if entry.truth_kind not in allowed_truth_kinds:
            raise CalibrationInputError(
                "calibration reference manifest contains an unsupported truth kind"
            )
    if set(manifest.fitting_process_ids) & set(manifest.validation_process_ids):
        raise CalibrationInputError(
            "calibration reference manifest fitting and validation splits must be "
            "disjoint by process_id"
        )
    if set(manifest.fitting_case_ids) & set(manifest.validation_case_ids):
        raise CalibrationInputError(
            "calibration reference manifest fitting and validation splits must be "
            "disjoint by case_id"
        )
    if len(manifest.fitting_process_ids) < profile.reference_policy.minimum_fitting_processes:
        raise CalibrationInputError(
            "calibration reference manifest fitting split is below the minimum process count"
        )
    if len(manifest.validation_process_ids) < profile.reference_policy.minimum_validation_processes:
        raise CalibrationInputError(
            "calibration reference manifest validation split is below the minimum process count"
        )


def _validate_coefficient_table(
    table: CoefficientTable,
    *,
    profile: CalibrationProfile | None,
    reference_manifest: CalibrationReferenceManifest | None,
) -> None:
    if table.schema_version != "1.0":
        raise CalibrationInputError("unsupported attenuation coefficient table schema")
    if not table.table_version:
        raise CalibrationInputError("attenuation coefficient table_version must be non-empty")
    if table.artifact_contract_version != "1.0":
        raise CalibrationInputError("unsupported attenuation coefficient table contract version")
    if not table.reference_manifest_version:
        raise CalibrationInputError("reference_manifest_version must be non-empty")
    if table.radar_band != SUPPORTED_RADAR_BAND:
        raise CalibrationInputError("current attenuation coefficient table is frozen for S band")
    if not table.entries:
        raise CalibrationInputError("attenuation coefficient table must contain entries")
    radar_ids = [entry.radar_id.lower() for entry in table.entries]
    if len(radar_ids) != len(set(radar_ids)):
        raise CalibrationInputError("attenuation coefficient table radar_ids must be unique")
    if profile is not None:
        _validate_profile(profile)
        if table.source_calibration_profile_version != profile.profile_version:
            raise CalibrationInputError(
                "attenuation coefficient table source profile version differs from the "
                "selected profile"
            )
        minimum_fitting_processes = profile.reference_policy.minimum_fitting_processes
        minimum_validation_processes = profile.reference_policy.minimum_validation_processes
    else:
        if not table.source_calibration_profile_version:
            raise CalibrationInputError(
                "attenuation coefficient table source_calibration_profile_version must be non-empty"
            )
        minimum_fitting_processes = 1
        minimum_validation_processes = 1
    fitting_process_ids: set[str] = set()
    validation_process_ids: set[str] = set()
    if reference_manifest is not None:
        fitting_process_ids = set(reference_manifest.fitting_process_ids)
        validation_process_ids = set(reference_manifest.validation_process_ids)
    for entry in table.entries:
        if entry.coefficient_state != SUPPORTED_COEFFICIENT_STATE:
            raise CalibrationInputError("unsupported attenuation coefficient state")
        if entry.coefficient_a <= 0.0 or entry.exponent_b <= 0.0:
            raise CalibrationInputError("attenuation coefficients must be positive")
        if len(entry.fitted_process_ids) < minimum_fitting_processes:
            raise CalibrationInputError(
                "attenuation coefficient table entry is below the minimum fitting process count"
            )
        if len(entry.validation_process_ids) < minimum_validation_processes:
            raise CalibrationInputError(
                "attenuation coefficient table entry is below the minimum validation process count"
            )
        if reference_manifest is not None:
            if not set(entry.fitted_process_ids) <= fitting_process_ids:
                raise CalibrationInputError(
                    "attenuation coefficient table fitted process ids fall outside the "
                    "reference manifest"
                )
            if not set(entry.validation_process_ids) <= validation_process_ids:
                raise CalibrationInputError(
                    "attenuation coefficient table validation process ids fall outside the "
                    "reference manifest"
                )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CalibrationInputError(f"{label} must be an object")
    return value


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationInputError(f"{label} must be a non-empty string")
    return value.strip()


def _string_tuple(
    value: Any,
    label: str,
    *,
    unique: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CalibrationInputError(f"{label} must be a list of strings")
    normalized = tuple(_non_empty_string(item, label) for item in value)
    if not normalized:
        raise CalibrationInputError(f"{label} must be non-empty")
    if unique and len(normalized) != len(set(normalized)):
        raise CalibrationInputError(f"{label} must contain unique values")
    return normalized


def _temperature_range(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CalibrationInputError(f"{label} must be a two-value list")
    if len(value) != 2:
        raise CalibrationInputError(f"{label} must contain exactly two values")
    minimum_temperature = float(value[0])
    maximum_temperature = float(value[1])
    if maximum_temperature <= minimum_temperature:
        raise CalibrationInputError(f"{label} must be strictly increasing")
    return (minimum_temperature, maximum_temperature)


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise CalibrationInputError(f"{label} must be a lowercase SHA-256 hex digest")


def _parse_utc_datetime(value: Any, label: str) -> datetime:
    text = _non_empty_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise CalibrationInputError(f"{label} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None:
        raise CalibrationInputError(f"{label} must include timezone information")
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sorted_unique(values: Sequence[str] | Any) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))
