from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .qpe import QPEInputError
from .qpe_profile import VPRCorrectionConfig


@dataclass(frozen=True)
class VPRCorrectionResult:
    corrected_dbzh: np.ndarray
    correction_db: np.ndarray
    uncertainty_db: np.ndarray
    applied_mask: np.ndarray
    stratiform_mask: np.ndarray
    overshoot_mask: np.ndarray
    valid_mask: np.ndarray
    qc_flags: np.ndarray
    diagnostics: dict[str, Any]


def apply_stratiform_vpr_correction(
    *,
    reflectivity: np.ndarray,
    valid_mask: np.ndarray,
    qc_flags: np.ndarray,
    beam_height: np.ndarray,
    precipitation_type: np.ndarray,
    melting_layer_bottom: np.ndarray,
    melting_layer_top: np.ndarray,
    config: VPRCorrectionConfig,
    flag_masks: dict[str, np.uint32],
) -> VPRCorrectionResult:
    shape = np.asarray(reflectivity).shape
    arrays = {
        "VALID_MASK": np.asarray(valid_mask),
        "QC_FLAGS": np.asarray(qc_flags),
        config.beam_height_field: np.asarray(beam_height),
        config.precipitation_type_field: np.asarray(precipitation_type),
        config.melting_layer_bottom_field: np.asarray(melting_layer_bottom),
        config.melting_layer_top_field: np.asarray(melting_layer_top),
    }
    for name, values in arrays.items():
        if values.shape != shape:
            raise QPEInputError(f"{name} must match DBZH_QC shape")
    if np.any((arrays["VALID_MASK"] != 0) & (arrays["VALID_MASK"] != 1)):
        raise QPEInputError("VALID_MASK must be binary")
    required_flags = {"MISSING", "CORRECTED"}
    if config.write_bright_band_flag:
        required_flags.add("BRIGHT_BAND")
    missing_flags = sorted(required_flags - flag_masks.keys())
    if missing_flags:
        raise QPEInputError(
            f"VPR correction requires QC flag masks: {', '.join(missing_flags)}"
        )

    present = arrays["VALID_MASK"] == 1
    stratiform = present & (precipitation_type == config.stratiform_code)
    invalid_stratiform = stratiform & (
        ~np.isfinite(beam_height)
        | ~np.isfinite(melting_layer_bottom)
        | ~np.isfinite(melting_layer_top)
    )
    if np.any(invalid_stratiform):
        raise QPEInputError(
            "valid stratiform VPR inputs must contain finite beam height and melting-layer fields"
        )
    invalid_thickness = stratiform & (melting_layer_top <= melting_layer_bottom)
    if np.any(invalid_thickness):
        raise QPEInputError("melting layer top must exceed bottom for stratiform VPR")

    corrected = np.asarray(reflectivity, dtype="float32").copy()
    correction = np.full(shape, np.nan, dtype="float32")
    uncertainty = np.full(shape, np.nan, dtype="float32")
    applied = np.zeros(shape, dtype="uint8")
    stratiform_mask = np.zeros(shape, dtype="uint8")
    overshoot = np.zeros(shape, dtype="uint8")
    updated_valid = arrays["VALID_MASK"].astype("uint8").copy()
    updated_flags = arrays["QC_FLAGS"].astype("uint32").copy()

    stratiform_mask[stratiform] = np.uint8(1)

    passthrough = present & ~stratiform
    correction[passthrough] = np.float32(0.0)
    uncertainty[passthrough] = np.float32(0.0)

    below = stratiform & (beam_height < melting_layer_bottom)
    correction[below] = np.float32(0.0)
    uncertainty[below] = np.float32(0.0)

    within = stratiform & (beam_height >= melting_layer_bottom) & (beam_height <= melting_layer_top)
    if np.any(within):
        thickness = melting_layer_top[within] - melting_layer_bottom[within]
        position = (beam_height[within] - melting_layer_bottom[within]) / thickness
        band_shape = 1.0 - np.abs(2.0 * position - 1.0)
        delta = -config.max_bright_band_reduction_db * band_shape
        corrected[within] = reflectivity[within] + delta.astype("float32")
        correction[within] = delta.astype("float32")
        uncertainty[within] = np.minimum(
            config.maximum_uncertainty_db,
            config.base_uncertainty_db + np.abs(delta) / 2.0,
        ).astype("float32")
        applied[within] = np.uint8(1)
        updated_flags[within] |= np.uint32(flag_masks["CORRECTED"])
        if config.write_bright_band_flag:
            updated_flags[within] |= np.uint32(flag_masks["BRIGHT_BAND"])

    above = stratiform & (beam_height > melting_layer_top)
    height_above = beam_height - melting_layer_top
    extrapolated = above & (height_above <= config.extrapolation_limit_m)
    if np.any(extrapolated):
        delta = np.minimum(
            (height_above[extrapolated] / 1000.0)
            * config.above_melting_layer_correction_db_per_km,
            config.maximum_positive_correction_db,
        )
        corrected[extrapolated] = reflectivity[extrapolated] + delta.astype("float32")
        correction[extrapolated] = delta.astype("float32")
        uncertainty[extrapolated] = np.minimum(
            config.maximum_uncertainty_db,
            config.base_uncertainty_db
            + (height_above[extrapolated] / 1000.0)
            * config.uncertainty_growth_db_per_km,
        ).astype("float32")
        applied[extrapolated] = np.uint8(1)
        updated_flags[extrapolated] |= np.uint32(flag_masks["CORRECTED"])

    overshoot_cells = above & (height_above > config.extrapolation_limit_m)
    if np.any(overshoot_cells):
        corrected[overshoot_cells] = np.nan
        uncertainty[overshoot_cells] = np.minimum(
            config.maximum_uncertainty_db,
            config.base_uncertainty_db
            + (height_above[overshoot_cells] / 1000.0)
            * config.uncertainty_growth_db_per_km,
        ).astype("float32")
        overshoot[overshoot_cells] = np.uint8(1)
        updated_valid[overshoot_cells] = np.uint8(0)
        updated_flags[overshoot_cells] |= np.uint32(flag_masks["MISSING"])

    finite_corrections = correction[present & np.isfinite(correction)]
    finite_uncertainty = uncertainty[present & np.isfinite(uncertainty)]
    diagnostics = {
        "vpr_correction_enabled": True,
        "vpr_stratiform_cell_count": int(np.count_nonzero(stratiform)),
        "vpr_corrected_cell_count": int(np.count_nonzero(applied)),
        "vpr_bright_band_cell_count": int(np.count_nonzero(within)),
        "vpr_overshoot_missing_cell_count": int(np.count_nonzero(overshoot_cells)),
        "vpr_max_correction_db": (
            float(np.max(np.abs(finite_corrections))) if finite_corrections.size else 0.0
        ),
        "vpr_mean_uncertainty_db": (
            float(np.mean(finite_uncertainty)) if finite_uncertainty.size else 0.0
        ),
    }
    return VPRCorrectionResult(
        corrected_dbzh=corrected,
        correction_db=correction,
        uncertainty_db=uncertainty,
        applied_mask=applied,
        stratiform_mask=stratiform_mask,
        overshoot_mask=overshoot,
        valid_mask=updated_valid,
        qc_flags=updated_flags,
        diagnostics=diagnostics,
    )