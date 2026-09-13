from __future__ import annotations

import numpy as np

from .decision import Action


def validate_sweep(group, attrs) -> None:
    if attrs.get("flag_definition_version") != "qc-flags-v2":
        raise ValueError("open-source QC requires v2 cause flags")
    if attrs.get("operational_eligible") is not False:
        raise ValueError("unaccepted open-source QC must remain non-operational")
    shape = group["VALID_MASK"].shape
    required = {
        "QC_ACTION": "uint8",
        "QC_DECISION_REASON": "uint16",
        "REFLECTIVITY_TRUST_MASK": "uint8",
        "QPE_ELIGIBLE_MASK": "uint8",
        "METEO_SCORE": "float32",
        "METEO_SCORE_AVAILABLE_MASK": "uint8",
        "RFI_CANDIDATE_MASK": "uint8",
        "DBZH_USABLE": "float32",
    }
    for name, dtype in required.items():
        if name not in group or group[name].shape != shape or group[name].dtype != np.dtype(dtype):
            raise ValueError(f"invalid open-source QC field {name}")
    for name in group:
        if name.endswith("_MASK"):
            values = group[name][:]
            if values.shape != shape or np.any((values != 0) & (values != 1)):
                raise ValueError(f"invalid QC mask {name}")
    action = group["QC_ACTION"][:]
    valid = group["VALID_MASK"][:] == 1
    trusted = group["REFLECTIVITY_TRUST_MASK"][:] == 1
    eligible = group["QPE_ELIGIBLE_MASK"][:] == 1
    flags = group["QC_FLAGS"][:]
    reject = action == Action.REJECT
    if np.any(action > Action.MISSING) or not np.array_equal(action == Action.MISSING, ~valid):
        raise ValueError("QC actions and original observation support disagree")
    quarantine = np.zeros(shape, bool)
    if attrs.get("qc_pipeline_version") == "qc-opensource-2.0.0":
        for field, dtype in {
            "RFI_OBJECT_ID": "uint32",
            "RFI_RISK_STATE": "uint8",
            "RFI_QUARANTINE_MASK": "uint8",
            "TEMPORAL_RFI_SAMPLE_COUNT": "uint8",
            "TEMPORAL_CANDIDATE_PERSISTENCE": "float32",
            "RFI_LINKED_OBSERVATION_MASK": "uint8",
            "RFI_BOUNDARY_OBSERVATION_MASK": "uint8",
            "OS_POL_RAW_MOMENT_COUNT": "uint8",
            "OS_POL_AXIAL_MOMENT_COUNT": "uint8",
            "OS_POL_TEXTURE_MOMENT_COUNT": "uint8",
        }.items():
            if (
                field not in group
                or group[field].shape != shape
                or group[field].dtype != np.dtype(dtype)
            ):
                raise ValueError(f"invalid RFI v2 field {field}")
        quarantine = group["RFI_QUARANTINE_MASK"][:] == 1
        state = group["RFI_RISK_STATE"][:]
        object_id = group["RFI_OBJECT_ID"][:]
        if np.any(state > 3) or not np.array_equal(state == 2, quarantine):
            raise ValueError("RFI risk states differ from quarantine")
        if np.any(quarantine & (reject | ~valid | eligible | (action != Action.DOWNWEIGHT))):
            raise ValueError("quarantined RFI must be withheld, not reported as a rejection")
        if np.any((state == 3) & ~reject) or np.any((state > 0) & (object_id == 0)):
            raise ValueError("RFI object/risk/rejection identity is inconsistent")
        if np.any(
            (group["RFI_BOUNDARY_OBSERVATION_MASK"][:] == 1)
            & (group["RFI_LINKED_OBSERVATION_MASK"][:] == 0)
        ):
            raise ValueError("boundary hypotheses cannot become original confirmed seeds")
        count = group["TEMPORAL_RFI_SAMPLE_COUNT"][:]
        persistence = group["TEMPORAL_CANDIDATE_PERSISTENCE"][:]
        if np.any(count > 3) or np.any(~np.isnan(persistence[count == 0])):
            raise ValueError("temporal support with zero samples cannot imply evidence")
        if (
            np.any(~np.isfinite(persistence[count > 0]))
            or np.any(persistence < 0)
            or np.any(persistence > 1)
        ):
            raise ValueError("invalid supported temporal persistence")
        if np.any((object_id > 0) & ~valid) or not np.array_equal(
            object_id > 0, group["RFI_CANDIDATE_MASK"][:] == 1
        ):
            raise ValueError("candidate identity cannot create observations")
        if np.any((group["RFI_LINKED_OBSERVATION_MASK"][:] == 1) & (object_id == 0)):
            raise ValueError("linked observations require a bounded object")
        for field in (
            "OS_POL_RAW_MOMENT_COUNT",
            "OS_POL_AXIAL_MOMENT_COUNT",
            "OS_POL_TEXTURE_MOMENT_COUNT",
        ):
            if np.any(group[field][:] > 3):
                raise ValueError("invalid polarization moment counts")
        if np.any((state == 3) & ((flags & np.uint32(8)) == 0)):
            raise ValueError("confirmed RFI lacks a radial cause flag")
    if not np.array_equal(trusted, valid & ~reject & ~quarantine) or np.any(eligible & ~trusted):
        raise ValueError("QC measurement trust is inconsistent with decisions")
    if np.any(reject & ((flags & np.uint32(32768)) == 0)):
        raise ValueError("rejected values must carry a downstream hard-reject flag")
    usable = group["DBZH_USABLE"][:]
    if np.any(~np.isfinite(usable[eligible])) or np.any(~np.isnan(usable[~eligible])):
        raise ValueError("untrusted observation leaked into quantitative field")
    score = group["METEO_SCORE"][:]
    supported = group["METEO_SCORE_AVAILABLE_MASK"][:] == 1
    if np.any(~np.isfinite(score[supported])) or np.any(~np.isnan(score[~supported])):
        raise ValueError("membership availability is inconsistent")
    if np.any((score[supported] < 0) | (score[supported] > 1)):
        raise ValueError("invalid meteorological membership")
    for name in ("PHIDP", "RHOHV", "ZDR", "VR", "SW", "SNR"):
        mask = group[f"{name}_TRUST_MASK"][:] == 1
        if np.any(mask & ~trusted):
            raise ValueError("a field cannot remain trusted inside rejected reflectivity")
        if name + "_RAW" in group and np.any(~np.isfinite(group[name + "_RAW"][:][mask])):
            raise ValueError("trusted field contains nonfinite values")
