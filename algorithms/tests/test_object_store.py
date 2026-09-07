import hashlib
import io
import json
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest
import zarr
from minio.error import S3Error
from zarr.storage import MemoryStore

from rainpulse_algo.worker.contracts import JobCompleted
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    AtomicObjectPublisher,
    normalize_artifact_objects,
    parse_s3_uri,
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
        self.writes: list[tuple[str, str]] = []
        self.put_hook: Callable[[str, str, bytes], None] | None = None
        self.raw_put_hook: Callable[[str, str, bytes], None] | None = None
        self.get_hook: Callable[[str, str], None] | None = None
        self.stat_hook: Callable[[str, str, int], int] | None = None

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
        if self.put_hook is not None:
            self.put_hook(bucket, key, value)
        self.objects[(bucket, key)] = value
        self.writes.append((bucket, key))

    def _put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        if headers and headers.get("If-None-Match") == "*" and (bucket, key) in self.objects:
            raise S3Error(
                SimpleNamespace(status=412),
                "PreconditionFailed",
                "marker exists",
                key,
                "request-id",
                "host-id",
                bucket,
                key,
            )
        if self.raw_put_hook is not None:
            self.raw_put_hook(bucket, key, body)
        self.objects[(bucket, key)] = body
        self.writes.append((bucket, key))

    def stat_object(self, bucket: str, key: str) -> SimpleNamespace:
        size = len(self.objects[(bucket, key)])
        if self.stat_hook is not None:
            size = self.stat_hook(bucket, key, size)
        return SimpleNamespace(size=size)

    def copy_object(self, bucket: str, key: str, source: object) -> None:
        self.objects[(bucket, key)] = self.objects[(source.bucket_name, source.object_name)]

    def remove_object(self, bucket: str, key: str) -> None:
        self.removed.append((bucket, key))
        self.objects.pop((bucket, key), None)

    def get_object(self, bucket: str, key: str) -> Response:
        if self.get_hook is not None:
            self.get_hook(bucket, key)
        return Response(self.objects[(bucket, key)])


class ConcurrencyTracker:
    def __init__(
        self,
        *,
        expected_parallelism: int,
        pause_seconds: float = 0.02,
        wait_timeout_seconds: float = 0.2,
    ) -> None:
        self._expected_parallelism = expected_parallelism
        self._pause_seconds = pause_seconds
        self._wait_timeout_seconds = wait_timeout_seconds
        self._lock = threading.Lock()
        self._parallel_ready = threading.Event()
        self.active = 0
        self.max_active = 0

    def hold(self) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= self._expected_parallelism:
                self._parallel_ready.set()
        self._parallel_ready.wait(timeout=self._wait_timeout_seconds)
        time.sleep(self._pause_seconds)
        with self._lock:
            self.active -= 1


@pytest.mark.parametrize(
    "uri",
    [
        "s3://rainpulse",
        "s3://user@rainpulse/path",
        "s3://rainpulse/../path",
        "s3://rainpulse/path?x=1",
    ],
)
def test_parse_s3_uri_rejects_unsafe_or_root_only_locations(uri: str) -> None:
    with pytest.raises(ValueError):
        parse_s3_uri(uri)


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
    assert client.writes[-1] == ("rainpulse", published.marker_key)
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
    volume_asset = completion.payload.assets[0].model_copy(
        update={"uri": completion.payload.assets[0].uri.replace("forecast.zarr", "volume.zarr")}
    )
    completion = completion.model_copy(
        update={"payload": completion.payload.model_copy(update={"assets": [volume_asset]})}
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
    assert (
        publisher.load_completion(request.payload.output_prefix, "volume.zarr").event_id
        == completion.event_id
    )


def test_atomic_publish_rejects_completion_for_different_bundle_bytes() -> None:
    from rainpulse_algo.worker.contracts import JobRequested

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=worker._coerce_result((b"expected", {})),  # noqa: SLF001
    )

    with pytest.raises(ValueError, match="completion asset identity"):
        AtomicObjectPublisher(FakeMinio()).publish(  # type: ignore[arg-type]
            output_prefix=request.payload.output_prefix,
            job_id=request.job_id,
            data=b"different",
            completion=completion,
        )


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
    volume_asset = completion.payload.assets[0].model_copy(
        update={"uri": completion.payload.assets[0].uri.replace("forecast.zarr", "volume.zarr")}
    )
    completion = completion.model_copy(
        update={"payload": completion.payload.model_copy(update={"assets": [volume_asset]})}
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
    assert marker["schema_version"] == "2.0"
    assert marker["data_prefix"] == f"_objects/{published.sha256}"
    assert [item["key"] for item in marker["objects"]] == sorted(objects)
    assert published.size_bytes == sum(map(len, objects.values()))
    assert completion.payload.assets[0].sha256 == published.sha256
    assert all(not key.startswith("_temporary/") for _, key in client.objects)

    loaded = ArtifactObjectReader(client).load(published.asset_uri)  # type: ignore[arg-type]
    assert loaded == objects


def test_atomic_publish_uses_bounded_parallel_object_writes() -> None:
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
        **{f"sweep_000/DBZH/{index}.0": f"chunk-{index}".encode() for index in range(6)},
    }
    result = WorkerResult(objects=objects, metrics={"sweep_count": 1.0})
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=result,
    )
    volume_asset = completion.payload.assets[0].model_copy(
        update={"uri": completion.payload.assets[0].uri.replace("forecast.zarr", "volume.zarr")}
    )
    completion = completion.model_copy(
        update={"payload": completion.payload.model_copy(update={"assets": [volume_asset]})}
    )
    client = FakeMinio()
    tracker = ConcurrencyTracker(expected_parallelism=2)
    client.put_hook = lambda _bucket, _key, _value: tracker.hold()

    published = AtomicObjectPublisher(client, max_workers=2).publish(  # type: ignore[arg-type]
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=None,
        objects=objects,
        completion=completion,
        artifact_name="volume.zarr",
    )

    marker = json.loads(client.objects[("rainpulse", published.marker_key)])

    assert tracker.max_active == 2
    assert [item["key"] for item in marker["objects"]] == sorted(objects)
    assert client.writes[-1] == ("rainpulse", published.marker_key)


