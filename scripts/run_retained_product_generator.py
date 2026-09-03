#!/usr/bin/env python3
"""Run a file-product generator and retain current plus previous versions.

The wrapped command must use the literal ``{staging_root}`` as its output-root
argument. Products are first generated in an isolated sibling directory, then
validated and promoted into the live root. Existing products remain untouched
when generation fails or publishes no bundle.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "algorithms"))

from rainpulse_algo.products.version_retention import (  # noqa: E402
    ProductRetentionError,
    apply_product_retention,
    plan_product_retention,
    retention_report,
    scan_product_versions,
)

STAGING_TOKEN = "{staging_root}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a derived-product generator in staging and retain the newest "
            "versions per cycle"
        )
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--keep-versions", type=int, default=2)
    parser.add_argument("--dry-run-retention", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command or not any(STAGING_TOKEN in value for value in command):
        parser.error(
            "wrapped command must contain {staging_root} in its output-root argument"
        )
    if not 1 <= args.keep_versions <= 10:
        parser.error("--keep-versions must be between 1 and 10")

    output_root = args.output_root.expanduser().resolve()
    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".rainpulse-product-generation-",
        dir=output_root.parent,
    ) as temporary:
        staging_root = Path(temporary) / "products"
        staging_root.mkdir()
        rendered = [
            value.replace(STAGING_TOKEN, str(staging_root))
            for value in command
        ]
        completed = subprocess.run(rendered, check=False)
        if completed.returncode != 0:
            return completed.returncode
        promoted = promote_generated_bundles(staging_root, output_root)

    plan = plan_product_retention(
        output_root,
        keep_versions=args.keep_versions,
    )
    deleted = apply_product_retention(
        plan,
        dry_run=args.dry_run_retention,
    )
    report = retention_report(
        plan,
        dry_run=args.dry_run_retention,
        deleted=deleted,
    )
    report["promoted_bundle_ids"] = promoted
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def promote_generated_bundles(
    staging_root: Path,
    output_root: Path,
) -> list[str]:
    staged = scan_product_versions(staging_root)
    if not staged:
        return []
    output_root.mkdir(parents=True, exist_ok=True)
    promoted: list[str] = []
    for item in staged:
        destination = output_root / item.bundle_id
        if destination.exists():
            existing = {
                version.bundle_id: version
                for version in scan_product_versions(output_root)
            }.get(item.bundle_id)
            if (
                existing is not None
                and existing.manifest_sha256 == item.manifest_sha256
            ):
                shutil.rmtree(item.path)
                promoted.append(item.bundle_id)
                continue
            raise ProductRetentionError(
                f"product bundle identity collision: {destination}"
            )
        os.replace(item.path, destination)
        promoted.append(item.bundle_id)
    return promoted


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProductRetentionError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
