"""Measured footprint/time context. Unknown upper layers never mean absent echoes."""
from dataclasses import dataclass
import numpy as np
from ..geometry import EARTH
from ..data import ResourceLimit


@dataclass(frozen=True)
class ContextMetadata:
    beam_width_deg: float | None = None
    beam_source: str | None = None
    doppler_pair_verified: bool = False
    waveform: str | None = None
    nyquist_ms: float | None = None
    verification_id: str | None = None

    def __post_init__(self):
        if type(self.doppler_pair_verified) is not bool:
            raise ValueError("doppler verification must be an explicit boolean")
        for name in ("beam_width_deg", "nyquist_ms"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not np.isfinite(value) or value <= 0):
                raise ValueError("invalid context metadata: " + name)
        if self.beam_width_deg is not None and self.beam_width_deg > 3.:
            raise ValueError("unsupported declared beam width")
        for name in ("beam_source", "waveform", "verification_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("invalid context identity: " + name)

    def beam_ok(self):
        return (self.beam_width_deg is not None and np.isfinite(self.beam_width_deg) and
                0 < self.beam_width_deg <= 3 and bool(self.beam_source))

    def pair_ok(self, other):
        return bool(self.doppler_pair_verified and other.doppler_pair_verified and self.verification_id and
                    self.verification_id == other.verification_id and self.waveform and self.waveform == other.waveform and
                    self.nyquist_ms is not None and other.nyquist_ms is not None and
                    np.isfinite([self.nyquist_ms, other.nyquist_ms]).all() and
                    self.nyquist_ms > 0 and np.isclose(self.nyquist_ms, other.nyquist_ms))


def height(r, e):
    e = np.deg2rad(e)
    return np.sqrt(EARTH*EARTH+r*r+2*EARTH*r*np.sin(e))-EARTH


def ground(r, e):
    e = np.deg2rad(e)
    return EARTH*np.arctan2(r*np.cos(e), EARTH+r*np.sin(e))


def sample_ground(s, angle, distance):
    """Return original sorted-ray indices; no sector/range extrapolation."""
    angle, distance = np.broadcast_arrays(np.asarray(angle)%360, np.asarray(distance))
    order = np.argsort(s.azimuth, kind="stable"); az = s.azimuth[order]
    p = np.searchsorted(az, angle); left=(p-1)%len(az); right=p%len(az)
    delta = lambda a,b: (a-b+180.)%360.-180.
    k = np.where(abs(delta(angle,az[left])) <= abs(delta(angle,az[right])),left,right)
    row = order[k]
    gaps = (np.roll(az,-1)-az)%360.
    positive = gaps[(gaps > .01) & (gaps <= 3.)]
    spacing=float(np.median(positive)) if len(positive) else 0.
    after=np.minimum(gaps,spacing)/2; before=np.roll(after,1)
    signed=delta(angle,az[k]); angular=(abs(signed)<=np.where(signed>=0,after[k],before[k])+1e-8)
    arc=distance/EARTH; denom=np.cos(np.deg2rad(s.elevation[row])+arc)
    slant=EARTH*np.sin(arc)/np.maximum(denom,1e-9)
    p=np.searchsorted(s.ranges,slant).clip(0,s.shape[1]-1); prev=np.maximum(p-1,0)
    gate=np.where(abs(slant-s.ranges[prev])<=abs(slant-s.ranges[p]),prev,p)
    valid=angular & s.good[row] & (spacing>0) & (denom>0)
    valid &= abs(slant-s.ranges[gate]) <= s.dr/2+1e-6
    valid &= (slant>=max(0.,s.ranges[0]-s.dr/2)) & (slant<=s.ranges[-1]+s.dr/2)
    return row,gate,valid,height(s.ranges[gate],s.elevation[row])


def empty(shape):
    a={k:np.full(shape,np.nan,"float32") for k in ("CF_UPPER_DROP_DB","CF_DOPPLER_V_MS","CF_DOPPLER_SW_MS",
       "CF_UPPER_AGE_S","CF_UPPER_DZ_M","CF_UPPER_HORIZONTAL_ERROR_M","CF_DOPPLER_AGE_S",
       "CF_DOPPLER_DZ_M","CF_DOPPLER_HORIZONTAL_ERROR_M")}
    a.update({k:np.zeros(shape,"uint8") for k in ("CF_UPPER_MEASURED_MASK","CF_UPPER_ACTION_AVAILABLE_MASK",
       "CF_DOPPLER_MEASURED_MASK","CF_DOPPLER_ACTION_AVAILABLE_MASK","CF_DOPPLER_PAIRED_MASK")})
    a.update({k:np.full(shape,-1,"int32") for k in ("CF_UPPER_SWEEP","CF_UPPER_RAY","CF_UPPER_GATE",
       "CF_DOPPLER_SWEEP","CF_DOPPLER_RAY","CF_DOPPLER_GATE")})
    return a


def derive(index, sweeps, cfg, metadata=None):
    s=sweeps[index]; out=empty(s.shape)
    meta=metadata or [ContextMetadata() for _ in sweeps]
    if len(meta)!=len(sweeps): raise ValueError("context metadata/sweep list differs")
    z,obs=s.moment("DBZH");snr,sa=s.moment("SNR")
    near=obs & (s.ranges[None,:]>=cfg.minimum_range_m) & (s.ranges[None,:]<=cfg.maximum_range_m)
    rows,gates=np.nonzero(near)
    if len(rows)*len(sweeps)>cfg.maximum_context_pairs: raise ResourceLimit("clutter context pairing budget")
    v,va=s.moment("VR");w,wa=s.moment("SW")
    own=near&va&wa&(w>=0)
    out["CF_DOPPLER_MEASURED_MASK"][own]=1;out["CF_DOPPLER_ACTION_AVAILABLE_MASK"][own]=1
    out["CF_DOPPLER_V_MS"][own]=v[own];out["CF_DOPPLER_SW_MS"][own]=w[own]
    out["CF_DOPPLER_SWEEP"][own]=index
    rr,gg=np.nonzero(own);out["CF_DOPPLER_RAY"][own]=rr;out["CF_DOPPLER_GATE"][own]=gg
    for k in ("CF_DOPPLER_AGE_S","CF_DOPPLER_DZ_M","CF_DOPPLER_HORIZONTAL_ERROR_M"):out[k][own]=0
    if not len(rows) or s.ray_time_s is None: return out
    r=s.ranges[gates];el=s.elevation[rows];g=ground(r,el);h=height(r,el)
    best_upper=np.full(len(rows),np.inf);best_dop=np.full(len(rows),np.inf)
    own_selected=own[rows,gates]
    for di,d in enumerate(sweeps):
        if di==index or d.ray_time_s is None:continue
        dr,dg,foot,dh=sample_ground(d,s.azimuth[rows],g)
        age=abs(d.ray_time_s[dr]-s.ray_time_s[rows]);de=d.elevation[dr]-el;dz=dh-h
        donor_ground=ground(d.ranges[dg],d.elevation[dr])
        da=np.deg2rad((d.azimuth[dr]-s.azimuth[rows]+180)%360-180)
        horizontal=np.sqrt((donor_ground-g)**2+2*donor_ground*g*(1-np.cos(da)))
        foot &= horizontal<=cfg.maximum_horizontal_error_m
        vz,vza=d.moment("DBZH");ds,dsa=d.moment("SNR")
        upper=foot & (age<=cfg.maximum_context_seconds)&(de>.2)&(dz>=cfg.minimum_vertical_delta_m)&(dz<=cfg.maximum_vertical_delta_m)
        upper &= vza[dr,dg] & dsa[dr,dg] & (ds[dr,dg]>=cfg.minimum_snr_db)&sa[rows,gates]&(snr[rows,gates]>=cfg.minimum_snr_db)
        fresh=upper&(dz<best_upper)
        # Main-lobe interval separation is required for action-grade distinct heights.
        beam_ok=np.zeros(len(rows),bool)
        if meta[index].beam_ok() and meta[di].beam_ok():
            target_hi=height(r,el+meta[index].beam_width_deg/2)
            donor_lo=height(d.ranges[dg],d.elevation[dr]-meta[di].beam_width_deg/2)
            beam_ok=donor_lo>target_hi
        ii=(rows[fresh],gates[fresh]); best_upper[fresh]=dz[fresh]
        out["CF_UPPER_MEASURED_MASK"][ii]=1
        out["CF_UPPER_ACTION_AVAILABLE_MASK"][ii]=(beam_ok[fresh] & (cfg.vertical_policy=="verified_beam"))
        for k,val in (("CF_UPPER_DROP_DB",z[rows,gates]-vz[dr,dg]),("CF_UPPER_AGE_S",age),
                      ("CF_UPPER_DZ_M",dz),("CF_UPPER_HORIZONTAL_ERROR_M",horizontal),
                      ("CF_UPPER_RAY",dr),("CF_UPPER_GATE",dg)):
            out[k][ii]=val[fresh]
        out["CF_UPPER_SWEEP"][ii]=di
        dv,dva=d.moment("VR");dw,dwa=d.moment("SW")
        pair=foot&(age<=cfg.maximum_doppler_seconds)&(abs(de)<=cfg.maximum_doppler_elevation_deg)&(abs(dz)<=cfg.maximum_doppler_delta_m)
        pair &= dva[dr,dg]&dwa[dr,dg]&(dw[dr,dg]>=0)&~own_selected
        pair_verified=meta[index].pair_ok(meta[di]) and cfg.paired_doppler_policy=="verified_pair"
        if pair_verified:
            pair &= abs(dv[dr,dg])<=meta[di].nyquist_ms+1e-6
        rank=age+np.where(pair_verified,0.,cfg.maximum_doppler_seconds+1.)
        fresh=pair&(rank<best_dop);ii=(rows[fresh],gates[fresh]);best_dop[fresh]=rank[fresh]
        for k in ("CF_DOPPLER_MEASURED_MASK","CF_DOPPLER_PAIRED_MASK"):out[k][ii]=1
        out["CF_DOPPLER_ACTION_AVAILABLE_MASK"][ii]=pair_verified
        for k,val in (("CF_DOPPLER_V_MS",dv[dr,dg]),("CF_DOPPLER_SW_MS",dw[dr,dg]),
                      ("CF_DOPPLER_AGE_S",age),("CF_DOPPLER_DZ_M",dz),
                      ("CF_DOPPLER_HORIZONTAL_ERROR_M",horizontal),("CF_DOPPLER_RAY",dr),("CF_DOPPLER_GATE",dg)):
            out[k][ii]=val[fresh]
        out["CF_DOPPLER_SWEEP"][ii]=di
    return out
