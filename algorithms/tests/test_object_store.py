import io
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from rainpulse_algo.worker.contracts import JobCompleted
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    AtomicObjectPublisher,
    normalize_artifact_objects,
)
from rainpulse_algo.worker.runtime import Worker, WorkerConfig

from .test_worker_contracts import requested_data


class Response(io.BytesIO):
    def release_conn(self) -> None:
        pass


class FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.removed: list[tuple[str, str]] = []

    def put_object(
        self,
        bucket: str,
        key: str,
        body: io.BytesIO,
        length: int,
        **_: object,
    ) -> None:
        value = body.read(length)
        assert len(value) == length
        self.objects[(bucket, key)] = value

    def stat_object(self, bucket: str, key: str) -> SimpleNamespace:
        return SimpleNamespace(size=len(self.objects[(bucket, key)]))

    def copy_object(self, bucket: str, key: str, source: object) -> None:
        self.objects[(bucket, key)] = self.objects[(source.bucket_name, source.object_name)]

    def remove_object(self, bucket: str, key: str) -> None:
        self.removed.append((bucket, key))
        self.objects.pop((bucket, key), None)

    def get_object(self, bucket: str, key: str) -> Response:
        return Response(self.objects[(bucket, key)])


def test_atomic_publish_writes_marker_last_and_reloads_completion() -> None:
    from rainpulse_algo.worker.contracts import JobRequested

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    data = b'{"simulation":true}'
    completion = worker._build_completion(  # noqa: SLF001 - verifies SDK publication contract
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=worker._coerce_result((data, {"simulation": 1.0})),  # noqa: SLF001
    )
    client = FakeMinio()
    publisher = AtomicObjectPublisher(client)  # type: ignore[arg-type]

    published = publisher.publish(
        output_prefix=request.payload.output_prefix,
        job_id=UUID(str(request.job_id)),
        data=data,
        completion=completion,
    )

    assert published.marker_key.endswith("forecast.zarr/_SUCCESS.json")
    assert client.objects[("rainpulse", published.marker_key)]
    assert all(not key.startswith("_temporary/") for _, key in client.objects)
    reloaded = publisher.load_completion(request.payload.output_prefix)
    assert isinstance(reloaded, JobCompleted)
    assert reloaded.event_id == completion.event_id


def test_atomic_publish_supports_stage_specific_artifact_names() -> None:
    from rainpulse_algo.worker.contracts import JobRequested

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    data = b'{"synthetic_contract_fixture":true}'
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=worker._coerce_result((data, {"simulation": 1.0})),  # noqa: SLF001
    )
    client = FakeMinio()
    publisher = AtomicObjectPublisher(client)  # type: ignore[arg-type]

    published = publisher.publish(
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=data,
        completion=completion,
        artifact_name="volume.zarr",
    )

    assert published.asset_uri.endswith("/volume.zarr")
    assert published.marker_key.endswith("volume.zarr/_SUCCESS.json")
    assert publisher.load_completion(
        request.payload.output_prefix, "volume.zarr"
    ).event_id == completion.event_id


def test_atomic_publish_commits_multi_object_zarr_bundle_before_marker() -> None:
    from rainpulse_algo.worker.contracts import JobRequested
    from rainpulse_algo.worker.runtime import WorkerResult

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    objects = {
        ".zgroup": b'{"zarr_format":2}',
        ".zattrs": b'{"contract_name":"rainpulse.normalized-radar-volume"}',
        "sweep_000/DBZH/0.0": b"compressed-radar-bytes",
    }
    result = WorkerResult(objects=objects, metrics={"sweep_count": 1.0})
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=result,
    )
    client = FakeMinio()
    publisher = AtomicObjectPublisher(client)  # type: ignore[arg-type]

    published = publisher.publish(
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=None,
        objects=objects,
        completion=completion,
        artifact_name="volume.zarr",
    )

    marker = json.loads(client.objects[("rainpulse", published.marker_key)])
    assert [item["key"] for item in marker["objects"]] == sorted(objects)
    assert published.size_bytes == sum(map(len, objects.values()))
    assert completion.payload.assets[0].sha256 == published.sha256
    assert all(not key.startswith("_temporary/") for _, key in client.objects)

    loaded = ArtifactObjectReader(client).load(published.asset_uri)  # type: ignore[arg-type]
    assert loaded == objects


def test_artifact_reader_rejects_corrupt_published_object() -> None:
    from rainpulse_algo.worker.contracts import JobRequested
    from rainpulse_algo.worker.runtime import WorkerResult

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    objects = {".zgroup": b'{"zarr_format":2}', ".zattrs": b"{}"}
    result = WorkerResult(objects=objects)
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=result,
    )
    client = FakeMinio()
    published = AtomicObjectPublisher(client).publish(  # type: ignore[arg-type]
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=None,
        objects=objects,
        completion=completion,
    )
    bucket, key = next(
        item for item in client.objects if item[1].endswith("forecast.zarr/.zattrs")
    )
    client.objects[(bucket, key)] = b"xx"

    with pytest.raises(RuntimeError, match="checksum"):
        ArtifactObjectReader(client).load(published.asset_uri)  # type: ignore[arg-type]


@pytest.mark.parametrize("key", [".", "../escape", "/absolute", "_SUCCESS.json"])
def test_multi_object_artifact_rejects_unsafe_or_reserved_keys(key: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        normalize_artifact_objects(data=None, objects={key: b"value"})
