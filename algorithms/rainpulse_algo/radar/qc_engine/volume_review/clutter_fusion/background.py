"""Use the attached episode model as evidence, never a second disposition."""
from datetime import datetime, timezone
from functools import lru_cache
import numpy as np
from ..data import array_digest
from ..episode_background.data import Sample
from ..episode_background.core import evaluate as episode_evaluate
from ..episode_background.io import load_background


def empty(shape,cfg=None):
    a={"CF_BG_"+k+"_MASK":np.zeros(shape,"uint8") for k in (
        "AVAILABLE","STABLE","MATCH","CURRENT_NONMET","ENHANCEMENT","TAIL_MATCH")}
    a["CF_BG_DISTANCE"]=np.full(shape,np.nan,"float32")
    a["CF_BG_FEATURE_COUNT"]=np.zeros(shape,"uint8")
    if cfg is not None and cfg.near_revision is not None:
        a["CF_BG_STATE"]=np.zeros(shape,"uint8")
        a["CF_BG_REASON"]=np.zeros(shape,"uint32")
        for key in ("DBZH_DEPARTURE_DB","MAP_AZ_ERROR_DEG","MAP_RANGE_ERROR_M"):
            a["CF_BG_"+key]=np.full(shape,np.nan,"float32")
    return a


@lru_cache(maxsize=2)
def load(path, digest, maximum_bytes):
    return load_background(path,digest,maximum_bytes=maximum_bytes)


def compare(sample, model, cfg):
    """Public pure bridge used by both worker and synthetic/background tests."""
    ev=episode_evaluate(sample,model,cfg.background)
    a=empty(sample.shape,cfg)
    for dst,src in (("AVAILABLE","MODEL_AVAILABLE"),("STABLE","STABLE_CORE"),
                    ("MATCH","BACKGROUND_MATCH"),("CURRENT_NONMET","CURRENT_NONMET"),
                    ("ENHANCEMENT","ENHANCEMENT"),("TAIL_MATCH","TAIL_STATE_MATCH")):
        a["CF_BG_"+dst+"_MASK"]=ev.arrays["EBG_"+src+"_MASK"].copy()
    a["CF_BG_DISTANCE"]=ev.arrays["EBG_MAX_DISTANCE"].copy()
    a["CF_BG_FEATURE_COUNT"]=ev.arrays["EBG_FEATURE_COUNT"].copy()
    if cfg.near_revision is not None:
        for key in ("STATE","REASON","DBZH_DEPARTURE_DB","MAP_AZ_ERROR_DEG","MAP_RANGE_ERROR_M"):
            a["CF_BG_"+key]=ev.arrays["EBG_"+key].copy()
    return a, ev.summary


def from_native(n):
    """Sorted raw view; identity must come from the actual normalized volume."""
    attrs=n.attrs
    for k in ("radar_id","scan_id","radar_config_version"):
        if not isinstance(attrs.get(k),str) or not attrs[k].strip():
            raise ValueError("background source missing identity: "+k)
    times=np.asarray(n.ray_time)
    stamp=attrs.get("volume_end_time_utc")
    if times.dtype.kind=="M" and not np.isnat(times).any():
        stamp=datetime.fromtimestamp(float(times.max().astype("datetime64[ns]").astype("int64"))/1e9,timezone.utc).isoformat()
    if stamp is None:raise ValueError("background target observation time missing")
    source=attrs.get("normalized_content_sha256") or array_digest({**n.fields,"azimuth":n.azimuth,"range":n.ranges,"elevation":n.elevation})
    # Processing identity is the same explicit identity used by the existing exporter.
    return Sample(radar_id=attrs["radar_id"].lower(),scan_id=attrs["scan_id"],sweep_id=n.name,
        processing_id=attrs["radar_config_version"],observed_at=stamp,source_sha256=source,
        azimuth=n.azimuth,elevation=n.elevation,ranges=n.ranges,fields=n.fields,
        available=n.field_available,geometry_good=n.geometry_good)


def for_native(n,cfg):
    binding=cfg.background.assets.get(str(n.attrs.get("radar_id","")).lower())
    if binding is None:
        return empty(n.shape,cfg),{"status":"NO_ASSET_BOUND","background_sha256":None}
    model=load(binding.path,binding.sha256,cfg.background.maximum_asset_bytes)
    a,summary=compare(from_native(n),model,cfg)
    return a,{**summary,"background_sha256":binding.sha256,"used_as_evidence_only":True}
