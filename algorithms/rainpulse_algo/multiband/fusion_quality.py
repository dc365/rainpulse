# ruff: noqa: E501, I001
"""Explicit v2 admission: a corrected/display value alone is never evidence.

This module does not change an input measurement. It constructs independent
purpose masks, diagnostics and sampled-source receipts for the existing fusion
kernel. S is a peer observation, not truth; unresolved X can never erase S.
"""
from __future__ import annotations

from dataclasses import replace
from enum import IntFlag

import numpy as np

from .attenuation import PathState
from .model import Volume
from .moment_support import binary_mask, moment_support

METHOD = "quality_height_v2"
VERSION = "sx-quality-height-20260929-v2"


class InputReason(IntFlag):
    PATH_UNQUALIFIED = 1
    RADOME_UNQUALIFIED = 2
    CALIBRATION_UNQUALIFIED = 4
    QC_WITHHELD = 8
    INVALID_MEASUREMENT = 16
    MISSING_PATH_CONTRACT = 32


def prepare_source(volume, station):
    """Make purpose-specific views once per cut, outside all tile/height loops."""
    result = Volume(dict(volume.metadata), [])
    calibrated = (station.calibration_verified
                  and volume.metadata.get("calibration_id") == station.calibration_id)
    for cut in volume.sweeps:
        f = cut.fields
        shape = f["DBZH"].shape
        obs = binary_mask(f, "OBSERVED_MASK", shape)
        noecho = binary_mask(f, "NO_ECHO_MASK", shape)
        admitted = binary_mask(f, "REFLECTIVITY_ELIGIBLE_FOR_CR", shape) & obs
        quality = np.asarray(f["QUALITY_SCORE"])
        sample = np.asarray(f["DBZH_QC"])
        if sample.shape != shape or quality.shape != shape:
            raise ValueError("fusion quality/value geometry differs")
        measured = noecho | (np.isfinite(sample) & (sample >= -50) & (sample <= 100))
        reasons = np.zeros(shape, "uint16")
        reasons[~measured & obs] |= int(InputReason.INVALID_MEASUREMENT)
        hard = binary_mask(f, "CONFIRMED_NONMET_MASK", shape)
        withheld = binary_mask(f, "CR_WITHHELD_MASK", shape)
        withheld |= binary_mask(f, "XQC_WITHHELD_MASK", shape)
        withheld |= binary_mask(f, "XQC_BUDGET_WITHHELD_MASK", shape)
        if "QC_ACTION" in f:
            action = np.asarray(f["QC_ACTION"])
            if action.shape != shape or not np.isin(action, (0, 1, 2, 3)).all():
                raise ValueError("invalid QC action contract")
            # QC_ACTION 2 = reject, 3 = unresolved; never turn either into rain/no rain.
            hard |= action == 2
            withheld |= action == 3
        reasons[obs & (~admitted | withheld | hard)] |= int(InputReason.QC_WITHHELD)
        cal = binary_mask(f, "CALIBRATION_QUALIFIED_MASK", shape, default=calibrated) & calibrated
        reasons[obs & ~cal] |= int(InputReason.CALIBRATION_UNQUALIFIED)
        admitted &= measured & cal & ~hard & ~withheld & (quality > 0)
        shadow = np.zeros(shape, bool)
        if station.band == "X":
            keys = ("PATH_VALID_MASK", "PATH_STATE", "RADOME_QUALIFIED_MASK")
            if not all(k in f for k in keys):
                admitted[:] = False
                reasons[obs] |= int(InputReason.MISSING_PATH_CONTRACT)
            else:
                path = binary_mask(f, "PATH_VALID_MASK", shape)
                state = np.asarray(f["PATH_STATE"])
                if state.shape != shape or not np.isin(state, [int(s) for s in PathState]).all():
                    raise ValueError("invalid X path-state contract")
                if np.any(path & (state == PathState.UNKNOWN)):
                    raise ValueError("unknown X path cannot be marked usable")
                radome = binary_mask(f, "RADOME_QUALIFIED_MASK", shape)
                reasons[obs & ~path] |= int(InputReason.PATH_UNQUALIFIED)
                reasons[obs & ~radome] |= int(InputReason.RADOME_UNQUALIFIED)
                admitted &= path & radome
            # An explicitly censored path has no recoverable X reflectivity.
            # Carry its uncertainty even when DBZH is absent. Mere absent input
            # or "no signal" without this diagnostic is not asserted extinction.
            shadow = binary_mask(f, "ATTENUATION_UNRELIABLE_MASK", shape) & ~hard
            reasons[shadow] |= int(InputReason.PATH_UNQUALIFIED)
        unresolved = ((obs & ~admitted) | shadow) & ~hard
        ref = moment_support(f, "DBZH", shape)
        diagnostic = np.where(np.isfinite(sample), sample, ref.values)
        diagnostic = np.where(unresolved & obs & ~noecho & ref.valid
                              & (diagnostic >= -50) & (diagnostic <= 100), diagnostic, np.nan)
        fields = {**f,
                  "FUSION_ELIGIBLE_MASK": admitted.astype("uint8"),
                  "FUSION_UNRESOLVED_MASK": unresolved.astype("uint8"),
                  "FUSION_UNCERTAIN_DBZH": diagnostic.astype("float32"),
                  "FUSION_INPUT_REASON": reasons}
        view = replace(cut, fields=fields)
        view.fusion_quality = {
            "contract": VERSION,
            "admitted_gates": int(admitted.sum()),
            "unresolved_gates": int(unresolved.sum()),
            "reason_counts": {r.name: int(np.count_nonzero(reasons & int(r))) for r in InputReason},
            "attenuation_method": volume.metadata.get("attenuation_method"),
            "path_quality_version": volume.metadata.get("path_quality_version"),
            "attenuation_parameters": volume.metadata.get("attenuation_parameters"),
            "radome_status": volume.metadata.get("radome_status", "unknown"),
            "calibration_verified": calibrated,
            "path_quality": getattr(cut, "path_quality", None),
        }
        result.sweeps.append(view)
    return result


