"""Deterministic in-memory I/O accounting; not a server speed or RSS benchmark."""
import json
from core_modules import access
from fixtures import Store


def probe():
    objects={"qc/summary.json":b'{"operational_eligible":false}',
             **{f"arrays/field{i:02d}/0":bytes([i])*65536 for i in range(24)}}
    baseline=Store(objects);reader=baseline.reader(retained=0)
    assert reader.load("s3://rainpulse/asset")==objects
    assert reader.load("s3://rainpulse/asset")==objects
    full_gets=len(baseline.object_gets)
    warmed=Store(objects);reader=warmed.reader(retained=4*1024**2)
    assert reader.load("s3://rainpulse/asset")==objects
    before=len(warmed.object_gets)
    assert reader.load("s3://rainpulse/asset")==objects
    warm_gets=len(warmed.object_gets)-before
    selected=Store(objects);reader=selected.reader(retained=0)
    assert reader.load_selected("s3://rainpulse/asset",keys=["qc/summary.json"])=={"qc/summary.json":objects["qc/summary.json"]}
    packed=Store(objects,packed=True,pack_size=8*1024**2);reader=packed.reader(retained=0)
    assert reader.load_selected("s3://rainpulse/asset",keys=["qc/summary.json"])=={"qc/summary.json":objects["qc/summary.json"]}
    return {"kind":"synthetic_in_memory_object_IO_not_wallclock_benchmark",
        "input_objects":len(objects),"input_bytes":sum(map(len,objects.values())),
        "two_full_reads_cache_disabled_object_gets":full_gets,
        "second_full_read_cache_enabled_object_gets":warm_gets,
        "markers_still_fetched_on_each_read":sum(k.endswith('/_SUCCESS.json') for _,k in warmed.calls),
        "schema2_summary_only_object_gets":len(selected.object_gets),
        "schema2_summary_only_download_bytes":sum(len(selected.data[k]) for k in selected.object_gets),
        "schema3_summary_only_requires_full_verification":reader.last_session.stats["packed_full_fallback"]==1,
        "full_and_selected_bytes_equal":True,"operational_eligible_changed":False,
        "real_radar_replay_performed":False,"server_performance_measured":False}


if __name__=="__main__":print(json.dumps(probe(),indent=2))
