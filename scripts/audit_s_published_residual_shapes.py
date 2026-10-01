#!/usr/bin/env python3
"""Explain published residuals using complete immutable RAW shape detection; no actions."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import fragment_constellation, morphology_objects


def audit(snapshot, receipt):
    report = json.loads(receipt.read_text())
    if report.get('scope') != 'exact_Web_consumed_stored_QC_not_replay':
        raise ValueError('actual published QC receipt required')
    if hashlib.sha256(snapshot.read_bytes()).hexdigest() != report['snapshot_sha256']:
        raise ValueError('immutable snapshot mismatch')
    with np.load(snapshot, allow_pickle=False) as data:
        a = {k: data[k] for k in data.files}
    meta = json.loads(str(a['METADATA']))
    if meta['scan_id'] != report['web_frame_identity']['web_scan_id']:
        raise ValueError('published/native scan mismatch')
    remaining = np.zeros(a['RAW'].shape, bool)
    seen = set()
    for record in report['target_records']:
        row, col = record['row'], record['column']
        if type(row) is not int or type(col) is not int or (row,col) in seen:
            raise ValueError('unique integer native target indices required')
        seen.add((row,col))
        if not (0 <= row < remaining.shape[0] and 0 <= col < remaining.shape[1]):
            raise ValueError('invalid native target index')
        if (not np.isclose(a['RAW'][row, col], record['raw_dbzh'], atol=.0001, rtol=0) or
            not np.isclose(a['AZIMUTH'][row], record['azimuth_deg'], atol=.0001, rtol=0) or
            not np.isclose(a['RANGE'][col], record['range_m'], atol=.001, rtol=0)):
            raise ValueError('native target coordinate/measurement mismatch')
        remaining[row, col] = record['renderer_visible']
    if (len(seen) != report['target_gates'] or
        int(remaining.sum()) != report['renderer_eligible_visible_gates']):
        raise ValueError('published target count mismatch')
    native = SimpleNamespace(shape=a['RAW'].shape,
        fields={'DBZH':a['RAW'].copy(), **{k[7:]:v for k,v in a.items() if k.startswith('MOMENT_')}},
        field_available={k[10:]:v for k,v in a.items() if k.startswith('AVAILABLE_')},
        ranges=a['RANGE'], azimuth=a['AZIMUTH'], geometry_good=a['GEOMETRY_GOOD'],
        gap_after=a['GAP_AFTER'], gate_spacing_m=float(np.median(np.diff(a['RANGE']))))
    # Selection is used only AFTER full RAW detection, never as its input.
    blocked = (a['WEATHER']==1) | (a['CONFLICTS']==1)
    summaries = {}
    for name, module, options in (
        ('constellation', fragment_constellation, {'segment_evidence':True,'partition_evidence':True}),
        ('whole_object', morphology_objects, {'physical_windows':True})):
        fields, evidence = module.detect(native, blocked, **options)
        prefix = module.PREFIX
        ids, counts = np.unique(fields[prefix+'ID'][remaining], return_counts=True)
        lookup = {r['id']:r for r in evidence['objects']}
        summaries[name] = dict(candidate_overlap=int((remaining & (fields[prefix+'MASK']==1)).sum()),
            strong_overlap=int((remaining & (fields[prefix+'STRONG_MASK']==1)).sum()),
            weather_proxy_overlap=int((remaining & (fields[prefix+'WEATHER_VETO_MASK']==1)).sum()),
            unmatched=int((remaining & (fields[prefix+'ID']==0)).sum()),
            objects=[dict(target_overlap=int(count), **lookup[int(ident)])
                     for ident,count in zip(ids,counts) if ident])
    assert np.array_equal(native.fields['DBZH'], a['RAW'], equal_nan=True)
    return dict(scope='complete_RAW_shapes_explaining_actual_published_residuals',
        snapshot_sha256=report['snapshot_sha256'],published_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        remaining_visible=int(remaining.sum()),detectors=summaries,product_writes=False,
        action_authority=False,independent_weather_truth=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot',type=Path);p.add_argument('receipt',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise ValueError('new output required')
    result=audit(args.snapshot,args.receipt)
    with args.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='detectors'}))
    for name,value in result['detectors'].items():
        print(name,json.dumps({k:v for k,v in value.items() if k!='objects'}))


if __name__=='__main__':main()
