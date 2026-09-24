# ruff: noqa: E501, E701, E702, I001, E402
import copy
import io
import json
from zipfile import ZipFile

import numpy as np
import pytest

from conftest import TARGET, network_document, volume
from rainpulse_algo.multiband.adapters import ray_seconds, read_volume
from rainpulse_algo.multiband.cli import LocalReader, logical_digest, replay
from rainpulse_algo.multiband.codec import decode_arrays, decode_volume, encode_arrays, encode_volume
from rainpulse_algo.multiband.managed import Executor, VolumeCache
from rainpulse_algo.multiband.model import Network
from rainpulse_algo.multiband.quality import accept_s_qc, x_qc
from rainpulse_algo.multiband.fusion import build_composite

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
    request = dict(event_type='ops.multiband.requested.v1', occurred_at=CUTOFF, payload=dict(mode='sx_composite', network_sha256=network.sha256, product_id='local', analysis_time=TARGET, input_cutoff=CUTOFF, sources=sources))
    return config, request, index, bundles


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
    assert len(first)==4 and first['cr.png'].startswith(b'\x89PNG')
    arrays = decode_arrays(first['arrays.npz'],maximum_bytes=10*1024**2)
    assert arrays['CR_DBZH'].item()==35
    assert m2['decoded_cache_hits']==2
    assert m2['decoded_cache_bytes'] <= e.network.cache_max_bytes


def test_executor_standalone_x_writes_polar_qc(tmp_path):
    config, req, index, _ = setup_inputs(tmp_path)
    req['payload'].update(mode='x_qc',sources=req['payload']['sources'][1:])
    objects, _, _ = Executor(config).execute(req,LocalReader(tmp_path,index,512*1024**2),artifact_digest=logical_digest)
    assert len(objects)==6
    v = decode_volume({'volume.json':objects['native_volume.json'],'arrays.npz':objects['native_arrays.npz']},maximum_bytes=10*1024**2,asset_sha256='a'*64)
    assert not v.metadata['operational_eligible']
    assert not v.sweeps[0].fields['QPE_ELIGIBLE_MASK'].any()


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


def test_local_reader_does_not_follow_external_paths(tmp_path):
    with pytest.raises(ValueError): LocalReader(tmp_path,{'s3://b/a':'../escape'},1000).load('s3://b/a')


def test_unverified_geometry_and_geographic_grid_refused():
    d = network_document(); d['stations']['x1']['geometry_verified']=False
    with pytest.raises(ValueError): Network.from_bytes(json.dumps(d).encode())
    d = network_document(); d['products']['local']['crs']='EPSG:4326'
    with pytest.raises(ValueError): Network.from_bytes(json.dumps(d).encode())


def test_altitude_datum_required(net):
    v = volume(net.stations['x1']); v.metadata['height_datum']='ellipsoid'
    with pytest.raises(ValueError): x_qc(v,net.stations['x1'],net.sha256)


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
