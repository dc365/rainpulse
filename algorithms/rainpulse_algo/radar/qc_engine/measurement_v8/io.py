"""Local-only bounded IO, checksums and no-overwrite atomic directory publication."""

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np


def safe(value):
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return safe(value.tolist())
    if isinstance(value, np.generic):
        return safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def json_bytes(obj):
    return (
        json.dumps(
            safe(obj), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        + "\n"
    ).encode()


def digest(obj):
    return hashlib.sha256(json_bytes(obj)).hexdigest()


def file_hash(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ValueError(f"expected regular local file: {p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for data in iter(lambda: f.read(1 << 20), b""):
            h.update(data)
    return h.hexdigest()


def tree_hash(path):
    """v1 tree hash of relative paths and file SHA256, NOT RainPulse artifact_sha256."""
    p = Path(path)
    if p.is_symlink() or not p.is_dir():
        raise ValueError("expected regular local directory")
    entries = []
    for f in sorted(p.rglob("*")):
        if f.is_symlink():
            raise ValueError("symlinks are not frozen assets")
        if f.is_file():
            entries.append((f.relative_to(p).as_posix(), file_hash(f)))
    if not entries:
        raise ValueError("empty frozen directory")
    return digest({"scheme": "relative-path-file-sha256-v1", "files": entries})


def checked(root, ref, directory=False):
    raw = str(ref.path if hasattr(ref, "path") else ref["path"])
    expected = ref.sha256 if hasattr(ref, "sha256") else ref["sha256"]
    if "://" in raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise ValueError("frozen references must be confined relative local paths")
    base = Path(root).resolve()
    original = base / raw
    if any(p.is_symlink() for p in [original, *original.parents] if p != base.parent):
        raise ValueError("symlink in input path")
    p = original.resolve()
    if not p.is_relative_to(base):
        raise ValueError("input escapes frozen directory")
    actual = tree_hash(p) if directory else file_hash(p)
    if actual != expected:
        raise ValueError(f"checksum mismatch: {raw}")
    return p


@contextmanager
def atomic_directory(destination):
    out = Path(destination).absolute()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() or out.is_symlink():
        raise ValueError("output exists; choose a new directory")
    lock = out.with_name(out.name + ".lock")
    with lock.open("x", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    tmp = Path(tempfile.mkdtemp(prefix=".rp8-", dir=out.parent))
    try:
        yield tmp
        if out.exists() or out.is_symlink():
            raise ValueError("output appeared during execution")
        os.rename(tmp, out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
        lock.unlink(missing_ok=True)


def load_npz(path, maximum_bytes=512 * 1024 * 1024):
    import zipfile

    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > maximum_bytes:
            raise ValueError("NPZ exceeds memory budget")
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def write_json(path, value):
    Path(path).write_bytes(json_bytes(value))
