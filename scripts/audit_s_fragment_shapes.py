#!/usr/bin/env python3
"""Measure full RAW components touching residuals; diagnostic only, no QC actions."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import label, find_objects


def component_measurements(rows, cols, azimuth, ranges, spacing):
    """Physical PCA and native angular/range extents; none is a delete rule."""
    angles = np.deg2rad(azimuth[rows])
    distance = ranges[cols]
    bearing = np.arctan2(np.sin(angles).mean(), np.cos(angles).mean())
    offsets = np.angle(np.exp(1j * (angles - bearing)))
    xy = np.column_stack((distance * np.sin(angles), distance * np.cos(angles)))
    covariance = np.cov(xy, rowvar=False)
    values, vectors = np.linalg.eigh(covariance)
    axis = vectors[:, -1]
    radial = np.array([np.sin(bearing), np.cos(bearing)])
    angle = float(np.rad2deg(np.arccos(np.clip(abs(axis @ radial), 0., 1.))))
    width = float(np.rad2deg(offsets.max() - offsets.min()))
    span = float(distance.max() - distance.min() + spacing)
    windows = {}
    for scale in (5000, 10000, 20000):
        bins = np.unique(distance // scale).astype(int)
        windows[str(scale)] = dict(occupied=int(len(bins)),
            full_history=int(bins[-1] - bins[0] + 1),
            occupancy=float(len(bins) / (bins[-1] - bins[0] + 1)))
    return dict(gates=int(len(rows)), bearing_deg=float(np.rad2deg(bearing) % 360),
        range_min_m=float(distance.min()), range_max_m=float(distance.max()),
        radial_span_m=span, angular_extent_deg=width,
        pca_axis_to_radial_deg=angle,
        pca_elongation=float(np.sqrt(max(values[-1], 0.) / max(values[0], 1.))),
        occupied_range_windows=windows)


def measure_components(raw, available, azimuth, ranges, good, gaps, selected, level):
    shape = raw.shape
    if shape != (len(azimuth), len(ranges)) or any(np.shape(a) != shape for a in (available, selected)):
        raise ValueError('native array shape mismatch')
    if len(good) != shape[0] or len(gaps) != shape[0] or shape[1] < 2:
        raise ValueError('native geometry shape mismatch')
    spacing = float(np.median(np.diff(ranges)))
    if not np.isfinite(spacing) or spacing <= 0 or not np.allclose(np.diff(ranges), spacing):
        raise ValueError('uniform positive range spacing required')
    if not np.all(np.isfinite(azimuth)) or not np.all(np.isfinite(ranges)):
        raise ValueError('finite coordinates required')
    if raw.size > 2000000:
        raise ValueError('diagnostic gate budget exceeded')
    use = available.astype(bool) & np.isfinite(raw) & (raw >= level) & good.astype(bool)[:, None]
    # Break adjacency at explicit gaps and large angular jumps; no seam wrapping.
    steps = (np.diff(azimuth) + 180.) % 360. - 180.
    positive = steps[steps > 0]
    typical = float(np.median(positive)) if len(positive) else 0.
    breaks = gaps[:-1].astype(bool) | (steps <= 0) | (steps > 1.5 * typical)
    records = []; selected_ids = np.zeros(shape, dtype=np.uint32); offset = 0
    for segment_id, segment in enumerate(np.split(np.arange(shape[0]), np.flatnonzero(breaks) + 1)):
        labels, count = label(use[segment], structure=np.ones((3, 3)))
        for ident, box in enumerate(find_objects(labels), 1):
            if box is None:
                continue
            rr, cc = np.where(labels[box] == ident)
            rr = segment[rr + box[0].start]; cc = cc + box[1].start
            hits = int(selected[rr, cc].sum())
            if not hits or len(rr) < 3:
                continue
            record = component_measurements(rr, cc, azimuth, ranges, spacing)
            record.update(component_id=offset + ident, segment_id=segment_id,
                selected_remaining_gates=hits, contour_dbz=level)
            records.append(record); selected_ids[rr, cc] = offset + ident
        offset += count
    return sorted(records, key=lambda item: item['selected_remaining_gates'], reverse=True), selected_ids


def measure_fragment_groups(records, ids, ranges):
    """Fixed bearing cells, never recursive linking or an action authority.

    These groups contain only measured full RAW parents touching the selection;
    they are selection-biased diagnostics, not complete detected RAW objects.
    """
    reports = []
    spacing = float(np.median(np.diff(ranges)))
    for width in (2., 4., 8.):
        for origin in (0., width / 2):
            cells = {}
            for obj in records:
                key = (obj['segment_id'], int(((obj['bearing_deg'] - origin) % 360) // width))
                cells.setdefault(key, []).append(obj)
            for (segment, cell), objects in cells.items():
                if len(objects) < 3:
                    continue
                members = [obj['component_id'] for obj in objects]
                cols = np.flatnonzero(np.isin(ids, members).any(axis=0))
                first, last = int(cols[0]), int(cols[-1])
                reports.append(dict(segment_id=segment, cell_width_deg=width,
                    cell_origin_deg=origin, cell_index=cell, component_ids=members,
                    components=len(objects), selected_remaining_gates=sum(obj['selected_remaining_gates'] for obj in objects),
                    radial_span_m=float(ranges[last] - ranges[first] + spacing),
                    actual_range_support_m=float(len(cols) * spacing),
                    support_fraction=float(len(cols) / (last - first + 1)),
                    median_fragment_axis_to_radial_deg=float(np.median([obj['pca_axis_to_radial_deg'] for obj in objects]))))
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--range-min', type=float, default=300000.)
    parser.add_argument('--azimuth', nargs=2, type=float)
    parser.add_argument('--plot', action='store_true')
    args = parser.parse_args()
    from audit_s_source_footprint import select_roi
    args.output.mkdir(parents=True, exist_ok=False)
    reports = []; seen = set()
    for path in args.snapshots:
        with np.load(path, allow_pickle=False) as data:
            a = {key: data[key] for key in data.files}
        meta = json.loads(str(a['METADATA']))
        identity = (meta['scan_id'], meta['sweep'])
        if identity in seen or not meta.get('web_frame_identity'):
            raise ValueError('unique Web-paired native snapshot required')
        seen.add(identity)
        roi = select_roi(a['AZIMUTH'], a['RANGE'], range_min=args.range_min,
            azimuth_start=args.azimuth[0] if args.azimuth else None,
            azimuth_end=args.azimuth[1] if args.azimuth else None)
        selected = roi & a['BEFORE'].astype(bool) & ~a['ADDED'].astype(bool)
        contours = {}; groups = {}
        for level in (10., 20., 35., 55.):
            records, ids = measure_components(a['RAW'], a['AVAILABLE_DBZH'], a['AZIMUTH'],
                a['RANGE'], a['GEOMETRY_GOOD'], a['GAP_AFTER'], selected, level)
            contours[str(level)] = records
            groups[str(level)] = measure_fragment_groups(records, ids, a['RANGE'])
        report = dict(scan_id=identity[0], sweep=identity[1], diagnostic_only=True,
            selection_is_not_pollution_truth=True, source_stage_not_published_qc=True,
            input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            web_frame_identity=meta['web_frame_identity'], selected_remaining=int(selected.sum()),
            contours=contours, selection_biased_fragment_groups=groups)
        if args.plot:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            fig, axes = plt.subplots(1, 2, figsize=(13, 6), constrained_layout=True)
            theta = np.deg2rad(a['AZIMUTH'])[:, None]
            x = np.sin(theta) * a['RANGE'][None, :] / 1000
            y = np.cos(theta) * a['RANGE'][None, :] / 1000
            observed = a['AVAILABLE_DBZH'].astype(bool) & np.isfinite(a['RAW']) & (a['RAW'] >= 10)
            axes[0].scatter(x[observed], y[observed], c=a['RAW'][observed], s=.4, vmin=10, vmax=70)
            axes[1].scatter(x[selected], y[selected], c=a['RAW'][selected], s=2, vmin=10, vmax=70)
            for ax, title in zip(axes, ('Full original RAW', 'Selected source-stage residuals; no deletion')):
                ax.set(title=title, xlabel='East (km)', ylabel='North (km)', aspect='equal')
                ax.grid(alpha=.2)
            fig.savefig(args.output/(path.stem+'.png'), dpi=140); plt.close(fig)
        reports.append(report)
        print(path.stem, selected.sum(), {key: len(value) for key, value in contours.items()}, flush=True)
    (args.output/'report.json').write_text(json.dumps(reports, indent=2)+'\n')


if __name__ == '__main__':
    main()
