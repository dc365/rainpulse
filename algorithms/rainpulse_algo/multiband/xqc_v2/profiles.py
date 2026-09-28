"""New frozen network releases; no deployment or mutation of the parent file."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
from .config import XQCConfig


def generate(parent, radar_ids, backend):
    if backend not in {"wradlib", "numpy_reference"}:
        raise ValueError("explicit backend required")
    if not radar_ids or len(set(radar_ids)) != len(radar_ids):
        raise ValueError("select unique X radar IDs")
    for sid in radar_ids:
        if sid not in parent.get("stations", {}) or parent["stations"][sid].get("band") != "X":
            raise ValueError("not an X station: " + sid)
        if not parent["stations"][sid].get("x_qc_enabled"):
            raise ValueError("selected station is not already enabled for candidate X QC")
    profiles = {}
    for name, mode, clutter in (("audit", "audit", True), ("radial", "quarantine", False),
                                ("all-cr", "cr_only", True), ("all-quarantine", "quarantine", True)):
        out = copy.deepcopy(parent)
        out["release_id"] = parent["release_id"][:60] + "-xqc2-" + name
        # The v2 read set decodes dual-pol moments and masks; real 40-cut X
        # volumes exceed the legacy 512 MiB network budget, so releases must
        # lift it to the streaming X QC constant's headroom.
        out["maximum_input_bytes"] = max(int(out.get("maximum_input_bytes", 0)),
                                         1024 * 1024**2)
        for sid in radar_ids:
            x = out["stations"][sid].setdefault("x_qc", {})
            if x.get("enhancement") is not None:
                raise ValueError("parent already has an enhancement; do not silently replace it")
            cfg = XQCConfig.model_validate({"mode": mode, "clutter_enabled": clutter,
                "isolation_enabled": clutter,
                "clutter": {"mode": "quarantine", "minimum_range_m": 750.,
                    "minimum_phase_spacing_m": 50., "neighbourhood_m": 1000.,
                    "depolarization_backend": backend, "isolated_objects": {"mode": "audit"}},
                "receiver": {"local_policy": "source_joint_review",
                    "segment_reference": {"mode": "experiment"},
                    "source_family": {"mode": "experiment", "full_policy": "cr_only",
                        "local_block_m": 1000., "minimum_local_support_m": 500.}}})
            x["enhancement"] = cfg.model_dump(mode="json")
        profiles[name] = out
    return profiles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--radars", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("wradlib", "numpy_reference"), default="wradlib")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory must not exist")
    parent = json.loads(args.parent.read_bytes())
    result = generate(parent, args.radars, args.backend)
    from ..model import Network
    encoded = {name: json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode() + b"\n"
               for name, value in result.items()}
    for data in encoded.values():
        Network.from_bytes(data)
    args.output.mkdir(parents=True)
    for name, data in encoded.items():
        (args.output / ("xqc2-" + name + ".json")).write_bytes(data)

if __name__ == "__main__":
    main()
