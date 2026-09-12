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
    if not np.array_equal(trusted, valid & ~reject) or np.any(eligible & ~trusted):
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
