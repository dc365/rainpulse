from dataclasses import dataclass
import pytest
from core_modules import resources


@dataclass(frozen=True)
class Handler:
    profile: str = "radar-qc-basic"
    subject: str = "rainpulse.jobs.requested.radar_qc"
    consumer: str = "rainpulse-radar-qc-rp008-basic-v1"


def test_realtime_preserves_legacy_handler():
    original = Handler()
    assert resources.resource_handler(original, resources.WorkerResourcePolicy()) is original


@pytest.mark.parametrize("stage", sorted(resources.CPU_STAGES))
def test_background_routes_cannot_consume_realtime(stage):
    original = Handler(subject=resources.REQUEST_PREFIX + stage)
    result = resources.resource_handler(original, resources.WorkerResourcePolicy("background", 1, 1))
    assert result.subject == resources.BACKGROUND_PREFIX + stage
    assert result.consumer == original.consumer + "-background"
    assert original.subject == resources.REQUEST_PREFIX + stage


@pytest.mark.parametrize("subject", ["rainpulse.jobs.results.completed", "rainpulse.jobs.requested.nowcastnet_shadow", "rainpulse.jobs.requested.radar_qc_synthetic", "rainpulse.jobs.requested.background.radar_qc"])
def test_unmanaged_or_double_routed_handler_rejected(subject):
    with pytest.raises(ValueError):
        resources.resource_handler(Handler(subject=subject), resources.WorkerResourcePolicy("background", 1, 1))


@pytest.mark.parametrize("values", [dict(lane="typo"), dict(lane="background"), dict(max_ack_pending=0),dict(max_ack_pending=True),dict(native_threads=65),dict(native_threads=0)])
def test_invalid_policy(values):
    with pytest.raises(ValueError):
        resources.WorkerResourcePolicy(**values)


def test_environment_budget_before_native_import(monkeypatch):
    monkeypatch.setenv("RAINPULSE_WORKER_LANE", "background")
    monkeypatch.delenv("RAINPULSE_WORKER_MAX_ACK_PENDING", raising=False)
    monkeypatch.delenv("RAINPULSE_NATIVE_THREADS", raising=False)
    for name in resources.THREAD_VARIABLES: monkeypatch.setenv(name, "99")
    policy = resources.WorkerResourcePolicy.from_environment()
    resources.configure_native_threads(policy)
    import os
    assert all(os.environ[k] == "1" for k in resources.THREAD_VARIABLES)
    assert policy.report()["task_concurrency"] == 1


def test_default_does_not_override_existing_native_threads(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    resources.configure_native_threads(resources.WorkerResourcePolicy())
    import os
    assert os.environ["OMP_NUM_THREADS"] == "3"


def test_existing_durable_pending_limit_is_updated_without_recreation():
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    @dataclass
    class Config:
        filter_subject: str = Handler().subject
        max_ack_pending: int = 1000
        deliver_subject: str | None = None
        def evolve(self, **kwargs): return replace(self, **kwargs)
    class JS:
        def __init__(self):self.config=Config();self.updates=[];self.pending=0
        async def consumer_info(self, stream, consumer):
            return SimpleNamespace(config=self.config,num_pending=self.pending,num_ack_pending=0)
        async def add_consumer(self, stream, config):
            self.updates.append(config);self.config=config
    js=JS();policy=resources.WorkerResourcePolicy(max_ack_pending=4)
    asyncio.run(resources.ensure_pending_limit(js,"s",Handler(),policy))
    assert js.config.max_ack_pending==4 and len(js.updates)==1
    js.pending=1
    asyncio.run(resources.ensure_pending_limit(js,"s",Handler(),policy))
    assert len(js.updates)==1
    with pytest.raises(RuntimeError,match="drain"):
        asyncio.run(resources.ensure_pending_limit(js,"s",Handler(),resources.WorkerResourcePolicy(max_ack_pending=3)))
    js.config=Config(filter_subject="wrong")
    with pytest.raises(RuntimeError,match="expected"):
        asyncio.run(resources.ensure_pending_limit(js,"s",Handler(),policy))
