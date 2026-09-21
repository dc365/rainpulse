"""Strictly past raw observations, never past QC masks or future data.

Only the most recent *geometrically applicable* sweep is used at each gate.
It must itself contain a current-measurement nonmet seed; recurrence alone is
not a label. Temporal support affects CR only in this first release.
"""
from dataclasses import dataclass
import numpy as np
from ..data import ResourceLimit, array_digest
from .context import ground, height, sample_ground
from .partial_moments import extract


@dataclass(frozen=True)
class PastSweep:
    sweep: object
    source_sha256: str
    scan_id: str
    radar_id: str
    processing_id: str
    original_indices: object = None
    ingest_time_verified: bool = False

    def __post_init__(self):
        if len(self.source_sha256)!=64 or any(x not in '0123456789abcdef' for x in self.source_sha256):
            raise ValueError("temporal content identity required")
        if not self.scan_id or not self.radar_id or not self.processing_id:
            raise ValueError("identified raw temporal sweep required")
        if self.original_indices is not None:
            a=np.asarray(self.original_indices)
            if a.shape!=(self.sweep.shape[0],) or not np.array_equal(np.sort(a),np.arange(len(a))):
                raise ValueError("invalid temporal acquisition permutation")


def empty(shape):
    out={"CF_NR_TEMPORAL_"+k+"_MASK":np.zeros(shape,"uint8") for k in ("MEASURED","SUPPORT")}
    out.update({"CF_NR_TEMPORAL_"+k:np.full(shape,-1,"int32") for k in ("SOURCE","RAY","GATE")})
    out.update({"CF_NR_TEMPORAL_"+k:np.full(shape,np.nan,"float32") for k in
                ("AGE_S","DBZH_DELTA_DB","SNR_DELTA_DB","DZ_M","HORIZONTAL_ERROR_M")})
    return out


def derive(s, current, past, cfg, *, radar_id, processing_id, scan_id):
    c=cfg.near_revision;out=empty(s.shape)
    if c is None or not c.temporal_enabled:return out,{"status":"DISABLED","sources":[]}
    if len(past)>c.maximum_temporal_sweeps or sum(np.prod(p.sweep.shape) for p in past)>c.maximum_previous_gates:
        raise ResourceLimit("near temporal whole-volume resource limit")
    if s.ray_time_s is None:return out,{"status":"CURRENT_TIME_UNAVAILABLE","sources":[]}
    eligible=[];rejected={}
    for p in past:
        d=p.sweep;reason=None
        if p.radar_id.lower()!=radar_id.lower() or p.processing_id!=processing_id:reason="IDENTITY_MISMATCH"
        elif p.scan_id==scan_id:reason="SELF_REFERENCE"
        elif d.ray_time_s is None:reason="PAST_TIME_UNAVAILABLE"
        elif d.ray_time_s.max()>=s.ray_time_s.min():reason="NOT_STRICTLY_PAST"
        elif s.ray_time_s.min()-d.ray_time_s.max()>c.maximum_age_seconds:reason="TOO_OLD"
        elif abs(np.median(d.elevation)-np.median(s.elevation))>c.maximum_elevation_error_deg:reason="ELEVATION_MISMATCH"
        if reason:rejected[reason]=rejected.get(reason,0)+1
        else:eligible.append(p)
    eligible.sort(key=lambda p:(-float(p.sweep.ray_time_s.max()),p.source_sha256,p.sweep.name))
    # Duplicate physical snapshots cannot increase apparent evidence.
    unique=[];seen=set()
    for p in eligible:
        key=(p.source_sha256,p.sweep.name)
        if key not in seen:unique.append(p);seen.add(key)
    xx=ground(s.ranges[None,:],s.elevation[:,None]);hh=height(s.ranges[None,:],s.elevation[:,None])
    aa=np.broadcast_to(s.azimuth[:,None],s.shape);z,_=s.moment("DBZH");sn,_=s.moment("SNR")
    sources=[]
    for p in unique:
        d=p.sweep
        jj,kk,support,dh=sample_ground(d,aa,xx)
        angular=(d.azimuth[jj]-aa+180.)%360.-180.
        dg=ground(d.ranges[kk],d.elevation[jj])
        horizontal=np.sqrt(np.maximum(0.,xx*xx+dg*dg-2*xx*dg*np.cos(np.deg2rad(angular))))
        age=s.ray_time_s[:,None]-d.ray_time_s[jj]
        geometric=support&(horizontal<=c.maximum_horizontal_error_m)&(abs(dh-hh)<=c.maximum_vertical_error_m)
        geometric &= (abs(d.elevation[jj]-s.elevation[:,None])<=c.maximum_elevation_error_deg)&(age>0)&(age<=c.maximum_age_seconds)
        # A newer observed incompatible donor is an explicit conflict, not an
        # invitation to cherry-pick an older matching snapshot.
        take=geometric&(current['CF_NR_READY_MASK']==1)&(out['CF_NR_TEMPORAL_SOURCE']<0)
        dz,za=d.moment('DBZH');ds,sa=d.moment('SNR')
        take &= za[jj,kk]&sa[jj,kk]
        if not take.any():continue
        f=extract(d,cfg);seed=f['CF_NR_STRICT_MASK']==1
        value_delta=z-dz[jj,kk];snr_delta=sn-ds[jj,kk]
        accepted=take&seed[jj,kk]&(current['CF_NR_RELAXED_MASK']==1)
        accepted &= (abs(value_delta)<=c.maximum_dbzh_change_db)&(abs(snr_delta)<=c.maximum_snr_change_db)
        source=len(sources);sources.append({'scan_id':p.scan_id,'sweep':d.name,'source_sha256':p.source_sha256,
             'raw_sweep_sha256':d.digest,'radar_id':p.radar_id,'processing_id':p.processing_id,
             'ingest_time_verified':p.ingest_time_verified,'ray_order':'original_acquisition'})
        out['CF_NR_TEMPORAL_MEASURED_MASK'][take]=1;out['CF_NR_TEMPORAL_SUPPORT_MASK'][accepted]=1
        out['CF_NR_TEMPORAL_SOURCE'][take]=source
        original=np.arange(d.shape[0]) if p.original_indices is None else np.asarray(p.original_indices)
        out['CF_NR_TEMPORAL_RAY'][take]=original[jj[take]];out['CF_NR_TEMPORAL_GATE'][take]=kk[take]
        for key,value in (("AGE_S",age),("DBZH_DELTA_DB",value_delta),("SNR_DELTA_DB",snr_delta),
                          ("DZ_M",dh-hh),("HORIZONTAL_ERROR_M",horizontal)):
            out['CF_NR_TEMPORAL_'+key][take]=value[take]
    return out,{'status':'EVALUATED' if sources else 'NO_COMPARABLE_PAST','sources':sources,
       'rejected':rejected,'supported_gates':int(out['CF_NR_TEMPORAL_SUPPORT_MASK'].sum()),
       'semantics':'causal_measurement_recurrence_not_ground_truth'}
