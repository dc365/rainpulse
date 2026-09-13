"""Thin calls to pinned Py-ART/wradlib APIs, with explicit stencil availability."""

from __future__ import annotations

import importlib.metadata
import time
import warnings
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from scipy import ndimage

from .adapters import FIELD_NAMES, NativeSweep
from .profile import OpenSourceQCProfile


@dataclass(frozen=True)
class EvidenceSet:
    arrays: dict[str, np.ndarray]
    records: tuple[dict[str, Any], ...]
    libraries: dict[str, str]


@lru_cache(maxsize=4)
def require_libraries(pyart_version: str, wradlib_version: str) -> dict[str, str]:
    expected = {"arm_pyart": pyart_version, "wradlib": wradlib_version}
    for package, version in expected.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(f"required QC dependency {package} is missing") from error
        if actual != version:
            raise RuntimeError(f"QC {package} version {actual} differs from frozen {version}")
    return expected


def _values(value) -> np.ndarray:
    return np.asarray(np.ma.filled(value, np.nan), dtype="float32")


def library_evidence(
    native: NativeSweep, profile: OpenSourceQCProfile, clutter_prior: np.ndarray | None = None
) -> EvidenceSet:
    versions = require_libraries(profile.arm_pyart_version, profile.wradlib_version)
    import pyart
    import wradlib as wrl

    started = time.perf_counter()
    radar = native.to_pyart()
    shape = native.shape
    observed = native.field_available["DBZH"]
    echo = observed & (native.fields["DBZH"] >= profile.pyart.object_threshold_dbz)
    arrays: dict[str, np.ndarray] = {}
    records: list[dict[str, Any]] = []
    window = max(3, int(round(profile.pyart.texture_window_m / native.gate_spacing_m)) | 1)
    texture_ready = window <= shape[1]
    thresholds = {
        "DBZH": profile.pyart.max_textrefl,
        "ZDR": profile.pyart.max_textzdr,
        "PHIDP": profile.pyart.max_textphi,
        "RHOHV": profile.pyart.max_textrhv,
    }
    for field, threshold in thresholds.items():
        values = np.full(shape, np.nan, dtype="float32")
        available = np.zeros(shape, dtype=bool)
        if field in native.fields and texture_ready:
            values = _values(pyart.util.texture_along_ray(radar, FIELD_NAMES[field][0], window))
            available = native.support(native.field_available[field], 0, window // 2)
            if field == "PHIDP":
                # A phase branch-cut is not proof of noise. Vulpiani gets a separately
                # unwrapped continuous segment; texture evidence abstains near the cut.
                phase = native.fields[field]
                jump = np.zeros(shape, bool)
                jump[:, 1:] = np.abs(np.diff(phase, axis=1)) > (
                    profile.geometry.phase_period_deg / 2
                )
                available &= ~ndimage.maximum_filter1d(jump, size=window, axis=1)
            available &= np.isfinite(values)
        values[~available] = np.nan
        arrays[f"OS_{field}_TEXTURE"] = values
        arrays[f"OS_{field}_TEXTURE_AVAILABLE_MASK"] = available.astype("uint8")
        arrays[f"OS_{field}_TEXTURE_CANDIDATE_MASK"] = (
            available & echo & (values > threshold)
        ).astype("uint8")
        records.append(
            {
                "algorithm": "pyart.util.texture_along_ray",
                "version": versions["arm_pyart"],
                "family": "reflectivity_structure" if field == "DBZH" else "polarimetry",
                "input_fields": [field],
                "window_gates": window,
                "window_m": window * native.gate_spacing_m,
                "threshold": threshold,
                "available_gates": int(available.sum()),
                "status": "applied" if available.any() else "unavailable",
            }
        )

    # Retain the published combined filter as a comparison baseline; never count
    # it as an independent vote alongside its component tests.
    if texture_ready and observed.any():
        baseline = pyart.filters.moment_and_texture_based_gate_filter(
            radar,
            refl_field="reflectivity",
            zdr_field="differential_reflectivity",
            rhv_field="cross_correlation_ratio",
            phi_field="differential_phase",
            wind_size=window,
            max_textrefl=profile.pyart.max_textrefl,
            max_textzdr=profile.pyart.max_textzdr,
            max_textrhv=profile.pyart.max_textrhv,
            max_textphi=profile.pyart.max_textphi,
            min_rhv=profile.pyart.min_rhv,
        )
        arrays["OS_PYART_BASELINE_CANDIDATE_MASK"] = (baseline.gate_excluded & echo).astype("uint8")
    else:
        arrays["OS_PYART_BASELINE_CANDIDATE_MASK"] = np.zeros(shape, dtype="uint8")

    # Object methods may connect array-adjacent rays. Do not apply that topology
    # to a cut with internal gaps/duplicates; textures still work on valid stencils.
    continuous = not np.any(native.gap_after[:-1]) and native.geometry_good.all()
    small = np.zeros(shape, bool)
    object_available = observed & continuous
    if continuous and echo.any():
        filt = pyart.filters.GateFilter(radar)
        filt.exclude_gates(~observed)
        labels = pyart.correct.find_objects(
            radar,
            "reflectivity",
            threshold=profile.pyart.object_threshold_dbz,
            gatefilter=filt,
            delta=2.0 if native.full_ppi else 0.0,
        )["data"]
        # Select the published objects by size. Py-ART 2.2.5 despeckle_field
        # passes a scalar nomask to GateFilter on some all-valid/no-speckle
        # inputs; find_objects avoids that API edge without copying its algorithm.
        ids = np.asarray(np.ma.filled(labels, 0), dtype="int64")
        counts = np.bincount(ids.ravel())
        small = (ids > 0) & (counts[ids] < profile.pyart.small_object_gates) & echo
    arrays["OS_SMALL_OBJECT_CANDIDATE_MASK"] = small.astype("uint8")
    arrays["OS_SMALL_OBJECT_AVAILABLE_MASK"] = object_available.astype("uint8")
    records.append(
        {
            "algorithm": "pyart.correct.find_objects",
            "version": versions["arm_pyart"],
            "family": "reflectivity_structure",
            "size_gates": profile.pyart.small_object_gates,
            "threshold_dbz": profile.pyart.object_threshold_dbz,
            "status": "applied" if continuous else "unavailable",
            "reason": None if continuous else "discontinuous_azimuth_topology",
        }
    )

    config = profile.wradlib
    radius = config.gabella_window // 2
    gabella_available = native.support(observed, radius, radius)
    gabella = np.zeros(shape, bool)
    # Split internal azimuth gaps rather than allowing connected-object filtering
    # to bridge them. Additional rows are unavailable padding, never observations.
    edges = [0, *[int(x) + 1 for x in np.flatnonzero(native.gap_after[:-1])], shape[0]]
    if native.full_ppi:
        edges = [0, shape[0]]
    for left, right in zip(edges[:-1], edges[1:], strict=True):
        if right - left < config.gabella_window or not observed[left:right].any():
            gabella_available[left:right] = False
            continue
        working = np.where(observed[left:right], native.fields["DBZH"][left:right], np.nan)
        pad = radius + 1
        working = np.pad(
            working,
            ((pad, pad), (0, 0)),
            mode="wrap" if native.full_ppi else "constant",
            **({} if native.full_ppi else {"constant_values": np.nan}),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            raw = wrl.classify.filter_gabella(
                working,
                wsize=config.gabella_window,
                thrsnorain=config.gabella_thrsnorain,
                tr1=config.gabella_tr1,
                n_p=config.gabella_n_p,
                tr2=config.gabella_tr2,
                rm_nans=False,
                radial=False,
                cartesian=not native.full_ppi,
            )
        gabella[left:right] = raw[pad : pad + right - left]
        if caught:
            records.append(
                {
                    "algorithm": "wradlib.classify.filter_gabella",
                    "status": "warning",
                    "warnings": sorted({str(item.message) for item in caught}),
                }
            )
    arrays["OS_GABELLA_CANDIDATE_MASK"] = (gabella & gabella_available & echo).astype("uint8")
    arrays["OS_GABELLA_AVAILABLE_MASK"] = gabella_available.astype("uint8")
    records.append(
        {
            "algorithm": "wradlib.classify.filter_gabella",
            "version": versions["wradlib"],
            "family": "reflectivity_structure",
            "status": "applied",
            "parameters": {
                "wsize": config.gabella_window,
                "tr1": config.gabella_tr1,
                "n_p": config.gabella_n_p,
                "tr2": config.gabella_tr2,
                "rm_nans": False,
                "thrsnorain": config.gabella_thrsnorain,
            },
        }
    )

    # RAW moment input, not precomputed texture. A fresh dictionary isolates the
    # library's rho2 assignment from the read-only task inputs.
    data: dict[str, np.ndarray | None] = {}
    pol_count = np.zeros(shape, dtype="uint8")
    for field, key in (("ZDR", "zdr"), ("RHOHV", "rho"), ("PHIDP", "phi"), ("VR", "dop")):
        available = native.field_available.get(field, np.zeros(shape, bool)).copy()
        if field in {"ZDR", "RHOHV", "PHIDP"}:
            safe = native.support(available, 1, 1)
            if field == "PHIDP" and field in native.fields:
                # Protect both azimuthal and range branch cuts for the library's 3x3 texture.
                phase = native.fields[field]
                padded = np.pad(phase, 1, mode="edge")
                spread = ndimage.maximum_filter(padded, size=3) - ndimage.minimum_filter(
                    padded, size=3
                )
                safe &= spread[1:-1, 1:-1] <= profile.geometry.phase_period_deg / 2
            pol_count += safe.astype("uint8")
            if field == "PHIDP":
                available &= safe
        values = native.fields.get(field, np.full(shape, np.nan, dtype="float32"))
        data[key] = np.where(available, values, np.nan).astype("float32")
        if field == "PHIDP":
            pol_count -= safe.astype("uint8")
            pol_count += native.support(available, 1, 1).astype("uint8")
    data["map"] = None if clutter_prior is None else np.asarray(clutter_prior, dtype="float32")
    if data["map"] is not None and data["map"].shape != shape:
        raise ValueError("clutter prior geometry mismatch")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        score, _ = wrl.classify.classify_echo_fuzzy(
            data,
            weights=dict(config.fuzzy_weights),
            trpz=dict(config.fuzzy_trapezoids),
        )
    score = _values(score)
    # Library finite-weight normalization is NOT evidence availability.
    supported = native.support(observed, 1, 1) & (pol_count >= config.minimum_pol_moments)
    supported &= np.isfinite(score)
    arrays["OS_FUZZY_RAW_SCORE"] = score.copy()
    arrays["METEO_SCORE"] = np.where(supported, score, np.nan).astype("float32")
    arrays["METEO_SCORE_AVAILABLE_MASK"] = supported.astype("uint8")
    arrays["OS_POL_MOMENT_COUNT"] = pol_count
    if profile.rfi_objects is not None:
        raw_count = np.zeros(shape, dtype="uint8")
        axial_count = np.zeros(shape, dtype="uint8")
        for field in ("RHOHV", "ZDR", "PHIDP"):
            raw = native.field_available.get(field, np.zeros(shape, bool)) & observed
            axial = arrays[f"OS_{field}_TEXTURE_AVAILABLE_MASK"] == 1
            raw_count += raw.astype("uint8")
            axial_count += axial.astype("uint8")
            arrays[f"OS_{field}_RAW_AVAILABLE_MASK"] = raw.astype("uint8")
            arrays[f"OS_{field}_AXIAL_AVAILABLE_MASK"] = axial.astype("uint8")
        arrays["OS_POL_RAW_MOMENT_COUNT"] = raw_count
        arrays["OS_POL_AXIAL_MOMENT_COUNT"] = axial_count
        arrays["OS_POL_TEXTURE_MOMENT_COUNT"] = pol_count.copy()
    records.append(
        {
            "algorithm": "wradlib.classify.classify_echo_fuzzy",
            "version": versions["wradlib"],
            "family": "polarimetry",
            "status": "applied" if supported.any() else "unavailable",
            "score_semantics": "meteorological_membership_uncalibrated_not_probability",
            "parameters": {"weights": config.fuzzy_weights, "trpz": config.fuzzy_trapezoids},
            "minimum_real_pol_moments": config.minimum_pol_moments,
            "warnings": sorted({str(item.message) for item in caught}),
        }
    )
    records.append(
        {
            "algorithm": "open_source_evidence_total",
            "status": "applied",
            "elapsed_ms": (time.perf_counter() - started) * 1000,
        }
    )
    return EvidenceSet(arrays, tuple(records), dict(versions))
