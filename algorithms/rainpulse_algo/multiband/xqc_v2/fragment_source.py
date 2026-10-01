"""Held-out source qualification within separate, frozen RAW fragment families.

The radial-source stage integrates complete results under its existing gates.
This entry point alone is diagnostic. Geometry only selects reference domains;
paired receiver power and REF response must qualify every observed target gate.
"""
import numpy as np
from .fragment_geometry import nominate
from .source_blocks import detect as detect_blocks
from .source_summary import SourceStatistics


def detect(sweep, cfg, *, protected, prepared=None):
    if not isinstance(protected, np.ndarray) or protected.shape != sweep.shape or protected.dtype != bool:
        raise ValueError('fragment source protection must be a boolean native-sweep matrix')
    nomination, geometry = nominate(sweep, cfg)
    result = np.zeros(sweep.shape, bool)
    records = []
    stats = SourceStatistics.build(sweep, cfg) if prepared is None else prepared.use(sweep, cfg)
    for identity, family in enumerate(geometry['families']):
        # One family per evaluation: distant groups cannot share their training
        # support across an unknown or over-limit receiver interval.
        domain = np.zeros(sweep.shape, bool)
        row = family['ray']
        domain[row] = ((sweep.ranges >= family['range_min_m'])
                       & (sweep.ranges <= family['range_max_m']))
        fitted = np.zeros(sweep.shape, bool)
        modes = []
        for quantile in (90, 50):
            mask, record = detect_blocks(
                sweep, cfg, protected=protected, fan=True,
                family_width_deg=cfg.radial_source_maximum_width_deg,
                prepared=stats, response_quantile=quantile, domain=domain,
            )
            fitted |= mask
            modes.append(record)
        fitted &= nomination & domain & sweep.observed & ~protected
        result |= fitted
        records.append(dict(family, family_id=identity, qualified_gates=int(fitted.sum()), modes=modes))
    return result, dict(
        method='raw-fragment-family-heldout-v1', diagnostic_only=True,
        status='EVALUATED', source_gates=int(result.sum()),
        candidate_gates=int(nomination.sum()), families=records,
        rho_is_weather_truth=False, geometry_is_contamination=False,
        missing_gates_filled=False, work=stats.receipt(),
    )
