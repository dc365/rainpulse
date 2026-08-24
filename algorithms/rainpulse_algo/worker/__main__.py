from __future__ import annotations

import asyncio
import signal

from .handlers import handler_for_profile
from .object_store import AtomicObjectPublisher, minio_client_from_environment
from .runtime import Worker, WorkerConfig


async def main() -> None:
    config = WorkerConfig.from_environment()
    client = minio_client_from_environment()
    handler = None if config.profile == "simulation" else handler_for_profile(config.profile)
    worker = Worker(config, AtomicObjectPublisher(client), handler=handler)
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, worker.stop)
    await worker.run()
if __name__ == "__main__":
    asyncio.run(main())
