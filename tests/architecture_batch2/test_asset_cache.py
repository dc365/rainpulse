import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core_modules import cache


def identity(data=b"abc", *, key="object", namespace="test"):
    return cache.ObjectIdentity(namespace, "rainpulse", key, hashlib.sha256(data).hexdigest(), len(data))


def test_cache_hits_are_immutable_and_loader_not_called():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=20))
    first = c.read(identity(), lambda: b"abc")
    second = c.read(identity(), lambda: pytest.fail("cache miss"))
    assert first.source == "store" and second.source == "cache"
    assert second.data is first.data
    assert c.snapshot()["bytes"] == 3
    with pytest.raises(TypeError):
        second.data[0] = 0


@pytest.mark.parametrize("bad", [b"abd", b"a", bytearray(b"abc")])
def test_corruption_does_not_poison_cache(bad):
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=20))
    with pytest.raises(RuntimeError):
        c.read(identity(), lambda: bad)
    assert c.snapshot()["entries"] == 0
    assert c.read(identity(), lambda: b"abc").source == "store"


def test_lru_byte_limit():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=6))
    for key in "ab":
        c.read(identity(key=key), lambda: b"abc")
    c.read(identity(key="a"), lambda: pytest.fail("a must be warm"))
    c.read(identity(key="c"), lambda: b"abc")
    assert c.read(identity(key="a"), lambda: pytest.fail("a evicted instead of b")).source == "cache"
    assert c.read(identity(key="b"), lambda: b"abc").source == "store"
    assert c.snapshot()["peak_bytes"] <= 6


def test_entry_count_bounds_zero_size_objects():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=10, max_entries=2))
    for i in range(100):
        c.read(identity(b"", key=str(i)), lambda: b"")
    assert c.snapshot()["entries"] == 2


@pytest.mark.parametrize("limits", [cache.CacheLimits(max_bytes=0),
                                    cache.CacheLimits(max_bytes=100, max_entry_bytes=2)])
def test_oversize_and_disabled_cache_bypass(limits):
    c = cache.VerifiedObjectCache(limits)
    for _ in range(2):
        assert c.read(identity(), lambda: b"abc").source == "store"
    assert c.snapshot()["entries"] == 0
    assert c.snapshot()["bypasses"] == 2


def test_ttl_does_not_extend_on_hit_and_releases_expired_memory():
    clock = [0.0]
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=100, ttl_seconds=10), clock=lambda: clock[0])
    c.read(identity(), lambda: b"abc")
    clock[0] = 9
    assert c.read(identity(), lambda: b"abc").source == "cache"
    clock[0] = 10
    assert c.snapshot()["entries"] == 0
    assert c.read(identity(), lambda: b"abc").source == "store"


def test_endpoint_principal_and_object_identities_do_not_mix():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=100))
    for ns in ("endpoint-a/principal-a", "endpoint-b/principal-a", "endpoint-a/principal-b"):
        assert c.read(identity(namespace=ns), lambda: b"abc").source == "store"
    assert c.read(identity(b"xyz"), lambda: b"xyz").source == "store"
    assert c.snapshot()["entries"] == 4


def test_concurrent_duplicate_reads_are_single_flight():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=100, max_inflight=2))
    start, release = threading.Event(), threading.Event()
    calls = []
    def load():
        calls.append(1); start.set(); assert release.wait(3); return b"abc"
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(c.read, identity(), load)]
        assert start.wait(2)
        futures.extend(pool.submit(c.read, identity(), load) for _ in range(7))
        until = time.monotonic() + 2
        while c.snapshot()["shared_reads"] < 7 and time.monotonic() < until:
            time.sleep(0.001)
        release.set()
        assert [f.result().data for f in futures] == [b"abc"] * 8
    assert len(calls) == 1
    assert c.snapshot()["shared_reads"] == 7


def test_global_download_concurrency_is_bounded_even_without_retention():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=0, max_inflight=2))
    lock = threading.Lock()
    active, peak = [0], [0]
    def load():
        with lock:
            active[0] += 1; peak[0] = max(peak[0], active[0])
        time.sleep(0.005)
        with lock:
            active[0] -= 1
        return b"abc"
    with ThreadPoolExecutor(max_workers=12) as pool:
        result = list(pool.map(lambda i: c.read(identity(key=str(i)), load), range(50)))
    assert len(result) == 50 and peak[0] == 2
    assert c.snapshot()["peak_inflight"] == 2 and c.snapshot()["inflight"] == 0


def test_failed_owner_releases_waiters_and_retry_is_possible():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=100))
    start, release = threading.Event(), threading.Event()
    def fail():
        start.set(); assert release.wait(3); raise OSError("dependency down")
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(c.read, identity(), fail)
        assert start.wait(2)
        waiter = pool.submit(c.read, identity(), fail)
        until = time.monotonic() + 2
        while c.snapshot()["shared_reads"] != 1 and time.monotonic() < until:
            time.sleep(0.001)
        release.set()
        for f in (owner, waiter):
            with pytest.raises(OSError):
                f.result()
    assert c.read(identity(), lambda: b"abc").data == b"abc"


def test_clear_cannot_be_undone_by_inflight_owner():
    c = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=100))
    def load():
        c.clear(); return b"abc"
    assert c.read(identity(), load).data == b"abc"
    assert c.snapshot()["entries"] == 0


@pytest.mark.parametrize("args", [
    {"max_bytes": -1}, {"max_bytes": True}, {"max_entries": 0}, {"max_inflight": 0},
    {"max_inflight": 33}, {"max_entry_bytes": 0}, {"ttl_seconds": 0},
    {"ttl_seconds": float("inf")}, {"ttl_seconds": float("nan")},
])
def test_invalid_budgets_rejected(args):
    with pytest.raises(ValueError):
        cache.CacheLimits(**args)
