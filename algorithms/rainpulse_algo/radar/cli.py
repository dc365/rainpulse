from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from .config import load_radar_config
from .fmt import DecodedRadarVolume, decode_fmt_volume
from .zarr_volume import build_zarr_store, validate_zarr_store, write_zarr_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rainpulse-radar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="decode and print a metadata summary")
    _common_arguments(inspect_parser)

    decode_parser = subparsers.add_parser("decode", help="write a normalized polar Zarr")
    _common_arguments(decode_parser)
    decode_parser.add_argument("--output", type=Path, required=True)
    decode_parser.add_argument("--asset-id", type=UUID, required=True)
    decode_parser.add_argument("--source-uri")

    args = parser.parse_args(argv)
    config = load_radar_config(args.config)
    volume = decode_fmt_volume(args.input, config)
    summary = _volume_summary(volume, config.lifecycle)
    if args.command == "inspect":
        print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
        return 0

    source_uri = args.source_uri or args.input.resolve().as_uri()
    objects = build_zarr_store(
        volume,
        config,
        asset_id=args.asset_id,
        source_uri=source_uri,
    )
    write_zarr_store(objects, args.output)
    summary.update(validate_zarr_store(objects))
    summary["output"] = str(args.output)
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)


def _volume_summary(volume: DecodedRadarVolume, lifecycle: str) -> dict[str, object]:
    return {
        "radar_id": volume.site.code.lower(),
        "site_name": volume.site.name,
        "config_lifecycle": lifecycle,
        "input_sha256": volume.input_sha256,
        "volume_start_time_utc": volume.volume_start_time.isoformat(),
        "volume_end_time_utc": volume.volume_end_time.isoformat(),
        "sweep_count": len(volume.sweeps),
        "ray_count": volume.ray_count,
        "fields": list(volume.canonical_fields),
        "warnings": list(volume.warnings),
    }
