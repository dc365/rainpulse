"""Environment-to-path adapter; no defaults or path policy changes."""
from __future__ import annotations

import os
from pathlib import Path


def optional_file(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        from .qc import QCConfigError
        raise QCConfigError(f"{name} must identify a file")
    return path


def optional_directory(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        from .qc import QCConfigError
        raise QCConfigError(f"{name} must identify a directory")
    return path


def required_roots(name: str) -> tuple[Path, ...]:
    value = os.getenv(name)
    if not value:
        from .qc import QCConfigError
        raise QCConfigError(f"{name} is required for file ancillary assets")
    return tuple(Path(item).resolve(strict=True) for item in value.split(os.pathsep) if item)
