from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error

from .contracts import JobCompleted


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

    def load_completion(self, output_prefix: str) -> JobCompleted | None:
        bucket, prefix = parse_s3_uri(output_prefix)
        marker_key = self._marker_key(prefix)
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
        data: bytes,
        completion: JobCompleted,
    ) -> PublishedObject:
        bucket, prefix = parse_s3_uri(output_prefix)
        prefix = prefix.rstrip("/")
        temporary_key = f"_temporary/{job_id}/{uuid4()}/result.json"
        data_key = f"{prefix}/forecast.zarr/result.json"
        marker_key = self._marker_key(prefix)
        digest = hashlib.sha256(data).hexdigest()

        try:
            self._put_bytes(bucket, temporary_key, data, "application/json")
            temporary = self._client.stat_object(bucket, temporary_key)
            if temporary.size != len(data):
                raise RuntimeError("temporary object size validation failed")

            self._client.copy_object(bucket, data_key, CopySource(bucket, temporary_key))
            published = self._client.stat_object(bucket, data_key)
            if published.size != len(data):
                raise RuntimeError("published object size validation failed")

            marker = json.dumps(
                {
                    "schema_version": "1.0",
                    "data_key": data_key,
                    "sha256": digest,
                    "size_bytes": len(data),
                    "completion_event": completion.model_dump(mode="json"),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            self._put_bytes(bucket, marker_key, marker, "application/json")
        finally:
            try:
                self._client.remove_object(bucket, temporary_key)
            except S3Error:
                pass

        return PublishedObject(
            asset_uri=f"s3://{bucket}/{prefix}/forecast.zarr",
            sha256=digest,
            size_bytes=len(data),
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
    def _marker_key(prefix: str) -> str:
        return f"{prefix.rstrip('/')}/forecast.zarr/_SUCCESS.json"
