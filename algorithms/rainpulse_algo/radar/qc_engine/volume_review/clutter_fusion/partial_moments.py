"""Reliable rho/SNR/phase support without mandatory ZDR or rough reflectivity.

No intensity smoothing or missing fill. Circular increments are calculated
only across two observed adjacent gates. Neighbourhood masks describe evidence,
not new observations; each accepted target has its own valid rho/phase/SNR.
"""
import numpy as np
from scipy.ndimage import uniform_filter
from .features import safe_stencil
from ..data import ResourceLimit

MASKS = ("READY", "STRICT", "RELAXED", "SAFE")


def empty(shape):
    out = {"CF_NR_"+k+"_MASK": np.zeros(shape, "uint8") for k in MASKS}
    out.update(CF_NR_SAMPLE_COUNT=np.zeros(shape,"uint16"),
               CF_NR_PHASE_PAIR_COUNT=np.zeros(shape,"uint16"),
               CF_NR_JITTER_DEG=np.full(shape,np.nan,"float32"),
               CF_NR_STRICT_FRACTION=np.full(shape,np.nan,"float32"),
               CF_NR_RELAXED_FRACTION=np.full(shape,np.nan,"float32"))
    return out


def extract(s, cfg):
    c=cfg.near_revision
    if c is None:return {}
    out=empty(s.shape)
    if not c.partial_enabled:return out
    if np.prod(s.shape)>cfg.maximum_sweep_gates:raise ResourceLimit("partial moment sweep budget")
    z,az=s.moment("DBZH"); sn,ass=s.moment("SNR")
    rho,ar=s.moment("RHOHV"); phi,ap=s.moment("PHIDP")
    az=az&(z>=-32)&(z<=80);ar=ar&(rho>=0)&(rho<=1)
    window=(3,max(3,int(np.ceil(cfg.neighbourhood_m/s.dr))|1))
    count_max=int(np.prod(window))
    if count_max>65535:raise ResourceLimit("partial moment sample count capacity")
    safe,_=safe_stencil(s,window,cfg.maximum_ray_spacing_deg)
    # A bad row/gap cannot contribute through the other side of the stencil.
    base=s.good[:,None]&az&ass&(sn>=c.minimum_snr_db)&ar&ap
    phase_ready=s.good[:,None]&ap&ass&(sn>=c.minimum_snr_db)
    pairs=np.zeros(s.shape,bool);delta=np.zeros(s.shape,float)
    pairs[:,1:]=phase_ready[:,1:]&phase_ready[:,:-1]
    if not cfg.minimum_phase_spacing_m<=s.dr<=cfg.maximum_phase_spacing_m:pairs[:]=False
    delta[:,1:]=(np.diff(phi,axis=1)+180.)%360.-180.
    n=lambda a:np.rint(uniform_filter(a.astype(float),window,mode=("wrap", "constant"))*count_max)
    count=n(pairs)
    sine=numeric_sum(np.where(pairs,np.sin(np.deg2rad(delta)),0.),window)
    cosine=numeric_sum(np.where(pairs,np.cos(np.deg2rad(delta)),0.),window)
    resultant=np.divide(np.hypot(sine,cosine),count,out=np.ones(s.shape),where=count>0)
    jitter=np.rad2deg(np.sqrt(-2*np.log(np.clip(resultant,1e-12,1))))
    jitter[(count<c.minimum_samples)|~safe]=np.nan
    ready=base&safe&pairs&np.isfinite(jitter)
    strict_point=ready&(rho<=c.maximum_rhohv)&(jitter>=c.minimum_jitter_deg)
    relaxed_point=ready&(rho<=c.maximum_rhohv)&(jitter>=c.temporal_minimum_jitter_deg)
    nr=n(ready);ns=n(strict_point);nl=n(relaxed_point)
    frac=lambda x:np.divide(x,nr,out=np.full(s.shape,np.nan),where=nr>0)
    sf,lf=frac(ns),frac(nl)
    domain=az&s.good[:,None]&(z>=cfg.no_rain_below_dbz)&(z<c.maximum_dbz)
    domain &= (s.ranges[None,:]>=cfg.minimum_range_m)&(s.ranges[None,:]<=cfg.maximum_range_m)
    for key,a in (("READY",ready),("SAFE",safe&s.good[:,None]),
                  ("STRICT",strict_point&(nr>=c.minimum_samples)&(sf>=c.minimum_neighbour_fraction)&domain),
                  ("RELAXED",relaxed_point&(nr>=c.minimum_samples)&(lf>=c.minimum_neighbour_fraction)&domain)):
        out["CF_NR_"+key+"_MASK"]=(a&az).astype("uint8")
    out.update(CF_NR_SAMPLE_COUNT=np.where(az,nr,0).astype("uint16"),
               CF_NR_PHASE_PAIR_COUNT=np.where(az,count,0).astype("uint16"),
               CF_NR_JITTER_DEG=np.where(az,jitter,np.nan).astype("float32"),
               CF_NR_STRICT_FRACTION=np.where(az,sf,np.nan).astype("float32"),
               CF_NR_RELAXED_FRACTION=np.where(az,lf,np.nan).astype("float32"))
    return out


def numeric_sum(x,window):
    return uniform_filter(np.asarray(x,float),window,mode=("wrap", "constant"))*np.prod(window)
