#!/usr/bin/env python3
"""Replay complete Web-bound RAW snapshots; compare v1/v2, never publish products."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.config import RadialRevisionConfig
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.engine import evaluate
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.validation import validate_revision_fields


def replay(path, *, constellation=False):
    with np.load(path, allow_pickle=False) as data:
        a = {k: data[k] for k in data.files}
    meta = json.loads(str(a['METADATA']))
    if not meta.get('web_frame_identity'):
        raise ValueError('Web-bound original snapshot required')
    native = SimpleNamespace(shape=a['RAW'].shape,
        fields={'DBZH':a['RAW'].copy(), **{k[7:]:v for k,v in a.items() if k.startswith('MOMENT_')}},
        field_available={k[10:]:v for k,v in a.items() if k.startswith('AVAILABLE_')},
        ranges=a['RANGE'], azimuth=a['AZIMUTH'], geometry_good=a['GEOMETRY_GOOD'],
        gap_after=a['GAP_AFTER'], gate_spacing_m=float(np.median(np.diff(a['RANGE']))))
    cfg = RadialRevisionConfig.model_validate(meta['config'])
    legacy_cfg = cfg.model_copy(update={'mode':'experiment_quarantine',
        'fragment_line':cfg.fragment_line.model_copy(update={
            'whole_object_morphology_enabled':True, 'whole_object_physical_windows_enabled':False})})
    new_cfg = legacy_cfg.model_copy(update={'fragment_line':legacy_cfg.fragment_line.model_copy(
        update={'whole_object_physical_windows_enabled':True,
                'fragment_constellation_enabled':constellation})})
    kwargs = {'weather':a['WEATHER'], 'conflicts':a['CONFLICTS']}
    legacy, _ = evaluate(native, legacy_cfg, a['SEED'], a['RESIDUAL_DB'], **kwargs)
    new, _ = evaluate(native, new_cfg, a['SEED'], a['RESIDUAL_DB'], **kwargs)
    protected = (a['WEATHER']==1) | (a['CONFLICTS']==1)
    validate_revision_fields({**new,'DBZH_RAW':a['RAW']}, a['AVAILABLE_DBZH'].astype(bool), a['SEED'], protected)
    old_hit, new_hit = (v['RV2_ACTION_PROPOSAL_MASK']==1 for v in (legacy,new))
    assert not (old_hit & ~new_hit).any(), 'legacy dispositions lost'
    assert not (new_hit & protected).any(), 'protected observation nominated'
    for key in ('RV2_SOURCE_LEDGER_SEED_ID','RV2_SOURCE_LEDGER_KIND'):
        assert np.array_equal(new[key], legacy[key]), key
    assert np.array_equal(native.fields['DBZH'], a['RAW'], equal_nan=True)
    extra = new_hit & ~old_hit
    return dict(scan_id=meta['scan_id'], sweep=meta['sweep'], snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        legacy_proposals=int(old_hit.sum()), candidate_proposals=int(new_hit.sum()), additional_proposals=int(extra.sum()),
        protected_observations=int(protected.sum()), protected_overlap=0, raw_unchanged=True,
        source_identity_unchanged=True, writer_v2_replayed=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots', type=Path, nargs='+'); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--constellation', action='store_true', help='Also evaluate reviewed bounded fragment constellation')
    args = p.parse_args()
    if args.output.exists(): raise ValueError('output must be new')
    rows = []; seen = set()
    for path in args.snapshots:
        row = replay(path, constellation=args.constellation); identity = (row['scan_id'],row['sweep'])
        if identity in seen: raise ValueError('duplicate original scan/cut')
        seen.add(identity); rows.append(row); print(json.dumps(row), flush=True)
    report = dict(scope='complete_native_snapshot_engine_comparison', product_writes=False,
        independent_weather_truth=False, constellation_enabled=args.constellation, cuts=rows)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f: json.dump(report,f,indent=2); f.write('\n')


if __name__ == '__main__': main()
