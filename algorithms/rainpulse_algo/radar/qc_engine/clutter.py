"""Create a versioned prior from explicitly reviewed clear-air native volumes."""

from __future__ import annotations

import hashlib

import numpy as np


def asset_digest(arrays: dict[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for key in sorted(arrays):
        values = np.asarray(arrays[key])
        digest.update(key.encode() + str(values.dtype).encode() + str(values.shape).encode())
        digest.update(values.tobytes())
    return digest.hexdigest()


def build_clutter_prior(
    roots, *, reviewed_clear_air: bool, minimum_samples: int = 12, echo_threshold_dbz: float = 0.0
) -> tuple[dict, dict]:
    if not reviewed_clear_air or len(roots) < minimum_samples:
        raise ValueError("clutter prior requires enough explicitly reviewed clear-air volumes")
    first = roots[0]
    identities = [root.attrs.get("scan_id", root.attrs.get("asset_id")) for root in roots]
    if None in identities or len(set(identities)) != len(identities):
        raise ValueError("clutter samples must be unique identified volumes")
    arrays = {}
    for number in first["sweep_number"][:]:
        name = f"sweep_{int(number):03d}"
        observed_count = np.zeros(first[name]["DBZH"].shape, dtype="int32")
        echo_count = np.zeros_like(observed_count)
        for root in roots:
            if root.attrs.get("radar_id") != first.attrs.get("radar_id") or name not in root:
                raise ValueError("incompatible clutter sample radar/cut")
            for coord in ("range", "azimuth", "elevation"):
                if not np.array_equal(root[name][coord][:], first[name][coord][:]):
                    raise ValueError("clutter samples require identical native geometry")
            dbzh = root[name]["DBZH"][:]
            valid = np.isfinite(dbzh)
            observed_count += valid
            echo_count += valid & (dbzh >= echo_threshold_dbz)
        fraction = np.divide(
            echo_count,
            observed_count,
            out=np.full(echo_count.shape, np.nan, dtype="float64"),
            where=observed_count >= minimum_samples,
        )
        arrays[f"{name}__ground_clutter"] = fraction.astype("float32")
        for coord in ("range", "azimuth", "elevation"):
            arrays[f"{name}__{coord}"] = first[name][coord][:]
    return arrays, {
        "asset_content_sha256": asset_digest(arrays),
        "source_scan_ids": identities,
        "minimum_samples": minimum_samples,
        "echo_threshold_dbz": echo_threshold_dbz,
        "semantics": "clear_air_echo_frequency_prior_not_certain_clutter",
        "reviewed_clear_air": True,
    }
