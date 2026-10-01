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
    parser.add_argument('--engine-quarantine', action='store_true',
                        help='Validate opt-in experiment proposals through the actual engine; no product writes')
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
            ranges=a['RANGE'], azimuth=a['AZIMUTH'], geometry_good=a['GEOMETRY_GOOD'], gap_after=a['GAP_AFTER'],
            gate_spacing_m=float(np.median(np.diff(a['RANGE']))))
        blocked = (a['WEATHER'] == 1) | (a['CONFLICTS'] == 1) | (a['RV2_BARRED_MASK'] == 1)
        start = time.monotonic()
        arrays, detail = DETECTOR.detect(native, blocked,
            beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
        DETECTOR.validate(arrays, native, blocked,
            beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
        integration = {}
        extra_proposals = np.zeros(native.shape, bool)
        if args.engine_quarantine:
            engine = importlib.import_module('morph_object_runtime.engine.review_extension.radial_revision.engine')
            config = importlib.import_module('morph_object_runtime.engine.review_extension.radial_revision.config')
            validator = importlib.import_module('morph_object_runtime.engine.review_extension.radial_revision.validation')
            cfg = config.RadialRevisionConfig.model_validate(meta['config'])
            cfg = cfg.model_copy(update={'mode':'experiment_quarantine', 'fragment_line':
                cfg.fragment_line.model_copy(update={'whole_object_morphology_enabled':False})})
            barrier = (a['WEATHER'] == 1) | (a['CONFLICTS'] == 1)
            baseline, _ = engine.evaluate(native, cfg, a['SEED'], a['RESIDUAL_DB'],
                weather=a['WEATHER'], conflicts=a['CONFLICTS'])
            # Check the captured original baseline, not only another fresh run.
            changed = [key for key,value in baseline.items() if key in a and
                       not np.array_equal(value, a[key], equal_nan=True)]
            if changed:
                raise ValueError('frozen source-stage baseline differs: '+','.join(changed))
            cfg = cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(
                update={'whole_object_morphology_enabled':True})})
            integrated, engine_report = engine.evaluate(native, cfg, a['SEED'], a['RESIDUAL_DB'],
                weather=a['WEATHER'], conflicts=a['CONFLICTS'])
            validator.validate_revision_fields({**integrated,'DBZH_RAW':a['RAW']},
                a['AVAILABLE_DBZH'].astype(bool), a['SEED'], barrier)
            proposals = integrated['RV2_ACTION_PROPOSAL_MASK'] == 1
            prior_proposals = baseline['RV2_ACTION_PROPOSAL_MASK'] == 1
            extra_proposals = proposals & ~prior_proposals
            if np.any(prior_proposals & ~proposals):
                raise ValueError('new geometry withdrew an existing proposal')
            for key in ('RV2_SOURCE_LEDGER_SEED_ID','RV2_SOURCE_LEDGER_KIND'):
                if key in baseline and not np.array_equal(baseline[key], integrated[key]):
                    raise ValueError('new geometry became an original source')
            arrays = {key:value for key,value in integrated.items() if key.startswith(DETECTOR.PREFIX)}
            integration = {'experimental_engine_replay':True, 'serialized_validation_passed':True,
                           'original_source_identity_unchanged':True,
                           'extra_proposals':int((proposals & ~prior_proposals).sum()),
                           'experimental_config':cfg.model_dump(mode='json'),
                           'engine_report':engine_report,
                           'engine_module_sha256':hashlib.sha256((ENGINE/'review_extension/radial_revision/engine.py').read_bytes()).hexdigest(),
                           'validator_module_sha256':hashlib.sha256((ENGINE/'review_extension/radial_revision/validation.py').read_bytes()).hexdigest()}
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
                 'strong_remaining_selected': int((remaining & roi & strong).sum()), **detail, **integration}
        proof['extra_visible_proposals_selected'] = int((remaining & roi & extra_proposals).sum())
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
            after = remaining & ~extra_proposals if args.engine_quarantine else remaining
            third_title = ('Experiment after morphology quarantine' if args.engine_quarantine
                           else 'Strong morphology evidence in red')
            for ax, use, title in zip(axes, (raw, remaining, after),
                ('RAW observed DBZH', 'Existing source-stage remaining', third_title), strict=True):
                ax.scatter(x[use], y[use], c=a['RAW'][use], cmap='turbo', vmin=0, vmax=70, s=.6, rasterized=True)
                if ax is axes[-1] and not args.engine_quarantine:
                    hit = remaining & strong
                    ax.scatter(x[hit], y[hit], c='#d00000', s=2., rasterized=True)
                ax.set(xlim=(-470, 470), ylim=(-470, 470), title=title, xlabel='East (km)', ylabel='North (km)')
                ax.set_aspect('equal'); ax.grid(alpha=.2)
            caption = ('Validated engine experiment: no Web publication' if args.engine_quarantine
                       else 'Evidence only: no new removal or Web publication')
            fig.suptitle(path.stem+' | '+caption)
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
