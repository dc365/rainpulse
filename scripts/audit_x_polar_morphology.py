#!/usr/bin/env python3
"""Frozen normal task on stdin -> read-only native morphology replay JSON.

No publication or runtime config mutation. Product/source identity is checked
before detection; counts of old QC survivors are NOT precipitation truth.
"""

from contextlib import ExitStack

import hashlib
import json
import re
import sys
import time

import numpy as np

from rainpulse_algo.multiband.codec import decode_arrays
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
from rainpulse_algo.multiband.xqc_v2.geometry import adapt, mask
from rainpulse_algo.multiband.xqc_v2.polar_morphology import MorphologyPolicy, detect
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
from rainpulse_algo.multiband.xqc_v2.evidence_tables import expand


def read_task(stream):
    raw = stream.read(1024**2 + 1)
    if len(raw) > 1024**2:
        raise ValueError("task JSON exceeds 1 MiB")
    task = json.loads(raw)
    if not isinstance(task, dict):
        raise ValueError("task JSON must be an object")
    return task


def validate_native_arrays(arrays, shape):
    axes = {
        "azimuth": (shape[0],),
        "range_m": (shape[1],),
        "elevation": (shape[0],),
        "ray_time_epoch": (shape[0],),
    }
    if not {*axes, "DBZH_RAW"} <= arrays.keys():
        raise ValueError("missing native identity fields")
    for name, value in arrays.items():
        if value.shape != axes.get(name, shape):
            raise ValueError("native array shape mismatch: " + name)


def optional_mask_count(arrays, key, selected):
    """Absent historical diagnostics are unknown, not negative observations."""
    if key not in arrays:
        return None
    return int((checked_diagnostic_mask(arrays[key], selected.shape) & selected).sum())


def checked_diagnostic_mask(value, shape):
    from rainpulse_algo.radar.qc_engine.volume_review.data import checked_mask

    return checked_mask(value, shape, "published diagnostic mask")


def baseline_union(arrays):
    keys = [
        "XQC_" + name + "_MASK"
        for name in (
            "RECEIVER",
            "PARTIAL",
            "RADIAL_POLAR",
            "RADIAL_FRAGMENT",
            "RADIAL_SOURCE",
            "CLUTTER",
            "ISOLATED",
        )
    ]
    missing = [key for key in keys if key not in arrays]
    if missing:
        return None, missing
    result = np.zeros(arrays[keys[0]].shape, bool)
    for key in keys:
        result |= checked_diagnostic_mask(arrays[key], result.shape)
    return result, missing


def candidate_budget(arrays, selected, cfg, *, native_available):
    """Historical masks estimate a budget only with matching RAW availability.

    A resource-abstained export contains zero AVAILABLE/proposal masks despite
    observed RAW. Its zeros are not proof that a new normal task fits a budget.
    """
    shape = selected.shape
    current = checked_diagnostic_mask(native_available, shape)
    historical = checked_diagnostic_mask(arrays["XQC_AVAILABLE_MASK"], shape)
    mismatch = int((current != historical).sum())
    baseline, missing = baseline_union(arrays)
    record = dict(
        baseline_missing_masks=missing,
        historical_available_count=int(historical.sum()),
        native_available_count=int(current.sum()),
        budget_availability_mismatch_gates=mismatch,
        baseline_candidate_fraction=None,
        prospective_candidate_fraction=None,
        prospective_action_budget_abstained=None,
        budget_scope="UNAVAILABLE",
    )
    if baseline is None or mismatch:
        return record
    hard = checked_diagnostic_mask(arrays["XQC_HARD_WEATHER_MASK"], shape)
    baseline &= current & ~hard
    union = baseline | (selected & current & ~hard)
    denominator = max(int(current.sum()), 1)
    record.update(
        baseline_candidate_fraction=float(baseline.sum()) / denominator,
        prospective_candidate_fraction=float(union.sum()) / denominator,
        prospective_action_budget_abstained=bool(
            int(union.sum()) > cfg.maximum_new_exclusion_fraction * denominator
        ),
        budget_scope="HISTORICAL_MASK_ESTIMATE",
    )
    return record


