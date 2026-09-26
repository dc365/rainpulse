# ruff: noqa: E501, E701, E702, I001, E402
import copy
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from zipfile import ZipFile

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from conftest import TARGET, network_document, volume
from rainpulse_algo.multiband.adapters import ray_seconds, read_volume, read_x_qc_sweep
from rainpulse_algo.multiband.cli import LocalReader, logical_digest, replay
from rainpulse_algo.multiband.codec import decode_arrays, decode_volume, encode_arrays, encode_volume
from rainpulse_algo.multiband.managed import Executor, VolumeCache, _validated_sweep_numbers
from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.model import MAX_SWEEPS, Network
from rainpulse_algo.multiband.quality import accept_s_qc, x_qc
from rainpulse_algo.multiband.fusion import Composite, build_composite
from rainpulse_algo.multiband.product import sx_comparison_objects

CUTOFF = '2026-09-23T00:11:00Z'


def setup_inputs(tmp_path, *, cache=True):
    document = network_document()
    document['cache_max_bytes'] = 10*1024**2 if cache else 0
    config = tmp_path / 'network.json'
    config.write_text(json.dumps(document))
    network = Network.load(config)
    sources, index, bundles = [], {}, {}
    for id in ('s1', 'x1'):
        v = volume(network.stations[id], age=120 if id=='s1' else 0)
        uri = 's3://rainpulse/fixture/'+id
        objects = encode_volume(v)
        directory = tmp_path / id
        directory.mkdir()
        for key, data in objects.items():
            (directory / key).write_bytes(data)
        index[uri] = id
        bundles[uri] = objects
        sources.append(dict(radar_id=id, scan_id=v.metadata['scan_id'], input_uri=uri, **{k: v.metadata[k] for k in ('volume_start','volume_end','available_at')}))
    request = dict(event_type='ops.multiband.requested.v1', occurred_at=CUTOFF, payload=dict(mode='sx_composite', network_sha256=network.sha256, execution_sha256=ExecutionOptions().digest, product_id='local', analysis_time=TARGET, input_cutoff=CUTOFF, sources=sources))
    return config, request, index, bundles


@pytest.mark.parametrize("band", ["S", "X"])
def test_sx_preview_labels_a_single_band_result_without_claiming_fusion(band):
    echo_values = np.array([[35]], dtype=np.float32)
    no_values = np.full((1, 1), np.nan, dtype=np.float32)
    metadata = {
        "cadence_seconds": 360,
        "valid_echo_cells": 1,
        "valid_no_echo_cells": 0,
        "uncertain_only_cells": 0,
        "sources": [],
        "skipped": [],
    }
    result_metadata = {**metadata, "sources": [{"band": band}]}
    result = Composite(
        {"CR_DBZH": echo_values, "CR_UNCERTAIN_DBZH": no_values,
         "WINNER_SOURCE": np.array([[0]], dtype=np.int32)},
        result_metadata,
    )
    band_results = {
        current: Composite(
            {"CR_DBZH": echo_values if current == band else no_values},
            metadata if current == band else {**metadata, "valid_echo_cells": 0},
        )
        for current in ("S", "X")
    }
    objects = sx_comparison_objects(result, band_results)

    comparison = json.loads(objects["manifest.json"])["comparison"]
    fused = next(item for item in comparison["products"] if item["product_id"] == "sx_composite")
    assert fused["label"] == f"仅{band}贡献（未形成 S/X 融合）"
    assert fused["contributing_bands"] == [band]
    assert fused["echo_contributing_bands"] == [band]
    absent = "X" if band == "S" else "S"
    assert f"{absent} 波段没有合格贡献" in fused["reason"]
    assert "未形成双波段融合" in fused["reason"]


