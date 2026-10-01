#!/usr/bin/env python3
"""Full native X decode/QC in memory, independent of object-store capacity.

No products are published. File SHA, decoder config, actual UTC acquisition
times and every REF cut remain in the audit. Read-only geometry/quality gates
do not replace independent weather or real UI acceptance.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid
from datetime import datetime, timezone


def shard_for_file(raw_sha256, shards):
    if not 1 <= shards <= 12:
        raise ValueError("invalid bounded worker count")
    return int(raw_sha256, 16) % shards


def verify_config(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("decoder config differs from frozen batch identity")
    return data


def process_file(record, network, root, configs, parent_sha, config_identities):
    import numpy as np
    import zarr
    from zarr.storage import MemoryStore
    from rainpulse_algo.radar.config import load_radar_config
    from rainpulse_algo.radar.fmt import decode_fmt_volume
    from rainpulse_algo.radar.zarr_volume import build_zarr_store
    from rainpulse_algo.multiband.stream_io import GroupCuts
    from rainpulse_algo.multiband.execution import ExecutionOptions
    from rainpulse_algo.multiband.quality import x_qc
    from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
    from xqc_acceptance import cut_failures

    sid = record["radar_id"]
    prefix = {"scan_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "rainpulse:offline-xqc:" + record["sha256"])),
              "radar_id": sid, "raw_sha256": record["sha256"], "publication": "NONE",
              "identity_namespace": "offline_raw_sha256_not_catalog_scan_id"}
    station = network.stations.get(sid)
    if station is None or sid in {"zf703", "zf801"} or station.x_qc.enhancement is None:
        yield {**prefix, "state": "EXCLUDED", "reason": "NO_VERIFIED_NATIVE_TIME_FORMAT"}
        return
    path = root / record["relative_path"]
    if root.resolve() not in path.resolve().parents:
        raise ValueError("source escapes frozen read-only root")
    config_path = configs / (sid + ".yaml")
    config_bytes = verify_config(config_path, config_identities[sid])
    config = load_radar_config(config_path)
    if config_path.read_bytes() != config_bytes:
        raise ValueError("decoder config changed during load")
    started = time.monotonic()
    decoded = decode_fmt_volume(path, config)
    if decoded.input_sha256 != record["sha256"] or decoded.input_size_bytes != record["size_bytes"]:
        raise ValueError("raw source differs from frozen full-file identity")
    prefix.update(decoder_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
                  generic_type=decoded.generic_type,
                  observed_start_utc=decoded.volume_start_time.isoformat(),
                  observed_end_utc=decoded.volume_end_time.isoformat(),
                  six_minute_bucket_utc=int(decoded.volume_start_time.timestamp()//360),
                  decoded_ref_sweeps=sum("DBZH" in s.fields for s in decoded.sweeps))
    objects = build_zarr_store(decoded, config, asset_id=prefix["scan_id"], source_uri=path.as_uri(),
                               provenance={"scan_id": prefix["scan_id"]})
    from rainpulse_algo.worker.asset_access import artifact_digest
    normalized_sha = artifact_digest(objects)
    store = MemoryStore()
    store.update(objects)
    group = zarr.open_group(store=store, mode="r")
    cfg = XQCConfig.model_validate({**station.x_qc.enhancement,
                                    "radial_source_enabled": True,
                                    "radial_source_block_model_enabled": True,
                                    "radial_source_fan_model_enabled": True})
    child = replace(station, x_qc=replace(station.x_qc, enhancement=cfg.model_dump(mode="json")))
    source = {"scan_id": prefix["scan_id"], "radar_id": sid,
              "volume_start": decoded.volume_start_time.isoformat(),
              "volume_end": decoded.volume_end_time.isoformat(),
              # Read-only raw verification is available at this audit's actual time.
              "available_at": datetime.now(timezone.utc).isoformat()}
    cuts = GroupCuts(group, child, source, sha256=normalized_sha,
                     options=ExecutionOptions(streaming=True), maximum_bytes=network.maximum_input_bytes)
    yield {**prefix, "state": "RAW_DECODE_VERIFIED", "normalized_identity": normalized_sha,
           "parameter_sha256": cfg.digest, "ref_sweep_numbers": cuts.numbers,
           "decode_normalize_seconds": time.monotonic()-started}
    for number in cuts.numbers:
        volume = cuts.read(number)
        original = volume.sweeps[0]
        raw = original.fields["DBZH"].copy()
        mark = time.monotonic()
        output = x_qc(volume, child, parent_sha).sweeps[0]
        fields, evidence = output.fields, output.xqc_diagnostics
        held = fields["XQC_WITHHELD_MASK"] != 0
        rejected = fields["XQC_REJECTED_MASK"] != 0
        kwargs = dict(raw_equal=bool(np.array_equal(raw, fields["DBZH_RAW"], equal_nan=True) and
                                      np.array_equal(raw, original.fields["DBZH"], equal_nan=True)),
                      geometry_equal=bool(np.array_equal(original.azimuth_deg, output.azimuth_deg) and
                                          np.array_equal(original.range_m, output.range_m)),
                      held_visible=int(np.count_nonzero(held & np.isfinite(fields["DBZH_QC"]))),
                      held_admitted=int(np.count_nonzero(held & (fields["REFLECTIVITY_ELIGIBLE_FOR_CR"] != 0))),
                      protected_rejected=int(np.count_nonzero(rejected & (fields["XQC_HARD_WEATHER_MASK"] != 0))),
                      qpe_enabled=bool(fields["QPE_ELIGIBLE_MASK"].any()))
        failures = cut_failures(evidence, **kwargs)
        yield {**prefix, "sweep": number, "state": "FAIL" if failures else "RAW_MECHANICAL_GATES_PASSED",
               "failures": failures, "cut_status": evidence.get("status"),
               "source_status": evidence.get("module_records", {}).get("radial_source", {}).get("status"),
               "source_gates": int(fields["XQC_RADIAL_SOURCE_MASK"].sum()),
               "mixed_review_gates": int(fields["XQC_SOURCE_MIXED_MASK"].sum()),
               "remaining_echo_gates": int(np.count_nonzero(np.isfinite(fields["DBZH_QC"]) & (fields["DBZH_QC"] >= 15))),
               "seconds": time.monotonic()-mark, "independent_weather_acceptance": "PENDING", **kwargs}


def main():
    from rainpulse_algo.multiband.model import Network
    from xqc_acceptance import digest
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["raw"])
    parser.add_argument("--station", action="append")
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--raw-records", type=Path, required=True)
    parser.add_argument("--raw-sha256", required=True)
    parser.add_argument("--configs", type=Path, required=True)
    parser.add_argument("--limit-files", type=int)
    parser.add_argument("--config-identities", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args()
    manifest = json.load(sys.stdin)
    sha = manifest.pop("manifest_sha256")
    if digest(manifest) != sha or hashlib.sha256(args.raw_records.read_bytes()).hexdigest() != args.raw_sha256:
        raise ValueError("frozen source/network manifest changed")
    if not 0 <= args.shard_index < args.shards <= 12:
        raise ValueError("invalid shard index")
    config_identities = json.loads(args.config_identities.read_text())
    network = Network.from_bytes(json.dumps(manifest["network"]).encode())
    count = 0
    for line in args.raw_records.read_text().splitlines():
        record = json.loads(line)
        if shard_for_file(record["sha256"], args.shards) != args.shard_index:
            continue
        if args.station and record["radar_id"] not in args.station:
            continue
        if args.limit_files is not None and count >= args.limit_files:
            break
        count += 1
        try:
            for result in process_file(record, network, args.raw_root, args.configs, manifest["network_sha256"], config_identities):
                print(json.dumps({"manifest_sha256": sha, "raw_manifest_sha256": args.raw_sha256, **result}), flush=True)
            print(json.dumps({"scan_id": record["sha256"], "radar_id": record["radar_id"],
                              "raw_sha256": record["sha256"], "state": "RAW_FILE_COMPLETE"}), flush=True)
        except Exception as error:
            print(json.dumps({"scan_id": record["sha256"], "radar_id": record["radar_id"],
                              "raw_sha256": record["sha256"], "state": "FAIL", "failures": ["RAW_COMPUTE_ERROR"],
                              "error_type": type(error).__name__, "detail": str(error)[:240]}), flush=True)


if __name__ == "__main__":
    main()
