# ruff: noqa: E501, I001
"""Uses the current byte-verified fusion/stream source with synthetic native input."""
import copy
import os
import sys
import zipfile
from pathlib import Path
import numpy as np
import pytest
from pyproj import Transformer
from rainpulse_algo.multiband import fusion
from rainpulse_algo.multiband.model import Grid, Network, Station, Sweep, Volume, epoch
from rainpulse_algo.multiband.stream_fusion import build_composite_streaming
from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.fusion_audit import paired_diagnostic

def scene(method='quality_height', tiles=4):
    t = epoch('2026-08-28T00:06:00Z')
    stations = {}
    volumes = []
    rng = np.random.default_rng(37)
    for sid, band, q in [('s', 'S', 0.6), ('x', 'X', 0.8)]:
        st = Station(sid, band, 'native_bundle', frequency_hz=3000000000.0 if band == 'S' else 9400000000.0, longitude_deg=120.0, latitude_deg=26.0, altitude_m_msl=0.0, beam_width_h_deg=2.0, beam_width_v_deg=2.0, enabled=True, geometry_verified=True, calibration_verified=True, calibration_id='cal', maximum_age_seconds=600)
        stations[sid] = st
        cuts = []
        for n in range(3):
            z = rng.uniform(5, 55, (180, 100)).astype('float32')
            obs = (rng.random(z.shape) > 0.1).astype('uint8')
            no = ((rng.random(z.shape) < 0.08) & (obs == 1)).astype('uint8')
            z[(obs == 0) | (no == 1)] = np.nan
            eligible = (obs & (rng.random(z.shape) > 0.1)).astype('uint8')
            f = dict(DBZH=z, DBZH_QC=z.copy(), OBSERVED_MASK=obs, NO_ECHO_MASK=no, REFLECTIVITY_ELIGIBLE_FOR_CR=eligible, QUALITY_SCORE=np.full(z.shape, q, 'float32'), CR_UNCERTAIN_MASK=obs & 1 - eligible, PATH_VALID_MASK=obs.copy(), PATH_STATE=np.where(obs, 2, 0).astype('uint8'), RADOME_QUALIFIED_MASK=obs.copy())
            cuts.append(Sweep(n, np.arange(180, dtype=float) * 2, np.arange(100) * 500.0 + 250, np.full(180, 0.5 + n), np.full(180, t - 60), f))
        meta = dict(radar_id=sid, band=band, scan_id='scan-' + sid, frequency_hz=st.frequency_hz, longitude_deg=120.0, latitude_deg=26.0, altitude_m_msl=0.0, height_datum='MSL', volume_start='2026-08-28T00:04:00Z', volume_end='2026-08-28T00:05:00Z', available_at='2026-08-28T00:05:10Z', asset_sha256='a' * 64, scan_type='volume', network_sha256='b' * 64, calibration_id='cal')
        volumes.append(Volume(meta, cuts))
    tx = Transformer.from_crs(4326, 32651, always_xy=True)
    x, y = tx.transform(120.0, 26.0)
    grid = Grid('g', 'EPSG:32651', x - 12000, y - 10000, 1000.0, 24, 20, (100.0, 500.0, 1000.0, 2000.0), tiles, 360, method)
    return (volumes, Network('rel', stations, {'p': grid}, 'b' * 64))

def parent_module():
    path = Path(os.environ.get('SX_BASELINE_ZIP', '/mnt/data/sx_finish_work/baseline.zip'))
    if path.exists():
        with zipfile.ZipFile(path) as archive:
            text = archive.read('algorithms/rainpulse_algo/multiband/fusion.py').decode()
    else:
        text = (Path(__file__).parent / 'fixtures/fusion_parent.py').read_text()
    import hashlib
    encoded = text.encode()
    actual = hashlib.sha1(b'blob ' + str(len(encoded)).encode() + b'\x00' + encoded).hexdigest()
    assert actual == '9c1deedc196200baf5234aeb09d2cc7705900356', 'frozen numerical parent was modified'
    import types
    mod = types.ModuleType('rainpulse_algo.multiband._sx_frozen_fusion')
    mod.__package__ = 'rainpulse_algo.multiband'
    sys.modules[mod.__name__] = mod
    exec(compile(text, str(path) + '!/fusion.py', 'exec'), mod.__dict__)
    return mod

