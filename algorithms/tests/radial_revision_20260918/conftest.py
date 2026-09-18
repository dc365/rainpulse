"""Exercise exact extension modules without importing unrelated worker services."""
from pathlib import Path
import importlib
import types
import sys
import copy
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "rainpulse_algo/radar/qc_engine"
for name, paths in (("radial_revision_test", []), ("radial_revision_test.engine", [str(ENGINE)])):
    m = types.ModuleType(name)
    m.__path__ = paths
    sys.modules[name] = m


def load(name):
    return importlib.import_module("radial_revision_test.engine.review_extension."+name)


class Native:
    def __init__(self, z, *, dr=1000., start=50000., fields=None):
        z = np.asarray(z, dtype="float32")
        self.fields = {"DBZH": z.copy()}
        for k, value in (fields or {}).items():
            self.fields[k] = np.broadcast_to(value, z.shape).astype("float32").copy()
        self.field_available = {k: np.isfinite(v) for k, v in self.fields.items()}
        self.shape = z.shape
        self.ranges = start+dr*np.arange(z.shape[1], dtype=float)
        self.azimuth = np.arange(z.shape[0], dtype=float)
        self.elevation = np.full(z.shape[0], .5)
        self.geometry_good = np.ones(z.shape[0], bool)
        self.gap_after = np.zeros(z.shape[0], bool)
        self.gap_after[-1] = True
        self.full_ppi = False
        self.original_indices = np.arange(z.shape[0])
        self.name = "sweep_000"
        self.attrs = {"radar_id": "SYNTHETIC"}
    @property
    def gate_spacing_m(self):
        return float(np.median(np.diff(self.ranges)))
    def clone(self):
        return copy.deepcopy(self)
    def restore(self, a):
        out = np.empty_like(a)
        out[self.original_indices] = a
        return out


def line():
    z = np.full((13, 240), np.nan, dtype="float32")
    z[6] = 40.
    return Native(z, fields={"SNR": np.where(np.isfinite(z), 40., np.nan)})


def states(*, polar=True):
    r = 50000.+1000.*np.arange(600)
    snr = np.full((12, 600), 25., dtype="float32")
    snr[5] = np.where(np.arange(600)%20 < 10, 45., 5.)
    z = snr-35.+20.*np.log10(r[None, :]/1000.)+.012*r[None, :]/1000.
    f = {"SNR": snr}
    if polar:
        f.update(PHIDP=np.full(snr.shape, 30.), RHOHV=np.full(snr.shape, .99), ZDR=np.full(snr.shape, .5))
    return Native(z, fields=f)


def evaluate(n, cfg, source=None, **kwargs):
    src = np.zeros(n.shape, bool) if source is None else source
    return load("radial_revision.engine").evaluate(n, cfg, src, np.zeros(n.shape, "float32"), **kwargs)