def test_atomic_publish_does_not_write_marker_when_parallel_put_fails() -> None:
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
        "sweep_000/DBZH/0.0": b"chunk-0",
        "sweep_000/DBZH/1.0": b"chunk-1",
        "sweep_000/DBZH/broken.bin": b"broken",
    }
    result = WorkerResult(objects=objects)
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=result,
    )
    volume_asset = completion.payload.assets[0].model_copy(
        update={"uri": completion.payload.assets[0].uri.replace("forecast.zarr", "volume.zarr")}
    )
    completion = completion.model_copy(
        update={"payload": completion.payload.model_copy(update={"assets": [volume_asset]})}
    )
    client = FakeMinio()

    def fail_one_put(_bucket: str, key: str, _value: bytes) -> None:
        if key.endswith("/broken.bin"):
            raise RuntimeError("simulated put failure")

    client.put_hook = fail_one_put

    with pytest.raises(RuntimeError, match="simulated put failure"):
        AtomicObjectPublisher(client, max_workers=2).publish(  # type: ignore[arg-type]
            output_prefix=request.payload.output_prefix,
            job_id=request.job_id,
            data=None,
            objects=objects,
            completion=completion,
            artifact_name="volume.zarr",
        )

    assert not any(key.endswith("/_SUCCESS.json") for _, key in client.objects)


def test_concurrent_publish_reuses_first_committed_marker() -> None:
    from rainpulse_algo.worker.contracts import JobRequested

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    first_data = b"first-result"
    first_completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=worker._coerce_result((first_data, {})),  # noqa: SLF001
    )
    second_data = b"different-result"
    second_completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=worker._coerce_result((second_data, {})),  # noqa: SLF001
    )
    client = FakeMinio()
    publisher = AtomicObjectPublisher(client)  # type: ignore[arg-type]

    first = publisher.publish(
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=first_data,
        completion=first_completion,
    )
    second = publisher.publish(
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=second_data,
        completion=second_completion,
    )

    assert not first.reused
    assert second.reused
    assert second.sha256 == first.sha256
    assert second.completion.payload.assets[0].sha256 == first.sha256
    assert ArtifactObjectReader(client).load(second.asset_uri) == {  # type: ignore[arg-type]
        "result.json": first_data
    }


def test_artifact_reader_uses_bounded_parallel_gets() -> None:
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
        **{f"sweep_000/DBZH/{index}.0": f"chunk-{index}".encode() for index in range(5)},
    }
    result = WorkerResult(objects=objects)
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=result,
    )
    volume_asset = completion.payload.assets[0].model_copy(
        update={"uri": completion.payload.assets[0].uri.replace("forecast.zarr", "volume.zarr")}
    )
    completion = completion.model_copy(
        update={"payload": completion.payload.model_copy(update={"assets": [volume_asset]})}
    )
    client = FakeMinio()
    published = AtomicObjectPublisher(client, max_workers=2).publish(  # type: ignore[arg-type]
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=None,
        objects=objects,
        completion=completion,
        artifact_name="volume.zarr",
    )
    tracker = ConcurrencyTracker(expected_parallelism=2)

    def track_object_reads(_bucket: str, key: str) -> None:
        if key.endswith("/_SUCCESS.json"):
            return
        tracker.hold()

    client.get_hook = track_object_reads

    loaded = ArtifactObjectReader(client, max_workers=2).load(published.asset_uri)  # type: ignore[arg-type]

    assert loaded == objects
    assert tracker.max_active == 2


