import hashlib
import json

import pytest

from core_modules import access, cache
from fixtures import Store

OBJECTS = {".zgroup": b'{"zarr_format":2}', ".zattrs": b'{"valid":true}',
           "rain_rate/.zarray": b"array metadata", "rain_rate/0.0": b"a" * 50,
           "rain_rate_backup/0.0": b"b" * 100, "lat/0": b"latitude",
           "qc/summary.json": b'{"operational_eligible":false}', "other/0": b"other"}


@pytest.mark.parametrize("packed", [False, True])
def test_default_full_read_preserves_all_original_bytes(packed):
    store = Store(OBJECTS, packed=packed)
    reader = store.reader()
    assert reader.load("s3://rainpulse/asset") == OBJECTS
    assert access.artifact_digest(reader.load("s3://rainpulse/asset")) == store.marker["sha256"]
    assert len(store.object_gets) == len(store.marker["objects"])
    assert len([k for _, k in store.calls if k.endswith("/_SUCCESS.json")]) == 2


def test_selected_fields_do_not_fetch_unrelated_data():
    store = Store(OBJECTS)
    reader = store.reader()
    subset = reader.load_selected("s3://rainpulse/asset", keys=[".zgroup"], prefixes=["rain_rate"],
                                  expected_sha256=store.marker["sha256"])
    assert subset == {key: OBJECTS[key] for key in (".zgroup", "rain_rate/.zarray", "rain_rate/0.0")}
    assert len(store.object_gets) == 3
    assert all("rain_rate_backup" not in k and not k.endswith("other/0") for k in store.object_gets)


def test_summary_only_read_keeps_false_and_missing_not_zero():
    store = Store(OBJECTS)
    result = store.reader().load_selected("s3://rainpulse/asset", keys=["qc/summary.json"])
    assert json.loads(result["qc/summary.json"])["operational_eligible"] is False
    assert len(store.object_gets) == 1


def test_pack_selection_is_full_verified_fallback_not_unverified_range():
    store = Store(OBJECTS, packed=True)
    reader = store.reader()
    result = reader.load_selected("s3://rainpulse/asset", keys=["qc/summary.json"])
    assert result == {"qc/summary.json": OBJECTS["qc/summary.json"]}
    assert len(store.object_gets) == len(store.marker["objects"])
    assert reader.last_session.stats["packed_full_fallback"] == 1


def test_full_and_partial_reuse_same_verified_cache_across_readers():
    store = Store(OBJECTS)
    shared = cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=1024**2))
    store.reader(shared=shared).load("s3://rainpulse/asset")
    count = len(store.object_gets)
    reader = store.reader(shared=shared)
    reader.load_selected("s3://rainpulse/asset", prefixes=["rain_rate"])
    assert len(store.object_gets) == count
    assert reader.last_session.stats["cache_hits"] == 2


def test_success_marker_is_not_cached():
    store = Store(OBJECTS)
    reader = store.reader()
    reader.load("s3://rainpulse/asset")
    del store.data["asset/_SUCCESS.json"]
    with pytest.raises(KeyError):
        reader.load("s3://rainpulse/asset")


def test_manifest_snapshot_is_pinned_across_selections():
    store = Store(OBJECTS)
    reader = store.reader()
    session = reader.open("s3://rainpulse/asset")
    store.marker["sha256"] = "f" * 64
    store.update_marker()
    assert session.load(keys=[".zattrs"])[".zattrs"] == OBJECTS[".zattrs"]
    with pytest.raises(RuntimeError):
        reader.load("s3://rainpulse/asset")


@pytest.mark.parametrize("packed", [False, True])
def test_corrupt_object_rejected_before_caching(packed):
    store = Store(OBJECTS, packed=packed)
    key = next(k for k in store.data if not k.endswith("_SUCCESS.json"))
    store.data[key] = b"x" * len(store.data[key])
    reader = store.reader()
    with pytest.raises(RuntimeError, match="checksum"):
        reader.load("s3://rainpulse/asset")
    assert reader.cache.snapshot()["errors"] >= 1


@pytest.mark.parametrize("packed", [False, True])
def test_expected_bundle_mismatch_fails_before_object_reads(packed):
    store = Store(OBJECTS, packed=packed)
    with pytest.raises(RuntimeError):
        store.reader().load_selected("s3://rainpulse/asset", keys=[".zgroup"], expected_sha256="f" * 64)
    assert store.object_gets == []


