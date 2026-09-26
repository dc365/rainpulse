import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[3] / "scripts/summarize_performance_ab.py"
    spec = importlib.util.spec_from_file_location("ab_summary", path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_reports_root_not_inclusive_sum():
    m = module()
    record = {
        "schema": m.SCHEMA,
        "event": "performance.trace",
        "call_status": "returned",
        "stages": {
            "root": {
                "count": 1,
                "errors": 0,
                "inclusive_ms": 10.0,
                "self_ms": 2.0,
                "self_time_complete": True,
            },
            "root/child": {
                "count": 2,
                "errors": 0,
                "inclusive_ms": 8.0,
                "self_ms": 8.0,
                "self_time_complete": True,
            },
        },
    }
    out = m.summarize(["irrelevant\n", json.dumps(record)])
    assert out["root_wall_ms"]["root"]["p95"] == 10.0
    assert out["trace_records"] == 1 and out["ignored_lines"] == 1
    assert out["stages"]["root/child"]["calls"] == 2
    assert "returned is not workflow success" in out["warning"]


def test_report_bounds():
    m = module()
    record = {"schema": m.SCHEMA, "event": "performance.trace", "stages": {}}
    with pytest.raises(ValueError):
        m.summarize([json.dumps(record)] * 2, maximum_records=1)
    with pytest.raises(ValueError):
        m.summarize(["x" * (2 * 1024**2 + 1)])
    assert m.percentile([], 0.99) is None
