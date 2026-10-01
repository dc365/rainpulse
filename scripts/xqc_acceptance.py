#!/usr/bin/env python3
"""Read-only X-QC acceptance: freeze inventory, then audit committed products.

Run `freeze OUTPUT` on 105. Run `audit` inside an existing worker image with
the frozen manifest on stdin. Neither command changes tasks, radar data or QC
products. A successful task is deliberately not a meteorological PASS.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

MANDATORY = (
    "d42663f7-67de-5f8a-901a-7c9163a1d1d2",
    "07bdd490-9202-5772-8c90-2d529688298d",
    "93d2d253-7dc8-55fd-b428-c1d8b76ad8ef",
    "5418017f-36f5-554b-b763-a7551b4b0e86",
    "d1054e54-e2cb-59ba-a36a-4a0f8c5d11e5",
    "3b3bf9da-aac2-5800-88a5-6d29ed907d9c",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def cut_failures(record, *, raw_equal, geometry_equal, held_visible, held_admitted,
                 protected_rejected, qpe_enabled):
    """Mechanical gates only; independent weather/visual truth remains required."""
    failures = []
    if not raw_equal:
        failures.append("RAW_CHANGED")
    if not geometry_equal:
        failures.append("NATIVE_GEOMETRY_CHANGED")
    if record.get("status") != "EVALUATED":
        failures.append("CUT_" + str(record.get("status", "MISSING_STATUS")))
    source = record.get("module_records", {}).get("radial_source", {})
    if source.get("status") != "EVALUATED" or source.get("complete") is not True:
        failures.append("SOURCE_INCOMPLETE")
    if held_visible:
        failures.append("WITHHELD_VISIBLE")
    if held_admitted:
        failures.append("WITHHELD_ADMITTED")
    if protected_rejected:
        failures.append("PROTECTED_WEATHER_REJECTED")
    if qpe_enabled:
        failures.append("CANDIDATE_QPE_ENABLED")
    return failures


def query(sql):
    command = ["docker", "exec", "-i", "rainpulse-postgres-1", "sh", "-c",
               'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At']
    # Environment credentials stay inside the existing container, never logged.
    out = subprocess.check_output(command, input=("BEGIN READ ONLY;\n" + sql +
                                  "\nROLLBACK;\n").encode()).decode()
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def freeze(path):
    path = Path(path)
    if path.exists():
        raise ValueError("refusing to replace frozen manifest; choose a new revision")
    c = json.loads(subprocess.check_output(["docker", "inspect",
                                           "rainpulse-ops-multiband-worker-1"]))[0]
    environment = dict(item.split("=", 1) for item in c["Config"]["Env"] if "=" in item)
    network_bytes = subprocess.check_output(["docker", "exec", c["Id"], "cat",
                                             environment["RAINPULSE_MULTIBAND_CONFIG"]])
    network = json.loads(network_bytes)
    scans = query("""
SELECT jsonb_build_object('scan_id',s.scan_id,'radar_id',lower(s.radar_id),
 'start',s.volume_start_time,'end',s.volume_end_time,'raw_asset_id',s.raw_asset_id,
 'normalized_uri',r.normalized_uri,'decode_state',r.status,
 'config_version',r.radar_config_version,'available_at',r.updated_at,'task',t.task)
FROM radar_scans s LEFT JOIN LATERAL (
 SELECT * FROM radar_scan_runs WHERE scan_id=s.scan_id
 ORDER BY created_at DESC,run_id DESC LIMIT 1) r ON true
LEFT JOIN LATERAL (
 SELECT jsonb_build_object('id',id,'state',state,'spec',spec,'result',result) task
 FROM ops_tasks WHERE spec->'request'->'payload'->>'scan_id'=s.scan_id::text
 AND spec->'request'->'payload'->>'mode'='x_qc'
 ORDER BY created_at DESC,id DESC LIMIT 1) t ON true
WHERE lower(s.radar_id) LIKE 'zf%'
 AND s.volume_end_time >= '2026-08-27T16:00:00Z'
 AND s.volume_end_time < '2026-08-29T00:00:00Z'
