#!/usr/bin/env python3
"""Compare PBB/CBB geometry with old and converted radar antenna heights."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np

from rainpulse_algo.radar.ancillary import load_source
from rainpulse_algo.radar.blockage import calculate_polar_blockage
from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.dem import VerifiedDEMTileStore
from rainpulse_algo.radar.grid_profile import load_radar_grid_profile


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def blockage_metrics(blockage) -> dict:
    support = blockage.support_mask == 1
    partial = blockage.partial[support]
    cumulative = blockage.cumulative[support]
    finite = np.isfinite(partial) & np.isfinite(cumulative)
    partial = partial[finite]
    cumulative = cumulative[finite]
    return {
        "supported_gates": int(support.sum()),
        "finite_gates": int(finite.sum()),
        "pbb_mean": float(partial.mean()) if partial.size else None,
        "pbb_max": float(partial.max()) if partial.size else None,
        "cbb_mean": float(cumulative.mean()) if cumulative.size else None,
        "cbb_max": float(cumulative.max()) if cumulative.size else None,
        "flag_gates": int(np.count_nonzero(cumulative > 0.10)),
        "severe_gates": int(np.count_nonzero(cumulative > 0.70)),
        "flag_rate": float(np.mean(cumulative > 0.10)) if cumulative.size else None,
        "severe_rate": float(np.mean(cumulative > 0.70)) if cumulative.size else None,
    }


def compare_arrays(old, new) -> dict:
    support = (old.support_mask == 1) & (new.support_mask == 1)
    comparable = (
        support
        & np.isfinite(old.partial)
        & np.isfinite(new.partial)
        & np.isfinite(old.cumulative)
        & np.isfinite(new.cumulative)
    )
    pbb_delta = np.abs(new.partial[comparable] - old.partial[comparable])
    cbb_delta = np.abs(new.cumulative[comparable] - old.cumulative[comparable])
    old_metrics = blockage_metrics(old)
    new_metrics = blockage_metrics(new)
    return {
        "old": old_metrics,
        "new": new_metrics,
        "comparable_gates": int(comparable.sum()),
        "changed_pbb_gates": int(np.count_nonzero(pbb_delta > 1e-6)),
        "changed_cbb_gates": int(np.count_nonzero(cbb_delta > 1e-6)),
        "maximum_absolute_pbb_delta": float(pbb_delta.max()) if pbb_delta.size else 0.0,
        "maximum_absolute_cbb_delta": float(cbb_delta.max()) if cbb_delta.size else 0.0,
        "flag_rate_delta": (
            new_metrics["flag_rate"] - old_metrics["flag_rate"]
            if new_metrics["flag_rate"] is not None and old_metrics["flag_rate"] is not None
            else None
        ),
        "severe_rate_delta": (
            new_metrics["severe_rate"] - old_metrics["severe_rate"]
            if new_metrics["severe_rate"] is not None and old_metrics["severe_rate"] is not None
            else None
        ),
    }


def maximum_absolute_delta(comparisons: list[dict], key: str) -> float:
    return max(abs(item[key]) for item in comparisons if item[key] is not None)


def comparison_summary(comparisons: list[dict]) -> dict:
    maximum_pbb_delta = max(item["maximum_absolute_pbb_delta"] for item in comparisons)
    maximum_cbb_delta = max(item["maximum_absolute_cbb_delta"] for item in comparisons)
    maximum_flag_rate_delta = maximum_absolute_delta(comparisons, "flag_rate_delta")
    maximum_severe_rate_delta = maximum_absolute_delta(comparisons, "severe_rate_delta")
    return {
        "maximum_absolute_pbb_delta": maximum_pbb_delta,
        "maximum_absolute_cbb_delta": maximum_cbb_delta,
        "maximum_absolute_flag_rate_delta": maximum_flag_rate_delta,
        "maximum_absolute_severe_rate_delta": maximum_severe_rate_delta,
    }


def validate_pair(old_config, new_config) -> None:
    old_site = old_config.site
    new_site = new_config.site
    for key in ("longitude_deg", "latitude_deg"):
        if float(old_site[key]) != float(new_site[key]):
            raise ValueError(f"{old_config.radar_id} site {key} changed")
    for key in ("beam_width_vertical_deg", "beam_width_deg"):
        if old_config.hardware.get(key) != new_config.hardware.get(key):
            raise ValueError(f"{old_config.radar_id} hardware {key} changed")
    if old_config.scan != new_config.scan:
        raise ValueError(f"{old_config.radar_id} scan geometry changed")
    if old_config.ancillary != new_config.ancillary:
        raise ValueError(f"{old_config.radar_id} ancillary identity changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-config-dir", type=Path, required=True)
    parser.add_argument("--new-config-dir", type=Path, required=True)
    parser.add_argument("--grid-profile", type=Path, required=True)
    parser.add_argument("--ancillary-config", type=Path, required=True)
    parser.add_argument("--dem-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-range-m", type=float, default=75000.0)
    args = parser.parse_args()

    profile = load_radar_grid_profile(args.grid_profile)
    source = load_source(args.ancillary_config)
    terrain = VerifiedDEMTileStore(
        source,
        args.dem_root,
        expected_asset_version=profile.dem.asset_version,
        expected_config_version=profile.ancillary_config_version,
    )
    records = []
    correction_comparisons = []
    uncertainty_comparisons = []
    old_paths = sorted(args.old_config_dir.glob("z959*.yaml"))
    for old_path in old_paths:
        new_path = args.new_config_dir / old_path.name
        if not new_path.is_file():
            raise FileNotFoundError(new_path)
        old_config = load_radar_config(old_path)
        new_config = load_radar_config(new_path)
        if old_config.radar_id != new_config.radar_id:
            raise ValueError(f"radar identity differs for {old_path.name}")
        validate_pair(old_config, new_config)
        elevations = [float(value) for value in old_config.scan["expected_elevations_deg"]]
        azimuth = np.arange(0.0, 360.0, float(old_config.scan["azimuth_resolution_deg"]), dtype="float64")
        gate_count = int(args.maximum_range_m // float(old_config.scan["range_gate_m"]))
        ranges = np.arange(1, gate_count + 1, dtype="float64") * float(old_config.scan["range_gate_m"])
        required = np.full(azimuth.size, ranges.size - 1, dtype="int32")
        sweep_records = []
        for elevation in elevations:
            common = {
                "azimuth_deg": azimuth,
                "elevation_deg": np.full(azimuth.size, elevation, dtype="float64"),
                "range_m": ranges,
                "required_max_gate": required,
                "radar_longitude_deg": float(old_config.site["longitude_deg"]),
                "radar_latitude_deg": float(old_config.site["latitude_deg"]),
                "vertical_beam_width_deg": float(old_config.hardware["beam_width_vertical_deg"]),
                "beam_config": profile.beam_geometry,
                "blockage_config": profile.blockage,
                "terrain": terrain,
            }
            old_blockage = calculate_polar_blockage(
                antenna_altitude_m=float(old_config.site["antenna_altitude_m"]), **common
            )
            new_blockage = calculate_polar_blockage(
                antenna_altitude_m=float(new_config.site["antenna_altitude_m"]), **common
            )
            sigma_m = float(new_config.site["altitude_sigma_m"])
            lower_blockage = calculate_polar_blockage(
                antenna_altitude_m=float(new_config.site["antenna_altitude_m"]) - sigma_m,
                **common,
            )
            upper_blockage = calculate_polar_blockage(
                antenna_altitude_m=float(new_config.site["antenna_altitude_m"]) + sigma_m,
                **common,
            )
            correction = compare_arrays(old_blockage, new_blockage)
            lower_uncertainty = compare_arrays(new_blockage, lower_blockage)
            upper_uncertainty = compare_arrays(new_blockage, upper_blockage)
            correction_comparisons.append(correction)
            uncertainty_comparisons.extend((lower_uncertainty, upper_uncertainty))
            sweep_records.append({
                "elevation_deg": elevation,
                "correction_effect": correction,
                "uncertainty_minus_sigma": lower_uncertainty,
                "uncertainty_plus_sigma": upper_uncertainty,
            })
        records.append({
            "radar_id": old_config.radar_id,
            "old_config_version": old_config.config_version,
            "new_config_version": new_config.config_version,
            "old_antenna_altitude_m": float(old_config.site["antenna_altitude_m"]),
            "new_antenna_altitude_m": float(new_config.site["antenna_altitude_m"]),
            "new_altitude_datum_status": new_config.site.get("altitude_datum_status"),
            "old_config_sha256": sha256_file(old_path),
            "new_config_sha256": sha256_file(new_path),
            "sweeps": sweep_records,
        })

    correction_summary = comparison_summary(correction_comparisons)
    uncertainty_summary = comparison_summary(uncertainty_comparisons)
    correction_effect_small = (
        correction_summary["maximum_absolute_flag_rate_delta"] <= 0.002
        and correction_summary["maximum_absolute_severe_rate_delta"] <= 0.002
    )
    uncertainty_within_0p02_percent = (
        uncertainty_summary["maximum_absolute_flag_rate_delta"] <= 0.0002
        and uncertainty_summary["maximum_absolute_severe_rate_delta"] <= 0.0002
    )
    result = {
        "schema": "rainpulse.height-datum-blockage-comparison/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "complete",
        "operational_eligible": False,
        "meaning": "paired_geometry_comparison_not_weather_truth",
        "dem_manifest_sha256": terrain.manifest_sha256,
        "grid_profile_sha256": sha256_file(args.grid_profile),
        "ancillary_config_sha256": sha256_file(args.ancillary_config),
        "maximum_range_m": args.maximum_range_m,
        "correction_effect": correction_summary,
        "conversion_uncertainty": uncertainty_summary,
        "correction_effect_within_0p2_percent": correction_effect_small,
        "uncertainty_within_0p02_percent": uncertainty_within_0p02_percent,
        "within_0p02_percent": uncertainty_within_0p02_percent,
        "radars": records,
    }
    atomic_json(args.output, result)
    print(json.dumps({
        "output": str(args.output),
        "radar_count": len(records),
        "correction_effect": correction_summary,
        "conversion_uncertainty": uncertainty_summary,
        "correction_effect_within_0p2_percent": correction_effect_small,
        "uncertainty_within_0p02_percent": uncertainty_within_0p02_percent,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
