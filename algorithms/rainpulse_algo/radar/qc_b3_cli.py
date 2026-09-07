"""Offline B3 artifact builders. No service publication or profile activation."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .qc_clutter import build_static_ground_clutter_asset, clutter_asset_npz_arrays
from .qc_labels import build_label_manifest
from .qc_promotion import load_qc_promotion_profile, summarize_qc_promotion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("labels", "clutter", "promotion"):
        sub = commands.add_parser(command)
        sub.add_argument("--input", type=Path, required=True)
        sub.add_argument("--output", type=Path, required=True)
        if command == "labels":
            sub.add_argument("--frozen-config-sha256", required=True)
            sub.add_argument("--frozen-code-revision", required=True)
        if command == "promotion":
            sub.add_argument("--profile", type=Path, required=True)
            sub.add_argument("--label-manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output already exists; choose a new artifact path")
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if args.command == "labels":
            entries = []
            label_artifacts = {}
            for row in data["entries"]:
                path = (args.input.parent / row["label_npz"]).resolve()
                arrays = _verified_arrays(path, row["label_sha256"])
                entries.append({**row, "label_values": arrays["label_values"]})
                label_artifacts[row["scan_id"]] = {"uri": str(path), "sha256": row["label_sha256"]}
            report = build_label_manifest(
                entries,
                generated_at=datetime.now(UTC),
                frozen_config_sha256=args.frozen_config_sha256,
                frozen_code_revision=args.frozen_code_revision,
            )
            report["provenance"] = {
                "input_manifest_sha256": _sha(args.input),
                "operational_eligible": False,
            }
            for scan in report["scans"]:
                scan["label_artifact"] = label_artifacts[scan["scan_id"]]
            _write_json_exclusive(args.output, report)
            return 0
        if args.command == "clutter":
            samples = []
            provenance = []
            for row in data["samples"]:
                if not isinstance(row.get("source_uri"), str) or not row["source_uri"]:
                    raise ValueError("clutter source_uri must identify verified clear-sky evidence")
                path = (args.input.parent / row["npz_path"]).resolve()
                arrays = _verified_arrays(path, row["sha256"])
                samples.append(
                    {**row, **{key: arrays[key] for key in ("azimuth_deg", "range_m", "dbzh")}}
                )
                provenance.append(
                    {key: row[key] for key in ("source_uri", "sha256", "observed_at_utc")}
                )
            asset = build_static_ground_clutter_asset(samples)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temp = Path(tempfile.mkdtemp(prefix=".qc-clutter-", dir=args.output.parent))
            try:
                np.savez_compressed(temp / "prior.npz", **clutter_asset_npz_arrays(asset))
                np.savez_compressed(temp / "support.npz", **asset.support_count_by_sweep)
                report = {
                    "schema_version": "1.0",
                    "asset_version": asset.asset_version,
                    "radar_id": asset.radar_id,
                    "elevation_deg_by_sweep": asset.elevation_deg_by_sweep,
                    "geometry_by_sweep": asset.geometry_by_sweep,
                    "operational_eligible": False,
                    "clear_sky_day_count_by_sweep": asset.clear_sky_day_count_by_sweep,
                    "minimum_clear_sky_days": asset.minimum_clear_sky_days,
                    "minimum_gate_observations": asset.minimum_gate_observations,
                    "dbzh_threshold": asset.dbzh_threshold,
                    "sources": provenance,
                    "input_manifest_sha256": _sha(args.input),
                    "prior_sha256": _sha(temp / "prior.npz"),
                    "support_sha256": _sha(temp / "support.npz"),
                }
                (temp / "manifest.json").write_text(json.dumps(report, indent=2, allow_nan=False))
                temp.rename(args.output)
            finally:
                if temp.exists():
                    shutil.rmtree(temp)
            return 0
        labels = json.loads(args.label_manifest.read_text(encoding="utf-8"))
        if not labels.get("holdout_locked_after_freeze"):
            raise ValueError("promotion requires a frozen label manifest")
        for key in ("frozen_config_sha256", "frozen_code_revision"):
            if not labels.get(key) or data.get(key) != labels[key]:
                raise ValueError(f"promotion {key} differs from frozen labels")
        allowed = {
            (scan["process_id"], scan["partition"], scan["case_category"])
            for scan in labels["scans"]
        }
        for row in data["rows"]:
            if (row["process_id"], row["partition"], row["case_category"]) not in allowed:
                raise ValueError("promotion row is not represented by frozen labels")
        report = summarize_qc_promotion(
            data["rows"], profile=load_qc_promotion_profile(args.profile)
        )
        report["provenance"] = {
            "input_sha256": _sha(args.input),
            "label_manifest_sha256": _sha(args.label_manifest),
            "promotion_profile_sha256": _sha(args.profile),
            "frozen_config_sha256": labels["frozen_config_sha256"],
            "frozen_code_revision": labels["frozen_code_revision"],
        }
        _write_json_exclusive(args.output, report)
        return {"passed": 0, "insufficient_data": 3, "failed": 4}[report["overall_status"]]
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _verified_arrays(path: Path, digest: str) -> dict[str, np.ndarray]:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"input SHA-256 differs: {path}")
    with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
        return {key: arrays[key].copy() for key in arrays.files}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".qc-b3-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
