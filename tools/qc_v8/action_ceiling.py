"""Read-only V7 proposal/action bottleneck counts, NOT meteorological skill.

This never changes gates or runs a candidate classifier. The upper bound asks:
with the stored V5 corroboration and numeric-platform predicates frozen, how
many currently quantitative-eligible selected gates COULD be confirmed merely
by enlarging V7's review proposal? It is not a prediction or a safe-deletion set.

Requires a local QC Zarr, exact asset/scan IDs and its actual V7 YAML/flag file.
Optional regions JSON uses ORIGINAL ray/gate indices from verified PNG traces:
{"asset_id":"...","scan_id":"...","sweep":"sweep_000","regions":[
 {"id":"review-1","rays":[...],"gate_start":...,"gate_end_exclusive":...,
 "role":"unlabeled"}]}. No screenshot coordinates are accepted.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import zarr

REASONS={"PROTECTED":1,"NO_EVIDENCE":2,"SHORT_ATOM":4,"WIDE_INTERSECTION":8,
         "SHORT_GROUP":16,"EXCESS_GAP":32,"REVIEW":64,"SHAPE_MODEL":128,
         "MEASUREMENT_UNAVAILABLE":256,"CONFIRMED":512,"QUARANTINED":1024,
         "UNRESOLVED":2048,"SINGLE_SHOULDER_ONLY":4096}

def summarize(a, roi):
    observed=a['VALID_MASK']==1
    selected=observed & roi
    survivors=selected & (a['QPE_ELIGIBLE_MASK']==1)
    potential=(a['V5_POL_CORROBORATED_MASK']==1)&(a['V5_RANGE_MODEL_CODE']!=3)
    review=a['V7_GRAPH_REVIEW_MASK']==1
    # V5 stores reliability-filtered pol corroboration. CLI verifies the two
    # stages use identical reliability thresholds; ignore additional geometry
    # constraints to keep this an upper bound, never an achieved result.
    ceiling=survivors & potential
    reasons=a['V7_GRAPH_STAGE_REASON'].astype('uint32')
    count=lambda mask:int(np.count_nonzero(selected & mask))
    return {
      'selected_observed_gates':int(selected.sum()),'still_quantitative_eligible':int(survivors.sum()),
      'graph_proposal':count(a['V7_GRAPH_PROPOSAL_MASK']==1),'graph_review':count(review),
      'graph_shape_model':count(a['V7_GRAPH_MODEL_MASK']==1),
      'confirmed_action_gates':count(a['QC_ACTION']==2),'quarantined_gates':count(a['RFI_QUARANTINE_MASK']==1),
      'conditional_confirmation_upper_bound_by_proposal_only':int(ceiling.sum()),
      'potential_but_not_currently_reviewed':int((ceiling & ~review).sum()),
      'reviewed_still_eligible_without_pol_corroboration':int((survivors & review & ~potential).sum()),
      'unexpected_reviewed_pol_supported_still_eligible':int((ceiling & review).sum()),
      'survivor_reason_counts_overlapping':{k:int((survivors & ((reasons&v)!=0)).sum()) for k,v in REASONS.items()},
      'unrecorded_shape_failure_subreason':None,
      'unrecorded_shape_failure_explanation':'recompute frozen group model; absence of model alone does not identify which condition failed',
      'precision':None,'recall':None,'meaning':'engineering statistics; unknown labels; upper bound is conditional, not safe-to-remove count',
    }

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('qc-zarr','profile','flags','output'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--asset-id',required=True);p.add_argument('--scan-id',required=True)
    p.add_argument('--sweep',required=True);p.add_argument('--regions',type=Path)
    x=p.parse_args()
    if x.output.exists() or not x.qc_zarr.is_dir():raise ValueError('local existing QC and new output required')
    from rainpulse_algo.radar.qc import load_qc_profile
    prof=load_qc_profile(x.profile,x.flags)
    if prof.pipeline_version!='qc-opensource-7.0.0':raise ValueError('this auditor is pinned to V7 predicates')
    if prof.cross_radar.minimum_snr_db != prof.residual.minimum_snr_db:raise ValueError('different corroboration reliability: no safe stored-mask upper bound')
    root=zarr.open_group(str(x.qc_zarr),mode='r')
    for key,expected in [('contract_name','rainpulse.qc-radar-volume'),('asset_id',x.asset_id),('scan_id',x.scan_id),('qc_pipeline_version',prof.pipeline_version),('qc_parameters_sha256',prof.parameters_hash)]:
        if str(root.attrs.get(key))!=str(expected):raise ValueError(f'{key} identity mismatch or unavailable')
    g=root[x.sweep];shape=(len(g['azimuth']),len(g['range']))
    names=['VALID_MASK','QC_ACTION','RFI_QUARANTINE_MASK','QPE_ELIGIBLE_MASK','V5_POL_CORROBORATED_MASK','V5_RANGE_MODEL_CODE','V7_GRAPH_PROPOSAL_MASK','V7_GRAPH_REVIEW_MASK','V7_GRAPH_MODEL_MASK','V7_GRAPH_STAGE_REASON']
    data={};digests={}
    for name in names:
        if name not in g or g[name].shape!=shape:raise ValueError(f'missing or wrong-shape field: {name}')
        data[name]=g[name][:]
        if not np.isfinite(data[name]).all():raise ValueError(f'invalid {name}')
        digests[name]=hashlib.sha256(data[name].dtype.str.encode()+str(shape).encode()+np.ascontiguousarray(data[name]).tobytes()).hexdigest()
    regions=[]
    if x.regions:
        spec=json.loads(x.regions.read_text())
        if spec.get('asset_id')!=x.asset_id or spec.get('scan_id')!=x.scan_id or spec.get('sweep')!=x.sweep:raise ValueError('ROI identity mismatch')
        for region in spec['regions']:
            rays=region['rays'];lo=region['gate_start'];hi=region['gate_end_exclusive']
            if not rays or any(type(r)!=int or not 0<=r<shape[0] for r in rays) or type(lo)!=int or type(hi)!=int or not 0<=lo<hi<=shape[1]:raise ValueError('ROI must use actual bounded ray/gate indices')
            roi=np.zeros(shape,bool);roi[rays,lo:hi]=True
            regions.append(dict(region_id=region['id'],role=region.get('role','unlabeled'),**summarize(data,roi)))
    else:regions=[dict(region_id='whole_cut_unlabeled',**summarize(data,np.ones(shape,bool)))]
    out=dict(source_commit='1669ed1dad26b649eb69a083e8f6e8807c69d56b',asset_id=x.asset_id,scan_id=x.scan_id,sweep=x.sweep,pipeline=prof.pipeline_version,profile_hash=prof.parameters_hash,used_array_sha256=digests,rows=regions,meteorological_skill='NOT_MEASURED',context_equivalence='NOT_PROVEN_BY_THIS_SCRIPT',sample_counting='group runs by physical scan; this report does not merge contexts')
    x.output.parent.mkdir(parents=True,exist_ok=True)
    with x.output.open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2,allow_nan=False)
if __name__=='__main__':main()
