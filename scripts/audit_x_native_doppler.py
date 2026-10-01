#!/usr/bin/env python3
"""Read a completed normal task JSON on stdin; emit source-bound diagnostics.

Run with the compute worker's object-store environment and installed package.
Does not write product storage, modify network settings, or publish evidence.
"""
import hashlib
import io
import json
import os
import sys

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.multiband.adapters import read_x_qc_sweep
from rainpulse_algo.multiband.model import Network
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
from rainpulse_algo.multiband.xqc_v2.native_doppler_diagnostic import summarize
from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment


def main():
    task = json.load(sys.stdin)
    asset = task['attempts'][-1]['result']['asset']
    source = task['spec']['request']['payload']['sources'][0]
    sha = task['spec']['inputs'][0]['sha256']
    reader = ArtifactObjectReader(minio_client_from_environment(), max_workers=2)
    result = reader.open(asset['uri'], expected_sha256=asset['sha256'])
    manifest = json.loads(result.load(keys=['manifest.json'])['manifest.json'])
    objects = reader.load(source['input_uri'], expected_sha256=sha)
    store = MemoryStore(); store.update(objects)
    root = zarr.open_group(store=store, mode='r')
    net = Network.load(os.environ['RAINPULSE_MULTIBAND_CONFIG'])
    station = net.stations[source['radar_id']]
    cfg = XQCConfig.model_validate(station.x_qc.enhancement)
    cuts = []
    for entry in manifest['comparison']['sweeps']:
        number = entry['sweep_number']; descriptor = entry['xqc_v2']['native']
        key = descriptor['object_path']; payload = result.load(keys=[key])[key]
        if hashlib.sha256(payload).hexdigest() != descriptor['sha256']:
            raise ValueError('native product checksum differs')
        native = np.load(io.BytesIO(payload), allow_pickle=False)
        volume, _ = read_x_qc_sweep(objects, station, source, number,
                                   asset_sha256=sha, maximum_bytes=net.maximum_input_bytes)
        cut = volume.sweeps[0]
        for name, actual in [('DBZH_RAW', cut.fields['DBZH']),
                             ('azimuth', cut.azimuth_deg), ('range_m', cut.range_m),
                             ('elevation', cut.elevation_deg),
                             ('ray_time_epoch', cut.ray_time_epoch)]:
            np.testing.assert_array_equal(native[name], actual)
        sampling = root[f'sweep_{number:03d}'].attrs.get('native_cut_sampling')
        nyquist = root[f'sweep_{number:03d}'].attrs.get('nyquist_velocity_m_s')
        selections = {
            'all_raw': None,
            'visible_qc': np.isfinite(native['DBZH_QC']) & (native['DBZH_QC'] >= 5),
            'published_source': native['XQC_SOURCE_KIND'] != 0,
        }
        cuts.append({'sweep_number': number, 'native_sha256': descriptor['sha256'],
                     'sampling': sampling,
                     'statistics': {name: summarize(cut, cfg, declared_nyquist_mps=nyquist,
                                                    gate_selection=selection)
                                    for name, selection in selections.items()}})
    print(json.dumps({'task_id': task['id'], 'input_sha256': sha,
                      'result_sha256': asset['sha256'], 'no_publication': True,
                      'source_labels_are_independent_weather_truth': False,
                      'cuts': cuts}), flush=True)


if __name__ == '__main__':
    main()
