"""Emitter1 on actual adjacent-ray triples; missing samples are never encoded.

Splitting at missing support can shorten a detected run, so scores are local
segment evidence, not full-ray equivalence. Emitter2 is deliberately absent.
No QC actions are produced. The original vendor C sources remain unchanged.
"""

import math
from pathlib import Path
import subprocess
import tempfile
import numpy as np
from .io import file_hash
from .native import NativeResult, spans


def run_sparse_emitter(
    native,
    binary,
    expected_sha256,
    *,
    minimum_length_m=4000.0,
    minimum_contrast_db=8.0,
    maximum_calls=2000,
    timeout_seconds=20.0,
):
    if not np.isfinite(minimum_length_m) or minimum_length_m <= 0:
        raise ValueError("invalid minimum length")
    if not np.isfinite(minimum_contrast_db) or not 0.5 <= minimum_contrast_db <= 127:
        raise ValueError("invalid contrast")
    if not isinstance(maximum_calls, int) or maximum_calls < 1:
        raise ValueError("invalid call budget")
    if not np.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("invalid timeout")
    if file_hash(binary) != expected_sha256:
        raise ValueError("native binary checksum mismatch")
    length = int(math.ceil(minimum_length_m / native.gate_spacing_m))
    if not 2 <= length <= 254:
        raise ValueError("native length outside byte-core range")
    threshold = int(math.ceil(minimum_contrast_db / 0.5))
    z = native.fields["DBZH"]
    nr, ng = z.shape
    measured = native.field_available["DBZH"] & np.isfinite(z) & (z >= -32) & (z <= 94.5)
    score = np.full(z.shape, np.nan, "float32")
    summary = {
        "method": "original_emitter1_observed_triples_v1",
        "calls": 0,
        "planned_calls": 0,
        "failures": [],
        "budget_exhausted": False,
        "operational_eligible": False,
        "minimum_length_m": minimum_length_m,
        "minimum_contrast_db": minimum_contrast_db,
        "binary_sha256": expected_sha256,
        "interpolated": False,
        "missing_filled": False,
        "semantics": "segment_evidence_not_full_ray_score_or_truth",
    }
    plans = []
    for ray in range(nr):
        if not native.full_ppi and ray in (0, nr - 1):
            continue
        rows = np.array([(ray - 1) % nr, ray, (ray + 1) % nr])
        if len(set(rows)) < 3 or not native.geometry_good[rows].all():
            continue
        if native.gap_after[rows[0]] or native.gap_after[ray]:
            continue
        for lo, hi in spans(measured[rows].all(axis=0)):
            if hi - lo >= length:
                plans.append((ray, rows, lo, hi))
    summary["planned_calls"] = len(plans)
    with tempfile.TemporaryDirectory(prefix="emitter1-triples-") as folder:
        folder = Path(folder)
        for ray, rows, lo, hi in plans:
            if summary["calls"] >= maximum_calls:
                summary["budget_exhausted"] = True
                break
            encoded = np.rint((z[rows, lo:hi].astype("float64") + 32.5) / 0.5).astype("uint8")
            src = folder / "input.raw"
            dst = folder / "output.raw"
            src.write_bytes(encoded.tobytes())
            dst.unlink(missing_ok=True)
            summary["calls"] += 1
            try:
                run = subprocess.run(
                    [
                        str(Path(binary).resolve()),
                        str(src),
                        str(dst),
                        "3",
                        str(hi - lo),
                        "1",
                        str(threshold),
                        str(length),
                        "1",
                    ],
                    capture_output=True,
                    timeout=timeout_seconds,
                )
                if run.returncode:
                    raise RuntimeError(f"native returncode {run.returncode}")
                data = dst.read_bytes()
                if len(data) != encoded.size:
                    raise RuntimeError("native size mismatch")
                score[ray, lo:hi] = np.frombuffer(data, "uint8").reshape(3, hi - lo)[1] / 255.0
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                summary["failures"].append(
                    {"ray": int(ray), "start_gate": int(lo), "error": type(error).__name__}
                )
    summary["evaluated_gates"] = int(np.isfinite(score).sum())
    summary["positive_gates"] = int((score > 0).sum())
    summary["status"] = "executed" if np.isfinite(score).any() else "no_usable_support"
    return NativeResult({"1": score, "2": np.full(z.shape, np.nan, "float32")}, summary)
