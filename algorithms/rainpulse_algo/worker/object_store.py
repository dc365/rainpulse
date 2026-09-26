from __future__ import annotations

from rainpulse_algo.performance import (timed as _perf_timed)

import hashlib
import io
import json
import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from minio import Minio
from minio.error import S3Error

from .asset_access import VerifiedArtifactReader
from .asset_cache import VerifiedObjectCache
from .contracts import JobCompleted

MAX_ARTIFACT_MARKER_BYTES = 16 * 1024 * 1024
DEFAULT_OBJECT_STORE_MAX_WORKERS = 4


def minio_client_from_environment() -> Minio:
    endpoint = urlparse(_required_environment("RAINPULSE_OBJECT_STORE_ENDPOINT"))
    if not endpoint.hostname:
        raise ValueError("RAINPULSE_OBJECT_STORE_ENDPOINT must include a hostname")
    client = Minio(
        endpoint.netloc,
        access_key=_required_environment("RAINPULSE_OBJECT_STORE_ACCESS_KEY"),
        secret_key=_required_environment("RAINPULSE_OBJECT_STORE_SECRET_KEY"),
        secure=endpoint.scheme == "https",
    )
    # Do not retain or expose credentials in the cache key/metrics. Different
    # endpoints or principals must never share an object cache namespace.
    scope = endpoint.geturl() + "\0" + _required_environment("RAINPULSE_OBJECT_STORE_ACCESS_KEY")
    client._rainpulse_cache_namespace = hashlib.sha256(scope.encode()).hexdigest()
    return client


@dataclass(frozen=True)
class PublishedObject:
    asset_uri: str
    sha256: str
    size_bytes: int
    marker_key: str
    completion: JobCompleted
    reused: bool = False
    object_count: int = 0
    upload_ms: float = 0.0
    marker_commit_ms: float = 0.0


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    key = parsed.path.strip("/")
    key_path = PurePosixPath(key)
    if (
        parsed.scheme != "s3"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or not key
        or ".." in key_path.parts
        or str(key_path) != key
    ):
        raise ValueError(f"expected s3 URI, got {uri!r}")
    return parsed.netloc, key


