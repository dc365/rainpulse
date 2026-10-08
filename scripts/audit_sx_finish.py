# ruff: noqa: E501, I001
"""Read-only, SHA-bound validation of an exported three-product comparison.

Checks every finite grid winner, all declared audit/probe file hashes and source
namespaces. It does not read raw radar assets or claim meteorological accuracy.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from zipfile import ZipFile
import numpy as np
from rainpulse_algo.multiband.comparison_provenance import CONTRACT, WINNER_FIELDS, scope

def read_bounded(root, key, maximum):
    p = PurePosixPath(key)
    if p.is_absolute() or '..' in p.parts or str(p) != key:
        raise ValueError('invalid relative object path')
    path = (root / key).resolve()
    if not path.is_relative_to(root.resolve()) or path.stat().st_size > maximum:
        raise ValueError('object outside root or over budget')
    return path.read_bytes()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def audit(root, expected_manifest_sha, maximum_bytes=1024 ** 3):
    root = Path(root)
    raw = read_bounded(root, 'manifest.json', 16 * 1024 ** 2)
    if len(expected_manifest_sha) != 64 or digest(raw) != expected_manifest_sha:
        raise ValueError('manifest differs from frozen identity')
    m = json.loads(raw)
    if m.get('source_contract') != CONTRACT:
        raise ValueError('legacy result: no verified source-v2 contract')
    packed = read_bounded(root, 'arrays.npz', maximum_bytes)
    if digest(packed) != m.get('arrays_sha256'):
        raise ValueError('numeric artifact hash differs')
    import io
    with ZipFile(io.BytesIO(packed)) as archive:
        entries = archive.infolist()
        if len(entries) > 256 or len({e.filename for e in entries}) != len(entries) or sum((e.file_size for e in entries)) > maximum_bytes:
            raise ValueError('NPZ decoding budget exceeded')
        for e in entries:
            if not e.filename.endswith('.npy') or '/' in e.filename:
                raise ValueError('invalid numeric member')
            with archive.open(e) as f:
                version = np.lib.format.read_magic(f)
                if version == (1, 0):
                    shape, order, dtype = np.lib.format.read_array_header_1_0(f)
                elif version == (2, 0):
                    shape, order, dtype = np.lib.format.read_array_header_2_0(f)
                else:
                    raise ValueError('unsupported numeric header')
                size = math.prod(shape) * dtype.itemsize
                if dtype.hasobject or len(shape) != 2 or size > e.file_size or (size > maximum_bytes):
                    raise ValueError('invalid/unbounded numeric array')
    with np.load(io.BytesIO(packed), allow_pickle=False) as npz:
        arrays = {k: npz[k] for k in npz.files}
    result = {'contract': 'rainpulse.sx-export-audit-v1', 'manifest_sha256': expected_manifest_sha, 'arrays_sha256': m['arrays_sha256'], 'products': {}, 'verified_object_count': 0, 'meteorological_accuracy': 'not_evaluated'}
    total_checked_bytes = 0

    def verify(key, sha, limit=8 * 1024 ** 2):
        nonlocal total_checked_bytes
        data = read_bounded(root, key, limit)
        total_checked_bytes += len(data)
        if total_checked_bytes > maximum_bytes:
            raise ValueError('cumulative auxiliary audit budget exceeded')
        if digest(data) != sha:
            raise ValueError('object checksum differs: ' + key)
        result['verified_object_count'] += 1
    for ref in m.get('audit', {}).values():
        verify(ref['object_path'], ref['sha256'])
    for p in m['comparison']['products']:
        pid = p['product_id']
        if pid not in {'s_only', 'x_only', 'sx_composite'} or p.get('status') == 'no_inputs':
            continue
        suffix = '' if pid == 'sx_composite' else '_S_ONLY' if pid == 's_only' else '_X_ONLY'
        f = 'CR_DBZH' + suffix
        keys = {n: arrays[n + suffix] for n in WINNER_FIELDS if n + suffix in arrays}
        keys['CR_DBZH'] = arrays[f]
        prov = p['provenance']
        src = prov['sources']
        canonical = json.dumps(src, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if digest(canonical) != prov['sources_sha256']:
            raise ValueError('source table digest differs')
        checked = scope(SimpleNamespace(arrays=keys, metadata={'sources': src}), band=None if pid == 'sx_composite' else pid[0].upper())
        if checked['winner_source_indices'] != prov['winner_source_indices'] or checked['source_indices'] != prov['source_indices']:
            raise ValueError('declared source subset differs')
        probe = p.get('map', {}).get('probe')
        if probe:
            identity = probe['identity']
            if identity.get('sources_sha256') != prov['sources_sha256'] or identity.get('product_id') != pid:
                raise ValueError('probe identity differs')
            verify(p['map']['object_path'], probe['image_sha256'])
            for tile in probe['tiles'].values():
                verify(tile['path'], tile['sha256'], 2 * 1024 ** 2)
        result['products'][pid] = {'finite_grid_winners_checked': int(np.isfinite(keys['CR_DBZH']).sum()), 'winner_station_count': checked['counts']['winner_station_count']}
    result['status'] = 'passed'
    return result

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--maximum-bytes', type=int, default=1024 ** 3)
    a = p.parse_args(argv)
    if not 1 <= a.maximum_bytes <= 8 * 1024 ** 3:
        raise ValueError('maximum bytes outside bounds')
    output = Path(a.output)
    if output.exists():
        raise FileExistsError('refuse to overwrite an audit receipt')
    value = audit(a.bundle, a.manifest_sha256, a.maximum_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
if __name__ == '__main__':
    main()
