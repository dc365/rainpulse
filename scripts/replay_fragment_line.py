#!/usr/bin/env python3
"""Compare step3 vs fragment-line on frozen polar data; no product publication."""
import argparse
import json
from pathlib import Path
import sys
import time
import types
import numpy as np
import yaml

# Reuse the frozen numeric reader with the worker's existing Blosc dependency.
try:
    import blosc2
except ModuleNotFoundError:
    import numcodecs
    mod = types.ModuleType('blosc2')
    mod.decompress = numcodecs.blosc.decompress
    sys.modules['blosc2'] = mod
from radial_revision_case_io import case_groups, native
from replay_radial_revision_20260918 import modules


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--all-sweeps', action='store_true')
    p.add_argument('--plot', action='store_true')
    p.add_argument('--direct-morphology', action='store_true')
    p.add_argument('--isolated', action='store_true')
    p.add_argument('--sparse-isolated', action='store_true')
    p.add_argument('--group-morphology', action='store_true')
    p.add_argument('--window-tracks', action='store_true')
    a = p.parse_args()
    if a.window_tracks:
        a.group_morphology = True
    if a.output.resolve().is_relative_to(a.case_root.resolve()):
        raise ValueError('output must be outside frozen inputs')
    a.output.mkdir(parents=True, exist_ok=False)
    load = modules(Path(__file__).resolve().parents[1])
    cfg = yaml.safe_load((a.case_root/'configs/fujian-qc-review-20260917-experiment.yaml').read_text())
    sc = dict(cfg['generalization']['broad_source']['source_review'])
    sc['radial_revision'] = dict(step=3, mode='experiment_quarantine', allow_segmented_quarantine=True)
    config = load('config').SourceReviewConfig
    source = load('source').source_additions
    rows = []
    for case in map(json.loads, (a.case_root/'metadata/cases.jsonl').read_text().splitlines()):
        ng, qg = case_groups(a.case_root, case)
        sweeps = sorted(k for k in qg.keys() if k.startswith('sweep_') and k[6:].isdigit())
        for sweep in sweeps if a.all_sweeps else sweeps[:1]:
            n = native(ng[sweep], ng.attrs, cfg); q = qg[sweep]
            get = lambda k: q[k][:][n.original_indices]
            ref, residual = get('SRC_REVIEW_TARGET_MATCH_MASK'), get('SRC_REVIEW_RESIDUAL_DB')
            kw = dict(weather=get('SRC_REVIEW_WEATHER_PROTECTED_MASK'), conflicts=get('SRC_REVIEW_CONFLICT_MASK'),
                      reference_available=get('SRC_REVIEW_SOURCE_AVAILABLE_MASK'))
            started = time.monotonic()
            baseline, old, _ = source(n, config.model_validate(sc), ref, residual, **kw)
            line_cfg = ({'version': 'fragment-line-v2', 'morphology_quarantine_enabled': True}
                        if a.direct_morphology else {})
            if a.isolated or a.sparse_isolated or a.group_morphology:
                line_cfg = {'version': 'fragment-line-v3', 'morphology_quarantine_enabled': True,
                            'isolated_quarantine_enabled': True, 'sparse_isolated_enabled': a.sparse_isolated or a.group_morphology,
                            'group_morphology_enabled': a.group_morphology,
                            'window_tracks_enabled': a.window_tracks}
            child = dict(sc, radial_revision=dict(sc['radial_revision'], fragment_line=line_cfg))
            current, new, detail = source(n, config.model_validate(child), ref, residual, **kw)
            new['SRC_REVIEW_REFERENCE_FOLD_ID'] = get('SRC_REVIEW_REFERENCE_FOLD_ID')
            load('source_validation').validate_source_fields(new, n.field_available['DBZH'])
            before = (get('QPE_ELIGIBLE_MASK') == 1) & ~baseline
            added = before & current
            raw = n.fields['DBZH']
            row = dict(site=case['site'], sweep=sweep, elapsed_seconds=round(time.monotonic()-started, 2),
                       baseline_eligible=int(before.sum()), new_eligible_loss=int(added.sum()),
                       protected_loss=int((added & (kw['weather'] == 1)).sum()),
                       candidate_gates=int(new['RV2_LINE_MASK'].sum()),
                       source=detail['radial_revision'], examples=[])
            for angle in ([346.35, 265.72] if case['site'] == 'SITE_A' else [355.92, 226.87, 243.40]):
                i = int(np.argmin(abs((n.azimuth-angle+180)%360-180)))
                target = before[i] & (raw[i] >= 10) & (n.ranges >= 100000)
                row['examples'].append(dict(azimuth=float(n.azimuth[i]), before=int(target.sum()),
                                           added=int((target & added[i]).sum())))
            stem = f"{case['site']}_{sweep}"
            np.savez_compressed(a.output/(stem+'.npz'), RAW=raw, BEFORE=before, ADDED=added,
                                AZIMUTH=n.azimuth, RANGE=n.ranges, **{k:v for k,v in new.items() if k.startswith('RV2_')})
            rows.append(row)
            print(json.dumps({k:v for k,v in row.items() if k != 'source'}), flush=True)
    (a.output/'report.json').write_text(json.dumps(dict(scope='source_stage_action_projection',
        full_worker=False, independent_weather_truth=False, runs=rows), indent=2)+'\n')
    if a.plot:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 3, figsize=(16, 10), constrained_layout=True)
        for y, site in enumerate(('SITE_A', 'SITE_B')):
            data = np.load(a.output/f'{site}_sweep_000.npz')
            az = np.deg2rad(data['AZIMUTH'])[:, None]
            r = data['RANGE'][None, :]/1000.
            x, yy = r*np.sin(az), r*np.cos(az)
            raw, before, added = data['RAW'], data['BEFORE'], data['ADDED']
            for j, valid in enumerate((np.isfinite(raw), before, before & ~added)):
                use = valid & np.isfinite(raw) & (raw >= 0)
                im = axes[y, j].scatter(x[use], yy[use], c=raw[use], s=.35, cmap='turbo', vmin=0, vmax=65, rasterized=True)
                axes[y, j].set(xlim=(-470, 470), ylim=(-470, 470), aspect='equal',
                               title=f"{site}: {('Raw', 'Step3 baseline', 'Fragment-line projection')[j]}",
                               xlabel='East (km)', ylabel='North (km)')
                axes[y, j].grid(alpha=.2)
        fig.colorbar(im, ax=axes, label='Reflectivity (dBZ)', shrink=.65)
        fig.suptitle('08:42 lowest sweep | source-stage projection, not full worker output')
        fig.savefig(a.output/'comparison.png', dpi=150)
        plt.close(fig)


if __name__ == '__main__':
    main()