def load_bounded(objects, keys, maximum_bytes):
    total = sum(objects.session.index.logical[key][2] for key in keys)
    if total > maximum_bytes:
        raise ValueError("selected object byte budget exceeded before download")
    return {key: objects[key] for key in keys}


def check_staging_budget(session, maximum_disk_bytes):
    index = session.index
    if index.schema == "3.0":
        if len(index.physical) > 256:
            raise ValueError("packed asset pack count exceeds 256")
        if index.size > maximum_disk_bytes:
            raise ValueError("aggregate scratch byte budget exceeded")


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Verify frozen RAW source only; no historical QC comparison",
    )
    parser.add_argument(
        "--expanding-fans",
        action="store_true",
        help="Explicit v2 centre-stable widening geometry; read-only audit",
    )
    parser.add_argument(
        "--anchored-fans",
        action="store_true",
        help="Explicit v3 original stable-edge expanding geometry; read-only",
    )
    parser.add_argument(
        "--pulsing-fans",
        action="store_true",
        help="Explicit v4 original anchored pulsing geometry; read-only",
    )
    parser.add_argument(
        "--grouped-envelopes",
        action="store_true",
        help="Explicit v5 perforated complete envelopes; read-only",
    )
    parser.add_argument(
        "--branching-envelopes",
        action="store_true",
        help="Explicit v6 complete original split/join graph; read-only",
    )
    parser.add_argument(
        "--compact-counterexamples",
        action="store_true",
        help="Explicit v7 compact shape ambiguity protection; read-only",
    )
    parser.add_argument(
        "--transverse-counterexamples",
        action="store_true",
        help="Explicit v8 physical transverse ambiguity; read-only",
    )
    args = parser.parse_args()
    with ExitStack() as stack:
        if args.source_only:
            run_source_only(
                stack,
                expanding_fans=args.expanding_fans
                or args.anchored_fans
                or args.pulsing_fans
                or args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                anchored_fans=args.anchored_fans
                or args.pulsing_fans
                or args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                pulsing_fans=args.pulsing_fans
                or args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                grouped_envelopes=args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                **(
                    {"transverse_counterexamples": True}
                    if args.transverse_counterexamples
                    else {}
                ),
                **(
                    {"branching_envelopes": True}
                    if args.branching_envelopes
                    or (args.compact_counterexamples or args.transverse_counterexamples)
                    else {}
                ),
                **(
                    {"compact_counterexamples": True}
                    if (args.compact_counterexamples or args.transverse_counterexamples)
                    else {}
                ),
            )
        else:
            run(
                stack,
                expanding_fans=args.expanding_fans
                or args.anchored_fans
                or args.pulsing_fans
                or args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                anchored_fans=args.anchored_fans
                or args.pulsing_fans
                or args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                pulsing_fans=args.pulsing_fans
                or args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                grouped_envelopes=args.grouped_envelopes
                or args.branching_envelopes
                or (args.compact_counterexamples or args.transverse_counterexamples),
                **(
                    {"transverse_counterexamples": True}
                    if args.transverse_counterexamples
                    else {}
                ),
                **(
                    {"branching_envelopes": True}
                    if args.branching_envelopes
                    or (args.compact_counterexamples or args.transverse_counterexamples)
                    else {}
                ),
                **(
                    {"compact_counterexamples": True}
                    if (args.compact_counterexamples or args.transverse_counterexamples)
                    else {}
                ),
            )


def source_identity(task):
    """A source-only audit cannot silently substitute another catalog asset."""
    spec = task["spec"]
    payload = spec["request"]["payload"]
    sources, inputs = payload["sources"], spec["inputs"]
    if len(sources) != 1 or len(inputs) != 1:
        raise ValueError("source audit requires exactly one frozen input")
    source, identity = sources[0], inputs[0]
    if (
        task.get("state") != "SUCCEEDED"
        or payload.get("mode") != "x_qc"
        or payload.get("scan_id") != source["scan_id"]
        or payload.get("radar_id") != source["radar_id"]
        or identity["uri"] != source["input_uri"]
        or not re.fullmatch(r"[a-f0-9]{64}", identity["sha256"])
    ):
        raise ValueError("source differs from frozen successful X task identity")
    return source, identity["sha256"]


