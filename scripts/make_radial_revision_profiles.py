#!/usr/bin/env python3
"""Generate distinct opt-in child QC profiles from YOUR local reviewed parent.

Does not replace a parent file or modify planner/defaults. Scrubbed sample YAML
is never treated as a deployable source of radar/asset configuration.
"""
import argparse
import copy
from pathlib import Path
import sys
import yaml


def children(parent):
    if (parent.get("pipeline_version") != "qc-opensource-7.3.6" or
            parent.get("review_extension_version") != "qc-review-20260917-v1" or
            parent.get("operational_eligible") is not False):
        raise ValueError("requires an explicitly non-operational review-v1/7.3.6 parent")
    base = parent.get("generalization", {}).get("broad_source", {})
    if not base.get("source_review"):
        raise ValueError("the parent must already contain source_review")
    if base["source_review"].get("radial_revision") is not None:
        raise ValueError("use the unmodified parent, not an existing radial child")
    if "review-20260917" not in parent.get("profile_version", ""):
        raise ValueError("parent has no explicit review identity")
    result = {}
    for step in (1, 2, 3):
        for mode in ("audit", "experiment_quarantine"):
            child = copy.deepcopy(parent)
            child["profile_version"] = parent["profile_version"]+f"-radial-20260918-s{step}-{mode}"
            child["generalization"]["broad_source"]["source_review"]["radial_revision"] = {
                "version": "radial-source-v2-20260918", "step": step, "mode": mode,
                "allow_segmented_quarantine": step >= 2, "weak_policy": "diagnostic_only"}
            result[f"radial-20260918-step{step}-{mode}.yaml"] = child
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    result = children(yaml.safe_load(args.base.read_text(encoding="utf-8")))
    # Fail before writing anything if a generated profile already exists.
    if any((args.output_dir/name).exists() for name in result):
        raise FileExistsError("child profile already exists; no files overwritten")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in result.items():
        text = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        # exclusive creation prevents accidental replacement
        with (args.output_dir/name).open("x", encoding="utf-8") as f:
            f.write(text)
        print(args.output_dir/name)
    print("No runtime defaults changed. Validate profiles with the repository loader before selecting a candidate.")

if __name__ == "__main__": main()
