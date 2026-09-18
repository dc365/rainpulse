#!/usr/bin/env python3
"""Read-only source-stage replay; does not rebuild the worker or downstream fields.

Uses immutable normalized measurements plus the STORED legacy held-out source
references/protection masks. New segmented references are recomputed from raw.
No final QC artifact is published; an action projection is clearly distinguished
from complete QC -> Hybrid -> Mosaic -> QPE execution.
"""
from __future__ import annotations
import argparse
from datetime import datetime, UTC
import importlib
import json
from pathlib import Path
import sys
import time
import types
import hashlib
import numpy as np
import yaml
from radial_revision_case_io import case_groups, native, directory_digest


def modules(repo):
    engine = repo/"algorithms/rainpulse_algo/radar/qc_engine"
    if not (engine/"review_extension/radial_revision/engine.py").exists():
        raise ValueError("repository/overlay does not contain the radial revision")
    # Isolated diagnostic namespace: do not import unrelated worker dependencies.
    for name, paths in (("rp_stage_replay", []), ("rp_stage_replay.engine", [str(engine)])):
        m = types.ModuleType(name); m.__path__ = paths; sys.modules[name] = m
    load = lambda name: importlib.import_module("rp_stage_replay.engine.review_extension."+name)
    return load


def write_json(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    temp = path.with_suffix(path.suffix+".tmp")
    temp.write_text(data, encoding="utf-8"); temp.replace(path)


def replay(args):
    case_root, output = args.case_root.resolve(), args.output.resolve()
    if output == case_root or output.is_relative_to(case_root):
        raise ValueError("outputs must be outside immutable case data")
    output.mkdir(parents=True, exist_ok=False)
    load = modules(args.repo.resolve())
    sc_class = load("config").SourceReviewConfig
    rv_class = load("radial_revision.config").RadialRevisionConfig
    source = load("source").source_additions
    validator = load("source_validation").validate_source_fields
    cfg = yaml.safe_load((case_root/"configs/fujian-qc-review-20260917-experiment.yaml").read_text())
    if cfg.get("pipeline_version") != "qc-opensource-7.3.6":
        raise ValueError("this replay expects the supplied frozen 7.3.6 data contract")
    source_cfg = cfg["generalization"]["broad_source"]["source_review"]
    broad_mode = cfg["generalization"]["broad_source"]["mode"]
    cases = [json.loads(x) for x in (case_root/"metadata/cases.jsonl").read_text().splitlines() if x.strip()]
    all_rows, checks = [], []
    examples = {"SITE_A": [265.72, 346.35], "SITE_B": [355.92, 291.79, 226.87, 243.40]}
    for case in cases:
        normalized, qc = case_groups(case_root, case)
        actual = directory_digest(normalized.path)
        if actual != case["normalized_sha256_scrubbed"]:
            raise ValueError("normalized input content digest differs from manifest")
        names = sorted(k for k in qc.keys() if k.startswith("sweep_") and k[6:].isdigit())
        if args.sweeps == "lowest": names = names[:1]
        for name in names:
            n = native(normalized[name], normalized.attrs, cfg)
            q = qc[name]; order = n.original_indices
            keys = ("SRC_REVIEW_TARGET_MATCH_MASK", "SRC_REVIEW_SOURCE_AVAILABLE_MASK", "SRC_REVIEW_RESIDUAL_DB",
                    "SRC_REVIEW_WEATHER_PROTECTED_MASK", "SRC_REVIEW_CONFLICT_MASK", "SRC_REVIEW_REFERENCE_FOLD_ID",
                    "SRC_REVIEW_QUALIFIED_MASK", "QPE_ELIGIBLE_MASK", "QC_ACTION", "RFI_QUARANTINE_MASK",
                    "DBZH_RAW", "DBZH_USABLE", "QC_FLAGS", "QUALITY_INDEX")
            a = {k: q[k][:][order] for k in keys}
            raw_before = {k: v.copy() for k, v in n.fields.items()}
            check = {"site": case["site"], "sweep": name, "input_sha256": actual,
                     "raw_matches_stored": bool(np.array_equal(a["DBZH_RAW"], n.fields["DBZH"], equal_nan=True)),
                     "eligibility_matches_finite_usable": bool(np.array_equal(a["QPE_ELIGIBLE_MASK"] == 1, np.isfinite(a["DBZH_USABLE"]))) }
            if not check["raw_matches_stored"] or not check["eligibility_matches_finite_usable"]:
                raise ValueError("input baseline integrity failed")
            baseline_cfg = sc_class.model_validate(source_cfg)
            legacy_q, legacy_a, _ = source(n, baseline_cfg, a["SRC_REVIEW_TARGET_MATCH_MASK"], a["SRC_REVIEW_RESIDUAL_DB"],
                weather=a["SRC_REVIEW_WEATHER_PROTECTED_MASK"], conflicts=a["SRC_REVIEW_CONFLICT_MASK"],
                reference_available=a["SRC_REVIEW_SOURCE_AVAILABLE_MASK"])
            check["legacy_qualification_mismatches"] = int(np.sum(legacy_q != (a["SRC_REVIEW_QUALIFIED_MASK"] == 1)))
            if check["legacy_qualification_mismatches"]:
                raise ValueError("legacy source replay differs from stored qualification")
            observed = n.field_available["DBZH"]
            eligible = a["QPE_ELIGIBLE_MASK"] == 1
            domain = eligible & (n.fields["DBZH"] >= 10) & (n.ranges[None, :] >= 100000)
            for step in args.steps:
                started = time.monotonic(); records = []
                revision = rv_class(step=step, mode=args.mode, allow_segmented_quarantine=step >= 2)
                child_data = dict(source_cfg, radial_revision=revision.model_dump(mode="json"))
                qualified, new, summary = source(n, sc_class.model_validate(child_data), a["SRC_REVIEW_TARGET_MATCH_MASK"],
                    a["SRC_REVIEW_RESIDUAL_DB"], weather=a["SRC_REVIEW_WEATHER_PROTECTED_MASK"],
                    conflicts=a["SRC_REVIEW_CONFLICT_MASK"], reference_available=a["SRC_REVIEW_SOURCE_AVAILABLE_MASK"],
                    revision_records=records)
                new["SRC_REVIEW_REFERENCE_FOLD_ID"] = a["SRC_REVIEW_REFERENCE_FOLD_ID"]
                validator(new, observed)
                # Existing outer source/broad modes must also permit proposals.
                enabled = baseline_cfg.mode == broad_mode == "experiment_quarantine"
                proposal = (new["RV2_ACTION_PROPOSAL_MASK"] == 1) & enabled
                added = proposal & observed & (a["QC_ACTION"] != 2) & (a["RFI_QUARANTINE_MASK"] == 0)
                projected_eligible = eligible & ~added
                projected_usable = a["DBZH_USABLE"].copy(); projected_usable[added] = np.nan
                if not np.array_equal(projected_eligible, np.isfinite(projected_usable)):
                    raise ValueError("action projection leaked quantitative eligibility")
                if np.any(added & ~observed): raise ValueError("created an observation")
                if args.mode == "audit" and (added.any() or not np.array_equal(qualified, legacy_q)):
                    raise ValueError("audit altered baseline disposition")
                for key in n.fields:
                    if not np.array_equal(raw_before[key], n.fields[key], equal_nan=True):
                        raise ValueError("raw measurement modified")
                row = {"site": case["site"], "sweep": name, "step": step,
                       "mode": args.mode, "scope": "source_stage_plus_action_projection_not_full_worker",
                       "elapsed_seconds": round(time.monotonic()-started, 4),
                       "stored_eligible_gates": int(eligible.sum()),
                       "new_quarantine_projection_gates": int(added.sum()),
                       "new_eligible_loss_gates": int((added & eligible).sum()),
                       "new_eligible_loss_fraction": float((added & eligible).sum()/max(1, eligible.sum())),
                       "diagnostic_domain_gates_before": int(domain.sum()),
                       "new_quarantine_in_diagnostic_domain": int((domain & added).sum()),
                       "diagnostic_domain_gates_after": int((domain & ~added).sum()),
                       "candidate_in_domain": int((domain & (new["RV2_CANDIDATE_MASK"] == 1)).sum()),
                       "weak_receiver_match_in_domain": int((domain & (new["RV2_WEAK_MATCH_MASK"] == 1)).sum()),
                       "new_confirmed_gates": 0, "missing_filled": 0,
                       "summary": summary["radial_revision"], "examples": []}
                if name == "sweep_000":
                    for angle in examples.get(case["site"], []):
                        ray = int(np.argmin(abs((n.azimuth-angle+180)%360-180))); d = domain[ray]
                        row["examples"].append({"azimuth_deg": float(n.azimuth[ray]), "original_ray": int(order[ray]),
                          "domain_before": int(d.sum()), "candidate": int((d & (new["RV2_CANDIDATE_MASK"][ray] == 1)).sum()),
                          "weak_receiver_match": int((d & (new["RV2_WEAK_MATCH_MASK"][ray] == 1)).sum()),
                          "new_quarantine": int((d & added[ray]).sum()), "domain_after": int((d & ~added[ray]).sum())})
                stem = f"{case['site']}_{name}_step{step}"
                write_json(output/(stem+".json"), row)
                write_json(output/(stem+"_references.json"), records)
                if args.write_arrays:
                    np.savez_compressed(output/(stem+".npz"), **{k:v for k,v in new.items() if k.startswith("RV2_")},
                                        PROJECTED_NEW_QUARANTINE_MASK=added.astype("uint8"))
                all_rows.append(row)
                print(stem, "new eligible loss", row["new_eligible_loss_gates"], "domain", row["new_quarantine_in_diagnostic_domain"], flush=True)
            check["raw_unchanged_after_all_steps"] = True
            checks.append(check)
    report = {"created_at_utc": datetime.now(UTC).isoformat(), "baseline_commit": "547ead93f0a2849893f008dafcbbf78f1f2a483d",
              "scope": "incremental_source_stage_replay", "full_worker_rerun": False,
              "legacy_references": "stored held-out matches/residuals and protection masks",
              "new_segmented_references": "recomputed from normalized raw data excluding target and guard blocks",
              "independent_weather_acceptance": False, "operational_eligible": False,
              "sweep_count": len(checks), "stage_runs": len(all_rows), "input_checks": checks, "runs": all_rows}
    write_json(output/"report.json", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="new output directory, outside inputs")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--sweeps", choices=("all", "lowest"), default="all")
    p.add_argument("--steps", nargs="+", type=int, choices=(1, 2, 3), default=[1, 2, 3])
    p.add_argument("--mode", choices=("audit", "experiment_quarantine"), default="audit")
    p.add_argument("--write-arrays", action="store_true")
    args = p.parse_args()
    try:
        replay(args)
    except Exception as exc:
        print(f"replay failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

if __name__ == "__main__": main()
