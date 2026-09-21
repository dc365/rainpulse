"""DEM reliability before max-Z. Local interception is distinct from CBB.

Uses existing RainPulse beam/blockage primitives, not a new terrain model.
A verified, versioned EGM2008 terrain/antenna reference is mandatory for action.
Unknown upstream terrain cannot be treated as clear beam. No reflectivity correction.
"""
from types import SimpleNamespace
import numpy as np
from ..data import ResourceLimit
from .context import ground


def empty(shape):
    out={"CF_NR_DEM_"+k+"_MASK":np.zeros(shape,"uint8") for k in ("AVAILABLE","ACTION_AVAILABLE","LOCAL_INTERCEPTION","SEVERE")}
    out.update({"CF_NR_DEM_"+k:np.full(shape,np.nan,"float32") for k in ("PBB","CBB","BEAM_HEIGHT_M","TERRAIN_HEIGHT_M")})
    return out


def project(s,pbb,cbb,beam_height,terrain_height,cfg,*,verified):
    out=empty(s.shape);c=cfg.near_revision
    if c is None or c.dem_policy=='disabled':return out
    values=[np.asarray(x,dtype='float32') for x in (pbb,cbb,beam_height,terrain_height)]
    if any(x.shape!=s.shape or np.isinf(x).any() for x in values):raise ValueError('invalid native terrain arrays')
    p,b,h,t=values
    for x in (p,b):
        if np.any(np.isfinite(x)&((x<0)|(x>1))):raise ValueError('terrain blockage outside [0,1]')
    if np.any(np.isfinite(p)&np.isfinite(b)&(b+1e-6<p)):raise ValueError('CBB cannot be smaller than local PBB')
    both=np.isfinite(b[:,1:])&np.isfinite(b[:,:-1])
    if np.any(both&(np.diff(b,axis=1)<-1e-6)):raise ValueError('CBB must be cumulative')
    # Missing upstream DEM is not zero blockage; conservative prefix support.
    finite=np.isfinite(p)&np.isfinite(b)&np.isfinite(h)&np.isfinite(t)
    path=np.logical_and.accumulate(finite,axis=1)
    z,obs=s.moment('DBZH');obs=obs&(z>=-32)&(z<=80)
    within=(s.ranges[None,:]>=cfg.minimum_range_m)&(s.ranges[None,:]<=c.maximum_dem_range_m)
    av=path&obs&s.good[:,None]&within&(z>=cfg.no_rain_below_dbz)
    action=av&bool(verified)
    out['CF_NR_DEM_AVAILABLE_MASK']=av.astype('uint8')
    out['CF_NR_DEM_ACTION_AVAILABLE_MASK']=action.astype('uint8')
    out['CF_NR_DEM_LOCAL_INTERCEPTION_MASK']=(av&(p>=c.local_interception_pbb)).astype('uint8')
    out['CF_NR_DEM_SEVERE_MASK']=(action&(b>c.maximum_usable_cbb)).astype('uint8')
    for k,value in zip(('PBB','CBB','BEAM_HEIGHT_M','TERRAIN_HEIGHT_M'),values):out['CF_NR_DEM_'+k]=np.where(av,value,np.nan).astype('float32')
    return out


def from_sampler(s,cfg,beam,terrain,dem_version,*,primitives=None):
    c=cfg.near_revision
    if c is None or c.dem_policy=='disabled':return empty(s.shape),{'status':'DISABLED'}
    if beam is None or terrain is None:return empty(s.shape),{'status':'NO_TERRAIN_CONTEXT'}
    identity=getattr(terrain,'cache_identity',None)
    if not dem_version or not identity:return empty(s.shape),{'status':'UNVERSIONED_TERRAIN'}
    if getattr(beam,'altitude_datum_status',None)!='verified_egm2008':
        return empty(s.shape),{'status':'VERTICAL_DATUM_UNVERIFIED','datum_status':getattr(beam,'altitude_datum_status',None)}
    required=(beam.longitude_deg,beam.latitude_deg,beam.antenna_altitude_m,beam.beam_width_vertical_deg)
    if (not np.isfinite(required).all() or not -180<=beam.longitude_deg<=180 or
        not -90<beam.latitude_deg<90 or not 0<beam.beam_width_vertical_deg<=10):
        raise ValueError('invalid near DEM beam geometry')
    if np.prod(s.shape)>c.maximum_dem_gates:raise ResourceLimit('near DEM gate budget')
    if primitives is None:
        from ....blockage import beam_centre_height_m,beam_radius_m,circular_partial_blockage
        primitives=(beam_centre_height_m,beam_radius_m,circular_partial_blockage)
    centre,radius,partial=primitives
    from pyproj import Geod
    rr=np.broadcast_to(s.ranges,s.shape);az=np.broadcast_to(s.azimuth[:,None],s.shape)
    end=int(np.searchsorted(s.ranges,c.maximum_dem_range_m,side='right'))
    if not end:return empty(s.shape),{'status':'OUTSIDE_TERRAIN_DOMAIN'}
    geod=Geod(ellps='WGS84');arc=ground(rr[:,:end],s.elevation[:,None])
    lon,lat,_=geod.fwd(np.full(arc.shape,float(beam.longitude_deg)),np.full(arc.shape,float(beam.latitude_deg)),az[:,:end],arc)
    t=np.asarray(terrain.sample(lon.ravel(),lat.ravel()),float).reshape(arc.shape)
    if np.isinf(t).any():raise ValueError('infinite DEM elevation')
    h=centre(rr[:,:end],s.elevation[:,None],beam.antenna_altitude_m,
             SimpleNamespace(earth_radius_m=6371000.,effective_earth_radius_factor=4/3))
    p=partial(t,h,radius(rr[:,:end],beam.beam_width_vertical_deg))
    known=np.isfinite(p);b=np.maximum.accumulate(np.where(known,p,0.),axis=1)
    b[~np.logical_and.accumulate(known,axis=1)]=np.nan
    arrays=[]
    for data in (p,b,h,t):
        full=np.full(s.shape,np.nan,'float32');full[:,:end]=data;arrays.append(full)
    return project(s,*arrays,cfg,verified=True),{'status':'EVALUATED','dem_version':str(dem_version),
         'terrain_identity':str(identity),'radar_config_version':getattr(beam,'radar_config_version',None),
         'height_datum':'EGM2008','geometry':'effective_4_3_earth_ground_distance',
         'method':'existing_RainPulse_circular_partial_blockage','calculated_range_m':float(s.ranges[end-1]),
         'obstruction_is_not_clutter_label':True}