def test_artifact_reader_accepts_sparse_empty_chunks_but_rejects_manifest_dangling_reference() -> (
    None
):
    from rainpulse_algo.worker.contracts import JobRequested
    from rainpulse_algo.worker.runtime import WorkerResult

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.create_dataset(
        "DBZH_QC",
        data=np.full((4, 4), np.nan, dtype="float32"),
        chunks=(2, 2),
        fill_value=np.nan,
        overwrite=True,
        write_empty_chunks=False,
    )
    objects = {str(key): bytes(value) for key, value in store.items()}
    result = WorkerResult(objects=objects)
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=result,
    )
    volume_asset = completion.payload.assets[0].model_copy(
        update={"uri": completion.payload.assets[0].uri.replace("forecast.zarr", "volume.zarr")}
    )
    completion = completion.model_copy(
        update={"payload": completion.payload.model_copy(update={"assets": [volume_asset]})}
    )
    client = FakeMinio()
    published = AtomicObjectPublisher(client).publish(  # type: ignore[arg-type]
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=None,
        objects=objects,
        completion=completion,
        artifact_name="volume.zarr",
    )

    loaded = ArtifactObjectReader(client).load(published.asset_uri)  # type: ignore[arg-type]
    sparse_store = MemoryStore()
    sparse_store.update(loaded)
    assert np.isnan(zarr.open_group(store=sparse_store, mode="r")["DBZH_QC"][:]).all()

    marker_key = ("rainpulse", published.marker_key)
    marker = json.loads(client.objects[marker_key])
    marker["objects"].append(
        {
            "key": "DBZH_QC/0.0",
            "sha256": hashlib.sha256(b"").hexdigest(),
            "size_bytes": 0,
        }
    )
    client.objects[marker_key] = json.dumps(marker).encode()

    with pytest.raises(KeyError):
        ArtifactObjectReader(client).load(published.asset_uri)  # type: ignore[arg-type]


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
    bucket, key = next(item for item in client.objects if item[1].endswith("/.zattrs"))
    client.objects[(bucket, key)] = b"xx"

    with pytest.raises(RuntimeError, match="checksum"):
        ArtifactObjectReader(client).load(published.asset_uri)  # type: ignore[arg-type]


def test_artifact_reader_rejects_bundle_above_memory_safety_limit() -> None:
    from rainpulse_algo.worker.contracts import JobRequested

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    data = b"0123456789"
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=worker._coerce_result((data, {})),  # noqa: SLF001
    )
    client = FakeMinio()
    published = AtomicObjectPublisher(client).publish(  # type: ignore[arg-type]
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=data,
        completion=completion,
    )

    with pytest.raises(RuntimeError, match="byte limit"):
        ArtifactObjectReader(client, max_size_bytes=5).load(  # type: ignore[arg-type]
            published.asset_uri
        )


def test_artifact_reader_rejects_duplicate_manifest_keys() -> None:
    from rainpulse_algo.worker.contracts import JobRequested

    request = JobRequested.model_validate(requested_data())
    worker = Worker(
        WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
        publisher=None,  # type: ignore[arg-type]
    )
    data = b"result"
    completion = worker._build_completion(  # noqa: SLF001
        request=request,
        started_at=request.occurred_at,
        started_tick=0.0,
        result=worker._coerce_result((data, {})),  # noqa: SLF001
    )
    client = FakeMinio()
    published = AtomicObjectPublisher(client).publish(  # type: ignore[arg-type]
        output_prefix=request.payload.output_prefix,
        job_id=request.job_id,
        data=data,
        completion=completion,
    )
    marker_key = ("rainpulse", published.marker_key)
    marker = json.loads(client.objects[marker_key])
    marker["objects"].append(dict(marker["objects"][0]))
    marker["size_bytes"] *= 2
    client.objects[marker_key] = json.dumps(marker).encode()

    with pytest.raises(RuntimeError, match="repeats object"):
        ArtifactObjectReader(client).load(published.asset_uri)  # type: ignore[arg-type]


@pytest.mark.parametrize("key", [".", "../escape", "/absolute", "_SUCCESS.json"])
def test_multi_object_artifact_rejects_unsafe_or_reserved_keys(key: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        normalize_artifact_objects(data=None, objects={key: b"value"})
