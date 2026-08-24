"""Shared RainPulse compute-plane package."""

from typing import Final

PACKAGE_NAME: Final = "rainpulse-algo"
__version__: Final = "0.1.0"


def package_info() -> dict[str, str]:
    """Return stable package identity for diagnostics and worker startup logs."""

    return {
        "name": PACKAGE_NAME,
        "version": __version__,
    }


__all__ = ["PACKAGE_NAME", "__version__", "package_info"]
