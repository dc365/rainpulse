"""Load stdlib-only production modules without importing optional radar SDKs."""
import importlib
import sys
import types
from pathlib import Path

WORKER = Path(__file__).resolve().parents[2] / "algorithms/rainpulse_algo/worker"
NAME = "_rainpulse_batch2_core"
if NAME not in sys.modules:
    package = types.ModuleType(NAME)
    package.__path__ = [str(WORKER)]
    sys.modules[NAME] = package
cache = importlib.import_module(NAME + ".asset_cache")
access = importlib.import_module(NAME + ".asset_access")
resources = importlib.import_module(NAME + ".resources")
