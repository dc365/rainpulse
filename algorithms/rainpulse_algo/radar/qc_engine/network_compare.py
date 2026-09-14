"""Read-only adjacent V4/V5, V5/V6 or V6/V6.1 comparison; no publication.

Explicitly choose shared baseline/candidate evidence or per-profile preparation.
Geometry resources are checksum-bound, not implicitly inherited from the host.
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
from .context import prepare_open_source_inputs
from .fingerprints import context_arrays_identity
from .forensic_io import frozen_resources
from .network_gate import NetworkLimits, assess_network, counts
from .network_manifest import NetworkCaseManifest, NetworkManifest
from .paper_compare import _frozen
from .replay import LocalArtifacts, NoNetwork
from .review import _labels, _safe_json


def case_identity(path):
    path = Path(path).resolve()
    spec = NetworkCaseManifest.model_validate_json(path.read_text()).model_dump(mode="json")
    if spec.get("schema_version") != "rainpulse.qc-network-case.v1":
        raise ValueError("not a supported frozen network case")
    if spec.get("partition") not in {"development", "validation"} or not spec.get("process_id"):
        raise ValueError("declare weather process and evaluation partition")
    if spec.get("data_kind") not in {"real", "synthetic"}:
        raise ValueError("declare real versus synthetic input explicitly")
    request = RadarQCRequested.model_validate_json(_frozen(path.parent, spec["task"]).read_text())
    artifacts = spec["artifacts"]
    if not 1 <= len(artifacts) <= 7:
        raise ValueError("current plus at most six context artifacts")
    source = [x for x in artifacts if x["uri"] == request.payload.input_uri]
    if len(source) != 1:
        raise ValueError("current artifact absent or duplicated")
    hashes = tuple(spec[k]["sha256"] for k in ("baseline_profile", "candidate_profile", "flags"))
    return spec, request, source[0]["sha256"], hashes


def compare_case(path, *, inspect_rays=(0,), bundle_dir=None, context_mode="shared_baseline"):
    spec, _, _, _ = case_identity(path)
    with frozen_resources(Path(path).resolve().parent, spec["resource_environment"]) as resources:
        result = _compare_case(
            path, inspect_rays=inspect_rays, bundle_dir=bundle_dir, context_mode=context_mode
        )
        result["frozen_resources"] = resources
        return result


def _compare_case(path, *, inspect_rays, bundle_dir, context_mode):
    if context_mode not in {"shared_baseline", "shared_candidate", "each_profile"}:
        raise ValueError("explicit supported context comparison mode required")
    path = Path(path).resolve()
    root = path.parent
    spec, request, digest, _ = case_identity(path)
    flags = _frozen(root, spec["flags"])
    baseline = load_qc_profile(_frozen(root, spec["baseline_profile"]), flags)
    candidate = load_qc_profile(_frozen(root, spec["candidate_profile"]), flags)
    pair = (baseline.pipeline_version, candidate.pipeline_version)
    if pair == ("qc-opensource-4.0.0", "qc-opensource-5.0.0"):
        baseline_name, candidate_name, addition = "v4", "v5", "cross_radar"
    elif pair == ("qc-opensource-5.0.0", "qc-opensource-6.0.0"):
        baseline_name, candidate_name, addition = "v5", "v6", "residual"
    elif pair == ("qc-opensource-6.0.0", "qc-opensource-6.1.0"):
        baseline_name, candidate_name, addition = "v6", "v61", "residual_repair"
    else:
        raise ValueError("network comparison requires adjacent frozen V4/V5, V5/V6 or V6/V6.1")
    baseline_field = candidate_name.upper()
    for key, value in [
        ("qc_profile", baseline.profile_version),
        ("qc_pipeline_version", baseline.pipeline_version),
        ("flag_definition_version", baseline.flag_definition_version),
        ("qc_profile_sha256", spec["baseline_profile"]["sha256"]),
    ]:
        if getattr(request.payload, key) != value:
            raise ValueError("task does not select the frozen baseline")
    left, right = baseline.model_dump(), candidate.model_dump()
    for key in ("profile_version", "pipeline_version", "decision_version", addition):
        left.pop(key, None)
        right.pop(key, None)
    if left != right:
        raise ValueError("network comparison cannot hide simultaneous baseline retuning")
    reader = LocalArtifacts(root, spec["artifacts"])
    uris = {
        request.payload.input_uri,
        *[x.input_uri for x in request.payload.temporal_context],
        *[x.input_uri for x in request.payload.cross_radar_context],
    }
    if set(reader.definitions) != uris:
        raise ValueError("exact frozen context set required")
    raw = reader.load(request.payload.input_uri)
    view = open_qc_input(raw)
    for key in ("radar_id", "scan_id", "radar_config_version"):
        if str(view.root.attrs.get(key)) != str(getattr(request.payload, key)):
            raise ValueError("task/raw identity mismatch")
    names = {f"sweep_{int(x):03d}" for x in view.root["sweep_number"][:]}
    if set(spec["labels"]) - names:
        raise ValueError("label references an absent native sweep")
    profiles = {baseline_name: baseline, candidate_name: candidate}
    prepared_by_method, contexts, context_elapsed = {}, {}, {}
    prep_names = (
        list(profiles)
        if context_mode == "each_profile"
        else [baseline_name if context_mode == "shared_baseline" else candidate_name]
    )
    for name in prep_names:
        started = time.perf_counter()
        prepared_by_method[name], contexts[name] = prepare_open_source_inputs(
            request, raw, profiles[name], NoNetwork(), reader=reader
        )
        context_elapsed[name] = (time.perf_counter() - started) * 1000
    if len(prep_names) == 1:
        for name in profiles:
            prepared_by_method[name] = prepared_by_method[prep_names[0]]
            contexts[name] = contexts[prep_names[0]]
    context = contexts[baseline_name]
    out = {}
    elapsed = {}
    bundle_receipts = {}
    for method, profile in [(baseline_name, baseline), (candidate_name, candidate)]:
        start = time.perf_counter()
        out[method] = apply_basic_qc(
            raw, profile, **prepared_by_method[method], created_at=request.occurred_at
        )
        elapsed[method] = (time.perf_counter() - start) * 1000
        serialize_start = time.perf_counter()
        bundle, receipt = build_validated_qc_zarr_store(
            raw,
            out[method],
            asset_id=f"network-{method}-{request.job_id}",
            normalized_volume_uri=request.payload.input_uri,
            provenance={
                "context_fingerprint": contexts[method]["context_fingerprint"],
                "radial_context": json.dumps(contexts[method], sort_keys=True),
                "comparison_context_mode": context_mode,
            },
        )
        bundle_receipts[method] = dict(
            sha256=artifact_sha256(bundle),
            validation=receipt,
            serialize_validate_ms=(time.perf_counter() - serialize_start) * 1000,
        )
        if bundle_dir is not None:
            for key, value in bundle.items():
                target = Path(bundle_dir) / method / "qc.zarr" / key
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(value)
    case = dict(
        case_id=spec.get("case_id", str(request.payload.scan_id)),
        radar_id=request.payload.radar_id,
        scan_id=str(request.payload.scan_id),
        input_sha256=digest,
        partition=spec["partition"],
        process_id=spec["process_id"],
        data_kind=spec["data_kind"],
        observation_time_utc=view.root.attrs.get("volume_end_time_utc"),
        elapsed_ms=elapsed,
        context_mode=(
            f"identical_frozen_{baseline_name}_worker_context"
            if context_mode == "shared_baseline"
            else context_mode
        ),
        context_elapsed_ms=context_elapsed,
        context_by_method=contexts,
        context_arrays_by_method={
            name: context_arrays_identity(prepared_by_method[name]["radial_context"])
            for name in profiles
        },
        comparison_methods=[baseline_name, candidate_name],
        context=context,
        outputs=bundle_receipts,
        profiles={
            k: dict(
                profile_version=v.profile.profile_version,
                pipeline_version=v.profile.pipeline_version,
                parameters_hash=v.profile.parameters_hash,
            )
            for k, v in out.items()
        },
        sweeps=[],
        evaluation_rows=[],
    )
    budget = 0
    for old, new in zip(out[baseline_name].sweeps, out[candidate_name].sweeps, strict=True):
        if old.name != new.name:
            raise ValueError("different native cut order")
        name = old.name
        observed = old.valid_mask == 1
        shape = old.dbzh_raw.shape
        if candidate_name == "v61":
            if context_mode != "each_profile":
                old_rejected = old.optional_qc_fields["QC_ACTION"] == 2
                new_rejected = new.optional_qc_fields["QC_ACTION"] == 2
                if np.any(old_rejected & ~new_rejected) or np.any(
                    (new.optional_qc_fields["QPE_ELIGIBLE_MASK"] == 1)
                    & (old.optional_qc_fields["QPE_ELIGIBLE_MASK"] == 0)
                ):
                    raise ValueError("repair unexpectedly restored a frozen V6 measurement")
        elif context_mode != "each_profile":
            if not np.array_equal(
                new.optional_qc_fields[f"{baseline_field}_BASELINE_REJECT_MASK"],
                old.optional_qc_fields["QC_ACTION"] == 2,
            ):
                raise ValueError("embedded baseline differs from frozen baseline")
            for key, expected in (
                (
                    f"{baseline_field}_BASELINE_QUARANTINE_MASK",
                    old.optional_qc_fields["RFI_QUARANTINE_MASK"],
                ),
                (
                    f"{baseline_field}_BASELINE_ELIGIBLE_MASK",
                    old.optional_qc_fields["QPE_ELIGIBLE_MASK"],
                ),
            ):
                if not np.array_equal(new.optional_qc_fields[key], expected):
                    raise ValueError("embedded baseline qualification differs from frozen baseline")
        if not np.array_equal(old.valid_mask, new.valid_mask) or not np.array_equal(
            old.dbzh_raw, new.dbzh_raw, equal_nan=True
        ):
            raise ValueError("comparison changed original measurements")
        labels = _labels(spec, name, shape, root)
        group = view.root[name]
        ranges = group["range"][:]
        dr = float(np.median(np.diff(ranges)))
        record = dict(
            sweep=name,
            shape=list(shape),
            observed_gates=int(observed.sum()),
            label_status="available" if labels is not None else "unlabeled_no_skill_claim",
            methods={},
            radials=[],
            decision_funnel=out[candidate_name].summary["sweeps"][name]["decision_funnel"],
            range_signatures=out[candidate_name].summary["sweeps"][name]["cross_radar"],
            residual_v6=out[candidate_name].summary["sweeps"][name].get("residual_v6"),
        )
        for method, sweep in [(baseline_name, old), (candidate_name, new)]:
            f = sweep.optional_qc_fields
            reject = f["QC_ACTION"] == 2
            eligible = f["QPE_ELIGIBLE_MASK"] == 1
            entry = dict(
                status="computed",
                mask_semantics="final_reject",
                marked_observed_gates=int(reject.sum()),
                quarantined_observed_gates=int(f["RFI_QUARANTINE_MASK"].sum()),
                quarantine_fraction_of_observed=(
                    float(f["RFI_QUARANTINE_MASK"][observed].mean()) if observed.any() else None
                ),
                quantitative_eligible_gates=int(eligible.sum()),
                measurement_metrics=None,
            )
            if labels is not None:
                entry["measurement_metrics"] = compare_measurement_actions(
                    labels, observed, reject, sweep.dbzh_raw
                )
                entry["residual_measurement_counts"] = counts(
                    labels, observed, reject, eligible, dr, reflectivity_dbz=sweep.dbzh_raw
                )
                entry["withheld_measurement_metrics"] = compare_measurement_actions(
                    labels, observed, ~eligible, sweep.dbzh_raw
                )
            record["methods"][method] = entry
        if labels is not None:
            bands = list(candidate.cross_radar.distance_bands_m)
            if bands[-1] <= ranges[-1]:
                bands.append(float(ranges[-1] + dr))
            cap = new.optional_qc_fields["V5_CAPABILITY_CODE"]
            for lo, hi in zip(bands[:-1], bands[1:], strict=True):
                for capability in (1, 2, 3):
                    domain = (
                        observed
                        & (ranges[None, :] >= lo)
                        & (ranges[None, :] < hi)
                        & (cap == capability)
                    )
                    if not domain.any():
                        continue
                    row = dict(sweep=name, range_band=f"{lo:g}-{hi:g}m", capability_code=capability)
                    for method, sweep in [(baseline_name, old), (candidate_name, new)]:
                        f = sweep.optional_qc_fields
                        row[method] = counts(
                            labels,
                            domain,
                            f["QC_ACTION"] == 2,
                            f["QPE_ELIGIBLE_MASK"] == 1,
                            dr,
                            reflectivity_dbz=sweep.dbzh_raw,
                        )
                    case["evaluation_rows"].append(row)
        for ray in inspect_rays:
            if not 0 <= ray < shape[0]:
                raise ValueError("inspect-ray outside this native sweep")
            variants = {}
            for method, sweep in [(baseline_name, old), (candidate_name, new)]:
                f = sweep.optional_qc_fields
                fields = {
                    "DBZH_RAW": sweep.dbzh_raw[ray],
                    **{
                        k: v[ray]
                        for k, v in f.items()
                        if k.startswith(("V5_", "V6_", "V61_", "RFI_", "PAPER_", "AFL_", "OS_POL_"))
                        or k
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
                variants[method] = fields
                budget += sum(np.size(x) for x in fields.values())
            if budget > 2_000_000:
                raise ValueError("numeric report budget exceeded")
            record["radials"].append(
                dict(
                    ray_index=ray,
                    azimuth_deg=float(group["azimuth"][ray]),
                    range_m=ranges,
                    fields=variants[candidate_name],
                    variants=variants,
                )
            )
        case["sweeps"].append(record)
    if artifact_sha256(raw) != digest:
        raise ValueError("an algorithm modified raw inputs")
    return _safe_json(case)


def run_network(
    manifest_path, output, *, inspect_rays=(0,), save_bundles=False, context_mode="shared_baseline"
):
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    spec = NetworkManifest.model_validate_json(manifest_path.read_text()).model_dump(mode="json")
    if spec.get("schema_version") != "rainpulse.qc-network.v1":
        raise ValueError("unknown network schema")
    if not 1 <= len(spec["cases"]) <= 32:
        raise ValueError("network case budget is 1..32")
    if not 1 <= len(inspect_rays) <= 6 or len(set(inspect_rays)) != len(inspect_rays):
        raise ValueError("inspect 1..6 distinct native rays")
    limits = NetworkLimits.model_validate(spec.get("limits", {}))
    paths = []
    identities = set()
    current_hashes = set()
    processes = {}
    asset_splits = {}
    profile_set = None
    for file in spec["cases"]:
        path = _frozen(root, file)
        case, task, digest, hashes = case_identity(path)
        identity = (task.payload.radar_id, str(task.payload.scan_id))
        if identity in identities or digest in current_hashes:
            raise ValueError("duplicate physical scan/input")
        identities.add(identity)
        current_hashes.add(digest)
        if profile_set is not None and profile_set != hashes:
            raise ValueError("retuned profiles inside network suite")
        profile_set = hashes
        process, partition = case["process_id"], case["partition"]
        if process in processes and processes[process] != partition:
            raise ValueError("weather process split leakage")
        processes[process] = partition
        for asset in case["artifacts"]:
            if asset["sha256"] in asset_splits and asset_splits[asset["sha256"]] != partition:
                raise ValueError("input/context asset split leakage")
            asset_splits[asset["sha256"]] = partition
        paths.append(path)
    expected = spec["expected_radars"]
    assess_network([], expected, limits)  # validate requirements before doing work
    output = Path(output).absolute()
    if output.exists():
        raise ValueError("output already exists; choose a new report directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".lock")
    with lock.open("x"):
        pass
    tmp = Path(tempfile.mkdtemp(prefix="qc-network-", dir=output.parent))
    try:
        cases = [
            compare_case(
                p,
                inspect_rays=inspect_rays,
                bundle_dir=tmp / f"case-{i:02}" if save_bundles else None,
                context_mode=context_mode,
            )
            for i, p in enumerate(paths)
        ]
        gate = assess_network(cases, expected, limits)
        report = dict(
            schema_version="rainpulse.qc-review.v1",
            operational_eligible=False,
            cases=cases,
            manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            network_gate=gate,
            limitations=[
                "Engineering comparison, not automatic operational promotion.",
                (
                    f"Explicit context mode: {context_mode}; current code path, "
                    "not proof of historical equivalence."
                ),
                "AFL retains explicit engineering approximations; native RDD/SWAN not implemented.",
                "Unverified plateau is quarantined, never declared a verified saturation source.",
            ],
        )
        content = json.dumps(report, ensure_ascii=False, allow_nan=False)
        if len(content.encode()) > 50 * 1024 * 1024:
            raise ValueError("network JSON exceeds 50 MiB")
        (tmp / "report.json").write_text(content)
        (tmp / "network-gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2))
        if output.exists():
            raise ValueError("output appeared while comparing")
        os.rename(tmp, output)
        return report
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
        lock.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--inspect-ray", type=int, action="append")
    p.add_argument("--save-bundles", action="store_true")
    p.add_argument(
        "--context-mode",
        choices=["shared_baseline", "shared_candidate", "each_profile"],
        default="shared_baseline",
    )
    a = p.parse_args()
    report = run_network(
        a.manifest,
        a.output,
        inspect_rays=a.inspect_ray or (0,),
        save_bundles=a.save_bundles,
        context_mode=a.context_mode,
    )
    print(
        json.dumps(
            {"output": str(a.output), "gate": report["network_gate"]["status"], "published": False}
        )
    )


if __name__ == "__main__":
    main()
