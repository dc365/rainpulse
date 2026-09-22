"""Optional real NATS test: creates/deletes only a unique isolated test stream."""
import asyncio
from dataclasses import dataclass, replace
import os
import uuid
import pytest
from core_modules import resources

if os.getenv("RAINPULSE_PACKAGE_CORE_ONLY") == "1":
    pytest.skip("package-only validation does not connect to a NATS server", allow_module_level=True)

nats = pytest.importorskip("nats", reason="actual NATS SDK required for queue integration")
from nats.js.api import AckPolicy, ConsumerConfig, StreamConfig


@dataclass(frozen=True)
class Handler:
    subject: str = "rainpulse.jobs.requested.radar_qc"
    consumer: str = "qc-test"


def test_realtime_background_filters_and_durable_budget():
    url=os.getenv("RAINPULSE_TEST_NATS_URL")
    if not url:pytest.skip("isolated RAINPULSE_TEST_NATS_URL required; no broker test executed")
    async def run():
        nc=await nats.connect(servers=[url],connect_timeout=5,max_reconnect_attempts=0)
        js=nc.jetstream();identity=uuid.uuid4().hex
        stream="RP_BATCH2_TEST_"+identity
        prefix="test.batch2."+identity+"."
        created=False
        try:
            await js.add_stream(StreamConfig(name=stream,subjects=[prefix+">"],max_bytes=1024*1024))
            created=True
            original=Handler()
            bg=resources.resource_handler(original,resources.WorkerResourcePolicy("background",1,1))
            real=replace(original,subject=prefix+original.subject)
            bg=replace(bg,subject=prefix+bg.subject)
            sub_real=await js.pull_subscribe(real.subject,durable=real.consumer,stream=stream,
                config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT,max_ack_pending=8))
            sub_bg=await js.pull_subscribe(bg.subject,durable=bg.consumer,stream=stream,
                config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT,max_ack_pending=1))
            await resources.ensure_pending_limit(js,stream,real,resources.WorkerResourcePolicy(max_ack_pending=2))
            await js.publish(bg.subject,b"historical")
            await js.publish(real.subject,b"realtime")
            r=await sub_real.fetch(1,timeout=2);b=await sub_bg.fetch(1,timeout=2)
            assert r[0].data==b"realtime" and b[0].data==b"historical"
            await r[0].ack_sync();await b[0].ack_sync()
            info=await js.consumer_info(stream,real.consumer)
            assert info.config.max_ack_pending==2 and info.ack_floor.consumer_seq>=1
        finally:
            if created:await js.delete_stream(stream)
            await nc.close()
    asyncio.run(run())
