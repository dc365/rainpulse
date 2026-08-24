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
from uuid import UUID, uuid4

from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error

from .contracts import JobCompleted


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


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"expected s3 URI, got {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


class AtomicObjectPublisher:
    """Publishes an object and commits the prefix by writing a marker last."""

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
            response = self._client.get_object(bucket, marker_key)
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject"}:
                return None
            raise
        try:
            marker = json.loads(response.read())
        finally:
            response.close()
            response.release_conn()
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
        temporary_root = f"_temporary/{job_id}/{uuid4()}"
        temporary_keys: list[str] = []
        marker_key = self._marker_key(prefix, artifact_name)
        digest = artifact_sha256(payloads)
        total_size = sum(len(value) for value in payloads.values())
        manifest: list[dict[str, Any]] = []

        try:
            for relative_key, value in payloads.items():
                temporary_key = f"{temporary_root}/{relative_key}"
                data_key = f"{prefix}/{artifact_name}/{relative_key}"
                temporary_keys.append(temporary_key)
                self._put_bytes(bucket, temporary_key, value, _content_type(relative_key))
                temporary = self._client.stat_object(bucket, temporary_key)
                if temporary.size != len(value):
                    raise RuntimeError("temporary object size validation failed")

                self._client.copy_object(bucket, data_key, CopySource(bucket, temporary_key))
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
                    "schema_version": "1.0",
                    "sha256": digest,
                    "size_bytes": total_size,
                    "objects": manifest,
                    "completion_event": completion.model_dump(mode="json"),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            self._put_bytes(bucket, marker_key, marker, "application/json")
        finally:
            for temporary_key in temporary_keys:
                try:
                    self._client.remove_object(bucket, temporary_key)
                except S3Error:
                    pass

        return PublishedObject(
            asset_uri=f"s3://{bucket}/{prefix}/{artifact_name}",
            sha256=digest,
            size_bytes=total_size,
            marker_key=marker_key,
        )

    def _put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> Any:
        return self._client.put_object(
            bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
        )

    @staticmethod
    def _marker_key(prefix: str, artifact_name: str) -> str:
        artifact_name = artifact_name.strip("/")
        if not artifact_name or artifact_name.startswith("_temporary") or ".." in artifact_name:
            raise ValueError("artifact_name must be a safe relative object name")
        return f"{prefix.rstrip('/')}/{artifact_name}/_SUCCESS.json"


class ArtifactObjectReader:
    """Loads and verifies an atomically published multi-object artifact."""

    def __init__(self, client: Minio) -> None:
        self._client = client

    def load(self, artifact_uri: str) -> dict[str, bytes]:
        bucket, prefix = parse_s3_uri(artifact_uri)
        prefix = prefix.rstrip("/")
        marker = json.loads(self._get_bytes(bucket, f"{prefix}/_SUCCESS.json"))
        manifest = marker.get("objects")
        if not isinstance(manifest, list) or not manifest:
            raise RuntimeError("published artifact marker has no object manifest")
        objects: dict[str, bytes] = {}
        for item in manifest:
            relative_key = item.get("key")
            if not isinstance(relative_key, str):
                raise RuntimeError("published artifact manifest has an invalid key")
            normalized = normalize_artifact_objects(
                data=None,
                objects={relative_key: b""},
            )
            key = next(iter(normalized))
            value = self._get_bytes(bucket, f"{prefix}/{key}")
            if len(value) != item.get("size_bytes"):
                raise RuntimeError(f"published artifact size differs for {key}")
            if hashlib.sha256(value).hexdigest() != item.get("sha256"):
                raise RuntimeError(f"published artifact checksum differs for {key}")
            objects[key] = value
        if artifact_sha256(objects) != marker.get("sha256"):
            raise RuntimeError("published artifact bundle checksum differs")
        return objects

    def _get_bytes(self, bucket: str, key: str) -> bytes:
        response = self._client.get_object(bucket, key)
        try:
            return response.read()
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
    return "application/octet-stream"


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value
