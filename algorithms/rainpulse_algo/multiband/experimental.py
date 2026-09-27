"""Uncalibrated horizontal maximum, separate from verified equal-height fusion.

Streams native cuts, preserves S rejection gates and rejects known bad X gates.
Unknown X calibration/attenuation remains explicitly experimental in metadata.
"""
from __future__ import annotations

from tempfile import TemporaryDirectory
import time
import numpy as np
from pyproj import Transformer

from .fusion import EARTH_EFFECTIVE_M, GEOD, _nearest_ray, allocate_output, finish_composite
from .model import Sweep, epoch
from .quality import Flag, x_qc
from .stream_io import GroupCuts


def experimental_fields(fields, band, *, allow_missing_snr=False):
    observed = fields['OBSERVED_MASK'] == 1
    if band == 'X':
        reject = int(Flag.MISSING | Flag.LOW_SNR | Flag.NONMET_CONFIRMED |
                     Flag.NONMET_CANDIDATE | Flag.BLOCKED | Flag.INVALID_MOMENT | Flag.ATTENUATION_LIMIT)
        if allow_missing_snr and 'SNRH' not in fields:
            reject &= ~int(Flag.LOW_SNR)
        admitted = observed & ((fields['MB_QC_FLAGS'].astype(np.uint32) & reject) == 0)
        values = fields['DBZH_QC_DISPLAY']
        quality = np.full(values.shape, np.nan)
    else:
        admitted = observed & (fields['REFLECTIVITY_ELIGIBLE_FOR_CR'] == 1)
        for key in ('CR_WITHHELD_MASK', 'CONFIRMED_NONMET_MASK'):
            if key in fields:
                admitted &= fields[key] == 0
        values, quality = fields['DBZH_QC'], fields['QUALITY_INDEX']
        admitted &= np.isfinite(quality) & (quality > 0) & (quality <= 1)
    values = np.where(fields['NO_ECHO_MASK'] == 1, np.nan, values)
    admitted &= (fields['NO_ECHO_MASK'] == 1) | np.isfinite(values)
    return values, admitted, quality


def update_samples(out, values, admitted, ray, gate, age, quality, index):
    out['OBSERVED_MASK'] |= admitted.astype(np.uint8)
    replace = admitted & np.isfinite(values) & (~np.isfinite(out['CR_DBZH']) | (values > out['CR_DBZH']))
    for key, value in [('CR_DBZH', values), ('WINNER_RAY', ray), ('WINNER_GATE', gate),
                       ('WINNER_AGE_SECONDS', age), ('WINNER_QUALITY_SCORE', quality)]:
        out[key][replace] = value[replace]
    out['WINNER_SOURCE'][replace] = index


def unique_rays(sweep):
    """Resolve exact duplicate bearings by latest acquisition, first index on ties."""
    order = np.lexsort((np.arange(len(sweep.azimuth_deg)), -sweep.ray_time_epoch, sweep.azimuth_deg))
    _, positions = np.unique(sweep.azimuth_deg[order], return_index=True)
    chosen = np.sort(order[positions])
    return Sweep(sweep.number, sweep.azimuth_deg[chosen], sweep.range_m,
                 sweep.elevation_deg[chosen], sweep.ray_time_epoch[chosen],
                 {key:value[chosen] for key,value in sweep.fields.items()}), chosen


