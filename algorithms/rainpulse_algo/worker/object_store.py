from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from minio import Minio
from minio.error import S3Error

from .contracts import JobCompleted

MAX_ARTIFACT_MARKER_BYTES = 16 * 1024 * 1024


def minio_client_from_environment() -> Minio:
    endpoint = urlparse(_required_environment("RAINPULSE_OBJECT_STORE_ENDPOINT"))
    if not endpoint.hostname:
        raise ValueError("RAINPULSE_OBJECT_STORE_ENDPOINT must include a hostname")
    return Minio(
        endpoint.netloc,
        access_key=_required_environment("RAINPULSE_OBJECT_STORE_ACCESS_KEY"),
        secret_key=_required_environment("RAINPULSE_OBJECT_STORE_SECRET_KEY"),
        secure=endpoint.scheme == "https",
    )


@dataclass(frozen=True)
class PublishedObject:
    asset_uri: str
    sha256: str
    size_bytes: int
    marker_key: str
    completion: JobCompleted
    reused: bool = False


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

    def __init__(self, client: Minio) -> None:
        self._client = client

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
            "schema_version": "2.0",
            "data_prefix": data_prefix,
        }
        committed_completion = completion.model_copy(
            update={
                "payload": completion.payload.model_copy(
                    update={"diagnostics": diagnostics}
                )
            }
        )
        manifest: list[dict[str, Any]] = []

        for relative_key, value in payloads.items():
            data_key = f"{prefix}/{artifact_name}/{data_prefix}/{relative_key}"
            self._put_bytes(bucket, data_key, value, _content_type(relative_key))
            published = self._client.stat_object(bucket, data_key)
            if published.size != len(value):
                raise RuntimeError("published object size validation failed")
            manifest.append(
                {
                    "key": relative_key,
                    "sha256": hashlib.sha256(value).hexdigest(),
                    "size_bytes": len(value),
                }
            )

        marker = json.dumps(
            {
                "schema_version": "2.0",
                "sha256": digest,
                "size_bytes": total_size,
                "data_prefix": data_prefix,
                "objects": manifest,
                "completion_event": committed_completion.model_dump(mode="json"),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        try:
            self._put_marker_if_absent(bucket, marker_key, marker)
        except S3Error as error:
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
            )

        return PublishedObject(
            asset_uri=f"s3://{bucket}/{prefix}/{artifact_name}",
            sha256=digest,
            size_bytes=total_size,
            marker_key=marker_key,
            completion=committed_completion,
        )

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


class ArtifactObjectReader:
    """Loads and verifies an atomically published multi-object artifact."""

    def __init__(self, client: Minio, max_size_bytes: int | None = None) -> None:
        self._client = client
        if max_size_bytes is None:
            max_size_bytes = int(
                os.getenv("RAINPULSE_MAX_INPUT_ARTIFACT_BYTES", str(2 * 1024**3))
            )
        if max_size_bytes <= 0:
            raise ValueError("artifact input byte limit must be positive")
        self._max_size_bytes = max_size_bytes

    def load(self, artifact_uri: str) -> dict[str, bytes]:
        bucket, prefix = parse_s3_uri(artifact_uri)
        prefix = prefix.rstrip("/")
        marker = json.loads(
            self._get_bytes(
                bucket,
                f"{prefix}/_SUCCESS.json",
                max_bytes=MAX_ARTIFACT_MARKER_BYTES,
            )
        )
        if not isinstance(marker, dict):
            raise RuntimeError("published artifact marker must be an object")
        data_prefix = marker.get("data_prefix", "")
        if data_prefix:
            normalized_prefix = normalize_artifact_prefix(data_prefix)
        else:
            normalized_prefix = ""
        manifest = marker.get("objects")
        if not isinstance(manifest, list) or not manifest:
            raise RuntimeError("published artifact marker has no object manifest")
        if len(manifest) > 100_000:
            raise RuntimeError("published artifact exceeds the object-count safety limit")
        declared_size = marker.get("size_bytes")
        object_sizes: list[int] = []
        for item in manifest:
            if not isinstance(item, dict):
                raise RuntimeError("published artifact marker has an invalid object entry")
            object_size = item.get("size_bytes")
            if type(object_size) is not int or object_size < 0:
                raise RuntimeError("published artifact marker has an invalid object size")
            object_sizes.append(object_size)
        manifest_size = sum(object_sizes)
        if (
            type(declared_size) is not int
            or declared_size < 0
            or manifest_size != declared_size
        ):
            raise RuntimeError("published artifact marker has inconsistent size metadata")
        if declared_size > self._max_size_bytes:
            raise RuntimeError("published artifact exceeds the configured input byte limit")
        objects: dict[str, bytes] = {}
        seen_keys: set[str] = set()
        for item in manifest:
            relative_key = item.get("key")
            if not isinstance(relative_key, str):
                raise RuntimeError("published artifact manifest has an invalid key")
            normalized = normalize_artifact_objects(
                data=None,
                objects={relative_key: b""},
            )
            key = next(iter(normalized))
            if key in seen_keys:
                raise RuntimeError(f"published artifact manifest repeats object {key}")
            seen_keys.add(key)
            object_prefix = f"{prefix}/{normalized_prefix}" if normalized_prefix else prefix
            value = self._get_bytes(
                bucket,
                f"{object_prefix}/{key}",
                max_bytes=item["size_bytes"],
            )
            if len(value) != item.get("size_bytes"):
                raise RuntimeError(f"published artifact size differs for {key}")
            if hashlib.sha256(value).hexdigest() != item.get("sha256"):
                raise RuntimeError(f"published artifact checksum differs for {key}")
            objects[key] = value
        if artifact_sha256(objects) != marker.get("sha256"):
            raise RuntimeError("published artifact bundle checksum differs")
        return objects

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
