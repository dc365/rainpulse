# ruff: noqa: E501, I001
from copy import deepcopy
from dataclasses import replace
import numpy as np
import pytest
from rainpulse_algo.multiband.quality import x_qc, accept_s_qc
from rainpulse_algo.multiband.fusion import build_composite
from rainpulse_algo.multiband.stream_fusion import build_composite_streaming
from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.model import Volume
from .helpers import station, volume, network, STAMP, SHA

def prepared():
    x, s = (station(), station('S'))
    vx, vs = (volume(x), volume(s))
    vs.sweeps[0].elevation_deg[:] = 20
    xq, sq = (x_qc(vx, x, SHA), accept_s_qc(vs, s, SHA))
    second = deepcopy(xq.sweeps[0])
    second.number = 1
    second.path_quality = {**second.path_quality, 'extra_diagnostic': 'cut-1'}
    xq.sweeps.append(second)
    return (x, s, [sq, xq])

def cuts(volumes):
    for v in sorted(volumes, key=lambda v: v.metadata['radar_id']):
        for s in v.sweeps:
            yield Volume(v.metadata, [s])

@pytest.mark.parametrize('memory', [0, 80, 16 * 1024 * 1024])
@pytest.mark.parametrize('comparison', [False, True])
def test_stream_matches_eager_with_spill_and_comparisons(tmp_path, memory, comparison):
    x, s, vs = prepared()
    net = network(x, s, height=3, tile_rows=1, levels=(150.0, 1000.0))
    eager = build_composite(vs, net, 'sx', STAMP, STAMP)
    stats = {}
    stream = build_composite_streaming(cuts(vs), net, 'sx', STAMP, STAMP, options=ExecutionOptions(streaming=True, layer_memory_bytes=memory), directory=tmp_path, metrics=stats, comparison=comparison)
    for name in eager.arrays:
        np.testing.assert_array_equal(eager.arrays[name], stream.arrays[name], err_msg=name)
    assert eager.metadata == stream.metadata
    assert not list(tmp_path.iterdir())
    assert stats['layer_cleanup_complete'] == 1
    if comparison:
        for band in ('S', 'X'):
            expected = build_composite([v for v in vs if v.metadata['band'] == band], net, 'sx', STAMP, STAMP)
            actual = stream.band_comparisons[band]
            for name in expected.arrays:
                np.testing.assert_array_equal(expected.arrays[name], actual.arrays[name])

def test_last_bad_cut_cleans_scratch_and_never_returns_partial_result(tmp_path):
    x, s, vs = prepared()
    net = network(x, s, height=3, tile_rows=1)

    def broken():
        yield from cuts(vs)
        raise RuntimeError('last input corrupted')
    with pytest.raises(RuntimeError, match='last input'):
        build_composite_streaming(broken(), net, 'sx', STAMP, STAMP, options=ExecutionOptions(streaming=True, layer_memory_bytes=0), directory=tmp_path)
    assert not list(tmp_path.iterdir())

def test_admission_memory_is_included_in_cut_budget(tmp_path):
    x = station()
    v = x_qc(volume(x), x, SHA)
    options = ExecutionOptions(streaming=True, maximum_cut_bytes=1, maximum_qc_cut_bytes=v.nbytes)
    with pytest.raises(ValueError, match='admission fields'):
        build_composite_streaming(cuts([v]), network(x), 'sx', STAMP, STAMP, options=options, directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_numba_selection_retains_v2_admission(tmp_path):
    pytest.importorskip('numba')
    x, s, vs = prepared()
    net = network(x, s, height=3, tile_rows=1, levels=(150.0, 1000.0))
    expected = build_composite(vs, net, 'sx', STAMP, STAMP)
    result = build_composite_streaming(cuts(vs), net, 'sx', STAMP, STAMP,
        options=ExecutionOptions(streaming=True, selection_backend='numba', layer_memory_bytes=0),
        directory=tmp_path)
    for key in expected.arrays:
        np.testing.assert_array_equal(expected.arrays[key], result.arrays[key])
