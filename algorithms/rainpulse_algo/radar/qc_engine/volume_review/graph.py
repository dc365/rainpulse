"""Dual-view, non-propagating cross-elevation links, with actual gate receipts."""
import numpy as np
from scipy.spatial import cKDTree
from .geometry import xyz, nearest_ray
from .data import ResourceLimit


def associate(sweeps, raw_fields, local_weather_masks, cfg, *, objects=None):
    outputs = []
    links = object_links(sweeps, objects, cfg) if objects is not None else []
    for ti, target in enumerate(sweeps):
        shape = target.shape
        out = {"VOR_SOURCE_CORROBORATED_MASK": np.zeros(shape, "uint8"),
               "VOR_SPATIAL_WEATHER_MASK": np.zeros(shape, "uint8"),
               "VOR_DONOR_SWEEP": np.full(shape, -1, "int32"),
               "VOR_DONOR_RAY": np.full(shape, -1, "int32"),
               "VOR_DONOR_GATE": np.full(shape, -1, "int32")}
        candidate = raw_fields[ti]["VOR_CANDIDATE_MASK"] == 1
        rows, gates = np.nonzero(candidate)
        if len(rows) == 0 or target.ray_time_s is None:
            outputs.append(out)
            continue
        target_xyz = xyz(target, rows, gates)
        source_target = raw_fields[ti]["VOR_SOURCE_MATCH_MASK"][rows,gates] == 1
        for di, donor in enumerate(sweeps):
            if di == ti or donor.ray_time_s is None:
                continue
            # Source-coordinate view. Actual measured ray footprints; no row-number match.
            dr, angular_ok = nearest_ray(donor, target.azimuth[rows], cfg.maximum_link_angle_deg)
            dg = np.searchsorted(donor.ranges, target.ranges[gates]).clip(0, donor.shape[1]-1)
            prev = np.maximum(dg-1,0)
            dg = np.where(abs(donor.ranges[prev]-target.ranges[gates]) <= abs(donor.ranges[dg]-target.ranges[gates]), prev, dg)
            measured = (angular_ok & (abs(donor.ranges[dg]-target.ranges[gates]) <= min(cfg.maximum_link_range_m, donor.dr/2.+1e-6)) &
                        (abs(donor.ray_time_s[dr]-target.ray_time_s[rows]) <= cfg.maximum_link_seconds))
            other = raw_fields[di]
            support = (measured & source_target & (abs(donor.elevation[dr]-target.elevation[rows]) >= cfg.minimum_link_elevation_deg) & (other["VOR_SOURCE_MATCH_MASK"][dr,dg] == 1) &
                       (other["VOR_SOURCE_FAMILY"][dr,dg] == raw_fields[ti]["VOR_SOURCE_FAMILY"][rows,gates]))
            fresh = support & (out["VOR_DONOR_SWEEP"][rows,gates] < 0)
            out["VOR_SOURCE_CORROBORATED_MASK"][rows[support],gates[support]] = 1
            out["VOR_DONOR_SWEEP"][rows[fresh],gates[fresh]] = di
            out["VOR_DONOR_RAY"][rows[fresh],gates[fresh]] = dr[fresh]
            out["VOR_DONOR_GATE"][rows[fresh],gates[fresh]] = dg[fresh]
            if support.any():
                pairs = np.unique(np.column_stack((raw_fields[ti]["VOR_OBJECT_ID"][rows[support],gates[support]],
                                                   other["VOR_OBJECT_ID"][dr[support],dg[support]])), axis=0)
                for a,b in pairs:
                    links.append({"target_sweep": ti, "target_object": int(a), "donor_sweep": di,
                                  "donor_object": int(b), "kind": "source_coordinate_association",
                                  "is_independent_weather_truth": False, "action_propagation": False})
                    if len(links) > cfg.maximum_links:
                        raise ResourceLimit("volume links")
            # Physical-space view: POSITIVE comparable weather only. Missing high
            # echoes never act as evidence against shallow low-level weather.
            wr, wg = np.nonzero(local_weather_masks[di])
            if len(wr):
                scale = np.array([cfg.maximum_weather_horizontal_m, cfg.maximum_weather_horizontal_m,
                                  cfg.maximum_weather_vertical_m])
                points = xyz(donor, wr, wg)/scale
                distances, index = cKDTree(points).query(target_xyz/scale, distance_upper_bound=1.)
                ok = np.isfinite(distances)
                selected = np.minimum(index, len(wr)-1)
                ok &= abs(donor.ray_time_s[wr[selected]]-target.ray_time_s[rows]) <= cfg.maximum_link_seconds
                out["VOR_SPATIAL_WEATHER_MASK"][rows[ok],gates[ok]] = 1
        outputs.append(out)
    return outputs, links


def object_links(sweeps, objects, cfg):
    """Raw object associations are descriptive, even with missing moments/times.

    Nested contours are a hierarchy, not additional independent votes. Link
    endpoints are original object IDs; links never change member gate masks.
    """
    links=[]
    for i in range(len(sweeps)):
        targets=[o for o in objects[i] if o['parent_id']==0]
        for j in range(i+1,len(sweeps)):
            donors=[o for o in objects[j] if o['parent_id']==0]
            if not donors:
                continue
            angle=np.array([o['azimuth_center_deg'] for o in donors])
            rlo=np.array([o['range_min_m'] for o in donors]);rhi=np.array([o['range_max_m'] for o in donors])
            widths=np.array([o['azimuth_width_deg'] for o in donors])
            for target in targets:
                angle_delta=abs((angle-target['azimuth_center_deg']+180)%360-180)
                overlap=np.minimum(rhi,target['range_max_m'])-np.maximum(rlo,target['range_min_m'])
                selected=np.flatnonzero((angle_delta<=cfg.maximum_link_angle_deg+np.minimum(widths,target['azimuth_width_deg'])/2)&(overlap>=0))
                for k in selected:
                    donor=donors[k]
                    time_known=target['time_min_s'] is not None and donor['time_min_s'] is not None
                    time_ok=time_known and max(target['time_max_s'],donor['time_max_s'])-min(target['time_min_s'],donor['time_min_s'])<=cfg.maximum_link_seconds
                    if time_known and not time_ok:
                        continue
                    a=np.asarray(target['xyz_sample_m']);b=np.asarray(donor['xyz_sample_m'])
                    scale=np.array([cfg.maximum_weather_horizontal_m,cfg.maximum_weather_horizontal_m,cfg.maximum_weather_vertical_m])
                    distance=float(cKDTree(b/scale).query(a/scale)[0].min())
                    links.append({'target_sweep':i,'target_object':target['id'],'donor_sweep':j,'donor_object':donor['id'],
                                  'kind':'raw_object_association','radar_coordinate_overlap':True,
                                  'spatial_centres_comparable':distance<=1.,'scaled_spatial_distance':distance,
                                  'time_comparable':bool(time_ok),'timing_available':bool(time_known),
                                  'is_independent_weather_truth':False,'action_propagation':False})
                    if len(links)>cfg.maximum_links:
                        raise ResourceLimit('raw object association links')
    return links
