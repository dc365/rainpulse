"""Offline artifact tools. Never connect to queues, publish, or mutate raw volumes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import yaml
from . import VERSION
from .background import build_background, write_asset
from .config import BackgroundPolicy
from .lineage import audit_inventory, tree_digest, trace_mosaic_cell


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def _samples(manifest, base):
    for entry in manifest["samples"]:
        path = (base/entry["npz_path"]).resolve()
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["input_sha256"]:
            raise ValueError("sample NPZ differs from the committed input_sha256")
        with np.load(path, allow_pickle=False) as source:
            arrays = {key: source[key] for key in source.files}
        sample = {**entry["metadata"], **arrays, "input_sha256": entry["input_sha256"]}
        yield sample


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("make-profile")
    p.add_argument("--base-config", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--mode", choices=["audit", "experiment_quarantine"], default="audit")
    p = sub.add_parser("build-background")
    p.add_argument("--manifest", required=True); p.add_argument("--output", required=True)
    p.add_argument("--receipt", required=True)
    p = sub.add_parser("build-registry")
    p.add_argument("--manifest", required=True); p.add_argument("--output", required=True)
    p.add_argument("--receipt", required=True)
    p = sub.add_parser("digest")
    p.add_argument("artifact")
    p = sub.add_parser("audit-lineage")
    p.add_argument("--manifest", required=True); p.add_argument("--output", required=True)
    p = sub.add_parser("trace-cell")
    p.add_argument("--manifest", required=True); p.add_argument("--mosaic-id", required=True)
    p.add_argument("--row", type=int, required=True); p.add_argument("--column", type=int, required=True)
    p.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "make-profile":
        data = yaml.safe_load(Path(args.base_config).read_text())
        if data.get("pipeline_version") != "qc-opensource-7.3.6" or data.get("engine") != "open_source":
            raise ValueError("the child profile must be based on the frozen 7.3.6 profile")
        data["profile_version"] += "-review-20260917-" + args.mode.replace("_", "-")
        data["review_extension_version"] = VERSION
        data["operational_eligible"] = False
        data["generalization"]["broad_source"]["source_review"] = {"mode": args.mode}
        data["nonprecip_review"] = {"mode": args.mode, "quarantine_classes": ["fixed_ground"]}
        # Real model validation uses the installed repository, not a permissive YAML-only path.
        from ..profile import OpenSourceQCProfile
        validated = OpenSourceQCProfile.model_validate(data)
        with Path(args.output).open("x", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        print(json.dumps({"profile_version": validated.profile_version, "parameters_sha256": validated.parameters_hash, "operational_eligible": False}))
    elif args.command == "build-background":
        path = Path(args.manifest).resolve()
        data = json.loads(path.read_text())
        policy = BackgroundPolicy.model_validate(data.get("policy", {}))
        arrays, meta = build_background(_samples(data, path.parent), policy)
        if Path(args.receipt).exists():
            raise FileExistsError(args.receipt)
        receipt = write_asset(args.output, arrays)
        _write_json(args.receipt, {**receipt, "metadata": meta})
        print(json.dumps(receipt))
    elif args.command == "build-registry":
        from .background_registry import create_registry, REGISTRY_VERSION
        path = Path(args.manifest).resolve()
        entries = []
        for entry in json.loads(path.read_text())["entries"]:
            receipt = json.loads((path.parent/entry["receipt_path"]).read_text())
            meta = receipt["metadata"]
            entries.append({**meta, "asset_uri": entry["asset_uri"], "asset_content_sha256": receipt["asset_content_sha256"]})
        arrays, meta = create_registry(entries)
        if Path(args.receipt).exists():
            raise FileExistsError(args.receipt)
        receipt = write_asset(args.output, arrays, asset_version=REGISTRY_VERSION)
        _write_json(args.receipt, {**receipt, "metadata": meta})
        print(json.dumps(receipt))
    elif args.command == "digest":
        print(tree_digest(args.artifact))
    else:
        path = Path(args.manifest).resolve()
        data = json.loads(path.read_text())
        audit = audit_inventory(data, base_dir=path.parent)
        if args.command == "audit-lineage":
            _write_json(args.output, audit)
            return 0 if audit["lineage_consistent"] else 2
        if not audit["lineage_consistent"]:
            _write_json(args.output, {"status": "lineage_not_verified", "audit": audit})
            return 2
        import zarr
        roots = {ident: zarr.open_group(record["path"], mode="r") for ident, record in audit["assets"].items()}
        result = trace_mosaic_cell(roots[args.mosaic_id], roots, roots, row=args.row, column=args.column)
        _write_json(args.output, result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print(f"review command failed: {error}", file=sys.stderr)
        raise SystemExit(2)
