"""Offline height scenarios only; never publishes QC or verifies a height datum.

Each donor is varied independently against the target (49 pairs for seven
heights). Stability requires the SAME donor to observe echo in every pair.
Unknown support is not no-rain. Results do not establish a physical error bound.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import zarr
from rainpulse_algo.radar.ancillary import load_source
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.dem import VerifiedDEMTileStore
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.standalone_evidence import stage_a
from rainpulse_algo.radar.qc_geometry import (radar_beam_context_from_config,
    CrossRadarSupportReference, build_trusted_cross_radar_support)


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frozen',type=Path,required=True)
    p.add_argument('--scan',required=True)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--dem-root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--offsets',default='-30,-10,-5,0,5,10,30')
    p.add_argument('--config-dir',type=Path,default=None)
    args=p.parse_args(); offsets=tuple(float(x) for x in args.offsets.split(','))
    if not offsets or not all(np.isfinite(offsets)) or len(set(offsets))!=len(offsets) or 0 not in offsets:
        raise ValueError('finite unique scenarios including zero required')
    args.out.mkdir(parents=True,exist_ok=True)
    profile=load_qc_profile(args.repo/'configs/qc/fujian-qc-evidence-graph-v7.yaml',args.repo/'configs/qc/flag-definitions-v2.yaml')
    source=load_source(args.repo/'configs/ancillary/fujian-taiwan-v1.yaml')
    terrain=VerifiedDEMTileStore(source,args.dem_root,expected_asset_version=source.dem.asset_version,expected_config_version=source.config_version)
    root=zarr.open_group(str(args.frozen/args.scan/'normalized.zarr'),mode='r')
    qc=zarr.open_group(str(args.frozen/args.scan/'qc.zarr'),mode='r')
    provenance=json.loads(qc.attrs['radial_context'])
    config_dir=args.config_dir or args.repo/'configs/radars/fujian-egm2008-20260923'
    def beam_for(radar):
        path=config_dir/f'{radar.lower()}.yaml'
        return radar_beam_context_from_config(load_radar_config(path)),hashlib.sha256(path.read_bytes()).hexdigest()
    beam,config_hash=beam_for(root.attrs['radar_id'])
    cut=root['sweep_000']; current={'dbzh':cut['DBZH'][:],'azimuth':cut['azimuth'][:],'range':cut['range'][:],'elevation':cut['elevation'][:]}
    shape=current['dbzh'].shape
    stable_any=np.zeros(shape,bool); changed_any=np.zeros(shape,bool)
    record={'scan':args.scan,'offsets_m':offsets,'operational_eligible':False,
            'meaning':'scenario_stability_not_verified_height_conversion_or_weather_truth',
            'config_sha256':config_hash,'dem_manifest_sha256':terrain.manifest_sha256,
            'profile_hash':profile.parameters_hash,'source_datum_status':beam.altitude_datum_status,
            'source_context_fingerprint':qc.attrs.get('context_fingerprint'),
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'status':'running','references':[]}
    started=time.monotonic();status=args.out/'status.json';atomic_json(status,record)
    for artifact in provenance['artifacts']:
        if artifact['role']!='cross_radar': continue
        sid=artifact['scan_id'];path=args.frozen/sid/'normalized.zarr'
        report={'scan':sid,'source_status':artifact.get('status'),'scenarios':[]}
        record['references'].append(report)
        if artifact.get('status')!='available' or not path.exists():
            report['status']='frozen_reference_unavailable';atomic_json(status,record);continue
        donor=zarr.open_group(str(path),mode='r')
        if str(donor.attrs['scan_id'])!=sid: raise ValueError('donor scan mismatch')
        db,dh=beam_for(donor.attrs['radar_id']);report['config_sha256']=dh
        masks={}
        for num in donor['sweep_number'][:]:
            name=f'sweep_{int(num):03d}';native=adapt_sweep(donor,name,profile)
            masks[name]=native.restore(~stage_a(native,profile).donor_usable)
        ref=CrossRadarSupportReference(db.radar_id,donor,db,True,
            load_radar_config(config_dir/f'{db.radar_id.lower()}.yaml').ancillary.get('dem_asset_version')==source.dem.asset_version,masks)
        stable=np.ones(shape,bool);any_echo=np.zeros(shape,bool);all_available=np.ones(shape,bool)
        # Reuse blockage only within one donor height; target XY and requested
        # native footprint do not depend on the target antenna height.
        for donor_offset in offsets:
            cache={}
            shifted_ref=replace(ref,beam_context=replace(db,antenna_altitude_m=db.antenna_altitude_m+donor_offset))
            for target_offset in offsets:
                d=build_trusted_cross_radar_support(current,replace(beam,antenna_altitude_m=beam.antenna_altitude_m+target_offset),(shifted_ref,),terrain=terrain,
                    echo_threshold_dbzh=profile.context.echo_threshold_dbz,minimum_overlap_gates=1,
                    valid_range_dbz=profile.echo.dbzh_valid_range_dbz,blockage_cache=cache,
                    experimental_datum_assumption=True)
                available=d.available_mask==1;echo=available&(d.support_fraction>=profile.context.strong_support)
                stable &= echo;any_echo |= echo;all_available &= available
                report['scenarios'].append({'target_offset_m':target_offset,'donor_offset_m':donor_offset,'available_gates':int(available.sum()),'echo_gates':int(echo.sum())})
                record['elapsed_seconds']=time.monotonic()-started;atomic_json(status,record)
        changed=any_echo&~stable;stable_any |= stable;changed_any |= changed
        report.update(status='complete',stable_echo_gates=int(stable.sum()),sensitive_echo_gates=int(changed.sum()),always_available_gates=int(all_available.sum()))
        np.savez_compressed(args.out/f'{sid}.npz',stable_echo=stable,sensitive_echo=changed,always_available=all_available)
    np.savez_compressed(args.out/'combined.npz',stable_echo=stable_any,sensitive_echo=changed_any&~stable_any)
    record.update(status='complete',stable_echo_gates=int(stable_any.sum()),sensitive_echo_gates=int((changed_any&~stable_any).sum()),elapsed_seconds=time.monotonic()-started)
    atomic_json(status,record)

if __name__=='__main__':
    main()
