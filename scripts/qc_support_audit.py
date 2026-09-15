#!/usr/bin/env python3
"""Read-only frozen V7/V8 comparison. Categories describe evidence, not truth."""
import argparse
import json
from pathlib import Path
import numpy as np
import zarr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def audit(root):
    q = zarr.open_group(str(root / 'qc.zarr'), mode='r')['sweep_000']
    native = np.load(root / 'features/sweep_000-native.npz', allow_pickle=False)
    shape = q['QC_ACTION'].shape
    def get(key):
        return q[key][:] if key in q else np.full(shape, np.nan)
    action = get('QC_ACTION')
    raw, qc = get('DBZH_RAW'), get('DBZH_QC')
    v, c = get('V7_VERTICAL_SUPPORT_SCORE'), get('V7_CROSS_RADAR_SUPPORT_SCORE')
    keys = [k for k in q.array_keys() if 'CANDIDATE' in k and k.endswith('MASK') and q[k].shape == shape]
    candidate = np.zeros(shape, bool)
    for key in keys:
        candidate |= q[key][:] == 1
    e1, e2 = native['emitter1'], native['emitter2']
    if e1.shape != shape or e2.shape != shape:
        raise ValueError('native/QC geometry mismatch')
    # Exploratory operating points, not calibrated class probabilities.
    score = np.fmax(e1, e2)
    visible = np.isfinite(qc) & np.isfinite(raw) & (raw >= 10) & (action != 3)
    masks = {
        'quarantined_visible': visible & (get('RFI_QUARANTINE_MASK') == 1),
        'candidate_vertical_only_support': visible & candidate & (v >= .7) & ~(c >= .7),
        'candidate_missing_polarimetry': visible & candidate & ~np.isfinite(get('RHOHV_RAW')),
        'native_unavailable': visible & ~np.isfinite(score),
    }
    for threshold in (.25, .5, .75):
        masks[f'native_only_{threshold}'] = visible & ~candidate & (score >= threshold)
    report = {'scan_id': root.name, 'candidate_fields': keys,
              'visible_gates': int(visible.sum()), 'categories_overlap': True,
              'counts': {k: int(m.sum()) for k,m in masks.items()},
              'warning': 'Evidence association only; no causal attribution or truth accuracy. Missing cross support is not negative evidence.'}
    out = root / 'support-audit'
    out.mkdir(exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(report, indent=2))
    fig, axes = plt.subplots(2, 3, figsize=(16, 8), constrained_layout=True)
    panels = [(raw, 'Raw DBZH', 'turbo', -10, 70), (qc, 'V7 DBZH QC (may retain quarantined)', 'turbo', -10, 70),
              (score, 'V8 native score (uncalibrated)', 'viridis', 0, 1),
              (masks['candidate_vertical_only_support'], 'Candidate + vertical-only strong support', 'viridis', 0, 1),
              (masks['quarantined_visible'], 'Quarantined but visible', 'viridis', 0, 1),
              (np.where(np.isfinite(score), masks['native_only_0.5'].astype(float), np.nan), 'Native >=0.5; no candidate (white=unknown)', 'viridis', 0, 1)]
    for ax, (data,title,cmap,lo,hi) in zip(axes.flat, panels):
        im=ax.imshow(data, origin='lower', aspect='auto', cmap=cmap, vmin=lo, vmax=hi)
        ax.set_title(title); ax.set_xlabel('Original range gate'); ax.set_ylabel('Original ray index')
        fig.colorbar(im, ax=ax)
    fig.suptitle(root.name + ' / sweep_000 / evidence audit, not truth')
    fig.savefig(out / 'comparison.png', dpi=120)
    plt.close(fig)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('root'); args=p.parse_args()
    results=[]
    for root in sorted(Path(args.root).iterdir()):
        if not (root / 'features/sweep_000-native.npz').exists(): continue
        try: results.append(audit(root)); print(root.name, 'OK', flush=True)
        except Exception as e: results.append({'scan_id':root.name,'error':str(e)}); print(root.name,str(e),flush=True)
    (Path(args.root)/'support-audit.json').write_text(json.dumps(results,indent=2))

if __name__ == '__main__': main()
