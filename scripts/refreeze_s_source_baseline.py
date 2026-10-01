#!/usr/bin/env python3
"""Freeze a current source-stage baseline only after diagnostic-only drift proof.

Never overwrite the old snapshot or relax morphology replay's exact comparison.
No artifact/product writes; Web identity refers to the original captured inputs.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import types
import numpy as np

TRACE = 'RV2_SOURCE_FOOTPRINT_REJECTION_CODE'


def diagnostic_changes(captured, fresh):
    changes = {}
    for key, value in fresh.items():
        if key not in captured:
            raise ValueError('fresh baseline has uncaptured field: '+key)
        old = captured[key]
        if old.shape != value.shape or old.dtype != value.dtype:
            raise ValueError('baseline shape/dtype drift: '+key)
        if np.array_equal(old, value, equal_nan=True):
            continue
        if key != TRACE or old.dtype != np.dtype('uint8') or not np.isin(old,range(11)).all() or not np.isin(value,range(11)).all():
            raise ValueError('non-diagnostic baseline drift: '+key)
        pairs, counts = np.unique(np.column_stack((old[old!=value],value[old!=value])),axis=0,return_counts=True)
        changes[key] = dict(count=int((old!=value).sum()),
            old_new_counts=[list(map(int,pair))+[int(count)] for pair,count in zip(pairs,counts)])
    return changes


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import audit_s_morphology_objects as runtime
    engine=importlib.import_module('morph_object_runtime.engine.review_extension.radial_revision.engine')
    config=importlib.import_module('morph_object_runtime.engine.review_extension.radial_revision.config')
    with np.load(args.snapshot,allow_pickle=False) as data:
        captured={key:data[key] for key in data.files}
    meta=json.loads(str(captured['METADATA']))
    if not meta.get('web_frame_identity'):
        raise ValueError('Web-paired immutable input required')
    cfg=config.RadialRevisionConfig.model_validate(meta['config'])
    if cfg.fragment_line is not None and any((cfg.fragment_line.whole_object_morphology_enabled,
        cfg.fragment_line.radial_backbone_enabled,cfg.fragment_line.fragment_constellation_enabled)):
        raise ValueError('refreeze requires enhancements disabled')
    native=types.SimpleNamespace(shape=captured['RAW'].shape,
        fields={'DBZH':captured['RAW'],**{k[7:]:v for k,v in captured.items() if k.startswith('MOMENT_')}},
        field_available={k[10:]:v for k,v in captured.items() if k.startswith('AVAILABLE_')},
        ranges=captured['RANGE'],azimuth=captured['AZIMUTH'],geometry_good=captured['GEOMETRY_GOOD'],
        gap_after=captured['GAP_AFTER'],gate_spacing_m=float(np.median(np.diff(captured['RANGE']))))
    fresh,_=engine.evaluate(native,cfg,captured['SEED'],captured['RESIDUAL_DB'],
        weather=captured['WEATHER'],conflicts=captured['CONFLICTS'])
    changes=diagnostic_changes(captured,fresh)
    module_root=runtime.ENGINE/'review_extension/radial_revision'
    hashes={name:hashlib.sha256((module_root/(name+'.py')).read_bytes()).hexdigest()
        for name in meta['module_sha256']}
    proof=dict(parent_snapshot_sha256=hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
        parent_module_sha256=meta['module_sha256'],current_module_sha256=hashes,
        diagnostic_changes=changes,all_other_captured_engine_fields_exact=True,
        original_inputs_unchanged=True,local_source_stage_only=True,product_writes=False)
    updated=dict(captured)
    for key in changes: updated[key]=fresh[key]
    meta={**meta,'config':cfg.model_dump(mode='json'),'module_sha256':hashes,'baseline_refreeze':proof}
    updated['METADATA']=np.array(json.dumps(meta))
    args.output.mkdir(parents=True,exist_ok=False)
    target=args.output/args.snapshot.name
    np.savez_compressed(target,**updated)
    with np.load(target,allow_pickle=False) as stored:
        for key,value in captured.items():
            if key!='METADATA' and key not in changes and not np.array_equal(stored[key],value,equal_nan=True):
                raise ValueError('non-diagnostic input changed during freeze: '+key)
    proof['output_snapshot_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
    (args.output/'report.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps(proof['diagnostic_changes']))


if __name__=='__main__':main()
