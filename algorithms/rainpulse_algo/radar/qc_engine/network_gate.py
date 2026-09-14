"""Worst-group, fixed-denominator acceptance diagnostics; never automatic promotion."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .segments import runs


class NetworkLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    minimum_weather_gates: int = Field(default=100, ge=1)
    minimum_interference_gates: int = Field(default=100, ge=1)
    minimum_processes: int = Field(default=2, ge=1)
    minimum_recall: float = Field(default=0.9, ge=0, le=1)
    maximum_false_reject: float = Field(default=0.01, ge=0, le=1)
    maximum_strong_weather_loss: float = Field(default=0.005, ge=0, le=1)
    maximum_weather_withheld: float = Field(default=0.02, ge=0, le=1)
    maximum_recall_regression: float = Field(default=0, ge=0, le=1)
    maximum_weather_loss_increase: float = Field(default=0.001, ge=0, le=1)


def counts(labels, domain, reject, eligible, dr, reflectivity_dbz=None):
    if reflectivity_dbz is not None and reflectivity_dbz.shape != domain.shape:
        raise ValueError("reflectivity/label geometry mismatch")
    if not np.isfinite(dr) or dr <= 0:
        raise ValueError("positive measured gate spacing required")
    if any(x.shape != domain.shape for x in (labels, reject, eligible)):
        raise ValueError("network score geometry mismatch")
    if not np.isin(labels, [0, 1, 2, 3]).all():
        raise ValueError("unknown label; 0 unknown,1 trusted weather,2 pollution,3 mixed")
    weather = domain & (labels == 1)
    interference = domain & (labels == 2)
    mixed = domain & (labels == 3)
    strong = (
        np.zeros(domain.shape, bool)
        if reflectivity_dbz is None
        else weather & (reflectivity_dbz >= 35)
    )
    residual = interference & eligible
    max_length = max((int(b - a) * dr for row in residual for a, b in runs(row)), default=0.0)
    fragments = sum(len(runs(row)) for row in residual)
    maximum_dbz = (
        float(np.max(reflectivity_dbz[residual]))
        if reflectivity_dbz is not None and residual.any()
        else None
    )
    return dict(
        residual_fragment_count=int(fragments),
        maximum_residual_dbz=maximum_dbz,
        weather=int(weather.sum()),
        interference=int(interference.sum()),
        mixed=int(mixed.sum()),
        uncertain=int((domain & (labels == 0)).sum()),
        strong_weather=int(strong.sum()),
        strong_weather_rejected=int((strong & reject).sum()),
        strong_weather_withheld=int((strong & ~eligible).sum()),
        weather_rejected=int((weather & reject).sum()),
        weather_withheld=int((weather & ~eligible).sum()),
        interference_rejected=int((interference & reject).sum()),
        interference_withheld=int((interference & ~eligible).sum()),
        mixed_withheld=int((mixed & ~eligible).sum()),
        longest_residual_m=float(max_length),
    )


def rates(c):
    def ratio(a, b):
        return c[a] / c[b] if c[b] else None

    return dict(
        interference_recall=ratio("interference_rejected", "interference"),
        interference_withheld_rate=ratio("interference_withheld", "interference"),
        weather_false_reject=ratio("weather_rejected", "weather"),
        weather_withheld=ratio("weather_withheld", "weather"),
        strong_weather_withheld=ratio("strong_weather_withheld", "strong_weather"),
    )


def assess_network(cases, expected_radars, limits: NetworkLimits):
    if not expected_radars or len(set(expected_radars)) != len(expected_radars):
        raise ValueError("declare distinct required radars; an empty network cannot pass")
    pairs = {tuple(c.get("comparison_methods", ("v4", "v5"))) for c in cases}
    if len(pairs) > 1:
        raise ValueError("network assessment cannot mix baseline/candidate versions")
    pair = next(iter(pairs), ("v4", "v5"))
    if pair not in {("v4", "v5"), ("v5", "v6")}:
        raise ValueError("unsupported network comparison versions")
    baseline_name, candidate_name = pair
    aggregate = defaultdict(
        lambda: {
            baseline_name: defaultdict(float),
            candidate_name: defaultdict(float),
            "processes": set(),
            "weather_processes": set(),
            "interference_processes": set(),
            "rows": 0,
        }
    )
    seen = set()
    for case in cases:
        identity = (case["radar_id"], case["scan_id"])
        if identity in seen:
            raise ValueError("duplicate physical scan in network assessment")
        seen.add(identity)
        if case["partition"] != "validation" or case["data_kind"] != "real":
            continue
        for row in case["evaluation_rows"]:
            group = (case["radar_id"], row["range_band"], row["capability_code"])
            entry = aggregate[group]
            entry["processes"].add(case["process_id"])
            for kind in ("weather", "interference"):
                if row[candidate_name][kind] > 0:
                    entry[kind + "_processes"].add(case["process_id"])
            entry["rows"] += 1
            for method in pair:
                for key, val in row[method].items():
                    if key == "maximum_residual_dbz":
                        before = entry[method].get(key)
                        if val is not None:
                            entry[method][key] = val if before is None else max(before, val)
                        elif key not in entry[method]:
                            entry[method][key] = None
                    elif key == "longest_residual_m":
                        entry[method][key] = max(entry[method][key], val)
                    else:
                        entry[method][key] += val
    groups = []
    for (radar, band, cap), value in sorted(aggregate.items()):
        old, new = rates(value[baseline_name]), rates(value[candidate_name])
        failures = []
        if new["weather_false_reject"] is not None:
            if new["weather_false_reject"] > limits.maximum_false_reject:
                failures.append("weather_false_reject")
            if new["weather_withheld"] > limits.maximum_weather_withheld:
                failures.append("weather_withheld")
            if (
                new["weather_withheld"]
                > old["weather_withheld"] + limits.maximum_weather_loss_increase
            ):
                failures.append("weather_coverage_regression")
        if new["interference_recall"] is not None:
            if new["interference_recall"] < limits.minimum_recall:
                failures.append("interference_recall")
            if (
                new["interference_recall"] + limits.maximum_recall_regression
                < old["interference_recall"]
            ):
                failures.append("interference_recall_regression")
        if (
            new["strong_weather_withheld"] is not None
            and new["strong_weather_withheld"] > limits.maximum_strong_weather_loss
        ):
            failures.append("strong_weather_coverage_loss")
        enough = (
            value[candidate_name]["weather"] >= limits.minimum_weather_gates
            and value[candidate_name]["interference"] >= limits.minimum_interference_gates
            and len(value["weather_processes"]) >= limits.minimum_processes
            and len(value["interference_processes"]) >= limits.minimum_processes
        )
        status = "FAIL" if failures else ("PASS" if enough else "INSUFFICIENT")
        groups.append(
            dict(
                radar_id=radar,
                range_band=band,
                capability_code=cap,
                status=status,
                failures=failures,
                process_count=len(value["processes"]),
                weather_process_count=len(value["weather_processes"]),
                interference_process_count=len(value["interference_processes"]),
                **{
                    baseline_name + "_counts": dict(value[baseline_name]),
                    candidate_name + "_counts": dict(value[candidate_name]),
                    baseline_name: old,
                    candidate_name: new,
                },
            )
        )
    missing = sorted(set(expected_radars) - {x["radar_id"] for x in groups})
    status = (
        "FAIL"
        if any(x["status"] == "FAIL" for x in groups)
        else (
            "PASS"
            if groups and not missing and all(x["status"] == "PASS" for x in groups)
            else "INSUFFICIENT"
        )
    )
    return dict(
        schema_version="rainpulse.qc-network-gate.v1",
        status=status,
        operational_eligible=False,
        groups=groups,
        comparison_methods=list(pair),
        missing_radars=missing,
        limits=limits.model_dump(),
        note="Only declared real validation cases enter gates. "
        "Unlabeled/synthetic/development cases cannot PASS. "
        "Quarantine is weather coverage loss, not confirmed interference recall.",
    )
