"""Offline single-day background. Never a qualified production clutter asset.

Nearest observed samples on a 1 degree / 1 km research grid. No interpolation,
no missing-to-no-echo conversion. Recurrence denominator includes every decoded
volume, hence unknowns cannot manufacture positive recurrence evidence.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.fmt import decode_fmt_volume

AZ = np.arange(360, dtype=float)
RANGE = np.arange(500., 75000., 1000.)


def sample(sweep):
    distance = abs((sweep.azimuth_deg[:, None] - AZ + 180) % 360 - 180)
    rays = distance.argmin(axis=0)
    rg = abs(sweep.range_m[:, None] - RANGE)
    gates = rg.argmin(axis=0)
    good = (distance[rays, np.arange(360)] <= .6)[:, None]
    good = good & (rg[gates, np.arange(len(RANGE))] <= 500)[None, :]
    z = sweep.fields['DBZH'][rays[:, None], gates[None, :]]
    return np.where(good, z, np.nan)


def build(input_dir, config_dir, output):
    output.mkdir(parents=True, exist_ok=True)
    for station in ('Z9591', 'Z9598'):
        cfg = load_radar_config(config_dir / (station.lower()+'.yaml'))
        cuts, records, failures, hashes = {}, [], [], set()
        for path in sorted((input_dir/station).glob('*.bz2')):
            try:
                volume = decode_fmt_volume(path, cfg)
                if volume.site.code.upper() != station:
                    raise ValueError('station mismatch')
                if volume.input_sha256 in hashes:
                    raise ValueError('duplicate content')
                hashes.add(volume.input_sha256)
                for s in volume.sweeps:
                    if 'DBZH' not in s.fields:
                        continue
                    key = f'{s.source_sweep_number:03d}'
                    z = sample(s)
                    c = cuts.setdefault(key, dict(elevation=s.nominal_elevation_deg,
                        count=np.zeros(z.shape, 'uint16'), hits=np.zeros(z.shape, 'uint16'),
                        total=np.zeros(z.shape), total2=np.zeros(z.shape), samples=[]))
                    if abs(c['elevation']-s.nominal_elevation_deg) > .1:
                        raise ValueError('cut elevation changed')
                    valid = np.isfinite(z)
                    c['samples'].append(z.astype('float32'))
                    c['count'] += valid
                    c['hits'] += valid & (z >= 0.)
                    c['total'] += np.where(valid,z,0.)
                    c['total2'] += np.where(valid,z*z,0.)
                records.append(dict(file=path.name,sha256=volume.input_sha256,
                    start=volume.volume_start_time.isoformat(),end=volume.volume_end_time.isoformat()))
                print(station,len(records),path.name,flush=True)
            except Exception as exc:
                failures.append(dict(file=path.name,error=str(exc)))
                # Fail closed: a partly accumulated volume must never be published.
                raise RuntimeError(f'{path}: {exc}') from exc
        if not records:
            raise ValueError(f'no decoded volumes: {station}')
        arrays = dict(azimuth=AZ,range_m=RANGE)
        summary = []
        for key,c in cuts.items():
            mean=np.divide(c['total'],c['count'],out=np.full(c['total'].shape,np.nan),where=c['count']>0)
            recurrence=c['hits']/len(records)
            samples=np.stack(c['samples'])
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',RuntimeWarning)
                quantiles=np.nanquantile(samples,[.1,.5,.9],axis=0)
            arrays[key+'_observed_fraction']=c['count']/len(records)
            for q,value in zip(('p10','p50','p90'),quantiles):arrays[key+'_'+q]=value
            for threshold in (-10,-5,0,5,10):
                arrays[key+f'_ge_{threshold}_fraction']=((np.isfinite(samples))&(samples>=threshold)).sum(axis=0)/len(records)
            for name,value in dict(observed_count=c['count'],echo_count=c['hits'],mean=mean,
                                   recurrence_lower=recurrence,elevation=c['elevation']).items():
                arrays[key+'_'+name]=np.asarray(value)
            summary.append(dict(cut=key,elevation=c['elevation'],persistent_cells=int(((recurrence>=.8)&(c['count']>=20)).sum())))
        meta=dict(station=station,kind='single_day_offline_pilot',operational_eligible=False,
            clear_air_label_source='user_supplied_unverified_by_independent_weather_data',
            future_background_for_august_replay=True,volumes=len(records),inputs=records,
            failures=failures,cuts=summary,echo_threshold_dbz=0.,distribution_version='weak-background-v2',
            quantiles=[.1,.5,.9],thresholds_dbz=[-10,-5,0,5,10],
            denominator='all_decoded_volumes; missing remains unknown; lower recurrence only')
        np.savez_compressed(output/(station+'.npz'),**arrays)
        (output/(station+'.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in meta.items() if k!='inputs'}),flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('input',type=Path);p.add_argument('configs',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();build(a.input,a.configs,a.output)
