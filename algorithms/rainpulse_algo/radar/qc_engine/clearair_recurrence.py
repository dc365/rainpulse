"""Clear-air recurrence exclusion for near-station weak clutter (v3.1).

Implements the validated 2026-10-09 research scheme as a self-contained,
side-effect-free module so the engine wiring can be added without touching
in-flight work:

  prone footprint   = clear-air background recurrence >= t_clear
                      AND dilated anchor hits in the active window < anchor_min_hits
                      AND connected region size >= min_region_gates (azimuth wrap)
  sweep exclusion   = weak [-10, 5) dBZ CR-eligible gates on prone footprints,
                      sweep elevation <= max_removed_elevation_deg, minus every
                      existing production protection; CF1 (weather-compatible)
                      gates additionally require the strict clear threshold.

Anchors are strong-rain evidence gates (DBZH >= 15 dBZ, SNR >= 15 dB,
0.97 <= RHOHV <= 1, all measured) accumulated per footprint cell over the
active time window. The footprint grid is 1-degree azimuth x 250 m gates
(az bins = 360, gate limit = 400 => 100 km), matching the background asset
format qc-clearair-recurrence-v1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy import ndimage  # type: ignore[import-untyped]

CAUSE_FLAG = "CLEARAIR_RECURRENCE_EXCLUDE"
BACKGROUND_CONTRACT = "qc-clearair-recurrence-v1"
AZ_BINS = 360
GATE_LIMIT = 400
GATE_SPACING_M = 250.0

CF_CLASS_WEATHER_COMPATIBLE = 1


@dataclass(frozen=True)
class ClearairRecurrenceParams:
    """Frozen decision parameters; defaults are the validated v3.1 values."""

    t_clear: float = 0.5
    t_clear_strict: float = 0.7
    anchor_min_hits: int = 2
    anchor_dilate_cells: int = 1
    min_region_gates: int = 8
    weak_lo_dbz: float = -10.0
    weak_hi_dbz: float = 5.0
    max_removed_elevation_deg: float = 6.0


@dataclass(frozen=True)
class ClearairBackground:
    """Parsed qc-clearair-recurrence-v1 background asset."""

    station: str
    recurrence_fraction: np.ndarray  # (360, 400) float in [0, 1], NaN = no samples
    source_volumes: int
    built_at_utc: str

    def __post_init__(self) -> None:
        arr = self.recurrence_fraction
        if arr.shape != (AZ_BINS, GATE_LIMIT):
            raise ValueError("clear-air background grid shape differs from contract")
        if arr.dtype.kind != "f":
            raise ValueError("clear-air background recurrence must be float")
        finite = np.isfinite(arr)
        if finite.any() and (arr[finite] < 0).any() and (arr[finite] > 1).any():
            raise ValueError("clear-air background recurrence out of [0, 1]")
        if self.source_volumes < 1:
            raise ValueError("clear-air background needs at least one source volume")


def parse_background(arrays: Mapping[str, np.ndarray | bytes | str]) -> ClearairBackground:
    """Parse the background asset arrays; raises on contract violations."""
    required = ("RECURRENCE_FRACTION", "STATION", "SOURCE_VOLUMES", "BUILT_AT_UTC", "CONTRACT")
    missing = [k for k in required if k not in arrays]
    if missing:
        raise ValueError(f"clear-air background missing arrays: {missing}")
    contract = arrays["CONTRACT"]
    contract = contract.decode() if isinstance(contract, bytes) else str(contract)
    if contract != BACKGROUND_CONTRACT:
        raise ValueError(f"unsupported clear-air background contract: {contract}")
    station = arrays["STATION"]
    station = station.decode() if isinstance(station, bytes) else str(station)
    built = arrays["BUILT_AT_UTC"]
    built = built.decode() if isinstance(built, bytes) else str(built)
    return ClearairBackground(
        station=station,
        recurrence_fraction=np.asarray(arrays["RECURRENCE_FRACTION"], dtype=np.float32),
        source_volumes=int(arrays["SOURCE_VOLUMES"]),
        built_at_utc=built,
    )


def anchor_mask(dbzh: np.ndarray, snr: np.ndarray | None, rhohv: np.ndarray | None,
                *, measured: np.ndarray | None = None) -> np.ndarray:
    """Strong-rain anchor gates: DBZH >= 15, SNR >= 15, 0.97 <= RHOHV <= 1."""
    dbzh = np.asarray(dbzh, dtype=float)
    anchor = np.isfinite(dbzh) & (dbzh >= 15.0)
    if snr is not None:
        snr = np.asarray(snr, dtype=float)
        anchor &= np.isfinite(snr) & (snr >= 15.0)
    if rhohv is not None:
        rhohv = np.asarray(rhohv, dtype=float)
        anchor &= np.isfinite(rhohv) & (rhohv >= 0.97) & (rhohv <= 1.0)
    if measured is not None:
        anchor &= np.asarray(measured, bool)
    return anchor


def footprint_indices(azimuth_deg: np.ndarray, n_gates: int) -> tuple[np.ndarray, int]:
    """Map rays to 1-degree footprint rows; returns (row index per ray, n rows)."""
    az = np.asarray(azimuth_deg, dtype=float) % 360.0
    rows = np.clip((az + 0.5) // 1.0, 0, AZ_BINS - 1).astype(np.int64)
    return rows, min(n_gates, GATE_LIMIT)


def accumulate_anchor_counts(volumes: Sequence[Mapping[str, np.ndarray]]) -> np.ndarray:
    """Accumulate anchor hits over the active window onto the footprint grid.

    Each volume mapping carries per sweep: 'azimuth', 'DBZH', optional
    'SNR'/'RHOHV' and optional boolean 'MEASURED'. Returns uint16 counts of
    shape (360, 400), taking the per-sweep maximum inside one volume so a
    single volume cannot double count the same footprint cell.
    """
    total = np.zeros((AZ_BINS, GATE_LIMIT), np.uint16)
    for volume in volumes:
        per_volume = np.zeros((AZ_BINS, GATE_LIMIT), bool)
        for sweep in volume.values():
            az = np.asarray(sweep["azimuth"], dtype=float)
            dbzh = np.asarray(sweep["DBZH"], dtype=float)
            n_gates = min(dbzh.shape[1], GATE_LIMIT)
            rows, _ = footprint_indices(az, n_gates)
            def opt(name):
                value = sweep.get(name)
                return value[:, :n_gates] if value is not None else None

            mask = anchor_mask(dbzh[:, :n_gates], opt("SNR"), opt("RHOHV"),
                               measured=opt("MEASURED"))
            cols = np.arange(n_gates)
            sel_rows = rows[:, None].astype(np.int64) * GATE_LIMIT + cols[None, :]
            per_volume.flat[sel_rows[mask]] = True
        total += per_volume.astype(np.uint16)
    return total


def _label_wrap(mask: np.ndarray) -> np.ndarray:
    stacked = np.vstack([mask, mask])
    labels, n = ndimage.label(stacked, structure=np.ones((3, 3), bool))
    top = labels[: mask.shape[0]]
    bottom = labels[mask.shape[0]:]
    remap = np.arange(n + 1)
    rows, cols = np.nonzero((bottom > 0) & (top == 0))
    for r, c in zip(rows, cols):
        lb = int(bottom[r, c])
        if remap[lb] == lb:
            remap[lb] = int(top[r, c]) if top[r, c] > 0 else lb
    for _ in range(3):
        remap = remap[remap]
    return remap[top]


def prone_mask(background: ClearairBackground, anchor_counts: np.ndarray,
               params: ClearairRecurrenceParams) -> tuple[np.ndarray, dict]:
    """Clutter-prone footprint mask plus diagnostics."""
    if anchor_counts.shape != (AZ_BINS, GATE_LIMIT):
        raise ValueError("anchor count grid shape differs from contract")
    anchor_free = ~ndimage.binary_dilation(
        anchor_counts >= params.anchor_min_hits,
        structure=np.ones((3, 3), bool),
        iterations=params.anchor_dilate_cells,
    )
    recurrence = background.recurrence_fraction
    recur = np.nan_to_num(recurrence, nan=0.0) >= params.t_clear
    base = recur & anchor_free
    labels = _label_wrap(base)
    sizes = np.bincount(labels.ravel())
    base = base & (sizes >= params.min_region_gates)[labels]
    diag = {
        "anchor_free": anchor_free,
        "recur": recur,
        "recur_strict": np.nan_to_num(recurrence, nan=0.0) >= params.t_clear_strict,
        "labels": labels,
    }
    return base, diag


def sweep_exclusion(*, dbzh_raw: np.ndarray, eligible: np.ndarray, protected: np.ndarray,
                    cf_class: np.ndarray, prone: np.ndarray, diag: Mapping[str, np.ndarray],
                    azimuth_deg: np.ndarray, elevation_deg: float,
                    params: ClearairRecurrenceParams) -> np.ndarray:
    """Per-sweep exclusion mask for weak clutter-prone gates (bool, frame shape)."""
    dbzh_raw = np.asarray(dbzh_raw, dtype=float)
    n_gates = min(dbzh_raw.shape[1], GATE_LIMIT)
    rows, _ = footprint_indices(azimuth_deg, n_gates)
    cols = np.arange(n_gates)
    prone_frame = prone[np.ix_(rows, cols)]
    strict_frame = np.asarray(diag["recur_strict"], bool)[np.ix_(rows, cols)]
    weak = (np.asarray(eligible, bool) & np.isfinite(dbzh_raw[:, :n_gates])
            & (dbzh_raw[:, :n_gates] >= params.weak_lo_dbz)
            & (dbzh_raw[:, :n_gates] < params.weak_hi_dbz))
    if elevation_deg > params.max_removed_elevation_deg:
        return np.zeros(dbzh_raw.shape, bool)
    candidate = weak & prone_frame & ~np.asarray(protected, bool)[:, :n_gates]
    candidate &= (np.asarray(cf_class)[:, :n_gates] != CF_CLASS_WEATHER_COMPATIBLE) | strict_frame
    result = np.zeros(dbzh_raw.shape, bool)
    result[:, :n_gates] = candidate
    return result


# ---- engine extension (wiring seam) ---------------------------------------
#
# Follows the volume_review extension pattern: rebuild each QCSweep with the
# exclusion applied to CR eligibility plus exported cause/diagnostic arrays.
# Background assets and temporal anchor volumes are injected by the caller;
# the runner stays I/O free. Absence of either input abstains without
# touching any array, so a failing enhancement never blocks the baseline.

PROTECTION_ARRAYS = (
    "CF_HARD_WEATHER_MASK",
    "CF_LOCAL_WEATHER_MASK",
    "NP_WEATHER_PROTECTED_MASK",
    "RDR_UNKNOWN_PROTECTION_MASK",
    "SRC_REVIEW_WEATHER_PROTECTED_MASK",
)


def _params_from_config(config) -> ClearairRecurrenceParams:
    return ClearairRecurrenceParams(
        t_clear=float(config.t_clear),
        t_clear_strict=float(config.t_clear_strict),
        anchor_min_hits=int(config.anchor_min_hits),
        anchor_dilate_cells=int(config.anchor_dilate_cells),
        min_region_gates=int(config.min_region_gates),
        weak_lo_dbz=float(config.weak_lo_dbz),
        weak_hi_dbz=float(config.weak_hi_dbz),
        max_removed_elevation_deg=float(config.max_removed_elevation_deg),
    )


def review_result(result, native, *, background, anchor_volumes=None):
    """Apply the clear-air recurrence exclusion to a computed QCResult.

    ``background`` is a parsed :class:`ClearairBackground` (or None) and
    ``anchor_volumes`` a sequence of per-volume, per-sweep mappings consumed
    by :func:`accumulate_anchor_counts` (or None). Missing inputs abstain.
    """
    from dataclasses import replace

    config = getattr(result.profile, "clearair_recurrence", None)
    if config is None:
        return result
    params = _params_from_config(config)
    summary = {
        "status": "APPLIED",
        "contract": BACKGROUND_CONTRACT,
        "excluded_gates": 0,
        "sweeps": {},
    }
    if background is None:
        summary["status"] = "ABSTAINED_NO_BACKGROUND"
        return replace(result, summary={**result.summary, "clearair_recurrence": summary})
    if not anchor_volumes:
        summary["status"] = "ABSTAINED_NO_ANCHOR_WINDOW"
        return replace(result, summary={**result.summary, "clearair_recurrence": summary})
    anchor_counts = accumulate_anchor_counts(anchor_volumes)
    prone, diag = prone_mask(background, anchor_counts, params)
    summary["prone_cells"] = int(prone.sum())
    summary["anchor_cells_ge_min_hits"] = int((anchor_counts >= params.anchor_min_hits).sum())

    by_name = {n.name: n for n in native}
    if set(by_name) != {s.name for s in result.sweeps}:
        raise ValueError("QC/native volume mismatch")
    updated = []
    for old in result.sweeps:
        n = by_name[old.name]
        arrays = old.optional_qc_fields
        cr = arrays.get("REFLECTIVITY_ELIGIBLE_FOR_CR")
        summary["sweeps"][old.name] = 0
        if cr is None:
            updated.append(old)
            continue
        protected = np.zeros(old.dbzh_raw.shape, bool)
        for key in PROTECTION_ARRAYS:
            value = arrays.get(key)
            if value is not None:
                protected |= np.asarray(value, bool)
        cf_class = arrays.get("CF_CLASS")
        if cf_class is None:
            cf_class = np.zeros(old.dbzh_raw.shape, np.uint8)
        exclusion = sweep_exclusion(
            dbzh_raw=old.dbzh_raw,
            eligible=np.asarray(cr) == 1,
            protected=protected,
            cf_class=cf_class,
            prone=prone,
            diag=diag,
            azimuth_deg=n.azimuth,
            elevation_deg=float(np.median(n.elevation)),
            params=params,
        )
        new_arrays = dict(arrays)
        new_arrays["CLEARAIR_RECURRENCE_EXCLUDE_MASK"] = exclusion.astype("uint8")
        if exclusion.any():
            new_arrays["CLEARAIR_RECURRENCE_BEFORE_CR_MASK"] = np.asarray(cr).copy()
            new_arrays["REFLECTIVITY_ELIGIBLE_FOR_CR"] = np.where(
                exclusion, 0, np.asarray(cr)
            ).astype("uint8")
        count = int(exclusion.sum())
        summary["sweeps"][old.name] = count
        summary["excluded_gates"] += count
        updated.append(replace(old, optional_qc_fields=new_arrays))
    return replace(result, sweeps=tuple(updated),
                   summary={**result.summary, "clearair_recurrence": summary})