ORDER BY lower(s.radar_id),s.volume_end_time,s.scan_id;
""")
    # A source directory entry is NOT an immutable decoded scan identity.
    inventory = collections.Counter()
    root = Path("/data/Weather/RADA/RADA_L2_X_FMT/OBS_X")
    for folder, _, names in os.walk(root):
        for name in names:
            if "20260828" in name:
                station = next((part.lower() for part in name.replace(".", "_").split("_")
                                if part.upper().startswith("ZF") and part[2:].isdigit()), "unclassified")
                inventory[station] += 1
    result = {"contract": "xqc-acceptance-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "image": c["Config"]["Image"], "image_id": c["Image"],
              "network_sha256": hashlib.sha256(network_bytes).hexdigest(),
              "network": network, "mandatory_scan_ids": list(MANDATORY), "scans": scans,
              "raw_file_counts": dict(inventory),
              "time_scope": {"utc_day": "2026-08-28", "local_day": "2026-08-28 UTC+08:00"},
              "raw_inventory_identity_state": "REQUIRES_DECODE_AND_SIX_MINUTE_SELECTION",
              "weather_controls_state": "REQUIRES_INDEPENDENT_ANNOTATION",
              "visual_state": "REQUIRES_ALL_REPORTED_REGIONS_AND_ELEVATIONS"}
    result["manifest_sha256"] = digest(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(result, stream, sort_keys=True)
    counts = collections.Counter(s["radar_id"] for s in scans)
    print(json.dumps({"manifest_sha256": result["manifest_sha256"], "scans": len(scans),
                      "stations": dict(counts), "raw_file_counts": dict(inventory)}))


def audit(manifest, station_filter=None):
    import numpy as np
    import zarr
    from zarr.storage import MemoryStore
    from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment

    expected = manifest.pop("manifest_sha256")
    if digest(manifest) != expected:
        raise ValueError("frozen manifest identity mismatch")
    reader = ArtifactObjectReader(minio_client_from_environment(), max_workers=2)
    for scan in manifest["scans"]:
        if station_filter and scan["radar_id"] not in station_filter:
            continue
        prefix = {"manifest_sha256": expected, "scan_id": scan["scan_id"],
                  "radar_id": scan["radar_id"], "observed_at": scan["end"]}
        task = scan.get("task") or {}
        if task.get("state") != "SUCCEEDED":
            print(json.dumps({**prefix, "state": "FAIL", "failures": ["NO_SUCCEEDED_PRODUCT"]}), flush=True)
            continue
        try:
            asset = task["result"]["asset"]
            stream = reader.open(asset["uri"], expected_sha256=asset["sha256"])
            product = json.loads(stream.load(keys=["manifest.json"])["manifest.json"])
            source = task["spec"]["request"]["payload"]["sources"][0]
            input_sha = task["spec"]["inputs"][0]["sha256"]
            store = MemoryStore()
            store.update(reader.load(source["input_uri"], expected_sha256=input_sha))
            root = zarr.open_group(store=store, mode="r")
            product_numbers = [int(sw["sweep_number"]) for sw in product["comparison"]["sweeps"]]
            input_numbers = [int(x) for x in root["sweep_number"][:]
                             if "DBZH" in root[f"sweep_{int(x):03d}"]]
            if sorted(product_numbers) != sorted(input_numbers):
                raise ValueError("REF_CUT_COVERAGE_MISMATCH")
            for number in product_numbers:
                keys = [f"qc-v2/{number}/native.npz", f"qc-v2/{number}/evidence.json"]
                objects = stream.load(keys=keys)
                with np.load(io.BytesIO(objects[keys[0]]), allow_pickle=False) as arrays:
                    evidence = json.loads(objects[keys[1]])
                    original = root[f"sweep_{number:03d}"]
                    held = arrays["XQC_WITHHELD_MASK"] != 0
                    rejected = arrays["XQC_REJECTED_MASK"] != 0
                    raw_equal = bool(np.array_equal(arrays["DBZH_RAW"], original["DBZH"][:], equal_nan=True))
                    geometry_equal = bool(np.array_equal(arrays["azimuth"], original["azimuth"][:]) and
                                          np.array_equal(arrays["range_m"], original["range"][:]))
                    kwargs = dict(raw_equal=raw_equal, geometry_equal=geometry_equal,
                                  held_visible=int(np.count_nonzero(held & np.isfinite(arrays["DBZH_QC"]))),
                                  held_admitted=int(np.count_nonzero(held & (arrays["REFLECTIVITY_ELIGIBLE_FOR_CR"] != 0))),
                                  protected_rejected=int(np.count_nonzero(rejected & (arrays["XQC_HARD_WEATHER_MASK"] != 0))),
                                  qpe_enabled=bool(arrays["QPE_ELIGIBLE_MASK"].any()))
                    failures = cut_failures(evidence, **kwargs)
                    if task["spec"]["request"]["payload"]["network_sha256"] != manifest["network_sha256"]:
                        failures.append("OLD_NETWORK_PRODUCT")
                    qc_echo = np.isfinite(arrays["DBZH_QC"]) & (arrays["DBZH_QC"] >= 15)
                    print(json.dumps({**prefix, "sweep": number, "task_id": task["id"],
                                      "asset_sha256": asset["sha256"], "runtime_ms": task["result"].get("runtime_ms"),
                                      "state": "FAIL" if failures else "MECHANICAL_GATES_PASSED",
                                      "failures": failures, "cut_status": evidence.get("status"),
                                      "source_status": evidence.get("module_records", {}).get("radial_source", {}).get("status"),
                                      "remaining_echo_gates": int(qc_echo.sum()),
                                      "max_residual_ray_gates": int(qc_echo.sum(axis=1).max()),
                                      "withheld_gates": int(held.sum()), **kwargs}), flush=True)
        except Exception as error:
            print(json.dumps({**prefix, "state": "FAIL", "failures": ["PRODUCT_AUDIT_ERROR"],
                              "error_type": type(error).__name__, "detail": str(error)[:240]}), flush=True)


def pilot(manifest, station_filter=None, scan_filter=None):
    """Compare source activation on stratified native cuts without publication.

    Only three explicit source switches change. Existing thresholds, floor,
    backgrounds, deletion caps and all S profiles remain identical.
    """
    import numpy as np
    import zarr
    from zarr.storage import MemoryStore
    from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment
    from rainpulse_algo.multiband.model import Network
    from rainpulse_algo.multiband.stream_io import GroupCuts
    from rainpulse_algo.multiband.execution import ExecutionOptions
    from rainpulse_algo.multiband.quality import x_qc
    from rainpulse_algo.multiband.xqc_v2.config import XQCConfig

    expected = manifest.pop("manifest_sha256")
    if digest(manifest) != expected:
        raise ValueError("frozen manifest identity mismatch")
    network = Network.from_bytes(json.dumps(manifest["network"]).encode())
    reader = ArtifactObjectReader(minio_client_from_environment(), max_workers=2)
    grouped = collections.defaultdict(list)
    for scan in manifest["scans"]:
        if scan.get("normalized_uri"):
            grouped[scan["radar_id"]].append(scan)
    for sid, scans in grouped.items():
        if station_filter and sid not in station_filter:
            continue
        station = network.stations[sid]
        if station.x_qc is None or station.x_qc.enhancement is None:
            print(json.dumps({"scan_id": scans[0]["scan_id"], "radar_id": sid,
                              "state": "EXCLUDED", "reason": "NO_QUALIFIED_NATIVE_FORMAT"}), flush=True)
            continue
        selected = {scans[i]["scan_id"]: scans[i] for i in {0, len(scans)//2, len(scans)-1}}
        for scan in scans:
            if scan["scan_id"] in MANDATORY:
                selected[scan["scan_id"]] = scan
        cfg = XQCConfig.model_validate(station.x_qc.enhancement)
        patch = {"radial_source_enabled": True, "radial_source_block_model_enabled": True,
                 "radial_source_fan_model_enabled": True}
        child_cfg = XQCConfig.model_validate({**cfg.model_dump(mode="json"), **patch})
        child_station = replace(station, x_qc=replace(station.x_qc, enhancement=child_cfg.model_dump(mode="json")))
        for scan in selected.values():
            if scan_filter and scan["scan_id"] not in scan_filter:
                continue
            prefix = {"manifest_sha256": expected, "scan_id": scan["scan_id"], "radar_id": sid,
                      "source_activation_changed": cfg.digest != child_cfg.digest,
                      "parent_policy_sha256": cfg.digest, "child_policy_sha256": child_cfg.digest}
            try:
                task = scan.get("task") or {}
                if task.get("state") == "SUCCEEDED":
                    source = task["spec"]["request"]["payload"]["sources"][0]
                    sha = task["spec"]["inputs"][0]["sha256"]
                else:
                    source = {"scan_id": scan["scan_id"], "radar_id": sid,
                              "input_uri": scan["normalized_uri"],
                              "volume_start": scan["start"], "volume_end": scan["end"],
                              "available_at": scan["available_at"]}
                    sha = None
                snapshot = reader.open(source["input_uri"], expected_sha256=sha)
                sha = snapshot.index.sha256
                objects = snapshot.load()
                store = MemoryStore()
                store.update(objects)
                root = zarr.open_group(store=store, mode="r")
                cuts = GroupCuts(root, station, source, sha256=sha,
                                 options=ExecutionOptions(streaming=True),
                                 maximum_bytes=network.maximum_input_bytes)
                numbers = [int(i) for i in root["sweep_number"][:]
                           if "DBZH" in root[f"sweep_{int(i):03d}"]]
                # Low, middle and top REF cuts cover elevation behaviour.
                chosen = sorted({numbers[i] for i in {0, len(numbers)//2, len(numbers)-1}})
                if scan["scan_id"] in MANDATORY:
                    chosen = numbers  # Every user-reported volume gets ALL REF cuts.
                for number in chosen:
                    volume = cuts.read(number)
                    original = volume.sweeps[0]
                    raw = original.fields["DBZH"].copy()
                    started = time.monotonic()
                    before = x_qc(volume, station, manifest["network_sha256"]).sweeps[0]
                    after = (x_qc(volume, child_station, manifest["network_sha256"]).sweeps[0]
                             if cfg.digest != child_cfg.digest else before)
                    fields = after.fields
                    record = after.xqc_diagnostics
                    held = fields["XQC_WITHHELD_MASK"] != 0
                    rejected = fields["XQC_REJECTED_MASK"] != 0
                    kwargs = dict(raw_equal=bool(np.array_equal(fields["DBZH_RAW"], raw, equal_nan=True) and
                                                  np.array_equal(original.fields["DBZH"], raw, equal_nan=True)),
                                  geometry_equal=bool(np.array_equal(after.azimuth_deg, original.azimuth_deg) and
                                                      np.array_equal(after.range_m, original.range_m)),
                                  held_visible=int(np.count_nonzero(held & np.isfinite(fields["DBZH_QC"]))),
                                  held_admitted=int(np.count_nonzero(held & (fields["REFLECTIVITY_ELIGIBLE_FOR_CR"] != 0))),
                                  protected_rejected=int(np.count_nonzero(rejected & (fields["XQC_HARD_WEATHER_MASK"] != 0))),
                                  qpe_enabled=bool(fields["QPE_ELIGIBLE_MASK"].any()))
                    failures = cut_failures(record, **kwargs)
                    newly_rejected = rejected & ~(before.fields["XQC_REJECTED_MASK"] != 0)
                    print(json.dumps({**prefix, "sweep": number, "input_sha256": sha,
                                      "state": "FAIL" if failures else "PILOT_MECHANICAL_GATES_PASSED",
                                      "failures": failures, "seconds": time.monotonic()-started,
                                      "before_cut_status": before.xqc_diagnostics.get("status"),
                                      "after_cut_status": record.get("status"),
                                      "context": record.get("module_records", {}).get("context", {}),
                                      "mixed_review_gates": int(fields["XQC_SOURCE_MIXED_MASK"].sum()),
                                      "before_source_gates": int(before.fields["XQC_RADIAL_SOURCE_MASK"].sum()),
                                      "after_source_gates": int(fields["XQC_RADIAL_SOURCE_MASK"].sum()),
                                      "newly_rejected_gates": int(newly_rejected.sum()),
                                      "new_local_weather_overlap": int(np.count_nonzero(newly_rejected & (fields["XQC_LOCAL_WEATHER_MASK"] != 0))),
                                      "independent_weather_acceptance": "PENDING", **kwargs}), flush=True)
            except Exception as error:
                print(json.dumps({**prefix, "state": "FAIL", "failures": ["PILOT_ERROR"],
                                  "error_type": type(error).__name__, "detail": str(error)[:240]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze").add_argument("output")
    sub.add_parser("audit").add_argument("--station", action="append")
    pilot_parser = sub.add_parser("pilot")
    pilot_parser.add_argument("--station", action="append")
    pilot_parser.add_argument("--scan-id", action="append")
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.output)
    elif args.command == "audit":
        audit(json.load(sys.stdin), args.station)
    else:
        pilot(json.load(sys.stdin), args.station, args.scan_id)


if __name__ == "__main__":
    main()
