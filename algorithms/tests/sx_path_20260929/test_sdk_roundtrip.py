# ruff: noqa: E501, I001
"""Real Zarr adapter integration; never replace the SDK with a numeric mock."""
import pytest
zarr = pytest.importorskip('zarr', reason='real Zarr 2 SDK required for adapter roundtrip')
import numpy as np
from zarr.storage import MemoryStore
from rainpulse_algo.multiband.adapters import from_group, read_x_qc_sweep
from rainpulse_algo.multiband.attenuation import PATH_INPUT_FIELDS, PATH_PROVENANCE_KEYS
from rainpulse_algo.multiband.quality import x_qc
from .helpers import volume, station, SHA

def fixture():
    s = station(source='normalized_zarr')
    v = volume(s)
    store = MemoryStore()
    root = zarr.group(store=store)
    attrs = {**v.metadata, 'contract_name': 'rainpulse.normalized-radar-volume', 'radar_band': 'X', 'site_longitude_deg': s.longitude_deg, 'site_latitude_deg': s.latitude_deg, 'volume_start_time_utc': v.metadata['volume_start'], 'volume_end_time_utc': v.metadata['volume_end']}
    attrs.update(clear_path_evidence_sha256='c' * 64, path_anchor_evidence_sha256='d' * 64, negligible_attenuation_evidence_sha256='e' * 64)
    root.attrs.update(attrs)
    root.create_dataset('sweep_number', data=np.array([0], 'int32'))
    g = root.create_group('sweep_000')
    cut = v.sweeps[0]
    for key, values in cut.fields.items():
        g.create_dataset(key, data=values)
    for key in PATH_INPUT_FIELDS:
        if key not in g:
            g.create_dataset(key, data=np.zeros(cut.fields['DBZH'].shape, 'float32' if key.endswith('DB') else 'uint8'))
    for key, values in [('azimuth', cut.azimuth_deg), ('range', cut.range_m), ('elevation', cut.elevation_deg), ('ray_time', cut.ray_time_epoch)]:
        g.create_dataset(key, data=values)
    source = {k: v.metadata[k] for k in ('scan_id', 'volume_start', 'volume_end', 'available_at')}
    return (s, v, root, store, source)

@pytest.mark.parametrize('streaming', [False, True])
def test_actual_sdk_roundtrip_preserves_path_inputs(streaming):
    s, v, root, store, source = fixture()
    if streaming:
        got, _ = read_x_qc_sweep(dict(store), s, source, 0, asset_sha256=SHA, maximum_bytes=10 ** 7)
    else:
        got = from_group(root, s, source, asset_sha256=SHA, maximum_bytes=10 ** 7)
    for key in PATH_INPUT_FIELDS:
        assert key in got.sweeps[0].fields
    for key in PATH_PROVENANCE_KEYS:
        assert got.metadata[key] == root.attrs[key]
    out = x_qc(got, s, SHA)
    assert out.sweeps[0].fields['PATH_VALID_MASK'].any()
    np.testing.assert_array_equal(got.sweeps[0].fields['DBZH'], v.sweeps[0].fields['DBZH'])
