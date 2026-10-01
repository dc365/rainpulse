#!/usr/bin/env python3
"""Frozen normal task on stdin -> read-only native morphology replay JSON.

No publication or runtime config mutation. Product/source identity is checked
before detection; counts of old QC survivors are NOT precipitation truth.
"""

from contextlib import ExitStack

import hashlib
import json
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
    with ExitStack() as stack:
        run(stack)


def run(stack):
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
    policy = MorphologyPolicy(local_weather_policy="joint_review")
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
        from rainpulse_algo.multiband.adapters import X_QC_FIELDS

        group = f"sweep_{entry['sweep_number']:03d}/"
        names = X_QC_FIELDS | {"azimuth", "elevation", "ray_time", "range"}
        keys = [
            key
            for key in source_objects
            if key in {".zattrs", ".zgroup"}
            or (
                key.startswith(group)
                and (
                    key[len(group) :] in {".zattrs", ".zgroup"}
                    or key[len(group) :].split("/", 1)[0] in names
                )
            )
        ]
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
            baseline = np.zeros(selected.shape, bool)
            for name in (
                "RECEIVER",
                "PARTIAL",
                "RADIAL_POLAR",
                "RADIAL_FRAGMENT",
                "RADIAL_SOURCE",
                "CLUTTER",
                "ISOLATED",
            ):
                baseline |= arrays["XQC_" + name + "_MASK"] == 1
            baseline &= arrays["XQC_AVAILABLE_MASK"] == 1
            baseline &= arrays["XQC_HARD_WEATHER_MASK"] == 0
            union = baseline | (
                selected
                & (arrays["XQC_AVAILABLE_MASK"] == 1)
                & (arrays["XQC_HARD_WEATHER_MASK"] == 0)
            )
            observed_count = int((arrays["XQC_AVAILABLE_MASK"] == 1).sum())
            rec["baseline_candidate_fraction"] = float(baseline.sum()) / max(
                observed_count, 1
            )
            rec["prospective_candidate_fraction"] = float(union.sum()) / max(
                observed_count, 1
            )
            rec["prospective_action_budget_abstained"] = int(
                union.sum()
            ) > actual_cfg.maximum_new_exclusion_fraction * max(observed_count, 1)
            rec["selected_context_weather_gates"] = int(
                (selected & (arrays["XQC_CONTEXT_WEATHER_MASK"] == 1)).sum()
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
