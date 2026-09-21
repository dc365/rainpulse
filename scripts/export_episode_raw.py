#!/usr/bin/env python3
"""Export existing local normalized Zarrs as bound RAW episode inputs.

Requires the real RainPulse environment (Zarr + normal profile/adapter imports).
This exporter never interprets NaN as no echo and never trains on existing QC.
"""
from pathlib import Path
import argparse
from dataclasses import replace
import hashlib
import json
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))


def main():
    import zarr
    from rainpulse_algo.radar.qc import load_qc_profile
    from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
    from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.background import from_native
    from rainpulse_algo.radar.qc_engine.volume_review.episode_background.io import sample_bytes
    from rainpulse_algo.radar.qc_engine.volume_review.episode_background.cli import _folder_transaction
    from rainpulse_algo.radar.qc_engine.volume_review.episode_background.data import json_bytes, sha, is_hash
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", type=Path, required=True, help="JSON list of local normalized Zarr directory paths")
    p.add_argument("--qc-profile", type=Path, required=True, help="Actual source QC profile for raw availability rules")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reviewed-no-precipitation", action="store_true")
    p.add_argument("--review-receipt", help="SHA256 of the operator's review record")
    p.add_argument("--acquired-field"); p.add_argument("--no-echo-field")
    args = p.parse_args()
    if bool(args.acquired_field) != bool(args.no_echo_field):
        p.error("both decoder-verified status fields must be supplied together")
    if args.reviewed_no_precipitation and not is_hash(args.review_receipt):
        p.error("reviewed background requires a review-record SHA256")
    profile = load_qc_profile(args.qc_profile)
    paths = json.loads(args.inputs.read_text())
    if not isinstance(paths, list) or not paths: p.error("inputs must be a nonempty path list")
    def write(root):
        groups = {}; index = 0
        for path in paths:
            source = zarr.open_group(str(path), mode="r")
            if source.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
                raise ValueError("training export accepts normalized RAW volumes, not QC products")
            for number in source["sweep_number"][:]:
                name = f"sweep_{int(number):03d}"
                n = adapt_sweep(source, name, profile)
                sample = from_native(n)
                if args.acquired_field:
                    g = source[name]
                    sample = replace(sample, acquired=g[args.acquired_field][:][n.original_indices], no_echo=g[args.no_echo_field][:][n.original_indices])
                key = (sample.radar_id, sample.processing_id)
                identity = hashlib.sha256(json_bytes(key)).hexdigest()[:16]
                directory = root / ("episode-" + identity); directory.mkdir(exist_ok=True)
                filename = f"raw-{index:05d}.npz"; index += 1
                data = sample_bytes(sample); (directory / filename).write_bytes(data)
                groups.setdefault(key, (directory, []))[1].append({"path": filename, "sha256": sha(data)})
        for (station, processing), (directory, refs) in groups.items():
            manifest = {"schema": "rainpulse.raw-episode-v1", "samples": refs,
                "radar_id": station, "processing_id": processing,
                "reviewed_no_precipitation": args.reviewed_no_precipitation,
                "review_receipt": args.review_receipt,
                "acquisition_semantics": "explicit_decoder_fields" if args.acquired_field else "unknown_not_inferred",
                "processing_identity_semantics": "radar_config_version; undocumented hardware/calibration changes remain unverified"}
            (directory / "manifest.json").write_bytes(json_bytes(manifest))
    _folder_transaction(args.output, write)


if __name__ == "__main__": main()
