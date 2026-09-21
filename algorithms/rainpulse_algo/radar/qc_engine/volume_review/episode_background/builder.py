"""Frozen raw-only episode distributions with contiguous-time support checks."""
from __future__ import annotations

from dataclasses import dataclass
import warnings
import numpy as np
from . import VERSION
from .config import BuildConfig
from .data import Sample, MOMENTS, utc, is_hash, map_footprints, wrap


@dataclass(frozen=True)
class Background:
    metadata: dict
    arrays: dict[str, np.ndarray]


def _quantile(values, q):
    """Linear finite-sample quantile, vectorized over native gates.

    Time is bounded (<=256 samples). Sorting along that short axis avoids
    numpy.nanquantile's per-gate Python fallback on large masked radar arrays.
    No spatial filling or interpolation of missing observations is performed.
    """
    if not 0 <= q <= 1:
        raise ValueError("quantile outside unit interval")
    values = np.asarray(values)
    finite = np.isfinite(values)
    count = finite.sum(axis=0)
    ordered = np.sort(np.where(finite, values, np.inf), axis=0)
    rank = np.maximum(count - 1, 0) * float(q)
    lo = np.floor(rank).astype(int)
    hi = np.minimum(lo + 1, np.maximum(count - 1, 0))
    left = np.take_along_axis(ordered, lo[None, ...], axis=0)[0].astype(float)
    right = np.take_along_axis(ordered, hi[None, ...], axis=0)[0].astype(float)
    left = np.where(count > 0, left, 0.)
    right = np.where(count > 0, right, 0.)
    return np.where(count > 0, left + (right - left) * (rank - lo), np.nan)


