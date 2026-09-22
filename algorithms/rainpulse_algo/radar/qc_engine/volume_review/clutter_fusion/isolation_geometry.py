"""Physical native objects and measured annuli; no filtered-image input.

Quadrature describes area coverage, not additional observations. Unique original
ray/gate identities are counted separately. An unknown location is pessimistically
counted as possibly echoing, never as clear air. All scales use one frozen input.
"""
from dataclasses import dataclass
import numpy as np
from scipy.ndimage import label, find_objects
from scipy.spatial import cKDTree
from ..data import ResourceLimit, array_digest
from .context import ground, sample_ground


@dataclass(frozen=True)
class GeometryResult:
    arrays: dict
    objects: list
    summary: dict


FLOATS = ("AREA_KM2", "DIAMETER_M", "KNOWN_FRACTION", "QUADRANT_KNOWN_MIN",
          "POSSIBLE_ECHO_FRACTION", "NATIVE_FOOTPRINT_MAX_M",
          "WEAK_DIAG_A", "WEAK_DIAG_B", "WEAK_ZERO_A", "WEAK_ZERO_B")
MASKS = ("SMALL", "RING_AVAILABLE", "ISOLATED", "OBJECT_PROTECTED",
         "WEATHER_NEARBY", "GEOMETRY_LIMITED", "OBSTRUCTION_NEARBY", "WEAK_AVAILABLE", "WEAK_LOW_SCORE", "DOMAIN")


def empty(shape):
    a={"CF_ISO_"+k:np.full(shape,np.nan,"float32") for k in FLOATS}
    a.update({"CF_ISO_"+k+"_MASK":np.zeros(shape,"uint8") for k in MASKS})
    a.update({"CF_ISO_"+k:np.zeros(shape,"uint32") for k in
              ("OBJECT_ID","NATIVE_GATE_COUNT","RING_UNIQUE_GATES")})
    return a


def _spend(budget, n, c):
    budget[0] += int(n)
    if budget[0] > c.maximum_sample_points:
        raise ResourceLimit("isolated-object quadrature/diagnostic sample budget")


def _raw(s):
    z,ok=s.moment("DBZH")
    measured=ok & s.good[:,None] & np.isfinite(z) & (z>=-32) & (z<=80)
    no=np.asarray(s.no_echo,bool) & s.good[:,None]
    if np.any(no & measured):raise ValueError("raw no-echo and reflectivity overlap")
    return z,measured,no


def _topology(s, echo, c):
    """Connected original gates, canonical angular order, explicit seam union."""
    order=np.argsort(s.azimuth,kind="stable")
    az=s.azimuth[order]
    step=(np.roll(az,-1)-az)%360.
    good=s.good[order]
    edges=s.gap_after[order] | (step<=.01) | (step>c.maximum_ray_spacing_deg)
    edges |= ~good | ~np.roll(good,-1)
    valid_step=step[(step>.01)&(step<=c.maximum_ray_spacing_deg)]
    nominal=float(np.median(valid_step)) if len(valid_step) else 0.
    # Do not fabricate adjacency or cell widths for an unresolved sweep.
    if nominal<=0:return None
    width=(np.minimum(step,nominal)+np.minimum(np.roll(step,1),nominal))/2.
    a=echo[order]
    labels=np.zeros(a.shape,"int32");total=0
    cuts=[0,*list(np.flatnonzero(edges[:-1])+1),s.shape[0]]
    for lo,hi in zip(cuts[:-1],cuts[1:]):
        part,n=label(a[lo:hi],np.ones((3,3),"uint8"))
        if n:
            labels[lo:hi]=np.where(part>0,part+total,0)
            total += int(n)
            if total>c.maximum_objects:
                raise ResourceLimit("isolated-object component count budget")
    if total and not edges[-1]:
        parent=np.arange(total+1,dtype="int32")
        def root(k):
            while parent[k]!=k:
                parent[k]=parent[parent[k]];k=int(parent[k])
            return k
        for shift in (-1,0,1):
            if shift<0: aa,bb=labels[0,1:],labels[-1,:-1]
            elif shift>0: aa,bb=labels[0,:-1],labels[-1,1:]
            else: aa,bb=labels[0],labels[-1]
            for x,y in np.unique(np.stack((aa,bb),axis=1),axis=0):
                if x and y:
                    x,y=root(int(x)),root(int(y));parent[max(x,y)]=min(x,y)
        for k in range(1,total+1):parent[k]=root(k)
        mapped=parent[labels]
        _,inverse=np.unique(np.r_[0,mapped.ravel()],return_inverse=True)
        labels=inverse[1:].reshape(labels.shape).astype("int32")
    out=np.zeros(s.shape,"int32");out[order]=labels
    widths=np.zeros(s.shape[0]);widths[order]=np.deg2rad(width)
    boundary=np.zeros(s.shape,bool)
    boundary[order]=np.broadcast_to((edges|np.roll(edges,1)|~good)[:,None],s.shape)
    boundary[:,0]=True;boundary[:,-1]=True
    return out,widths,boundary


