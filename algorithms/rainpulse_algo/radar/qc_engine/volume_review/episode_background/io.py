"""Deterministic, checksum-bound raw snapshots and immutable background assets."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import os
import tempfile
import zipfile
import numpy as np
from ..receipts import npz_bytes, load_npz
from . import VERSION
from .builder import Background
from .config import BuildConfig
from .data import Sample, MOMENTS, sha, json_bytes, is_hash, utc


def atomic_file(path: str | Path, data: bytes) -> None:
    """Publish once, without replacing existing artifacts (also race-safe)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".episode-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.link(name, path)  # fails instead of overwriting an existing destination
    finally:
        Path(name).unlink(missing_ok=True)


def _bounded_npz(data, limit=256 * 1024**2):
    with zipfile.ZipFile(BytesIO(data)) as z:
        names = [x.filename for x in z.infolist()]
        if len(set(names)) != len(names) or len(names) > 2000 or sum(x.file_size for x in z.infolist()) > limit:
            raise ValueError("NPZ resource/duplicate-entry limit")
        if any('/' in n or '\\' in n or not n.endswith('.npy') for n in names):
            raise ValueError("unsafe NPZ entries")
    return load_npz(data)


def sample_bytes(s: Sample) -> bytes:
    meta = {k: getattr(s, k) for k in ("radar_id", "scan_id", "sweep_id", "processing_id", "observed_at")}
    meta.update(schema="rainpulse.raw-episode-sweep-v1", upstream_source_sha256=s.source_sha256)
    arrays = {"metadata_utf8": np.frombuffer(json_bytes(meta), dtype="uint8"),
              "azimuth": np.asarray(s.azimuth), "elevation": np.asarray(s.elevation),
              "range": np.asarray(s.ranges), "geometry_good": np.asarray(s.geometry_good, "uint8"),
              **{k: np.asarray(v) for k, v in s.fields.items()},
              **{"AVAILABLE_" + k: np.asarray(v, "uint8") for k, v in s.available.items()}}
    if s.acquired is not None:
        arrays.update(ACQUIRED_MASK=np.asarray(s.acquired, "uint8"), NO_ECHO_MASK=np.asarray(s.no_echo, "uint8"))
    return npz_bytes(arrays)


def read_sample(path, expected_sha256) -> Sample:
    data = Path(path).read_bytes()
    if not is_hash(expected_sha256) or sha(data) != expected_sha256:
        raise ValueError("raw snapshot content hash differs")
    a = _bounded_npz(data)
    if "metadata_utf8" not in a or a["metadata_utf8"].dtype != np.uint8 or a["metadata_utf8"].ndim != 1:
        raise ValueError("raw snapshot metadata missing")
    meta = json.loads(a.pop("metadata_utf8").tobytes())
    if meta.get("schema") != "rainpulse.raw-episode-sweep-v1":
        raise ValueError("wrong raw snapshot schema")
    allowed = {"azimuth", "elevation", "range", "geometry_good", "ACQUIRED_MASK", "NO_ECHO_MASK"}
    allowed |= set(MOMENTS) | {"AVAILABLE_" + k for k in MOMENTS}
    if set(a) - allowed:
        raise ValueError("QC/unknown fields forbidden in raw learning input")
    fields = {k: a[k] for k in MOMENTS if k in a}
    available = {k: a["AVAILABLE_" + k] for k in MOMENTS if "AVAILABLE_" + k in a}
    return Sample(**{k: meta[k] for k in ("radar_id", "scan_id", "sweep_id", "processing_id", "observed_at")},
                  source_sha256=expected_sha256, azimuth=a["azimuth"], elevation=a["elevation"], ranges=a["range"],
                  geometry_good=a["geometry_good"], fields=fields, available=available,
                  acquired=a.get("ACQUIRED_MASK"), no_echo=a.get("NO_ECHO_MASK"))


def read_manifest(path):
    path = Path(path)
    manifest = json.loads(path.read_text())
    if manifest.get("schema") != "rainpulse.raw-episode-v1" or not manifest.get("samples"):
        raise ValueError("raw episode manifest missing")
    if len(manifest["samples"]) > 4096:
        raise ValueError("raw episode manifest resource limit")
    samples = []
    seen = set()
    for ref in manifest["samples"]:
        rel = Path(ref["path"])
        if rel.is_absolute() or ".." in rel.parts or rel in seen:
            raise ValueError("unsafe or duplicate raw snapshot reference")
        seen.add(rel)
        resolved = (path.parent / rel).resolve()
        if not resolved.is_relative_to(path.parent.resolve()):
            raise ValueError("snapshot escapes manifest directory")
        samples.append(read_sample(resolved, ref["sha256"]))
    return manifest, samples


