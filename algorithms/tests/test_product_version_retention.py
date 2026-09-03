from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from rainpulse_algo.products.version_retention import (
    ProductRetentionError,
    apply_product_retention,
    plan_product_retention,
    retention_report,
    scan_product_versions,
)


def write_bundle(
    root: Path,
    *,
    issue_time: datetime,
    created_at: datetime,
    contract_name: str = "rainpulse.nowcastnet-shadow-product-bundle",
    model_id: str = "nowcastnet",
    bundle_id: str | None = None,
) -> Path:
    identity = bundle_id or str(uuid4())
    path = root / identity
    path.mkdir(parents=True)
    (path / "payload.bin").write_bytes(b"payload" * 10)
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "contract_name": contract_name,
                "contract_version": "1.1",
                "bundle_id": identity,
                "issue_time": issue_time.isoformat(),
                "grid_id": "fujian-grid",
                "model_id": model_id,
                "created_at": created_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_retention_keeps_current_and_previous_per_cycle(tmp_path: Path) -> None:
    issue = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    paths = [
        write_bundle(
            tmp_path,
            issue_time=issue,
            created_at=issue + timedelta(minutes=index),
        )
        for index in range(4)
    ]
    other_cycle = write_bundle(
        tmp_path,
        issue_time=issue + timedelta(minutes=5),
        created_at=issue + timedelta(minutes=10),
    )

    plan = plan_product_retention(tmp_path, keep_versions=2)
    assert [item.path for item in plan.retained] == [
        paths[2],
        paths[3],
        other_cycle,
    ]
    assert [item.path for item in plan.removed] == [paths[0], paths[1]]

    deleted = apply_product_retention(plan)
    assert set(deleted) == {paths[0], paths[1]}
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()
    assert paths[3].exists()
    assert other_cycle.exists()


def test_dry_run_reports_reclaimable_bytes_without_deleting(tmp_path: Path) -> None:
    issue = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    paths = [
        write_bundle(
            tmp_path,
            issue_time=issue,
            created_at=issue + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    plan = plan_product_retention(tmp_path, keep_versions=2)
    deleted = apply_product_retention(plan, dry_run=True)
    report = retention_report(plan, dry_run=True, deleted=deleted)
    assert deleted == ()
    assert report["removed_count"] == 1
    assert report["reclaimable_bytes"] > 0
    assert all(path.exists() for path in paths)


def test_unrelated_contract_and_hidden_directories_are_never_selected(
    tmp_path: Path,
) -> None:
    issue = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    write_bundle(
        tmp_path,
        issue_time=issue,
        created_at=issue,
        contract_name="unrelated.bundle",
    )
    hidden = tmp_path / ".retention-trash"
    hidden.mkdir()
    assert scan_product_versions(tmp_path) == ()


def test_manifest_change_aborts_before_deletion(tmp_path: Path) -> None:
    issue = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
    older = write_bundle(
        tmp_path,
        issue_time=issue,
        created_at=issue,
    )
    write_bundle(
        tmp_path,
        issue_time=issue,
        created_at=issue + timedelta(minutes=1),
    )
    plan = plan_product_retention(tmp_path, keep_versions=1)
    with (older / "manifest.json").open("a", encoding="utf-8") as target:
        target.write("\n")
    with pytest.raises(ProductRetentionError, match="changed"):
        apply_product_retention(plan)
    assert older.exists()
