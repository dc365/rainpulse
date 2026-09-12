"""Frozen-input baseline comparison and bounded, exact polar inspection export.

This command never writes business assets, changes a promotion gate, or contacts
an external service. A report without independent labels is NOT a skill report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from rainpulse_algo.worker.object_store import artifact_sha256

from ..qc import apply_basic_qc, load_qc_profile
from ..qc_input import open_qc_input
from ..qc_metrics import compare_measurement_actions
from .decision import Action
from .spike_reference import load_native_spike_reference

ROOT = Path(__file__).resolve().parents[4]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_local_artifact(path: Path, expected_hash: str) -> dict[str, bytes]:
    if not path.is_dir() or path.is_symlink():
        raise ValueError("normalized_zarr must be a local directory, not a link")
    objects = {}
    total = 0
    for file in sorted(path.rglob("*")):
        if file.is_symlink():
            raise ValueError("artifact links are not permitted")
        if not file.is_file() or file.name == "_SUCCESS.json":
            continue
        total += file.stat().st_size
        if total > 2 * 1024**3 or len(objects) >= 200000:
            raise ValueError("review artifact exceeds bounded local-read budget")
        objects[file.relative_to(path).as_posix()] = file.read_bytes()
    if artifact_sha256(objects) != expected_hash:
        raise ValueError("normalized artifact hash differs from the frozen case manifest")
    return objects


def _safe_json(value):
    if isinstance(value, dict):
        return {key: _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, np.ndarray):
        return _safe_json(value.tolist())
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def _labels(case, name, shape, manifest_root):
    definition = case.get("labels", {}).get(name)
    if not definition:
        return None
    path = (manifest_root / definition["path"]).resolve()
    if file_hash(path) != definition["sha256"]:
        raise ValueError("labels have changed since manifest freeze")
    labels = np.load(path, allow_pickle=False)
    if labels.shape != shape or not np.isin(labels, [0, 1, 2, 3]).all():
        raise ValueError("labels must share original ray/gate geometry and known classes")
    return labels


def compare_case(
    case,
    *,
    manifest_root: Path,
    inspect_rays: tuple[int, ...] = (0,),
    legacy_profile: Path | None = None,
):
    if case.get("partition") not in {"development", "validation"}:
        raise ValueError("each case needs an explicit development/validation partition")
    if not case.get("process_id"):
        raise ValueError("each case needs a weather-process identity")
    objects = load_local_artifact(
        (manifest_root / case["normalized_zarr"]).resolve(), case["input_sha256"]
    )
    view = open_qc_input(objects)
    time_value = view.root.attrs.get("volume_end_time_utc")
    created_at = (
        datetime.fromisoformat(time_value.replace("Z", "+00:00"))
        if time_value
        else datetime(1970, 1, 1, tzinfo=UTC)
    )
    profile_path = ROOT / "configs/qc/fujian-qc-opensource-v1.yaml"
    flags_path = ROOT / "configs/qc/flag-definitions-v2.yaml"
    started = time.perf_counter()
    profile = load_qc_profile(profile_path, flags_path)
    baseline = apply_basic_qc(objects, profile, input_view=view, created_at=created_at)
    elapsed = (time.perf_counter() - started) * 1000
    outputs = {"open_source_fusion": baseline}
    runtimes = {"open_source_fusion": elapsed}
    if case.get("experimental_rfi") is True:
        candidate_path = profile_path.with_name("fujian-qc-opensource-rfi-v1.yaml")
        candidate = load_qc_profile(candidate_path, flags_path)
        started = time.perf_counter()
        outputs["experimental_local_rfi"] = apply_basic_qc(
            objects, candidate, input_view=view, created_at=created_at
        )
        runtimes["experimental_local_rfi"] = (time.perf_counter() - started) * 1000
    if legacy_profile is not None:
        legacy = load_qc_profile(legacy_profile, ROOT / "configs/qc/flag-definitions.yaml")
        started = time.perf_counter()
        outputs["legacy"] = apply_basic_qc(objects, legacy, input_view=view, created_at=created_at)
        runtimes["legacy"] = (time.perf_counter() - started) * 1000
    report = {
        "case_id": case["case_id"],
        "partition": case["partition"],
        "process_id": case["process_id"],
        "radar_id": view.root.attrs["radar_id"],
        "scan_id": view.root.attrs.get("scan_id"),
        "input_sha256": case["input_sha256"],
        "observation_time_utc": time_value,
        "libraries": baseline.summary["libraries"],
        "elapsed_ms": runtimes,
        "context_mode": "single_volume_vertical_only_no_external_context",
        "profile_hashes": {
            name: value.summary.get("parameters_hash") for name, value in outputs.items()
        },
        "sweeps": [],
        "operational_eligible": False,
    }
    for index, base in enumerate(baseline.sweeps):
        name, shape = base.name, base.dbzh_raw.shape
        observed = base.valid_mask == 1
        labels = _labels(case, name, shape, manifest_root)
        masks = {
            "pyart_raw_filter": base.optional_qc_fields["OS_PYART_BASELINE_CANDIDATE_MASK"] == 1,
            "wradlib_raw_filter": (base.optional_qc_fields["OS_GABELLA_CANDIDATE_MASK"] == 1)
            | (
                np.isfinite(base.optional_qc_fields["METEO_SCORE"])
                & (base.optional_qc_fields["METEO_SCORE"] <= profile.wradlib.low_meteo_score)
            ),
        }
        for method, result in outputs.items():
            sweep = result.sweeps[index]
            if method == "legacy":
                definitions = result.profile.flag_masks
                cause = sum(
                    int(definitions[x])
                    for x in (
                        "RADIAL_INTERFERENCE",
                        "GROUND_CLUTTER",
                        "SEA_CLUTTER",
                        "ANOMALOUS_PROPAGATION",
                        "HARDWARE_ANOMALY",
                    )
                )
                masks[method] = (sweep.qc_flags & np.uint32(cause)) != 0
            else:
                masks[method] = sweep.optional_qc_fields["QC_ACTION"] == Action.REJECT
        record = {
            "sweep": name,
            "shape": list(shape),
            "observed_gates": int(observed.sum()),
            "label_status": "available" if labels is not None else "unlabeled_no_skill_claim",
            "methods": {},
            "radials": [],
            "evidence": baseline.summary["sweeps"][name],
        }
        for method, mask in masks.items():
            entry = {
                "mask_semantics": "raw_candidate_not_final_rejection"
                if "raw_filter" in method
                else "final_reject",
                "marked_observed_gates": int((mask & observed).sum()),
            }
            if labels is not None:
                entry["measurement_metrics"] = compare_measurement_actions(
                    labels, observed, mask, base.dbzh_raw
                )
            record["methods"][method] = entry
        reference = case.get("spike_reference", {}).get(name)
        if reference:
            item = load_native_spike_reference(
                manifest_root / reference, expected_input_sha256=case["input_sha256"], shape=shape
            )
            native = {"granularity": item["granularity"], "marked_count": int(item["mask"].sum())}
            if item["gate_metrics_permitted"] and labels is not None:
                native["measurement_metrics"] = compare_measurement_actions(
                    labels, observed, item["mask"].astype(bool), base.dbzh_raw
                )
            record["spike_native_reference"] = native
        else:
            record["spike_native_reference"] = {"status": "not_executed_no_native_reference"}
        group = view.root[name]
        for ray in inspect_rays:
            if not 0 <= ray < shape[0]:
                raise ValueError("inspection ray is outside original native geometry")
            fields = {
                "DBZH_RAW": base.dbzh_raw[ray],
                **{
                    k: v[ray]
                    for k, v in base.optional_qc_fields.items()
                    if k
                    in {
                        "DBZH_USABLE",
                        "QC_ACTION",
                        "QC_DECISION_REASON",
                        "METEO_SCORE",
                        "RFI_CANDIDATE_MASK",
                        "RHOHV_RAW",
                        "ZDR_RAW",
                        "PHIDP_RAW",
                        "KDP_OS",
                        "QPE_ELIGIBLE_MASK",
                    }
                },
            }
            record["radials"].append(
                {
                    "ray_index": ray,
                    "azimuth_deg": float(group["azimuth"][ray]),
                    "range_m": group["range"][:],
                    "fields": fields,
                }
            )
        report["sweeps"].append(record)
    return _safe_json(report)


def run_manifest(manifest_path: Path, output_path: Path, *, inspect_rays=(0,), legacy_profile=None):
    manifest = json.loads(manifest_path.read_text())
    cases = manifest.get("cases", [])
    if manifest.get("schema_version") != "1.0" or not 1 <= len(cases) <= 500:
        raise ValueError("review manifest must contain 1..500 frozen cases")
    if len(inspect_rays) > 12 or len(set(inspect_rays)) != len(inspect_rays):
        raise ValueError("inspect at most 12 distinct native rays per sweep")
    ids = [case["case_id"] for case in cases]
    hashes = [case["input_sha256"] for case in cases]
    if len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes):
        raise ValueError("duplicate case identity or source artifact")
    dev = {c.get("process_id") for c in cases if c.get("partition") == "development"}
    val = {c.get("process_id") for c in cases if c.get("partition") == "validation"}
    if dev & val:
        raise ValueError("development and validation must not share a weather process")
    report = {
        "schema_version": "rainpulse.qc-review.v1",
        "manifest_sha256": file_hash(manifest_path),
        "operational_eligible": False,
        "acceptance_status": "review_only_no_automatic_promotion",
        "limitations": [
            "Single-volume baseline; external context is not replayed by this command.",
            "Raw-filter candidate masks and final decisions are labeled separately.",
            "Native SPIKE is included only from a verified external execution.",
            "No independent gauge, weather-process or throughput acceptance is implied.",
        ],
        "cases": [],
    }
    for case in cases:
        report["cases"].append(
            compare_case(
                case,
                manifest_root=manifest_path.parent,
                inspect_rays=tuple(inspect_rays),
                legacy_profile=legacy_profile,
            )
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    )
    temporary.replace(output_path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inspect-ray", type=int, action="append", default=None)
    parser.add_argument("--legacy-profile", type=Path)
    args = parser.parse_args()
    report = run_manifest(
        args.manifest,
        args.output,
        inspect_rays=args.inspect_ray or (0,),
        legacy_profile=args.legacy_profile,
    )
    print(
        json.dumps(
            {
                "cases": len(report["cases"]),
                "report": str(args.output),
                "operational_eligible": False,
            }
        )
    )


if __name__ == "__main__":
    main()
