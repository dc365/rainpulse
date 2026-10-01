"""Receiver-bounded RAW fragment nomination; never a pollution/action mask.

Only observed gates are returned. Measured off states may connect a diagnostic
family, while unknown receiver intervals and acquisition gaps remain barriers.
A family must still pass independent source/polar/weather and action gates.
"""
import numpy as np
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
from rainpulse_algo.radar.qc_engine.volume_review.geometry import angular_widths, runs, wrap


def nominate(sweep, cfg):
    result = np.zeros(sweep.shape, bool)
    families = []
    record = {'diagnostic_only': True, 'method': 'receiver-bounded-raw-fragments-v1',
              'families': families, 'candidate_gates': 0}
    if cfg.noise_censor_snr_db is None:
        record['status'] = 'UNAVAILABLE_RECEIVER_FLOOR'
        return result, record
    z, za = sweep.moment('DBZH')
    sn, sa = sweep.moment('SNR')
    contour = min(cfg.objects.levels_dbz)
    echo = sweep.observed & za & (z >= contour)
    aw = angular_widths(sweep)
    floor = cfg.noise_censor_snr_db
    limit = cfg.radial_source_maximum_width_deg
    for row in np.flatnonzero(sweep.good):
        # Resolve both sides from measured receiver samples, never missing REF.
        distances = []
        for direction in (-1, 1):
            distance = np.full(sweep.shape[1], np.nan)
            pending = echo[row] & sa[row] & (sn[row] >= floor)
            current = row
            for step in range(1, sweep.shape[0]):
                other = (row + direction * step) % sweep.shape[0]
                edge = current if direction == 1 else other
                if sweep.gap_after[edge] or not sweep.good[other]:
                    break
                delta = abs(float(wrap(sweep.azimuth[other]-sweep.azimuth[row])))
                if delta > limit:
                    break
                # Unknown receivers cannot be traversed to find a quiet flank.
                pending &= sa[other]
                quiet = pending & (sn[other] < floor)
                distance[quiet] = delta
                pending[quiet] = False
                if not pending.any():
                    break
                current = other
            distances.append(distance)
        width = distances[0] + distances[1]
        bounded = echo[row] & np.isfinite(width) & (width <= limit)
        groups = []
        for start, end in runs(bounded):
            if groups:
                previous_end = groups[-1][-1][1]
                if ((start-previous_end)*sweep.dr <= cfg.radial_source_maximum_gap_m
                        and sa[row, previous_end:start].all()):
                    groups[-1].append((start, end))
                    continue
            groups.append([(start, end)])
        for group in groups:
            first, last = group[0][0], group[-1][1]-1
            gates = np.concatenate([np.arange(a, b) for a, b in group])
            span = float(sweep.ranges[last]-sweep.ranges[first]+sweep.dr)
            if (len(gates) < cfg.objects.minimum_object_gates
                    or span < cfg.radial_source_minimum_span_m):
                continue
            extent = float(np.max(width[gates]))
            mid = float(np.median(sweep.ranges[gates]))
            aspect = span/max(sweep.dr, mid*np.deg2rad(max(extent, aw[row])))
            if aspect < cfg.objects.minimum_source_aspect:
                continue
            if len(families) >= cfg.objects.maximum_objects:
                raise ResourceLimit('X raw fragment family count')
            result[row, gates] = True
            families.append({'ray': int(row), 'fragment_count': len(group),
                             'observed_gates': int(len(gates)), 'range_min_m': float(sweep.ranges[first]),
                             'range_max_m': float(sweep.ranges[last]), 'span_m': span,
                             'maximum_measured_width_deg': extent, 'aspect': aspect,
                             'observed_fraction': float(len(gates)/(last-first+1)),
                             'classification': 'UNQUALIFIED_GEOMETRY'})
    record.update(status='CANDIDATES' if families else 'NO_CANDIDATE',
                  candidate_gates=int(result.sum()))
    return result, record
