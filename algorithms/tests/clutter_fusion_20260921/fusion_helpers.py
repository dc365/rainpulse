from dataclasses import dataclass,replace
from types import SimpleNamespace as NS
import numpy as np
from volume_review.data import Sweep
from volume_review.config import VolumeReviewConfig
from volume_review.clutter_fusion.config import ClutterFusionConfig


def cfg(**kw):return ClutterFusionConfig(depolarization_backend="numpy_reference",**kw)


def scene(*,name="sweep_000",kind="bio",nr=12,ng=100,el=.5,time=0.,missing=(),z=10.,rho=.7,snr=16.,zdr=4.5,seed=1):
    shape=(nr,ng);values={"DBZH":z,"RHOHV":rho,"SNR":snr,"ZDR":zdr,"PHIDP":2.}
    fields={k:np.full(shape,v,"float32") for k,v in values.items() if k not in missing}
    if kind=="bio" and "PHIDP" in fields:fields["PHIDP"][:]=np.random.default_rng(seed).uniform(0,360,shape)
    if kind=="ground":
        fields["DBZH"][:]=10.+np.tile(np.array([0,12,0,-8],"float32"),ng//4+1)[:ng]
        fields["VR"]=np.full(shape,.1,"float32");fields["SW"]=np.full(shape,.3,"float32")
    gap=np.zeros(nr,bool);gap[-1]=True
    return Sweep(name,np.arange(nr,dtype=float),np.full(nr,el),2000.+np.arange(ng)*250,
        fields,{k:np.isfinite(v) for k,v in fields.items()},np.ones(nr,bool),gap,np.full(nr,time) if time is not None else None)


def baseline(s):
    obs=s.observed;z=s.fields['DBZH']
    a={"DBZH_RAW":z.copy(),"DBZH_QC":z.copy(),"DBZH_USABLE":np.where(obs,z,np.nan).astype("float32"),
       "QC_FLAGS":np.zeros(s.shape,"uint32"),"QUALITY_INDEX":np.full(s.shape,.8,"float32"),
       "QC_ACTION":np.where(obs,0,3).astype("uint8"),"LOW_QUALITY_MASK":np.zeros(s.shape,"uint8"),
       "CR_UNCERTAIN_MASK":np.zeros(s.shape,"uint8"),"CR_QUALIFICATION_REASON":obs.astype("uint16")}
    for k in ("VALID_MASK","REFLECTIVITY_TRUST_MASK","QPE_ELIGIBLE_MASK","REFLECTIVITY_ELIGIBLE_FOR_CR"):a[k]=obs.astype("uint8")
    for k,v in s.fields.items():
        a[k+"_RAW"]=v.copy();a[k+"_TRUST_MASK"]=(s.available[k]&obs).astype("uint8")
    return a


@dataclass(frozen=True)
class Q:
    name:str
    optional_qc_fields:dict
    dbzh_raw:np.ndarray
    dbzh_qc:np.ndarray
    qc_flags:np.ndarray
    quality_index:np.ndarray
    valid_mask:np.ndarray
    low_quality_mask:np.ndarray
    qi_components:dict


@dataclass(frozen=True)
class Result:
    profile:object
    sweeps:tuple
    summary:dict
    volume_review_artifacts:dict|None=None


class Native:
    def __init__(self,s,permuted=False):
        self.name=s.name;self.azimuth=s.azimuth;self.elevation=s.elevation;self.ranges=s.ranges
        self.fields=s.fields;self.field_available=s.available;self.geometry_good=s.good;self.gap_after=s.gap_after
        self.ray_time=s.ray_time_s if s.ray_time_s is not None else np.full(s.shape[0],np.nan)
        self.shape=s.shape;self.gate_spacing_m=s.dr
        indices=np.arange(s.shape[0])
        if isinstance(permuted,np.ndarray):self.original_indices=np.asarray(permuted)
        else:self.original_indices=np.roll(indices,3) if permuted else indices
        self.attrs={"radar_id":"site_a","scan_id":"test-scan","radar_config_version":"processor-v1",
                    "volume_end_time_utc":"2026-09-18T03:00:00+00:00"}
    def restore(self,a):
        out=np.empty_like(a);out[self.original_indices]=a;return out


def group(q):return {**q.optional_qc_fields,**q.qi_components,"DBZH_RAW":q.dbzh_raw,"DBZH_QC":q.dbzh_qc,
    "QC_FLAGS":q.qc_flags,"QUALITY_INDEX":q.quality_index,"VALID_MASK":q.valid_mask,"LOW_QUALITY_MASK":q.low_quality_mask}


def fixture(c,*,permuted=False,near=False,receiver=False,sweep=None,permutation=None):
    s=sweep if sweep is not None else scene(kind="ground",zdr=.2)
    n=Native(s,permuted=permutation if permutation is not None else permuted);a=baseline(s)
    for k in list(a):
        if k.startswith("CR_") or k=="REFLECTIVITY_ELIGIBLE_FOR_CR":del a[k]
    a={k:n.restore(v) for k,v in a.items()}
    separate=("DBZH_RAW","DBZH_QC","QC_FLAGS","QUALITY_INDEX","VALID_MASK","LOW_QUALITY_MASK")
    q=Q(n.name,{k:v for k,v in a.items() if k not in separate},*[a[k] for k in separate],{})
    from volume_review.near_measurement.config import NearMeasurementConfig
    from volume_review.receiver_domain.config import ReceiverDomainConfig
    p=NS(volume_review=VolumeReviewConfig(mode="experiment_quarantine",unknown_cr_policy="retain_with_risk",clutter_fusion=c,
        near_measurement=NearMeasurementConfig(depolarization_backend="numpy_reference") if near else None,
        receiver_domain=ReceiverDomainConfig() if receiver else None),context=NS(strong_support=.7),
        echo=NS(no_rain_below_dbz=-10.),quality_index=NS(quantitative_minimum=.5),flag_masks={"LOW_QUALITY":np.uint32(1024)})
    return Result(p,(q,),{"sweeps":{n.name:{}}}),[n]
