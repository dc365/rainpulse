#!/usr/bin/env python3
"""Read-only whole original boundary replay on frozen Web-bound snapshots."""

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("original-boundary", "anchored-shape", "original-fan"),
                        default="original-boundary")
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--published", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.method == "original-fan":
        from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import (
            original_fan_shape,
        )
        module = original_fan_shape
    elif args.method == "anchored-shape":
        from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import (
            anchored_radial_shape,
        )
        module = anchored_radial_shape
    else:
        from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import (
            original_boundary,
        )
        module = original_boundary
    module_sha = digest(Path(module.__file__))
    script_sha = digest(Path(__file__))
    report_sha = digest(args.source_report)
    source = json.loads(args.source_report.read_text())
    if (source["scope"] != "complete_original_source_replay_not_published_QC"
            or source["action_authority"] or source["product_writes"]):
        raise ValueError("frozen research input report required")
    receipts = {}
    for path in args.published:
        receipt = json.loads(path.read_text())
        if receipt["scope"] != "exact_Web_consumed_stored_QC_not_replay":
            raise ValueError("actual published receipt required")
        identity = receipt["snapshot_sha256"]
        if identity in receipts:
            raise ValueError("duplicate published receipt")
        receipts[identity] = (path, receipt)
    args.output.mkdir(exist_ok=False)
    results = []
    for item in source["results"]:
        path = Path(item["snapshot"])
        if digest(path) != item["snapshot_sha256"]:
            raise ValueError("snapshot bytes changed")
        with np.load(path, allow_pickle=False) as data:
            a = {key: data[key] for key in data.files}
        meta = json.loads(str(a["METADATA"]))
        if meta["scan_id"] != meta["web_frame_identity"]["web_scan_id"]:
            raise ValueError("exact Web-selected scan required")
        native = SimpleNamespace(
            shape=a["RAW"].shape, ranges=a["RANGE"], azimuth=a["AZIMUTH"],
            geometry_good=a["GEOMETRY_GOOD"], gap_after=a["GAP_AFTER"],
            fields={"DBZH": a["RAW"], **{k[7:]: v for k, v in a.items()
                                        if k.startswith("MOMENT_")}},
            field_available={k[10:]: v for k, v in a.items() if k.startswith("AVAILABLE_")},
        )
        raw = a["RAW"].copy()
        blocked = (a["WEATHER"] == 1) | (a["CONFLICTS"] == 1) | (a["RV2_BARRED_MASK"] == 1)
        group = {k: a[k] for k in ("RV2_RAW_FAN_ID", "RV2_SOURCE_LEDGER_SEED_ID")}
        arrays, report = module.qualify(native, blocked, group)
        module.validate(arrays, native, blocked, group)
        selected = arrays[module.PREFIX+"QUALIFIED_MASK"] == 1
        if ((selected & blocked).any() or (selected & ~native.field_available["DBZH"]).any()
                or not np.array_equal(raw, a["RAW"], equal_nan=True)):
            raise ValueError("observation, protection or RAW invariant failed")
        report.update(snapshot=str(path), snapshot_sha256=digest(path),
                      radar_id=meta["radar_id"], local_time=meta["local_time"],
                      raw_unchanged=True, protection_overlap=0)
        identity = item["snapshot_sha256"]
        if identity in receipts:
            receipt_path, receipt = receipts[identity]
            if (digest(receipt_path) != item["published_receipt_sha256"] or
                    receipt["web_frame_identity"]["web_scan_id"] != meta["scan_id"]):
                raise ValueError("published receipt bytes or identity changed")
            seen = np.zeros(native.shape, bool)
            visible = seen.copy()
            for target in receipt["target_records"]:
                i, j = target["row"], target["column"]
                if (seen[i, j] or not np.isclose(raw[i, j], target["raw_dbzh"], atol=.0001, rtol=0)
                        or not np.isclose(a["RANGE"][j], target["range_m"], atol=.001, rtol=0)
                        or not np.isclose(a["AZIMUTH"][i], target["azimuth_deg"], atol=.0001, rtol=0)):
                    raise ValueError("unbound published gate")
                seen[i, j] = True
                visible[i, j] = target["renderer_visible"]
            if (seen.sum() != receipt["target_gates"] or
                    visible.sum() != receipt["renderer_eligible_visible_gates"]):
                raise ValueError("published gate counts differ")
            report.update(published_visible=int(visible.sum()),
                          qualified_published_overlap=int((selected & visible).sum()),
                          published_receipt_sha256=digest(receipt_path))
        diagnostic = args.output/(path.stem+".npz")
        if diagnostic.exists():
            raise ValueError("duplicate diagnostic name")
        np.savez_compressed(diagnostic, **arrays)
        report.update(diagnostics=str(diagnostic), diagnostics_sha256=digest(diagnostic))
        results.append(report)
        print(json.dumps({k: v for k, v in report.items() if k not in ("records",)}), flush=True)
    if (module_sha != digest(Path(module.__file__)) or script_sha != digest(Path(__file__))
            or report_sha != digest(args.source_report)):
        raise ValueError("research code or input report changed during replay")
    (args.output/"report.json").write_text(json.dumps(dict(
        scope="original_RAW_boundary_research_not_published_QC", action_authority=False,
        method=args.method,
        product_writes=False, module_sha256=module_sha,
        script_sha256=script_sha, source_report_sha256=report_sha,
        results=results,
    ), indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