def source_cut_numbers(keys, declared_numbers=None):
    """Bound the full native inventory, including non-REF split sweeps."""
    all_numbers, reflectivity = set(), set()
    for key in keys:
        if (
            key.startswith("sweep_number/")
            or not key.startswith("sweep_")
            or "/" not in key
        ):
            continue
        group = key.split("/", 1)[0]
        match = re.fullmatch(r"sweep_([0-9]{3})", group)
        if match is None:
            parts = key.split("/", 2)
            # Normalized volumes also have root sweep_start/end_ray_index
            # arrays. They are coordinates, never native cut groups.
            if (
                len(parts) > 2
                or parts[1] == ".zgroup"
                or re.fullmatch(r"sweep_[0-9]+", group)
            ):
                raise ValueError("invalid native sweep number")
            continue
        number = int(match[1])
        all_numbers.add(number)
        if len(all_numbers) > 64:
            raise ValueError("source inventory exceeds 64 native sweeps")
        if key == group + "/DBZH/.zarray":
            reflectivity.add(number)
    if declared_numbers is not None and all_numbers != set(declared_numbers):
        raise ValueError("native sweep inventory differs from declared index")
    if not reflectivity:
        raise ValueError("source inventory contains no reflectivity sweep")
    return sorted(reflectivity)


def source_cut_keys(keys, number):
    from rainpulse_algo.multiband.adapters import X_QC_FIELDS

    group = f"sweep_{number:03d}/"
    names = X_QC_FIELDS | {"azimuth", "elevation", "ray_time", "range"}
    return [
        key
        for key in keys
        if key in {".zattrs", ".zgroup"}
        or (
            key.startswith(group)
            and (
                key[len(group) :] in {".zattrs", ".zgroup"}
                or key[len(group) :].split("/", 1)[0] in names
            )
        )
    ]


def policy_for_audit(
    expanding_fans=False,
    *,
    anchored_fans=False,
    pulsing_fans=False,
    grouped_envelopes=False,
    branching_envelopes=False,
    compact_counterexamples=False,
    transverse_counterexamples=False,
):
    compact_counterexamples = compact_counterexamples or transverse_counterexamples
    return MorphologyPolicy(
        version=(
            "x-polar-morphology-20261002-v8"
            if transverse_counterexamples
            else "x-polar-morphology-20261002-v7"
            if compact_counterexamples
            else "x-polar-morphology-20261002-v6"
            if branching_envelopes
            else "x-polar-morphology-20261002-v5"
            if grouped_envelopes
            else "x-polar-morphology-20261002-v4"
            if pulsing_fans
            else "x-polar-morphology-20261002-v3"
            if anchored_fans
            else "x-polar-morphology-20261002-v2"
            if expanding_fans
            else "x-polar-morphology-20261001-v1"
        ),
        expanding_fans_enabled=expanding_fans
        or anchored_fans
        or pulsing_fans
        or grouped_envelopes
        or branching_envelopes
        or compact_counterexamples,
        anchored_fans_enabled=anchored_fans
        or pulsing_fans
        or grouped_envelopes
        or branching_envelopes
        or compact_counterexamples,
        pulsing_fans_enabled=pulsing_fans
        or grouped_envelopes
        or branching_envelopes
        or compact_counterexamples,
        grouped_envelopes_enabled=grouped_envelopes
        or branching_envelopes
        or compact_counterexamples,
        branching_envelopes_enabled=branching_envelopes or compact_counterexamples,
        compact_counterexamples_enabled=compact_counterexamples,
        transverse_counterexamples_enabled=transverse_counterexamples,
        local_weather_policy="joint_review",
    )