def record_sample(out, sl, footprint, fields, station, represented, eligible):
    """Column diagnostics: OR over observed height levels, never a source weight."""
    r, g = footprint.ray, footprint.gate
    within = footprint.horizontal & represented
    seen = within & ((fields["OBSERVED_MASK"][r, g] == 1)
                     | (fields["FUSION_UNRESOLVED_MASK"][r, g] == 1))
    bit = 1 if station.band == "S" else 2
    out["AVAILABLE_BAND_BITS"][sl][seen] |= np.uint8(bit)
    out["QUALIFIED_BAND_BITS"][sl][within & eligible] |= np.uint8(bit)
    unresolved = within & (fields["FUSION_UNRESOLVED_MASK"][r, g] == 1)
    out["UNRESOLVED_MASK"][sl] |= unresolved.astype("uint8")
    out["INPUT_QUALITY_REASON"][sl] |= np.where(within, fields["FUSION_INPUT_REASON"][r, g], 0).astype("uint16")
    value = fields["FUSION_UNCERTAIN_DBZH"][r, g]
    out["CR_UNCERTAIN_DBZH"][sl] = np.fmax(out["CR_UNCERTAIN_DBZH"][sl], np.where(unresolved, value, np.nan))


def finish_quality(out, meta, sources):
    winner = out["WINNER_SOURCE"]
    bands = np.zeros(winner.shape, "uint8")
    for source in sources:
        bands[winner == source["index"]] = 1 if source["band"] == "S" else 2
    out["WINNER_BAND"] = bands
    # Facts, not invented counterfactual reasons: masks apply to the column.
    out["S_SELECTED_WITH_X_AVAILABLE_MASK"] = ((bands == 1) & ((out["AVAILABLE_BAND_BITS"] & 2) != 0)).astype("uint8")
    out["X_SELECTED_WITH_S_AVAILABLE_MASK"] = ((bands == 2) & ((out["AVAILABLE_BAND_BITS"] & 1) != 0)).astype("uint8")
    meta.update(
        quality_contract=VERSION,
        method="quality_select_at_height_then_vertical_max_v2",
        standalone_correction_before_fusion=True,
        s_is_ground_truth=False,
        band_bits={"1": "S", "2": "X", "3": "S+X"},
        input_reason_bits={str(int(r)): r.name for r in InputReason},
        band_coverage_semantics="OR over represented sampled heights; not same-height overlap or weights",
        correction_dependencies="X-local; no S gate used to change X corrected reflectivity",
        s_selected_cells=int((bands == 1).sum()),
        x_selected_cells=int((bands == 2).sum()),
        unresolved_coverage_cells=int(out["UNRESOLVED_MASK"].sum()),
    )
