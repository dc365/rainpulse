"""One auditable clear-air background builder for both former entrypoints.

Input must be chronological. Memory is bounded by the current day's native cuts,
not by the number of days. No prior QC labels or inferred NaN denominators.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import os
import tempfile
import numpy as np
from .arrays import mask
from .config import BackgroundPolicy

ASSET_VERSION = "rainpulse-clutter-background-v2"
IDENTITY_FIELDS = ("radar_id", "radar_config_version", "scan_strategy_id", "hardware_config_version")


def digest(arrays):
    h = hashlib.sha256()
    for key in sorted(arrays):
        x = np.asarray(arrays[key])
        if x.dtype.hasobject:
            raise ValueError("object arrays are forbidden in background assets")
        h.update(key.encode() + str(x.dtype).encode() + str(x.shape).encode())
        h.update(np.ascontiguousarray(x).tobytes())
    return h.hexdigest()


def text(sample, name):
    value = sample.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"background sample requires {name}")
    return value


def timestamp(value):
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("background timestamps require an explicit timezone")
    return t.astimezone(timezone.utc)


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("input_sha256 must be a lowercase SHA256")
    return value


def metadata(arrays):
    x = np.asarray(arrays.get("__meta_json"))
    if x.dtype != np.uint8 or x.ndim != 1 or x.size > 16 * 1024 * 1024:
        raise ValueError("missing/invalid v2 asset metadata")
    value = json.loads(x.tobytes().decode("utf-8"))
    if value.get("asset_version") != ASSET_VERSION:
        raise ValueError("unsupported background schema")
    return value


def _wilson(successes, n, z):
    with np.errstate(divide="ignore", invalid="ignore"):
        p = successes / n
        return np.clip((p + z*z/(2*n) - z*np.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n), 0., 1.)


def build_background(samples, policy=None):
    policy = policy or BackgroundPolicy()
    cuts, identity, inputs, seen = {}, None, [], set()
    seen_sources, seen_times = set(), set()
    previous_time, active_day = None, None
    daily = {}

    def finish_day():
        for name, (n, hits) in daily.items():
            ok = n > 0
            frac = np.divide(hits, n, out=np.zeros(n.shape, float), where=ok)
            cuts[name]["day_count"][ok] += 1
            cuts[name]["day_frequency_sum"][ok] += frac[ok]
            # A day is a support vote only when >=80% of its observed scans hit.
            cuts[name]["frequent_day_count"][ok & (frac >= .8)] += 1
        daily.clear()

    for sample in samples:
        current_identity = {k: text(sample, k) for k in IDENTITY_FIELDS}
        if identity is None:
            identity = current_identity
        if identity != current_identity:
            raise ValueError("radar/strategy/hardware/config identity changed inside background")
        if sample.get("reviewed_clear_air") is not True or sample.get("case_category") != "clear_sky":
            raise ValueError("only explicitly reviewed clear_sky labels may build the background")
        scan_id, name = text(sample, "scan_id"), text(sample, "sweep_name")
        source_sha = _sha(sample.get("input_sha256"))
        t = timestamp(text(sample, "observed_at_utc"))
        if previous_time is not None and t < previous_time:
            raise ValueError("background input must be chronological")
        previous_time = t
        key = (scan_id, name)
        if key in seen or (source_sha, name) in seen_sources or (t.isoformat(), name) in seen_times:
            raise ValueError("duplicate physical background observation")
        seen.add(key); seen_sources.add((source_sha, name)); seen_times.add((t.isoformat(), name))
        day = t.date().isoformat()
        if active_day is not None and day != active_day:
            finish_day()
        active_day = day
        az = np.asarray(sample.get("azimuth_deg"), dtype="float64")
        r = np.asarray(sample.get("range_m"), dtype="float64")
        el = np.asarray(sample.get("elevation_deg"), dtype="float64")
        if el.ndim == 0:
            el = np.full(az.shape, el.item(), dtype="float64")
        z = np.asarray(sample.get("dbzh"), dtype="float32")
        if az.ndim != 1 or r.ndim != 1 or el.shape != az.shape or z.shape != (az.size, r.size):
            raise ValueError("background native coordinate/field shape mismatch")
        if not all(np.isfinite(x).all() for x in (az, r, el)) or np.any(np.diff(r) <= 0) or not len(r) or r[0] < 0 or np.any((az < 0) | (az >= 360)):
            raise ValueError("invalid background geometry")
        if "observed_mask" not in sample or "no_echo_mask" not in sample:
            raise ValueError("explicit observed_mask/no_echo_mask required; finite DBZH is not a denominator contract")
        obs = mask(sample["observed_mask"], z.shape, "observed_mask")
        noecho = mask(sample["no_echo_mask"], z.shape, "no_echo_mask")
        if np.any(noecho & ~obs) or np.any(obs & ~noecho & ~np.isfinite(z)):
            raise ValueError("observed/detected/no-echo states are inconsistent")
        detected = obs & ~noecho
        lo, hi = policy.valid_dbzh_range
        if np.any(detected & ((z < lo) | (z > hi))):
            raise ValueError("observed DBZH is outside the physical range")
        hit = detected & (z >= policy.echo_threshold_dbz)
        if name not in cuts:
            cuts[name] = {
                "azimuth": az.copy(), "range": r.copy(), "elevation": el.copy(),
                **{k: np.zeros(z.shape, "uint32") for k in ("observed_count", "echo_count", "no_echo_count", "day_count", "frequent_day_count")},
                "day_frequency_sum": np.zeros(z.shape, "float64"),
            }
            for field in ("dbzh", "vr", "sw"):
                cuts[name].update({field+"_count": np.zeros(z.shape, "uint32"), field+"_mean": np.zeros(z.shape, "float64"), field+"_m2": np.zeros(z.shape, "float64")})
        c = cuts[name]
        if any(not np.array_equal(c[k], x) for k, x in (("azimuth", az), ("range", r), ("elevation", el))):
            raise ValueError("background geometry must exactly match each native cut")
        if name not in daily:
            daily[name] = (np.zeros(z.shape, "uint32"), np.zeros(z.shape, "uint32"))
        daily[name][0][:] += obs
        daily[name][1][:] += hit
        c["observed_count"] += obs
        c["echo_count"] += hit
        c["no_echo_count"] += noecho
        for field in ("dbzh", "vr", "sw"):
            x = z if field == "dbzh" else np.asarray(sample.get(field, np.full(z.shape, np.nan)), dtype="float32")
            if x.shape != z.shape or np.isinf(x).any():
                raise ValueError(f"invalid historical {field}")
            ok = detected & np.isfinite(x)
            if field == "sw":
                ok &= x >= 0
            count, mean, m2 = c[field+"_count"], c[field+"_mean"], c[field+"_m2"]
            count[ok] += 1
            delta = x[ok] - mean[ok]
            mean[ok] += delta / count[ok]
            m2[ok] += delta * (x[ok] - mean[ok])
        inputs.append({"scan_id": scan_id, "sweep_name": name, "input_sha256": source_sha, "observed_at_utc": t.isoformat(), "clear_air_review_id": text(sample, "clear_air_review_id")})
    if identity is None:
        raise ValueError("background samples must not be empty")
    finish_day()
    arrays = {}
    for name, c in sorted(cuts.items()):
        count, days = c["observed_count"], c["day_count"]
        qualified = (count >= policy.minimum_observations) & (days >= policy.minimum_days)
        frequency = np.divide(c["day_frequency_sum"], days, out=np.full(days.shape, np.nan), where=days > 0)
        lower = _wilson(c["frequent_day_count"], days, policy.confidence_z)
        values = {k: c[k] for k in ("azimuth", "range", "elevation", "observed_count", "echo_count", "no_echo_count", "day_count", "frequent_day_count")}
        values.update(ground_clutter=np.where(qualified, frequency, np.nan).astype("float32"), frequency=frequency.astype("float32"), confidence_lower_bound=np.where(qualified, lower, np.nan).astype("float32"), qualified_mask=qualified.astype("uint8"))
        for field in ("dbzh", "vr", "sw"):
            n = c[field+"_count"]
            values[field+"_count"] = n
            values[field+"_mean"] = np.where(n > 0, c[field+"_mean"], np.nan).astype("float32")
            values[field+"_std"] = np.sqrt(np.divide(c[field+"_m2"], np.maximum(n.astype(float)-1., 1.), out=np.full(n.shape, np.nan), where=n > 1)).astype("float32")
        arrays.update({name+"__"+k: v for k, v in values.items()})
    meta = {
        "asset_version": ASSET_VERSION, **identity, "policy": policy.model_dump(), "inputs": inputs,
        "sweeps": sorted(cuts), "reviewed_clear_air": True,
        "denominator": "explicit_observed_including_valid_no_echo",
        "frequency_semantics": "mean_of_per_day_echo_fractions",
        "lower_bound_semantics": "wilson_daily_fraction_at_least_0.8; independent_days_assumption; not_class_probability",
        "statistics": "measured_detected_moments_only; no_missing_imputation",
        "seasonal_stratification": "not_implemented; build_separate_versioned_assets",
    }
    encoded = json.dumps(meta, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 16 * 1024 * 1024:
        raise ValueError("background metadata exceeds 16 MiB; split reviewed seasonal assets")
    arrays["__meta_json"] = np.frombuffer(encoded, dtype="uint8").copy()
    return arrays, {**meta, "asset_content_sha256": digest(arrays)}


def verify_background(arrays, root, expected_sha, expected_version, *, identity_binding=None):
    if digest(arrays) != expected_sha:
        raise ValueError("background content digest mismatch")
    meta = metadata(arrays)
    if expected_version != ASSET_VERSION:
        raise ValueError("configured background version differs")
    identity_binding = identity_binding or {}
    identity_verification = {}
    for key in IDENTITY_FIELDS:
        value = root.attrs.get(key)
        method = "native_metadata"
        if value is None and key in {"scan_strategy_id", "hardware_config_version"}:
            value = identity_binding.get(key)
            method = "explicit_registry_binding_to_radar_config_version"
        if str(value or "") != meta[key]:
            raise ValueError(f"background {key} differs from current radar volume/binding")
        identity_verification[key] = method
    current_id = str(root.attrs.get("scan_id", ""))
    current_time = timestamp(root.attrs["volume_end_time_utc"])
    if any(x["scan_id"] == current_id or timestamp(x["observed_at_utc"]) >= current_time for x in meta["inputs"]):
        raise ValueError("background contains the target scan or future samples")
    result = {}
    for number in root["sweep_number"][:]:
        name = f"sweep_{int(number):03d}"
        if name not in meta["sweeps"]:
            raise ValueError("background native cut unavailable")
        for key in ("azimuth", "range", "elevation"):
            if not np.array_equal(arrays[name+"__"+key], root[name][key][:]):
                raise ValueError("background/native geometry mismatch")
        p = arrays[name+"__ground_clutter"]
        shape = root[name]["DBZH"].shape
        if p.shape != shape or np.isinf(p).any() or np.any((p < 0) | (p > 1)):
            raise ValueError("invalid background frequency field")
        fields = {key.split("__", 1)[1]: value for key, value in arrays.items() if key.startswith(name+"__") and key.split("__", 1)[1] not in {"azimuth", "range", "elevation"}}
        for key, value in fields.items():
            if np.asarray(value).shape != shape:
                raise ValueError(f"background field geometry differs: {key}")
        validate_background_cut(fields, meta, name)
        result[name] = {"ground_clutter": p, "nonprecip_background": fields, "nonprecip_background_receipt": {"verified": True, "asset_version": ASSET_VERSION, "asset_content_sha256": expected_sha, "native_order": "original", "identity_verification": identity_verification, "identity": {k: meta[k] for k in IDENTITY_FIELDS}}}
    return result


def write_asset(path, arrays, *, asset_version=ASSET_VERSION):
    """Immutable atomic creation; never overwrite an accepted or historic asset."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".background-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez_compressed(f, **arrays)
            f.flush(); os.fsync(f.fileno())
        os.link(temp, path)  # atomic no-overwrite publication on the target filesystem
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"path": str(path), "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "asset_content_sha256": digest(arrays), "asset_version": asset_version}