def run_source_only(
    stack,
    *,
    expanding_fans=False,
    anchored_fans=False,
    pulsing_fans=False,
    grouped_envelopes=False,
    branching_envelopes=False,
    compact_counterexamples=False,
    transverse_counterexamples=False,
):
    """Bounded raw-only evidence; never invent old survivors or weather labels."""
    import os
    from rainpulse_algo.multiband.model import Network
    from rainpulse_algo.multiband.adapters import read_x_qc_sweep

    task = read_task(sys.stdin.buffer)
    source, sha = source_identity(task)
    net = Network.load(os.environ["RAINPULSE_MULTIBAND_CONFIG"])
    reader = ArtifactObjectReader(
        minio_client_from_environment(),
        max_workers=2,
        max_size_bytes=min(net.maximum_input_bytes, 512 * 1024**2),
    )
    session = reader.open(source["input_uri"], expected_sha256=sha)
    check_staging_budget(session, 512 * 1024**2)
    objects = stack.enter_context(
        session.staged(
            maximum_disk_bytes=512 * 1024**2, maximum_object_bytes=64 * 1024**2
        )
    )
    from rainpulse_algo.multiband.managed import _zarr_sweep_numbers

    index_keys = [
        key
        for key in objects
        if key in {".zattrs", ".zgroup"} or key.startswith("sweep_number/")
    ]
    declared = _zarr_sweep_numbers(load_bounded(objects, index_keys, 1024**2))
    numbers = source_cut_numbers(objects, declared)
    cfg = XQCConfig(
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
    )
    policy = policy_for_audit(
        expanding_fans,
        anchored_fans=anchored_fans,
        pulsing_fans=pulsing_fans,
        grouped_envelopes=grouped_envelopes,
        branching_envelopes=branching_envelopes,
        compact_counterexamples=compact_counterexamples,
        transverse_counterexamples=transverse_counterexamples,
    )
    cuts = []
    for number in numbers:
        stamp = time.monotonic()
        chosen = load_bounded(objects, source_cut_keys(objects, number), 256 * 1024**2)
        volume, _ = read_x_qc_sweep(
            chosen,
            net.stations[source["radar_id"]],
            source,
            number,
            asset_sha256=sha,
            maximum_bytes=min(net.maximum_input_bytes, 256 * 1024**2),
        )
        if volume is None:
            raise ValueError("inventoried reflectivity cut disappeared")
        cut = volume.sweeps[0]
        hard_keys = [
            key
            for key in ("WEATHER_PROTECTED_MASK", "MIXED_WEATHER_MASK")
            if key in cut.fields
        ]
        hard = np.zeros(cut.fields["DBZH"].shape, bool)
        for key in hard_keys:
            hard |= mask(cut.fields, key, hard.shape)
        fingerprints = {}
        for key, value in {
            "DBZH_RAW": cut.fields["DBZH"],
            "azimuth": cut.azimuth_deg,
            "range_m": cut.range_m,
            "elevation": cut.elevation_deg,
            "ray_time_epoch": cut.ray_time_epoch,
        }.items():
            a = np.ascontiguousarray(value)
            digest = hashlib.sha256()
            digest.update(json.dumps([str(a.dtype), list(a.shape)]).encode())
            digest.update(a.data)
            fingerprints[key] = digest.hexdigest()
        try:
            view = adapt(cut, cfg)
            ev = detect(view.sweep, policy, protected=hard[view.order])
            selected = view.restore(ev.mask)
            rec = ev.record
            for obj in rec["objects"] + rec["review_objects"]:
                for key in ("first_native_ray", "last_native_ray"):
                    obj[key] = int(view.order[obj[key]])
            rec["selected_known_hard_weather_gates"] = (
                int((selected & hard).sum()) if hard_keys else None
            )
        except ResourceLimit as exc:
            rec = {
                "status": "RESOURCE_LIMIT_ABSTAINED",
                "reason": str(exc),
                "qualified_gates": 0,
            }
        rec.update(
            new_visible_selected=None,
            visible_before=None,
            selected_context_weather_gates=None,
            prospective_action_budget_abstained=None,
            action_semantics="diagnostic_only_no_actions",
        )
        cuts.append(
            {
                "sweep_number": number,
                "native_identity_sha256": fingerprints,
                "geometry": {
                    "rays": len(cut.azimuth_deg),
                    "gates": len(cut.range_m),
                    "minimum_elevation_deg": float(np.min(cut.elevation_deg)),
                    "maximum_elevation_deg": float(np.max(cut.elevation_deg)),
                },
                "hard_weather_masks_present": hard_keys,
                "normal_status": None,
                "normal_native_comparison": "UNAVAILABLE",
                "elapsed_s": time.monotonic() - stamp,
                "morphology": rec,
            }
        )
        print("completed source cut " + str(number), file=sys.stderr, flush=True)
    print(
        json.dumps(
            {
                "task_id": task["id"],
                "scan_id": source["scan_id"],
                "input_sha256": sha,
                "proof_scope": "verified_source_only",
                "morphology_policy": policy.model_dump(),
                "no_publication": True,
                "actions_executed": False,
                "survivor_is_weather_truth": False,
                "independent_weather_acceptance": "NOT_COMPLETED",
                "cuts": cuts,
            },
            allow_nan=False,
        ),
        flush=True,
    )


