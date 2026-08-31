#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import zarr
from minio import Minio
from zarr.storage import MemoryStore

from rainpulse_algo.grid import load_grid_config
from rainpulse_algo.nowcast.ensemble_zarr import (
    build_ensemble_forecast_output_zarr_store,
)
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKFields
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile
from rainpulse_algo.nowcast.pysteps_steps import run_pysteps_steps_fields
from rainpulse_algo.nowcast.steps_profile import load_pysteps_steps_profile
from rainpulse_algo.products.ensemble_builder import (
    build_ensemble_application_product_bundle,
)
from rainpulse_algo.products.ensemble_profile import (
    load_ensemble_application_product_profile,
)
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    artifact_sha256,
    minio_client_from_environment,
    parse_s3_uri,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill non-operational RP-039 STEPS products from committed NowcastInput."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--grid-config", type=Path, required=True)
    parser.add_argument("--lk-config", type=Path, required=True)
    parser.add_argument("--steps-config", type=Path, required=True)
    parser.add_argument("--product-config", type=Path, required=True)
    arguments = parser.parse_args()

    catalog = _load_catalog(arguments.catalog)
    grid = load_grid_config(arguments.grid_config)
    lk = load_pysteps_lk_profile(arguments.lk_config)
    steps = load_pysteps_steps_profile(arguments.steps_config)
    products = load_ensemble_application_product_profile(arguments.product_config)
    client = minio_client_from_environment()
    reader = ArtifactObjectReader(client)
    reports: list[dict[str, Any]] = []

    for input_uri in catalog:
        input_objects = reader.load(input_uri)
        root = _open(input_objects)
        issue_time = datetime.fromisoformat(
            str(root.attrs["issue_time_utc"]).replace("Z", "+00:00")
        ).astimezone(UTC)
        input_asset_ids = [UUID(value) for value in root.attrs["input_asset_ids"]]
        run_id = uuid5(
            NAMESPACE_URL,
            f"rainpulse:rp039:steps:{steps.profile_version}:{input_uri}",
        )
        model_job_id = uuid5(run_id, "model")
        product_job_id = uuid5(run_id, "products")
        destination = arguments.output_root.resolve() / str(run_id)
        if (destination / "manifest.json").is_file():
            reports.append(
                {"run_id": str(run_id), "issue_time": issue_time.isoformat(), "reused": True}
            )
            continue

        fields = PystepsLKFields(
            reflectivity_dbz=root[lk.motion.input_field][:],
            rate_mm_h=root["RATE_QPE"][:],
            quality_index=root["QUALITY_INDEX"][:],
            valid_mask=root["VALID_MASK"][:],
            low_quality_mask=root["LOW_QUALITY_MASK"][:],
        )
        started = time.perf_counter()
        result = run_pysteps_steps_fields(
            fields,
            profile=steps,
            lk_profile=lk,
            grid=grid,
        )
        algorithm_seconds = time.perf_counter() - started
        forecast_objects = build_ensemble_forecast_output_zarr_store(
            result,
            run_id=run_id,
            job_id=model_job_id,
            issue_time=issue_time,
            input_uri=input_uri,
            input_asset_ids=input_asset_ids,
            profile=steps,
            grid=grid,
            runtime_ms=round(algorithm_seconds * 1000),
        )
        forecast_sha = artifact_sha256(forecast_objects)
        source_uri = f"s3://rainpulse/historical-ensemble/{run_id}/forecast.zarr"
        _publish_artifact(client, source_uri, forecast_objects, forecast_sha)
        bundle = build_ensemble_application_product_bundle(
            forecast_objects,
            source_forecast_uri=source_uri,
            source_forecast_sha256=forecast_sha,
            run_id=run_id,
            job_id=product_job_id,
            profile=products,
            grid=grid,
        )
        _write_bundle(arguments.output_root, run_id, bundle)
        reports.append(
            {
                "run_id": str(run_id),
                "issue_time": issue_time.isoformat(),
                "input_uri": input_uri,
                "forecast_uri": source_uri,
                "forecast_sha256": forecast_sha,
                "algorithm_seconds": round(algorithm_seconds, 3),
                "member_count": steps.ensemble.member_count,
                "first_lead_coverage_ratio": float(result.output_valid_mask[0].mean()),
                "last_lead_coverage_ratio": float(result.output_valid_mask[-1].mean()),
                "reused": False,
            }
        )
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)

    print(json.dumps({"processed": len(reports), "runs": reports}, ensure_ascii=False))


def _load_catalog(path: Path) -> list[str]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("catalog must be a JSON array")
    uris: list[str] = []
    for item in raw:
        uri = item if isinstance(item, str) else item.get("input_uri") if isinstance(item, dict) else None
        if not isinstance(uri, str):
            raise ValueError("each catalog entry must contain input_uri")
        parse_s3_uri(uri)
        uris.append(uri)
    return list(dict.fromkeys(uris))


def _open(objects: dict[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update(objects)
    return zarr.open_group(store=store, mode="r")


def _publish_artifact(
    client: Minio,
    uri: str,
    objects: dict[str, bytes],
    digest: str,
) -> None:
    bucket, prefix = parse_s3_uri(uri)
    data_prefix = f"_objects/{digest}"
    manifest = []
    for key, value in sorted(objects.items()):
        object_key = f"{prefix}/{data_prefix}/{key}"
        client.put_object(bucket, object_key, io.BytesIO(value), len(value))
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    marker = json.dumps(
        {
            "schema_version": "2.0",
            "sha256": digest,
            "size_bytes": sum(len(value) for value in objects.values()),
            "data_prefix": data_prefix,
            "objects": manifest,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    client.put_object(
        bucket,
        f"{prefix}/_SUCCESS.json",
        io.BytesIO(marker),
        len(marker),
        content_type="application/json",
    )


def _write_bundle(root: Path, run_id: UUID, objects: dict[str, bytes]) -> None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / str(run_id)
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite existing bundle {destination}")
    with tempfile.TemporaryDirectory(prefix=f".{run_id}-", dir=root) as temporary:
        staging = Path(temporary)
        for relative_path, data in objects.items():
            path = staging / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        # mkdtemp creates the staging root with mode 0700.  The published
        # bundle is read through a read-only bind mount by the API container,
        # which intentionally runs under a different UID.
        staging.chmod(0o755)
        staging.rename(destination)


if __name__ == "__main__":
    main()
