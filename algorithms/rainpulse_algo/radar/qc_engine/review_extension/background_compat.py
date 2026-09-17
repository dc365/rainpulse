"""Explicit v2 adapters for the two retained historical builder entrypoints.

Legacy callers remain reproducible; all v2 calls share build_background. No
legacy asset is upgraded merely by renaming its version.
"""
import hashlib
import numpy as np
from .background import build_background, ASSET_VERSION, IDENTITY_FIELDS, timestamp


def from_roots(roots, *, sample_metadata, policy=None):
    if sample_metadata is None:
        raise ValueError("v2 root builder needs per-scan reviewed provenance and explicit states")
    def samples():
        for root in roots:
            scan_id = str(root.attrs.get("scan_id", ""))
            meta = dict(sample_metadata.get(scan_id, {}))
            for key in IDENTITY_FIELDS:
                if key not in meta:
                    meta[key] = root.attrs.get(key)
                if root.attrs.get(key) is not None and meta[key] != root.attrs[key]:
                    raise ValueError(f"root/manifest {key} differs")
            actual_time = root.attrs.get("volume_end_time_utc")
            if not actual_time:
                raise ValueError("normalized root lacks observation end time")
            if meta.get("observed_at_utc") and timestamp(meta["observed_at_utc"]) != timestamp(actual_time):
                raise ValueError("root/manifest observation time differs")
            if meta.get("scan_id") and str(meta["scan_id"]) != scan_id:
                raise ValueError("root/manifest scan identity differs")
            per_cut = meta.pop("sweeps", {})
            for number in root["sweep_number"][:]:
                name = f"sweep_{int(number):03d}"
                g = root[name]
                states = per_cut.get(name, {})
                if not {"observed_mask", "no_echo_mask"} <= states.keys():
                    raise ValueError("explicit per-cut detection-state masks required for v2")
                yield {
                    **meta, "scan_id": scan_id, "sweep_name": name,
                    "observed_at_utc": meta.get("observed_at_utc", root.attrs.get("volume_end_time_utc")),
                    "azimuth_deg": g["azimuth"][:], "range_m": g["range"][:],
                    "elevation_deg": g["elevation"][:], "dbzh": g["DBZH"][:],
                    "observed_mask": states["observed_mask"], "no_echo_mask": states["no_echo_mask"],
                    **{dst: g[src][:] for src, dst in (("VR", "vr"), ("SW", "sw")) if src in g},
                }
    return build_background(samples(), policy)


def from_samples(samples, asset_type, *, policy=None):
    arrays, meta = build_background(samples, policy)
    names = meta["sweeps"]
    geometry = {}
    for name in names:
        az, r = arrays[name+"__azimuth"], arrays[name+"__range"]
        geometry[name] = {
            "ray_count": int(az.size), "gate_count": int(r.size),
            "azimuth_float32_sha256": hashlib.sha256(az.astype("<f4").tobytes()).hexdigest(),
            "range_float32_sha256": hashlib.sha256(r.astype("<f4").tobytes()).hexdigest(),
        }
    return asset_type(
        radar_id=meta["radar_id"],
        elevation_deg_by_sweep={n: float(np.median(arrays[n+"__elevation"])) for n in names},
        geometry_by_sweep=geometry, asset_version=ASSET_VERSION,
        dbzh_threshold=meta["policy"]["echo_threshold_dbz"],
        minimum_clear_sky_days=meta["policy"]["minimum_days"],
        minimum_gate_observations=meta["policy"]["minimum_observations"],
        probability_by_sweep={n: arrays[n+"__ground_clutter"] for n in names},
        support_count_by_sweep={n: arrays[n+"__observed_count"] for n in names},
        clear_sky_day_count_by_sweep={n: len({x["observed_at_utc"][:10] for x in meta["inputs"] if x["sweep_name"] == n}) for n in names},
        canonical_arrays=arrays, canonical_metadata=meta,
    )
