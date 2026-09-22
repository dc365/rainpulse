"""Contract validation without requiring a live operational service."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_all_openapi_references_exist_and_operations_are_unique():
    value = json.loads((ROOT / 'contracts/internal/operations-openapi.json').read_text())
    assert value['openapi'] == '3.0.3'
    seen = set()
    def visit(node):
        if isinstance(node, dict):
            if '$ref' in node:
                path = node['$ref'].split('/')[1:]
                ref = value
                for part in path:
                    ref = ref[part]
            for sub in node.values():
                visit(sub)
        elif isinstance(node, list):
            for sub in node:
                visit(sub)
    visit(value)
    for path, methods in value['paths'].items():
        for op in methods.values():
            assert op['operationId'] not in seen
            seen.add(op['operationId'])
            wanted = 'workerBearer' if path.startswith('/internal/') else 'adminBearer'
            assert op['security'] == [{wanted: []}]
    assert len(value['paths']) == 23


def test_commands_reject_unknown_properties_and_bound_logs():
    v = json.loads((ROOT / 'contracts/internal/operations-openapi.json').read_text())
    schemas = v['components']['schemas']
    for name in ['Selection','Submit','Action','ClaimRequest','Pulse','Finish']:
        assert schemas[name]['additionalProperties'] is False
    assert schemas['Pulse']['properties']['lines']['maxItems'] == 20
    assert schemas['LogLine']['properties']['message']['maxLength'] == 4096
    assert schemas['Submit']['properties']['idempotency_key']['maxLength'] == 128
    assert schemas['Candidate']['properties']['candidate_only']['enum'] == [True]


def test_blocked_plan_identity_does_not_weaken_worker_identity():
    import re
    v = json.loads((ROOT / 'contracts/internal/operations-openapi.json').read_text())
    schemas = v['components']['schemas']
    requirement = schemas['Spec']['properties']['identity']['properties']['fingerprint']
    worker = schemas['Identity']['properties']['fingerprint']
    assert re.fullmatch(requirement['pattern'], '')
    assert not re.fullmatch(worker['pattern'], '')
