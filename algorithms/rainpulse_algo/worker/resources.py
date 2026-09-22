"""Small execution budgets; independent from QC/model publication eligibility."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

REQUEST_PREFIX = "rainpulse.jobs.requested."
BACKGROUND_PREFIX = REQUEST_PREFIX + "background."
CPU_STAGES = frozenset({
    "radar_decode", "radar_qc", "radar_grid", "analysis_mosaic", "analysis_qpe",
    "analysis_diagnostics", "nowcast_input", "pysteps_lk", "pysteps_lk.v2",
    "product_build", "forecast_verification",
})
THREAD_VARIABLES = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENCV_FOR_THREADS_NUM",
)


@dataclass(frozen=True)
class WorkerResourcePolicy:
    lane: str = "realtime"
    max_ack_pending: int | None = None
    native_threads: int | None = None

    def __post_init__(self) -> None:
        if self.lane not in ("realtime", "background"):
            raise ValueError("worker resource lane must be realtime or background")
        for key, maximum in (("max_ack_pending", 1024), ("native_threads", 64)):
            value = getattr(self, key)
            if value is not None and (type(value) is not int or not 1 <= value <= maximum):
                raise ValueError(f"invalid worker resource {key}")
        if self.lane == "background" and (
            self.max_ack_pending is None or self.native_threads is None
        ):
            raise ValueError("background workers require explicit pending/thread limits")

    @classmethod
    def from_environment(cls) -> WorkerResourcePolicy:
        lane = os.getenv("RAINPULSE_WORKER_LANE", "realtime")
        pending = os.getenv("RAINPULSE_WORKER_MAX_ACK_PENDING", "1" if lane == "background" else "")
        threads = os.getenv("RAINPULSE_NATIVE_THREADS", "1" if lane == "background" else "")
        return cls(lane, int(pending) if pending else None, int(threads) if threads else None)

    def report(self) -> dict[str, Any]:
        return {"schema_version": 1, "lane": self.lane, "task_concurrency": 1,
                "max_ack_pending": self.max_ack_pending, "native_threads": self.native_threads}


def resource_handler(handler: Any, policy: WorkerResourcePolicy) -> Any:
    """Clone a registered TaskHandler, never mutate the shared HANDLERS registry."""
    if policy.lane == "realtime":
        return handler
    subject = handler.subject
    if not subject.startswith(REQUEST_PREFIX) or subject[len(REQUEST_PREFIX):] not in CPU_STAGES:
        raise ValueError("profile is not an eligible CPU background handler")
    return replace(handler, subject=BACKGROUND_PREFIX + subject[len(REQUEST_PREFIX):],
                   consumer=handler.consumer + "-background")


def configure_native_threads(policy: WorkerResourcePolicy | None = None) -> None:
    """Call before importing handlers/numpy. No hot changes during a task."""
    policy = policy or WorkerResourcePolicy.from_environment()
    if policy.native_threads is not None:
        for name in THREAD_VARIABLES:
            os.environ[name] = str(policy.native_threads)
        os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")


async def ensure_pending_limit(jetstream: Any, stream: str, handler: Any,
                               policy: WorkerResourcePolicy) -> None:
    """An existing durable may ignore pull_subscribe's creation config.

    Preserve the consumer and its ack floor. Change only the editable pending
    limit, only at a drained boundary. Never delete/recreate a durable.
    """
    wanted = policy.max_ack_pending
    if wanted is None:
        return
    info = await jetstream.consumer_info(stream, handler.consumer)
    config = info.config
    if config.filter_subject != handler.subject or getattr(config, "deliver_subject", None):
        raise RuntimeError("existing consumer is not the expected pull resource lane")
    if config.max_ack_pending != wanted:
        if info.num_pending or info.num_ack_pending:
            raise RuntimeError("drain the existing consumer before changing its pending limit")
        await jetstream.add_consumer(stream, config=config.evolve(max_ack_pending=wanted))
        info = await jetstream.consumer_info(stream, handler.consumer)
    if info.config.max_ack_pending != wanted or info.config.filter_subject != handler.subject:
        raise RuntimeError("consumer resource limit was not applied")
