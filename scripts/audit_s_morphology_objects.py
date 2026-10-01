#!/usr/bin/env python3
"""Replay and plot whole RAW morphology on immutable Web-paired snapshots."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import types
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT/'algorithms/rainpulse_algo/radar/qc_engine'
for name, paths in (('morph_object_runtime', []), ('morph_object_runtime.engine', [str(ENGINE)])):
    module = types.ModuleType(name); module.__path__ = paths; sys.modules[name] = module
DETECTOR = importlib.import_module('morph_object_runtime.engine.review_extension.radial_revision.morphology_objects')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--azimuth', type=float, nargs=2)
    parser.add_argument('--range-min', type=float, default=0.)
    parser.add_argument('--plot', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from audit_s_source_footprint import select_roi
    records = []; seen = set()
    for path in args.snapshots:
        with np.load(path, allow_pickle=False) as data:
            a = {k: data[k] for k in data.files}
        meta = json.loads(str(a['METADATA']))
        identity = (meta['scan_id'], meta['sweep'])
        if identity in seen:
            raise ValueError('duplicate native scan/cut')
        seen.add(identity)
        if not meta.get('web_frame_identity'):
            raise ValueError('snapshot not paired to actual Web frames')
        fields = {'DBZH': a['RAW'], **{k[7:]: v for k, v in a.items() if k.startswith('MOMENT_')}}
        native = types.SimpleNamespace(shape=a['RAW'].shape, fields=fields,
            field_available={k[10:]: v for k, v in a.items() if k.startswith('AVAILABLE_')},
            ranges=a['RANGE'], azimuth=a['AZIMUTH'], geometry_good=a['GEOMETRY_GOOD'], gap_after=a['GAP_AFTER'])
        blocked = (a['WEATHER'] == 1) | (a['CONFLICTS'] == 1) | (a['RV2_BARRED_MASK'] == 1)
        start = time.monotonic()
        arrays, detail = DETECTOR.detect(native, blocked,
            beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
        DETECTOR.validate(arrays, native, blocked,
            beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
        elapsed = time.monotonic()-start
        roi = select_roi(a['AZIMUTH'], a['RANGE'], range_min=args.range_min,
            azimuth_start=args.azimuth[0] if args.azimuth else None,
            azimuth_end=args.azimuth[1] if args.azimuth else None)
        remaining = a['BEFORE'].astype(bool) & ~a['ADDED'].astype(bool)
        candidate = arrays[DETECTOR.PREFIX+'MASK'] == 1
        strong = arrays[DETECTOR.PREFIX+'STRONG_MASK'] == 1
        proof = {'scan_id': meta['scan_id'], 'sweep': meta['sweep'],
                 'input_snapshot_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                 'web_frame_identity': meta['web_frame_identity'], 'elapsed_seconds': elapsed,
                 'remaining_selected': int((remaining & roi).sum()),
                 'candidate_remaining_selected': int((remaining & roi & candidate).sum()),
                 'strong_remaining_selected': int((remaining & roi & strong).sum()), **detail}
        output = args.output/(path.stem+'.npz')
        if output.exists():
            raise ValueError('duplicate output stem')
        np.savez_compressed(output, METADATA=np.array(json.dumps(proof)), **arrays)
        proof['evidence_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
        if args.plot:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            az = np.deg2rad(a['AZIMUTH'][:, None]); r = a['RANGE'][None, :]/1000.
            x, y = r*np.sin(az), r*np.cos(az)
            fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
            raw = a['AVAILABLE_DBZH'].astype(bool) & np.isfinite(a['RAW']) & (a['RAW'] >= 0.)
            for ax, use, title in zip(axes, (raw, remaining, remaining),
                ('RAW observed DBZH', 'Existing source-stage remaining', 'Strong morphology evidence in red'), strict=True):
                ax.scatter(x[use], y[use], c=a['RAW'][use], cmap='turbo', vmin=0, vmax=70, s=.6, rasterized=True)
                if ax is axes[-1]:
                    hit = remaining & strong
                    ax.scatter(x[hit], y[hit], c='#d00000', s=2., rasterized=True)
                ax.set(xlim=(-470, 470), ylim=(-470, 470), title=title, xlabel='East (km)', ylabel='North (km)')
                ax.set_aspect('equal'); ax.grid(alpha=.2)
            fig.suptitle(path.stem+' | Evidence only: no new removal or Web publication')
            image = args.output/(path.stem+'.png'); fig.savefig(image, dpi=140); plt.close(fig)
            proof['image_sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
        records.append(proof)
        print(path.stem, proof['remaining_selected'], proof['candidate_remaining_selected'],
              proof['strong_remaining_selected'], round(elapsed, 2), flush=True)
    report = {'scope': 'whole_object_morphology_evidence_not_QC', 'action_gates': 0,
              'product_writes': False, 'independent_weather_truth': False,
              'selection': {'azimuth': args.azimuth, 'range_min': args.range_min},
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'detector_sha256': hashlib.sha256((ENGINE/'review_extension/radial_revision/morphology_objects.py').read_bytes()).hexdigest(),
              'cases': records}
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
