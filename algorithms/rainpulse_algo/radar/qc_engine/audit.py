"""Read-only residual audit on a fixed ORIGINAL-observation domain, without truth claims.

Use on qc.zarr from a deployed job or the frozen offline replay. Reports retain
artifact/profile identity. Optional ROI is a user-supplied native boolean mask,
never a candidate-selected denominator. No files are overwritten or published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from ..qc_input import open_qc_input
from ..qc_zarr import validate_qc_zarr_store
from .objects import _runs
from .refinement import V3Blocker, V3Path
from .review import _safe_json, load_local_artifact


def audit_sweep(dbzh, observed, arrays, ranges, *, threshold=5.0, roi=None, maximum_rays=24):
    if dbzh.shape != observed.shape or dbzh.shape[1] != len(ranges):
        raise ValueError("audit geometry mismatch")
    domain = observed & np.isfinite(dbzh) & (dbzh >= threshold)
    if roi is not None:
        if roi.shape != domain.shape or np.any(~np.isin(roi, [0, 1])):
            raise ValueError("audit ROI must be a native binary mask")
        domain &= roi.astype(bool)
    action = arrays["QC_ACTION"]
    eligible = arrays["QPE_ELIGIBLE_MASK"] == 1
    quarantine = arrays.get("RFI_QUARANTINE_MASK", np.zeros(domain.shape)) == 1
    retained = domain & eligible
    blockers = arrays.get("RFI_V3_BLOCKER_BITS")
    path = arrays.get("RFI_V3_DECISION_PATH")
    rfi_state = arrays.get("RFI_RISK_STATE")
    rays = []
    dr = float(np.median(np.diff(ranges)))
    for ray in np.flatnonzero(retained.any(axis=1)):
        runs = _runs(retained[ray])
        a, b = max(runs, key=lambda pair: pair[1] - pair[0])
        gates = np.flatnonzero(retained[ray])
        rays.append(
            {
                "original_ray_index": int(ray),
                "retained_echo_gates": int(len(gates)),
                "longest_continuous_m": (b - a) * dr,
                "retained_span_m": float(ranges[gates[-1]] - ranges[gates[0]] + dr),
                "longest_run_gate_start": int(a),
                "longest_run_gate_end_exclusive": int(b),
                "blockers_on_retained_echo": {}
                if blockers is None
                else {
                    bit.name: int(((blockers[ray, gates] & int(bit)) != 0).sum())
                    for bit in V3Blocker
                },
            }
        )
    rays.sort(
        key=lambda r: (
            -r["longest_continuous_m"],
            -r["retained_echo_gates"],
            r["original_ray_index"],
        )
    )
    return {
        "domain": "original_observed_dbzh_at_or_above_threshold_intersect_optional_frozen_roi",
        "echo_threshold_dbz": threshold,
        "original_domain_gates": int(domain.sum()),
        "confirmed_rejected_gates": int((domain & (action == 2)).sum()),
        "confirmed_rfi_gates": None
        if rfi_state is None
        else int((domain & (rfi_state == 3)).sum()),
        "other_qc_rejected_gates": None
        if rfi_state is None
        else int((domain & (action == 2) & (rfi_state != 3)).sum()),
        "quarantined_not_rejected_gates": int((domain & quarantine).sum()),
        "quantitative_withheld_gates": int((domain & ~eligible).sum()),
        "quantitative_retained_gates": int(retained.sum()),
        "blockers_overlap_not_exclusive": True,
        "blockers_on_retained_echo": {}
        if blockers is None
        else {bit.name: int((retained & ((blockers & int(bit)) != 0)).sum()) for bit in V3Blocker},
        "decision_path_counts": {}
        if path is None
        else {code.name: int((domain & (path == code)).sum()) for code in V3Path},
        "top_retained_echo_rays": rays[:maximum_rays],
        "retained_echo_ray_count": len(rays),
        "interpretation": (
            "Retained echoes include real weather; this is NOT a residual RFI truth mask "
            "or skill score."
        ),
    }


def audit_artifact(path, expected_sha256, output, *, roi_path=None, roi_sha256=None):
    source_path = Path(path).resolve()
    output_path = Path(output).resolve()
    if output_path.is_relative_to(source_path):
        raise ValueError("audit output cannot modify the source artifact directory")
    if (roi_path is None) != (roi_sha256 is None):
        raise ValueError("ROI file and hash must be provided together")
    objects = load_local_artifact(source_path, expected_sha256)
    validate_qc_zarr_store(objects)
    root = open_qc_input(objects).root
    roi = {}
    if roi_path is not None:
        file = Path(roi_path)
        if hashlib.sha256(file.read_bytes()).hexdigest() != roi_sha256:
            raise ValueError("ROI hash differs from requested frozen selection")
        with np.load(file, allow_pickle=False) as archive:
            roi = {key: archive[key] for key in archive.files}
        expected = {f"sweep_{int(n):03d}" for n in root["sweep_number"][:]}
        if set(roi) != expected:
            raise ValueError(
                "ROI must explicitly cover every native cut; use an all-false mask to omit one"
            )
    report = {
        "schema_version": "rainpulse.qc-residual-audit.v1",
        "qc_artifact_sha256": expected_sha256,
        "published": False,
        "roi_sha256": roi_sha256,
        "identity": {
            key: root.attrs.get(key)
            for key in (
                "scan_id",
                "radar_id",
                "job_id",
                "qc_profile",
                "qc_pipeline_version",
                "decision_version",
                "qc_parameters_sha256",
                "qc_libraries",
                "context_fingerprint",
            )
        },
        "sweeps": {},
        "acceptance": "diagnostic_only_no_labels_no_skill_claim",
    }
    for n in root["sweep_number"][:]:
        name = f"sweep_{int(n):03d}"
        group = root[name]
        arrays = {
            key: group[key][:]
            for key in (
                "QC_ACTION",
                "QPE_ELIGIBLE_MASK",
                "RFI_QUARANTINE_MASK",
                "RFI_RISK_STATE",
                "RFI_V3_BLOCKER_BITS",
                "RFI_V3_DECISION_PATH",
            )
            if key in group
        }
        result = audit_sweep(
            group["DBZH_RAW"][:],
            group["VALID_MASK"][:] == 1,
            arrays,
            group["range"][:],
            roi=roi.get(name),
        )
        result["azimuth_deg"] = group["azimuth"][:].tolist()
        report["sweeps"][name] = result
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="qc-audit-", dir=output.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(_safe_json(report), stream, ensure_ascii=False, allow_nan=False, indent=2)
        # link is an atomic create-if-absent; never clobber an earlier evidence report.
        os.link(tmp, output)
    finally:
        os.unlink(tmp)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qc-zarr", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--roi", type=Path)
    parser.add_argument("--roi-sha256")
    args = parser.parse_args()
    if bool(args.roi) != bool(args.roi_sha256):
        parser.error("ROI file and hash must be supplied together")
    audit_artifact(
        args.qc_zarr, args.sha256, args.output, roi_path=args.roi, roi_sha256=args.roi_sha256
    )
    print(json.dumps({"output": str(args.output), "published": False}))


if __name__ == "__main__":
    main()
