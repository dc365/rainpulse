"""Wire frozen task-selected raw history and existing geometry resources to CF.

No filesystem discovery of latest jobs and no new control-plane requests.
The upstream context loader already validates transport/content/health/cutoff.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import numpy as np
from ..data import array_digest
from .causal_temporal import PastSweep


@dataclass(frozen=True)
class RuntimeContext:
    radar_id: str
    scan_id: str
    processing_id: str
    past: tuple = ()
    beam: object = None
    terrain: object = None
    dem_version: str | None = None
    cutoff_utc: str | None = None
    abstention_reason: str | None = None


def prepare(profile,current_root,temporal,beam,terrain,dem_version,cutoff,artifacts):
    v=getattr(profile,'volume_review',None);cf=getattr(v,'clutter_fusion',None)
    if cf is None or cf.near_revision is None:return None
    attrs=current_root.attrs
    for k in ('radar_id','scan_id','radar_config_version'):
        if not attrs.get(k):raise ValueError('unidentified near revision input: '+k)
    by_scan={str(x.get('scan_id')):x for x in artifacts if x.get('role')=='temporal' and x.get('status')=='available'}
    if beam is not None:
        if str(beam.radar_id).lower()!=str(attrs['radar_id']).lower():
            raise ValueError('near DEM beam belongs to another radar')
        if getattr(beam,'radar_config_version',None)!=attrs['radar_config_version']:
            raise ValueError('near DEM processing geometry identity differs')
    count=gates=0
    for root,_,_ in temporal:
        for i in root['sweep_number'][:]:
            g=root[f'sweep_{int(i):03d}'];count+=1
            gates+=int(np.prod(g['DBZH'].shape)) if 'DBZH' in g else len(g['azimuth'])*len(g['range'])
    if cf.near_revision.temporal_enabled and (count>cf.near_revision.maximum_temporal_sweeps or gates>cf.near_revision.maximum_previous_gates):
        # The entire new extension abstains later, retaining the parent CF.
        # Do not copy oversized raw histories first and fail after allocation.
        return RuntimeContext(str(attrs['radar_id']),str(attrs['scan_id']),str(attrs['radar_config_version']),
             (),beam,terrain,dem_version,cutoff.isoformat(),'temporal context preparation resource budget')
    past=[]
    if cf.near_revision.temporal_enabled:
        from ...adapters import adapt_sweep
        from .integration import raw_sweep
        for root,_,_ in temporal:
            a=root.attrs;record=by_scan.get(str(a.get('scan_id')))
            if not record:raise ValueError('temporal source missing frozen artifact receipt')
            for i in root['sweep_number'][:]:
                n=adapt_sweep(root,f'sweep_{int(i):03d}',profile)
                past.append(PastSweep(raw_sweep(n),record['sha256'],str(a['scan_id']),
                    str(a['radar_id']),str(a['radar_config_version']),n.original_indices,
                    bool(record.get('ingest_time_verified'))))
    return RuntimeContext(str(attrs['radar_id']),str(attrs['scan_id']),str(attrs['radar_config_version']),
                           tuple(past),beam,terrain,dem_version,cutoff.isoformat())