def assert_same(a, b):
    assert a.arrays.keys() == b.arrays.keys()
    for k in a.arrays:
        assert a.arrays[k].dtype == b.arrays[k].dtype, k
        np.testing.assert_array_equal(a.arrays[k], b.arrays[k], err_msg=k)
    def selection_metadata(result):
        metadata = copy.deepcopy(result.metadata)
        metadata.pop('fusion_audit', None)
        # Integration adds truthful stored QC declarations. They are provenance,
        # not a selection input; every array and all other metadata remain exact.
        for source in metadata.get('sources', []):
            source.pop('qc_version', None)
            source.pop('qc_identity', None)
        return metadata
    assert selection_metadata(a) == selection_metadata(b)

@pytest.mark.parametrize('method', ['quality_height', 'quality_height_v2'])
@pytest.mark.parametrize('tiles', [1, 4, 20])
def test_eager_parent_all_fields_and_originals(method, tiles):
    v, net = scene(method, tiles)
    old = copy.deepcopy(v)
    expected = parent_module().build_composite(copy.deepcopy(v), net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z')
    actual = fusion.build_composite(v, net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z')
    assert_same(expected, actual)
    for a, b in zip(v, old, strict=True):
        for x, y in zip(a.sweeps, b.sweeps, strict=True):
            for k in x.fields:
                np.testing.assert_array_equal(x.fields[k], y.fields[k])
    report = actual.metadata['fusion_audit']
    assert len(report['sources']) == 6
    for s in report['sources']:
        assert sum(s['native'].values()) == 18000
        assert s['projected_total'] == 24 * 20 * 4

@pytest.mark.parametrize('method', ['quality_height', 'quality_height_v2'])
@pytest.mark.parametrize('capacity', [0, 1000000])
def test_stream_eager_and_sampled_pairs(tmp_path, method, capacity):
    v, net = scene(method)
    eager = fusion.build_composite(copy.deepcopy(v), net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z')
    pieces = [Volume(dict(a.metadata), [s]) for a in v for s in a.sweeps]
    result = build_composite_streaming(iter(pieces), net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z', options=ExecutionOptions(streaming=True, layer_memory_bytes=capacity), directory=tmp_path, comparison=True)
    assert_same(eager, result)
    assert eager.metadata['fusion_audit'] == result.metadata['fusion_audit']
    for band in ('S', 'X'):
        component = fusion.build_composite([copy.deepcopy(a) for a in v if a.metadata['band'] == band], net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z')
        assert_same(component, result.band_comparisons[band])
    r = paired_diagnostic(result.band_comparisons['S'].audit_samples, result.band_comparisons['X'].audit_samples)
    assert sum(r['primary_partition'].values()) == r['sample_count']
    assert r['sample_count'] <= 4096
    assert not list(tmp_path.iterdir())

def test_last_source_failure_no_result_and_cleanup(tmp_path):
    v, net = scene()

    def source():
        yield Volume(v[0].metadata, [v[0].sweeps[0]])
        raise RuntimeError('last cut bad checksum')
    with pytest.raises(RuntimeError):
        build_composite_streaming(source(), net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z', options=ExecutionOptions(streaming=True, layer_memory_bytes=0), directory=tmp_path, comparison=True)
    assert not list(tmp_path.iterdir())

def test_unqualified_x_cannot_replace_qualified_s_echo():
    v, net = scene('quality_height_v2')
    base = fusion.build_composite([copy.deepcopy(v[0])], net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z')
    for cut in v[1].sweeps:
        cut.fields['PATH_VALID_MASK'][:] = 0
        cut.fields['PATH_STATE'][:] = 0
    result = fusion.build_composite(v, net, 'p', '2026-08-28T00:06:00Z', '2026-08-28T00:06:00Z')
    finite = np.isfinite(base.arrays['CR_DBZH'])
    for key in ('CR_DBZH', 'WINNER_SOURCE', 'WINNER_RAY', 'WINNER_GATE'):
        np.testing.assert_array_equal(base.arrays[key][finite], result.arrays[key][finite])