def _sample(s,x,y,c):
    distance=np.hypot(x,y);angle=np.degrees(np.arctan2(x,y))%360
    row,gate,foot,_=sample_ground(s,angle,distance)
    # The sampler bounds ordinary angular/range footprints; declared sector
    # discontinuities need a separate conservative exclusion.
    delta=(np.roll(s.azimuth,-1)-s.azimuth)%360.
    edges=s.gap_after|(delta<=.01)|(delta>c.maximum_ray_spacing_deg)
    safe=s.good & ~edges & ~np.roll(edges,1)
    foot &= safe[row] & (abs(s.elevation[row])<=c.maximum_elevation_deg)
    return row,gate,foot


def _weak(s,z,measured,no,domain,c,out,budget):
    """Paper-inspired two-diagonal sums on fixed METRE offsets; never a decision.

    Both true measured scores (all three distinct, finite source gates) and
    explicitly named zero-background comparison scores are retained. The latter
    are not physical reflectivity and must not be read by qualification code.
    """
    if not c.weak_diagnostic_enabled:return
    rr,gg=np.nonzero(domain)
    for begin in range(0,len(rr),20000):
        r=rr[begin:begin+20000];g=gg[begin:begin+20000]
        d=ground(s.ranges[g],s.elevation[r]);az=np.deg2rad(s.azimuth[r])
        x,y=d*np.sin(az),d*np.cos(az)
        both=np.ones(len(r),bool)
        for suffix,sy in (("A",1),("B",-1)):
            _spend(budget,2*len(r),c)
            r1,g1,f1=_sample(s,x-c.weak_offset_m,y-sy*c.weak_offset_m,c)
            r2,g2,f2=_sample(s,x+c.weak_offset_m,y+sy*c.weak_offset_m,c)
            m1=f1&measured[r1,g1];m2=f2&measured[r2,g2]
            id0=r*s.shape[1]+g;id1=r1*s.shape[1]+g1;id2=r2*s.shape[1]+g2
            unique=(id0!=id1)&(id0!=id2)&(id1!=id2)
            ready=m1&m2&unique
            value=z[r,g]+np.where(m1,z[r1,g1],0.)+np.where(m2,z[r2,g2],0.)
            out["CF_ISO_WEAK_ZERO_"+suffix][r,g]=value
            out["CF_ISO_WEAK_DIAG_"+suffix][r,g]=np.where(ready,value,np.nan)
            both &= ready
        out["CF_ISO_WEAK_AVAILABLE_MASK"][r,g]=both.astype("uint8")
        out["CF_ISO_WEAK_LOW_SCORE_MASK"][r,g]=(both & (
            (out["CF_ISO_WEAK_DIAG_A"][r,g]<=c.weak_score_threshold) |
            (out["CF_ISO_WEAK_DIAG_B"][r,g]<=c.weak_score_threshold))).astype("uint8")