def build_episode(samples: list[Sample], cfg: BuildConfig, *, review_receipt: str,
                  reviewed_no_precipitation: bool, origin_time: str | None = None) -> Background:
    """Caller must pass reference samples only, never weather targets or QC fields."""
    if reviewed_no_precipitation is not True or not is_hash(review_receipt):
        raise ValueError("explicit reviewed no-precipitation receipt required")
    if not samples:
        raise ValueError("empty episode")
    identities = {(s.radar_id.lower(), s.processing_id) for s in samples}
    if len(identities) != 1:
        raise ValueError("one radar and processing identity per asset required")
    by_sweep = {}
    seen = set()
    times_by_scan = {}
    for s in samples:
        key = s.scan_id, s.sweep_id
        if key in seen:
            raise ValueError("duplicate scan/sweep observation")
        seen.add(key)
        by_sweep.setdefault(s.sweep_id, []).append(s)
        t = utc(s.observed_at).timestamp()
        # All layers may have different actual acquisition times. Keep them;
        # do not count multiple layers as independent time samples of one gate.
        times_by_scan.setdefault(s.scan_id, []).append(t)
    origin = utc(origin_time).timestamp() if origin_time else min(utc(s.observed_at).timestamp() for s in samples)
    before = [s.raw_digest for s in samples]
    arrays, sweeps = {}, []
    for index, (sweep_id, seq) in enumerate(sorted(by_sweep.items())):
        seq = sorted(seq, key=lambda x: utc(x.observed_at))
        n = len(seq)
        if not cfg.minimum_samples <= n <= cfg.maximum_samples:
            raise ValueError("insufficient or excessive reference scans for " + sweep_id)
        t = np.array([utc(s.observed_at).timestamp() for s in seq])
        if len(set(t.tolist())) != n:
            raise ValueError("duplicate sweep acquisition times")
        if t[-1] - t[0] < cfg.minimum_episode_span_seconds or np.any(t < origin):
            raise ValueError("episode too short or origin after observations")
        blocks = np.floor((t - origin) / cfg.block_seconds).astype(int)
        block_ids = np.unique(blocks)
        if len(block_ids) < cfg.minimum_blocks or len(block_ids) > 16:
            raise ValueError("insufficient/excessive episode time blocks")
        if len(set(s.source_sha256 for s in seq)) != n:
            raise ValueError("duplicate input content receipts within one sweep")
        first = seq[0]
        order = np.argsort(first.azimuth % 360, kind="stable")
        az = np.asarray(first.azimuth)[order] % 360
        el = np.asarray(first.elevation)[order]
        r = np.asarray(first.ranges)[np.asarray(first.ranges) <= cfg.maximum_range_m]
        good = np.asarray(first.geometry_good)[order]
        if len(r) < 2:
            raise ValueError("no usable reference range")
        shape = len(az), len(r)
        cells = int(np.prod(shape))
        if cells > cfg.maximum_sweep_cells or cells * n > cfg.maximum_stack_elements:
            raise ValueError("episode memory budget exceeded before allocation")
        maps = [map_footprints(s.azimuth, s.elevation, s.ranges, s.geometry_good, az, el, r, cfg) for s in seq]
        prefix = f"s{index:03d}__"
        a = {"azimuth": az.astype("float64"), "elevation": el.astype("float64"),
             "range": r.astype("float64"), "geometry_good": good.astype("uint8")}
        foot = np.stack([m[2] for m in maps])
        a["n_slots"] = foot.sum(axis=0).astype("uint16")
        acquired = np.zeros(shape, "uint16"); noecho = acquired.copy(); echo_known = acquired.copy()
        for s, m in zip(seq, maps):
            rows, gates, ok, _, _ = m
            if s.acquired is not None:
                use = np.ix_(rows, gates)
                acq = np.asarray(s.acquired, bool)[use] & ok
                ne = np.asarray(s.no_echo, bool)[use] & acq
                z, valid = s.moment("DBZH")
                hits = valid[use] & (z[use] >= cfg.no_rain_below_dbz)
                if np.any(ne & hits):
                    raise ValueError("confirmed no-echo conflicts with raw echo")
                # Acquisition alone does not imply no echo; unresolved statuses
                # are not part of the occurrence denominator.
                decidable = acq & (ne | valid[use])
                acquired += decidable.astype("uint16"); noecho += ne.astype("uint16")
                echo_known += (decidable & hits).astype("uint16")
        a["n_decidable_acquisition"] = acquired
        a["n_confirmed_noecho"] = noecho
        a["n_decidable_echo"] = echo_known
        a["occurrence_available"] = (acquired >= cfg.minimum_samples).astype("uint8")
        a["echo_occurrence_fraction"] = np.divide(echo_known, acquired,
            out=np.full(shape, np.nan, "float32"), where=acquired >= cfg.minimum_samples)
        block_counts_z = None; block_medians_z = None; snr_drift = None
        for field in MOMENTS:
            values = np.full((n, *shape), np.nan, "float32")
            present = np.zeros_like(values, bool)
            tail_counts = {"positive": np.zeros(shape, "uint16"), "negative": np.zeros(shape, "uint16")}
            for j, (s, m) in enumerate(zip(seq, maps)):
                v, va = s.moment(field); rows, gates, ok, _, _ = m
                picked = np.ix_(rows, gates)
                use = va[picked] & ok
                present[j] = use
                if field == "ZDR":
                    tail_counts["positive"] += (use & (v[picked] >= cfg.zdr_tail_db)).astype("uint16")
                    tail_counts["negative"] += (use & (v[picked] <= -cfg.zdr_tail_db)).astype("uint16")
                if field in ("RHOHV", "ZDR", "PHIDP"):
                    signal, signal_ok = s.moment("SNR")
                    use &= signal_ok[picked] & (signal[picked] >= cfg.minimum_polar_snr_db)
                values[j] = np.where(use, v[picked], np.nan)
            if field == "ZDR":
                den = present.sum(axis=0)
                a["zdr_state_n"] = den.astype("uint16")
                for name, counts in tail_counts.items():
                    a["zdr_tail_" + name + "_fraction"] = np.divide(counts, den,
                        out=np.full(shape, np.nan, "float32"), where=den > 0)
                values[abs(values) >= cfg.zdr_tail_db] = np.nan
            if field == "PHIDP":
                den = np.isfinite(values).sum(axis=0)
                rad = np.deg2rad(np.where(np.isfinite(values), values, 0))
                c = np.where(np.isfinite(values), np.cos(rad), 0).sum(axis=0)
                si = np.where(np.isfinite(values), np.sin(rad), 0).sum(axis=0)
                center = np.rad2deg(np.arctan2(si, c))
                center[den == 0] = np.nan
                deviations = wrap(values - center)
                median = wrap(center + _quantile(deviations, .5))
                mad = _quantile(abs(wrap(values - median)), .5)
                resultant = np.divide(np.hypot(c, si), den, out=np.full(shape, np.nan), where=den > 0)
                a["PHIDP_resultant"] = resultant.astype("float32")
            else:
                median = _quantile(values, .5)
                mad = _quantile(abs(values - median), .5)
            count = np.isfinite(values).sum(axis=0)
            a[field + "_n"] = count.astype("uint16")
            a[field + "_median"] = median.astype("float32")
            a[field + "_mad"] = mad.astype("float32")
            a[field + "_q10"] = _quantile(values, .1).astype("float32") if field != "PHIDP" else np.full(shape, np.nan, "float32")
            a[field + "_q90"] = _quantile(values, .9).astype("float32") if field != "PHIDP" else np.full(shape, np.nan, "float32")
            counts, medians = [], []
            for b in block_ids:
                block = values[blocks == b]
                counts.append(np.isfinite(block).sum(axis=0))
                medians.append(_quantile(block, .5))
            counts = np.asarray(counts); medians = np.asarray(medians)
            a[field + "_block_counts"] = counts.astype("uint16")
            a[field + "_block_medians"] = medians.astype("float32")
            if field in ("DBZH", "SNR"):
                support = counts >= cfg.minimum_samples_per_block
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    masked = np.where(support, medians, np.nan)
                    drift = np.nanmax(masked, axis=0) - np.nanmin(masked, axis=0)
                a[field + "_block_drift"] = drift.astype("float32")
                if field == "DBZH":
                    block_counts_z, block_medians_z = support, medians
                else:
                    snr_drift = drift
        fraction = np.divide(a["DBZH_n"], a["n_slots"], out=np.zeros(shape, "float32"), where=a["n_slots"] > 0)
        a["measured_support_fraction"] = fraction
        stable = ((a["DBZH_n"] >= cfg.minimum_samples) & (fraction >= cfg.minimum_measured_fraction) &
                  (block_counts_z.sum(axis=0) >= cfg.minimum_blocks) &
                  (a["DBZH_block_drift"] <= cfg.maximum_dbzh_block_drift) & good[:, None])
        # SNR is not required to represent a DBZH background, but if sufficiently
        # observed, nonstationary SNR prevents action-grade stable-core status.
        stable &= (a["SNR_n"] < cfg.minimum_samples) | (snr_drift <= cfg.maximum_snr_block_drift)
        a["stable_measured_core"] = stable.astype("uint8")
        if sum(v.nbytes for v in arrays.values()) + sum(v.nbytes for v in a.values()) > cfg.maximum_asset_array_bytes:
            raise ValueError("background output arrays exceed declared resource budget")
        arrays.update({prefix + k: v for k, v in a.items()})
        sweeps.append({"sweep_id": sweep_id, "prefix": prefix, "shape": list(shape), "samples": n,
                       "time_blocks": block_ids.tolist(), "stable_cells": int(stable.sum()),
                       "source_scan_ids": [s.scan_id for s in seq],
                       "source_sha256": [s.source_sha256 for s in seq],
                       "source_times": [s.observed_at for s in seq],
                       "source_raw_digests": [s.raw_digest for s in seq]})
    if before != [s.raw_digest for s in samples]:
        raise RuntimeError("builder modified raw observations")
    metadata = {"schema": VERSION, "grade": "short_episode", "radar_id": samples[0].radar_id.lower(),
                "processing_id": samples[0].processing_id, "reviewed_no_precipitation": True,
                "review_receipt": review_receipt, "build_config": cfg.model_dump(mode="json"),
                "build_config_sha256": cfg.digest, "origin_time_seconds": origin,
                "start_time": min(samples, key=lambda s: utc(s.observed_at)).observed_at,
                "end_time": max(samples, key=lambda s: utc(s.observed_at)).observed_at,
                "sweeps": sweeps, "sample_count_total_sweeps": len(samples),
                "probabilities_calibrated": False, "missing_is_no_echo": False,
                "sample_dependence": "one_short_episode_not_independent_weather_events"}
    for value in arrays.values(): value.flags.writeable = False
    return Background(metadata, arrays)
