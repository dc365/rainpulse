"""Published phase algorithms on trusted continuous segments, without gap repair."""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np

from .adapters import NativeSweep
from .profile import OpenSourceQCProfile


def process_phase(
    native: NativeSweep,
    masks: dict[str, np.ndarray],
    profile: OpenSourceQCProfile,
    environment: dict | None = None,
) -> tuple[dict, dict]:
    import wradlib as wrl

    shape = native.shape
    phi_out = np.full(shape, np.nan, dtype="float32")
    kdp_out = np.full(shape, np.nan, dtype="float32")
    usable = np.zeros(shape, dtype="uint8")
    corrected_z = np.full(shape, np.nan, dtype="float32")
    attenuation_mask = np.zeros(shape, dtype="uint8")
    arrays = {
        "PHIDP_OS_RECONSTRUCTED": phi_out,
        "KDP_OS": kdp_out,
        "KDP_OS_AVAILABLE_MASK": usable,
        "DBZH_OS_ATTENUATION_CORRECTED": corrected_z,
        "ATTENUATION_OS_AVAILABLE_MASK": attenuation_mask,
    }
    record = {
        "method": "wradlib.dp.phidp_kdp_vulpiani",
        "version": profile.wradlib_version,
        "status": "unavailable",
        "segment_count": 0,
        "available_gates": 0,
        "phase_semantics": "reconstructed_relative_phase_per_trusted_segment",
        "attenuation_status": "disabled"
        if not profile.phase.attenuation_enabled
        else "missing_environment",
    }
    if not profile.phase.enabled:
        record.update(status="disabled")
        return arrays, record
    if "PHIDP" not in native.fields or "RHOHV" not in native.fields:
        record["reason"] = "missing_phase_or_correlation"
        return arrays, record
    actual_band = native.attrs.get("radar_band")
    if actual_band and actual_band != profile.phase.band:
        raise ValueError("phase profile band differs from source radar")
    trusted = (masks["PHIDP_TRUST_MASK"] == 1) & (masks["RHOHV_TRUST_MASK"] == 1)
    trusted &= masks["REFLECTIVITY_TRUST_MASK"] == 1
    trusted &= native.fields["RHOHV"] >= profile.phase.minimum_rhohv
    trusted &= native.fields["DBZH"] >= profile.echo.no_rain_below_dbz
    minimum = max(
        profile.phase.window_gates * 2 + 1,
        int(np.ceil(profile.phase.minimum_segment_m / native.gate_spacing_m)),
    )
    env_ok = _valid_environment(environment, native)
    if profile.phase.attenuation_enabled and env_ok:
        record["attenuation_status"] = "applied_to_trusted_segments_only"
    for ray in range(shape[0]):
        edges = np.diff(np.r_[False, trusted[ray], False].astype("int8"))
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
            if end - start < minimum:
                continue
            # Remove only a constant phase offset. No smoothing crosses a missing or rejected gate.
            raw = native.fields["PHIDP"][ray : ray + 1, start:end].astype("float64", copy=True)
            raw = np.unwrap(raw, period=profile.geometry.phase_period_deg, axis=-1)
            raw -= raw[:, :1]
            phi, kdp = wrl.dp.phidp_kdp_vulpiani(
                raw,
                native.gate_spacing_m / 1000,
                ndespeckle=5,
                winlen=profile.phase.window_gates,
                niter=profile.phase.niter,
                copy=True,
            )
            margin = profile.phase.window_gates
            inside = slice(start + margin, end - margin)
            good = np.isfinite(phi[0, margin:-margin]) & np.isfinite(kdp[0, margin:-margin])
            phi_out[ray, inside] = np.where(good, phi[0, margin:-margin], np.nan)
            kdp_out[ray, inside] = np.where(good, kdp[0, margin:-margin], np.nan)
            usable[ray, inside] = good
            record["segment_count"] += 1
            if profile.phase.attenuation_enabled and env_ok:
                _attenuation_segment(
                    native,
                    ray,
                    start,
                    end,
                    raw,
                    profile,
                    environment,
                    corrected_z,
                    attenuation_mask,
                )
    record.update(
        status="applied" if usable.any() else "unavailable", available_gates=int(usable.sum())
    )
    return arrays, record


def _valid_environment(environment: dict | None, native: NativeSweep) -> bool:
    if not environment or environment.get("verified") is not True:
        return False
    sha = environment.get("source_asset_sha256", "")
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        return False
    try:
        level = float(environment["freezing_level_m"])
        frequency = float(native.attrs["frequency_mhz"])
        altitude = float(native.attrs["antenna_altitude_m"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        np.isfinite(level)
        and 0 < level < 20000
        and np.isfinite(frequency)
        and 1000 < frequency < 20000
        and np.isfinite(altitude)
        and environment.get("vertical_datum") == "EGM2008"
        and native.attrs.get("altitude_datum") in ("EGM2008", "EPSG:3855")
    )


def _attenuation_segment(native, ray, start, end, phase, profile, environment, output, available):
    import pyart

    fields = {name: value[ray : ray + 1, start:end].copy() for name, value in native.fields.items()}
    fields["PHIDP"] = phase.astype("float32")
    segment = replace(
        native,
        ranges=native.ranges[start:end],
        azimuth=native.azimuth[ray : ray + 1],
        elevation=native.elevation[ray : ray + 1],
        ray_time=native.ray_time[ray : ray + 1],
        fields=fields,
        field_available={name: np.isfinite(value) for name, value in fields.items()},
    )
    radar = segment.to_pyart()
    radar.instrument_parameters = {
        "frequency": {"data": np.array([float(native.attrs["frequency_mhz"]) * 1e6])}
    }
    result = pyart.correct.calculate_attenuation_zphi(
        radar,
        refl_field="reflectivity",
        phidp_field="differential_phase",
        zdr_field="differential_reflectivity",
        fzl=float(environment["freezing_level_m"]),
        temp_ref="fixed_fzl",
        smooth_window_len=5,
    )
    corrected = np.asarray(np.ma.filled(result[2]["data"], np.nan))[0]
    original = fields["DBZH"][0]
    delta = corrected - original
    # Never apply a correction above the independently supplied freezing level,
    # or outside explicit correction bounds. No default C-band fallback is possible.
    heights = radar.gate_altitude["data"][0]
    good = np.isfinite(corrected) & (delta >= 0) & (delta <= profile.phase.maximum_correction_db)
    good &= heights < float(environment["freezing_level_m"])
    good &= corrected <= profile.echo.dbzh_valid_range_dbz[1]
    good[: profile.phase.window_gates] = False
    good[-profile.phase.window_gates :] = False
    output[ray, start:end] = np.where(good, corrected, np.nan)
    available[ray, start:end] = good