def test_sx_preview_preserves_valid_no_echo_coverage():
    no_values = np.full((1, 1), np.nan, dtype=np.float32)
    metadata = {
        "cadence_seconds": 360,
        "valid_echo_cells": 0,
        "valid_no_echo_cells": 1,
        "uncertain_only_cells": 0,
        "sources": [],
        "skipped": [],
    }
    s_result = Composite({"CR_DBZH": no_values}, metadata)
    x_result = Composite({"CR_DBZH": no_values}, metadata)
    result = Composite(
        {"CR_DBZH": no_values, "CR_UNCERTAIN_DBZH": no_values}, metadata
    )

    objects = sx_comparison_objects(result, {"S": s_result, "X": x_result})

    comparison = json.loads(objects["manifest.json"])["comparison"]
    fused = next(item for item in comparison["products"] if item["product_id"] == "sx_composite")
    assert fused["status"] == "no_echo"
    assert fused["label"] == "S/X 融合（仅有效无回波像元）"
    assert fused["contributing_bands"] == ["S", "X"]
    assert fused["echo_contributing_bands"] == []
    assert fused["valid_no_echo_cells"] == 1
    assert "最终组合含 1 个有效无回波像元" in fused["reason"]


def test_sx_preview_labels_the_final_fused_no_echo_state_not_single_band_echo():
    no_values = np.full((1, 1), np.nan, dtype=np.float32)
    echo = np.full((1, 1), 25, dtype=np.float32)
    s_metadata = {
        "cadence_seconds": 360,
        "valid_echo_cells": 1,
        "valid_no_echo_cells": 0,
        "uncertain_only_cells": 0,
        "sources": [],
        "skipped": [],
    }
    x_metadata = {**s_metadata, "valid_echo_cells": 0, "valid_no_echo_cells": 1}
    fused_metadata = {
        **s_metadata,
        "valid_echo_cells": 0,
        "valid_no_echo_cells": 1,
        "sources": [{"band": "S"}, {"band": "X"}],
    }
    result = Composite(
        {"CR_DBZH": no_values, "CR_UNCERTAIN_DBZH": no_values,
         "WINNER_SOURCE": np.full((1, 1), -1, dtype=np.int32)},
        fused_metadata,
    )
    objects = sx_comparison_objects(
        result,
        {
            "S": Composite({"CR_DBZH": echo}, s_metadata),
            "X": Composite({"CR_DBZH": no_values}, x_metadata),
        },
    )

    fused = next(
        item for item in json.loads(objects["manifest.json"])["comparison"]["products"]
        if item["product_id"] == "sx_composite"
    )
    assert fused["status"] == "no_echo"
    assert fused["label"] == "S/X 融合（仅有效无回波像元）"
    assert fused["echo_contributing_bands"] == []
    assert "最终组合含 1 个有效无回波像元" in fused["reason"]
    assert "单波段候选均有有效无回波覆盖" not in fused["reason"]


def test_sx_preview_reports_echo_bands_selected_by_final_composite():
    echo = np.full((1, 1), 25, dtype=np.float32)
    no_values = np.full((1, 1), np.nan, dtype=np.float32)
    metadata = {
        "cadence_seconds": 360,
        "valid_echo_cells": 1,
        "valid_no_echo_cells": 0,
        "uncertain_only_cells": 0,
        "sources": [],
        "skipped": [],
    }
    fused_metadata = {
        **metadata,
        "sources": [{"band": "S"}, {"band": "X"}],
    }
    result = Composite(
        {"CR_DBZH": echo, "CR_UNCERTAIN_DBZH": no_values,
         "WINNER_SOURCE": np.array([[1]], dtype=np.int32)},
        fused_metadata,
    )
    objects = sx_comparison_objects(
        result,
        {
            "S": Composite({"CR_DBZH": echo}, metadata),
            "X": Composite({"CR_DBZH": echo}, metadata),
        },
    )

    fused = next(
        item for item in json.loads(objects["manifest.json"])["comparison"]["products"]
        if item["product_id"] == "sx_composite"
    )
    assert fused["label"] == "S/X 融合（最终回波来自 X）"
    assert fused["echo_contributing_bands"] == ["X"]


