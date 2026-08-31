from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import UUID


def _load_backfill_script() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "backfill_historical_steps.py"
    spec = importlib.util.spec_from_file_location("backfill_historical_steps", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_bundle_is_readable_by_a_different_runtime_uid(tmp_path: Path) -> None:
    script = _load_backfill_script()
    run_id = UUID("8ccbcb50-b27b-5871-8e7c-87e8dea335b2")

    script._write_bundle(
        tmp_path,
        run_id,
        {
            "manifest.json": b"{}",
            "probability-gt-1/lead-005/layer.png": b"png",
        },
    )

    destination = tmp_path / str(run_id)
    assert destination.stat().st_mode & 0o777 == 0o755
    assert (destination / "manifest.json").read_bytes() == b"{}"
    assert (
        destination / "probability-gt-1" / "lead-005" / "layer.png"
    ).read_bytes() == b"png"
