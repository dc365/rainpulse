"""Long narrow receiver corridors, with held-out range-block prediction.

High rho is neither a source vote nor a weather veto. Geometry alone cannot
act: both shoulders must have measured below-floor SNR, the receiver power
must be stationary and the paired REF must follow its range response in
held-out blocks. This is a candidate source model, not rainfall ground truth.
"""
from __future__ import annotations

import numpy as np


def detect(s, cfg, *, protected, details=None):
    result = np.zeros(s.shape, bool)
    if not cfg.radial_source_enabled:
        return result, {"status": "DISABLED"}
    from .source_summary import SourceStatistics
    stats = SourceStatistics.build(s, cfg)
    z, za = s.moment("DBZH")
    sn, sa = s.moment("SNR")
    floor = cfg.noise_censor_snr_db
    signal = za & sa & (sn >= floor) & (z < cfg.radial_maximum_dbzh)
    signal &= (s.ranges[None, :] >= cfg.receiver.minimum_range_m) & ~protected
    quiet = sa & (sn < floor)
    blocks, law = stats.blocks, stats.law
    models = []
    for row in np.flatnonzero(s.good):
        distances = []
        for direction in (-1, 1):
            nearest = np.full(s.shape[1], np.inf)
            current = row
            for step in range(1, s.shape[0]):
                stats.trial()
                other = (row + direction * step) % s.shape[0]
                edge = current if direction == 1 else other
                angle = abs(float((s.azimuth[other] - s.azimuth[row] + 180) % 360 - 180))
                if s.gap_after[edge] or not s.good[other] or angle > cfg.radial_source_maximum_width_deg:
                    break
                measured = quiet[other] & (sn[row] - sn[other] >= cfg.radial_flank_contrast_db)
                nearest = np.where(measured & ~np.isfinite(nearest), angle, nearest)
                current = other
            distances.append(nearest)
        support = signal[row] & ((distances[0] + distances[1]) <= cfg.radial_source_maximum_width_deg)
        indices = np.flatnonzero(support)
        if len(indices) < cfg.receiver.minimum_pair_samples:
            continue
        # Bridge bounded gaps only to form reference groups; actions remain on
        # actually measured target gates. Missing geometry is never filled.
        cuts = np.flatnonzero(np.diff(s.ranges[indices]) > cfg.radial_source_maximum_gap_m + s.dr) + 1
        for segment in np.split(indices, cuts):
            if len(segment) < cfg.receiver.minimum_pair_samples:
                continue
            span = float(s.ranges[segment[-1]] - s.ranges[segment[0]] + s.dr)
            if span < cfg.radial_source_minimum_span_m or len(segment) * s.dr / span < cfg.radial_source_minimum_fraction:
                continue
            for block in np.unique(blocks[segment]):
                stats.trial()
                train = segment[abs(blocks[segment] - block) > cfg.receiver.guard_blocks]
                target = segment[blocks[segment] == block]
                if len(train) < cfg.receiver.minimum_pair_samples or len(np.unique(blocks[train])) < 3:
                    continue
                if np.ptp(s.ranges[train]) < cfg.radial_source_minimum_span_m:
                    continue
                power = sn[row, train]
                response = z[row, train] - law[train]
                center, offset = float(np.median(power)), float(np.median(response))
                spread = cfg.radial_source_maximum_spread_db
                if np.percentile(abs(power-center), 90) > spread or np.percentile(abs(response-offset), 90) > spread:
                    continue
                # A range-dependent rain core cannot qualify just by fitting
                # its own gates: neither target nor adjacent blocks train it.
                good = (abs(sn[row, target]-center) <= spread) & (abs(z[row, target]-law[target]-offset) <= spread)
                accepted = target[good]
                result[row, accepted] = True
                if len(accepted):
                    stats.model()
                    models.append({"ray": int(row), "target_block": int(block),
                                   "reference_blocks": np.unique(blocks[train]).tolist(),
                                   "reference_gates": int(len(train)), "target_gates": int(len(accepted)),
                                   "snr_center_db": center, "range_response_offset_db": offset})
    kinds = result.astype(np.uint8)  # 1=continuous, 2=blocks, 4=fan (not votes)
    block_record = {"status": "DISABLED"}
    if cfg.radial_source_block_model_enabled:
        from .source_blocks import detect as detect_blocks
        block_mask, block_record = detect_blocks(s, cfg, protected=protected, prepared=stats)
        result |= block_mask
        kinds[block_mask] |= 2
    fan_record = {"status": "DISABLED"}
    if cfg.radial_source_fan_model_enabled:
        from .source_fans import detect as detect_fans
        fan_mask, fan_record = detect_fans(s, cfg, protected=protected, prepared=stats)
        result |= fan_mask
        kinds[fan_mask] |= 4
    if details is not None:
        details["source_kind"] = kinds
    return result, {"status": "EVALUATED", "kind_counts": {
                    "continuous": int(((kinds & 1) != 0).sum()),
                    "blocks": int(((kinds & 2) != 0).sum()),
                    "fan": int(((kinds & 4) != 0).sum()),
                    "union": int(result.sum())},
                    "kind_bits_are_independent_votes": False, "fan_model": fan_record, "block_model": block_record, "source_gates": int(result.sum()),
                    "models": models, "method": "bilateral-receiver-corridor-heldout-v1",
                    "rho_is_weather_truth": False, "work": stats.receipt()}
