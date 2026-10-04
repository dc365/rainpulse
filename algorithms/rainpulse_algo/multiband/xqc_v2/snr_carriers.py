"""Complete original SNR geometry with independently declared dB policy.

No writer or product activation lives here. Local enhanced/unknown power remains
unresolved even when the complete native carrier geometry is radial.
"""

from dataclasses import dataclass, replace
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit, checked_mask

from .polar_morphology import MorphologyPolicy, detect


class SNRCarrierPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["x-original-snr-carrier-20261004-v1"] = "x-original-snr-carrier-20261004-v1"
    moment: Literal["SNR"] = "SNR"
    geometry: MorphologyPolicy
    levels_db: tuple[Annotated[float, Field(ge=5.0, le=55.0)], ...] = Field(
        min_length=1, max_length=6
    )
    reference_block_m: Literal[5000.0] = 5000.0
    reference_guard_blocks: Literal[1] = 1
    minimum_reference_blocks: Literal[3] = 3
    minimum_reference_span_m: Literal[20000.0] = 20000.0
    reference_power_margin_db: Literal[1.0] = 1.0
    maximum_work: int = Field(default=50000000, ge=1, le=50000000, strict=True)
    maximum_summary_bytes: int = Field(default=64 * 1024**2, ge=1, le=64 * 1024**2, strict=True)

    @model_validator(mode="after")
    def independent_signal_policy(self):
        if len(set(self.levels_db)) != len(self.levels_db):
            raise ValueError("unique explicit SNR levels required")
        return self


@dataclass(frozen=True)
class SNRCarrierReview:
    candidate: np.ndarray
    local_excess: np.ndarray
    unknown: np.ndarray
    record: dict


def review(sweep, policy, *, target, protected=None):
    p = SNRCarrierPolicy.model_validate(policy)
    shape = sweep.shape
    gates = shape[0] * shape[1]
    # Bound the new native views/summaries before constructing them. Geometry's
    # existing gate/object/work guards separately constrain its internal arrays.
    summary_bytes = gates * 25 + shape[1] * 8
    if gates > p.geometry.maximum_sweep_gates:
        raise ResourceLimit("original SNR native gate budget exceeded")
    if summary_bytes > p.maximum_summary_bytes:
        raise ResourceLimit("original SNR native summary budget exceeded")
    target = checked_mask(target, shape, "SNR review target")
    hard = (
        np.zeros(shape, bool)
        if protected is None
        else checked_mask(protected, shape, "SNR review protection")
    )
    work = 0

    def charge(amount):
        nonlocal work
        work += int(amount)
        if work > p.maximum_work:
            raise ResourceLimit("original SNR geometry/profile work budget exceeded")

    charge(gates * 2)
    before = sweep.digest
    sn, available = sweep.moment("SNR")
    dbzh, dbzh_available = sweep.moment("DBZH")
    target &= dbzh_available & (dbzh >= 5.0) & ~sweep.no_echo & ~hard
    # Only this temporary signal channel changes. Its SNR units and availability
    # are explicit; the original immutable sweep and observation state stay intact.
    signal = replace(
        sweep,
        fields={"DBZH": sn, "SNR": sn},
        available={"DBZH": available, "SNR": available},
        no_echo=np.zeros(shape, bool),
    )
    if work == p.maximum_work:
        raise ResourceLimit("original SNR geometry has no remaining work allowance")
    geometry_policy = p.geometry.model_copy(
        update={
            "levels_dbz": p.levels_db,
            "maximum_work": min(p.geometry.maximum_work, p.maximum_work - work),
        }
    )
    geometry = detect(signal, geometry_policy, protected=hard, collect_carriers=True)
    charge(geometry.record.get("work", 0))
    summary_bytes += sum(ids.nbytes for ids in geometry.carriers)
    if summary_bytes > p.maximum_summary_bytes:
        raise ResourceLimit("original SNR carrier membership summary budget exceeded")
    candidate = np.zeros(shape, bool)
    excess = candidate.copy()
    unknown = candidate.copy()
    buckets = np.floor(sweep.ranges / p.reference_block_m).astype(int)
    for indices in geometry.carriers:
        charge(len(indices))
        rows, cols = np.divmod(indices, shape[1])
        charge(len(rows))
        for row in np.unique(rows):
            charge(len(cols))
            native = cols[rows == row]
            charge(2 * len(native))
            if np.any(np.diff(native) <= 0):
                raise ValueError("original SNR range membership must be ordered and unique")
            native_buckets = buckets[native]
            blocks = np.split(native, np.flatnonzero(np.diff(native_buckets)) + 1)
            statistics = {}
            for block in blocks:
                charge(len(block) * 2)
                valid = available[row, block]
                if valid.sum() >= p.geometry.minimum_flank_fraction * len(block):
                    statistics[int(buckets[block[0]])] = float(
                        np.quantile(sn[row, block[valid]], 0.9)
                    )
            for block in blocks:
                charge(len(block) + len(statistics))
                chosen = block[target[row, block]]
                if not len(chosen):
                    continue
                bucket = int(buckets[block[0]])
                pool = [key for key in statistics if abs(key - bucket) > p.reference_guard_blocks]
                charge(len(pool) * max(int(np.ceil(np.log2(max(len(pool), 2)))), 1))
                ordered = sorted(pool, key=lambda key: (abs(key - bucket), key))
                refs = []
                lo = hi = None
                for index, key in enumerate(ordered):
                    charge(1)
                    refs.append(key)
                    lo = key if lo is None else min(lo, key)
                    hi = key if hi is None else max(hi, key)
                    if index + 1 < len(ordered) and abs(ordered[index + 1] - bucket) == abs(
                        key - bucket
                    ):
                        continue
                    if (
                        len(refs) >= p.minimum_reference_blocks
                        and (hi - lo) * p.reference_block_m >= p.minimum_reference_span_m
                    ):
                        break
                valid = available[row, block]
                if (
                    len(refs) < p.minimum_reference_blocks
                    or (max(refs) - min(refs)) * p.reference_block_m < p.minimum_reference_span_m
                    or valid.sum() < p.geometry.minimum_flank_fraction * len(block)
                ):
                    unknown[row, chosen] = True
                    continue
                charge(len(block) + len(refs) + len(chosen))
                background = float(np.median([statistics[key] for key in refs]))
                power = float(np.quantile(sn[row, block[valid]], 0.9))
                if power > background + p.reference_power_margin_db:
                    excess[row, chosen] = True
                elif power < background - p.reference_power_margin_db:
                    unknown[row, chosen] = True
                else:
                    candidate[row, chosen[available[row, chosen]]] = True
                    unknown[row, chosen[~available[row, chosen]]] = True
    charge(gates * 2)
    candidate &= ~excess & ~unknown
    assert not candidate[~target | ~available | hard].any()
    assert sweep.digest == before
    for array in (candidate, excess, unknown):
        array.setflags(write=False)
    return SNRCarrierReview(
        candidate,
        excess,
        unknown,
        dict(
            status="OFFLINE_CANDIDATE_REVIEW",
            version=p.version,
            moment=p.moment,
            levels_db=list(p.levels_db),
            geometry_signal_units="SNR_dB_not_DBZH",
            geometry_detector_shared=True,
            carrier_count=len(geometry.carriers),
            work=work,
            summary_bytes=summary_bytes,
            candidate_gates=int(candidate.sum()),
            local_excess_gates=int(excess.sum()),
            unknown_gates=int(unknown.sum()),
            raw_unchanged=True,
            weather_truth=False,
            source_confirmed=False,
            production_enabled=False,
            parent_dispositions_changed=False,
        ),
    )
