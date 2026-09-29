"""Receiver-supported fan families, independent of site, time and elevation.

Bilateral measured shoulders enclose the family, not each narrow ray. Every
acted gate still passes a distance-held-out receiver/range-response model.
Neighbouring model support is required; angular width alone never rejects.
"""
import numpy as np
import warnings
from .source_blocks import detect as detect_blocks


def detect(s, cfg, *, protected):
    candidates, record = detect_blocks(s, cfg, protected=protected, fan=True)
    support = np.zeros(s.shape, np.uint8)
    blocks = (s.ranges // cfg.receiver.block_m).astype(int)
    block_gates = [blocks == b for b in np.unique(blocks)]
    sn, available = s.moment("SNR")
    quiet = np.zeros((s.shape[0],len(block_gates)),bool)
    model = np.zeros_like(quiet)
    for b,gates in enumerate(block_gates):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore",RuntimeWarning)
            median = np.nanmedian(np.where(available[:,gates],sn[:,gates],np.nan),axis=1)
        quiet[:,b] = (median < cfg.noise_censor_snr_db) | (available[:,gates].mean(axis=1) < cfg.noise_censor_minimum_coverage)
        model[:,b] = candidates[:,gates].sum(axis=1) >= 3
    for row in np.flatnonzero(s.good):
        for direction in (-1, 1):
            current = row
            active = np.ones(len(block_gates),bool)
            for step in range(1, s.shape[0]):
                other = (row + direction*step) % s.shape[0]
                edge = current if direction == 1 else other
                angle = abs(float((s.azimuth[other]-s.azimuth[row]+180)%360-180))
                if s.gap_after[edge] or not s.good[other] or angle > 45.:
                    break
                # Range-block support survives alternating missing REF gates.
                active &= ~quiet[other]
                for b in np.flatnonzero(active & model[other]):
                    support[row,block_gates[b]] += 1
                if not active.any():
                    break
                current = other
    # A single strong spoke has bilateral local shoulders instead of a
    # multi-ray family. It needs the same held-out physical evidence.
    narrow, narrow_record = detect_blocks(s, cfg, protected=protected, fan=True,
                                         family_width_deg=cfg.radial_source_maximum_width_deg)
    out = (candidates & (support >= 2)) | narrow
    return out, dict(record, method='receiver-fan-family-heldout-v1',
                     proposed_gates=int(candidates.sum()), source_gates=int(out.sum()),
                     narrow_model=narrow_record,
                     maximum_family_width_deg=45., minimum_neighbour_models=2,
                     reflectivity_ceiling_used=False)
