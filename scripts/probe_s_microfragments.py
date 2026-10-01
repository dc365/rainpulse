#!/usr/bin/env python3
"""Factorial native microfragment/observed-noise probe; never applies QC actions."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'algorithms/rainpulse_algo/radar/qc_engine'
for name,path in (('sparse_probe_runtime',[]),('sparse_probe_runtime.engine',[str(ENGINE)])):
    mod=types.ModuleType(name); mod.__path__=path; sys.modules[name]=mod
DETECTOR=importlib.import_module('sparse_probe_runtime.engine.review_extension.radial_revision.discontinuous')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots',type=Path,nargs='+')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--azimuth',type=float,nargs=2)
    p.add_argument('--range-min',type=float,default=0.)
    p.add_argument('--maximum-half-beams',type=int,default=2)
    p.add_argument('--maximum-width-deg',type=float,default=3.)
    args=p.parse_args()
    if args.output.exists():raise ValueError('new output directory required')
    args.output.mkdir(parents=True)
    from audit_s_source_footprint import select_roi
    records=[];seen=set()
    for path in args.snapshots:
        with np.load(path,allow_pickle=False) as data:a={k:data[k] for k in data.files}
        meta=json.loads(str(a['METADATA']));case=(meta['scan_id'],meta['sweep'])
        if case in seen:raise ValueError('duplicate scan/cut input')
        seen.add(case)
        fields={'DBZH':a['RAW'],**{k[7:]:v for k,v in a.items() if k.startswith('MOMENT_')}}
        native=types.SimpleNamespace(shape=a['RAW'].shape,fields=fields,
            field_available={k[10:]:v for k,v in a.items() if k.startswith('AVAILABLE_')},
            ranges=a['RANGE'],azimuth=a['AZIMUTH'],geometry_good=a['GEOMETRY_GOOD'],gap_after=a['GAP_AFTER'])
        blocked=(a['WEATHER']==1)|(a['CONFLICTS']==1)|(a['RV2_BARRED_MASK']==1)
        roi=select_roi(a['AZIMUTH'],a['RANGE'],range_min=args.range_min,
            azimuth_start=args.azimuth[0] if args.azimuth else None,
            azimuth_end=args.azimuth[1] if args.azimuth else None)
        remaining=a['BEFORE']&~a['ADDED']&roi
        variants=[]
        for micro in (False,True):
            for noise in (False,True):
                arrays,detail=DETECTOR.probe(native,blocked,
                    beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'),
                    microfragments=micro,measured_noise=noise,
                    maximum_half_beams=args.maximum_half_beams, maximum_width_deg=args.maximum_width_deg)
                candidate=arrays['RV2_SPARSE_PROBE_CANDIDATE_MASK']==1
                evidence=arrays['RV2_SPARSE_PROBE_EVIDENCE_MASK']==1
                # Diagnostic evidence is not weather truth and never authorizes deletion.
                rho=fields.get('RHOHV',np.full(native.shape,np.nan))
                snr=fields.get('SNR',np.full(native.shape,np.nan))
                reliable_weather=(native.field_available.get('RHOHV',False)&
                    native.field_available.get('SNR',False)&np.isfinite(rho)&np.isfinite(snr)&(rho>=.95)&(snr>=10.))
                name=path.stem+f'-micro{int(micro)}-noise{int(noise)}.npz'
                proof={'scan_id':meta['scan_id'],'sweep':meta['sweep'],
                    'snapshot_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),**detail}
                np.savez_compressed(args.output/name,METADATA=np.array(json.dumps(proof)),**arrays)
                codes=arrays['RV2_SPARSE_PROBE_REJECTION_CODE']
                stages={0:'not_eligible_target',1:'no_local_narrow_corridor',2:'below_fragment_length',
                    3:'below_four_fragments',4:'below_8km_observed_support',5:'below_80km_span',
                    6:'below_four_distance_blocks',7:'insufficient_radial_aspect',8:'nominated'}
                partition={name:int((remaining&(codes==code)).sum()) for code,name in stages.items()}
                if sum(partition.values())!=int(remaining.sum()):raise ValueError('rejection partition incomplete')
                variants.append({**detail,'rejections_remaining':partition,'candidate_remaining':int((remaining&candidate).sum()),
                    'evidence_remaining':int((remaining&evidence).sum()),
                    'weather_like_evidence_remaining':int((remaining&evidence&reliable_weather).sum()),
                    'evidence_snapshot':name})
        records.append({'case':path.stem,'scan_id':meta['scan_id'],'sweep':meta['sweep'],
            'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'web_identity_present':bool(meta.get('web_frame_identity')),
            'remaining_selected':int(remaining.sum()),'variants':variants})
        print(path.stem,[(v['microfragments'],v['measured_noise'],v['candidate_remaining'],v['evidence_remaining']) for v in variants],flush=True)
    (args.output/'report.json').write_text(json.dumps({'scope':'nomination_probe_not_QC_product',
        'independent_weather_truth':False,'actions':0,'product_writes':False,
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'detector_sha256':hashlib.sha256((ENGINE/'review_extension/radial_revision/discontinuous.py').read_bytes()).hexdigest(),
        'selection':{'azimuth':args.azimuth,'range_min':args.range_min},'cases':records},indent=2))


if __name__=='__main__':main()