def execute(executor, request, reader, *, started):
    import zarr
    from zarr.storage import KVStore
    from .managed import selected_source_keys
    from .product import sx_comparison_objects
    p, net, options = request['payload'], executor.network, executor.execution
    grid = net.products[p['product_id']]
    target, cutoff = epoch(p['analysis_time']), epoch(p['input_cutoff'])
    if target > cutoff or target % grid.cadence_seconds:
        raise ValueError('invalid experimental target time')
    outputs = {key:allocate_output(grid) for key in ('S','X','S+X')}
    sources, skipped = [], []
    total_gates = 0
    with TemporaryDirectory(prefix='rainpulse-horizontal-', dir=options.scratch_parent) as scratch:
        for src in sorted(p['sources'], key=lambda s:s['radar_id']):
            station = net.stations[src['radar_id']]
            if not station.experimental_enabled or station.source not in {'s_qc_zarr','normalized_zarr'}:
                raise ValueError('experimental station/adapter not enabled')
            if epoch(src['volume_end']) > target or epoch(src['available_at']) > cutoff or target-epoch(src['volume_end']) > station.maximum_age_seconds:
                skipped.append({'radar_id':station.radar_id,'reason':'future_or_expired'})
                continue
            session = reader.open(src['input_uri'])
            with session.staged(directory=scratch, maximum_disk_bytes=options.maximum_scratch_bytes,
                                maximum_object_bytes=options.maximum_object_bytes,
                                keys=selected_source_keys(session.index.logical,station.source,x_qc_only=station.band=='X')) as mapping:
                root=zarr.open_group(store=KVStore(mapping),mode='r')
                cuts=GroupCuts(root,station,src,sha256=session.index.sha256,options=options,
                               maximum_bytes=net.maximum_input_bytes,reject_mask=executor.reject_mask,
                               flag_version=executor.flag_version,sweep_limit=64)
                total_gates += sum(cuts.gates.values())
                if total_gates > options.maximum_task_gates:
                    raise ValueError('experimental task gate budget exceeded')
                index=len(sources)
                sources.append({**src,'band':station.band,'asset_sha256':session.index.sha256,
                                'geometry_verified':station.geometry_verified,'calibration_verified':station.calibration_verified})
                for number in cuts.numbers:
                    volume=cuts.read(number)
                    resolved, native_ray = unique_rays(volume.sweeps[0])
                    volume.sweeps = [resolved]
                    volume.validate(station,require_geometry=False)
                    if station.band=='X':
                        volume=x_qc(volume,station,net.sha256)
                    elif volume.metadata.get('qc_pipeline_version') not in station.allowed_s_qc_versions:
                        raise ValueError('unapproved S QC version')
                    lon,lat=volume.metadata.get('longitude_deg'),volume.metadata.get('latitude_deg')
                    if lon is None or lat is None or not np.isfinite([lon,lat]).all() or not -180<=lon<=180 or not -85<lat<85:
                        raise ValueError('missing/invalid native horizontal coordinates')
                    sweep=volume.sweeps[0]
                    values,admitted,quality=experimental_fields(sweep.fields,station.band,allow_missing_snr=True)
                    if len(sweep.range_m)<2:
                        continue
                    for y in range(0,grid.height,grid.tile_rows):
                        sl=slice(y,min(y+grid.tile_rows,grid.height))
                        xx,yy=np.meshgrid(grid.west_m+(np.arange(grid.width)+.5)*grid.spacing_m,
                                         grid.south_m+(np.arange(sl.start,sl.stop)+.5)*grid.spacing_m)
                        glon,glat=Transformer.from_crs(grid.crs,'EPSG:4326',always_xy=True).transform(xx,yy)
                        bearing,_,distance=GEOD.inv(np.full(xx.shape,lon),np.full(xx.shape,lat),glon,glat)
                        ray,offset=_nearest_ray(sweep.azimuth_deg,bearing%360)
                        arc=distance/EARTH_EFFECTIVE_M
                        denominator=np.cos(np.deg2rad(sweep.elevation_deg[ray])+arc)
                        slant=EARTH_EFFECTIVE_M*np.sin(arc)/np.maximum(denominator,1e-9)
                        ranges=sweep.range_m
                        pos=np.clip(np.searchsorted(ranges,slant),1,len(ranges)-1)
                        gate=np.where(abs(slant-ranges[pos-1])<=abs(slant-ranges[pos]),pos-1,pos)
                        widths=np.r_[np.diff(ranges),np.diff(ranges)[-1]]
                        age=target-sweep.ray_time_epoch[ray]
                        support=(offset<=1.0)&(denominator>0)&(abs(slant-ranges[gate])<=widths[gate]/2)&(age>=0)&(age<=station.maximum_age_seconds)
                        valid=support&admitted[ray,gate]
                        for band in (station.band,'S+X'):
                            update_samples({k:v[sl] for k,v in outputs[band].items()},values[ray,gate],valid,native_ray[ray],gate,age,quality[ray,gate],index)
                    del volume,values,admitted,quality
    results={}
    for band,out in outputs.items():
        result=finish_composite(out,grid,net,p['product_id'],p['analysis_time'],p['input_cutoff'],sources,skipped)
        result.metadata.update(method='experimental_horizontal_max_v1',levels_m_msl=[],
            vertical_coverage='not_height_aligned',observed_mask_semantics='at_least_one_admitted_native_cut',
            experimental=True,calibration_status='unverified',coordinate_status='native_header_unverified',
            missing_snr_policy='retain_as_uncertain_only_when_field_absent',angular_tolerance_deg=1.,duplicate_ray_policy='latest_acquisition_first_index_on_tie',display_warning='未标定试验 · X 缺少 SNR 时保留候选 · 非等高融合')
        results[band]=result
    objects=sx_comparison_objects(results['S+X'],{'S':results['S'],'X':results['X']})
    if sum(map(len,objects.values()))>options.maximum_output_bytes:
        raise ValueError('experimental output budget exceeded')
    return objects,{'candidate_only':True,'operational_eligible':False,'qpe_enabled':False,
                    'experimental':True,'mode':p['mode'],'detail':'manifest.json'}, {'total_ms':(time.perf_counter()-started)*1000,'input_gates':total_gates}