def background_bytes(model: Background) -> bytes:
    data = npz_bytes(model.arrays)
    meta = {**model.metadata, "arrays_sha256": sha(data)}
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as z:
        for name, content in (("metadata.json", json_bytes(meta)), ("arrays.npz", data)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            z.writestr(info, content)
    return out.getvalue()


def load_background(path, expected_sha256, *, maximum_bytes=512 * 1024**2) -> Background:
    path = Path(path)
    if path.stat().st_size > maximum_bytes:
        raise ValueError("background asset resource limit")
    data = path.read_bytes()
    if not is_hash(expected_sha256) or sha(data) != expected_sha256:
        raise ValueError("background asset hash mismatch")
    with zipfile.ZipFile(BytesIO(data)) as z:
        if sorted(z.namelist()) != ["arrays.npz", "metadata.json"] or sum(i.file_size for i in z.infolist()) > maximum_bytes:
            raise ValueError("unexpected background ZIP entries or size")
        meta = json.loads(z.read("metadata.json"))
        payload = z.read("arrays.npz")
    if meta.get("schema") != VERSION or meta.get("grade") != "short_episode" or meta.get("reviewed_no_precipitation") is not True:
        raise ValueError("not a reviewed short-episode background")
    if meta.get("arrays_sha256") != sha(payload) or not is_hash(meta.get("review_receipt")):
        raise ValueError("invalid background internal provenance")
    cfg = BuildConfig.model_validate(meta["build_config"])
    if cfg.digest != meta.get("build_config_sha256"):
        raise ValueError("background configuration digest differs")
    if utc(meta["start_time"]) > utc(meta["end_time"]):
        raise ValueError("background time interval inverted")
    if not isinstance(meta.get("radar_id"), str) or not meta["radar_id"] or not isinstance(meta.get("processing_id"), str) or not meta["processing_id"]:
        raise ValueError("background identity missing")
    if not isinstance(meta.get("sweeps"), list) or not meta["sweeps"]:
        raise ValueError("background sweep metadata absent")
    a = _bounded_npz(payload, maximum_bytes)
    seen = set()
    for rec in meta["sweeps"]:
        if rec["sweep_id"] in seen:
            raise ValueError("duplicate background sweep")
        seen.add(rec["sweep_id"])
        prefix = rec["prefix"]; shape = tuple(rec["shape"])
        if len(shape) != 2 or np.prod(shape) > cfg.maximum_sweep_cells:
            raise ValueError("invalid background shape")
        required = {"stable_measured_core", "n_slots", "measured_support_fraction", "occurrence_available",
                    "echo_occurrence_fraction", "zdr_state_n", "zdr_tail_positive_fraction", "zdr_tail_negative_fraction", "PHIDP_resultant"}
        for field in MOMENTS:
            required |= {field + suffix for suffix in ("_n", "_median", "_mad", "_q10", "_q90")}
        for key in required:
            if prefix + key not in a or a[prefix + key].shape != shape:
                raise ValueError("background statistic missing or malformed: " + key)
        if (a[prefix + "azimuth"].shape != (shape[0],) or a[prefix + "elevation"].shape != (shape[0],) or
                a[prefix + "range"].shape != (shape[1],)):
            raise ValueError("background geometry malformed")
        az, el, rg = (a[prefix + k] for k in ("azimuth", "elevation", "range"))
        if not (np.isfinite(az).all() and np.isfinite(el).all() and np.isfinite(rg).all()) or np.any(np.diff(rg) <= 0) or np.any(rg < 0):
            raise ValueError("background coordinates invalid")
        if a.get(prefix + "geometry_good", np.empty(0)).shape != (shape[0],) or not np.isin(a[prefix + "geometry_good"], (0, 1)).all():
            raise ValueError("background geometry validity invalid")
        for key in ("source_sha256", "source_raw_digests"):
            if len(rec.get(key, [])) != rec["samples"] or not all(is_hash(v) for v in rec[key]):
                raise ValueError("background source content identities malformed")
        if len(rec.get("source_times", [])) != rec["samples"] or any(not utc(meta["start_time"]) <= utc(t) <= utc(meta["end_time"]) for t in rec["source_times"]):
            raise ValueError("background source time provenance malformed")
        if not np.isin(a[prefix + "stable_measured_core"], (0, 1)).all():
            raise ValueError("background stable mask invalid")
        if len(rec["source_scan_ids"]) != rec["samples"] or len(set(rec["source_scan_ids"])) != rec["samples"]:
            raise ValueError("background source identities malformed")
    for value in a.values(): value.flags.writeable = False
    return Background(meta, a)
