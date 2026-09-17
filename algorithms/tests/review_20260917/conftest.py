"""Load the delivered algorithm modules without importing unrelated worker services.

This is a branch/contract test harness, NOT a substitute for upstream worker tests.
Original narrow/opening functions come from the installed repo, or SHA-verified
source_basis in the standalone delivery.
"""
from pathlib import Path
import sys
import types
import importlib
from dataclasses import dataclass
import copy
import numpy as np
import pytest

HERE = Path(__file__).resolve()
ENGINE = HERE.parents[2]/"rainpulse_algo/radar/qc_engine"
BUNDLE = next((p for p in HERE.parents if (p/"integration_edits.json").exists()), None)
PATHS = [str(ENGINE)]
if BUNDLE:
    PATHS.append(str(BUNDLE/"source_basis/qc_engine"))
for name, paths in (("rp_review_tests", []), ("rp_review_tests.engine", PATHS)):
    mod = types.ModuleType(name); mod.__path__ = paths; sys.modules[name] = mod

def load(name):
    return importlib.import_module("rp_review_tests.engine.review_extension."+name)


class Group(dict):
    def __init__(self, *a, attrs=None, **kw):
        super().__init__(*a, **kw); self.attrs = attrs or {}
    def __getitem__(self, key):
        if key in self.keys():
            return super().__getitem__(key)
        if isinstance(key, str) and "/" in key:
            a, b = key.split("/", 1)
            return super().__getitem__(a)[b]
        return super().__getitem__(key)


class Native:
    def __init__(self, z=None, shape=(12,120), *, full=False, dr=1000., start=10000., **fields):
        z = np.full(shape, 0., "float32") if z is None else np.asarray(z, dtype="float32")
        self.shape = z.shape
        self.fields = {"DBZH": z.copy(), **{k: np.full(z.shape, v, "float32") if np.ndim(v)==0 else np.asarray(v,dtype="float32").copy() for k,v in fields.items()}}
        self.field_available = {k: np.isfinite(v) for k,v in self.fields.items()}
        self.ranges = start + dr*np.arange(z.shape[1], dtype="float64")
        self.azimuth = np.arange(z.shape[0], dtype="float64")*(360/z.shape[0] if full else 1.)
        self.elevation = np.full(z.shape[0], .5, "float64")
        self.geometry_good = np.ones(z.shape[0], bool)
        self.gap_after = np.zeros(z.shape[0], bool)
        if not full: self.gap_after[-1]=True
        self.full_ppi=full
        self.original_indices=np.arange(z.shape[0]); self.name="sweep_000"
        self.attrs={"scan_id":"target", "radar_id":"synthetic", "volume_end_time_utc":"2026-09-17T08:24:00+00:00"}
    @property
    def gate_spacing_m(self): return float(np.median(np.diff(self.ranges)))
    def restore(self, x):
        result=np.empty_like(x); result[self.original_indices]=x; return result
    def clone(self): return copy.deepcopy(self)


@dataclass(frozen=True)
class DecisionFixture:
    arrays: dict
    flags: np.ndarray
    quality: np.ndarray


def keep(native):
    observed=native.field_available["DBZH"]
    a={name:observed.astype("uint8") for name in load("runtime").TRUST_FIELDS}
    a.update(QC_ACTION=np.where(observed,0,3).astype("uint8"), RFI_QUARANTINE_MASK=np.zeros(native.shape,"uint8"),
             RFI_RISK_STATE=np.zeros(native.shape,"uint8"), DBZH_USABLE=np.where(observed,native.fields["DBZH"],np.nan).astype("float32"))
    return DecisionFixture(a,np.where(observed,0,4096).astype("uint32"),np.where(observed,1.,np.nan).astype("float32"))


def serial(decision, native):
    return Group({**decision.arrays,"QC_FLAGS":decision.flags,"QUALITY_INDEX":decision.quality,"VALID_MASK":native.field_available["DBZH"].astype("uint8")})


def history(native):
    s=native.shape
    return {"receipt":{"verified":True},"fields":{
        "ground_clutter":np.full(s,.99,"float32"),"confidence_lower_bound":np.full(s,.85,"float32"),
        "day_count":np.full(s,20,"uint32"),"observed_count":np.full(s,200,"uint32"),
        "qualified_mask":np.ones(s,"uint8"),"dbzh_mean":np.full(s,20.,"float32")}}


def recurrence(native):
    return {"NP_FIXED_MATCH_FRACTION":np.ones(native.shape,"float32"),"NP_FIXED_SAMPLE_COUNT":np.full(native.shape,3,"uint8")}
