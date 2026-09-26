import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC

import pytest

from rainpulse_algo import performance as p


def test_union_overlap():
    assert p._union_ms([(0, 5), (1, 3), (4, 7), (9, 12)], 0, 10) == 8000
    assert p._union_ms([], 0, 10) == 0


def test_nested_inclusive_self_and_no_result_mutation():
    seen = []

    @p.timed("child")
    def child():
        time.sleep(0.001)
        return {"bytes": b"unchanged"}

    @p.timed("root", root=True)
    def root():
        p.observe("objects", 3)
        time.sleep(0.001)
        return child()

    with p.capture_traces(seen.append):
        out = root()
    assert out == {"bytes": b"unchanged"} and len(seen) == 1
    report = seen[0]
    st = report["stages"]
    assert st["root"]["inclusive_ms"] >= st["root/child"]["inclusive_ms"]
    assert st["root"]["self_ms"] >= 0 and st["root"]["self_time_complete"]
    assert report["counters"]["objects"] == 3
    assert report["call_status"] == "returned"
    resources = report["resources"]
    assert "scope" in resources
    # Current-RSS sampling uses procfs; macOS exposes only the historical peak.
    if "process_rss_bytes" in resources["before"]:
        assert resources["sample_count"] >= 1
    else:
        assert resources["sample_count"] == 0
        assert resources["sampled_peak_lower_bound"] == {}
    json.dumps(report, allow_nan=False)
    assert not p._STACK.get()


def test_parallel_context_union():
    reports = []

    @p.timed("download")
    def task():
        time.sleep(0.004)
        p.observe("bytes", 10)

    with p.capture_traces(reports.append):
        with p.measure("root", root=True):
            with ThreadPoolExecutor(3) as pool:
                jobs = [p.submit_with_context(pool, task) for _ in range(3)]
                for f in jobs:
                    f.result()
    st = reports[0]["stages"]
    assert st["root/download"]["count"] == 3
    assert st["root"]["self_ms"] >= 0
    assert reports[0]["counters"]["bytes"] == 30


def test_async_to_thread_context():
    reports = []

    @p.timed("number")
    def call():
        return 3

    @p.timed("request", root=True)
    async def main():
        return await asyncio.to_thread(call)

    with p.capture_traces(reports.append):
        assert asyncio.run(main()) == 3
    assert "request/number" in reports[0]["stages"]


def test_generator_only_resumes_are_measured():
    reports = []

    @p.timed("read")
    def source():
        yield 1
        yield 2

    with p.capture_traces(reports.append):
        with p.measure("root", root=True):
            it = source()
            assert next(it) == 1
            time.sleep(0.01)
            assert it.send(None) == 2
            with pytest.raises(StopIteration):
                next(it)
            it.close()
    root = reports[0]["stages"]["root"]
    part = reports[0]["stages"]["root/read"]
    assert part["errors"] == 0 and root["inclusive_ms"] - part["inclusive_ms"] > 5


def test_exception_still_same_and_no_secrets():
    reports = []
    err = ValueError("do-not-serialize-this-secret")

    @p.timed("op", root=True)
    def call(claim):
        raise err

    claim = {
        "task_id": "x",
        "attempt_id": "a",
        "token": "private-token",
        "identity": {"fingerprint": "abc", "token": "private"},
        "request": {
            "job_id": "j",
            "event_type": "ops.multiband.requested.v1",
            "payload": {"password": "secret"},
        },
    }
    with p.capture_traces(reports.append):
        with pytest.raises(ValueError) as exc:
            call(claim)
    assert exc.value is err
    text = json.dumps(reports)
    assert "private" not in text and "password" not in text and str(err) not in text
    assert (
        reports[0]["call_status"] == "raised"
        and reports[0]["identity"]["frozen_fingerprint"] == "abc"
    )


