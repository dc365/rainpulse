#!/usr/bin/env python3
"""Test receiver range correction on frozen source references; never write QC.

Target and adjacent 20 km blocks are excluded from paired DBZH/SNR calibration
across ALL rays. This correction is not an independent interference vote.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots', type=Path)
    p.add_argument('models', type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('refuse to overwrite an audit receipt')
    engine = ROOT / 'algorithms/rainpulse_algo/radar/qc_engine'
    for name, paths in (('fan_audit', []), ('fan_audit.engine', [str(engine)])):
        module = types.ModuleType(name)
        module.__path__ = paths
        sys.modules[name] = module
    package = 'fan_audit.engine.review_extension.radial_revision'
    relation = importlib.import_module(package + '.segments').range_relation
    fit = importlib.import_module(package + '.fan_joint').reference_model
    rows = []
    for path in sorted(args.models.glob('*.npz')):
        raw_path = args.snapshots / path.name
        with np.load(raw_path, allow_pickle=False) as data:
            a = {k: data[k] for k in data.files}
        with np.load(path, allow_pickle=False) as data:
            b = {k: data[k] for k in data.files}
        raw_meta = json.loads(str(a['METADATA']))
        model_meta = json.loads(str(b['METADATA']))
        if raw_meta['scan_id'] != model_meta['scan_id']:
            raise ValueError('scan identity mismatch')
        z, r = a['RAW'], a['RANGE']
        dr = float(np.median(np.diff(r)))
        blocked = (a['WEATHER'] == 1) | (a['CONFLICTS'] == 1) | (a['RV2_BARRED_MASK'] == 1)
        paired = a['AVAILABLE_DBZH'].astype(bool) & a['AVAILABLE_SNR'].astype(bool) & ~blocked
        paired &= np.isfinite(a['MOMENT_SNR']) & (a['MOMENT_SNR'] >= 10.)
        seed = b['RV2_SOURCE_LEDGER_SEED_ID']
        available = b['RV2_FAN_JOINT_MODEL_AVAILABLE_MASK'] == 1
        roi = ((r[None, :] >= 250000) & (a['AZIMUTH'][:, None] >= 285) &
               (a['AZIMUTH'][:, None] <= 340)) if raw_meta['radar_id'] == 'z9591' else (
               (r[None, :] >= 100000) & (a['AZIMUTH'][:, None] >= 160) & (a['AZIMUTH'][:, None] <= 280))
        targets = available & a['BEFORE'].astype(bool) & ~a['ADDED'].astype(bool) & roi
        cache, results = {}, []
        for ray, gate in zip(*np.nonzero(targets)):
            block = int(r[gate] // 20000)
            if block not in cache:
                train = abs((r // 20000).astype(int) - block) > 1
                cache[block] = relation(r, z, a['MOMENT_SNR'], paired, train)
            slope, receipt = cache[block]
            source = b['RV2_FAN_JOINT_MODEL_SOURCE_ID'][ray, gate]
            # Reproduce the exact protected-safe source section used in the fit.
            barriers = np.flatnonzero(blocked[ray])
            left = barriers[barriers < gate]
            right = barriers[barriers > gate]
            lo = int(left[-1] + 1) if len(left) else 0
            hi = int(right[0]) if len(right) else len(r)
            gates = np.flatnonzero((seed[ray] == source) & (np.arange(len(r)) >= lo) & (np.arange(len(r)) < hi))
            corrected = z[ray] - slope * r / 1000.
            model = fit(r, dr, corrected, gates, block)
            if model is None:
                raise ValueError('original reference unexpectedly unavailable')
            residual = float(corrected[gate] - 20 * np.log10(max(r[gate], 1.) / 50000.) - model['INTERCEPT_DB'])
            consistent = (model['REFERENCE_P90_DB'] <= 2.5 and
                          model['REFERENCE_SPLIT_DELTA_DB'] <= 1.5 and abs(residual) <= 2.5)
            results.append({'ray': int(ray), 'gate': int(gate), 'source_id': int(source),
                            'range_term_status': receipt['status'], 'range_term_db_per_km': slope,
                            'reference_sha256': receipt['sha256'], 'residual_db': residual,
                            'reference_p90_db': model['REFERENCE_P90_DB'],
                            'diagnostic_consistent': bool(consistent)})
        rows.append({'case': path.stem, 'scan_id': raw_meta['scan_id'],
                     'input_sha256': hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                     'model_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'targets': len(results), 'measured_range_term': sum(x['range_term_status'] == 'measured_consistent' for x in results),
                     'previous_consistent': int((targets & (b['RV2_FAN_JOINT_QUALIFIED_MASK'] == 1)).sum()),
                     'corrected_consistent_diagnostic': sum(x['diagnostic_consistent'] for x in results),
                     'details': results})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    modules = {name: hashlib.sha256((engine / 'review_extension/radial_revision' / name).read_bytes()).hexdigest()
               for name in ('segments.py', 'fan_joint.py')}
    args.output.write_text(json.dumps({'actions': 0, 'filled_gates': 0,
        'independent_interference_vote': False, 'module_sha256': modules,
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'cases': rows}, indent=2) + '\n')
    print(json.dumps([{k: v for k, v in row.items() if k != 'details'} for row in rows], indent=2))


if __name__ == '__main__':
    main()
