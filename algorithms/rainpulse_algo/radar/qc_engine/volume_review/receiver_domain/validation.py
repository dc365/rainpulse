"""Validate the exact parent VOR/NMR view FIRST, then the new receiver delta."""
from collections.abc import Mapping
import numpy as np
from .config import ReceiverDomainConfig
from .core import DTYPES
from .disposition import apply, mutable_names


class ParentView(Mapping):
    def __init__(self, group):
        self.group = group
        self.keys_ = [k for k in group if not k.startswith("RDR_")]
    def __iter__(self): return iter(self.keys_)
    def __len__(self): return len(self.keys_)
    def __getitem__(self, key):
        if key not in self.keys_: raise KeyError(key)
        old = "RDR_BEFORE_"+key
        return self.group[old] if old in self.group else self.group[key]


def validate_serialized(group, attrs, parent_validator):
    cfg = ReceiverDomainConfig.model_validate(attrs.get("qc_receiver_domain_config", {}))
    if (attrs.get("qc_receiver_domain_version") != cfg.version or
            attrs.get("qc_receiver_domain_sha256") != cfg.digest or attrs.get("operational_eligible") is not False):
        raise ValueError("receiver configuration identity differs")
    view = ParentView(group)
    for k in mutable_names(view):
        if "RDR_BEFORE_"+k not in group:
            raise ValueError("missing exact receiver before-state " + k)
    clean = {k: v for k, v in attrs.items() if not k.startswith("qc_receiver_domain_")}
    parent_validator(view, clean)
    before = {k: np.array(view[k][:], copy=True) for k in view}
    evidence = {"RDR_"+k: np.asarray(group["RDR_"+k][:]) for k in DTYPES}
    predicted, _ = apply(before, evidence, cfg, low_quality_flag=attrs["qc_receiver_domain_low_quality_flag"])
    if set(predicted) != set(group):
        raise ValueError("unexpected/missing receiver output keys")
    for k, value in predicted.items():
        if value.dtype != group[k].dtype or not np.array_equal(value, np.asarray(group[k][:]), equal_nan=True):
            raise ValueError("unexplained receiver delta " + k)
