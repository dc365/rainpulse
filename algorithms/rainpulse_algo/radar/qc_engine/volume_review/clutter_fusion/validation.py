"""Recreate the exact pre-fusion view, run the unchanged parent, then check delta."""
from collections.abc import Mapping
import numpy as np
from .config import ClutterFusionConfig
from .disposition import apply,mutable_names


class BeforeView(Mapping):
    def __init__(self,group):self.group=group;self.keys_=[k for k in group if not k.startswith("CF_")]
    def __iter__(self):return iter(self.keys_)
    def __len__(self):return len(self.keys_)
    def __getitem__(self,key):
        if key not in self.keys_:raise KeyError(key)
        source="CF_BEFORE_"+key
        return self.group[source] if source in self.group else self.group[key]


def validate_serialized(group,attrs,parent_validator):
    cfg=ClutterFusionConfig.model_validate(attrs.get("qc_clutter_fusion_config",{}))
    if attrs.get("qc_clutter_fusion_version")!=cfg.version or attrs.get("qc_clutter_fusion_sha256")!=cfg.digest or attrs.get("operational_eligible") is not False:
        raise ValueError("fusion identity/config mismatch")
    view=BeforeView(group)
    for k in mutable_names(view):
        if "CF_BEFORE_"+k not in group:raise ValueError("missing pre-fusion state: "+k)
    clean={k:v for k,v in attrs.items() if not k.startswith("qc_clutter_fusion_")}
    parent_validator(view,clean)
    baseline={k:np.asarray(view[k][:]) for k in view}
    outputs={"CF_CR_WITHHELD_MASK","CF_QUARANTINE_MASK","CF_MIXED_CR_WITHHELD_MASK","CF_DERIVED_INVALIDATION_MASK"}
    evidence={k:np.asarray(group[k][:]) for k in group if k.startswith("CF_") and not k.startswith("CF_BEFORE_") and k not in outputs}
    out,_=apply(baseline,evidence,cfg,low_quality_flag=attrs["qc_clutter_fusion_low_quality_flag"])
    if set(out)!=set(group):raise ValueError("unexpected or missing fusion output fields")
    for k,expected in out.items():
        actual=np.asarray(group[k][:])
        if actual.dtype!=expected.dtype or not np.array_equal(actual,expected,equal_nan=True):
            raise ValueError("unexplained fusion change: "+k)