def test_logical_manifest_tamper_detected_even_in_unselected_field():
    store = Store(OBJECTS)
    store.marker["objects"][-1]["sha256"] = "0" * 64
    store.update_marker()
    with pytest.raises(RuntimeError, match="checksum"):
        store.reader().load_selected("s3://rainpulse/asset", keys=[".zgroup"])
    assert not store.object_gets


def test_packed_logical_index_tamper_detected_by_full_root_hash():
    store = Store(OBJECTS, packed=True)
    store.marker["packed_entries"][0][0] = "renamed"
    store.update_marker()
    with pytest.raises(RuntimeError, match="checksum"):
        store.reader().load_selected("s3://rainpulse/asset", keys=["qc/summary.json"])


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(schema_version="99"),
    lambda m: m.update(size_bytes=-1),
    lambda m: m.update(size_bytes=True),
    lambda m: m.update(size_bytes=1),
    lambda m: m.update(data_prefix="../escape"),
    lambda m: m.update(data_prefix="_objects/" + "0" * 64),
    lambda m: m["objects"].append(m["objects"][0]),
    lambda m: m["objects"][0].update(key="../secret"),
    lambda m: m["objects"][0].update(size_bytes=True),
    lambda m: m["objects"][0].update(sha256="bad"),
    lambda m: m.update(objects=[]),
])
def test_bad_manifest_rejected_before_downloads(mutation):
    store = Store(OBJECTS)
    mutation(store.marker); store.update_marker()
    with pytest.raises(RuntimeError):
        store.reader().load("s3://rainpulse/asset")
    assert not store.object_gets


@pytest.mark.parametrize("mutation", [
    lambda m: m["packed_entries"][0].__setitem__(2, 1),
    lambda m: m["packed_entries"][0].__setitem__(3, -1),
    lambda m: m["packed_entries"][0].__setitem__(3, True),
    lambda m: m["packed_entries"][0].__setitem__(1, "nonexistent"),
    lambda m: m["packed_entries"].append(m["packed_entries"][0]),
    lambda m: m["packed_entries"].pop(),
    lambda m: m["packed_entries"][0].__setitem__(0, "/escape"),
])
def test_invalid_pack_index_fails_before_downloads(mutation):
    store = Store(OBJECTS, packed=True)
    mutation(store.marker); store.update_marker()
    with pytest.raises(RuntimeError):
        store.reader().load_selected("s3://rainpulse/asset", keys=[".zgroup"])
    assert not store.object_gets


@pytest.mark.parametrize("selection", [{"keys": []}, {"prefixes": []}, {"keys": "lat/0"},
                                        {"keys": ["absent"]}, {"prefixes": ["absent"]},
                                        {"keys": ["../escape"]}, {}])
def test_invalid_or_missing_selection_never_synthesizes_zero(selection):
    store = Store(OBJECTS)
    with pytest.raises((ValueError, KeyError, RuntimeError)):
        store.reader().load_selected("s3://rainpulse/asset", **selection)
    assert not store.object_gets


def test_input_size_limit_applies_to_partial_read_too():
    store = Store(OBJECTS)
    with pytest.raises(RuntimeError, match="input byte limit"):
        store.reader(maximum=10).load_selected("s3://rainpulse/asset", keys=[".zgroup"])
    assert not store.object_gets


def test_duplicate_marker_property_rejected():
    store = Store(OBJECTS)
    store.data["asset/_SUCCESS.json"] = b'{"sha256":"a","sha256":"b"}'
    with pytest.raises(RuntimeError, match="repeats"):
        store.reader().load("s3://rainpulse/asset")


def test_manifest_digest_matches_original_wire_algorithm():
    digest = hashlib.sha256()
    for k, v in sorted(OBJECTS.items()):
        key = k.encode(); digest.update(len(key).to_bytes(4, "big")); digest.update(key)
        digest.update(len(v).to_bytes(8, "big")); digest.update(hashlib.sha256(v).digest())
    assert access.manifest_digest(access.Entry(k, len(v), hashlib.sha256(v).hexdigest())
                                  for k, v in OBJECTS.items()) == digest.hexdigest()
