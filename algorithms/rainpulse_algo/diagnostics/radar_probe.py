"""Small content-bound numeric tiles for server-side radar point queries.

Coordinates are image pixels (north-up), values are sampled native scalars,
never decoded colours. NaN stays missing; valid/no-echo/quality are explicit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import zlib

import numpy as np

TILE = 64


def attach_probe(objects, prefix, fields, *, image_path, identity):
    names = list(fields)
    shape = np.shape(fields[names[0]])
    if len(shape) != 2 or max(shape) > 4096 or len(names) > 16:
        raise ValueError("invalid probe index dimensions")
    if any(np.shape(value) != shape for value in fields.values()):
        raise ValueError("probe field shapes differ")
    height, width = shape
    records = np.stack([np.asarray(fields[name], dtype='<f8') for name in names], axis=-1)
    tiles = {}
    for row in range(0, height, TILE):
        for col in range(0, width, TILE):
            values = records[row:row+TILE, col:col+TILE]
            raw = values.tobytes(order='C')
            content = json.dumps({'encoding': 'zlib-f64le', 'width': values.shape[1],
                                  'height': values.shape[0], 'fields': names,
                                  'data': base64.b64encode(zlib.compress(raw, 3)).decode()},
                                 separators=(',', ':')).encode()
            path = f'{prefix}/{row//TILE}_{col//TILE}.json'
            objects[path] = content
            tiles[f'{row//TILE}_{col//TILE}'] = {'path': path, 'sha256': hashlib.sha256(content).hexdigest()}
    return {'contract': 'rainpulse.radar-probe-v1', 'width': width, 'height': height,
            'tile_size': TILE, 'fields': names, 'row_order': 'north_to_south',
            'image_sha256': hashlib.sha256(objects[image_path]).hexdigest(),
            'identity': identity, 'tiles': tiles}


def attach_bound_polar_probe(objects, layer, arrays, valid, image_size):
    from .polar_sampling import polar_pixels

    sample = polar_pixels(arrays['azimuth'], arrays['range'], image_size)
    ray, gate, support = sample.ray, sample.gate, sample.available
    fields = {}
    for name in ('DBZH_RAW', 'DBZH_QC', 'QUALITY_INDEX', 'QC_FLAGS', 'LOW_QUALITY_MASK', 'NO_ECHO_MASK'):
        if name in arrays:
            fields[name] = np.where(support, arrays[name][ray, gate], np.nan)
    fields['DISPLAY_VALID'] = (support & np.asarray(valid, bool)[ray, gate]).astype(float)
    fields['SOURCE_RAY'] = np.where(support, ray, np.nan)
    fields['SOURCE_GATE'] = np.where(support, gate, np.nan)
    layer['probe'] = attach_probe(objects, 'query/radar/' + layer['layer_id'], fields,
                                 image_path=layer['object_path'],
                                 identity={key: layer[key] for key in ('radar_id', 'scan_id', 'sweep_number', 'field')})
