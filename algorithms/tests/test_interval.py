import hashlib
import json

import numpy as np
import pytest

from rainpulse_algo.products.interval import IntervalService, decode_point, integrate
from rainpulse_algo.products.point_index import encode_point_query_index


def test_arbitrary_interval_and_missing_support():
    rates = np.full((1, 24, 2, 3), 12.0)
    valid = np.ones_like(rates, dtype=bool)
    valid[:, 0, 0, 0] = False
    values, support = integrate(rates, valid, list(range(5, 121, 5)), 15, 45)
    assert np.all(values == 6)
    assert support.all()
    values, support = integrate(rates, valid, list(range(5, 121, 5)), 0, 60)
    assert np.isnan(values[0, 0]) and not support[0, 0]
    assert values[1, 0] == 12


def test_member_accumulation_precedes_p50():
    rates = np.zeros((3, 24, 1, 1))
    rates[0, :12] = 10
    rates[1, 12:] = 10
    values, _ = integrate(rates, np.ones_like(rates), list(range(5, 121, 5)), 0, 120, quantile=0.5)
    assert values.item() == 10
    assert np.median(rates, axis=0).sum() == 0


@pytest.mark.parametrize("start,end", [(0, 0), (-5, 60), (10, 5), (0, 125), (1, 60)])
def test_invalid_interval(start, end):
    with pytest.raises(ValueError):
        integrate(
            np.ones((1, 24, 1, 1)), np.ones((1, 24, 1, 1)), list(range(5, 121, 5)), start, end
        )


def test_missing_frame_and_duplicate_rejected():
    for leads in ([5, 15], [5, 5]):
        with pytest.raises(ValueError):
            integrate(np.ones((1, 2, 1, 1)), np.ones((1, 2, 1, 1)), leads, 0, 15)


def test_point_order_cache_invalidation_and_sample(monkeypatch):
    service = IntervalService()
    rate = np.arange(24 * 2 * 3, dtype=np.float32).reshape(24, 2, 3)
    data = encode_point_query_index(
        rate,
        np.ones_like(rate),
        np.ones_like(rate),
        west=118,
        south=25,
        longitude_interval=0.01,
        latitude_interval=0.01,
    )
    np.testing.assert_equal(decode_point(data)[0], rate)
    monkeypatch.setattr(service, "_read", lambda *args: data)
    request = dict(
        algorithm="lk",
        start=15,
        end=45,
        issue_time="2026-08-28T08:30:00Z",
        bounds=[117.995, 24.995, 118.025, 25.015],
        sources=[
            dict(
                uri="s3://test/rate",
                sha256=hashlib.sha256(data).hexdigest(),
                leads=list(range(5, 121, 5)),
                indices=list(range(24)),
            )
        ],
    )
    frame = service.calculate(request)
    assert frame is service.calculate(request)
    key = frame["asset_id"]
    code, _, body = service.dispatch(
        "GET", f"/interval/{key}/sample?longitude=118&latitude=25", b""
    )
    assert code == 200
    assert json.loads(body)["value"] == pytest.approx(float(rate[3:9, 0, 0].sum() / 12))
    assert service.dispatch("GET", f"/interval/{key}/image", b"")[2].startswith(b"\x89PNG")
    changed = {**request, "sources": [{**request["sources"][0], "uri": "s3://test/updated-rate"}]}
    assert service.calculate(changed)["asset_id"] != key
    service.cache[key] = (0, *service.cache[key][1:])
    assert service.dispatch("GET", f"/interval/{key}/image", b"")[0] == 410


def test_checksum_failure_is_not_cached(monkeypatch):
    service = IntervalService()
    monkeypatch.setattr(service, "_read", lambda *args: b"bad")
    with pytest.raises(ValueError, match="checksum"):
        service._point(dict(uri="s3://test/source", sha256="0" * 64))
    assert not service.sources


def test_legacy_qpe_float32_pitch_is_not_a_different_grid(monkeypatch):
    service = IntervalService()
    rate = np.ones((1, 2, 3), dtype=np.float32) * 12
    def pitch(origin):
        return float(np.float32(origin + 0.01)) - float(np.float32(origin))
    data = encode_point_query_index(
        rate,
        np.ones_like(rate),
        np.ones_like(rate),
        west=118,
        south=25,
        longitude_interval=pitch(118),
        latitude_interval=pitch(25),
    )
    monkeypatch.setattr(service, "_read", lambda *args: data)
    request = dict(
        algorithm="qpe",
        start=0,
        end=5,
        issue_time="2026-08-28T08:30:00Z",
        bounds=[117.995, 24.995, 118.025, 25.015],
        sources=[
            dict(
                uri="s3://test/qpe", sha256=hashlib.sha256(data).hexdigest(), leads=[5], indices=[0]
            )
        ],
    )
    assert service.calculate(request)["valid_cell_count"] == 6
    with pytest.raises(ValueError, match="grid differs"):
        service.calculate({**request, "bounds": [117.995, 24.995, 118.028, 25.015]})
