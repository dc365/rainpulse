"""Read-only native moment statistics; never assert Doppler verification.

The declared-period statistic is a sensitivity check, not a dealiaser or a
weather/nonweather classifier. Unknown waveform codes remain unknown.
"""
from __future__ import annotations

import numpy as np

from ..moment_support import binary_mask, moment_support
from .geometry import adapt


def _quantiles(values):
    return np.percentile(values, [10, 50, 90]).tolist() if values.size else None


def summarize(cut, cfg, *, declared_nyquist_mps=None, gate_selection=None):
    """Use only adjacent original gates with independently supported moments."""
    view = adapt(cut, cfg.model_copy(update={'doppler_verified': False}))
    shape = np.shape(cut.fields['DBZH'])
    good = view.restore(view.sweep.good)
    z, sn, vr, sw = [moment_support(cut.fields, k, shape, geometry=good)
                     for k in ('DBZH', 'SNR', 'VR', 'SW')]
    observed = binary_mask(cut.fields, 'OBSERVED_MASK', shape)
    no_echo = binary_mask(cut.fields, 'NO_ECHO_MASK', shape)
    valid = (observed & ~no_echo & view.restore(view.sweep.available['DBZH'])
             & z.valid & sn.valid & vr.valid & sw.valid
             & (sn.values >= cfg.radial_minimum_snr_db)
             & (sw.values >= 0))
    if gate_selection is not None:
        valid &= binary_mask({'selection': gate_selection}, 'selection', shape)
    ny = declared_nyquist_mps
    period_ok = (not isinstance(ny, (bool, np.bool_))
                 and isinstance(ny, (int, float, np.integer, np.floating))
                 and np.isfinite(ny) and ny > 0)
    rows = []
    for row in range(shape[0]):
        pair = valid[row, :-1] & valid[row, 1:]
        # Only adjacent native gates; missing gates break pairs. No derivatives
        # of angularly sorted data or fabricated velocities enter this report.
        delta = np.diff(vr.values[row].astype('float64'))[pair]
        circular = (delta + ny) % (2 * ny) - ny if period_ok else None
        rows.append({
            'native_ray': row,
            'azimuth_deg': float(cut.azimuth_deg[row]),
            'elevation_deg': float(cut.elevation_deg[row]),
            'geometry_good': bool(good[row]),
            'paired_moment_gates': int(valid[row].sum()),
            'consecutive_pairs': int(pair.sum()),
            'raw_absolute_delta_mps': _quantiles(abs(delta)),
            'declared_period_absolute_delta_mps':
                _quantiles(abs(circular)) if circular is not None else None,
            'declared_period_pair_coherence':
                float(abs(np.exp(1j * np.pi * circular / ny).mean()))
                if circular is not None and circular.size else None,
            'spectrum_width_mps': _quantiles(sw.values[row, valid[row]]),
        })
    return {
        'version': 'native-doppler-diagnostic-v1',
        'action_eligible': False,
        'doppler_verified': False,
        'period_status': 'DECLARED_ONLY_UNVERIFIED' if period_ok else 'UNAVAILABLE',
        'declared_nyquist_mps': float(ny) if period_ok else None,
        'minimum_snr_db': cfg.radial_minimum_snr_db,
        'geometry': view.report,
        'rays': rows,
    }
