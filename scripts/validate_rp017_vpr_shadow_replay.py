#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ALGORITHMS_ROOT = REPOSITORY_ROOT / "algorithms"
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from rainpulse_algo.radar.vpr_shadow_replay import (  # noqa: E402
    DEFAULT_FLAG_DEFINITIONS_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PROFILE_PATH,
    run_vpr_shadow_replay_validation,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay the fixed RP-017 synthetic VPR-QPE shadow manifest and emit a deterministic JSON report."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to the fixed RP-017 replay manifest JSON.",
    )
    parser.add_argument(
        "--qpe-profile",
        type=Path,
        default=DEFAULT_PROFILE_PATH,
        help="Path to the mounted VPR-capable QPE profile.",
    )
    parser.add_argument(
        "--flag-definitions",
        type=Path,
        default=DEFAULT_FLAG_DEFINITIONS_PATH,
        help="Path to the QC flag-definition YAML used to resolve VPR flag masks.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional path to write the JSON report.",
    )
    args = parser.parse_args()

    report = run_vpr_shadow_replay_validation(
        args.manifest,
        profile_path=args.qpe_profile,
        flag_definitions_path=args.flag_definitions,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if int(report["failed_case_count"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())