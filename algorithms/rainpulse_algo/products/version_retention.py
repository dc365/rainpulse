from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SUPPORTED_DERIVED_PRODUCT_CONTRACTS = frozenset(
    {
        "rainpulse.ensemble-application-product-bundle",
        "rainpulse.nowcastnet-shadow-product-bundle",
    }
)
MAXIMUM_MANIFEST_BYTES = 2 * 1024 * 1024


class ProductRetentionError(RuntimeError):
    """Raised when a derived-product directory cannot be pruned safely."""


@dataclass(frozen=True)
class ProductVersion:
    root: Path
    path: Path
    contract_name: str
    bundle_id: str
    issue_time: datetime
    grid_id: str
    model_id: str
    created_at: datetime
    manifest_sha256: str
    size_bytes: int

    @property
    def cycle_key(self) -> tuple[str, str, str, datetime]:
        return (
            self.contract_name,
            self.grid_id,
            self.model_id,
            self.issue_time,
        )


@dataclass(frozen=True)
class RetentionPlan:
    root: Path
    keep_versions: int
    retained: tuple[ProductVersion, ...]
    removed: tuple[ProductVersion, ...]

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.size_bytes for item in self.removed)


def scan_product_versions(
    root: str | Path,
    *,
    allowed_contracts: Iterable[str] = SUPPORTED_DERIVED_PRODUCT_CONTRACTS,
) -> tuple[ProductVersion, ...]:
    directory = Path(root).resolve()
    if not directory.exists():
        return ()
    if not directory.is_dir() or directory.is_symlink():
        raise ProductRetentionError(
            f"derived-product root must be a real directory: {directory}"
        )
    allowed = frozenset(allowed_contracts)
    versions: list[ProductVersion] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or not path.is_dir() or path.is_symlink():
            continue
        manifest_path = path / "manifest.json"
        try:
            info = manifest_path.stat()
        except FileNotFoundError:
            continue
        if (
            not manifest_path.is_file()
            or manifest_path.is_symlink()
            or info.st_size < 2
            or info.st_size > MAXIMUM_MANIFEST_BYTES
        ):
            continue
        data = manifest_path.read_bytes()
        try:
            manifest = json.loads(data)
        except json.JSONDecodeError:
            continue
        version = _parse_version(
            directory,
            path,
            manifest,
            manifest_sha256=hashlib.sha256(data).hexdigest(),
            allowed_contracts=allowed,
        )
        if version is not None:
            versions.append(version)
    return tuple(versions)


def plan_product_retention(
    root: str | Path,
    *,
    keep_versions: int = 1,
    allowed_contracts: Iterable[str] = SUPPORTED_DERIVED_PRODUCT_CONTRACTS,
) -> RetentionPlan:
    if keep_versions < 1 or keep_versions > 10:
        raise ValueError("keep_versions must be between 1 and 10")
    directory = Path(root).resolve()
    grouped: dict[
        tuple[str, str, str, datetime],
        list[ProductVersion],
    ] = defaultdict(list)
    for item in scan_product_versions(
        directory,
        allowed_contracts=allowed_contracts,
    ):
        grouped[item.cycle_key].append(item)

    retained: list[ProductVersion] = []
    removed: list[ProductVersion] = []
    for versions in grouped.values():
        versions.sort(
            key=lambda item: (item.created_at, item.bundle_id),
            reverse=True,
        )
        retained.extend(versions[:keep_versions])
        removed.extend(versions[keep_versions:])
    retained.sort(key=_report_order)
    removed.sort(key=_report_order)
    return RetentionPlan(
        root=directory,
        keep_versions=keep_versions,
        retained=tuple(retained),
        removed=tuple(removed),
    )


def apply_product_retention(
    plan: RetentionPlan,
    *,
    dry_run: bool = False,
) -> tuple[Path, ...]:
    if dry_run or not plan.removed:
        return ()
    root = plan.root.resolve()
    trash = root / ".retention-trash"
    trash.mkdir(mode=0o700, parents=True, exist_ok=True)
    deleted: list[Path] = []
    for item in plan.removed:
        _verify_planned_version(item, root)
        quarantine = trash / f"{item.bundle_id}-{uuid4()}"
        os.replace(item.path, quarantine)
        shutil.rmtree(quarantine)
        deleted.append(item.path)
    try:
        trash.rmdir()
    except OSError:
        pass
    return tuple(deleted)