def validate_background_cut(fields, meta, name):
    """Reject a well-hashed but internally inconsistent asset, not just corruption."""
    policy = BackgroundPolicy.model_validate(meta["policy"])
    if meta.get("reviewed_clear_air") is not True or meta.get("denominator") != "explicit_observed_including_valid_no_echo":
        raise ValueError("background review/denominator metadata differs")
    count_names = ("observed_count", "echo_count", "no_echo_count", "day_count", "frequent_day_count", "dbzh_count", "vr_count", "sw_count")
    for key in count_names:
        if key not in fields or np.asarray(fields[key]).dtype != np.dtype("uint32"):
            raise ValueError(f"invalid background count field {key}")
    n, hits, noecho, days, frequent = (fields[k].astype("uint64") for k in count_names[:5])
    if np.any(hits+noecho > n) or np.any(days > n) or np.any(frequent > days):
        raise ValueError("background count identities are inconsistent")
    if not np.array_equal(fields["dbzh_count"], n-noecho):
        raise ValueError("background detected/no-echo denominator differs")
    inputs = [x for x in meta["inputs"] if x["sweep_name"] == name]
    actual_days = len({timestamp(x["observed_at_utc"]).date() for x in inputs})
    if np.any(n > len(inputs)) or np.any(days > actual_days):
        raise ValueError("background support exceeds recorded input provenance")
    q = mask(fields.get("qualified_mask"), n.shape, "background_qualified")
    expected_q = (n >= policy.minimum_observations) & (days >= policy.minimum_days)
    if not np.array_equal(q, expected_q):
        raise ValueError("background qualification does not match per-gate days/counts")
    for key in ("frequency", "ground_clutter", "confidence_lower_bound"):
        x = np.asarray(fields.get(key))
        if x.shape != n.shape or np.isinf(x).any() or np.any((x < 0) | (x > 1)):
            raise ValueError(f"invalid background probability-like statistic {key}")
    freq = fields["frequency"]
    if not np.array_equal(np.isfinite(freq), days > 0):
        raise ValueError("background frequency availability differs from daily support")
    expected_lower = np.where(q, _wilson(frequent, days, policy.confidence_z), np.nan)
    if not np.allclose(fields["ground_clutter"], np.where(q, freq, np.nan), equal_nan=True, atol=1e-7):
        raise ValueError("background prior differs from qualified daily frequency")
    if not np.allclose(fields["confidence_lower_bound"], expected_lower, equal_nan=True, atol=1e-6):
        raise ValueError("background confidence bound does not match daily support")
    for field in ("dbzh", "vr", "sw"):
        count = fields[field+"_count"]
        mean, std = fields.get(field+"_mean"), fields.get(field+"_std")
        if mean is None or std is None or np.isinf(mean).any() or np.isinf(std).any():
            raise ValueError("missing/nonfinite historical distribution")
        if np.any(count > fields["dbzh_count"]) or not np.array_equal(np.isfinite(mean), count > 0) or not np.array_equal(np.isfinite(std), count > 1) or np.any(std < 0):
            raise ValueError("historical distribution support differs")
