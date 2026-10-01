#!/usr/bin/env python3
"""Join immutable source snapshots and window replay; diagnose, never edit QC."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def audit(snapshot, replay):
    with np.load(snapshot, allow_pickle=False) as z:
        a = {k: z[k] for k in z.files}
    with np.load(replay, allow_pickle=False) as z:
        b = {k: z[k] for k in z.files}
    meta = json.loads(str(a['METADATA']))
    receipt = json.loads(str(b['METADATA']))
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if receipt['input_snapshot_sha256'] != digest or receipt['scan_id'] != meta['scan_id']:
        raise ValueError('replay does not match immutable input')
    r, az = a['RANGE'][None, :], a['AZIMUTH'][:, None]
    roi = ((r >= 250000) & (az >= 285) & (az <= 340) if meta['radar_id'] == 'z9591'
           else (r >= 100000) & (az >= 160) & (az <= 280))
    remaining = a['BEFORE'].astype(bool) & ~a['ADDED'].astype(bool) & roi
    raw = b['RV2_RAW_FAMILY_MASK'] == 1
    candidate = b['RV2_SOURCE_WINDOW_CANDIDATE_MASK'] == 1
    qualified = b['RV2_SOURCE_WINDOW_QUALIFIED_MASK'] == 1
    hold = b['RV2_SOURCE_WINDOW_HOLD_REASON']
    buckets = {
        'window_qualified_proposal': remaining & qualified,
        'window_parent_insufficient': remaining & candidate & (hold == 1),
        'window_parent_ambiguous': remaining & candidate & (hold == 2),
        'raw_family_without_window_parent': remaining & raw & ~candidate,
        'outside_narrow_family_and_window': remaining & ~raw & ~candidate,
    }
    counts = {k: int(v.sum()) for k, v in buckets.items()}
    if sum(counts.values()) != int(remaining.sum()):
        raise ValueError('failure partition is incomplete or overlaps')
    return {'case': snapshot.stem, 'scan_id': meta['scan_id'],
            'input_sha256': digest, 'replay_sha256': hashlib.sha256(replay.read_bytes()).hexdigest(),
            'remaining_sector': int(remaining.sum()), 'partition': counts,
            'scope': 'saved_source_stage_only_not_product_or_interference_truth',
            'independent_weather_truth': False, 'product_writes': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots', type=Path)
    p.add_argument('replays', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('do not overwrite evidence')
    rows = [audit(args.snapshots / x.name, x) for x in sorted(args.replays.glob('*.npz'))]
    if not rows:
        raise ValueError('no replay snapshots')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, allow_nan=False) + '\n')
    for row in rows:
        print(json.dumps({'case': row['case'], **row['partition']}))


if __name__ == '__main__':
    main()
