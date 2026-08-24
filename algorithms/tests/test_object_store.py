import io
from types import SimpleNamespace
from uuid import UUID

from rainpulse_algo.worker.contracts import JobCompleted
from rainpulse_algo.worker.object_store import AtomicObjectPublisher
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
        data=data,
        metrics={"simulation": 1.0},
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