class AtomicObjectPublisher:
    """Publishes immutable content and claims the stable marker exactly once."""

    def __init__(self, client: Minio, max_workers: int | None = None) -> None:
        self._client = client
        self._max_workers = _resolve_max_workers(max_workers)

    def load_completion(
        self,
        output_prefix: str,
        artifact_name: str = "forecast.zarr",
    ) -> JobCompleted | None:
        bucket, prefix = parse_s3_uri(output_prefix)
        marker_key = self._marker_key(prefix, artifact_name)
        try:
            marker = self._load_marker(bucket, marker_key)
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject"}:
                return None
            raise
        return JobCompleted.model_validate(marker["completion_event"])

    @_perf_timed("io.atomic_publication")
    def publish(
        self,
        *,
        output_prefix: str,
        job_id: UUID,
        data: bytes | None,
        completion: JobCompleted,
        artifact_name: str = "forecast.zarr",
        objects: Mapping[str, bytes] | None = None,
    ) -> PublishedObject:
        bucket, prefix = parse_s3_uri(output_prefix)
        prefix = prefix.rstrip("/")
        payloads = normalize_artifact_objects(data=data, objects=objects)
        marker_key = self._marker_key(prefix, artifact_name)
        digest = artifact_sha256(payloads)
        total_size = sum(len(value) for value in payloads.values())
        packed_entries = None
        if os.getenv("RAINPULSE_QC_PACKED_STORAGE", "0") == "1" and "qc/summary.json" in payloads:
            payloads, packed_entries = _pack_objects(payloads)
        data_prefix = f"_objects/{digest}"
        expected_asset_uri = f"s3://{bucket}/{prefix}/{artifact_name}"
        matching_assets = [
            asset for asset in completion.payload.assets if asset.uri == expected_asset_uri
        ]
        if (
            completion.job_id != job_id
            or len(matching_assets) != 1
            or matching_assets[0].sha256 != digest
            or matching_assets[0].size_bytes != total_size
        ):
            raise ValueError("completion asset identity differs from the artifact bundle")
        diagnostics = dict(completion.payload.diagnostics)
        diagnostics["artifact_publication"] = {
            "schema_version": "3.0" if packed_entries is not None else "2.0",
            "data_prefix": data_prefix,
        }
        committed_completion = completion.model_copy(
            update={
                "payload": completion.payload.model_copy(
                    update={"diagnostics": diagnostics}
                )
            }
        )
        upload_started = time.perf_counter()
        manifest = _bounded_parallel_map(
            list(payloads.items()),
            worker_count=self._max_workers,
            worker=self._publish_manifest_entry(bucket, prefix, artifact_name, data_prefix),
        )
        upload_ms = _elapsed_ms(upload_started)

        marker = json.dumps(
            {
                "schema_version": "3.0" if packed_entries is not None else "2.0",
                "sha256": digest,
                "size_bytes": total_size,
                "data_prefix": data_prefix,
                "objects": manifest,
                **({"packed_entries": packed_entries} if packed_entries is not None else {}),
                "completion_event": committed_completion.model_dump(mode="json"),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        if len(marker) > MAX_ARTIFACT_MARKER_BYTES:
            raise ValueError("artifact marker exceeds reader size limit")
        marker_started = time.perf_counter()
        try:
            self._put_marker_if_absent(bucket, marker_key, marker)
        except S3Error as error:
            marker_commit_ms = _elapsed_ms(marker_started)
            if error.code not in {"ConditionalRequestConflict", "PreconditionFailed"}:
                raise
            existing = self._load_marker(bucket, marker_key)
            existing_completion = JobCompleted.model_validate(existing["completion_event"])
            if existing_completion.job_id != job_id:
                raise RuntimeError(
                    "artifact marker is already owned by a different job"
                ) from error
            return PublishedObject(
                asset_uri=f"s3://{bucket}/{prefix}/{artifact_name}",
                sha256=str(existing["sha256"]),
                size_bytes=int(existing["size_bytes"]),
                marker_key=marker_key,
                completion=existing_completion,
                reused=True,
                object_count=_marker_object_count(existing),
                upload_ms=upload_ms,
                marker_commit_ms=marker_commit_ms,
            )
        marker_commit_ms = _elapsed_ms(marker_started)

        return PublishedObject(
            asset_uri=f"s3://{bucket}/{prefix}/{artifact_name}",
            sha256=digest,
            size_bytes=total_size,
            marker_key=marker_key,
            completion=committed_completion,
            object_count=len(manifest),
            upload_ms=upload_ms,
            marker_commit_ms=marker_commit_ms,
        )

    def _publish_manifest_entry(
        self,
        bucket: str,
        prefix: str,
        artifact_name: str,
        data_prefix: str,
    ) -> Callable[[tuple[str, bytes]], dict[str, Any]]:
        def publish_one(item: tuple[str, bytes]) -> dict[str, Any]:
            relative_key, value = item
            data_key = f"{prefix}/{artifact_name}/{data_prefix}/{relative_key}"
            self._put_bytes(bucket, data_key, value, _content_type(relative_key))
            published = self._client.stat_object(bucket, data_key)
            if published.size != len(value):
                raise RuntimeError("published object size validation failed")
            return {
                "key": relative_key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }

        return publish_one

    @_perf_timed("io.marker_commit")
    def _put_marker_if_absent(self, bucket: str, key: str, data: bytes) -> Any:
        return self._client._put_object(  # noqa: SLF001 - MinIO exposes no public conditional PUT
            bucket,
            key,
            data,
            headers={"Content-Type": "application/json", "If-None-Match": "*"},
        )

    def _put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> Any:
        return self._client.put_object(
            bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
        )

    def _load_marker(self, bucket: str, marker_key: str) -> dict[str, Any]:
        response = self._client.get_object(bucket, marker_key)
        try:
            marker = json.loads(response.read())
        finally:
            response.close()
            response.release_conn()
        if not isinstance(marker, dict):
            raise RuntimeError("published artifact marker must be an object")
        return marker

    @staticmethod
    def _marker_key(prefix: str, artifact_name: str) -> str:
        artifact_name = artifact_name.strip("/")
        if not artifact_name or artifact_name.startswith("_temporary") or ".." in artifact_name:
            raise ValueError("artifact_name must be a safe relative object name")
        return f"{prefix.rstrip('/')}/{artifact_name}/_SUCCESS.json"


class ArtifactObjectReader(VerifiedArtifactReader):
    """Compatible full reader plus verified selections and process-local cache."""

    def __init__(
        self,
        client: Minio,
        max_size_bytes: int | None = None,
        max_workers: int | None = None,
        *,
        cache_namespace: str | None = None,
        cache: VerifiedObjectCache | None = None,
    ) -> None:
        self._client = client
        if max_size_bytes is None:
            max_size_bytes = int(
                os.getenv("RAINPULSE_MAX_INPUT_ARTIFACT_BYTES", str(2 * 1024**3))
            )
        self._max_size_bytes = max_size_bytes
        self._max_workers = _resolve_max_workers(max_workers)
        namespace = cache_namespace or getattr(client, "_rainpulse_cache_namespace", None)
        if namespace is None:
            # Unknown external clients do not share cache entries accidentally.
            # The normal environment factory attaches a stable, non-secret scope.
            namespace = "unshared-" + os.urandom(16).hex()
        super().__init__(
            self._get_bytes,
            namespace=namespace,
            maximum=max_size_bytes,
            workers=self._max_workers,
            cache=cache,
        )

    def _get_bytes(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        response = self._client.get_object(bucket, key)
        try:
            if max_bytes is None:
                return response.read()
            value = response.read(max_bytes + 1)
            if len(value) > max_bytes:
                raise RuntimeError(f"published artifact object exceeds its declared size: {key}")
            return value
        finally:
            response.close()
            response.release_conn()


def _pack_objects(objects: Mapping[str, bytes], target: int = 8 * 1024**2):
    packs: dict[str, bytes] = {}
    entries: list[list[Any]] = []
    buffer = bytearray()
    name = "packs/000000.bin"
    for key, value in sorted(objects.items()):
        if buffer and len(buffer) + len(value) > target:
            packs[name] = bytes(buffer)
            buffer.clear()
            name = f"packs/{len(packs):06d}.bin"
        entries.append([key, name, len(buffer), len(value)])
        buffer.extend(value)
    packs[name] = bytes(buffer)
    return packs, entries


def _unpack_objects(packs: dict[str, bytes], entries: Any) -> dict[str, bytes]:
    if not isinstance(entries, list) or not 0 < len(entries) <= 100_000:
        raise RuntimeError("invalid packed artifact index")
    positions = dict.fromkeys(packs, 0)
    objects: dict[str, bytes] = {}
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 4:
            raise RuntimeError("invalid packed artifact entry")
        key, name, offset, size = entry
        if (not isinstance(key, str) or not isinstance(name, str)
                or name not in packs or key in objects
                or type(offset) is not int or type(size) is not int or size < 0
                or offset != positions[name] or offset + size > len(packs[name])):
            raise RuntimeError("invalid packed artifact bounds or duplicate key")
        objects[key] = packs[name][offset:offset + size]
        positions[name] += size
    if any(positions[name] != len(value) for name, value in packs.items()):
        raise RuntimeError("packed artifact has unreferenced bytes")
    return normalize_artifact_objects(data=None, objects=objects)


def normalize_artifact_objects(
    *,
    data: bytes | None,
    objects: Mapping[str, bytes] | None,
) -> dict[str, bytes]:
    if (data is None) == (objects is None):
        raise ValueError("exactly one of data or objects is required")
    payloads = {"result.json": data} if data is not None else dict(objects or {})
    if not payloads:
        raise ValueError("artifact object bundle must not be empty")
    normalized: dict[str, bytes] = {}
    for key, value in payloads.items():
        path = PurePosixPath(key)
        if (
            not key
            or key in {".", "_SUCCESS.json"}
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != key
        ):
            raise ValueError(f"unsafe artifact object key {key!r}")
        if not isinstance(value, bytes):
            raise TypeError(f"artifact object {key!r} must contain bytes")
        normalized[key] = value
    return dict(sorted(normalized.items()))


def normalize_artifact_prefix(prefix: str) -> str:
    path = PurePosixPath(prefix)
    if not prefix or path.is_absolute() or ".." in path.parts or str(path) != prefix:
        raise RuntimeError("published artifact marker has an invalid data prefix")
    return prefix.rstrip("/")


@_perf_timed("io.artifact_digest")
def artifact_sha256(objects: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for key, value in sorted(objects.items()):
        key_bytes = key.encode()
        digest.update(len(key_bytes).to_bytes(4, "big"))
        digest.update(key_bytes)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(hashlib.sha256(value).digest())
    return digest.hexdigest()


def _content_type(key: str) -> str:
    if key.endswith((".json", ".zattrs", ".zarray", ".zgroup")):
        return "application/json"
    if key.endswith(".png"):
        return "image/png"
    if key.endswith((".tif", ".tiff")):
        return "image/tiff"
    if key.endswith(".nc"):
        return "application/x-netcdf"
    return "application/octet-stream"


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _resolve_max_workers(max_workers: int | None) -> int:
    if max_workers is None:
        max_workers = int(
            os.getenv(
                "RAINPULSE_OBJECT_STORE_MAX_WORKERS",
                str(DEFAULT_OBJECT_STORE_MAX_WORKERS),
            )
        )
    if max_workers <= 0:
        raise ValueError("object store worker count must be positive")
    return max_workers


@_perf_timed("io.parallel_upload")
def _bounded_parallel_map(
    items: list[Any],
    *,
    worker_count: int,
    worker: Callable[[Any], Any],
) -> list[Any]:
    if len(items) <= 1 or worker_count == 1:
        return [worker(item) for item in items]

    results: list[Any] = [None] * len(items)
    next_index = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        pending: dict[Future[Any], int] = {}

        def submit_next() -> bool:
            nonlocal next_index
            if next_index >= len(items):
                return False
            future = executor.submit(worker, items[next_index])
            pending[future] = next_index
            next_index += 1
            return True

        for _ in range(min(worker_count, len(items))):
            submit_next()

        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                results[index] = future.result()
                submit_next()
    return results


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)


def _marker_object_count(marker: dict[str, Any]) -> int:
    objects = marker.get("objects")
    if not isinstance(objects, list):
        return 0
    return len(objects)