def inspect(s,cfg,base,*,budget=None):
    c=cfg.isolated_objects
    if c is None:raise ValueError("isolation configuration required")
    if np.prod(s.shape)>c.maximum_sweep_gates:raise ResourceLimit("isolation sweep gate budget")
    budget=[0] if budget is None else budget
    initial=budget[0]
    z,measured,no=_raw(s)
    out=empty(s.shape)
    domain=measured&(z>=c.echo_threshold_dbz)&(z<cfg.protected_dbz)
    domain &= (s.ranges[None,:]>=cfg.minimum_range_m)&(s.ranges[None,:]<=cfg.maximum_range_m)
    domain &= abs(s.elevation[:,None])<=c.maximum_elevation_deg
    out["CF_ISO_DOMAIN_MASK"]=domain.astype("uint8")
    if not measured.any():
        return GeometryResult(out,[],{"status":"NO_REFLECTIVITY","raw_sha256":s.digest,
                                      "objects":0,"small_objects":0,"isolated_objects":0,"samples":0})
    echo=measured&(z>=c.echo_threshold_dbz)
    topo=_topology(s,echo,c)
    if topo is None:
        return GeometryResult(out,[],{"status":"GEOMETRY_UNRESOLVED","samples":0})
    ids,widths,boundary=topo
    out["CF_ISO_OBJECT_ID"]=ids.astype("uint32")
    n=int(ids.max())
    flat=ids.ravel();count=np.bincount(flat,minlength=n+1)
    r=s.ranges[None,:];el=s.elevation[:,None]
    lo=ground(np.maximum(0.,r-s.dr/2),el);hi=ground(r+s.dr/2,el)
    d=ground(r,el);theta=np.deg2rad(s.azimuth[:,None])
    x=d*np.sin(theta);y=d*np.cos(theta)
    area=np.maximum(0.,(hi*hi-lo*lo)/2*widths[:,None])
    # Conservative containing radius for a curved native sector footprint.
    half=np.maximum(abs(d-lo),abs(hi-d))+hi*widths[:,None]/2
    areas=np.bincount(flat,weights=area.ravel(),minlength=n+1)
    cx=np.divide(np.bincount(flat,weights=(x*area).ravel(),minlength=n+1),areas,
                 out=np.zeros(n+1),where=areas>0)
    cy=np.divide(np.bincount(flat,weights=(y*area).ravel(),minlength=n+1),areas,
                 out=np.zeros(n+1),where=areas>0)
    rad=np.zeros(n+1);np.maximum.at(rad,flat,(np.hypot(x-cx[ids],y-cy[ids])+half).ravel())
    max_foot=np.zeros(n+1);np.maximum.at(max_foot,flat,(2*half).ravel())
    obstruction=np.zeros(s.shape,bool)
    if "CF_NR_DEM_ACTION_AVAILABLE_MASK" in base and "CF_NR_DEM_SEVERE_MASK" in base:
        ready=np.asarray(base["CF_NR_DEM_ACTION_AVAILABLE_MASK"])
        blocked=np.asarray(base["CF_NR_DEM_SEVERE_MASK"])
        if ready.shape!=s.shape or blocked.shape!=s.shape or not np.isin(ready,(0,1)).all() or not np.isin(blocked,(0,1)).all():
            raise ValueError("invalid existing obstruction evidence")
        obstruction=(ready==1)&(blocked==1)
    known=(measured|no)&~obstruction
    weather=measured&(z>=cfg.protected_dbz)
    for k in ("CF_HARD_WEATHER_MASK","CF_LOCAL_WEATHER_MASK","CF_LEGACY_PROTECTED_MASK",
              "CF_WEATHER_PROXY_MASK","CF_BG_ENHANCEMENT_MASK","CF_MIXED_MASK"):
        if k in base:
            a=np.asarray(base[k])
            if a.shape!=s.shape or not np.isin(a,(0,1)).all():raise ValueError("invalid weather mask "+k)
            weather |= (a==1)&measured
    # Exact original weather footprints guard quadrature blind spots: one small
    # but protected cell must not disappear merely between quadrature samples.
    wr,wg=np.nonzero(weather)
    weather_tree=cKDTree(np.column_stack((x[wr,wg],y[wr,wg]))) if len(wr) else None
    weather_half=half[wr,wg]
    maximum_weather_half=float(weather_half.max()) if len(wr) else 0.
    protected_count=np.bincount(flat,weights=weather.ravel(),minlength=n+1)
    edge_count=np.bincount(flat,weights=boundary.ravel(),minlength=n+1)
    outside=np.bincount(flat,weights=(echo&~domain).ravel(),minlength=n+1)
    locations=find_objects(ids)
    records=[];small_count=0;isolated_count=0
    for oid in range(1,n+1):
        sl=locations[oid-1]
        if sl is None:continue
        local=ids[sl]==oid
        row,gate=np.nonzero(local);row+=sl[0].start;gate+=sl[1].start
        ix=(row,gate)
        out["CF_ISO_AREA_KM2"][ix]=areas[oid]/1e6
        out["CF_ISO_DIAMETER_M"][ix]=2*rad[oid]
        out["CF_ISO_NATIVE_GATE_COUNT"][ix]=count[oid]
        out["CF_ISO_NATIVE_FOOTPRINT_MAX_M"][ix]=max_foot[oid]
        out["CF_ISO_OBJECT_PROTECTED_MASK"][ix]=bool(protected_count[oid])
        limited=bool(edge_count[oid] or outside[oid] or max_foot[oid]>c.maximum_gate_footprint_m)
        out["CF_ISO_GEOMETRY_LIMITED_MASK"][ix]=limited
        small=(areas[oid]/1e6<=c.maximum_area_km2 and 2*rad[oid]<=c.maximum_diameter_m
               and count[oid]<=c.maximum_action_gates_per_object)
        if not small:continue
        small_count+=1;out["CF_ISO_SMALL_MASK"][ix]=1
        record={"id":oid,"gates":int(count[oid]),"area_km2":float(areas[oid]/1e6),
                "containing_diameter_m":float(2*rad[oid]),"geometry_limited":limited,
                "protected":bool(protected_count[oid]),"rings":[]}
        records.append(record)
        if limited or protected_count[oid]:continue
        ok=True;near=False;terrain_near=False;cover=1.;qmin=1.;possible=0.;unique_min=2**32-1
        inner=rad[oid]+c.ring_gap_m
        if weather_tree is not None:
            guard=inner+max(c.ring_widths_m)
            distance,index=weather_tree.query([cx[oid],cy[oid]],k=1)
            # Deliberately conservative: use the largest weather footprint for
            # the guard rather than letting a coarse protected gate be missed.
            near=bool(distance<=guard+maximum_weather_half)
        for width in c.ring_widths_m:
            outer=inner+width
            grid=np.arange(-np.ceil(outer/c.quadrature_step_m),
                           np.ceil(outer/c.quadrature_step_m)+1)*c.quadrature_step_m
            xx,yy=np.meshgrid(grid,grid);dd=np.hypot(xx,yy)
            disk=dd<=outer
            dx,dy=xx[disk],yy[disk];ring=dd[disk]>=inner
            _spend(budget,len(dx),c)
            ri,gi,foot=_sample(s,dx+cx[oid],dy+cy[oid],c)
            observed=foot&known[ri,gi]
            ee=foot&echo[ri,gi]
            # Oversampling one footprint does not create independent supports.
            uid=np.unique(ri[ring&observed]*s.shape[1]+gi[ring&observed])
            cov=float(observed[ring].mean()) if ring.any() else 0.
            poss=float((ee[ring]|~observed[ring]).mean()) if ring.any() else 1.
            quadrant=(dx>=0).astype(int)+2*(dy>=0).astype(int)
            qc=[float(observed[ring&(quadrant==q)].mean())
                if (ring&(quadrant==q)).any() else 0. for q in range(4)]
            nearby=bool((foot&weather[ri,gi]).any())
            terrain_near |= bool((foot&obstruction[ri,gi]).any())
            eligible=(cov>=c.minimum_known_fraction and min(qc)>=c.minimum_quadrant_known_fraction
                      and len(uid)>=c.minimum_unique_ring_gates)
            ok &= eligible and poss<=c.maximum_possible_echo_fraction
            near |= nearby;cover=min(cover,cov);qmin=min(qmin,min(qc));possible=max(possible,poss)
            unique_min=min(unique_min,len(uid))
            record["rings"].append({"width_m":width,"quadrature_points":int(ring.sum()),
                "known_fraction":cov,"quadrant_known":qc,"possible_echo_fraction":poss,
                "unique_native_gates":int(len(uid)),"weather_nearby":nearby})
        out["CF_ISO_KNOWN_FRACTION"][ix]=cover
        out["CF_ISO_QUADRANT_KNOWN_MIN"][ix]=qmin
        out["CF_ISO_POSSIBLE_ECHO_FRACTION"][ix]=possible
        out["CF_ISO_RING_UNIQUE_GATES"][ix]=unique_min
        available=(cover>=c.minimum_known_fraction and qmin>=c.minimum_quadrant_known_fraction
                   and unique_min>=c.minimum_unique_ring_gates)
        out["CF_ISO_RING_AVAILABLE_MASK"][ix]=available
        out["CF_ISO_WEATHER_NEARBY_MASK"][ix]=near
        out["CF_ISO_OBSTRUCTION_NEARBY_MASK"][ix]=terrain_near
        out["CF_ISO_ISOLATED_MASK"][ix]=ok and not near and not terrain_near
        isolated_count+=bool(ok and not near and not terrain_near)
    _weak(s,z,measured,no,domain,c,out,budget)
    return GeometryResult(out,records,{"status":"EVALUATED","raw_sha256":s.digest,
        "geometry_sha256":array_digest({"az":s.azimuth,"range":s.ranges,"elevation":s.elevation,
                                         "good":s.good,"gaps":s.gap_after}),
        "objects":n,"small_objects":small_count,"isolated_objects":isolated_count,
        "samples":budget[0]-initial,"object_records":records,
        "unknown_policy":"worst_case_echo_not_zero","iterations":1,
        "support_source":"original_measurements_not_parent_QC", "area_units":"km2_horizontal_4_3_earth",
        "weak_scores":"diagnostic_dBZ_sum_not_physical_power","scores_are_probabilities":False})
