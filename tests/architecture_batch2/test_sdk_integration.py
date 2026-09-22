"""Run against the real installed Worker SDK and Zarr 2 in a complete checkout."""
import io
import os
import pytest

if os.getenv("RAINPULSE_PACKAGE_CORE_ONLY") == "1":
    pytest.skip("package-only validation excludes full SDK integration", allow_module_level=True)
import numpy as np

pytest.importorskip("minio", reason="actual MinIO SDK required for adapter integration")
pytest.importorskip("nats", reason="actual NATS SDK required for Worker package imports")
zarr = pytest.importorskip("zarr", reason="actual Zarr 2 required for scientific integration")

from rainpulse_algo.worker.object_store import ArtifactObjectReader, artifact_sha256
from rainpulse_algo.worker.asset_cache import CacheLimits, VerifiedObjectCache
from rainpulse_algo.products.interval import IntervalService
from fixtures import Store


class Response(io.BytesIO):
    def release_conn(self): pass


class MinioFixture:
    def __init__(self, objects):
        self.backend, self.uri = Store(objects), "s3://rainpulse/asset"
        self._rainpulse_cache_namespace = "integration-fixture"
    def get_object(self, bucket, key):
        return Response(self.backend.read(bucket, key, 1024**3))


def ensemble_objects():
    store=zarr.storage.MemoryStore();root=zarr.group(store=store)
    root.attrs.update({"schema_version":"1.0"})
    lat=np.array([25.,25.01],dtype="float64");lon=np.array([119.,119.01],dtype="float64")
    rates=np.full((2,3,2,2),10.,dtype="float32")
    mask=np.ones(rates.shape,dtype="uint8")
    mask[:,1,0,0]=0;rates[:,1,0,0]=np.nan
    for key,data in {"rain_rate":rates,"member_valid_mask":mask,"lat":lat,"lon":lon,
                     "lead_time":np.array([6,12,18]),"unused_members":np.zeros((2,100,100),dtype="float32")}.items():
        root.create_dataset(key,data=data,chunks=data.shape)
    return {key:bytes(value) for key,value in store.items()}


def test_actual_reader_preserves_full_artifact_and_selects_array_fields():
    objects=ensemble_objects();client=MinioFixture(objects)
    cache=VerifiedObjectCache(CacheLimits(max_bytes=1024**2))
    reader=ArtifactObjectReader(client,cache=cache)
    assert reader.load(client.uri)==objects
    result=reader.load_selected(client.uri,keys=[".zgroup",".zattrs"],prefixes=["rain_rate","member_valid_mask","lat","lon","lead_time"],expected_sha256=artifact_sha256(objects))
    assert not any(key.startswith("unused_members/") for key in result)
    assert result=={k:v for k,v in objects.items() if not k.startswith("unused_members/")}


def test_actual_interval_result_and_missing_mask_identical_to_full_read(monkeypatch):
    objects=ensemble_objects();client=MinioFixture(objects)
    request={"start":0,"end":18,"algorithm":"steps","issue_time":"2026-09-22T00:00:00Z",
        "bounds":[118.995,24.995,119.015,25.015],"sources":[{"uri":client.uri,"sha256":artifact_sha256(objects)}]}
    selected=IntervalService(client);frame=selected.calculate(request)
    result=selected.cache[frame["asset_id"]]
    def full_read(self,uri,**kwargs):return self.load(uri,expected_sha256=kwargs.get("expected_sha256"))
    monkeypatch.setattr(ArtifactObjectReader,"load_selected",full_read)
    reference=IntervalService(MinioFixture(objects));full=reference.calculate(request)
    expected=reference.cache[full["asset_id"]]
    assert frame==full and result[2]==expected[2]
    np.testing.assert_array_equal(result[3],expected[3])
    assert np.isnan(result[3][0,0]) and frame["missing_cell_count"]==1
