#!/usr/bin/env python3
"""Paired initial-decision experiment on frozen data; not full pipeline replay."""
import json
import sys
from pathlib import Path
import numpy as np
from rainpulse_algo.radar.qc_engine.measurement_v8.case import FrozenCase
from rainpulse_algo.radar.qc_engine.algorithms import library_evidence
from rainpulse_algo.radar.qc_engine.objects import radial_objects
from rainpulse_algo.radar.qc_engine.decision import decide
from rainpulse_algo.radar.qc_engine.support import weather_support

results=[]
for root in sorted(Path(sys.argv[1]).iterdir()):
    if not (root/'features').is_dir(): continue
    try:
        case=FrozenCase(root/'case.json')
        native, _, _=case.sweep('sweep_000')
        q=case.qc['sweep_000']; order=native.original_indices
        def get(key): return q[key][:][order] if key in q else None
        v=get('V7_VERTICAL_SUPPORT_SCORE')
        if v is None: raise ValueError('missing frozen vertical support')
        cross=get('V7_CROSS_RADAR_SUPPORT_SCORE')
        objects=radial_objects(native,case.profile.rfi_objects)
        evidence=library_evidence(native,case.profile)
        pair=[]
        for split in (False,True):
            d=decide(native,evidence,case.profile,object_evidence=objects,rfi_candidate=objects.candidate,
                weather_support=weather_support(v,cross,objects.candidate,split=split),
                temporal_persistence=get('TEMPORAL_CANDIDATE_PERSISTENCE'),
                temporal_samples=get('TEMPORAL_RFI_SAMPLE_COUNT'))
            pair.append(d.arrays['QC_ACTION'])
        before,after=pair
        row={'scan_id':root.name,'scope':'initial_decide_only_same_frozen_context_not_published_baseline',
             'changed':int((before!=after).sum()),
             'transitions':{f'{a}->{b}':int(((before==a)&(after==b)).sum()) for a in range(4) for b in range(4) if a!=b}}
        np.savez_compressed(root/'support-counterfactual.npz',before=native.restore(before),after=native.restore(after))
        results.append(row); print(row,flush=True)
    except Exception as e: results.append({'scan_id':root.name,'error':str(e)}); print(root.name,str(e),flush=True)
    target=Path(sys.argv[1])/'support-counterfactual.json'
    target.write_text(json.dumps(results,indent=2))
