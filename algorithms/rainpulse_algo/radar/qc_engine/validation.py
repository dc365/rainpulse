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
    version7 = attrs.get("qc_pipeline_version") == "qc-opensource-7.0.0"
    version61 = version7 or attrs.get("qc_pipeline_version") == "qc-opensource-6.1.0"
    baseline7_reject, baseline7_quarantine = reject, quarantine
    graph_domain = np.zeros(shape, bool)
    if version7:
        from .evidence_validation import validate_evidence_fields

        graph_domain = validate_evidence_fields(group, valid, reject)
        baseline7_reject = group["V7_BASELINE_REJECT_MASK"][:] == 1
        baseline7_quarantine = group["V7_BASELINE_QUARANTINE_MASK"][:] == 1
    version6 = version61 or attrs.get("qc_pipeline_version") == "qc-opensource-6.0.0"
    version5 = version6 or attrs.get("qc_pipeline_version") == "qc-opensource-5.0.0"
    version4 = version5 or attrs.get("qc_pipeline_version") == "qc-opensource-4.0.0"
    version3 = version4 or attrs.get("qc_pipeline_version") == "qc-opensource-3.0.0"
    if attrs.get("qc_pipeline_version") in {
        "qc-opensource-2.0.0",
        "qc-opensource-3.0.0",
        "qc-opensource-4.0.0",
        "qc-opensource-5.0.0",
        "qc-opensource-6.0.0",
        "qc-opensource-6.1.0",
        "qc-opensource-7.0.0",
    }:
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
        peripheral = np.zeros(shape, bool)
        if version3:
            for field, dtype in {
                "RFI_PERIPHERAL_REVIEW_MASK": "uint8",
                "RFI_V3_EVIDENCE_SCORE": "float32",
                "RFI_V3_CONFIRMATION_MASK": "uint8",
                "RFI_V3_PERIPHERAL_USED_MASK": "uint8",
            }.items():
                if (
                    field not in group
                    or group[field].shape != shape
                    or group[field].dtype != np.dtype(dtype)
                ):
                    raise ValueError(f"invalid RFI v3 field {field}")
            peripheral = group["RFI_PERIPHERAL_REVIEW_MASK"][:] == 1
            if np.any(peripheral & (~valid | (object_id > 0))):
                raise ValueError("peripheral review must be observed and outside object membership")
            if np.any((group["RFI_V3_CONFIRMATION_MASK"][:] == 1) & (state != 3)):
                raise ValueError("V3 confirmation must have confirmed radial state")
        paper_domain = np.zeros(shape, bool)
        if version4:
            from .paper_validation import validate_paper_fields

            if version5:
                from .crossradar_validation import validate_crossradar_fields

                if version6:
                    from .residual_validation import validate_residual_fields

                    extra_domain = validate_residual_fields(
                        group, valid,
                        baseline7_reject if version7 else reject,
                        baseline7_quarantine if version7 else quarantine,
                    ) | graph_domain
                    extra_domain |= validate_crossradar_fields(
                        group,
                        valid,
                        group["V6_BASELINE_REJECT_MASK"][:] == 1,
                        group["V6_BASELINE_QUARANTINE_MASK"][:] == 1,
                    )
                else:
                    extra_domain = validate_crossradar_fields(group, valid, reject, quarantine)
                paper_domain = (
                    validate_paper_fields(
                        group,
                        valid,
                        group["V5_BASELINE_REJECT_MASK"][:] == 1,
                        group["V5_BASELINE_QUARANTINE_MASK"][:] == 1,
                    )
                    | extra_domain
                )
            else:
                paper_domain = validate_paper_fields(group, valid, reject, quarantine)
        if np.any((state == 3) & ~reject) or np.any(
            (state > 0) & (object_id == 0) & ~peripheral & ~paper_domain
        ):
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
    if version61:
        for name in ("V61_POL_RELIABLE_MASK", "V61_SNR_AVAILABLE_MASK", "V61_REVIEW_OUTCOME"):
            if (
                name not in group
                or group[name].shape != shape
                or group[name].dtype != np.dtype("uint8")
            ):
                raise ValueError(f"invalid 6.1 diagnostic field {name}")
        outcome = group["V61_REVIEW_OUTCOME"][:]
        if np.any(outcome > 5) or not np.array_equal(outcome == 5, ~valid):
            raise ValueError("6.1 outcome changed original missing semantics")
        if np.any((outcome == 3) & ~(baseline7_quarantine if version7 else quarantine)) or np.any(
            (outcome == 4) & ~(baseline7_reject if version7 else reject)
        ):
            raise ValueError("6.1 outcome disagrees with final measurement disposition")
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
