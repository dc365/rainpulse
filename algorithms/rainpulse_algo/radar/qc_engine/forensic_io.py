"""Checksum-bound local reads and explicitly scoped resource configuration."""

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

RESOURCE_ENV = {
    "RAINPULSE_RADAR_CONFIG_DIR": "directory",
    "RAINPULSE_ANCILLARY_CONFIG": "file",
    "RAINPULSE_ANCILLARY_ROOT": "directory",
}


def file_digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("local regular file required; symlinks are not frozen inputs")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def directory_digest(path, *, maximum_bytes=4 * 1024**3, maximum_files=200000):
    """Same identity as artifact_sha256, without keeping all chunks in memory."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("local directory required")
    files = []
    for p in path.rglob("*"):
        if p.is_symlink():
            raise ValueError("symlink in frozen directory")
        if p.is_file():
            files.append(p)
        if len(files) > maximum_files:
            raise ValueError("frozen directory file budget exceeded")
    h = hashlib.sha256()
    size = 0
    for p in sorted(files, key=lambda x: x.relative_to(path).as_posix()):
        key = p.relative_to(path).as_posix().encode()
        before = p.stat()
        size += before.st_size
        if size > maximum_bytes:
            raise ValueError("frozen directory byte budget exceeded")
        digest = file_digest(p)
        after = p.stat()
        # Reading may legitimately change access time; content identity must not.
        if any(
            getattr(after, k) != getattr(before, k)
            for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            raise ValueError("file changed while hashing")
        h.update(len(key).to_bytes(4, "big"))
        h.update(key)
        h.update(before.st_size.to_bytes(8, "big"))
        h.update(bytes.fromhex(digest))
    return h.hexdigest()


def verified_path(root, spec, *, directory=False):
    if set(spec) != {"path", "sha256"}:
        raise ValueError("frozen input requires exactly path and sha256")
    path = Path(root) / spec["path"]
    digest = directory_digest(path) if directory else file_digest(path)
    if digest != spec["sha256"]:
        raise ValueError("frozen input checksum changed")
    return path.resolve()


@contextmanager
def frozen_resources(root, specs):
    """No ambient geometry paths; no credentials captured. Single-process CLI only."""
    if set(specs) - RESOURCE_ENV.keys():
        raise ValueError("resource environment contains a non-whitelisted key")
    paths = {
        k: verified_path(root, value, directory=RESOURCE_ENV[k] == "directory")
        for k, value in specs.items()
    }
    previous = {key: os.environ.get(key) for key in RESOURCE_ENV}
    try:
        for key in RESOURCE_ENV:
            os.environ.pop(key, None)
            if key in paths:
                os.environ[key] = str(paths[key])
        yield {k: {"sha256": specs[k]["sha256"], "kind": RESOURCE_ENV[k]} for k in paths}
        for key, value in specs.items():
            verified_path(root, value, directory=RESOURCE_ENV[key] == "directory")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
