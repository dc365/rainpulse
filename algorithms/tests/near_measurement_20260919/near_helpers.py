from dataclasses import dataclass
from types import SimpleNamespace as NS
import numpy as np
from volume_review.near_measurement.config import NearMeasurementConfig as C


def cfg(**kw):
    return C(depolarization_backend="numpy_reference", **kw)


def sample(z=10., snr=16., rho=.7, zdr=.2, ng=100):
    az=np.arange(12, dtype=float)
    r=1000.+np.arange(ng)*500.
    shape=(len(az),len(r))
    a={k:np.full(shape,v,"float32") for k,v in {"DBZH_RAW":z,"DBZH_QC":z,"SNR_RAW":snr,
       "RHOHV_RAW":rho,"ZDR_RAW":zdr,"PHIDP_RAW":2.,"QUALITY_INDEX":.8,"DBZH_USABLE":z}.items()}
    a.update({k:np.ones(shape,"uint8") for k in ("VALID_MASK","REFLECTIVITY_TRUST_MASK","QPE_ELIGIBLE_MASK","REFLECTIVITY_ELIGIBLE_FOR_CR")})
    a.update({k:np.zeros(shape,"uint8") for k in ("QC_ACTION","LOW_QUALITY_MASK","CR_UNCERTAIN_MASK","RFI_QUARANTINE_MASK","NP_QUARANTINE_MASK")})
    a["QC_FLAGS"]=np.zeros(shape,"uint32");a["CR_QUALIFICATION_REASON"]=np.ones(shape,"uint16")
    for k in ("SNR","RHOHV","ZDR","PHIDP"):a[k+"_TRUST_MASK"]=np.ones(shape,"uint8")
    return a,az,r


def same(a,b):
    assert set(a)==set(b)
    for k in a: assert np.array_equal(a[k],b[k],equal_nan=True),k


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
    def __init__(self, a,az,r, permuted=False):
        self.name="sweep_000";self.azimuth=az;self.ranges=r;self.shape=a["DBZH_RAW"].shape
        self.original_indices=np.roll(np.arange(len(az)),4) if permuted else np.arange(len(az))
        self.fields={k:a[k+"_RAW"].copy() for k in ("DBZH","SNR","RHOHV","ZDR","PHIDP") if k+"_RAW" in a}
        self.field_available={k:np.isfinite(v) for k,v in self.fields.items()}
        self.geometry_good=np.ones(len(az),bool);self.gap_after=np.zeros(len(az),bool);self.gap_after[-1]=True
        self.ray_time=np.full(len(az),10.);self.elevation=np.full(len(az),.5)
    def restore(self,v):
        o=np.empty_like(v);o[self.original_indices]=v;return o


def result_fixture(c, permuted=False):
    a,az,r=sample();n=Native(a,az,r,permuted)
    a={k:n.restore(v) for k,v in a.items()}
    raw_names=("DBZH_RAW","DBZH_QC","QC_FLAGS","QUALITY_INDEX","VALID_MASK","LOW_QUALITY_MASK")
    q=Q(n.name,{k:v for k,v in a.items() if k not in raw_names},*[a[k] for k in raw_names],{})
    from volume_review.config import VolumeReviewConfig
    p=NS(volume_review=VolumeReviewConfig(mode="experiment_quarantine",near_measurement=c),
        context=NS(strong_support=.7),echo=NS(no_rain_below_dbz=-10.),
        quality_index=NS(quantitative_minimum=.5),flag_masks={"LOW_QUALITY":np.uint32(1024)})
    return Result(p,(q,),{"sweeps":{n.name:{}}}),[n]


def group(q):
    return {**q.optional_qc_fields,**q.qi_components,"DBZH_RAW":q.dbzh_raw,"DBZH_QC":q.dbzh_qc,
       "QC_FLAGS":q.qc_flags,"QUALITY_INDEX":q.quality_index,"VALID_MASK":q.valid_mask,"LOW_QUALITY_MASK":q.low_quality_mask}
