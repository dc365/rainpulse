#!/usr/bin/env python3
"""Plot immutable source-stage snapshots and wide RAW nominees; no product edits."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshots', type=Path)
    p.add_argument('replays', type=Path)
    p.add_argument('--case', action='append', required=True, help='Saved NPZ stem')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    cmap = LinearSegmentedColormap.from_list('radar', ['#18a8dc', '#04d000', '#078800', '#ffc000', '#ee746d', '#d000b4', '#b597ee'])
    receipts = []
    for case in args.case:
        source, replay = args.snapshots/(case+'.npz'), args.replays/(case+'.npz')
        with np.load(source, allow_pickle=False) as z: a = {k:z[k] for k in z.files}
        with np.load(replay, allow_pickle=False) as z: b = {k:z[k] for k in z.files}
        m, receipt = json.loads(str(a['METADATA'])), json.loads(str(b['METADATA']))
        if receipt['input_snapshot_sha256'] != hashlib.sha256(source.read_bytes()).hexdigest() or receipt['scan_id'] != m['scan_id']:
            raise ValueError('mismatched immutable input')
        az = np.deg2rad(a['AZIMUTH'][:,None]); r = a['RANGE'][None,:]/1000.
        x, y = r*np.sin(az), r*np.cos(az)
        remaining = a['BEFORE'].astype(bool) & ~a['ADDED'].astype(bool)
        raw = a['AVAILABLE_DBZH'].astype(bool) & (a['RAW'] >= 0)
        fig, axes = plt.subplots(2, 3, figsize=(14, 9), constrained_layout=True)
        titles = ('RAW measured echoes', 'Prior source-stage remaining', 'Remaining + broad RAW nominations')
        extent = (-470,470,-470,470)
        zoom = (-450,-80,80,450) if m['radar_id'] == 'z9591' else (-450,200,-470,0)
        for row, limits in enumerate((extent, zoom)):
            for col, ax in enumerate(axes[row]):
                use = raw if col == 0 else remaining
                if col == 2:
                    ax.scatter(x[use], y[use], s=2., c='#c6c6c6', rasterized=True)
                    nominated = use & (b['RV2_RAW_FAN_MASK'] == 1)
                    ax.scatter(x[nominated], y[nominated], s=3., c='#d62728', rasterized=True)
                else:
                    ax.scatter(x[use], y[use], s=1.5, c=a['RAW'][use], cmap=cmap, vmin=5, vmax=70, rasterized=True)
                ax.set(xlim=limits[:2], ylim=limits[2:], title=titles[col], xlabel='East (km)', ylabel='North (km)')
                ax.set_aspect('equal'); ax.grid(alpha=.2)
        fig.suptitle(case+' | Red = diagnostic candidate only; no new removal or Web publication')
        image = args.output/(case+'.png'); fig.savefig(image, dpi=145); plt.close(fig)
        receipts.append({'case':case, 'scan_id':m['scan_id'], 'input_sha256':receipt['input_snapshot_sha256'],
                         'replay_sha256':hashlib.sha256(replay.read_bytes()).hexdigest(),
                         'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
                         'scope':'diagnostic_nomination_only', 'product_writes':0})
    (args.output/'receipt.json').write_text(json.dumps(receipts, indent=2)+'\n')


if __name__ == '__main__': main()
