"""Measured transverse RAW fragments in a frozen radial envelope; research only.

An original fragment anchors one bounded angular neighborhood. Newly associated
fragments never become anchors. No residual/ROI/source ledger enters detection.
"""
import numpy as np
from scipy.ndimage import label, find_objects
from ..arrays import native_geometry, moment, mask
from .geometry import ResourceLimit

PREFIX = 'RV2_CONSTELLATION_'


def detect(native, blocked, *, beam_width=None, maximum_objects=10000, segment_evidence=False):
    r, az, dr, good, gaps = native_geometry(native)
    if native.shape[0] < 3:
        raise ValueError('at least three native rays required')
    if np.prod(native.shape) > 2000000:
        raise ResourceLimit('constellation gate budget exceeded')
    if not isinstance(maximum_objects, int) or not 1 <= maximum_objects <= 10000:
        raise ValueError('bounded positive object budget required')
    if beam_width is not None and (not np.isfinite(beam_width) or beam_width <= 0):
        raise ValueError('positive finite beam width required')
    z, observed = moment(native, 'DBZH'); snr, sa = moment(native, 'SNR'); rho, ra = moment(native, 'RHOHV')
    barred = mask(blocked, native.shape, 'constellation barriers') | ~good[:, None]
    weather = observed & ra & sa & (rho >= .95) & (snr >= 10)
    out = {PREFIX+'MASK': np.zeros(native.shape, 'uint8'),
           PREFIX+'STRONG_MASK': np.zeros(native.shape, 'uint8'),
           PREFIX+'ID': np.zeros(native.shape, 'uint32'),
           PREFIX+'WEATHER_VETO_MASK': weather.astype('uint8')}
    steps = (np.diff(az) + 180) % 360 - 180
    positive = steps[steps > 0]
    if not len(positive):
        raise ValueError('increasing native azimuth required')
    spacing = float(np.median(positive)); beam = max(spacing, beam_width or spacing)
    if not np.isfinite(beam) or beam <= 0:
        raise ValueError('positive finite beam width required')
    breaks = gaps[:-1] | (steps <= 0) | (steps > 1.5 * spacing)
    records = []; work = 0
    def charge(amount):
        nonlocal work
        work += int(amount)
        if work > 50000000:
            raise ResourceLimit('constellation work budget exceeded; no partial output')
    for segment in np.split(np.arange(len(az)), np.flatnonzero(breaks)+1):
        angle = np.rad2deg(np.unwrap(np.deg2rad(az[segment])))
        for level in (10., 20., 35.):
            charge(len(segment)*len(r))
            use = observed[segment] & good[segment, None] & (z[segment] >= level)
            labels, count = label(use, np.ones((3, 3)))
            if count > 20000:
                raise ResourceLimit('constellation component budget exceeded')
            fragments = []
            for ident, box in enumerate(find_objects(labels), 1):
                if box is None:
                    continue
                rr, cc = np.where(labels[box] == ident)
                rr += box[0].start; cc += box[1].start
                charge(len(rr))
                if len(rr) < 3 or np.ptp(r[cc])+dr > 20000 or np.ptp(angle[rr])+spacing > 4*beam:
                    continue
                bearing = float(angle[rr].mean()); theta = np.deg2rad(angle[rr])
                xy = np.column_stack((r[cc]*np.sin(theta), r[cc]*np.cos(theta)))
                values, vectors = np.linalg.eigh(np.cov(xy, rowvar=False))
                radial = np.array([np.sin(np.deg2rad(bearing)), np.cos(np.deg2rad(bearing))])
                alignment = abs(vectors[:, -1] @ radial)
                if alignment > .5 or values[-1] < 4*max(values[0], 1.):
                    continue
                fragments.append(dict(ident=ident, rows=rr, cols=cc, bearing=bearing,
                    left=int(rr.min()), right=int(rr.max())))
            if not fragments:
                continue
            bearings = np.array([f['bearing'] for f in fragments]); seen = set()
            # Every neighborhood is anchored in original RAW, never in a new association.
            for anchor in fragments:
                charge(len(fragments))
                members = np.flatnonzero(abs(bearings-anchor['bearing']) <= beam)
                key = tuple(fragments[i]['ident'] for i in members)
                if len(members) < 4 or key in seen:
                    continue
                seen.add(key); group = [fragments[i] for i in members]
                rr = np.concatenate([f['rows'] for f in group]); cc = np.concatenate([f['cols'] for f in group])
                cols = np.unique(cc); span = float(r[cols[-1]]-r[cols[0]]+dr)
                windows = [len(np.unique(r[cols]//scale)) for scale in (5000, 10000, 20000)]
                if span < 60000 or len(cols)*dr < 5000 or min(windows) < 4:
                    continue
                if len(records) >= maximum_objects:
                    raise ResourceLimit('constellation object budget exceeded; no partial output')
                holds = []; accept = []; fractions = []; side_observations = []; member_history = []
                if weather[segment[rr], cc].any(): holds.append('measured_weather_member')
                if barred[segment[rr], cc].any(): holds.append('protected_original_member')
                for fragment in group:
                    fr, fc = fragment['rows'], fragment['cols']
                    accepted = ~barred[segment[fr], fc] & ~weather[segment[fr], fc]
                    sides = []
                    for side in (fragment['left']-1, fragment['right']+1):
                        if side < 0 or side >= len(segment):
                            accepted[:] = False; break
                        row = segment[side]
                        quiet = ~observed[row, fc] & sa[row, fc] & (snr[row, fc] <= 3)
                        contrast = observed[row, fc] & (z[row, fc] <= z[segment[fr], fc]-6)
                        sides.append(dict(observed_dbzh_fraction=float(observed[row, fc].mean()),
                            measured_quiet_snr_fraction=float(quiet.mean()),
                            unknown_fraction=float((~observed[row, fc] & ~quiet).mean())))
                        accepted &= ~barred[row, fc] & (quiet | contrast)
                    fractions.append(float(accepted.mean())); accept.append(accepted); side_observations.append(sides)
                    member_history.append(dict(component_id=fragment['ident'],
                        range_min_m=float(r[fc].min()), range_max_m=float(r[fc].max()+dr),
                        bearing_deg=fragment['bearing'] % 360,
                        angular_width_deg=float(np.ptp(angle[fr])+spacing),
                        observed_weather_gates=int(weather[segment[fr], fc].sum()),
                        protected_gates=int(barred[segment[fr], fc].sum()),
                        bilateral_fraction=float(accepted.mean())))
                # Keep every original member in qualification history; no restarting
                # the object after discarding a failed/unknown/weather fragment.
                if min(fractions) < .8: holds.append('incomplete_measured_bilateral_boundaries')
                object_id = len(records)+1
                out[PREFIX+'MASK'][segment[rr], cc] = 1
                out[PREFIX+'ID'][segment[rr], cc] = object_id
                # Full-parent width history is retained before optional segmentation.
                # A fixed-km ribbon narrows in angle as range increases; splitting
                # away its near/weather part must not evade this counterexample.
                distance = np.array([(m['range_min_m']+m['range_max_m'])/2 for m in member_history])
                widths = np.array([m['angular_width_deg'] for m in member_history])
                narrowing = False; slope = correlation = None
                if distance.min() > 0 and distance.max()/distance.min() >= 1.7 and np.ptp(widths) > beam:
                    slope = float(np.polyfit(np.log(distance), np.log(widths), 1)[0])
                    correlation = float(np.corrcoef(np.log(distance), np.log(widths))[0, 1])
                    narrowing = slope <= -.4 and correlation <= -.7
                if narrowing:
                    holds.append('full_original_parent_narrowing_weather_counterexample')
                if not holds:
                    for f, accepted in zip(group, accept, strict=True):
                        out[PREFIX+'STRONG_MASK'][segment[f['rows'][accepted]], f['cols'][accepted]] = 1
                segments = []
                ordered = np.argsort([m['range_min_m'] for m in member_history])
                qualifying = np.array([m['bilateral_fraction'] >= .8 and
                    not m['observed_weather_gates'] and not m['protected_gates'] for m in member_history])
                # Any original failed member blocks its observed radial interval,
                # including another member that overlaps the same interval.
                for i in np.flatnonzero(~qualifying):
                    lo, hi = member_history[i]['range_min_m'], member_history[i]['range_max_m']
                    for j, m in enumerate(member_history):
                        if m['range_min_m'] < hi and m['range_max_m'] > lo:
                            qualifying[j] = False
                edges = np.diff(np.r_[False, qualifying[ordered], False].astype('int8'))
                for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
                    indices = ordered[start:end]
                    if len(indices) < 4:
                        continue
                    sc = np.unique(np.concatenate([group[i]['cols'] for i in indices]))
                    ss = float(r[sc[-1]]-r[sc[0]]+dr)
                    sw = [len(np.unique(r[sc]//scale)) for scale in (5000,10000,20000)]
                    eligible = not narrowing and ss >= 60000 and len(sc)*dr >= 5000 and min(sw) >= 4
                    segments.append(dict(original_components=[group[i]['ident'] for i in indices],
                        radial_span_m=ss, actual_range_support_m=float(len(sc)*dr),
                        range_window_counts=sw, independently_qualified=bool(eligible)))
                    if segment_evidence and eligible:
                        for i in indices:
                            f = group[i]; selected = accept[i]
                            out[PREFIX+'STRONG_MASK'][segment[f['rows'][selected]],f['cols'][selected]] = 1
                records.append(dict(id=object_id, contour_dbz=level, original_components=list(key),
                    anchor_bearing_deg=anchor['bearing'] % 360, neighborhood_half_width_deg=beam,
                    radial_span_m=span, actual_range_support_m=float(len(cols)*dr),
                    range_window_counts=windows, bilateral_fractions=fractions,
                    side_observations=side_observations,
                    member_history=member_history, measured_segments=segments,
                    full_parent_width_slope=slope, full_parent_width_correlation=correlation,
                    full_parent_narrowing_weather_hold=bool(narrowing),
                    strong=not holds, hold_reasons=holds))
    return out, dict(objects=records, source_claim=False, recursive_growth=False,
        action_gates=0, research_only=True, work=work)


def validate(arrays, native, blocked, **options):
    expected, _ = detect(native, blocked, **options)
    for key, value in expected.items():
        if key not in arrays or not np.array_equal(arrays[key], value):
            raise ValueError('constellation proof mismatch: '+key)
