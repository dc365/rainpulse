from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

NOAA_SOURCE_ID = "noaa-mrms-pds-CONUS-PrecipRate_00.00-v1"
NOAA_BASE_URL = "https://noaa-mrms-pds.s3.amazonaws.com"
IEM_SOURCE_ID = "iem-mtarchive-MRMS-NCEP-PrecipRate-v1"
IEM_BASE_URL = "https://mtarchive.geol.iastate.edu"
PRODUCT = "PrecipRate_00.00"
NOAA_ARCHIVE_START = date(2020, 10, 14)
IEM_ARCHIVE_START = date(2019, 1, 1)
SOURCE_CADENCE_MINUTES = 2
S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
FILENAME_PATTERN = re.compile(
    r"^(?:MRMS_)?PrecipRate_00\.00_(?P<day>\d{8})-(?P<hour>\d{2})"
    r"(?P<minute>\d{2})(?P<second>\d{2})\.grib2\.gz$"
)


class MRMSArchiveError(RuntimeError):
    """Raised when MRMS discovery, download, or verification fails."""


@dataclass(frozen=True)
class MRMSObject:
    key: str
    size_bytes: int | None
    etag: str
    valid_time: datetime
    source_id: str = NOAA_SOURCE_ID
    source_url: str | None = None
    storage_namespace: str = "noaa-mrms-pds"

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def download_url(self) -> str:
        return self.source_url or f"{NOAA_BASE_URL}/{self.key}"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def iter_dates(start: date, end: date) -> Iterable[date]:
    if end < start:
        raise MRMSArchiveError("end date must not be before start date")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def validate_cadence(cadence_minutes: int) -> None:
    if cadence_minutes < SOURCE_CADENCE_MINUTES:
        raise MRMSArchiveError(f"cadence must be at least {SOURCE_CADENCE_MINUTES} minutes")
    if cadence_minutes % SOURCE_CADENCE_MINUTES or 60 % cadence_minutes:
        raise MRMSArchiveError(
            "cadence must be a multiple of the two-minute source cadence and divide one hour"
        )


def _curl_base(proxy: str | None) -> list[str]:
    curl = shutil.which("curl")
    if curl is None:
        raise MRMSArchiveError("curl is required for MRMS archive access")
    command = [
        curl,
        "--fail",
        "--location",
        "--retry",
        "5",
        "--retry-connrefused",
        "--retry-all-errors",
        "--connect-timeout",
        "15",
        "--max-time",
        "600",
        "--silent",
        "--show-error",
    ]
    if proxy:
        command.extend(["--proxy", proxy])
    return command


