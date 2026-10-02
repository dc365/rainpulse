"""Receiver-supported fan families, independent of site, time and elevation.

Bilateral measured shoulders enclose the family, not each narrow ray. Every
acted gate still passes a distance-held-out receiver/range-response model.
Neighbouring models or a coherent held-out corridor establish support.
Angular width alone never rejects; bounded associations retain source causes.
"""

import numpy as np

from .source_blocks import detect as detect_blocks


def detect(s, cfg, *, protected, prepared=None, near_floor_references=False):
    from .source_summary import SourceStatistics

    stats = (prepared or SourceStatistics.build(s, cfg)).use(s, cfg)

    def modes(width=None, proven=None):
        research = {'near_floor_references': True} if near_floor_references else {}
        remaining = {'target_exclusion': proven} if proven is not None else {}
        # A single upper quantile switches processor modes when their mixture
        # crosses 10%. Fit primary and upper modes independently, each with the
        # same target/guard exclusion and physical response tests.
        mask, record = detect_blocks(
            s, cfg, protected=protected, fan=True, family_width_deg=width, prepared=stats,
            **research,
            **remaining,
        )
        primary_targets = {'target_exclusion': mask if proven is None else mask | proven}
        primary, primary_record = detect_blocks(
            s,
            cfg,
            protected=protected,
            fan=True,
            family_width_deg=width,
            prepared=stats,
            response_quantile=50,
            **research,
            **primary_targets,
        )
        mask |= primary
        record["primary_mode"] = primary_record
        record["source_gates"] = int(mask.sum())
        return mask, record

    candidates, record = modes()
    # Only 0, 1, >=2 is needed. Saturate in block space; NEVER uint8 wrap.
    support = np.zeros((s.shape[0], len(stats.ids)), np.uint8)
    quiet = (stats.receiver < cfg.noise_censor_snr_db) | (
        stats.coverage < cfg.noise_censor_minimum_coverage
    )
    model = np.stack([candidates[:, g].sum(axis=1) >= 3 for g in stats.indices], axis=1)
    for row in np.flatnonzero(s.good):
        for direction in (-1, 1):
            current = row
            active = np.ones(len(stats.ids), bool)
            for step in range(1, s.shape[0]):
                stats.geometry()
                other = (row + direction * step) % s.shape[0]
                edge = current if direction == 1 else other
                angle = abs(float((s.azimuth[other] - s.azimuth[row] + 180) % 360 - 180))
                if s.gap_after[edge] or not s.good[other] or angle > 45.0:
                    break
                # Range-block support survives alternating missing REF gates.
                # Below-floor median is not an empty angular shoulder when
                # independently fitted, measured source gates occupy the block.
                # Unknown coverage remains a barrier regardless of candidates.
                active &= (~quiet[other] | model[other]) & (
                    stats.coverage[other] >= cfg.noise_censor_minimum_coverage
                )
                supported_blocks = active & model[other]
                support[row, supported_blocks] = np.minimum(support[row, supported_blocks] + 1, 2)
                if not active.any():
                    break
                current = other
    # A corridor can establish its own held-out distance evidence. Requiring
    # other REF-bearing rays made missing REF in a measured SNR lobe veto an
    # otherwise proven source. This only admits already fitted gates; it never
    # fills gaps or labels a gate from geometry alone.
    _, za = s.moment("DBZH")
    sn, sa = s.moment("SNR")
    eligible = za & sa & (sn >= cfg.noise_censor_snr_db) & ~protected
    if near_floor_references:
        # Bound interior associations to the same actually measured research
        # domain as targets/references; stronger unexpected receiver returns
        # cannot inherit a weak near-floor source identity.
        eligible &= sn <= cfg.noise_censor_snr_db + cfg.radial_source_maximum_spread_db
    eligible &= s.ranges[None, :] >= cfg.receiver.minimum_range_m
    coherent = np.zeros(s.shape[0], bool)
    for row in np.flatnonzero(s.good):
        gates = np.flatnonzero(candidates[row])
        if len(gates) < cfg.receiver.minimum_pair_samples:
            continue
        coherent[row] = (
            len(gates) >= cfg.radial_source_minimum_fraction * max(int(eligible[row].sum()), 1)
            and np.ptp(s.ranges[gates]) >= cfg.radial_source_minimum_span_m
            and np.unique(stats.block_index[gates]).size >= cfg.receiver.minimum_reference_blocks
        )
    out = candidates & ((support[:, stats.block_index] >= 2) | coherent[:, None])
    # A single strong spoke still requires its own bilateral local shoulders
    # and held-out physical evidence. Only targets already proved by the full
    # family can skip this second action proof; all RAW references remain.
    narrow, narrow_record = modes(cfg.radial_source_maximum_width_deg, proven=out)
    out |= narrow
    # Associate only bounded interior gaps between two original source anchors.
    # Never iterate newly added gates: no unbounded propagation along a ray.
    z, _ = s.moment("DBZH")
    added = np.zeros(s.shape, bool)
    for row in np.flatnonzero(s.good):
        anchors = np.flatnonzero(out[row])
        if len(anchors) < 2:
            continue
        targets = np.flatnonzero(eligible[row] & ~out[row])
        pos = np.searchsorted(anchors, targets)
        inside = (pos > 0) & (pos < len(anchors))
        targets, pos = targets[inside], pos[inside]
        left, right = anchors[pos - 1], anchors[pos]
        bounded = s.ranges[right] - s.ranges[left] <= cfg.radial_source_maximum_gap_m
        barrier = ~sa[row] | protected[row] | (sn[row] < cfg.noise_censor_snr_db)
        prefix = np.r_[0, np.cumsum(barrier)]
        bounded &= prefix[right + 1] == prefix[left]
        spread = cfg.radial_source_maximum_spread_db
        receiver_ok = sn[row, targets] <= np.maximum(sn[row, left], sn[row, right]) + spread
        response = z[row] - stats.law
        response_ok = response[targets] <= np.maximum(response[left], response[right]) + spread
        accepted = targets[bounded & receiver_ok & response_ok]
        added[row, accepted] = True
    out |= added
    return out, dict(
        record,
        method="receiver-fan-family-heldout-v3-first-proof",
        associated_interior_gates=int(added.sum()),
        association_maximum_gap_m=cfg.radial_source_maximum_gap_m,
        proposed_gates=int(candidates.sum()),
        source_gates=int(out.sum()),
        narrow_model=narrow_record,
        maximum_family_width_deg=45.0,
        minimum_neighbour_models=2,
        coherent_corridors=int(coherent.sum()),
        reflectivity_ceiling_used=False,
    )
