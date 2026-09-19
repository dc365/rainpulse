"""Bounded nearest actual observations, never interpolation or missing fill."""
import numpy as np
from .arrays import moment


def nearest(current, donor, cfg):
    angle = abs((current.azimuth[:, None]-donor.azimuth[None, :]+180)%360-180)
    angle[:, ~donor.geometry_good] = np.inf
    ray = angle.argmin(axis=1)
    best = angle[np.arange(len(ray)), ray]
    unique = (np.isclose(angle,best[:,None],atol=1e-7,rtol=0)).sum(axis=1)==1
    # Require the nearest range centre to be inside the donor's observed footprint.
    distance = abs(current.ranges[:,None]-donor.ranges[None,:])
    gate = distance.argmin(axis=1)
    dr = float(np.median(np.diff(donor.ranges)))
    range_ok = distance[np.arange(len(gate)),gate] <= dr/2+.001
    rows = current.geometry_good & unique & (best <= cfg.match_maximum_azimuth_deg)
    rows &= abs(current.elevation-donor.elevation[ray]) <= cfg.match_maximum_elevation_deg
    r=current.ranges[None,:];d=donor.ranges[gate][None,:]
    el=np.deg2rad(current.elevation[:,None]);de=np.deg2rad(donor.elevation[ray,None])
    offset=np.deg2rad(np.where(np.isfinite(best),best,180.)[:,None])
    horizontal=np.sqrt(np.maximum(0.,(r*np.cos(el)-d*np.cos(de))**2+2*r*d*np.cos(el)*np.cos(de)*(1-np.cos(offset))))
    height=r*np.sin(el)+r*r/(2*8494667.)
    donor_height=d*np.sin(de)+d*d/(2*8494667.)
    ok=rows[:,None]&range_ok[None,:]&(horizontal<=cfg.match_maximum_horizontal_m)&(abs(height-donor_height)<=cfg.match_maximum_vertical_m)
    return ray,gate,ok


def paired_doppler(current, sweeps, cfg):
    shape=current.shape
    vr=np.full(shape,np.nan,'float32');sw=vr.copy();age=vr.copy()
    source=np.full(shape,-1,'int16');source_ray=np.full(shape,-1,'int32');source_gate=source_ray.copy()
    if not cfg.paired_doppler_enabled:
        return {}
    # Only explicit absolute timestamps are accepted for cross-cut matching.
    if not np.issubdtype(current.ray_time.dtype,np.datetime64):
        return {}
    best=np.full(shape,np.inf)
    _,local_v=moment(current,'VR');_,local_w=moment(current,'SW')
    for donor in sweeps:
        if donor.name==current.name:
            continue
        if any(not current.attrs.get(k) or current.attrs.get(k)!=donor.attrs.get(k) for k in ('radar_id','scan_id')):
            continue
        if not np.issubdtype(donor.ray_time.dtype,np.datetime64):
            continue
        v,av=moment(donor,'VR');w,aw=moment(donor,'SW')
        if not (av&aw).any():
            continue
        ray,gate,ok=nearest(current,donor,cfg)
        dt=abs((current.ray_time-donor.ray_time[ray])/np.timedelta64(1,'s'))
        vv=v[ray[:,None],gate[None,:]];ww=w[ray[:,None],gate[None,:]]
        ok &= av[ray[:,None],gate[None,:]]&aw[ray[:,None],gate[None,:]]&(ww>=0)
        ok &= np.isfinite(dt[:,None])&(dt[:,None]<=cfg.paired_doppler_maximum_seconds)&(dt[:,None]<best)
        # Do not overwrite even a partial native velocity/width observation.
        ok &= ~(local_v|local_w)
        vr[ok]=vv[ok];sw[ok]=ww[ok]
        times=np.broadcast_to(dt[:,None],shape);age[ok]=times[ok];best[ok]=times[ok]
        source[ok]=int(donor.name.rsplit('_',1)[-1])
        source_ray[ok]=np.broadcast_to(donor.original_indices[ray,None],shape)[ok]
        source_gate[ok]=np.broadcast_to(gate[None,:],shape)[ok]
    return dict(NP_PAIRED_VR=vr,NP_PAIRED_SW=sw,NP_PAIRED_AGE_SECONDS=age,
                NP_PAIRED_DOPPLER_AVAILABLE_MASK=np.isfinite(vr).astype('uint8'),
                NP_PAIRED_DONOR_SWEEP=source,NP_PAIRED_DONOR_RAY=source_ray,NP_PAIRED_DONOR_GATE=source_gate)