def run(
    stack,
    *,
    expanding_fans=False,
    anchored_fans=False,
    pulsing_fans=False,
    grouped_envelopes=False,
    branching_envelopes=False,
    compact_counterexamples=False,
    transverse_counterexamples=False,
):
    from rainpulse_algo.multiband.model import Network
    import os

    task = read_task(sys.stdin.buffer)
    net = Network.load(os.environ["RAINPULSE_MULTIBAND_CONFIG"])
    asset = task["attempts"][-1]["result"]["asset"]
    reader = ArtifactObjectReader(
        minio_client_from_environment(),
        max_workers=2,
        max_size_bytes=min(net.maximum_input_bytes, 512 * 1024**2),
    )
    product_session = reader.open(asset["uri"], expected_sha256=asset["sha256"])
    check_staging_budget(product_session, 512 * 1024**2)
    product = stack.enter_context(
        product_session.staged(
            maximum_disk_bytes=512 * 1024**2,
            maximum_object_bytes=64 * 1024**2,
        )
    )
    manifest = json.loads(
        load_bounded(product, ["manifest.json"], 1024**2)["manifest.json"]
    )
    cfg = XQCConfig(
        receiver_enabled=False,
        radial_objects_enabled=False,
        clutter_enabled=False,
        isolation_enabled=False,
    )
    policy = policy_for_audit(
        expanding_fans,
        anchored_fans=anchored_fans,
        pulsing_fans=pulsing_fans,
        grouped_envelopes=grouped_envelopes,
        branching_envelopes=branching_envelopes,
        compact_counterexamples=compact_counterexamples,
        transverse_counterexamples=transverse_counterexamples,
    )
    cuts = []
    from rainpulse_algo.multiband.adapters import read_x_qc_sweep

    source = task["spec"]["request"]["payload"]["sources"][0]
    sha = task["spec"]["inputs"][0]["sha256"]
    source_session = reader.open(source["input_uri"], expected_sha256=sha)
    product_scratch = (
        product_session.index.size if product_session.index.schema == "3.0" else 0
    )
    check_staging_budget(source_session, 512 * 1024**2 - product_scratch)
    source_objects = stack.enter_context(
        source_session.staged(
            maximum_disk_bytes=512 * 1024**2 - product_scratch,
            maximum_object_bytes=64 * 1024**2,
        )
    )
    for entry in manifest["comparison"]["sweeps"]:
        stamp = time.monotonic()
        descriptor = entry["xqc_v2"]["native"]
        payload = load_bounded(product, [descriptor["object_path"]], 64 * 1024**2)[
            descriptor["object_path"]
        ]
        if hashlib.sha256(payload).hexdigest() != descriptor["sha256"]:
            raise ValueError("native product SHA mismatch")
        if len(payload) > 64 * 1024**2:
            raise ValueError("native NPZ exceeds encoded budget")
        arrays = decode_arrays(payload, maximum_bytes=256 * 1024**2)
        # Native export retains source RAW DBZH, but auxiliary moments live in
        # the frozen normalized input. Read only this source cut.
        keys = source_cut_keys(source_objects, entry["sweep_number"])
        objects = load_bounded(source_objects, keys, 256 * 1024**2)
        volume, _ = read_x_qc_sweep(
            objects,
            net.stations[source["radar_id"]],
            source,
            entry["sweep_number"],
            asset_sha256=sha,
            maximum_bytes=min(net.maximum_input_bytes, 256 * 1024**2),
        )
        cut = volume.sweeps[0]
        validate_native_arrays(arrays, cut.fields["DBZH"].shape)
        for name, value in [
            ("DBZH_RAW", cut.fields["DBZH"]),
            ("azimuth", cut.azimuth_deg),
            ("range_m", cut.range_m),
            ("elevation", cut.elevation_deg),
            ("ray_time_epoch", cut.ray_time_epoch),
        ]:
            np.testing.assert_array_equal(arrays[name], value)
        view = adapt(cut, cfg)
        hard = mask(cut.fields, "WEATHER_PROTECTED_MASK", cut.fields["DBZH"].shape)
        hard |= mask(cut.fields, "MIXED_WEATHER_MASK", hard.shape)
        try:
            ev = detect(view.sweep, policy, protected=hard[view.order])
            selected = view.restore(ev.mask)
            rec = ev.record
            for obj in rec["objects"] + rec["review_objects"]:
                for name in ["first_native_ray", "last_native_ray"]:
                    obj[name] = int(view.order[obj[name]])
            visible = np.isfinite(arrays["DBZH_QC"]) & (arrays["DBZH_QC"] >= 5)
            rows = []
            for row in np.flatnonzero((selected & visible).any(axis=1)):
                rows.append(
                    dict(
                        ray=int(row),
                        azimuth_deg=float(cut.azimuth_deg[row]),
                        selected_remaining=int((selected[row] & visible[row]).sum()),
                        remaining_before=int(visible[row].sum()),
                    )
                )
            rec["local_proxy_policy"] = policy.local_weather_policy
            rec["selected_local_weather_gates"] = int(
                (selected & (arrays["XQC_LOCAL_WEATHER_MASK"] == 1)).sum()
            )
            rec["action_semantics"] = "candidate_withheld_not_confirmed"
            rec.update(
                new_visible_selected=int((selected & visible).sum()),
                visible_before=int(visible.sum()),
                selected_hard_weather=int((selected & hard).sum()),
                per_ray_remaining=rows,
            )
            actual_cfg = XQCConfig.model_validate(
                net.stations[source["radar_id"]].x_qc.enhancement
            )
            rec.update(
                candidate_budget(
                    arrays,
                    selected,
                    actual_cfg,
                    native_available=view.restore(view.sweep.observed),
                )
            )
            rec["selected_context_weather_gates"] = optional_mask_count(
                arrays, "XQC_CONTEXT_WEATHER_MASK", selected
            )
        except ResourceLimit as exc:
            rec = {
                "status": "RESOURCE_LIMIT_ABSTAINED",
                "reason": str(exc),
                "qualified_gates": 0,
            }
        evidence = expand(
            json.loads(
                load_bounded(product, [entry["xqc_v2"]["evidence_path"]], 8 * 1024**2)[
                    entry["xqc_v2"]["evidence_path"]
                ]
            )
        )
        cuts.append(
            dict(
                sweep_number=entry["sweep_number"],
                native_sha256=descriptor["sha256"],
                elapsed_s=time.monotonic() - stamp,
                normal_status=evidence["status"],
                normal_detail=evidence.get("detail"),
                morphology=rec,
            )
        )
        print(
            "completed cut " + str(entry["sweep_number"]), file=sys.stderr, flush=True
        )
    print(
        json.dumps(
            dict(
                task_id=task["id"],
                input_sha256=sha,
                result_sha256=asset["sha256"],
                morphology_policy=policy.model_dump(),
                no_publication=True,
                survivor_is_weather_truth=False,
                cuts=cuts,
            ),
            allow_nan=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