def test_disabled_does_not_emit(monkeypatch):
    monkeypatch.setenv("RAINPULSE_PERFORMANCE_TELEMETRY", "0")
    seen = []

    @p.timed("root", root=True)
    def root():
        return 42

    with p.capture_traces(seen.append):
        assert root() == 42
    assert not seen and not p._STACK.get()


def test_telemetry_failure_cannot_fail_work(monkeypatch):
    @p.timed("root", root=True)
    def call():
        return b"x"

    def fail(*a, **kw):
        raise RuntimeError("sink down")

    monkeypatch.setattr(p, "_emit", fail)
    assert call() == b"x"
    monkeypatch.setattr(p, "resource_snapshot", fail)
    assert call() == b"x"


def test_bounded_records(monkeypatch):
    seen = []
    monkeypatch.setattr(p, "MAX_STAGES", 12)
    monkeypatch.setattr(p, "MAX_CHILD_INTERVALS", 5)
    with p.capture_traces(seen.append):
        with p.measure("root", root=True):
            for i in range(40):
                with p.measure("child" + str(i)):
                    pass
    assert len(seen[0]["stages"]) <= 12
    assert seen[0]["dropped_stages"] > 0
    assert seen[0]["stages"]["root"]["self_time_complete"] is False
    assert seen[0]["stages"]["root"]["self_ms"] is None


def test_signature_and_dict_identity():
    import inspect

    @p.timed("root", root=True)
    def call(x: int, *, output=None):
        return x

    assert str(inspect.signature(call)) == "(x: int, *, output=None)"
    seen = []
    with p.capture_traces(seen.append):
        call(
            {
                "event_type": "ops",
                "payload": {
                    "network_sha256": "a" * 64,
                    "execution_sha256": "b" * 64,
                    "mode": "x_qc",
                    "raw_password": "secret",
                },
            }
        )
    assert seen[0]["identity"]["mode"] == "x_qc"
    assert "raw_password" not in json.dumps(seen)


def test_isolated_parallel_roots():
    seen = []

    @p.timed("task", root=True)
    def task(x):
        p.identify(job_id=x)
        p.observe("sum", x)
        time.sleep(0.002)
        return x

    # Capture sink propagated explicitly along with each independent context.
    with p.capture_traces(seen.append):
        with ThreadPoolExecutor(2) as pool:
            futures = [p.submit_with_context(pool, task, i) for i in (2, 3)]
            assert [f.result() for f in futures] == [2, 3]
    assert sorted(v["counters"]["sum"] for v in seen) == [2, 3]
    assert not p._STACK.get()


def test_cancellation_closes_trace():
    seen = []

    @p.timed("async", root=True)
    async def call():
        raise asyncio.CancelledError()

    with p.capture_traces(seen.append):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(call())
    assert seen[0]["call_status"] == "raised"
    assert not p._STACK.get()


def test_counter_limit_and_nonfinite(monkeypatch):
    seen = []
    monkeypatch.setattr(p, "MAX_COUNTERS", 3)
    with p.capture_traces(seen.append):
        with p.measure("root", root=True):
            p.observe("nan", float("nan"))
            p.observe("bool", True)
            for i in range(10):
                p.observe(str(i), i)
    assert len(seen[0]["counters"]) == 3
    assert "nan" not in seen[0]["counters"]


def test_generator_throw_preserves_cleanup():
    seen = []
    clean = []

    @p.timed("producer")
    def source():
        try:
            yield 1
        except KeyError:
            yield 2
        finally:
            clean.append(True)

    with p.capture_traces(seen.append):
        with p.measure("root", root=True):
            it = source()
            assert next(it) == 1
            assert it.throw(KeyError("x")) == 2
            it.close()
    assert clean == [True]


