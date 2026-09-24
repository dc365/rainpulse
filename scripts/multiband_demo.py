#!/usr/bin/env python3
# ruff: noqa: E501, E701, E702, I001, E402
"""Reproducible synthetic S/X replay. No real radar or operational validation."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys

import numpy as np
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))
from rainpulse_algo.multiband.cli import replay
from rainpulse_algo.multiband.codec import encode_volume
from rainpulse_algo.multiband.model import Network, Sweep, Volume


def make_demo(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    target = datetime(2026, 9, 23, 12, tzinfo=UTC)
    iso = lambda v: v.isoformat().replace('+00:00', 'Z')
    lon, lat = 119.3, 26.1
    x, y = Transformer.from_crs('EPSG:4326', 'EPSG:32651', always_xy=True).transform(lon, lat)
    common = dict(longitude_deg=lon, latitude_deg=lat, altitude_m_msl=100., beam_width_h_deg=1., beam_width_v_deg=1., enabled=True, geometry_verified=True, calibration_verified=True, calibration_id='synthetic-only', source='native_bundle', quality_scale=1.)
    config = dict(schema_version='1.0', release_id='SYNTHETIC-NOT-OPERATIONAL', maximum_input_bytes=128*1024**2, cache_max_bytes=8*1024**2,
                  stations={'s_demo': dict(common, band='S', frequency_hz=2.8e9, nominal_cadence_seconds=360, maximum_age_seconds=420, allowed_s_qc_versions=['s-synthetic-v1']),
                            'x_demo': dict(common, band='X', frequency_hz=9.4e9, nominal_cadence_seconds=60, maximum_age_seconds=120, x_qc={'attenuation':'upstream_verified'})},
                  products={'local':dict(grid_id='demo-500m', crs='EPSG:32651', west_m=x-40000, south_m=y-40000, spacing_m=500., width=160, height=160, levels_m_msl=[250.,500.,1000.,1500.,2000.,3000.,5000.], tile_rows=16)})
    path=output/'network.json';path.write_text(json.dumps(config,indent=2))
    net=Network.load(path)
    sources=[];index={}
    for station in net.stations.values():
        s_band=station.band=='S'
        end=target-timedelta(seconds=180 if s_band else 0)
        az=np.arange(360,dtype=np.float64)
        ranges=np.arange(250.,60000. if s_band else 30000.,500.)
        rr, aa=np.meshgrid(ranges,np.deg2rad(az))
        east,north=rr*np.sin(aa),rr*np.cos(aa)
        # S includes a high-level core. X has a newly stronger lower-level cell.
        low=45*np.exp(-((east-12000)**2+(north-3000)**2)/(2*7000**2))
        high=60*np.exp(-((east+10000)**2+(north-8000)**2)/(2*5000**2))
        sweeps=[]
        for number,elevation in enumerate([1.,5.] if s_band else [1.]):
            dbzh=(high if number else low+(8 if not s_band else 0)).astype(np.float32)
            noecho=dbzh<2
            dbzh[noecho]=np.nan
            observed=np.ones(dbzh.shape,np.uint8)
            fields=dict(DBZH=dbzh,OBSERVED_MASK=observed,NO_ECHO_MASK=noecho.astype(np.uint8),SNRH=np.full(dbzh.shape,20,np.float32),RHOHV=np.full(dbzh.shape,.99,np.float32))
            if s_band:
                fields.update(DBZH_QC=dbzh.copy(),REFLECTIVITY_ELIGIBLE_FOR_CR=observed.copy(),QUALITY_INDEX=np.full(dbzh.shape,.9,np.float32))
            else:
                propagation=~((rr>18000)&(az[:,None]>65)&(az[:,None]<115))
                fields['ATTENUATION_VALID_MASK']=propagation.astype(np.uint8)
            sweeps.append(Sweep(number,az.copy(),ranges.copy(),np.full(360,elevation),np.full(360,end.timestamp()),fields))
        meta=dict(radar_id=station.radar_id,scan_id=station.radar_id+'-20260923T120000',band=station.band,frequency_hz=station.frequency_hz,longitude_deg=lon,latitude_deg=lat,altitude_m_msl=100.,height_datum='MSL',volume_start=iso(end-timedelta(seconds=10)),volume_end=iso(end),available_at=iso(end+timedelta(seconds=1)),scan_type='volume',asset_sha256='0'*64,calibration_id='synthetic-only',attenuation_status='corrected',qc_pipeline_version='s-synthetic-v1')
        v=Volume(meta,sweeps);v.validate(station)
        directory=output/station.radar_id;directory.mkdir()
        for key,data in encode_volume(v).items():(directory/key).write_bytes(data)
        uri='s3://rainpulse/synthetic/'+station.radar_id
        index[uri]=station.radar_id
        sources.append(dict(radar_id=station.radar_id,scan_id=meta['scan_id'],input_uri=uri,**{k:meta[k] for k in ('volume_start','volume_end','available_at')}))
    request=dict(schema_version='1.0',event_type='ops.multiband.requested.v1',occurred_at=iso(target+timedelta(minutes=1)),payload=dict(mode='sx_composite',product_id='local',network_sha256=net.sha256,analysis_time=iso(target),input_cutoff=iso(target+timedelta(minutes=1)),sources=sources))
    request_path,index_path=output/'request.json',output/'index.json'
    request_path.write_text(json.dumps(request,indent=2));index_path.write_text(json.dumps(index))
    receipt=replay(path,request_path,output,index_path,output/'product')
    manifest=json.loads((output/'product/manifest.json').read_text())
    assert {s['band'] for s in manifest['sources']}=={'S','X'}
    assert not manifest['operational_eligible'] and not manifest['qpe_eligible']
    print(json.dumps({'scope':'synthetic numerical replay only','summary':receipt['summary'],'artifact_sha256':receipt['artifact_sha256']},indent=2,ensure_ascii=False))
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    make_demo(args.output)
