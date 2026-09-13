"""Wen et al. (2020) AFL formula reproduction with explicit parameter uncertainty.

Equations 2--9 and Table 2/row 8 are implemented here. Figure-4 knots are digitized
approximations. The article does not numerically specify Z_thresh or its final
score threshold; these are explicit engineering assumptions, NOT an official
SWAN implementation. No source variable is imputed or mutated.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .paper_profile import AFLConfig, Curve


@dataclass(frozen=True)
class PaperEvidence:
    arrays: dict[str, np.ndarray]
    metadata: dict


def shifted(values: np.ndarray, offset: int, fill=np.nan) -> np.ndarray:
    """out[j] = values[j+offset]; no range wrap."""
    output = np.full_like(values, fill)
    n = values.shape[1]
    if abs(offset) < n:
        if offset >= 0:
            output[:, : n - offset] = values[:, offset:]
        else:
            output[:, -offset:] = values[:, : n + offset]
    return output


def membership(values: np.ndarray, curve: Curve) -> np.ndarray:
    return np.interp(values, curve.x, curve.y).astype("float32")


def _local_tail_mean(values, valid, window, tail_fraction, required):
    """Centered fixed-width local adaptation; only complete geometric windows.

    This is AFL-local, not the paper's full-ray definition. Edges are unavailable,
    not truncated to a shorter sample with an artificially increased coverage.
    """
    n = values.shape[1]
    out = np.full(values.shape, np.nan)
    coverage = np.full(values.shape, np.nan)
    if window > n:
        return out, coverage
    half = window // 2
    count = max(1, int(np.ceil(window * tail_fraction)))
    total = np.pad(np.cumsum(np.where(valid, values, 0.0), axis=1), ((0, 0), (1, 0)))
    counts = np.pad(np.cumsum(valid, axis=1), ((0, 0), (1, 0)))
    centres = np.arange(half, n - half)
    left, right = centres - half, centres + half + 1
    tail_count = counts[:, right] - counts[:, right - count]
    numerator = total[:, right] - total[:, right - count]
    mean = np.divide(
        numerator,
        tail_count,
        out=np.full_like(numerator, np.nan),
        where=tail_count >= np.ceil(count * required),
    )
    out[:, centres] = mean
    coverage[:, centres] = (counts[:, right] - counts[:, left]) / window * 100
    return out, coverage


def afl_evidence(native, config: AFLConfig) -> PaperEvidence:
    z = np.asarray(native.fields["DBZH"], dtype="float64")
    valid = native.field_available["DBZH"] & np.isfinite(z)
    ranges_km = native.ranges / 1000.0
    valid = valid & (ranges_km[None, :] > 0)
    corrected = z - 20 * np.log10(np.maximum(ranges_km, np.finfo(float).tiny))[None, :]
    corrected = np.where(valid, corrected, np.nan)
    current = np.where(valid, z, np.nan)
    following, preceding = shifted(current, 1), shifted(current, -1)
    pair = valid & np.isfinite(following)
    triple = pair & np.isfinite(preceding)
    differences = np.where(pair, (current - following) ** 2, 0)
    spin = np.where(
        triple,
        (np.abs(current - preceding) + np.abs(following - current)) / 2 > config.spin_jump_db,
        0,
    ).astype("float64")
    # Eq.7 uses adjacent-pair squared differences, not std(Z) or variance(Z).
    texture = ndimage.convolve1d(differences, np.ones(11) / 11, axis=1, mode="constant")
    pair_count = ndimage.convolve1d(
        pair.astype("int16"), np.ones(11, "int16"), axis=1, mode="constant"
    )
    spin_count = ndimage.convolve1d(spin, np.ones(11), axis=1, mode="constant")
    triple_count = ndimage.convolve1d(
        triple.astype("int16"), np.ones(11, "int16"), axis=1, mode="constant"
    )
    texture_valid = valid & (pair_count == 11)
    spin_valid = valid & (triple_count == 11)
    rough = texture > config.rough_texture_db2
    n = z.shape[1]
    tail_n = max(1, int(np.ceil(n * config.tail_fraction)))
    tail_valid = valid[:, -tail_n:]
    tail_count = tail_valid.sum(axis=1)
    sums = np.where(tail_valid, corrected[:, -tail_n:], 0).sum(axis=1)
    tail_mean = np.divide(
        sums,
        tail_count,
        out=np.full(z.shape[0], np.nan),
        where=tail_count >= np.ceil(tail_n * config.minimum_tail_support),
    )
    coverage = np.broadcast_to(valid.mean(axis=1)[:, None] * 100, z.shape)
    local_n = max(13, int(round(config.local_window_m / native.gate_spacing_m)) | 1)
    local_mean, local_coverage = _local_tail_mean(
        corrected, valid, local_n, config.tail_fraction, config.minimum_tail_support
    )
    arrays = {
        "AFL_R_REF_PCT": np.where(valid, coverage, np.nan).astype("float32"),
        "AFL_T_DBZ_DB2": np.where(texture_valid, texture, np.nan).astype("float32"),
        "AFL_S_PIN": np.where(spin_valid, spin_count, np.nan).astype("float32"),
        "AFL_TEXTURE_AVAILABLE_MASK": texture_valid.astype("uint8"),
        "AFL_SPIN_AVAILABLE_MASK": spin_valid.astype("uint8"),
    }
    for prefix, cover, tail in (
        ("AFL", coverage, tail_mean[:, None]),
        ("AFL_LOCAL", local_coverage, local_mean),
    ):
        delta = corrected - tail  # SIGNED Eq.6, not |B - tail|.
        available = texture_valid & np.isfinite(delta) & np.isfinite(cover)
        available &= ~rough | spin_valid
        smooth_curves = (
            config.smooth_rref,
            config.smooth_db,
            config.smooth_texture,
            config.smooth_spin,
        )
        rough_curves = (config.rough_rref, config.rough_db, config.rough_texture, config.rough_spin)
        features = (cover, delta, texture, spin_count)
        scores = []
        for curves, weights in (
            (smooth_curves, config.smooth_weights),
            (rough_curves, config.rough_weights),
        ):
            score = np.zeros(z.shape, "float32")
            for feature, curve, weight in zip(features, curves, weights, strict=True):
                if weight > 0:
                    score += membership(feature, curve) * weight
            scores.append(score / sum(weights))
        result = np.where(rough, scores[1], scores[0])
        arrays[f"{prefix}_D_B_DB"] = np.where(valid, delta, np.nan).astype("float32")
        arrays[f"{prefix}_SCORE"] = np.where(available, result, np.nan).astype("float32")
        arrays[f"{prefix}_AVAILABLE_MASK"] = available.astype("uint8")
        arrays[f"{prefix}_CANDIDATE_MASK"] = (
            available & (result > config.decision_threshold)
        ).astype("uint8")
    arrays["AFL_LOCAL_R_REF_PCT"] = np.where(valid, local_coverage, np.nan).astype("float32")
    return PaperEvidence(
        arrays,
        {
            "algorithm": config.method,
            "source_doi": config.source_doi,
            "fidelity": config.fidelity,
            "score_semantics": "uncalibrated_membership",
            "status": "applied",
            "author_complete_reproduction": False,
            "parameters": config.model_dump(mode="json"),
            "local_window_gates": local_n,
            "caveats": [
                "Figure-4 knots are approximate digitizations.",
                "spin_jump_db and decision_threshold are explicit assumptions.",
                "AFL_LOCAL changes full-ray geometry and is a separate adaptation.",
                "Scores do not by themselves authorize QPE rejection.",
            ],
        },
    )
