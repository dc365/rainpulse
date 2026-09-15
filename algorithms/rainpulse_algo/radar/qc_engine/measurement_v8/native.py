"""Original bRopo C core on fully OBSERVED support only.

Full valid PPI: periodic padded full-sweep reference. With missing data: complete
observed rectangular tiles, explicitly different from a full-sweep detector.
Emitter2 uses a row statistic, so no claim of full-sweep equivalence for tiles.
Unavailable / failure / budget-exhausted outputs are NaN, never an all-zero result.
"""

import math
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .io import digest, file_hash


@dataclass
class NativeResult:
    scores: dict
    summary: dict


def spans(mask):
    edge = np.diff(np.r_[False, mask, False].astype("int8"))
    return zip(np.flatnonzero(edge == 1), np.flatnonzero(edge == -1), strict=True)


def run_native(native, cfg, binary=None, expected_sha256=None):
    started = time.perf_counter()
    scores = {str(k): np.full(native.shape, np.nan, "float32") for k in (1, 2)}
    summary = {
        "kind": "original_bropo_C_core",
        "score_semantics": "uncalibrated_byte_score/255",
        "config": cfg.model_dump(mode="json"),
        "calls": 0,
        "tiles": [],
        "failures": [],
        "binary_sha256": None,
        "status": "disabled",
    }
    if not cfg.enabled:
        return NativeResult(scores, summary)
    if binary is None or not expected_sha256:
        if cfg.required:
            raise ValueError("native binary and expected SHA256 are required")
        summary["status"] = "unavailable_binary"
        return NativeResult(scores, summary)
    actual = file_hash(binary)
    if actual != expected_sha256:
        raise ValueError("native binary checksum mismatch")
    summary["binary_sha256"] = actual
    z = native.fields["DBZH"]
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    representable = observed & (z >= -32) & (z <= 94.5)
    dr = native.gate_spacing_m
    length = int(math.ceil(cfg.minimum_length_m / dr))
    if not 2 <= length <= 254:
        raise ValueError("native horizontal length outside byte-core range [2,254]")
    spacing = native.audit["azimuth_spacing_deg"]
    step = (np.roll(native.azimuth, -1) - native.azimuth) % 360
    usable_edges = ~native.gap_after
    if not np.allclose(step[usable_edges], spacing, atol=0.05, rtol=0):
        if cfg.required:
            raise ValueError("native core requires uniform supported angular sampling")
        summary["status"] = "unsupported_angular_geometry"
        return NativeResult(scores, summary)
    nr, ng = native.shape
    halo = 2 * cfg.width_rays + 4
    gate_halo = max(length * 2, 4)
    threshold = int(round((cfg.minimum_dbz + 32.5) / 0.5))
    plans = []
    if native.full_ppi and representable.all():
        rows = np.arange(-halo, nr + halo) % nr
        plans.append((rows, np.arange(nr), halo, 0, ng, "full_ppi"))
    else:
        for start in range(0, nr, cfg.tile_rays):
            end = min(start + cfg.tile_rays, nr)
            if not native.full_ppi and (start < halo or end + halo > nr):
                continue
            rows = np.arange(start - halo, end + halo) % nr
            if np.any(native.gap_after[rows[:-1]]) or not native.geometry_good[rows].all():
                continue
            all_measured = np.all(representable[rows], axis=0)
            for lo, hi in spans(all_measured):
                if hi - lo >= max(cfg.minimum_tile_gates, 2 * gate_halo + 1):
                    plans.append(
                        (rows, np.arange(start, end), halo, int(lo), int(hi), "observed_tile")
                    )
    summary["planned_tiles"] = len(plans)
    with tempfile.TemporaryDirectory(prefix="rainpulse-native-") as tmp:
        tmp = Path(tmp)
        for rows, centers, trim, lo, hi, mode in plans:
            if summary["calls"] + 2 > cfg.maximum_calls:
                summary["budget_exhausted"] = True
                break
            values = z[rows, lo:hi]
            if not representable[rows, lo:hi].all():
                raise AssertionError("native plan attempted to fabricate an observation")
            encoded = np.rint((values.astype("float64") + 32.5) / 0.5).astype("uint8")
            encoded.tofile(tmp / "input.raw")
            for det in (1, 2):
                summary["calls"] += 1
                out = tmp / f"output-{det}.raw"
                out.unlink(missing_ok=True)
                command = [
                    str(Path(binary).resolve()),
                    str(tmp / "input.raw"),
                    str(out),
                    str(len(rows)),
                    str(hi - lo),
                    str(det),
                    str(threshold),
                    str(length),
                    str(cfg.width_rays),
                ]
                try:
                    process = subprocess.run(
                        command, capture_output=True, timeout=cfg.timeout_seconds, check=False
                    )
                    if process.returncode or not out.exists():
                        raise RuntimeError(f"native returncode={process.returncode}")
                    raw = out.read_bytes()
                    if len(raw) != encoded.size:
                        raise RuntimeError("native output size mismatch")
                    output = np.frombuffer(raw, dtype="uint8").reshape(encoded.shape)
                    a, b = lo + gate_halo, hi - gate_halo
                    if a >= b:
                        continue
                    scores[str(det)][centers[:, None], np.arange(a, b)] = (
                        output[trim : trim + len(centers), gate_halo:-gate_halo].astype("float32")
                        / 255
                    )
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                    summary["failures"].append(
                        {"detector": det, "error": type(error).__name__ + ": " + str(error)}
                    )
                    if cfg.required:
                        raise RuntimeError("required native detector failed") from error
            summary["tiles"].append(
                {
                    "mode": mode,
                    "ray_start": int(centers[0]),
                    "ray_end_exclusive": int(centers[-1] + 1),
                    "gate_start": lo,
                    "gate_end_exclusive": hi,
                    "range_halo_gates": gate_halo,
                }
            )
    available = np.isfinite(scores["1"]) & np.isfinite(scores["2"])
    summary.update(
        status="executed" if available.any() else "no_usable_native_support",
        available_gates=int(available.sum()),
        observed_gates=int(observed.sum()),
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )
    summary["semantic_identity"] = digest(
        {
            "config": cfg.model_dump(mode="json"),
            "binary_sha256": actual,
            "support_policy": "observed_tiles_v1_no_fill",
        }
    )
    return NativeResult(scores, summary)
