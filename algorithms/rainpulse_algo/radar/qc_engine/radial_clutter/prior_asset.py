"""Numeric NPZ payloads with embedded metadata; never pickle."""
import json

import numpy as np

from .geometry import digest_arrays
from .prior_types import align_rows, utc


def finish_prior(builder):
    if builder.last_time is None:
        raise ValueError("no reviewed samples")
    count, hits = builder.count, builder.hits
    days = builder.days + builder.today.astype("uint16")
    n = np.maximum(count.astype(float), 1)
    rate = hits / n
    z = 1.96
    center = (rate + z*z/(2*n)) / (1 + z*z/n)
    half = z * np.sqrt(rate*(1-rate)/n + z*z/(4*n*n)) / (1 + z*z/n)
    supported = (count >= builder.minimum_observations) & (days >= builder.minimum_days)
    metadata = {"schema": "rainpulse.clutter-prior.rc1", "radar_id": builder.radar,
                "partition_id": builder.partition, "echo_threshold_dbz": builder.threshold,
                "minimum_days": builder.minimum_days, "minimum_observations": builder.minimum_observations,
                "last_sample_utc": builder.last_time.isoformat(), "samples": builder.receipts,
                "meaning": "reviewed_clear_air_echo_frequency_not_confirmed_clutter"}
    arrays = {"range": builder.reference.ranges.copy(), "azimuth": builder.reference.azimuth.copy(),
              "elevation": builder.reference.elevation.copy(), "observed_count": count.copy(),
              "hit_count": hits.copy(), "day_count": days.copy(),
              "frequency": np.where(supported, (hits+1)/(n+2), np.nan).astype("float32"),
              "lower": np.where(supported, center-half, np.nan).astype("float32"),
              "upper": np.where(supported, center+half, np.nan).astype("float32"),
              "metadata": np.frombuffer(json.dumps(metadata, sort_keys=True).encode(), dtype="uint8").copy()}
    return arrays, digest_arrays(arrays)


def match_prior(arrays, expected_sha, n, radar_id, partition_id, target_time, cfg):
    if digest_arrays(arrays) != expected_sha:
        raise ValueError("clutter prior checksum differs")
    meta = json.loads(bytes(arrays["metadata"]).decode())
    if meta["schema"] != "rainpulse.clutter-prior.rc1":
        raise ValueError("unknown prior schema")
    if (meta["radar_id"], meta["partition_id"]) != (radar_id, partition_id):
        raise ValueError("prior radar or hardware/scan partition differs")
    if utc(meta["last_sample_utc"]) >= utc(target_time):
        raise ValueError("future or overlapping prior samples")
    if not np.array_equal(arrays["range"], n.ranges):
        raise ValueError("prior range differs")
    count, hits, days = arrays["observed_count"], arrays["hit_count"], arrays["day_count"]
    if np.any(hits > count) or np.any(days > count):
        raise ValueError("inconsistent prior counts")
    rows, safe = align_rows(arrays["azimuth"], arrays["elevation"], n, cfg.prior_azimuth_tolerance_fraction)
    enough = (count >= cfg.prior_minimum_observations) & (days >= cfg.prior_minimum_days)
    result = {}
    for key in ("frequency", "lower", "upper"):
        value = arrays[key]
        if value.shape != count.shape or np.any(np.isfinite(value) & ((value < 0) | (value > 1))):
            raise ValueError("invalid prior statistics")
        valid = enough[rows] & safe[:, None] & np.isfinite(value[rows])
        result[key] = np.where(valid, value[rows], np.nan).astype("float32")
    result["receipt_sha256"] = expected_sha
    return result
