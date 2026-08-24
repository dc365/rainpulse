from __future__ import annotations

import asyncio
import os
import signal
from urllib.parse import urlparse

from minio import Minio

from .handlers import handler_for_profile
from .object_store import AtomicObjectPublisher
from .runtime import Worker, WorkerConfig


async def main() -> None:
    config = WorkerConfig.from_environment()
    endpoint = urlparse(required_environment("RAINPULSE_OBJECT_STORE_ENDPOINT"))
    if not endpoint.hostname:
        raise ValueError("RAINPULSE_OBJECT_STORE_ENDPOINT must include a hostname")
    client = Minio(
        endpoint.netloc,
        access_key=required_environment("RAINPULSE_OBJECT_STORE_ACCESS_KEY"),
        secret_key=required_environment("RAINPULSE_OBJECT_STORE_SECRET_KEY"),
        secure=endpoint.scheme == "https",
    )
    handler = None if config.profile == "simulation" else handler_for_profile(config.profile)
    worker = Worker(config, AtomicObjectPublisher(client), handler=handler)
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, worker.stop)
    await worker.run()


def required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


if __name__ == "__main__":
    asyncio.run(main())
