#!/usr/bin/env python3
"""Explain published residuals using complete immutable RAW shape detection; no actions."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import fragment_constellation, morphology_objects, variable_morphology


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


def boundary_target_diagnosis(native,remaining,hypotheses):
    """Select actual residuals after RAW measurement, never reanchor a branch."""
    rr,cc=np.where(remaining)
    records=[]
    for original in hypotheses:
        overlap=np.zeros(len(rr),bool)
        blocks=(native.ranges[cc]//original['scale_m']).astype(int)
        for run in original['matched_original_runs']:
            overlap |= ((blocks==run['block'])&(rr>=run['native_row_start'])&
                        (rr<run['native_row_end']))
        overlap &= native.fields['DBZH'][rr,cc]>=original['level_dbz']
        if overlap.any():
            records.append(dict(original,target_overlap=int(overlap.sum()),
                target_range_min_m=float(native.ranges[cc[overlap]].min()),
                target_range_max_m=float(native.ranges[cc[overlap]].max())))
    return records


def source_segment_diagnosis(native,blocked,group,targets):
    """Recompute original held-out source models before selecting Web targets.

    Power agreement is evidence, never a pollution classification. The stored
    source ledger is immutable; models are recomputed from native RAW values.
    """
    from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import fan_joint,fan_states
    from rainpulse_algo.radar.qc_engine.review_extension.arrays import moment,runs
    if np.prod(native.shape)>2000000 or int(targets.sum())>50000:
        raise ValueError('bounded source diagnostic size exceeded')
    seed=np.asarray(group['RV2_SOURCE_LEDGER_SEED_ID'])
    parent=np.asarray(group['RV2_RAW_FAN_ID'])
    if any(v.shape!=native.shape or v.dtype!=np.dtype('uint32') for v in [seed,parent]):
        raise ValueError('original native source/parent IDs required')
    z,observed=moment(native,'DBZH')
    if ((seed>0)&(~observed|blocked)).any():
        raise ValueError('original source crossed native observation/protection')
    for key,membership in [('RV2_SOURCE_LEDGER_RANGE_M',seed>0),('RV2_RAW_FAN_RANGE_M',parent>0)]:
        coordinates=np.asarray(group[key])
        if coordinates.shape!=native.shape or not np.allclose(
            coordinates[membership],np.broadcast_to(native.ranges,native.shape)[membership],atol=.01,rtol=0):
            raise ValueError('original source/parent coordinate mismatch')
    joint,jreport=fan_joint.qualify(native,blocked,group)
    fan_joint.validate({**group,**joint},observed,blocked)
    states,sreport=fan_states.diagnose(native,blocked,{**group,**joint})
    rho,rho_ok=moment(native,'RHOHV');snr,snr_ok=moment(native,'SNR')
    weather=rho_ok&snr_ok&(rho>=.95)&(snr>=10.)
    jq=joint[fan_joint.PREFIX+'QUALIFIED_MASK']==1
    sq=states[fan_states.PREFIX+'MATCH_MASK']==1
    same_source=joint[fan_joint.PREFIX+'SOURCE_ID']==states[fan_states.PREFIX+'SOURCE_ID']
    agreement=jq&sq&same_source&~weather&~blocked
    def scalar(v):
        return float(v) if np.isfinite(v) else None
    records=[]
    safe_sections={int(row):runs(~blocked[row]) for row in np.flatnonzero(targets.any(axis=1))}
    for row,col in zip(*np.where(targets)):
        available=bool(joint[fan_joint.PREFIX+'MODEL_AVAILABLE_MASK'][row,col])
        source=int(joint[fan_joint.PREFIX+'MODEL_SOURCE_ID'][row,col])
        original=np.flatnonzero(seed[row]==source) if source else np.array([],int)
        block=int(native.ranges[col]//20000)
        section=next(((lo,hi) for lo,hi in safe_sections[int(row)] if lo<=col<hi),None)
        references=original[abs((native.ranges[original]//20000).astype(int)-block)>1]
        if section is None:references=np.array([],int)
        else:references=references[(references>=section[0])&(references<section[1])]
        kinds=np.asarray(group['RV2_SOURCE_LEDGER_KIND'])[row,original]
        records.append(dict(row=int(row),column=int(col),range_m=float(native.ranges[col]),
            original_parent_id=int(parent[row,col]),original_source_id=source,
            original_source_gates=int(len(original)),guard_excluded_reference_gates=int(len(references)),
            original_source_kind_counts={str(int(kind)):int((kinds==kind).sum()) for kind in np.unique(kinds)},
            original_source_start_m=float(native.ranges[original[0]]) if len(original) else None,
            original_source_end_m=float(native.ranges[original[-1]]) if len(original) else None,
            original_state_source_id=int(states[fan_states.PREFIX+'SOURCE_ID'][row,col]),
            original_state_id=int(states[fan_states.PREFIX+'STATE_ID'][row,col]),
            model_available=available,joint_power_match=bool(jq[row,col]),
            source_state_match=bool(sq[row,col]),same_original_source=bool(same_source[row,col] and source),
            combined_source_agreement=bool(agreement[row,col]),
            target_guard_used_for_training=False,
            raw_dbzh=scalar(z[row,col]),rhohv=scalar(rho[row,col]) if rho_ok[row,col] else None,
            snr_db=scalar(snr[row,col]) if snr_ok[row,col] else None,
            current_weather_retained=bool(weather[row,col]),protected=bool(blocked[row,col]),
            model={key:scalar(joint[fan_joint.PREFIX+key][row,col]) for key in fan_joint.MODEL_FLOATS},
            action_authority=False))
    return dict(original_joint_report=jreport,original_state_report=sreport,
        target_joint_matches=int((targets&jq).sum()),target_state_matches=int((targets&sq).sum()),
        target_same_source_agreement=int((targets&agreement).sum()),
        target_current_weather_retained=int((targets&weather).sum()),
        source_agreement_is_pollution_truth=False,action_authority=False,records=records)


def audit(snapshot, receipt, *, shoulder_windows=False, shoulder_band=False, short_subset=False, parent_footprint=False,
          geometry_evidence=False, variable_objects=False, boundary_hypotheses=False,enclosed_branches=False,
          variable_boundaries=False,source_models=False):
    if variable_boundaries and not boundary_hypotheses:
        raise ValueError('variable boundaries require full original boundary hypotheses')
    if enclosed_branches and not boundary_hypotheses:
        raise ValueError('enclosed branches require full original boundary hypotheses')
    if boundary_hypotheses and not variable_objects:
        raise ValueError('boundary hypotheses require complete variable objects')
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
    detectors = [
        ('constellation', fragment_constellation, {'segment_evidence':True,'partition_evidence':True,
             'shoulder_windows':shoulder_windows,'shoulder_band':shoulder_band,
             'short_subset_evidence':short_subset,'short_parent_footprint':parent_footprint,
             'geometry_evidence':geometry_evidence}),
        ('whole_object', morphology_objects, {'physical_windows':True})]
    if variable_objects:
        detectors.append(('variable_object', variable_morphology, {'branch_shoulders':True,
            'boundary_hypotheses':boundary_hypotheses,'enclosed_branch_hypotheses':enclosed_branches,
            'variable_boundary_hypotheses':variable_boundaries}))
    for name, module, options in detectors:
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
        if prefix+'BOUNDARY_HYPOTHESIS_MASK' in fields:
            summaries[name]['boundary_candidate_overlap']=int((remaining &
                (fields[prefix+'BOUNDARY_HYPOTHESIS_MASK']==1)).sum())
            summaries[name]['boundary_qualified_research_overlap']=int((remaining &
                (fields[prefix+'BOUNDARY_QUALIFIED_RESEARCH_MASK']==1)).sum())
            summaries[name]['boundary_hypotheses']=evidence['boundary_hypotheses']
            summaries[name]['boundary_target_hypotheses']=boundary_target_diagnosis(
                native,remaining,evidence['boundary_hypotheses'])
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
    code_manifest={module.__name__:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        for _,module,_ in detectors}
    if boundary_hypotheses:
        from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import branch_boundaries
        code_manifest[branch_boundaries.__name__]=hashlib.sha256(
            Path(branch_boundaries.__file__).read_bytes()).hexdigest()
    if source_models:
        from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import fan_joint,fan_states
        summaries['source_segments']=source_segment_diagnosis(native,blocked,a,remaining)
        for module in [fan_joint,fan_states]:
            code_manifest[module.__name__]=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    return dict(scope='complete_RAW_shapes_explaining_actual_published_residuals',
        snapshot_sha256=report['snapshot_sha256'],published_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        audit_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        detector_sha256=code_manifest,
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
    p.add_argument('--variable-objects',action='store_true',help='Explain residuals with complete variable-width RAW histories; no actions')
    p.add_argument('--boundary-hypotheses',action='store_true',help='Frozen original split/merge boundary research; requires variable objects')
    p.add_argument('--enclosed-branches',action='store_true',help='Measured branches inside frozen original outer edges; requires boundary hypotheses')
    p.add_argument('--variable-boundaries',action='store_true',help='Measure bounded RAW exterior changes around immutable seed; requires boundary hypotheses')
    p.add_argument('--source-models',action='store_true',help='Recompute original same-ray source models and held-out state agreement; no actions')
    args=p.parse_args()
    if args.output.exists():raise ValueError('new output required')
    result=audit(args.snapshot,args.receipt,shoulder_windows=args.shoulder_windows,
        shoulder_band=args.shoulder_band,short_subset=args.short_subset,parent_footprint=args.parent_footprint,
        geometry_evidence=args.geometry_evidence,variable_objects=args.variable_objects,
        boundary_hypotheses=args.boundary_hypotheses,enclosed_branches=args.enclosed_branches,
        variable_boundaries=args.variable_boundaries,source_models=args.source_models)
    result['research_options']=dict(shoulder_windows=args.shoulder_windows,
        shoulder_band=args.shoulder_band,short_subset=args.short_subset,parent_footprint=args.parent_footprint,
        geometry_evidence=args.geometry_evidence,variable_objects=args.variable_objects,
        boundary_hypotheses=args.boundary_hypotheses,enclosed_branches=args.enclosed_branches,
        variable_boundaries=args.variable_boundaries,source_models=args.source_models)
    with args.output.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='detectors'}))
    for name,value in result['detectors'].items():
        print(name,json.dumps({k:v for k,v in value.items() if k not in ('objects','records','original_target_parents','geometry_hypotheses','boundary_hypotheses','boundary_target_hypotheses')}))


if __name__=='__main__':main()