def test_sx_preview_mixed_echo_and_no_echo_does_not_claim_echo_is_absent():
    values = np.array([[25, np.nan]], dtype=np.float32)
    metadata = {
        "cadence_seconds": 360,
        "valid_echo_cells": 1,
        "valid_no_echo_cells": 1,
        "uncertain_only_cells": 0,
        "sources": [],
        "skipped": [],
    }
    fused_metadata = {
        **metadata,
        "sources": [{"band": "S"}, {"band": "X"}],
    }
    result = Composite(
        {
            "CR_DBZH": values,
            "CR_UNCERTAIN_DBZH": np.full_like(values, np.nan),
            "WINNER_SOURCE": np.array([[1, -1]], dtype=np.int32),
        },
        fused_metadata,
    )
    objects = sx_comparison_objects(
        result,
        {
            "S": Composite({"CR_DBZH": values}, metadata),
            "X": Composite({"CR_DBZH": values}, metadata),
        },
    )

    fused = next(
        item for item in json.loads(objects["manifest.json"])["comparison"]["products"]
        if item["product_id"] == "sx_composite"
    )
    assert fused["status"] == "available"
    assert fused["valid_echo_cells"] == 1
    assert fused["valid_no_echo_cells"] == 1
    assert "但没有有效回波" not in fused["reason"]
    assert "最终组合的有效回波来自 X" in fused["reason"]


def test_canonical_roundtrip_and_determinism(net):
    v = volume(net.stations['x1'])
    objects = encode_volume(v)
    assert encode_volume(v) == objects
    r = decode_volume(objects, maximum_bytes=10*1024**2, asset_sha256=logical_digest(objects))
    r.validate(net.stations['x1'])
    np.testing.assert_equal(v.sweeps[0].fields['DBZH'], r.sweeps[0].fields['DBZH'])
    assert r.metadata['asset_sha256'] != v.metadata['asset_sha256']


def test_npz_does_not_accept_pickle_or_duplicate_or_traversal():
    with pytest.raises(ValueError):
        encode_arrays({'x': np.array([dict(bad=1)], dtype=object)})
    data = io.BytesIO()
    np.save(data, np.array([1.], dtype=np.float32))
    for names in (['../a.npy'], ['a.npy','a.npy']):
        out = io.BytesIO()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)  # deliberately malformed duplicate NPZ
            with ZipFile(out, 'w') as z:
                for name in names:
                    z.writestr(name, data.getvalue())
        with pytest.raises(ValueError):
            decode_arrays(out.getvalue(), maximum_bytes=10000)


def test_npz_budget_and_checksum(net):
    raw = encode_arrays({'test': np.zeros(10000, np.float64)})
    with pytest.raises(ValueError):
        decode_arrays(raw, maximum_bytes=1000)
    objects = encode_volume(volume(net.stations['x1']))
    objects['arrays.npz'] += b'changed'
    with pytest.raises(ValueError):
        decode_volume(objects, maximum_bytes=10*1024**2, asset_sha256='a'*64)


def test_ray_time_units():
    assert ray_seconds(np.array([1000]), 'milliseconds since 1970-01-01T00:00:00Z')[0] == 1
    assert ray_seconds(np.array(['1970-01-01T00:00:01'], dtype='datetime64[s]'), None)[0] == 1
    with pytest.raises(ValueError):
        ray_seconds(np.array([1]), 'minutes since yesterday')
    with pytest.raises(ValueError):
        ray_seconds(np.array(['NaT'], dtype='datetime64[s]'), None)


def test_native_identity_is_not_overridden_by_catalog(net):
    v = volume(net.stations['x1']); objects = encode_volume(v)
    source = {k:v.metadata[k] for k in ('scan_id','available_at','volume_start','volume_end')}
    source['volume_start'] = CUTOFF
    with pytest.raises(ValueError):
        read_volume(objects, net.stations['x1'], source, maximum_bytes=10*1024**2, asset_sha256=logical_digest(objects))


