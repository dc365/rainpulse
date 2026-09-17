"""Validate baseline first, then baseline plus bounded RC1 additions."""
import numpy as np

from .disposition import BASE_FIELDS


class BaselineView:
    def __init__(self, group):
        self.group = group

    def __getitem__(self, name):
        key = "RC1_BASE_" + name
        return self.group[key] if key in self.group else self.group[name]

    def __iter__(self):
        return iter(k for k in self.group if not k.startswith("RC1_"))

    def __contains__(self, key):
        return key in self.group


def validate(group, attrs, legacy):
    shape = group["VALID_MASK"].shape
    for name in (*BASE_FIELDS, "QUALITY_INDEX", "QC_FLAGS", "LOW_QUALITY_MASK"):
        if "RC1_BASE_" + name not in group:
            raise ValueError("missing finalized baseline field: " + name)
    legacy(BaselineView(group), {**attrs, "qc_pipeline_version": "qc-opensource-7.3.6"})
    for name in group:
        if name.startswith("RC1_"):
            a = group[name][:]
            if a.shape != shape:
                raise ValueError("RC1 field geometry differs")
            if name.endswith("_MASK") and not np.isin(a, [0, 1]).all():
                raise ValueError("nonbinary RC1 mask")
    valid = group["VALID_MASK"][:] == 1
    add = group["RC1_ADDED_QUARANTINE_MASK"][:] == 1
    prop = group["RC1_PROPOSED_MASK"][:] == 1
    if np.any(add & (~valid | ~prop)):
        raise ValueError("addition outside observed proposals")
    mode = group["RC1_MODE"][:]
    if np.any(mode > 1) or (not mode.any() and add.any()):
        raise ValueError("audit mode changed actions")
    if np.any(group["RC1_BUDGET_STOP_MASK"][:] == 1) and add.any():
        raise ValueError("budget stop applied a partial result")
    action = group["QC_ACTION"][:]
    old = group["RC1_BASE_QC_ACTION"][:]
    if not np.array_equal(action, np.where(add, 1, old)):
        raise ValueError("RC1 changed unrecorded actions")
    for name in ("RFI_QUARANTINE_MASK", "QPE_ELIGIBLE_MASK", "REFLECTIVITY_TRUST_MASK"):
        before = group["RC1_BASE_" + name][:]
        after = group[name][:]
        expected = np.where(add, 1 if name == "RFI_QUARANTINE_MASK" else 0, before)
        if not np.array_equal(after, expected):
            raise ValueError("RC1 mask projection differs")
    q = group["QUALITY_INDEX"][:]
    bq = group["RC1_BASE_QUALITY_INDEX"][:]
    if not np.array_equal(q[~add], bq[~add], equal_nan=True) or np.any(q[add] > bq[add]):
        raise ValueError("quality changed outside RC1 or increased")
    if np.any(q[add] >= 0.5):
        raise ValueError("quarantine retained quantitative quality")
    value = group["DBZH_USABLE"][:]
    before = group["RC1_BASE_DBZH_USABLE"][:]
    if not np.array_equal(value[~add], before[~add], equal_nan=True) or np.any(~np.isnan(value[add])):
        raise ValueError("RC1 invented or retained withheld quantitative values")
    flags, previous = group["QC_FLAGS"][:], group["RC1_BASE_QC_FLAGS"][:]
    if np.any((flags & previous) != previous) or np.any(flags[~add] != previous[~add]):
        raise ValueError("RC1 changed unrecorded cause flags")
