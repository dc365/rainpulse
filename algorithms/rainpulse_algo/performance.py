"""Bounded, runtime-only stage traces. No values are added to product metadata.

Nested spans report inclusive and exclusive wall time; overlapping direct child
intervals are unioned (not summed). Context propagates through asyncio.to_thread
and through submit_with_context. Resource counters are process/container scoped,
not asserted to be attributable to one concurrent task. Telemetry failures never
replace the original result/exception. No credentials, paths, or raw arrays.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import json
import logging
import math
import os
import platform
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

VERSION = "rainpulse.performance-ab-v1"
MAX_STAGES = 256
MAX_CHILD_INTERVALS = 4096
MAX_COUNTERS = 128
_TOKEN = re.compile(r"^[A-Za-z0-9_.:/+-]{1,160}$")
_STACK = contextvars.ContextVar("rainpulse_perf_stack", default=())
_CAPTURE = contextvars.ContextVar("rainpulse_perf_capture", default=None)
_CONFIG_LOCK = threading.Lock()
_LOGGER = logging.getLogger("rainpulse.performance")
_LOGGER.propagate = False
_LOG_READY = False


@functools.lru_cache(maxsize=1)
def runtime_identity():
    versions = {}
    for name in ("numpy", "scipy", "numba", "pyproj", "zarr", "arm-pyart", "wradlib"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return {"python": platform.python_version(), "libraries": versions}


def enabled():
    return os.getenv("RAINPULSE_PERFORMANCE_TELEMETRY", "1") != "0"


def _safe(value):
    # Structured identifiers only: never serialize object __dict__ or environment.
    if value is None:
        return None
    value = str(value)
    return value if _TOKEN.fullmatch(value) else "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _read_int(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def _pairs(path):
    try:
        result = {}
        for line in Path(path).read_text().splitlines():
            parts = line.replace(":", " ").split()
            if len(parts) == 2:
                try:
                    result[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        return result
    except OSError:
        return {}


def resource_snapshot():
    """Gauges and cumulative counters; missing platform support stays unavailable."""
    result = {"gauges": {}, "counters": {}, "scope": "process_and_cgroup_not_task"}
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result["gauges"]["process_peak_rss_bytes"] = int(usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024))
        result["counters"].update(process_user_cpu_seconds=usage.ru_utime, process_system_cpu_seconds=usage.ru_stime)
    except (ImportError, OSError, ValueError):
        pass
    try:
        rss = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        result["gauges"]["process_rss_bytes"] = rss
    except (OSError, ValueError, IndexError):
        pass
    io = _pairs("/proc/self/io")
    for key in ("read_bytes", "write_bytes", "rchar", "wchar"):
        if key in io:
            result["counters"]["process_io_" + key] = io[key]
    # cgroup v2. On a host, resolve the process membership; in a container the
    # visible mount may already be rooted at that membership. Never assume v1.
    base = Path("/sys/fs/cgroup")
    try:
        entry = next(x[3:] for x in Path("/proc/self/cgroup").read_text().splitlines() if x.startswith("0::"))
        member = base / entry.lstrip("/")
        if ".." not in Path(entry).parts and (member / "cpu.stat").is_file():
            base = member
    except (OSError, StopIteration):
        pass
    for file, key in (("memory.current", "cgroup_memory_current_bytes"), ("memory.peak", "cgroup_memory_peak_bytes")):
        value = _read_int(base / file)
        if value is not None:
            result["gauges"][key] = value
    mem = _pairs(base / "memory.stat")
    for key in ("anon", "file"):
        if key in mem:
            result["gauges"]["cgroup_memory_" + key + "_bytes"] = mem[key]
    cpu = _pairs(base / "cpu.stat")
    for key in ("usage_usec", "nr_throttled", "throttled_usec"):
        if key in cpu:
            result["counters"]["cgroup_cpu_" + key] = cpu[key]
    return result


def _union_ms(intervals, start, end):
    intervals = sorted((max(a, start), min(b, end)) for a, b in intervals if b > start and a < end)
    total = 0.
    if not intervals:
        return total
    left, right = intervals[0]
    for a, b in intervals[1:]:
        if a <= right:
            right = max(right, b)
        else:
            total += right - left
            left, right = a, b
    return (total + right - left) * 1000.


@dataclass
class _Frame:
    trace: "_Trace"
    path: str
    start: float
    children: list = field(default_factory=list)
    overflow: bool = False


class _Trace:
    def __init__(self, identity):
        self.identity = {str(k): _safe(v) for k, v in identity.items() if v is not None}
        self.stages = {}
        self.counters = {}
        self.dropped_stages = 0
        self.lock = threading.Lock()
        self.before = resource_snapshot()
        self.sample_stop = threading.Event()
        self.sampled_peak = {}
        self.sample_count = 0
        self.sample_interval = 0.25
        self.sampler = threading.Thread(target=self._sample_loop, daemon=True,
                                        name="rp-perf-sampler")
        self._sample()
        self.sampler.start()

    def _sample(self):
        values = {}
        try:
            values["process_rss_bytes"] = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass
        if values:
            with self.lock:
                self.sample_count += 1
                for key, value in values.items():
                    self.sampled_peak[key] = max(value, self.sampled_peak.get(key, 0))

    def _sample_loop(self):
        while not self.sample_stop.wait(self.sample_interval):
            try:
                self._sample()
            except Exception:
                pass

    def close(self):
        self.sample_stop.set()
        self.sampler.join(timeout=1)
        self._sample()

    def add(self, frame, end, error):
        inclusive = (end - frame.start) * 1000
        exclusive = None if frame.overflow else max(0., inclusive - _union_ms(frame.children, frame.start, end))
        with self.lock:
            if frame.path not in self.stages and len(self.stages) >= MAX_STAGES - (1 if "/" in frame.path else 0):
                self.dropped_stages += 1
                return
            value = self.stages.setdefault(frame.path, {"count": 0, "errors": 0, "inclusive_ms": 0., "self_ms": 0., "max_ms": 0., "self_time_complete": True})
            value["count"] += 1
            value["errors"] += int(error)
            value["inclusive_ms"] += inclusive
            value["max_ms"] = max(value["max_ms"], inclusive)
            if exclusive is None:
                value["self_time_complete"] = False
                value["self_ms"] = None
            elif value["self_ms"] is not None:
                value["self_ms"] += exclusive

    def output(self, status):
        after = resource_snapshot()
        before_counts = self.before["counters"]
        delta = {k: v - before_counts[k] for k, v in after["counters"].items() if k in before_counts and v >= before_counts[k]}
        threads = {}
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
            raw = os.getenv(name, "")
            if raw.isdigit():
                threads[name] = int(raw)
        try:
            affinity = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            affinity = None
        return {"schema": VERSION, "event": "performance.trace", "call_status": status,
                "identity": self.identity, "runtime": runtime_identity(),
                "thread_limits": threads, "cpu_affinity_count": affinity,
                "stages": self.stages, "counters": self.counters,
                "dropped_stages": self.dropped_stages,
                "resources": {"before": self.before["gauges"], "after": after["gauges"],
                              "counter_deltas": delta, "scope": after["scope"],
                              "sampled_peak_lower_bound": self.sampled_peak,
                              "sample_count": self.sample_count,
                              "sampling_interval_seconds": self.sample_interval},
                "timings_are_runtime_only": True}


def _emit(value):
    capture = _CAPTURE.get()
    if capture is not None:
        capture(value)
        return
    global _LOG_READY
    if not _LOG_READY:
        with _CONFIG_LOCK:
            if not _LOG_READY:
                if not _LOGGER.handlers:
                    handler = logging.StreamHandler()
                    handler.setFormatter(logging.Formatter("%(message)s"))
                    _LOGGER.addHandler(handler)
                _LOGGER.setLevel(logging.INFO)
                _LOG_READY = True
    _LOGGER.info(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


@contextmanager
def capture_traces(sink):
    """Tests/offline tools only; does not write timing to meteorological products."""
    token = _CAPTURE.set(sink)
    try:
        yield
    finally:
        _CAPTURE.reset(token)


@contextmanager
def measure(name, *, root=False, identity=None):
    stack = _STACK.get()
    if not stack and (not root or not enabled()):
        yield
        return
    try:
        trace = stack[-1].trace if stack else _Trace(identity or {})
        path = stack[-1].path + "/" + name if stack else name
        frame = _Frame(trace, path, time.perf_counter())
        token = _STACK.set((*stack, frame))
    except Exception:
        yield
        return
    failed = False
    try:
        yield
    except BaseException as exc:
        failed = not isinstance(exc, (StopIteration, GeneratorExit))
        raise
    finally:
        end = time.perf_counter()
        _STACK.reset(token)
        try:
            if not stack:
                trace.close()
            trace.add(frame, end, failed)
            if stack:
                parent = stack[-1]
                with trace.lock:
                    if len(parent.children) < MAX_CHILD_INTERVALS:
                        parent.children.append((frame.start, end))
                    else:
                        parent.overflow = True
            else:
                _emit(trace.output("raised" if failed else "returned"))
        except Exception:
            # Observability is never allowed to hide the algorithm's outcome.
            pass


def observe(name, value, *, maximum=False):
    stack = _STACK.get()
    if not stack or type(value) not in (int, float) or not math.isfinite(value):
        return
    trace = stack[-1].trace
    with trace.lock:
        if name not in trace.counters and len(trace.counters) >= MAX_COUNTERS:
            return
        previous = trace.counters.get(name, 0)
        trace.counters[name] = max(previous, value) if maximum else previous + value


def identify(**values):
    stack = _STACK.get()
    if not stack:
        return
    with stack[-1].trace.lock:
        for k, v in values.items():
            if v is not None and len(stack[-1].trace.identity) < 32:
                stack[-1].trace.identity[k] = _safe(v)


def _event_age(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime) and value.tzinfo is not None:
            age = (datetime.now(timezone.utc) - value).total_seconds()
            stack = _STACK.get()
            if stack:
                trace = stack[-1].trace
                with trace.lock:
                    names = ("request_age_at_start_seconds", "request_timestamp_ahead_seconds")
                    # Nested root-capable adapters must not relabel a later
                    # entry as the outer request's start.
                    if not any(k in trace.counters for k in names) and len(trace.counters) < MAX_COUNTERS:
                        trace.counters[names[0] if age >= 0 else names[1]] = abs(age)
    except (ValueError, OverflowError):
        pass


def _observe_result(result):
    try:
        values = (result[2] if isinstance(result, tuple) and len(result) == 3 and isinstance(result[2], dict)
                  else getattr(result, "observability", {}))
        allowed = {"input_bytes", "output_bytes", "object_count", "input_download_bytes",
                   "packed_staged_bytes", "peak_physical_object_bytes", "qc_executions",
                   "decoded_cut_cache_hits", "decoded_cut_cache_misses", "decoded_cache_hits",
                   "decoded_cache_misses", "metadata_cache_hits", "output_encoded_bytes",
                   "peak_qc_cut_array_bytes", "peak_input_cut_array_bytes", "geometry_hits",
                   "geometry_misses", "decoded_cache_bytes", "resident_input_bytes"}
        if isinstance(values, dict):
            for key in allowed & values.keys():
                value = values[key]
                if type(value) in (float, int) and math.isfinite(value):
                    # Existing metrics keep their original cumulative/gauge
                    # meanings; they are not relabelled as new per-task RSS.
                    observe("reported." + key, value, maximum=True)
    except Exception:
        pass


def _call_identity(args, kwargs):
    values = {}
    for arg in args:
        network = getattr(arg, "network", None)
        if network is not None and getattr(network, "sha256", None) is not None:
            values["startup_network_sha256"] = network.sha256
        execution = getattr(arg, "execution", None)
        if execution is not None:
            values["selection_backend"] = getattr(execution, "selection_backend", None)
            values["streaming"] = getattr(execution, "streaming", None)
        if isinstance(arg, dict) and "request" in arg and "attempt_id" in arg:
            values.update({k: arg[k] for k in ("task_id", "attempt_id") if k in arg})
            request = arg.get("request", {})
            if isinstance(request, dict):
                values.update({k: request[k] for k in ("job_id", "run_id", "event_type") if k in request})
                _event_age(request.get("occurred_at"))
            identity = arg.get("identity", {})
            if isinstance(identity, dict):
                values.update({"frozen_" + k: identity[k] for k in ("fingerprint", "code_sha256", "kind") if k in identity})
        adapter = getattr(arg, "adapter", None)
        adapter_startup = getattr(adapter, "startup", None)
        if isinstance(adapter_startup, dict):
            values.update({"startup_" + k: adapter_startup[k]
                           for k in ("fingerprint", "code_sha256", "kind") if k in adapter_startup})
        startup = getattr(arg, "_startup_release_identity", None)
        if isinstance(startup, dict):
            values.update({k: startup[k] for k in ("fingerprint", "code_sha256", "qc_parameters_sha256", "qc_config_sha256", "qc_flags_sha256", "runtime_sha256", "profile") if k in startup})
        if hasattr(arg, "event_type") and hasattr(arg, "job_id"):
            values.update(job_id=str(arg.job_id), event_type=arg.event_type)
            for key in ("run_id", "trace_id"):
                if getattr(arg, key, None) is not None:
                    values[key] = str(getattr(arg, key))
            _event_age(getattr(arg, "occurred_at", None))
        if isinstance(arg, dict) and "event_type" in arg and "payload" in arg:
            values.update({k: arg[k] for k in ("job_id", "run_id", "event_type") if k in arg})
            _event_age(arg.get("occurred_at"))
            payload = arg.get("payload", {})
            if isinstance(payload, dict):
                values.update({k: payload[k] for k in ("mode", "network_sha256", "execution_sha256") if k in payload})
        for key in ("profile_version", "parameters_hash", "pipeline_version", "execution_policy_sha256"):
            if hasattr(arg, key):
                values[key] = getattr(arg, key)
    # Import/loaded release identity is provided by the Worker, not guessed from
    # a git command against a potentially dirty deployment checkout.
    return values


class _TimedIterator:
    def __init__(self, iterator, name):
        self.iterator, self.name = iterator, name
    def __iter__(self):
        return self
    def __next__(self):
        with measure(self.name):
            return next(self.iterator)
    def send(self, value):
        with measure(self.name):
            return self.iterator.send(value)
    def throw(self, *args):
        with measure(self.name):
            return self.iterator.throw(*args)
    def close(self):
        with measure(self.name):
            return self.iterator.close()


def timed(name, *, root=False):
    """Decorate a sync/async call or each generator resume, preserving its API."""
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def asynchronous(*args, **kwargs):
                with measure(name, root=root):
                    try:
                        if _STACK.get():
                            identify(**_call_identity(args, kwargs))
                    except Exception:
                        pass
                    return await fn(*args, **kwargs)
            return asynchronous
        if inspect.isgeneratorfunction(fn):
            @functools.wraps(fn)
            def generator(*args, **kwargs):
                # yield-from preserves generator introspection and return values
                # while the proxy times only each actual producer resumption.
                return (yield from _TimedIterator(fn(*args, **kwargs), name))
            return generator
        @functools.wraps(fn)
        def synchronous(*args, **kwargs):
            with measure(name, root=root):
                try:
                    if root and _STACK.get():
                        identify(**_call_identity(args, kwargs))
                except Exception:
                    pass
                result = fn(*args, **kwargs)
                if root and _STACK.get():
                    _observe_result(result)
                return result
        return synchronous
    return decorate


def submit_with_context(pool, fn, *args, **kwargs):
    """An independent Context per submission: one Context cannot run concurrently."""
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)