def retention_report(
    plan: RetentionPlan,
    *,
    dry_run: bool,
    deleted: Iterable[Path] = (),
) -> dict[str, Any]:
    deleted_names = {path.name for path in deleted}
    return {
        "schema_version": "1.0",
        "root": str(plan.root),
        "keep_versions": plan.keep_versions,
        "dry_run": dry_run,
        "retained_count": len(plan.retained),
        "removed_count": len(plan.removed),
        "deleted_count": len(deleted_names),
        "reclaimable_bytes": plan.reclaimable_bytes,
        "retained": [_version_report(item) for item in plan.retained],
        "removed": [
            {
                **_version_report(item),
                "deleted": item.bundle_id in deleted_names,
            }
            for item in plan.removed
        ],
    }


def _parse_version(
    root: Path,
    path: Path,
    manifest: Any,
    *,
    manifest_sha256: str,
    allowed_contracts: frozenset[str],
) -> ProductVersion | None:
    if not isinstance(manifest, dict):
        return None
    contract_name = manifest.get("contract_name")
    bundle_id = manifest.get("bundle_id")
    grid_id = manifest.get("grid_id")
    model_id = manifest.get("model_id")
    if (
        contract_name not in allowed_contracts
        or not isinstance(bundle_id, str)
        or bundle_id != path.name
        or not isinstance(grid_id, str)
        or not grid_id
        or not isinstance(model_id, str)
        or not model_id
    ):
        return None
    try:
        issue_time = _parse_time(manifest.get("issue_time"), "issue_time")
        created_at = _parse_time(manifest.get("created_at"), "created_at")
    except ProductRetentionError:
        return None
    return ProductVersion(
        root=root,
        path=path,
        contract_name=contract_name,
        bundle_id=bundle_id,
        issue_time=issue_time,
        grid_id=grid_id,
        model_id=model_id,
        created_at=created_at,
        manifest_sha256=manifest_sha256,
        size_bytes=_directory_size(path),
    )


def _parse_time(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ProductRetentionError(f"product manifest has no {name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductRetentionError(
            f"product manifest has invalid {name}"
        ) from exc
    if parsed.utcoffset() is None:
        raise ProductRetentionError(
            f"product manifest {name} lacks an offset"
        )
    return parsed.astimezone(UTC)


def _directory_size(path: Path) -> int:
    total = 0
    for parent, directories, files in os.walk(path, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if not (Path(parent) / name).is_symlink()
        ]
        for name in files:
            candidate = Path(parent) / name
            if candidate.is_symlink():
                continue
            try:
                total += candidate.stat().st_size
            except FileNotFoundError:
                continue
    return total


def _verify_planned_version(item: ProductVersion, root: Path) -> None:
    try:
        relative = item.path.resolve().relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise ProductRetentionError(
            f"planned product escaped its root: {item.path}"
        ) from exc
    if len(relative.parts) != 1 or item.path.is_symlink() or not item.path.is_dir():
        raise ProductRetentionError(
            f"planned product path is no longer safe: {item.path}"
        )
    manifest_path = item.path / "manifest.json"
    try:
        current = manifest_path.read_bytes()
    except OSError as exc:
        raise ProductRetentionError(
            f"planned product manifest disappeared: {manifest_path}"
        ) from exc
    if hashlib.sha256(current).hexdigest() != item.manifest_sha256:
        raise ProductRetentionError(
            f"planned product manifest changed before deletion: {manifest_path}"
        )


def _version_report(item: ProductVersion) -> dict[str, Any]:
    return {
        "contract_name": item.contract_name,
        "bundle_id": item.bundle_id,
        "issue_time": item.issue_time.isoformat(),
        "grid_id": item.grid_id,
        "model_id": item.model_id,
        "created_at": item.created_at.isoformat(),
        "size_bytes": item.size_bytes,
    }


def _report_order(item: ProductVersion) -> tuple[str, str, datetime, datetime, str]:
    return (
        item.contract_name,
        item.grid_id,
        item.issue_time,
        item.created_at,
        item.bundle_id,
    )
