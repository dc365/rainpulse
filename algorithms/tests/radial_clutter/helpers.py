from types import SimpleNamespace

import numpy as np

from rainpulse_algo.radar.qc_engine.decision import Decision


def native(rays=9, gates=800, spacing=250):
    shape = (rays, gates)
    fields = {k: np.full(shape, v, 'float32') for k, v in
              [('DBZH', -20), ('SNR', 15), ('RHOHV', 0.995), ('ZDR', 0),
               ('PHIDP', 20), ('VR', 5), ('SW', 3)]}
    available = {k: np.ones(shape, bool) for k in fields}
    return SimpleNamespace(fields=fields, field_available=available, shape=shape,
                           ranges=(np.arange(gates) + .5) * spacing,
                           azimuth=np.arange(rays, dtype=float), elevation=np.full(rays, .5),
                           geometry_good=np.ones(rays, bool),
                           gap_after=np.r_[np.zeros(rays-1, bool), True], full_ppi=False,
                           gate_spacing_m=float(spacing), audit={'azimuth_spacing_deg': 1.0},
                           name='sweep_000', original_indices=np.arange(rays), restore=lambda a: a)


def radial(n, ray=4, lo=50, hi=750, dbz=20, polar=True):
    n.fields['DBZH'][ray, lo:hi] = dbz
    if polar:
        n.fields['RHOHV'][ray, lo:hi] = .3
        n.fields['PHIDP'][ray, lo:hi] = np.arange(hi-lo) % 2 * 90
    return n


def baseline(n):
    observed = n.field_available['DBZH'] & np.isfinite(n.fields['DBZH'])
    arrays = {'QC_ACTION': np.where(observed, 0, 3).astype('uint8'),
              'RFI_QUARANTINE_MASK': np.zeros(n.shape, 'uint8'),
              'RFI_RISK_STATE': np.zeros(n.shape, 'uint8'),
              'QPE_ELIGIBLE_MASK': observed.astype('uint8'),
              'DBZH_USABLE': np.where(observed, n.fields['DBZH'], np.nan).astype('float32')}
    for key in ('REFLECTIVITY', 'RHOHV', 'ZDR', 'PHIDP', 'VR', 'SW', 'SNR'):
        arrays[key+'_TRUST_MASK'] = observed.astype('uint8')
    return Decision(arrays, np.zeros(n.shape, 'uint32'), np.where(observed, .8, np.nan).astype('float32'))
