#!/usr/bin/env python3
"""Compare two saved native normal-QC snapshots; never proposes QC actions."""
import argparse
import json
from pathlib import Path

import numpy as np


def load(path):
    with np.load(path, allow_pickle=False) as archive:
        return {key: archive[key] for key in archive.files}


def compare(before, after):
    a, b = [json.loads(str(x['METADATA'])) for x in (before, after)]
    for key in ('scan_id', 'radar_id', 'sweep', 'normalized_uri'):
        if a[key] != b[key]:
            raise ValueError('Different source identity: ' + key)
    for key in ('RAW', 'AZIMUTH', 'RANGE', 'RAY_TIME', 'ELEVATION', 'NATIVE_TO_ORIGINAL'):
        if not np.array_equal(before[key], after[key], equal_nan=True):
            raise ValueError('Different original data/geometry: ' + key)
    if a['qc_uri'] == b['qc_uri']:
        raise ValueError('Same QC generation supplied twice')
    removed = before['BEFORE'] & ~after['BEFORE']
    restored = after['BEFORE'] & ~before['BEFORE']
    return dict(scan_id=a['scan_id'], old_qc_uri=a['qc_uri'], new_qc_uri=b['qc_uri'],
        scope='stored_normal_QC_comparison_not_independent_weather_truth',
        raw_and_geometry_identical=True, newly_withheld_visible=int(removed.sum()),
        restored_visible=int(restored.sum()),
        old_weather_barrier_overlap=int((removed & (before['WEATHER'] == 1)).sum()),
        old_conflict_barrier_overlap=int((removed & (before['CONFLICTS'] == 1)).sum()))


def render(before_path, after_path, output, zoom):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    before, after = load(before_path), load(after_path)
    report = compare(before, after)
    meta = json.loads(str(after['METADATA']))
    angle = np.deg2rad(after['AZIMUTH'])[:, None]
    radius = after['RANGE'][None, :] / 1000
    x, y = radius * np.sin(angle), radius * np.cos(angle)
    maximum = float(radius.max()) + 5
    colors = ['#389be3', '#5edee4', '#70f22e', '#00d200', '#078600', '#ffff00',
              '#edc500', '#ff9000', '#ff1000', '#d00000', '#bb0000', '#ef00e8',
              '#9000b4', '#ac92e9']
    cmap = LinearSegmentedColormap.from_list('radar', colors, N=256)
    fig, axes = plt.subplots(2, 3, figsize=(14, 9), layout='constrained')
    for line, limits in zip(axes, [(-maximum, maximum, -maximum, maximum), zoom]):
        region = (x >= limits[0]) & (x <= limits[1]) & (y >= limits[2]) & (y <= limits[3])
        panels = [(after['RAW'], np.isfinite(after['RAW']) & (after['RAW'] >= 5), 'RAW'),
                  (before['QC'], before['BEFORE'], 'Previous stored normal QC'),
                  (after['QC'], after['BEFORE'], 'New stored normal QC')]
        for ax, (values, visible, title) in zip(line, panels):
            use = visible & region
            plot = ax.scatter(x[use], y[use], c=values[use], s=0.9,
                cmap=cmap, vmin=5, vmax=70, rasterized=True)
            ax.set(xlim=limits[:2], ylim=limits[2:], aspect='equal', title=title,
                   xlabel='East (km)', ylabel='North (km)')
            ax.grid(alpha=0.2)
    fig.colorbar(plot, ax=axes, label='Reflectivity (dBZ)', shrink=0.7)
    fig.suptitle(f"{meta['radar_id'].upper()} {meta['local_date']} {meta['local_time']} CST"
        f" | sweep {meta['sweep']}\nStored normal QC; additional visible gates withheld: "
        f"{report['newly_withheld_visible']}; restored: {report['restored_visible']}")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--zoom', type=float, nargs=4, default=[-80, 80, -100, 30],
                        metavar=('WEST', 'EAST', 'SOUTH', 'NORTH'))
    args = parser.parse_args()
    if not np.isfinite(args.zoom).all() or args.zoom[0] >= args.zoom[1] or args.zoom[2] >= args.zoom[3]:
        parser.error('Zoom bounds must be finite and increasing')
    print(json.dumps(render(args.before, args.after, args.output, args.zoom)))
