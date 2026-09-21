"""Offline build/holdout/audit. No API writes, online training or publication."""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import tempfile
import numpy as np
from .builder import build_episode
from .config import BuildConfig, EpisodeConfig
from .core import evaluate
from .data import json_bytes, sha, utc
from .io import read_manifest, background_bytes, load_background, atomic_file
from ..receipts import npz_bytes


def _folder_transaction(destination, writer):
    path = Path(destination)
    if path.exists(): raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".episode-output-", dir=path.parent))
    try:
        writer(tmp)
        # A directory destination is new and exclusive. No partial output is
        # presented as a completed audit. Existing nonempty output is refused.
        if path.exists(): raise FileExistsError(path)
        tmp.rename(path)
    finally:
        if tmp.exists(): shutil.rmtree(tmp)


def cross_validate(manifest, samples, cfg):
    origin = manifest.get("origin_time") or min(samples, key=lambda s: utc(s.observed_at)).observed_at
    t0 = utc(origin).timestamp()
    ids = {s.scan_id: int((utc(s.observed_at).timestamp() - t0) // cfg.block_seconds) for s in samples}
    # Assign an entire scan to its earliest layer's block. A volume is never
    # partly trained and partly held out, including across block boundaries.
    for s in samples:
        ids[s.scan_id] = min(ids[s.scan_id], int((utc(s.observed_at).timestamp() - t0) // cfg.block_seconds))
    result = []
    for b in sorted(set(ids.values())):
        train = [s for s in samples if ids[s.scan_id] != b]
        test = [s for s in samples if ids[s.scan_id] == b]
        rec = {"heldout_block": b, "train_scans": sorted({s.scan_id for s in train}),
               "heldout_scans": sorted({s.scan_id for s in test})}
        assert not set(rec["train_scans"]) & set(rec["heldout_scans"])
        try:
            model = build_episode(train, cfg, review_receipt=manifest["review_receipt"],
                reviewed_no_precipitation=manifest.get("reviewed_no_precipitation"), origin_time=origin)
        except ValueError as e:
            result.append({**rec, "status": "INSUFFICIENT_TRAINING_SUPPORT", "reason": str(e)})
            continue
        eligible = matched = supported = 0
        per_sweep = []
        for sample in test:
            ev = evaluate(sample, model, EpisodeConfig(mode="audit", no_rain_below_dbz=cfg.no_rain_below_dbz), validation_holdout=True)
            a = ev.arrays
            n = int(((a["EBG_DOMAIN_MASK"] == 1) & (a["EBG_MODEL_AVAILABLE_MASK"] == 1)).sum())
            m = int(a["EBG_BACKGROUND_MATCH_MASK"].sum())
            c = int(a["EBG_ACTION_CANDIDATE_MASK"].sum())
            eligible += n; matched += m; supported += c
            per_sweep.append({"scan_id": sample.scan_id, "sweep_id": sample.sweep_id,
                              "observed_domain_gates": n, "background_matches": m,
                              "candidate_not_applied": c})
        result.append({**rec, "status": "EVALUATED", "reference_asset_sha256": sha(background_bytes(model)),
                       "observed_domain_gates": eligible, "background_matches": matched,
                       "background_match_fraction": matched / eligible if eligible else None,
                       "candidate_not_applied": supported, "sweeps": per_sweep})
    return {"schema": "rainpulse.episode-contiguous-holdout-v1", "build_config_sha256": cfg.digest,
            "split_unit": "whole_scan_contiguous_time_block", "folds": result,
            "independent_weather_validation": False, "operational_eligible": False,
            "calibrated_probability": False, "no_online_parameter_update": True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("build", "cv"):
        x = sub.add_parser(name); x.add_argument("--manifest", type=Path, required=True)
        x.add_argument("--build-config", type=Path); x.add_argument("--output", type=Path, required=True)
    x = sub.add_parser("audit"); x.add_argument("--manifest", type=Path, required=True)
    x.add_argument("--asset", type=Path, required=True); x.add_argument("--sha256", required=True)
    x.add_argument("--config", type=Path); x.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    manifest, samples = read_manifest(args.manifest)
    if args.command in ("build", "cv"):
        cfg = BuildConfig.model_validate(json.loads(args.build_config.read_text()) if args.build_config else {})
        if args.command == "build":
            model = build_episode(samples, cfg, review_receipt=manifest["review_receipt"],
                reviewed_no_precipitation=manifest.get("reviewed_no_precipitation"), origin_time=manifest.get("origin_time"))
            data = background_bytes(model); atomic_file(args.output, data)
            print(json.dumps({"path": str(args.output), "sha256": sha(data), "grade": "short_episode",
                              "build_config_sha256": cfg.digest, "sweeps": len(model.metadata["sweeps"])}))
        else:
            report = cross_validate(manifest, samples, cfg)
            atomic_file(args.output, json_bytes(report))
    else:
        cfg = EpisodeConfig.model_validate(json.loads(args.config.read_text()) if args.config else {})
        if cfg.mode != "audit": raise ValueError("raw audit never applies CR changes; use audit configuration")
        model = load_background(args.asset, args.sha256, maximum_bytes=cfg.maximum_asset_bytes)
        def write(directory):
            report = []
            for i, sample in enumerate(samples):
                ev = evaluate(sample, model, cfg)
                data = npz_bytes(ev.arrays); name = f"target-{i:04d}.npz"
                (directory / name).write_bytes(data)
                report.append({"scan_id": sample.scan_id, "sweep_id": sample.sweep_id, "path": name,
                               "sha256": sha(data), "summary": ev.summary})
            (directory / "report.json").write_bytes(json_bytes({"config_sha256": cfg.digest, "targets": report,
                "background_sha256": args.sha256, "no_qc_or_cr_changed": True,
                "warning": "raw-only audit has no inherited weather masks; not a production disposition"}))
        _folder_transaction(args.output, write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
