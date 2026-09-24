# ruff: noqa: E501, I001
"""Offline reference replay, using the same numerical executor as the ops Worker.

Local bundles are test/replay inputs, not a replacement for the authenticated
object reader or the canonical ingest registration on the server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

from .managed import Executor
from .model import json_bytes


def logical_digest(objects: dict[str, bytes]) -> str:
    """Byte-for-byte RainPulse artifact hash, usable without MinIO imports.

    Equivalent to worker.object_store.artifact_sha256 at fb5fb215. The parity
    test in a complete repository checks the implementation, not a stub.
    """
    h = hashlib.sha256()
    for key, value in sorted(objects.items()):
        k = key.encode()
        h.update(len(k).to_bytes(4, 'big'))
        h.update(k)
        h.update(len(value).to_bytes(8, 'big'))
        h.update(hashlib.sha256(value).digest())
    return h.hexdigest()


class LocalReader:
    def __init__(self, root: Path, index: dict[str, str], maximum_bytes: int):
        self.root, self.index, self.maximum = root.resolve(strict=True), dict(index), maximum_bytes

    def load(self, uri: str) -> dict[str, bytes]:
        relative = self.index[uri]
        candidate = PurePosixPath(relative)
        if not relative or candidate.is_absolute() or '..' in candidate.parts:
            raise ValueError('local replay path must be below the input root')
        directory = (self.root / relative).resolve(strict=True)
        if not directory.is_relative_to(self.root) or not directory.is_dir():
            raise ValueError('local replay path escaped input root')
        # First version accepts only a canonical two-object local native bundle.
        # Live Zarr is read by the server adapter; no directory-rglob here.
        objects = {}
        total = 0
        for name in ('volume.json', 'arrays.npz'):
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('input must be a regular canonical native object')
            size = path.stat().st_size
            total += size
            if total > self.maximum:
                raise ValueError('local input exceeds declared read budget')
            with path.open('rb') as f:
                data = f.read(self.maximum+1)
            if len(data) != size:
                raise ValueError('local input changed during read or exceeded budget')
            objects[name] = data
        return objects


def replay(network: Path, request_path: Path, root: Path, index_path: Path, output: Path) -> dict:
    with request_path.open("rb") as stream:
        request_raw = stream.read(1024**2 + 1)
    with index_path.open("rb") as stream:
        index_raw = stream.read(1024**2 + 1)
    if len(request_raw) > 1024**2 or len(index_raw) > 1024**2:
        raise ValueError('oversized local replay manifest')
    request, index = json.loads(request_raw), json.loads(index_raw)
    if not isinstance(index, dict) or len(index) > 16:
        raise ValueError('local input index exceeds network limits')
    executor = Executor(network)
    objects, summary, metrics = executor.execute(request, LocalReader(root, index, executor.network.maximum_input_bytes), artifact_digest=logical_digest)
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError('output already exists; replay never overwrites evidence')
    # Single-process local reference publishing. Live workers use the existing
    # conditional _SUCCESS object-store publisher instead of this path.
    with tempfile.TemporaryDirectory(prefix='.multiband-', dir=output.parent) as staging:
        directory = Path(staging) / 'product'
        directory.mkdir()
        for name, data in objects.items():
            if PurePosixPath(name).name != name:
                raise ValueError('unexpected local output key')
            (directory / name).write_bytes(data)
        receipt = dict(summary=summary, metrics=metrics, artifact_sha256=logical_digest(objects), mode='offline-reference-not-operational')
        (directory / 'replay-receipt.json').write_bytes(json_bytes(receipt))
        # Do not replace an existing destination created by another operator.
        # Directory creation is exclusive, then files are moved into it; receipt
        # is last so clients never mistake an incomplete output for success.
        output.mkdir()
        for name in sorted(objects):
            os.replace(directory / name, output / name)
        os.replace(directory / 'replay-receipt.json', output / 'replay-receipt.json')
    return receipt


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('network', 'request', 'input-root', 'index', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    try:
        receipt = replay(a.network, a.request, a.input_root, a.index, a.output)
    except (OSError, ValueError, KeyError) as e:
        p.error(str(e))
    print(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
