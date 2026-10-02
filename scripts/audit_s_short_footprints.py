#!/usr/bin/env python3
"""Compare complete RAW short/weak-parent research masks; never publish QC actions."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.fragment_constellation import detect, PREFIX


def replay(path, *, geometry_evidence=False):
    with np.load(path,allow_pickle=False) as data:
        a={k:data[k] for k in data.files}
    meta=json.loads(str(a['METADATA']))
    if not meta.get('web_frame_identity'):
        raise ValueError('complete Web-bound native RAW snapshot required')
    native=SimpleNamespace(shape=a['RAW'].shape,
        fields={'DBZH':a['RAW'].copy(),**{k[7:]:v for k,v in a.items() if k.startswith('MOMENT_')}},
        field_available={k[10:]:v for k,v in a.items() if k.startswith('AVAILABLE_')},
        ranges=a['RANGE'],azimuth=a['AZIMUTH'],geometry_good=a['GEOMETRY_GOOD'],gap_after=a['GAP_AFTER'])
    blocked=(a['WEATHER']==1)|(a['CONFLICTS']==1)|(a['RV2_BARRED_MASK']==1)
    options=dict(partition_evidence=True,shoulder_windows=True,short_subset_evidence=True)
    baseline,_=detect(native,blocked,**options)
    extended,report=detect(native,blocked,short_parent_footprint=True,
                           geometry_evidence=geometry_evidence,**options)
    if not all(np.array_equal(baseline[k],extended[k]) for k in baseline):
        raise ValueError('existing detector arrays changed')
    short=extended[PREFIX+'SHORT_RESEARCH_MASK']==1
    parent=extended[PREFIX+'SHORT_PARENT_RESEARCH_MASK']==1
    proposal=short|parent
    if (proposal&blocked).any() or (proposal&(extended[PREFIX+'WEATHER_VETO_MASK']==1)).any():
        raise ValueError('protected or weather-proxy overlap')
    if not np.array_equal(native.fields['DBZH'],a['RAW'],equal_nan=True):
        raise ValueError('RAW changed')
    geometry=extended.get(PREFIX+'GEOMETRY_RESEARCH_MASK',np.zeros(native.shape,'uint8'))==1
    if (geometry&blocked).any() or (geometry&(extended[PREFIX+'WEATHER_VETO_MASK']==1)).any():
        raise ValueError('geometry protected or weather-proxy overlap')
    proofs=[p['geometry_hypothesis'] for o in report['objects']+report.get('standalone_geometry_objects',[])
            for p in o.get('original_distance_partitions',[]) if 'geometry_hypothesis' in p]
    from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import fragment_constellation,shape_constellation
    code_sha256={name:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                 for name,module in [('detector',fragment_constellation),('geometry_assessor',shape_constellation)]}
    return dict(scan_id=meta['scan_id'],sweep=meta['sweep'],snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        code_sha256=code_sha256,geometry_evidence=geometry_evidence,
        standalone_geometry_objects=len(report.get('standalone_geometry_objects',[])),
        qualified_geometry_hypotheses=sum(p['qualified'] for p in proofs),
        quantized_weather_ambiguous_hypotheses=sum(p['qualified'] and p['fixed_physical_width_compatible'] for p in proofs),
        geometry_hypothesis_gates=int(geometry.sum()),geometry_only_gates=int((geometry&~proposal).sum()),
        short_research_gates=int(short.sum()),parent_research_gates=int(parent.sum()),
        combined_research_gates=int(proposal.sum()),parent_only_gates=int((parent&~short).sum()),
        protected_overlap=0,weather_proxy_overlap=0,existing_arrays_unchanged=True,raw_unchanged=True,
        recursive_growth=report['recursive_growth'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots',nargs='+',type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--geometry-evidence',action='store_true')
    args=p.parse_args()
    if args.output.exists():raise ValueError('new output required')
    cuts=[];seen=set()
    for path in args.snapshots:
        row=replay(path,geometry_evidence=args.geometry_evidence);identity=(row['scan_id'],row['sweep'])
        if identity in seen:raise ValueError('duplicate scan/cut')
        seen.add(identity);cuts.append(row);print(json.dumps(row),flush=True)
    result=dict(scope='complete_native_RAW_short_parent_research',cuts=cuts,
        action_authority=False,product_writes=False,independent_weather_truth=False)
    with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')


if __name__=='__main__':main()
