#!/usr/bin/env python3
"""Join native receipts to the frozen corpus; never promote mechanical PASS."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path


def summarize(inventory, receipts, configurations, *, day="2026-08-28"):
    if not inventory:
        raise ValueError("empty raw acceptance corpus")
    expected = {r["sha256"]: r for r in inventory}
    if len(expected) != len(inventory):
        raise ValueError("duplicate raw identity needs explicit file disambiguation")
    rows = defaultdict(list)
    errors = []
    for shard, r in receipts:
        sha = r["raw_sha256"]
        if sha not in expected or r["radar_id"] != expected[sha]["radar_id"]:
            errors.append({"reason": "UNFROZEN_SOURCE", "sha256": sha})
            continue
        rows[sha].append(r)
    matrix = []
    buckets = defaultdict(set)
    exclusion_counts = Counter()
    for sha, source in expected.items():
        evidence = rows.get(sha, [])
        decoded = [r for r in evidence if r["state"] == "RAW_DECODE_VERIFIED"]
        excluded = [r for r in evidence if r["state"] == "EXCLUDED"]
        complete = sum(r["state"] == "RAW_FILE_COMPLETE" for r in evidence)
        failures = [f for r in evidence for f in r.get("failures", [])]
        if excluded:
            if source["radar_id"] not in {"zf703", "zf801"} or decoded:
                failures.append("UNAUTHORIZED_OR_CONFLICTING_EXCLUSION")
            else:
                exclusion_counts[source["radar_id"]] += 1
        elif len(decoded) != 1:
            failures.append("NO_UNIQUE_NATIVE_DECODE")
        else:
            d = decoded[0]
            if configurations.get(source["radar_id"]) != d["decoder_config_sha256"]:
                failures.append("DECODER_CONFIG_DRIFT")
            cuts = [r for r in evidence if "sweep" in r]
            actual = [r["sweep"] for r in cuts]
            if not d["ref_sweep_numbers"] or len(d["ref_sweep_numbers"]) != d.get("decoded_ref_sweeps", len(d["ref_sweep_numbers"])):
                failures.append("NORMALIZED_REF_CUT_COVERAGE_INCOMPLETE")
            if len(actual) != len(set(actual)) or set(actual) != set(d["ref_sweep_numbers"]):
                failures.append("REF_CUT_COVERAGE_INCOMPLETE")
            if any(r["state"] != "RAW_MECHANICAL_GATES_PASSED" for r in cuts):
                failures.append("CUT_NOT_MECHANICALLY_ACCEPTED")
            start = datetime.fromisoformat(d["observed_start_utc"])
            end = datetime.fromisoformat(d["observed_end_utc"])
            if start.tzinfo is None or end.tzinfo is None or end < start:
                failures.append("INVALID_NATIVE_TIME")
            else:
                for label, offset in (("UTC", 0), ("UTC+08", 8)):
                    local = start.astimezone(timezone(timedelta(hours=offset)))
                    buckets[(source["radar_id"], label, local.date().isoformat())].add(
                        (local.hour * 60 + local.minute) // 6)
        if complete != 1:
            failures.append("NO_UNIQUE_FILE_COMPLETION")
        pending = not complete and not any(r["state"] == "FAIL" for r in evidence)
        matrix.append({"sha256": sha, "radar_id": source["radar_id"],
                       "relative_path": source["relative_path"],
                       "state": "PENDING" if pending else "FAIL" if failures else "EXCLUDED" if excluded else "MECHANICAL_GATES_PASSED",
                       "failures": sorted(set(failures))})
    coverage = []
    for (station, clock, date), observed in sorted(buckets.items()):
        coverage.append({"radar_id": station, "clock": clock, "date": date,
                         "native_start_buckets": len(observed),
                         "missing_native_start_buckets": sorted(set(range(240)) - observed),
                         "product_time_alignment_proven": False})
    return {"files_expected": len(expected), "files_with_receipts": len(rows),
            "file_states": dict(Counter(r["state"] for r in matrix)),
            "failure_reasons": dict(Counter(f for r in matrix if r["state"] == "FAIL" for f in r["failures"])),
            "pending_checks": dict(Counter(f for r in matrix if r["state"] == "PENDING" for f in r["failures"])),
            "source_identity_errors": errors, "exclusions": dict(exclusion_counts),
            "requested_utc_day": day, "native_start_coverage": coverage,
            "mechanical_corpus_complete": not errors and all(r["state"] in {"MECHANICAL_GATES_PASSED", "EXCLUDED"} for r in matrix),
            "meteorological_acceptance": "NOT_COMPLETED",
            "normal_publication_and_ui_acceptance": "NOT_COMPLETED", "files": matrix}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inventory", type=Path)
    p.add_argument("batch", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    state = json.loads((a.batch / "state.json").read_text())
    frozen = json.loads((a.inventory / "state.json").read_text())
    data = (a.inventory / "raw-files.jsonl").read_bytes()
    if frozen["status"] != "FROZEN" or hashlib.sha256(data).hexdigest() != frozen["manifest_sha256"]:
        raise ValueError("raw corpus freeze mismatch")
    inventory = [json.loads(x) for x in data.splitlines()]
    if len(inventory) != frozen["files_expected"]:
        raise ValueError("raw corpus file count changed")
    receipts = []
    for f in a.batch.glob("audit-*.jsonl"):
        shard = int(f.stem.split("-")[1])
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if int(r["raw_sha256"], 16) % state["worker_count"] != shard:
                raise ValueError("receipt assigned to wrong file shard")
            receipts.append((shard, r))
    result = summarize(inventory, receipts,
                       json.loads((a.batch / "decoder-config-identities.json").read_text()))
    result.update(batch_status=state["status"], batch_script_sha256=state["audit_script_sha256"],
                  compute_image_id=state["compute_image_id"], raw_manifest_sha256=frozen["manifest_sha256"])
    with a.output.open("x") as target:
        json.dump(result, target, indent=2)
    print(json.dumps({k: result[k] for k in ("files_expected", "files_with_receipts", "file_states", "failure_reasons", "mechanical_corpus_complete")}))


if __name__ == "__main__":
    main()
