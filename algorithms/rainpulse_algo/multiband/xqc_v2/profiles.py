"""New frozen network releases; no deployment or mutation of the parent file."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
from .config import XQCConfig

# Measured on the live 2026-08-28 ZF701 interference frame (scan 93d2d253):
# of 1230 radial-geometry candidate gates on the lowest cut the legacy gates
# confirmed only 84. RHOHV<=0.8 kept 40% (real spokes measured 0.63-0.88),
# phase jitter>=20 deg kept 33%, SNR>=12 kept 67%, and the both-flank 8 dB
# contrast within 6 deg killed 70% of the polar survivors (weak spokes
# contrast only 6-13 dB). The S receiver-domain radial core cannot take over
# either: X reference-block SNR measures mu ~ -5 dB / p90 spread 4-12 dB
# against the S core's hard mu>=20 dB, spread<=2 dB contract, so it abstains
# on X SNR semantics. The tuned conjunction below lifts per-ray coverage to
# 30-180 contiguous gates on the real spokes while rain stays protected:
# RHOHV>0.90 gates can never be flagged (rain is ~0.95+), the CF weather
# proxy and weather-protected masks stay excluded, and geometry + measured
# flanks + polar evidence all remain required, so intensity alone still
# cannot trigger removal. X-only: these fields exist solely in
# station.x_qc.enhancement and never touch the S qc_engine configs.
#
# r5 fragment completion (S association contract, see xqc_v2/fragments.py):
# confirmed per-gate evidence leaves dotted spokes because X interference is
# bursty per gate. On the same frame, completing along confirmed rays with
# target-local polarimetric badness + a 20log10(r) source-law match recovers
# ~1200 more gates per cut with zero rho>0.90 contamination; the 537 cut0
# candidates with rain-like rho>0.9 stay untouched by design.
RADIAL_TUNING = {
    "radial_maximum_rhohv": .90,
    "radial_phase_jitter_deg": 10.,
    "radial_maximum_dbzh": 45.,
    "radial_flank_contrast_db": 6.,
    "radial_minimum_snr_db": 8.,
    "radial_flank_deg": 10.,
    "fragment_maximum_distance_m": 15000.,
    "fragment_minimum_anchor_gates": 4,
    "fragment_association_difference_db": 8.,
}


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
                "isolation_enabled": clutter, **RADIAL_TUNING,
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