def _list_day_xml(day: date, proxy: str | None) -> bytes:
    prefix = f"CONUS/{PRODUCT}/{day:%Y%m%d}/"
    command = _curl_base(proxy)
    command.extend(
        [
            "--get",
            NOAA_BASE_URL + "/",
            "--data-urlencode",
            "list-type=2",
            "--data-urlencode",
            f"prefix={prefix}",
            "--data-urlencode",
            "max-keys=1000",
        ]
    )
    try:
        return subprocess.run(command, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip()
        raise MRMSArchiveError(f"S3 listing failed for {day}: {stderr}") from exc


def _object_from_filename(
    filename: str,
    *,
    key: str,
    size_bytes: int | None,
    etag: str,
    cadence_minutes: int,
    source_id: str = NOAA_SOURCE_ID,
    source_url: str | None = None,
    storage_namespace: str = "noaa-mrms-pds",
) -> MRMSObject | None:
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    if second != 0 or minute % cadence_minutes:
        return None
    valid_time = datetime.strptime(
        match.group("day") + match.group("hour") + match.group("minute") + match.group("second"),
        "%Y%m%d%H%M%S",
    ).replace(tzinfo=UTC)
    return MRMSObject(
        key=key,
        size_bytes=size_bytes,
        etag=etag,
        valid_time=valid_time,
        source_id=source_id,
        source_url=source_url,
        storage_namespace=storage_namespace,
    )


def parse_listing(payload: bytes, cadence_minutes: int) -> tuple[MRMSObject, ...]:
    validate_cadence(cadence_minutes)
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MRMSArchiveError(f"invalid S3 listing XML: {exc}") from exc
    if root.findtext("s3:IsTruncated", namespaces=S3_NAMESPACE) == "true":
        raise MRMSArchiveError("daily S3 listing was truncated")

    objects: list[MRMSObject] = []
    for item in root.findall("s3:Contents", S3_NAMESPACE):
        key = item.findtext("s3:Key", namespaces=S3_NAMESPACE) or ""
        obj = _object_from_filename(
            key.rsplit("/", 1)[-1],
            key=key,
            size_bytes=int(item.findtext("s3:Size", namespaces=S3_NAMESPACE) or 0),
            etag=(item.findtext("s3:ETag", namespaces=S3_NAMESPACE) or "").strip('"'),
            cadence_minutes=cadence_minutes,
        )
        if obj is not None and obj.size_bytes > 0:
            objects.append(obj)
    return tuple(sorted(objects, key=lambda item: item.valid_time))


def _list_iem_day_html(day: date, proxy: str | None) -> bytes:
    url = f"{IEM_BASE_URL}/{day:%Y/%m/%d}/mrms/ncep/PrecipRate/"
    command = _curl_base(proxy)
    command.append(url)
    try:
        return subprocess.run(command, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip()
        raise MRMSArchiveError(f"IEM listing failed for {day}: {stderr}") from exc


def parse_iem_listing(day: date, payload: bytes, cadence_minutes: int) -> tuple[MRMSObject, ...]:
    validate_cadence(cadence_minutes)
    expected_day = f"{day:%Y%m%d}"
    filenames = sorted(
        {
            match.group(0)
            for match in re.finditer(
                rf"(?:MRMS_)?PrecipRate_00\.00_{expected_day}-\d{{6}}\.grib2\.gz",
                payload.decode(errors="replace"),
            )
        }
    )
    objects: list[MRMSObject] = []
    for filename in filenames:
        key = f"{day:%Y/%m/%d}/mrms/ncep/PrecipRate/{filename}"
        obj = _object_from_filename(
            filename,
            key=key,
            size_bytes=None,
            etag="",
            cadence_minutes=cadence_minutes,
            source_id=IEM_SOURCE_ID,
            source_url=f"{IEM_BASE_URL}/{key}",
            storage_namespace="iem-mtarchive",
        )
        if obj is not None:
            objects.append(obj)
    return tuple(sorted(objects, key=lambda item: item.valid_time))


def list_day(day: date, cadence_minutes: int, proxy: str | None) -> tuple[MRMSObject, ...]:
    if day >= NOAA_ARCHIVE_START:
        noaa_objects = parse_listing(_list_day_xml(day, proxy), cadence_minutes)
        expected_count = 24 * 60 // cadence_minutes
        if len(noaa_objects) >= expected_count:
            return noaa_objects
        try:
            iem_objects = parse_iem_listing(day, _list_iem_day_html(day, proxy), cadence_minutes)
        except MRMSArchiveError:
            return noaa_objects
        by_valid_time = {item.valid_time: item for item in iem_objects}
        by_valid_time.update({item.valid_time: item for item in noaa_objects})
        return tuple(sorted(by_valid_time.values(), key=lambda item: item.valid_time))
    if day >= IEM_ARCHIVE_START:
        return parse_iem_listing(day, _list_iem_day_html(day, proxy), cadence_minutes)
    return ()


def relative_object_path(item: MRMSObject, cadence_minutes: int) -> Path:
    timestamp = item.valid_time
    return (
        Path("raw")
        / item.storage_namespace
        / "CONUS"
        / PRODUCT
        / f"{cadence_minutes}min"
        / f"{timestamp:%Y}"
        / f"{timestamp:%m}"
        / f"{timestamp:%d}"
        / item.filename
    )


def manifest_path(root: Path, day: date, cadence_minutes: int) -> Path:
    return (
        root
        / "manifests"
        / PRODUCT
        / f"{cadence_minutes}min"
        / f"{day:%Y}"
        / f"{day:%m}"
        / f"{day:%d}.json"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_source_etag(path: Path, etag: str) -> bool:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", etag):
        return True
    digest = hashlib.md5(usedforsecurity=False)  # noqa: S324 - S3 integrity only
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == etag.lower()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


@contextmanager
def download_lock(root: Path):
    path = root / ".mrms-download.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MRMSArchiveError(f"another MRMS download holds {path}") from exc
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _download_one(
    item: MRMSObject,
    root: Path,
    cadence_minutes: int,
    proxy: str | None,
) -> dict[str, Any]:
    relative_path = relative_object_path(item, cadence_minutes)
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")

    expected_size = item.size_bytes
    destination_is_valid = destination.is_file() and destination.stat().st_size > 0
    if destination_is_valid and expected_size is not None:
        destination_is_valid = destination.stat().st_size == expected_size
    if destination_is_valid:
        destination_is_valid = _matches_source_etag(destination, item.etag)
    if destination_is_valid:
        status = "existing"
    else:
        if destination.exists():
            destination.unlink()
        if (
            expected_size is not None
            and partial.is_file()
            and partial.stat().st_size > expected_size
        ):
            partial.unlink()
        if (
            expected_size is not None
            and partial.is_file()
            and partial.stat().st_size == expected_size
            and not _matches_source_etag(partial, item.etag)
        ):
            partial.unlink()

        for attempt in range(2):
            partial_is_ready = (
                expected_size is not None
                and partial.is_file()
                and partial.stat().st_size > 0
                and partial.stat().st_size == expected_size
                and _matches_source_etag(partial, item.etag)
            )
            if not partial_is_ready:
                if attempt == 1 and partial.exists():
                    partial.unlink()
                command = _curl_base(proxy)
                command.extend(
                    [
                        "--continue-at",
                        "-",
                        "--output",
                        str(partial),
                        item.download_url,
                    ]
                )
                try:
                    subprocess.run(command, check=True)
                except subprocess.CalledProcessError as exc:
                    if attempt == 0:
                        continue
                    raise MRMSArchiveError(f"download failed for {item.key}") from exc
            partial_is_ready = partial.is_file() and partial.stat().st_size > 0
            if partial_is_ready and expected_size is not None:
                partial_is_ready = partial.stat().st_size == expected_size
            if partial_is_ready and _matches_source_etag(partial, item.etag):
                break
            actual_size = partial.stat().st_size if partial.is_file() else 0
            if attempt == 1:
                if expected_size is None:
                    raise MRMSArchiveError(f"empty download for {item.key}")
                raise MRMSArchiveError(
                    f"size mismatch for {item.key}: expected {expected_size}, got {actual_size}"
                )
        os.replace(partial, destination)
        status = "downloaded"

    actual_size = destination.stat().st_size
    return {
        "etag": item.etag,
        "relative_path": relative_path.as_posix(),
        "sha256": sha256_file(destination),
        "size_bytes": actual_size,
        "source_id": item.source_id,
        "source_key": item.key,
        "source_url": item.download_url,
        "status": status,
        "valid_time": item.valid_time.isoformat().replace("+00:00", "Z"),
    }


def _expected_times(day: date, cadence_minutes: int) -> tuple[str, ...]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return tuple(
        (start + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")
        for offset in range(0, 24 * 60, cadence_minutes)
    )


def _day_manifest(
    day: date,
    cadence_minutes: int,
    objects: tuple[MRMSObject, ...],
    assets: list[dict[str, Any]],
    failures: list[dict[str, str]],
) -> dict[str, Any]:
    expected = set(_expected_times(day, cadence_minutes))
    available = {item.valid_time.isoformat().replace("+00:00", "Z") for item in objects}
    return {
        "archive_start": IEM_ARCHIVE_START.isoformat(),
        "assets": sorted(assets, key=lambda asset: asset["valid_time"]),
        "available_count": len(objects),
        "cadence_minutes": cadence_minutes,
        "complete": not failures and available == expected,
        "day": day.isoformat(),
        "downloaded_bytes": sum(int(asset["size_bytes"]) for asset in assets),
        "expected_count": len(expected),
        "failures": failures,
        "generated_at": datetime.now(UTC).isoformat(),
        "missing_source_times": sorted(expected - available),
        "product": PRODUCT,
        "source_cadence_minutes": SOURCE_CADENCE_MINUTES,
        "source_ids": sorted({item.source_id for item in objects}),
        "source_supported": day >= IEM_ARCHIVE_START,
    }


def write_dataset_metadata(root: Path, cadence_minutes: int) -> None:
    _write_json_atomic(
        root / "dataset.json",
        {
            "archive_start": IEM_ARCHIVE_START.isoformat(),
            "cadence_minutes": cadence_minutes,
            "directory_layouts": [
                (f"raw/noaa-mrms-pds/CONUS/PrecipRate_00.00/{cadence_minutes}min/YYYY/MM/DD"),
                (f"raw/iem-mtarchive/CONUS/PrecipRate_00.00/{cadence_minutes}min/YYYY/MM/DD"),
            ],
            "product": PRODUCT,
            "source_cadence_minutes": SOURCE_CADENCE_MINUTES,
            "sources": [
                {
                    "archive_start": NOAA_ARCHIVE_START.isoformat(),
                    "base_url": NOAA_BASE_URL,
                    "id": NOAA_SOURCE_ID,
                    "license_url": "https://registry.opendata.aws/noaa-mrms-pds/",
                    "role": "preferred",
                },
                {
                    "archive_start": IEM_ARCHIVE_START.isoformat(),
                    "base_url": IEM_BASE_URL,
                    "id": IEM_SOURCE_ID,
                    "role": "historical_before_noaa_s3",
                },
            ],
        },
    )


def download_day(
    day: date,
    root: Path,
    *,
    cadence_minutes: int,
    workers: int,
    proxy: str | None,
) -> dict[str, Any]:
    objects = list_day(day, cadence_minutes, proxy)
    assets: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    if objects:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_download_one, item, root, cadence_minutes, proxy): item
                for item in objects
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    assets.append(future.result())
                except Exception as exc:  # noqa: BLE001 - each asset failure is recorded
                    failures.append({"source_key": item.key, "error": str(exc)})
    manifest = _day_manifest(day, cadence_minutes, objects, assets, failures)
    _write_json_atomic(manifest_path(root, day, cadence_minutes), manifest)
    return manifest


def download_range(
    start: date,
    end: date,
    root: Path,
    *,
    cadence_minutes: int,
    workers: int,
    proxy: str | None,
) -> Path:
    validate_cadence(cadence_minutes)
    if workers < 1 or workers > 32:
        raise MRMSArchiveError("workers must be between 1 and 32")
    requested_days = tuple(iter_dates(start, end))
    run_started = datetime.now(UTC)
    summaries: list[dict[str, Any]] = []
    write_dataset_metadata(root, cadence_minutes)
    with download_lock(root):
        for index, day in enumerate(requested_days, start=1):
            try:
                manifest = download_day(
                    day,
                    root,
                    cadence_minutes=cadence_minutes,
                    workers=workers,
                    proxy=proxy,
                )
            except MRMSArchiveError as exc:
                manifest = _day_manifest(
                    day,
                    cadence_minutes,
                    (),
                    [],
                    [{"source_key": f"listing:{day}", "error": str(exc)}],
                )
                _write_json_atomic(manifest_path(root, day, cadence_minutes), manifest)
            summaries.append(
                {
                    "available_count": manifest["available_count"],
                    "complete": manifest["complete"],
                    "day": manifest["day"],
                    "downloaded_bytes": manifest["downloaded_bytes"],
                    "failure_count": len(manifest["failures"]),
                    "missing_source_count": len(manifest["missing_source_times"]),
                    "source_supported": manifest["source_supported"],
                }
            )
            print(
                f"[{index}/{len(requested_days)}] {day} "
                f"assets={manifest['available_count']} failures={len(manifest['failures'])} "
                f"complete={manifest['complete']}",
                flush=True,
            )

    run_finished = datetime.now(UTC)
    run_id = f"{start:%Y%m%d}_{end:%Y%m%d}_{run_started:%Y%m%dT%H%M%SZ}"
    run_path = root / "runs" / f"{run_id}.json"
    _write_json_atomic(
        run_path,
        {
            "cadence_minutes": cadence_minutes,
            "days": summaries,
            "end": end.isoformat(),
            "finished_at": run_finished.isoformat(),
            "requested_day_count": len(requested_days),
            "source_ids": [NOAA_SOURCE_ID, IEM_SOURCE_ID],
            "start": start.isoformat(),
            "started_at": run_started.isoformat(),
            "summary": {
                "available_asset_count": sum(day["available_count"] for day in summaries),
                "downloaded_bytes": sum(day["downloaded_bytes"] for day in summaries),
                "failure_count": sum(day["failure_count"] for day in summaries),
                "incomplete_day_count": sum(not day["complete"] for day in summaries),
                "source_unsupported_day_count": sum(
                    not day["source_supported"] for day in summaries
                ),
            },
            "workers": workers,
        },
    )
    if any(day["failure_count"] for day in summaries):
        raise MRMSArchiveError(f"one or more downloads failed; inspect {run_path}")
    return run_path


def verify_range(
    start: date,
    end: date,
    root: Path,
    *,
    cadence_minutes: int,
    full_hash: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    asset_count = 0
    total_bytes = 0
    incomplete_days: list[str] = []
    for day in iter_dates(start, end):
        path = manifest_path(root, day, cadence_minutes)
        if not path.is_file():
            errors.append(f"missing manifest: {path}")
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not manifest.get("complete", False):
            incomplete_days.append(day.isoformat())
        for asset in manifest.get("assets", []):
            asset_count += 1
            target = root / asset["relative_path"]
            expected_size = int(asset["size_bytes"])
            if not target.is_file():
                errors.append(f"missing asset: {target}")
                continue
            actual_size = target.stat().st_size
            total_bytes += actual_size
            if actual_size != expected_size:
                errors.append(
                    f"size mismatch: {target} expected={expected_size} actual={actual_size}"
                )
            if full_hash and sha256_file(target) != asset["sha256"]:
                errors.append(f"sha256 mismatch: {target}")
    transport_integrity = not errors
    source_completeness = not incomplete_days
    return {
        "asset_count": asset_count,
        "cadence_minutes": cadence_minutes,
        "complete": transport_integrity and source_completeness,
        "end": end.isoformat(),
        "errors": errors,
        "full_hash": full_hash,
        "incomplete_source_days": incomplete_days,
        "source_completeness": source_completeness,
        "start": start.isoformat(),
        "total_bytes": total_bytes,
        "transport_integrity": transport_integrity,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download NOAA MRMS precipitation-rate data")
    parser.add_argument("command", choices=("download", "verify"))
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cadence-minutes", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--proxy")
    parser.add_argument("--full-hash", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "download":
            run_path = download_range(
                args.start,
                args.end,
                args.root,
                cadence_minutes=args.cadence_minutes,
                workers=args.workers,
                proxy=args.proxy,
            )
            print(run_path)
        else:
            result = verify_range(
                args.start,
                args.end,
                args.root,
                cadence_minutes=args.cadence_minutes,
                full_hash=args.full_hash,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            if not result["complete"]:
                return 1
    except MRMSArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
