"""Validate the OLD chain against its exact pre-extension values, then the delta.

Do not weaken NP/OC1/V7 validators to accommodate a later stage. The public
validator recurses once on this view without extension attrs, preserving the
legacy checks byte-for-byte, then validates the actual new action projection.
"""
from collections.abc import Mapping
import numpy as np
from .disposition import validate_fields, DERIVED_FIELDS, derived_invalidation


class LegacyView(Mapping):
    def __init__(self,group):
        self.group=group
        self.keys_=[k for k in group if not k.startswith(("VOR_","CR_")) and k!="REFLECTIVITY_ELIGIBLE_FOR_CR"]
    def __iter__(self):return iter(self.keys_)
    def __len__(self):return len(self.keys_)
    def __getitem__(self,key):
        if key not in self.keys_:raise KeyError(key)
        source="VOR_BEFORE_"+key
        return self.group[source] if source in self.group else self.group[key]


def validate_serialized(group,attrs,legacy_validator):
    if attrs.get("qc_receiver_domain_version") is not None or any(k.startswith("RDR_") for k in group):
        from .receiver_domain.validation import validate_serialized as validate_receiver
        return validate_receiver(group, attrs, lambda g, a: validate_serialized(g, a, legacy_validator))
    if attrs.get("qc_near_measurement_version") is not None or any(k.startswith("NMR_") for k in group):
        from .near_measurement.validation import validate_serialized as validate_near
        return validate_near(group, attrs, lambda g, a: validate_serialized(g, a, legacy_validator))
    if attrs.get("qc_volume_review_version")!="volume-object-review-20260919-v1" or attrs.get("operational_eligible") is not False:
        raise ValueError("unsupported volume extension identity")
    if attrs.get("qc_volume_review_mode") not in ("audit","experiment_quarantine"):
        raise ValueError("unsupported volume extension mode")
    # Some fields are handled by QCSweep rather than optional arrays. Require their
    # exact before-state as well; no synthetic baseline may be silently assumed.
    expected={"QC_ACTION","REFLECTIVITY_TRUST_MASK","QPE_ELIGIBLE_MASK","DBZH_USABLE",
              "QC_FLAGS","QUALITY_INDEX","LOW_QUALITY_MASK"}
    expected.update(k for k in group if k.endswith("_TRUST_MASK") and not k.startswith("VOR_"))
    expected.update(k for k in ("P2_ADMIN_PENALTY_REMOVED_MASK","QI_METEO","QI_INTERFERENCE") if k in group)
    expected.update(k for k in DERIVED_FIELDS if k in group)
    for k in expected:
        if "VOR_BEFORE_"+k not in group:
            raise ValueError("missing pre-volume provenance "+k)
    for alias, key in (("VOR_BASELINE_TRUST_MASK", "REFLECTIVITY_TRUST_MASK"),
                       ("VOR_BASELINE_QPE_MASK", "QPE_ELIGIBLE_MASK"),
                       ("VOR_BASELINE_ACTION", "QC_ACTION")):
        if alias not in group or not np.array_equal(group[alias][:],group["VOR_BEFORE_"+key][:]):
            raise ValueError("volume baseline alias differs: "+alias)
    clean={k:v for k,v in attrs.items() if not k.startswith(("qc_volume_review_","volume_","cr_"))}
    legacy_validator(LegacyView(group),clean)
    obs=np.asarray(group['VALID_MASK'][:])==1
    reject=np.asarray(group['QC_ACTION'][:])==2
    q=validate_fields(group,obs,reject)
    if attrs["qc_volume_review_mode"]=="audit" and q.any():
        raise ValueError("audit caused isolation")
    cap=float(attrs["volume_quarantine_quality"]);flag=np.uint32(attrs["volume_low_quality_flag"])
    if not 0<=cap<.5 or flag==0:
        raise ValueError("invalid volume disposition metadata")
    derived=derived_invalidation(np.asarray(group["VOR_BASELINE_TRUST_MASK"][:])==1,q)
    if "VOR_DERIVED_INVALIDATION_MASK" not in group or not np.array_equal(
            np.asarray(group["VOR_DERIVED_INVALIDATION_MASK"][:]),derived.astype("uint8")):
        raise ValueError("derived-field provenance differs")
    for key in expected:
        before=np.array(group['VOR_BEFORE_'+key][:],copy=True)
        after=np.asarray(group[key][:])
        if before.shape!=obs.shape or before.dtype!=after.dtype:
            raise ValueError("before-state geometry/dtype differs")
        if key in DERIVED_FIELDS:before[derived]=0 if key.endswith('_MASK') else np.nan
        elif key=='QC_ACTION':before[q]=1
        elif key=='QC_FLAGS':before[q]|=flag
        elif key in ('QUALITY_INDEX','QI_METEO','QI_INTERFERENCE'):before[q]=np.minimum(before[q],cap)
        elif key=='LOW_QUALITY_MASK':before[q]=1
        elif key=='DBZH_USABLE':before[q]=np.nan
        else:before[q]=0
        if not np.array_equal(before,after,equal_nan=True):
            raise ValueError("unexplained volume-stage change "+key)
    cr=np.asarray(group['REFLECTIVITY_ELIGIBLE_FOR_CR'][:])==1
    expected_cr=(np.asarray(group['VOR_BASELINE_TRUST_MASK'][:])==1)&obs&~q&(np.asarray(group['VOR_STATE'][:])!=4)
    if attrs['cr_unknown_policy']=='withhold':expected_cr &= np.asarray(group['VOR_UNKNOWN_MASK'][:])==0
    elif attrs['cr_unknown_policy']!='retain_with_risk':raise ValueError('unknown CR policy')
    if not np.array_equal(cr,expected_cr):raise ValueError('CR projection differs from explicit policy')
    return None
