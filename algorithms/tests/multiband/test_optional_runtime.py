# ruff: noqa: E501, E701, E702, I001, E402
"""Real SDK adapter parity in the full repository; no dependency stand-ins."""
from dataclasses import replace

import numpy as np
import pytest

zarr = pytest.importorskip('zarr', reason='requires repository-locked Zarr dependency')
pytest.importorskip('minio', reason='requires real object-store SDK')
pytest.importorskip('nats', reason='requires real Worker SDK dependencies')
from zarr.storage import MemoryStore
from rainpulse_algo.worker.object_store import artifact_sha256
from rainpulse_algo.multiband.cli import logical_digest
from rainpulse_algo.multiband.adapters import from_group
from conftest import volume


def test_logical_hash_matches_actual_worker_hash():
    value={'a':b'one','path/b':b'two\0'}
    assert logical_digest(value)==artifact_sha256(value)


def test_actual_normalized_zarr_adapter_preserves_coordinates(net):
    station=replace(net.stations['x1'],source='normalized_zarr')
    v=volume(station);s=v.sweeps[0]
    store=MemoryStore();root=zarr.group(store=store,overwrite=True)
    root.attrs.update(contract_name='rainpulse.normalized-radar-volume',radar_id=station.radar_id,radar_band=station.band,scan_id=v.metadata['scan_id'],calibration_id=station.calibration_id,attenuation_status='corrected',scan_type='volume')
    root.create_dataset('sweep_number',data=np.array([0],np.int32))
    g=root.create_group('sweep_000')
    for name,data in [('azimuth',s.azimuth_deg),('range',s.range_m),('elevation',s.elevation_deg),('ray_time',s.ray_time_epoch),*s.fields.items()]:
        g.create_dataset(name,data=data)
    source={k:v.metadata[k] for k in ('scan_id','volume_start','volume_end','available_at')}
    converted=from_group(root,station,source,asset_sha256='a'*64,maximum_bytes=20*1024**2)
    converted.validate(station)
    np.testing.assert_equal(converted.sweeps[0].ray_time_epoch,s.ray_time_epoch)
    np.testing.assert_equal(converted.sweeps[0].fields['DBZH'],s.fields['DBZH'])
