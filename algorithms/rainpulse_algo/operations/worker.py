# ruff: noqa: E501, I001
"""Long-lived management lane. Shares NATS/outbox and existing algorithm code.

Never subscribes to automatic-pipeline subjects or publishes its terminal results
on the legacy completion subject. Only the managed API commits candidate results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import socket
import time
import uuid

from .engine import Engine
from .native import NativeAdapter
from .protocol import ControlClient, ControlError, redact


def may_fetch(ready: bool, accepting: bool, registered_at: float, now: float) -> bool:
    """Intake is separate from health. Missing/stale control receipts fail closed."""
    return (ready is True and accepting is True and math.isfinite(registered_at)
            and math.isfinite(now) and 0 <= now - registered_at < 75)


async def main() -> None:
    import nats
    from nats.errors import TimeoutError as NATSTimeoutError
    from nats.js.api import AckPolicy, ConsumerConfig

    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("qc", "render", "diagnostics"), required=True)
    args = parser.parse_args()
    adapter = NativeAdapter(args.kind)
    control = ControlClient(os.environ["RAINPULSE_OPS_CONTROL_URL"], os.environ["RAINPULSE_OPS_WORKER_TOKEN"])
    worker_id = f"ops-{args.kind}-{socket.gethostname()[:24]}-{uuid.uuid4().hex[:12]}"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    connection = await nats.connect(servers=[os.environ["RAINPULSE_NATS_URL"]], name=worker_id,
                                    max_reconnect_attempts=-1, reconnect_time_wait=1)
    js = connection.jetstream()
    subject = "rainpulse.jobs.requested.ops." + args.kind
    subscription = await js.pull_subscribe(subject, durable="rainpulse-ops-" + args.kind + "-v1",
        stream="RAINPULSE_JOBS", config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT,
            ack_wait=90, max_deliver=-1, max_ack_pending=32))
    current: dict[str, str] = {}
    ready = True
    accepting = False
    registered_at = 0.0

    async def register() -> None:
        nonlocal ready, accepting, registered_at
        try:
            await asyncio.to_thread(adapter.check_identity, adapter.startup)
            ready = not stop.is_set()
        except Exception:
            ready = False
        receipt = await asyncio.to_thread(control.post, "register", {"id": worker_id, "identity": adapter.startup,
            "ready": ready, "busy": bool(current), "current_task": current.get("task_id", "")})
        accepting = receipt.get("accepting") is True
        registered_at = time.monotonic()

    async def registration_loop() -> None:
        while not stop.is_set():
            try:
                await register()
            except Exception as error:
                print(json.dumps({"event": "ops.registration_unavailable", "error": type(error).__name__}))
            try:
                await asyncio.wait_for(stop.wait(), timeout=20)
            except TimeoutError:
                pass

    async def health(reader, writer) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 2)
            healthy = (b" /healthz " in line and ready and connection.is_connected
                       and time.monotonic() - registered_at < 75)
            data = json.dumps({"status": "ready" if healthy else "unavailable", "worker_id": worker_id,
                               "kind": args.kind, "accepting": accepting, "current_task": current.get("task_id")}).encode()
            writer.write((f"HTTP/1.1 {200 if healthy else 503} Result\r\nContent-Type: application/json\r\n"
                          f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n").encode() + data)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    await register()
    registrations = asyncio.create_task(registration_loop())
    server = await asyncio.start_server(health, "0.0.0.0", int(os.getenv("RAINPULSE_OPS_HEALTH_PORT", "8095")))
    try:
        while not stop.is_set():
            if not may_fetch(ready, accepting, registered_at, time.monotonic()):
                await asyncio.sleep(2)
                continue
            try:
                messages = await subscription.fetch(1, timeout=1)
            except NATSTimeoutError:
                continue
            for message in messages:
                try:
                    ref = json.loads(message.data)
                    if (ref.get("event_type") != "ops.task.requested.v1"
                            or str(uuid.UUID(ref["task_id"])) != ref["task_id"]
                            or type(ref.get("generation")) is not int):
                        raise ValueError("invalid managed reference")
                except (ValueError, KeyError, TypeError):
                    await message.term()
                    continue
                try:
                    claim = await asyncio.to_thread(control.post, "claim", {"task_id": ref["task_id"],
                        "generation": ref["generation"], "worker_id": worker_id})
                    if claim["decision"] == "terminal":
                        await message.ack()
                        continue
                    if claim["decision"] != "claimed":
                        # A committed marker can repair lost registration without taking
                        # over a running computation or issuing a new attempt.
                        if claim["decision"] == "busy":
                            try:
                                await asyncio.to_thread(control.post, "reconcile", {"task_id": ref["task_id"]})
                            except Exception:
                                pass
                        await message.nak(delay=10)
                        continue
                    current.update(task_id=claim["task_id"])
                    await register()
                    async def ack_progress():
                        while True:
                            await asyncio.sleep(15)
                            await message.in_progress()
                    ack_task = asyncio.create_task(ack_progress())
                    try:
                        accepted = await asyncio.to_thread(Engine(control, adapter).run, claim)
                        if accepted:
                            await message.ack()
                        else:
                            await message.nak(delay=10)
                    finally:
                        ack_task.cancel()
                        try:
                            await ack_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        current.clear()
                        await register()
                except Exception as error:
                    print(json.dumps({"event": "ops.delivery_deferred", "job_id": ref["task_id"],
                                      "error": redact(str(error))}))
                    await message.nak(delay=15 if isinstance(error, ControlError) else 5)
    finally:
        stop.set()
        registrations.cancel()
        await asyncio.gather(registrations, return_exceptions=True)
        ready = False
        try:
            await register()
        except Exception:
            pass
        server.close()
        await server.wait_closed()
        await connection.drain()


if __name__ == "__main__":
    asyncio.run(main())
