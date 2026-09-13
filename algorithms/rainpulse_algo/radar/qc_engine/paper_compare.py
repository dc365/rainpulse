"""Frozen V3--AFL--RDD--fusion comparison, with an explicit unavailable RDD state.

Uses the existing QC review JSON/UI and actual context preparation. No model
reruns, remote I/O, database writes, publication or automatic promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from ..qc import apply_basic_qc, load_qc_profile
from ..qc_input import open_qc_input
from ..qc_metrics import compare_measurement_actions
from ..qc_zarr import build_validated_qc_zarr_store
from .adapters import adapt_sweep
from .context import prepare_open_source_inputs
from .decision import Action
from .rdd_reference import load_rdd_reference
from .replay import LocalArtifacts, NoNetwork
from .review import _labels, _safe_json

ROOT = Path(__file__).resolve().parents[4]


def _frozen(root: Path, spec: dict) -> Path:
    path = (root / spec["path"]).resolve()
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != spec["sha256"]:
        raise ValueError("frozen file missing or checksum changed")
    return path


def _longest(mask, dr):
    maximum = 0
    for row in mask:
        edges = np.diff(np.r_[False, row, False].astype("int8"))
        lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
        if lengths.size:
            maximum = max(maximum, int(lengths.max()))
    return maximum * dr


def compare_task(manifest: dict, root: Path, *, inspect_rays=(0,)):
    """Return review and computed bundles; all source paths/hashes are frozen."""
    if manifest.get("schema_version") != "rainpulse.qc-paper-comparison.v1":
        raise ValueError("unsupported paper comparison manifest")
    if manifest.get("partition") not in {"development", "validation"} or not manifest.get(
        "process_id"
    ):
        raise ValueError("comparison requires a declared partition and weather-process identity")
    if not 1 <= len(inspect_rays) <= 12 or len(set(inspect_rays)) != len(inspect_rays):
        raise ValueError("inspect 1..12 distinct native rays")
    task_path = _frozen(root, manifest["task"])
    request = RadarQCRequested.model_validate_json(task_path.read_text())
    flags = _frozen(root, manifest["flags"])
    v3_path = _frozen(root, manifest["v3_profile"])
    v4_path = _frozen(root, manifest["fusion_profile"])
    v3, v4 = load_qc_profile(v3_path, flags), load_qc_profile(v4_path, flags)
    if v3.pipeline_version != "qc-opensource-3.0.0" or v4.pipeline_version != "qc-opensource-4.0.0":
        raise ValueError("comparison requires frozen V3 and fusion V4 profiles")
    for key, expected in (
        ("qc_profile", v3.profile_version),
        ("qc_pipeline_version", v3.pipeline_version),
        ("flag_definition_version", v3.flag_definition_version),
        ("qc_profile_sha256", manifest["v3_profile"]["sha256"]),
    ):
        if getattr(request.payload, key) != expected:
            raise ValueError(f"frozen task {key} does not select the V3 baseline")
    # The only allowed differences in this controlled comparison are algorithm identity
    # and the literature block. No simultaneous retuning of V3 or context is hidden.
    left, right = v3.model_dump(), v4.model_dump()
    for key in ("profile_version", "pipeline_version", "decision_version", "literature"):
        left.pop(key, None)
        right.pop(key, None)
    if left != right:
        raise ValueError("V3 and fusion comparison must share all non-paper parameters")
    definitions = manifest["artifacts"]
    if not 1 <= len(definitions) <= 7:
        raise ValueError("comparison context exceeds seven-artifact budget")
    reader = LocalArtifacts(root, definitions)
    expected_uris = {
        request.payload.input_uri,
        *[x.input_uri for x in request.payload.temporal_context],
        *[x.input_uri for x in request.payload.cross_radar_context],
    }
    if set(reader.definitions) != expected_uris:
        raise ValueError("comparison artifacts do not match the frozen task")
    normalized = reader.load(request.payload.input_uri)
    normalized_view = open_qc_input(normalized)
    for key in ("scan_id", "radar_id", "radar_config_version"):
        if str(normalized_view.root.attrs.get(key)) != str(getattr(request.payload, key)):
            raise ValueError(f"current {key} differs from frozen comparison task")
    prepared, context = prepare_open_source_inputs(
        request, normalized, v3, NoNetwork(), reader=reader
    )
    view = prepared["input_view"]
    input_hash = artifact_sha256(normalized)
    references = {}
    for name, definition in manifest.get("rdd_references", {}).items():
        if name not in view.root:
            raise ValueError("RDD reference refers to an absent sweep")
        native = adapt_sweep(view.root, name, v4)
        references[name] = load_rdd_reference(
            root / definition["path"],
            metadata_sha256=definition["sha256"],
            input_sha256=input_hash,
            native=native,
            sweep_name=name,
        )
    # Same actual prepared raw/context input for both algorithms: no future/leakage advantage.
    # External RDD is included as a comparison-only structure reference, not an extra
    # independent polarization observation and not silently included in online tasks.
    outputs, elapsed = {}, {}
    for method, profile in (("v3", v3), ("paper_fusion_v4", v4)):
        started = time.perf_counter()
        extra = {"paper_references_by_sweep": references} if method == "paper_fusion_v4" else {}
        outputs[method] = apply_basic_qc(
            normalized, profile, **prepared, **extra, created_at=request.occurred_at
        )
        elapsed[method] = (time.perf_counter() - started) * 1000
    case = {
        "case_id": manifest.get("case_id", str(request.payload.scan_id)),
        "radar_id": request.payload.radar_id,
        "scan_id": str(request.payload.scan_id),
        "input_sha256": input_hash,
        "partition": manifest["partition"],
        "process_id": manifest["process_id"],
        "observation_time_utc": view.root.attrs.get("volume_end_time_utc"),
        "elapsed_ms": elapsed,
        "context_mode": "identical_frozen_v3_worker_context",
        "context": context,
        "profiles": {
            key: {
                "profile_version": result.profile.profile_version,
                "pipeline_version": result.profile.pipeline_version,
                "parameters_hash": result.profile.parameters_hash,
            }
            for key, result in outputs.items()
        },
        "sweeps": [],
    }
    bundles = {}
    for method, output in outputs.items():
        # Compare bundles are isolated and NEVER emitted as business completions.
        objects, validation = build_validated_qc_zarr_store(
            normalized,
            output,
            asset_id=f"paper-review-{method}-{request.job_id}",
            normalized_volume_uri=request.payload.input_uri,
            provenance={"context_fingerprint": context["context_fingerprint"]},
        )
        bundles[method] = objects
        case.setdefault("outputs", {})[method] = {
            "artifact_sha256": artifact_sha256(objects),
            "validation": validation,
        }
    exported_values = 0
    for index, baseline in enumerate(outputs["v3"].sweeps):
        final = outputs["paper_fusion_v4"].sweeps[index]
        shape, name = baseline.dbzh_raw.shape, baseline.name
        observed = baseline.valid_mask == 1
        labels = _labels(manifest, name, shape, root)
        group = view.root[name]
        arrays = final.optional_qc_fields
        masks = {
            "v3": baseline.optional_qc_fields["QC_ACTION"] == Action.REJECT,
            "afl_parameterized": arrays["AFL_CANDIDATE_MASK"] == 1,
            "afl_local": arrays["AFL_LOCAL_CANDIDATE_MASK"] == 1,
            "rdd_reference": arrays["RDD_CANDIDATE_MASK"] == 1,
            "paper_fusion_v4": arrays["QC_ACTION"] == Action.REJECT,
        }
        metadata = outputs["paper_fusion_v4"].summary["sweeps"][name]["literature"]
        record = {
            "sweep": name,
            "shape": list(shape),
            "observed_gates": int(observed.sum()),
            "label_status": "available" if labels is not None else "unlabeled_no_skill_claim",
            "paper_sources": metadata,
            "methods": {},
            "radials": [],
        }
        common = np.zeros(shape, bool)
        # Fixed candidate-review domain is shared, not selected separately by each output.
        for mask in masks.values():
            common |= mask
        dr = float(np.median(np.diff(group["range"][:])))
        for method, mask in masks.items():
            absent = method == "rdd_reference" and metadata["rdd"]["status"].startswith(
                "not_executed"
            )
            entry = {
                "status": "not_executed_no_verified_reference" if absent else "computed",
                "mask_semantics": "final_reject"
                if method in outputs
                else "raw_candidate_not_final_rejection",
                "marked_observed_gates": int((mask & observed).sum()) if not absent else None,
                "measurement_metrics": None,
            }
            if absent:
                record["methods"][method] = entry
                continue
            available = observed
            if method not in outputs:
                prefix = {
                    "afl_parameterized": "AFL",
                    "afl_local": "AFL_LOCAL",
                    "rdd_reference": "RDD",
                }[method]
                available = arrays[prefix + "_AVAILABLE_MASK"] == 1
                entry["available_observed_gates"] = int(available.sum())
                entry["unavailable_observed_gates"] = int((observed & ~available).sum())
            if labels is not None:
                # Unavailable support remains in the fixed denominator, never an easy subset.
                entry["measurement_metrics"] = compare_measurement_actions(
                    labels, observed, mask, baseline.dbzh_raw
                )
            if method in outputs:
                out = outputs[method].sweeps[index].optional_qc_fields
                eligible = out["QPE_ELIGIBLE_MASK"] == 1
                entry.update(
                    quarantined_observed_gates=int((out["RFI_QUARANTINE_MASK"] == 1).sum()),
                    quantitative_eligible_gates=int(eligible.sum()),
                    retained_candidate_maximum_continuous_m=_longest(common & eligible, dr),
                    residual_domain_semantics="shared_union_candidates_not_independent_truth",
                )
                if labels is not None:
                    entry["withheld_measurement_metrics"] = compare_measurement_actions(
                        labels, observed, ~eligible, baseline.dbzh_raw
                    )
            record["methods"][method] = entry
        for ray in inspect_rays:
            if not 0 <= ray < shape[0]:
                raise ValueError("inspection index is outside original ray geometry")
            variants = {}
            for method in masks:
                if method == "rdd_reference" and record["methods"][method]["status"].startswith(
                    "not_executed"
                ):
                    continue
                out = outputs[method].sweeps[index] if method in outputs else final
                fields = {
                    "DBZH_RAW": out.dbzh_raw[ray],
                    **{
                        key: value[ray]
                        for key, value in out.optional_qc_fields.items()
                        if key.startswith(("AFL_", "RDD_", "PAPER_", "RFI_"))
                        or key
                        in {
                            "DBZH_USABLE",
                            "QC_ACTION",
                            "QC_DECISION_REASON",
                            "QPE_ELIGIBLE_MASK",
                            "RHOHV_RAW",
                            "ZDR_RAW",
                            "PHIDP_RAW",
                            "SNR_RAW",
                            "METEO_SCORE",
                        }
                    },
                }
                if method not in outputs:
                    # A candidate preview isn't a published rainfall field: no synthetic action.
                    fields = {
                        key: value
                        for key, value in fields.items()
                        if key.startswith(("DBZH_RAW", "AFL_", "RDD_"))
                    }
                    fields["CANDIDATE_MASK"] = masks[method][ray].astype("uint8")
                    fields["CANDIDATE_PREVIEW_DBZH"] = np.where(
                        observed[ray] & ~masks[method][ray], out.dbzh_raw[ray], np.nan
                    )
                exported_values += sum(np.size(v) for v in fields.values())
                if exported_values > 2_000_000:
                    raise ValueError("report numeric budget exceeded; inspect fewer native rays")
                variants[method] = fields
            record["radials"].append(
                {
                    "ray_index": ray,
                    "azimuth_deg": float(group["azimuth"][ray]),
                    "range_m": group["range"][:],
                    "variants": variants,
                    "fields": variants["v3"],
                }
            )
        case["sweeps"].append(record)
    report = _safe_json(
        {
            "schema_version": "rainpulse.qc-review.v1",
            "operational_eligible": False,
            "acceptance_status": "engineering_comparison_not_real_weather_acceptance",
            "manifest_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True).encode()
            ).hexdigest(),
            "limitations": [
                (
                    "AFL implements published formulas with digitized membership "
                    "knots and explicit assumptions."
                ),
                "AFL-local is a separate adaptation, not a faithful full-ray reference.",
                (
                    "RDD is an external-reference boundary; absent results are not "
                    "scored as negative detections."
                ),
                (
                    "AFL and RDD share reflectivity: agreement is not two independent "
                    "physical evidence families."
                ),
                (
                    "Same frozen V3 context for all comparison outputs; no "
                    "independent weather/gauge skill implied."
                ),
            ],
            "cases": [case],
        }
    )
    return report, bundles


def run_comparison(
    manifest_path: Path,
    output_path: Path,
    *,
    inspect_rays=(0,),
    save_bundles=False,
    save_images=False,
):
    manifest_path = manifest_path.resolve()
    output_path = output_path.absolute()
    if output_path.exists():
        raise ValueError("comparison output exists; never overwrite a frozen result")
    if manifest_path.stat().st_size > 2_000_000:
        raise ValueError("comparison manifest exceeds resource budget")
    manifest = json.loads(manifest_path.read_text())
    report, bundles = compare_task(manifest, manifest_path.parent, inspect_rays=inspect_rays)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock = output_path.with_name(output_path.name + ".lock")
    with lock.open("x"):
        pass
    temp = Path(tempfile.mkdtemp(prefix="paper-comparison-", dir=output_path.parent))
    try:
        if output_path.exists():
            raise ValueError("comparison output appeared while acquiring lock")
        content = json.dumps(report, ensure_ascii=False, allow_nan=False)
        if len(content.encode()) > 50 * 1024 * 1024:
            raise ValueError("comparison report exceeds 50 MiB; inspect fewer rays")
        (temp / "report.json").write_text(content)
        if save_bundles:
            for method, objects in bundles.items():
                for key, value in objects.items():
                    if Path(key).is_absolute() or ".." in Path(key).parts:
                        raise ValueError("unsafe generated artifact object path")
                    p = temp / method / "qc.zarr" / key
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(value)
        if save_images:
            from .paper_images import write_comparison_images

            write_comparison_images(bundles, report, temp / "images")
        os.rename(temp, output_path)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
        lock.unlink()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inspect-ray", type=int, action="append")
    parser.add_argument("--save-bundles", action="store_true")
    parser.add_argument("--save-images", action="store_true")
    args = parser.parse_args()
    run_comparison(
        args.manifest,
        args.output,
        inspect_rays=args.inspect_ray or (0,),
        save_bundles=args.save_bundles,
        save_images=args.save_images,
    )
    print(json.dumps({"output": str(args.output), "published": False}))


if __name__ == "__main__":
    main()
