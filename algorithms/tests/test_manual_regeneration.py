from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest


def _load_steps_backfill() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "backfill_historical_steps.py"
    spec = importlib.util.spec_from_file_location("backfill_historical_steps", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load STEPS backfill module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


steps_backfill = _load_steps_backfill()


def test_steps_backfill_accepts_direct_inputs_without_catalog() -> None:
    values = steps_backfill._load_catalog(
        None,
        [
            "s3://rainpulse/nowcast-input/first/input.zarr",
            "s3://rainpulse/nowcast-input/first/input.zarr",
            "s3://rainpulse/nowcast-input/second/input.zarr",
        ],
    )
    assert values == [
        "s3://rainpulse/nowcast-input/first/input.zarr",
        "s3://rainpulse/nowcast-input/second/input.zarr",
    ]
    with pytest.raises(ValueError, match="either --catalog or --input-uri"):
        steps_backfill._load_catalog(None, [])


def test_steps_bundle_publication_keeps_one_cycle_version(tmp_path) -> None:
    issue_time = "2026-08-28T02:30:00+00:00"
    grid_id = "fujian-grid"
    old_id = uuid4()
    current_id = uuid4()
    unrelated_id = uuid4()

    def bundle(run_id: object, cycle: str = issue_time) -> dict[str, bytes]:
        return {
            "manifest.json": json.dumps(
                {
                    "contract_name": "rainpulse.ensemble-application-product-bundle",
                    "bundle_id": str(run_id),
                    "run_id": str(run_id),
                    "issue_time": cycle,
                    "grid_id": grid_id,
                }
            ).encode(),
            "frames/lead-005.png": str(run_id).encode(),
        }

    steps_backfill._write_bundle(tmp_path, old_id, bundle(old_id))
    steps_backfill._write_bundle(
        tmp_path,
        unrelated_id,
        bundle(unrelated_id, "2026-08-28T02:35:00+00:00"),
    )
    steps_backfill._write_bundle(tmp_path, current_id, bundle(current_id))
    steps_backfill._prune_cycle_versions(tmp_path, current_id, bundle(current_id))

    assert not (tmp_path / str(old_id)).exists()
    assert (tmp_path / str(current_id)).is_dir()
    assert (tmp_path / str(unrelated_id)).is_dir()


def test_forced_steps_regeneration_uses_a_fresh_identity() -> None:
    input_uri = "s3://rainpulse/nowcast-input/source/input.zarr"
    stable = steps_backfill._regeneration_run_id(
        profile_version="steps-v1", input_uri=input_uri, force=False
    )
    repeated = steps_backfill._regeneration_run_id(
        profile_version="steps-v1", input_uri=input_uri, force=False
    )
    forced = steps_backfill._regeneration_run_id(
        profile_version="steps-v1", input_uri=input_uri, force=True
    )
    assert stable == repeated
    assert forced != stable
