#!/usr/bin/env python3
"""Opt-in native angular mapping experiment; frozen artifacts stay unchanged."""
import json
import sys
from pathlib import Path
import numpy as np
from rainpulse_algo.radar.qc_engine.measurement_v8.case import FrozenCase
from rainpulse_algo.radar.qc_engine.measurement_v8.native import run_native
from rainpulse_algo.radar.qc_engine.measurement_v8.schema import NativeConfig
from rainpulse_algo.radar.qc_engine.measurement_v8.io import file_hash

root=Path(sys.argv[1]); output=Path(sys.argv[2]); output.mkdir(parents=True,exist_ok=True)
binary='/usr/local/bin/emitter-core'; sha=file_hash(binary)
cfg=NativeConfig(angular_mapping=True,angular_step_deg=1.0,tile_rays=8)
reports=[]
for path in sorted(root.glob('*/case.json')):
    target=output/path.parent.name;target.mkdir(exist_ok=True)
    try:
        case=FrozenCase(path);native,_,_=case.sweep('sweep_000')
        result=run_native(native,cfg,binary,sha)
        np.savez_compressed(target/'native.npz',**{f'emitter{k}':native.restore(v) for k,v in result.scores.items()})
        report={'scan_id':path.parent.name,'radar_id':case.spec.expected_radar_id,**result.summary}
        (target/'summary.json').write_text(json.dumps(report,indent=2))
        reports.append(report)
        print(path.parent.name,result.summary['status'],result.summary['calls'],result.summary.get('available_gates'),flush=True)
    except Exception as e:
        reports.append({'scan_id':path.parent.name,'error':str(e)});print(path.parent.name,str(e),flush=True)
    temp=output/'progress.tmp';temp.write_text(json.dumps(reports,indent=2));temp.replace(output/'progress.json')
