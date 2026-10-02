#!/usr/bin/env python3
"""Explain published residuals using complete immutable RAW shape detection; no actions."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import fragment_constellation, morphology_objects


def original_parent_target_diagnosis(native,targets,proposal,evidence):
    """Label COMPLETE RAW before selecting residuals; observe, never classify QC."""
    from scipy.ndimage import label
    from rainpulse_algo.radar.qc_engine.review_extension.arrays import native_geometry,moment
    r,az,dr,good,gaps=native_geometry(native)
    z,observed=moment(native,'DBZH')
    steps=(np.diff(az)+180)%360-180
    spacing=float(np.median(steps[steps>0]))
    breaks=gaps[:-1]|(steps<=0)|(steps>1.5*spacing)
    authorized=set()
    for obj in evidence['objects']:
        for part in obj.get('original_distance_partitions',[]):
            if part.get('measured_subset',{}).get('qualified'):
                authorized.update((obj['native_segment_start'],identity)
                    for identity in part['original_lower_parent_ids'])
    results=[]
    for segment in np.split(np.arange(len(az)),np.flatnonzero(breaks)+1):
        angle=np.rad2deg(np.unwrap(np.deg2rad(az[segment])))
        labels,_=label(observed[segment]&good[segment,None]&(z[segment]>=10),np.ones((3,3)))
        selected=targets[segment]
        for identity in np.unique(labels[selected]):
            if not identity:continue
            rr,cc=np.where(labels==identity)
            tr,tc=np.where(selected&(labels==identity))
            results.append(dict(native_segment_start=int(segment[0]),original_lower_parent_id=int(identity),
                original_gate_count=len(rr),range_min_m=float(r[cc].min()),range_max_m=float(r[cc].max()+dr),
                original_radial_span_m=float(np.ptp(r[cc])+dr),actual_range_support_m=float(len(np.unique(cc))*dr),
                original_left_deg=float(angle[rr].min()-spacing/2),original_right_deg=float(angle[rr].max()+spacing/2),
                originally_authorized_parent=(int(segment[0]),int(identity)) in authorized,
                targets=[dict(row=int(segment[row]),column=int(col),range_m=float(r[col]),
                    raw_dbzh=float(z[segment[row],col]),research_nominated=bool(proposal[segment[row],col]))
                    for row,col in zip(tr,tc)]))
    return results


def audit(snapshot, receipt, *, shoulder_windows=False, shoulder_band=False, short_subset=False, parent_footprint=False,
          geometry_evidence=False):
    if shoulder_band and not shoulder_windows:
        raise ValueError('shoulder band requires measured windows')
    if short_subset and not shoulder_windows:
        raise ValueError('short subset requires measured windows')
    if parent_footprint and not short_subset:
        raise ValueError('parent footprint requires short subset evidence')
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
    blocked = (a['WEATHER']==1) | (a['CONFLICTS']==1) | (a['RV2_BARRED_MASK']==1)
    summaries = {}
    for name, module, options in (
        ('constellation', fragment_constellation, {'segment_evidence':True,'partition_evidence':True,
             'shoulder_windows':shoulder_windows,'shoulder_band':shoulder_band,
             'short_subset_evidence':short_subset,'short_parent_footprint':parent_footprint,
             'geometry_evidence':geometry_evidence}),
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
        if prefix+'SHORT_RESEARCH_MASK' in fields:
            mask=fields[prefix+'SHORT_RESEARCH_MASK']==1
            summaries[name]['short_research_overlap']=int((mask&remaining).sum())
            summaries[name]['short_research_total']=int(mask.sum())
            summaries[name]['short_protected_overlap']=int((mask&blocked).sum())
        if prefix+'SHORT_PARENT_RESEARCH_MASK' in fields:
            mask=fields[prefix+'SHORT_PARENT_RESEARCH_MASK']==1
            summaries[name]['parent_research_overlap']=int((mask&remaining).sum())
            summaries[name]['parent_research_total']=int(mask.sum())
            summaries[name]['parent_protected_overlap']=int((mask&blocked).sum())
            combined=mask|(fields[prefix+'SHORT_RESEARCH_MASK']==1)
            summaries[name]['combined_research_overlap']=int((combined&remaining).sum())
            summaries[name]['combined_research_total']=int(combined.sum())
            summaries[name]['original_target_parents']=original_parent_target_diagnosis(
                native,remaining,combined,evidence)
            summaries[name]['unmatched_original_parent_targets']=int(remaining.sum())-sum(
                len(p['targets']) for p in summaries[name]['original_target_parents'])
        if prefix+'GEOMETRY_RESEARCH_MASK' in fields:
            mask=fields[prefix+'GEOMETRY_RESEARCH_MASK']==1
            summaries[name]['geometry_hypothesis_overlap']=int((mask&remaining).sum())
            summaries[name]['geometry_hypothesis_total']=int(mask.sum())
            summaries[name]['geometry_protected_overlap']=int((mask&blocked).sum())
            all_objects=evidence['objects']+evidence.get('standalone_geometry_objects',[])
            summaries[name]['geometry_hypotheses']=[dict(native_segment_start=o['native_segment_start'],
                original_components=p['original_components'],original_lower_parent_ids=p['original_lower_parent_ids'],
                standalone=bool(o.get('standalone_geometry_only')),**p['geometry_hypothesis'])
                for o in all_objects for p in o.get('original_distance_partitions',[])
                if 'geometry_hypothesis' in p]
    assert np.array_equal(native.fields['DBZH'], a['RAW'], equal_nan=True)
    return dict(scope='complete_RAW_shapes_explaining_actual_published_residuals',
        snapshot_sha256=report['snapshot_sha256'],published_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        remaining_visible=int(remaining.sum()),detectors=summaries,product_writes=False,
        action_authority=False,independent_weather_truth=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot',type=Path);p.add_argument('receipt',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--shoulder-windows',action='store_true',help='Research measured 1/2/5km bilateral windows')
    p.add_argument('--shoulder-band',action='store_true',help='Research bounded exterior band; requires windows')
    p.add_argument('--short-subset',action='store_true',help='Research independently measured short subsets; requires windows')
    p.add_argument('--parent-footprint',action='store_true',help='One-hop frozen weak-parent research; requires short subset')
    p.add_argument('--geometry-evidence',action='store_true',help='Shadow complete transverse-shard geometry; no action authority')
    args=p.parse_args()
    if args.output.exists():raise ValueError('new output required')
    result=audit(args.snapshot,args.receipt,shoulder_windows=args.shoulder_windows,
        shoulder_band=args.shoulder_band,short_subset=args.short_subset,parent_footprint=args.parent_footprint,
        geometry_evidence=args.geometry_evidence)
    result['research_options']=dict(shoulder_windows=args.shoulder_windows,
        shoulder_band=args.shoulder_band,short_subset=args.short_subset,parent_footprint=args.parent_footprint,
        geometry_evidence=args.geometry_evidence)
    with args.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='detectors'}))
    for name,value in result['detectors'].items():
        print(name,json.dumps({k:v for k,v in value.items() if k not in ('objects','original_target_parents','geometry_hypotheses')}))


if __name__=='__main__':main()