def test_cache_limit_ttl_and_immutable_arrays(net):
    tick = [0.]
    v = volume(net.stations['x1'])
    c = VolumeCache(v.nbytes, 2, clock=lambda:tick[0])
    c.put(('a',),v)
    with pytest.raises(ValueError):
        c.get(('a',)).sweeps[0].fields['DBZH'][0,0] = 99
    c.put(('b',),volume(net.stations['x1']))
    assert c.get(('a',)) is None and c.bytes <= c.maximum
    tick[0] = 3
    assert c.get(('b',)) is None and c.bytes == 0
    off = VolumeCache(0, 2); off.put(('a',),v)
    assert off.get(('a',)) is None


def test_executor_full_numerical_path_and_repeat(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    e = Executor(config)
    reader = LocalReader(tmp_path,index,512*1024**2)
    first, summary, m = e.execute(req,reader,artifact_digest=logical_digest)
    second, _, m2 = e.execute(req,reader,artifact_digest=logical_digest)
    assert first == second and summary['candidate_only'] is True
    assert len(first)==11 and first['cr.png'].startswith(b'\x89PNG')
    assert all(first[f'map/{name}.png'].startswith(b'\x89PNG') for name in ('s_only', 'x_only', 'sx_composite'))
    assert all(p['map']['crs'] == 'EPSG:4326' for p in json.loads(first['manifest.json'])['comparison']['products'] if 'map' in p)
    arrays = decode_arrays(first['arrays.npz'],maximum_bytes=10*1024**2)
    assert arrays['CR_DBZH'].item()==35
    assert arrays['DBZH_X_MINUS_S'].shape == arrays['CR_DBZH'].shape
    comparison = json.loads(first['manifest.json'])['comparison']
    assert comparison['same_grid'] and comparison['cadence_seconds'] == 60
    assert {p['product_id'] for p in comparison['products']} == {'s_only','x_only','sx_composite','x_minus_s'}
    assert m2['decoded_cache_hits']==2
    assert m2['decoded_cache_bytes'] <= e.network.cache_max_bytes


def test_executor_standalone_x_writes_polar_qc(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    req['payload'].update(mode='x_qc',sources=req['payload']['sources'][1:])
    req['payload'].pop('product_id')
    objects, _, _ = Executor(config).execute(req,LocalReader(tmp_path,index,512*1024**2),artifact_digest=logical_digest)
    assert len(objects)==7
    manifest=json.loads(objects['manifest.json'])
    assert manifest['contract']=='rainpulse.multiband.x-qc-preview-v1'
    assert manifest['geometry'].startswith('station-centred polar')
    mapping=manifest['comparison']['sweeps'][0]['map']
    assert mapping['crs']=='EPSG:4326'
    assert all(mapping[k] in objects for k in ('raw','qc','flags'))
    assert manifest['operational_eligible'] is False
    assert 'native_arrays.npz' not in objects


def test_standalone_x_qc_rejects_input_after_frozen_cutoff(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    req['payload'].update(mode='x_qc',sources=req['payload']['sources'][1:])
    req['payload']['sources'][0]['available_at']='2026-09-23T00:12:00Z'
    with pytest.raises(ValueError,match='frozen task cutoff'):
        Executor(config).execute(req,LocalReader(tmp_path,index,512*1024**2),artifact_digest=logical_digest)


def test_x_qc_runs_geometry_free_without_spatial_products(tmp_path):
    import zarr
    from zarr.storage import MemoryStore

    document={
      'schema_version':'1.0','release_id':'x-only-fixture',
      'stations':{'x1':{'band':'X','source':'normalized_zarr','x_qc_enabled':True,
                        'geometry_verified':False,'calibration_verified':False,'calibration_id':'unverified'}},
      'products':{},'maximum_input_bytes':128*1024**2,
    }
    config=tmp_path/'x-only-network.json'; config.write_text(json.dumps(document))
    network=Network.load(config)
    store=MemoryStore(); root=zarr.group(store=store)
    end=datetime.fromisoformat(TARGET.replace('Z','+00:00')).timestamp()
    root.attrs.update(contract_name='rainpulse.normalized-radar-volume',radar_id='x1',radar_band='X',scan_id='x1-40',
                      volume_start_time_utc=datetime.fromtimestamp(end-30,UTC).isoformat(),
                      volume_end_time_utc=datetime.fromtimestamp(end,UTC).isoformat(),scan_type='volume')
    root.create_dataset('sweep_number',data=np.arange(40,dtype=np.int16))
    for number in range(40):
        group=root.create_group(f'sweep_{number:03d}')
        az=np.arange(8,dtype=np.float32)*45
        ranges=np.arange(8,dtype=np.float32)*250+125
        shape=(len(az),len(ranges))
        group.create_dataset('azimuth',data=az)
        group.create_dataset('range',data=ranges)
        group.create_dataset('elevation',data=np.full(len(az),number*.2+0.1,np.float32))
        group.create_dataset('ray_time',data=np.full(len(az),end,np.float64))
        group.create_dataset('DBZH',data=np.full(shape,35,np.float32))
        group.create_dataset('OBSERVED_MASK',data=np.ones(shape,np.uint8))
        group.create_dataset('NO_ECHO_MASK',data=np.zeros(shape,np.uint8))
        group.create_dataset('SNR',data=np.full(shape,20,np.float32))
        group.create_dataset('RHOHV',data=np.full(shape,.99,np.float32))
    objects={key:store[key] for key in store.keys()}
    source={'radar_id':'x1','scan_id':'x1-40','input_uri':'s3://fixture/x1',
            'volume_start':datetime.fromtimestamp(end-30,UTC).isoformat(),
            'volume_end':datetime.fromtimestamp(end,UTC).isoformat(),
            'available_at':datetime.fromtimestamp(end,UTC).isoformat()}
    decoded, _ = read_x_qc_sweep(
        objects, network.stations['x1'], source, 0,
        asset_sha256=logical_digest(objects), maximum_bytes=128*1024**2)
    np.testing.assert_array_equal(decoded.sweeps[0].fields['SNRH'], decoded.sweeps[0].fields['SNR'])

    index=SimpleNamespace(schema='2.0',sha256=logical_digest(objects),
                          logical={key:(key,0,len(value)) for key,value in objects.items()})
    loads=[]
    class Session:
        def __init__(self): self.index=index
        def load(self,*,keys):
            loads.append(tuple(keys))
            return {key:objects[key] for key in keys}
    class Reader:
        def open(self,uri): return Session()

    at=datetime.fromtimestamp(end,UTC).isoformat().replace('+00:00','Z')
    before=datetime.fromtimestamp(end-30,UTC).isoformat().replace('+00:00','Z')
    request={'event_type':'ops.multiband.requested.v1','occurred_at':CUTOFF,
             'payload':{'mode':'x_qc','network_sha256':network.sha256,'execution_sha256':ExecutionOptions().digest,'input_cutoff':CUTOFF,
                        'sources':[{'radar_id':'x1','scan_id':'x1-40','input_uri':'s3://fixture/x1',
                                    'volume_start':before,'volume_end':at,'available_at':at}]}}
    output,summary,metrics=Executor(config).execute(request,Reader(),artifact_digest=logical_digest)
    assert summary['sweeps']==40
    assert len(output)==121
    assert metrics['resident_input_bytes'] < metrics['decoded_input_bytes']
    assert json.loads(output['manifest.json'])['comparison']['sweeps'][-1]['sweep_number']==39
    assert len(loads)==41  # root index once, then one object selection per sweep


def test_network_and_task_schemas_allow_only_geometry_free_x_qc():
    from pathlib import Path
    root=Path(__file__).resolve().parents[3]
    network_schema=json.loads((root/'contracts/internal/multiband/network.schema.json').read_text())
    request_schema=json.loads((root/'contracts/internal/multiband/request.schema.json').read_text())
    example=json.loads((root/'configs/multiband/network.example.json').read_text())
    Draft202012Validator(network_schema).validate(example)
    Network.from_bytes(json.dumps(example).encode())
    document={'schema_version':'1.0','release_id':'x-preview-v1',
              'stations':{'x1':{'band':'X','source':'normalized_zarr','x_qc_enabled':True}},'products':{}}
    Draft202012Validator(network_schema).validate(document)
    fusion={**document,'stations':{'x1':{'band':'X','source':'normalized_zarr','enabled':True,
                                         'geometry_verified':False}}}
    assert list(Draft202012Validator(network_schema).iter_errors(fusion))
    request={'schema_version':'1.0','event_type':'ops.multiband.requested.v1',
             'event_id':'00000000-0000-4000-8000-000000000001',
             'job_id':'00000000-0000-4000-8000-000000000002',
             'run_id':'00000000-0000-4000-8000-000000000003',
             'trace_id':'00000000-0000-4000-8000-000000000004','occurred_at':TARGET,
             'payload':{'mode':'x_qc','network_sha256':'a'*64,'execution_sha256':ExecutionOptions().digest,'analysis_time':TARGET,
                        'input_cutoff':CUTOFF,'sources':[{'radar_id':'x1','scan_id':'x1-scan',
                          'input_uri':'s3://rainpulse/input','volume_start':TARGET,'volume_end':TARGET,
                          'available_at':TARGET}], 'output_prefix':'s3://rainpulse/operations/x/',
                        'analysis_id':'x1-scan'}}
    Draft202012Validator(request_schema).validate(request)
    composite={**request,'payload':{**request['payload'],'mode':'sx_composite'}}
    assert list(Draft202012Validator(request_schema).iter_errors(composite))


def test_executor_config_change_and_duplicate_refused(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    e = Executor(config); reader = LocalReader(tmp_path,index,512*1024**2)
    bad = copy.deepcopy(req); bad['payload']['sources'].append(bad['payload']['sources'][0])
    with pytest.raises(ValueError): e.execute(bad,reader,artifact_digest=logical_digest)
    config.write_text(config.read_text()+' ')
    with pytest.raises(ValueError): e.execute(req,reader,artifact_digest=logical_digest)


def test_executor_cutoff_cannot_claim_future_creation(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    req['occurred_at'] = TARGET
    with pytest.raises(ValueError): Executor(config).execute(req,LocalReader(tmp_path,index,512*1024**2),artifact_digest=logical_digest)


def test_offline_replay_never_overwrites(tmp_path):
    config, request, index, _ = setup_inputs(tmp_path)
    q, idx, out = tmp_path/'request.json',tmp_path/'index.json',tmp_path/'result'
    q.write_text(json.dumps(request)); idx.write_text(json.dumps(index))
    receipt = replay(config,q,tmp_path,idx,out)
    assert receipt['mode']=='offline-reference-not-operational'
    assert (out/'replay-receipt.json').exists()
    with pytest.raises(FileExistsError): replay(config,q,tmp_path,idx,out)


def test_offline_replay_publishes_nested_x_qc_preview_paths(tmp_path):
    config, request, index, _ = setup_inputs(tmp_path)
    request['payload'].update(mode='x_qc', sources=request['payload']['sources'][1:])
    request['payload'].pop('product_id')
    q, idx, out = tmp_path/'x-request.json',tmp_path/'x-index.json',tmp_path/'x-result'
    q.write_text(json.dumps(request)); idx.write_text(json.dumps(index))
    replay(config,q,tmp_path,idx,out)
    assert (out/'sweeps/0/raw.png').is_file()
    assert (out/'sweeps/0/qc.png').is_file()
    assert (out/'sweeps/0/flags.png').is_file()


def test_local_reader_does_not_follow_external_paths(tmp_path):
    with pytest.raises(ValueError): LocalReader(tmp_path,{'s3://b/a':'../escape'},1000).load('s3://b/a')


def test_unverified_geometry_and_geographic_grid_refused():
    d = network_document(); d['stations']['x1']['geometry_verified']=False
    with pytest.raises(ValueError): Network.from_bytes(json.dumps(d).encode())
    d = network_document(); d['products']['local']['crs']='EPSG:4326'
    with pytest.raises(ValueError): Network.from_bytes(json.dumps(d).encode())


def test_altitude_datum_required(net):
    v = volume(net.stations['x1']); v.metadata['height_datum']='ellipsoid'
    # Native station-centred X preview does not use site height or datum.
    assert x_qc(v,net.stations['x1'],net.sha256).metadata['operational_eligible'] is False


def test_uncertain_layer_is_not_column_no_echo(net):
    s = accept_s_qc(volume(net.stations['s1'],noecho=True),net.stations['s1'],net.sha256)
    x = volume(net.stations['x1']); x.metadata['calibration_id']='unknown'
    x = x_qc(x,net.stations['x1'],net.sha256)
    out = build_composite([s,x],net,'local',TARGET,CUTOFF)
    assert out.arrays['NO_ECHO_MASK'].item()==0
    assert out.arrays['UNCERTAIN_MASK'].item()==1


def test_confirmed_nonmet_does_not_show_as_uncertain(net):
    x=volume(net.stations['x1']); x.sweeps[0].fields['CONFIRMED_NONMET_MASK']=np.ones_like(x.sweeps[0].fields['OBSERVED_MASK'])
    x=x_qc(x,net.stations['x1'],net.sha256)
    out=build_composite([x],net,'local',TARGET,CUTOFF)
    assert not np.isfinite(out.arrays['CR_DBZH']).any()
    assert not np.isfinite(out.arrays['CR_UNCERTAIN_DBZH']).any()


def test_selected_zarr_fields_do_not_load_large_diagnostics():
    from rainpulse_algo.multiband.managed import selected_source_keys
    keys=['.zattrs','.zgroup','.zmetadata','sweep_number/.zarray','sweep_number/0','sweep_000/.zgroup','sweep_000/.zattrs',
          'sweep_000/DBZH_QC/.zarray','sweep_000/DBZH_QC/0.0','sweep_000/CF_CR_WITHHELD_MASK/0.0','sweep_000/azimuth/.zarray',
          'sweep_000/EXPERIMENT_HUGE/0.0','qc/summary.json']
    selected=selected_source_keys(keys,'s_qc_zarr')
    assert 'sweep_000/DBZH_QC/0.0' in selected
    assert 'sweep_000/CF_CR_WITHHELD_MASK/0.0' in selected
    assert 'qc/summary.json' not in selected and 'sweep_000/EXPERIMENT_HUGE/0.0' not in selected


def test_decoded_hit_reads_marker_not_data_again(tmp_path):
    from types import SimpleNamespace
    config,req,index,bundles=setup_inputs(tmp_path)
    class Session:
        def __init__(self,objects):
            self.objects=objects
            self.index=SimpleNamespace(sha256=logical_digest(objects),logical=dict.fromkeys(objects))
        def load(self,keys):
            transport.loads+=1
            return {key:self.objects[key] for key in keys}
    class Reader:
        loads=0; markers=0
        def open(self,uri):
            self.markers+=1
            return Session(bundles[uri])
    transport=Reader();e=Executor(config)
    a,_,_=e.execute(req,transport,artifact_digest=logical_digest)
    b,_,_=e.execute(req,transport,artifact_digest=logical_digest)
    assert a==b and transport.loads==2 and transport.markers==4


def test_identical_bytes_at_different_uri_preserve_source_ledger(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    executor = Executor(config)
    reader = LocalReader(tmp_path, index, 512*1024**2)
    executor.execute(req, reader, artifact_digest=logical_digest)
    altered = copy.deepcopy(req)
    source = altered['payload']['sources'][0]
    old_uri = source['input_uri']
    source['input_uri'] += '-copy'
    reader.index[source['input_uri']] = reader.index[old_uri]
    objects, _, _ = executor.execute(altered, reader, artifact_digest=logical_digest)
    manifest = json.loads(objects['manifest.json'])
    assert source['input_uri'] in json.dumps(manifest)
    assert executor.cache.hits == 1  # only the unchanged X source is reused


def test_local_manifest_read_is_bounded(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    request = tmp_path / 'oversized.json'
    request.write_bytes(b' '*(1024**2+2))
    idx = tmp_path/'index.json'
    idx.write_text(json.dumps(index))
    with pytest.raises(ValueError, match='oversized'):
        replay(config, request, tmp_path, idx, tmp_path/'output')


def test_x_qc_sweep_index_is_bounded_and_identity_checked():
    assert _validated_sweep_numbers(np.array([0, 2, 1], dtype=np.int16)) == [0, 1, 2]
    with pytest.raises(ValueError, match='duplicate or invalid'):
        _validated_sweep_numbers(np.array([0, 0], dtype=np.int16))
    with pytest.raises(ValueError, match='invalid or oversized'):
        _validated_sweep_numbers(np.array([1.5], dtype=np.float32))


def test_x_qc_rejects_huge_mismatched_fill_array_before_materializing():
    import zarr
    from zarr.storage import MemoryStore

    document={'schema_version':'1.0','release_id':'x-shape-v1',
              'stations':{'x1':{'band':'X','source':'normalized_zarr','x_qc_enabled':True}},
              'products':{}}
    station = Network.from_bytes(json.dumps(document).encode()).stations['x1']
    store=MemoryStore(); root=zarr.group(store=store)
    end=datetime.fromisoformat(TARGET.replace('Z','+00:00')).timestamp()
    start=datetime.fromtimestamp(end-30,UTC).isoformat()
    finish=datetime.fromtimestamp(end,UTC).isoformat()
    root.attrs.update(contract_name='rainpulse.normalized-radar-volume',radar_id='x1',radar_band='X',scan_id='x-shape',
                      volume_start_time_utc=start,volume_end_time_utc=finish)
    root.create_dataset('sweep_number',data=np.array([0],np.int16))
    group=root.create_group('sweep_000')
    shape=(4,3)
    group.create_dataset('DBZH',data=np.ones(shape,np.float32))
    group.create_dataset('azimuth',data=np.arange(4,dtype=np.float32)*90)
    group.create_dataset('elevation',data=np.ones(4,np.float32))
    group.create_dataset('ray_time',data=np.full(4,end,np.float64))
    group.create_dataset('range',data=np.arange(3,dtype=np.float32)*250+125)
    group.create_dataset('RHOHV',shape=(400_000_000,),chunks=(4096,),dtype='u1',fill_value=255)
    objects={key:store[key] for key in store.keys()}
    source={'radar_id':'x1','scan_id':'x-shape','volume_start':start,'volume_end':finish,
            'available_at':finish}
    wrong_band_store=MemoryStore(); wrong_band_store.update(objects)
    wrong_band_root=zarr.open_group(store=wrong_band_store,mode='a')
    wrong_band_root.attrs['radar_band']='S'
    wrong_band_objects={key:wrong_band_store[key] for key in wrong_band_store.keys()}
    with pytest.raises(ValueError,match='radar band'):
        read_x_qc_sweep(wrong_band_objects,station,source,0,asset_sha256=logical_digest(wrong_band_objects),
                        maximum_bytes=512*1024**2)
    with pytest.raises(ValueError,match='shape differs'):
        read_x_qc_sweep(objects,station,source,0,asset_sha256=logical_digest(objects),
                        maximum_bytes=512*1024**2)

    class HugeSparseIndex:
        shape = (MAX_SWEEPS + 1,)
        dtype = np.dtype('uint8')
        def __getitem__(self, _):
            raise AssertionError('oversized sweep index must be rejected before materialization')

    with pytest.raises(ValueError, match='invalid or oversized'):
        _validated_sweep_numbers(HugeSparseIndex())
