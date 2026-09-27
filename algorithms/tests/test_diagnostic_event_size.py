import asyncio
import json

from rainpulse_algo.worker.runtime import Worker, WorkerConfig, WorkerResult
from .test_worker_runtime import FakeJetStream, FakePublisher, make_handler, make_message


def test_large_probe_index_stays_in_marker_while_new_and_replayed_events_are_small():
    async def scenario():
        # Four-station manifests can exceed NATS's default 1 MiB payload.
        probe = {'tiles': {str(i): {'path': f'query/radar/site/{i}.json',
                                   'sha256': 'a' * 64} for i in range(14000)}}
        manifest = {'analysis_id': 'frozen', 'layers': [
            {'layer_id': 'radar-s-dbzh-qc', 'object_path': 's.png', 'probe': probe}
        ]}
        def execute(_):
            return WorkerResult(data=b'unchanged-asset',
                                diagnostics={'analysis_diagnostics': manifest})
        publisher = FakePublisher()
        worker = Worker(WorkerConfig("nats://test", "127.0.0.1", 8091, "test"), publisher, handler=make_handler(execute))
        stream = FakeJetStream()
        await worker.process_message(make_message(), stream)
        await worker.process_message(make_message(), stream)
        assert publisher.existing is not None
        stored = publisher.existing.model_dump(mode='json')
        assert len(json.dumps(stored)) > 1024 * 1024
        completed = [json.loads(data) for topic, data, _ in stream.events
                     if topic.endswith('completed')]
        assert len(completed) == 2
        for event in completed:
            assert len(json.dumps(event)) < 64 * 1024
            assert event['event_id'] == stored['event_id']
            assert event['payload']['assets'] == stored['payload']['assets']
            assert 'probe' not in event['payload']['diagnostics']['analysis_diagnostics']['layers'][0]
        assert publisher.existing.payload.diagnostics['analysis_diagnostics']['layers'][0]['probe'] == probe
    asyncio.run(scenario())