def test_request_identity_and_age_do_not_mutate_metrics():
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    reports = []
    request = {
        "event_type": "test.task",
        "job_id": "job-1",
        "run_id": "run-1",
        "occurred_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "payload": {"mode": "x_qc", "network_sha256": "a" * 64, "secret": "must-not-appear"},
    }
    result = SimpleNamespace(
        observability={
            "input_bytes": 123,
            "decoded_cache_hits": 7,
            "resident_input_bytes": 456,
            "password": "hidden",
        },
        objects={"manifest.json": b"unchanged"},
    )

    @p.timed("test.identity", root=True)
    def call(value):
        return result

    with p.capture_traces(reports.append):
        out = call(request)
    assert out is result and out.objects == {"manifest.json": b"unchanged"}
    report = reports[0]
    assert report["identity"]["mode"] == "x_qc"
    assert report["counters"]["request_age_at_start_seconds"] >= 1
    assert report["counters"]["reported.input_bytes"] == 123
    assert report["counters"]["reported.decoded_cache_hits"] == 7
    assert "queue_latency_seconds" not in report["counters"]
    raw = json.dumps(report)
    assert "must-not-appear" not in raw and "hidden" not in raw


def test_resource_unavailable_is_not_zero(monkeypatch):
    monkeypatch.setattr(
        p, "resource_snapshot", lambda: {"gauges": {}, "counters": {}, "scope": "unavailable"}
    )
    reports = []
    with p.capture_traces(reports.append):
        with p.measure("no-resource", root=True):
            pass
    assert reports[0]["resources"]["before"] == {}
    assert reports[0]["resources"]["counter_deltas"] == {}


def test_actual_release_keys():
    from types import SimpleNamespace

    w = SimpleNamespace(
        _startup_release_identity={
            "qc_config_sha256": "a" * 64,
            "qc_flags_sha256": "b" * 64,
            "runtime_sha256": "c" * 64,
            "profile": "radar-qc-basic",
            "secret": "hidden",
        }
    )
    values = p._call_identity((w,), {})
    assert values["qc_config_sha256"] == "a" * 64 and values["profile"] == "radar-qc-basic"
    assert "secret" not in values


def test_disabled_skips_identity_walk(monkeypatch):
    monkeypatch.setenv("RAINPULSE_PERFORMANCE_TELEMETRY", "0")
    visited = []
    monkeypatch.setattr(p, "_call_identity", lambda *args: visited.append(True) or {})

    @p.timed("disabled", root=True)
    def call():
        return 2

    assert call() == 2 and not visited


def test_generator_type_and_return_value():
    import inspect

    @p.timed("generator")
    def call():
        yield 3
        return 7

    assert inspect.isgeneratorfunction(call)
    iterator = call()
    assert inspect.isgenerator(iterator) and next(iterator) == 3
    with pytest.raises(StopIteration) as e:
        next(iterator)
    assert e.value.value == 7


def test_request_age_first_observation_only():
    from datetime import datetime, timedelta

    reports = []
    with p.capture_traces(reports.append):
        with p.measure("root", root=True):
            p._event_age(datetime.now(UTC) - timedelta(seconds=1))
            p._event_age(datetime.now(UTC) - timedelta(days=1))
    assert 1 <= reports[0]["counters"]["request_age_at_start_seconds"] < 5


def test_frozen_identity_does_not_overwrite_running_identity():
    from types import SimpleNamespace

    actual = SimpleNamespace(
        network=SimpleNamespace(sha256="a" * 64),
        adapter=SimpleNamespace(startup={"code_sha256": "c" * 64, "fingerprint": "d" * 64}),
    )
    claim = {
        "task_id": "x",
        "attempt_id": "y",
        "request": {},
        "identity": {"code_sha256": "e" * 64, "fingerprint": "f" * 64},
    }
    request = {"event_type": "test", "payload": {"network_sha256": "b" * 64}}
    values = p._call_identity((actual, claim, request), {})
    assert values["startup_network_sha256"] == "a" * 64
    assert values["network_sha256"] == "b" * 64
    assert values["startup_code_sha256"] == "c" * 64
    assert values["frozen_code_sha256"] == "e" * 64
