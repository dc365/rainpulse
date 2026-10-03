"""Offline complete-carrier compact review, with held-out local power support.

Geometry nominates exact members; a compact contour is a proxy, not weather
truth. Local receiver excess or missing evidence stays unresolved. This module
does not participate in the normal pipeline or alter parent dispositions.
"""

from dataclasses import dataclass

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .polar_morphology import MorphologyPolicy, detect


@dataclass(frozen=True)
class CarrierReview:
    candidate: np.ndarray
    local_excess: np.ndarray
    unknown: np.ndarray
    record: dict


def review(sweep, policy, *, protected=None):
    p = MorphologyPolicy.model_validate(policy)
    parent = detect(sweep, p, protected=protected, collect_carriers=True)
    shape = sweep.shape
    candidate = np.zeros(shape, bool)
    excess = np.zeros(shape, bool)
    unknown = np.zeros(shape, bool)
    compact = parent.counterexample_mask
    work = parent.record.get("work", 0)

    def charge(count):
        nonlocal work
        work += int(count)
        if work > p.maximum_work:
            raise ResourceLimit("complete-carrier held-out work budget exceeded")

    sn, sa = sweep.moment("SNR")
    # Same physical reference scale and membership margin as the receiver/fan
    # branch. No reflectivity/log-range response fit is a shape prerequisite.
    buckets = np.floor(sweep.ranges / 5000.0).astype(int)
    if compact is not None:
        for indices in parent.carriers:
            charge(len(indices))
            rows, cols = np.divmod(indices, shape[1])
            for row in np.unique(rows):
                charge(len(cols))
                native = cols[rows == row]
                targets = native[compact[row, native]]
                if not len(targets):
                    continue
                # Original entries are ordered disjoint range windows. Partition
                # once: rescanning all members per bucket creates uncharged
                # quadratic work on long native carriers.
                charge(2 * len(native))
                if np.any(np.diff(native) <= 0):
                    raise ValueError("original carrier range membership is not ordered and unique")
                native_buckets = buckets[native]
                blocks = np.split(native, np.flatnonzero(np.diff(native_buckets)) + 1)
                statistics = {}
                for block in blocks:
                    bucket = buckets[block[0]]
                    charge(len(block))
                    valid = sa[row, block]
                    if valid.sum() >= p.minimum_flank_fraction * len(block):
                        statistics[int(bucket)] = float(np.quantile(sn[row, block[valid]], 0.9))
                for target in blocks:
                    bucket = buckets[target[0]]
                    chosen = target[compact[row, target]]
                    if not len(chosen):
                        charge(len(target))
                        continue
                    charge(len(target) + len(statistics))
                    pool = [key for key in statistics if abs(key - int(bucket)) > 1]
                    # Full original geometry still qualifies the carrier. Local
                    # support uses the nearest held-out stencil, not a universal
                    # power level over the complete native range axis.
                    charge(len(pool) * max(int(np.ceil(np.log2(max(len(pool), 2)))), 1))
                    ordered = sorted(pool, key=lambda key: (abs(key - int(bucket)), key))
                    refs = []
                    lo, hi = None, None
                    for index, key in enumerate(ordered):
                        refs.append(key)
                        lo = key if lo is None else min(lo, key)
                        hi = key if hi is None else max(hi, key)
                        # No arbitrary left/right vote at equal distance.
                        if index + 1 < len(ordered) and abs(
                            ordered[index + 1] - int(bucket)
                        ) == abs(key - int(bucket)):
                            continue
                        if len(refs) >= 3 and (hi - lo) * 5000.0 >= 20000.0:
                            break
                    valid = sa[row, target]
                    # A finite-value placeholder is never a measured SNR vote.
                    enough = (
                        len(refs) >= 3
                        and (max(refs) - min(refs)) * 5000.0 >= 20000.0
                        and valid.sum() >= p.minimum_flank_fraction * len(target)
                    )
                    if not enough:
                        unknown[row, chosen] = True
                        continue
                    background = float(np.median([statistics[key] for key in refs]))
                    target_power = float(np.quantile(sn[row, target[valid]], 0.9))
                    if target_power > background + 1.0:
                        excess[row, chosen] = True
                    elif target_power < background - 1.0:
                        unknown[row, chosen] = True
                    else:
                        candidate[row, chosen[sa[row, chosen]]] = True
                        unknown[row, chosen[~sa[row, chosen]]] = True
    # Conflicting original carriers retain uncertainty. A narrower overlapping
    # track cannot erase a local-excess decision from its complete predecessor.
    candidate &= ~excess & ~unknown
    for array in (candidate, excess, unknown):
        array.setflags(write=False)
    return CarrierReview(
        candidate,
        excess,
        unknown,
        dict(
            status="OFFLINE_CANDIDATE_REVIEW",
            version="x-fragmented-carrier-review-v3",
            field_statistic="matched_target_and_reference_snr_p90",
            reference_scope="nearest_heldout_support_stencil_on_complete_original_carrier",
            work=work,
            carrier_count=len(parent.carriers),
            candidate_gates=int(candidate.sum()),
            local_excess_gates=int(excess.sum()),
            unknown_gates=int(unknown.sum()),
            source_confirmed=False,
            weather_truth=False,
            production_enabled=False,
            parent_dispositions_changed=False,
        ),
    )
