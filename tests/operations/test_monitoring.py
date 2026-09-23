"""Management v2 contracts and production Worker intake helpers."""
import json
import math
from pathlib import Path

import pytest

from rainpulse_algo.operations.worker import may_fetch

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('ready,accepting,seen,now,expected', [
    (True, True, 100, 100, True),
    (True, True, 100, 174.9, True),
    (True, True, 100, 175, False),
    (True, True, 100, 99, False),
    (False, True, 100, 101, False),
    (True, False, 100, 101, False),
    (True, None, 100, 101, False),
    (True, 'true', 100, 101, False),
    (True, True, math.nan, 101, False),
    (True, True, 100, math.inf, False),
])
def test_intake_is_independent_from_health(ready, accepting, seen, now, expected):
    assert may_fetch(ready, accepting, seen, now) is expected


def test_additive_upgrade_does_not_invent_old_queue_times():
    source = (ROOT / 'services/control/internal/operations/schema_v2.sql').read_text()
    assert 'ADD COLUMN IF NOT EXISTS queued_at' in source
    assert 'UPDATE ops_tasks' not in source
    assert 'UPDATE ops_attempts' not in source
    assert "ON CONFLICT DO NOTHING" in source


def test_new_code_does_not_overwrite_weather_products():
    directory = ROOT / 'services/control/internal/operations'
    for name in ['data.go', 'resources.go', 'performance.go']:
        source = (directory / name).read_text()
        for forbidden in ['UPDATE jobs ', 'UPDATE radar_scan_runs ', 'UPDATE analysis_cycles ', 'DELETE FROM', 'os/exec']:
            assert forbidden not in source


def test_new_contracts_are_closed_and_preserve_scope():
    api = json.loads((ROOT / 'contracts/internal/operations-openapi.json').read_text())
    schemas = api['components']['schemas']
    assert schemas['PoolAction']['additionalProperties'] is False
    assert schemas['PoolAction']['properties']['expected_revision']['minimum'] == 1
    assert schemas['AssetCheck']['properties']['scope']['enum'] == ['completion_marker_only']
    assert schemas['PerformanceReport']['properties']['quantile_basis']['enum'] == ['successful_attempts_only_nearest_rank']
    assert schemas['Task']['properties']['queued_at']['nullable'] is True
    for scope in ['runs', 'tasks']:
        operation = api['paths'][f'/api/v1/admin/ops/{scope}/{{id}}/events']['get']
        assert {'level', 'attempt', 'q', 'before', 'after', 'direction'} <= {p['name'] for p in operation['parameters']}
