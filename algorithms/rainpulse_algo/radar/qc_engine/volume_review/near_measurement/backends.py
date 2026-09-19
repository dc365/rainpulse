"""Explicit third-party calls; never silently substitute backend identities."""
from importlib import import_module, metadata
import numpy as np


def require(package, module, expected):
    try:
        actual = metadata.version(package)
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"selected backend requires {package}=={expected}") from exc
    if actual != expected:
        raise RuntimeError(f"{package} version {actual} differs from frozen {expected}")
    try:
        return import_module(module)
    except ImportError as exc:
        raise RuntimeError(f"{package} exists but cannot import: {exc}") from exc


def reference_depolarization(zdr, rho):
    """wradlib's published DR equation, float64; inputs have already been checked."""
    zdr, rho = np.broadcast_arrays(np.asarray(zdr, float), np.asarray(rho, float))
    linear = np.power(10., zdr / 10.)
    cross = 2 * rho * np.sqrt(linear)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 10 * np.log10(np.maximum((1 + linear - cross) / (1 + linear + cross), 0.))


def depolarization(zdr, rho, cfg):
    z, r = np.broadcast_arrays(np.asarray(zdr, float), np.asarray(rho, float))
    good = np.isfinite(z) & np.isfinite(r) & (r >= 0) & (r <= 1) & (abs(z) < cfg.maximum_abs_zdr_db)
    # Mask first. Library result at missing inputs is never evidence.
    zz, rr = np.where(good, z, np.nan), np.where(good, r, np.nan)
    if cfg.depolarization_backend == "wradlib":
        wrl = require("wradlib", "wradlib", cfg.wradlib_version)
        with np.errstate(divide="ignore", invalid="ignore"):
            result = np.asarray(wrl.dp.depolarization(zz, rr), float)
        receipt = {"backend": "wradlib.dp.depolarization", "version": cfg.wradlib_version}
    else:
        result = reference_depolarization(zz, rr)
        receipt = {"backend": "numpy_reference_DR_equation", "version": "1", "wradlib_called": False}
    if result.shape != z.shape or np.any(good & (np.isnan(result) | np.isposinf(result))):
        raise ValueError("DR backend returned invalid output on valid inputs")
    # rho=1, ZDR=0 has DR=-inf. Floor for a finite diagnostic, not an evidence threshold.
    return np.where(good, np.maximum(result, -100.), np.nan).astype("float32"), receipt


def check_native_gatefilter(native, baseline_raw, target_raw, cfg):
    """Optional Py-ART GateFilter audit on ACTUAL NativeSweep geometry/fields.

    Does not manufacture velocity/phase or apply any additional classifier.
    Never uses an include operation that might revive an old exclusion.
    """
    if cfg.gatefilter_check == "disabled":
        return {"backend": "disabled", "pyart_called": False}
    pyart = require("arm_pyart", "pyart", cfg.pyart_version)
    radar = native.to_pyart()
    order = np.asarray(native.original_indices)
    before = np.asarray(baseline_raw, bool)[order]
    after = np.asarray(target_raw, bool)[order]
    if np.any(after & ~before):
        raise ValueError("CR GateFilter attempted to revive excluded gates")
    filt = pyart.filters.GateFilter(radar, exclude_based=True)
    filt.exclude_gates(~before, op="or")
    filt.exclude_gates(~after, op="or")
    if not np.array_equal(filt.gate_excluded, ~after):
        raise ValueError("Py-ART GateFilter differs from CR qualification")
    return {"backend": "pyart.filters.GateFilter.exclude_gates", "version": cfg.pyart_version,
            "pyart_called": True}
