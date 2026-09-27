import base64
import hashlib
import json
import zlib

import numpy as np

from rainpulse_algo.diagnostics.radar_probe import attach_probe


def test_probe_retains_source_precision_missing_and_high_flag_bits():
    values = np.array([[12.345, np.nan], [0, -8.25]], dtype=np.float32)
    objects = {'image.png': b'image-binding'}
    flags = np.array([[2**31+1, 0], [3, 2]], dtype=np.uint32)
    index = attach_probe(objects, 'query/test', {'DBZH_RAW': values, 'QC_FLAGS': flags},
                         image_path='image.png', identity={'scan_id': 'frozen'})
    item = index['tiles']['0_0']
    content = objects[item['path']]
    assert hashlib.sha256(content).hexdigest() == item['sha256']
    tile = json.loads(content)
    decoded = np.frombuffer(zlib.decompress(base64.b64decode(tile['data'])), dtype='<f8').reshape(2,2,2)
    assert decoded[0,0,0] == float(values[0,0])
    assert decoded[0,0,1] == 2**31+1
    assert np.isnan(decoded[0,1,0])
    assert decoded[1,0,0] == 0
    assert decoded[1,1,0] == -8.25
