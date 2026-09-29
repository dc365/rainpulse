#!/usr/bin/env python3
# ruff: noqa: E501, I001
"""Create a new, validated S/X quality-height release; never edit active files.

Requires repository algorithm dependencies. It preserves all existing stations,
X nonmet settings, historical products and geometry/calibration verification.
A new product does not enable a station, deploy workers or promote QPE.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "algorithms"))
from rainpulse_algo.multiband.model import Grid, Network, XProfile  # noqa: E402

ALLOWED_X = {"attenuation", "alpha_db_per_degree", "zphi", "max_pia_db", "phase_window_m",
             "max_negative_phase_step_deg", "phase_anchor_max_range_m"}


def read_document(path: Path, maximum: int = 1024 * 1024):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("configuration exceeds byte budget")
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def prepare(base, grid, product_id, release_id, x_settings=None):
    value = copy.deepcopy(base)
    if release_id == value.get("release_id"):
        raise ValueError("a new release identity is required")
    if product_id in value.get("products", {}):
        raise ValueError("existing products are not replaced; use a new product_id")
    if len(value.get("products", {})) >= 4:
        raise ValueError("product limit reached; review the inventory separately")
    g = copy.deepcopy(grid)
    if g.get("method", "quality_height_v2") != "quality_height_v2":
        raise ValueError("supply an explicit common-height grid, not a horizontal trial")
    g["method"] = "quality_height_v2"
    Grid(**{**g, "levels_m_msl": tuple(g["levels_m_msl"])})
    for name, settings in (x_settings or {}).items():
        if name not in value["stations"] or value["stations"][name]["band"] != "X":
            raise ValueError("X settings require an existing X station: " + name)
        if not isinstance(settings, dict) or set(settings) - ALLOWED_X:
            raise ValueError("only explicit attenuation settings may be changed")
        current = value["stations"][name].get("x_qc", {})
        value["stations"][name]["x_qc"] = {**current, **settings}
        XProfile(**value["stations"][name]["x_qc"])
    value["release_id"] = release_id
    value.setdefault("products", {})[product_id] = g
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    network = Network.from_bytes(raw)
    warnings = []
    for name, station in network.stations.items():
        if not station.enabled:
            warnings.append(name + ": not enabled for quality-height products; unchanged")
        if not station.geometry_verified:
            warnings.append(name + ": geometry remains unverified; not made operational")
        if not station.calibration_verified:
            warnings.append(name + ": calibration remains unverified; v2 will not trust its gates")
        if station.band == "X":
            warnings.append(name + ": per-observation path/radome/liquid evidence is checked at execution, not inferred here")
    return raw, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--grid", type=Path, required=True, help="Explicit metre CRS grid with common MSL levels")
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--x-settings", type=Path, help="Optional object: station ID -> reviewed attenuation settings")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw, warnings = prepare(read_document(args.network), read_document(args.grid),
            args.product_id, args.release_id, read_document(args.x_settings) if args.x_settings else None)
        # Exclusive create: no active network or earlier release is overwritten.
        with args.output.open("xb") as stream:
            stream.write(raw)
        print(json.dumps({"network_sha256": hashlib.sha256(raw).hexdigest(),
                          "product_id": args.product_id, "warnings": warnings,
                          "deployed": False, "qpe_enabled": False}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("Cannot create release: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
