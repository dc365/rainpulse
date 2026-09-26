#!/usr/bin/env python3
"""Summarize runtime-only performance.trace JSON lines, without opening radar data."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

SCHEMA = "rainpulse.performance-ab-v1"


def percentile(values, q):
    return sorted(values)[max(0, math.ceil(len(values) * q) - 1)] if values else None


def summarize(lines, maximum_records=10000):
    roots, stages = {}, {}
    statuses = Counter()
    total = ignored = 0
    for line in lines:
        if len(line) > 2 * 1024**2:
            raise ValueError("log line exceeds 2 MiB input limit")
        try:
            obj = json.loads(line[line.index("{") :])
        except (ValueError, TypeError):
            ignored += 1
            continue
        if obj.get("schema") != SCHEMA or obj.get("event") != "performance.trace":
            ignored += 1
            continue
        if total >= maximum_records:
            raise ValueError("trace limit reached; split the input window")
        total += 1
        statuses[str(obj.get("call_status", "unknown"))] += 1
        for path, value in obj.get("stages", {}).items():
            elapsed = value.get("inclusive_ms")
            if (
                not isinstance(elapsed, (float, int))
                or not math.isfinite(elapsed)
                or elapsed < 0
            ):
                raise ValueError("invalid stage duration")
            if path not in stages and len(stages) >= 2048:
                raise ValueError(
                    "stage identity limit reached; split versions/workloads"
                )
            item = stages.setdefault(
                path,
                {
                    "calls": 0,
                    "inclusive_ms": 0.0,
                    "self_ms": 0.0,
                    "incomplete_self_records": 0,
                    "errors": 0,
                },
            )
            item["calls"] += int(value["count"])
            item["errors"] += int(value["errors"])
            item["inclusive_ms"] += elapsed
            if value.get("self_time_complete") and value.get("self_ms") is not None:
                item["self_ms"] += value["self_ms"]
            else:
                item["incomplete_self_records"] += 1
            if "/" not in path:
                roots.setdefault(path, []).append(elapsed)
    return {
        "schema": "rainpulse.performance-ab-summary-v1",
        "trace_records": total,
        "ignored_lines": ignored,
        "call_status_counts": dict(statuses),
        "root_wall_ms": {
            path: {
                "count": len(v),
                "p50": percentile(v, 0.5),
                "p95": percentile(v, 0.95),
                "p99": percentile(v, 0.99),
                "maximum": max(v),
            }
            for path, v in roots.items()
        },
        "stages": dict(
            sorted(stages.items(), key=lambda pair: pair[1]["self_ms"], reverse=True)
        ),
        "percentile": "nearest_rank",
        "warning": "Do not sum nested inclusive times; returned is not workflow success. Separate cold/warm and execution identities before comparing.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-records", type=int, default=10000)
    args = parser.parse_args()
    if not 1 <= args.maximum_records <= 100000 or args.output.exists():
        parser.error("invalid record limit or output already exists")
    with args.input.open(encoding="utf-8") as source:
        result = summarize(source, args.maximum_records)
    with args.output.open("x", encoding="utf-8") as dest:
        json.dump(result, dest, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
