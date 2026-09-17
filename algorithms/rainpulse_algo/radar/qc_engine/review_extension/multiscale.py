"""Raw multi-scale radial evidence. Links identify segments; they never fill gates."""
from dataclasses import dataclass
import numpy as np
from .arrays import mask, native_geometry, moment, runs
from .config import SourceReviewConfig


@dataclass(frozen=True)
class RadialEvidence:
    arrays: dict[str, np.ndarray]
    summary: dict


def _stencil(native, row, radius, gaps, good):
    n = native.shape[0]
    indices = np.arange(row - radius, row + radius + 1)
    if not native.full_ppi and (indices[0] < 0 or indices[-1] >= n):
        return None
    if len(indices) > n:
        return None
    indices %= n
    if not good[indices].all() or gaps[indices[:-1]].any():
        return None
    return indices


def multiscale_radials(native, cfg: SourceReviewConfig, *, weather=None, conflicts=None):
    shape = native.shape
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    observed &= good[:, None]
    blocked = mask(weather, shape, "weather") | mask(conflicts, shape, "conflicts")
    support = np.zeros(shape, bool)
    contrast = np.full(shape, np.nan, "float32")
    bundle = np.zeros(shape, "uint8")
    # Each side must contain ACTUAL available DBZH. Missing/undetect is not -infinity.
    for half_width in cfg.bundle_half_widths:
        radius = half_width + cfg.flank_rays
        for row in range(shape[0]):
            ix = _stencil(native, row, radius, gaps, good)
            if ix is None:
                continue
            center = ix[cfg.flank_rays:-cfg.flank_rays]
            angles = (az[center] - az[row] + 180.) % 360. - 180.
            if np.ptp(angles) > cfg.maximum_bundle_width_deg:
                continue
            valid = observed[ix].all(axis=0)
            valid &= ~blocked[center].any(axis=0)
            left = np.median(z[ix[:cfg.flank_rays]], axis=0)
            right = np.median(z[ix[-cfg.flank_rays:]], axis=0)
            delta = np.minimum(np.min(z[center], axis=0) - left, np.min(z[center], axis=0) - right)
            # A bundle must be internally consistent, not a broad rain gradient.
            valid &= np.ptp(z[center], axis=0) <= 2 * cfg.maximum_link_delta_db
            for target_row in center:
                better = valid & (~np.isfinite(contrast[target_row]) | (delta > contrast[target_row]))
                contrast[target_row, better] = delta[better]
                bundle[target_row, better] = len(center)
                support[target_row] |= valid
    score = np.where(support, np.clip(contrast / cfg.full_score_contrast_db, 0., 1.), np.nan).astype("float32")
    raw = support & (contrast >= cfg.minimum_contrast_db) & ~blocked
    fragments = []
    for row in range(shape[0]):
        for lo, hi in runs(raw[row]):
            if (hi - lo) * dr >= cfg.minimum_fragment_m:
                fragments.append((row, int(lo), int(hi)))
    ids = np.zeros(shape, "uint32")
    candidate = np.zeros(shape, bool)
    scale_bits = np.zeros(shape, "uint16")
    linked = np.zeros(shape, bool)
    scale_scores = {f"SRC_REVIEW_SCALE_{int(scale)}M_SCORE": np.where(support, 0., np.nan).astype("float32") for scale in cfg.scales_m}
    status = "measured_candidates"
    links = 0
    if len(fragments) > cfg.maximum_objects:
        status = "resource_limit_abstained"
    else:
        next_id = 0
        previous = None
        identity_start = 0
        for row, lo, hi in fragments:
            connect = False
            if previous is not None and previous[0] == row:
                _, old_lo, old_hi, old_id = previous
                gap_length = (lo - old_hi) * dr
                # Only immutable raw segments enter this graph; total span bounds transitivity.
                connect = (gap_length <= cfg.maximum_link_gap_m and (hi - identity_start) * dr <= cfg.maximum_identity_span_m
                           and not blocked[row, old_hi:lo].any()
                           and abs(float(np.median(contrast[row, old_lo:old_hi])) - float(np.median(contrast[row, lo:hi]))) <= cfg.maximum_link_delta_db)
            if connect:
                object_id = previous[3]
                linked[row, previous[1]:previous[2]] = True
                linked[row, lo:hi] = True
                links += 1
            else:
                next_id += 1
                object_id = next_id
                identity_start = lo
            ids[row, lo:hi] = object_id
            candidate[row, lo:hi] = True
            for bit, scale in enumerate(cfg.scales_m):
                if (hi - lo) * dr >= scale:
                    scale_bits[row, lo:hi] |= 1 << bit
                    scale_scores[f"SRC_REVIEW_SCALE_{int(scale)}M_SCORE"][row, lo:hi] = score[row, lo:hi]
            previous = (row, lo, hi, object_id)
    arrays = {
        "SRC_REVIEW_CANDIDATE_MASK": candidate.astype("uint8"),
        "SRC_REVIEW_SUPPORT_MASK": support.astype("uint8"),
        "SRC_REVIEW_CONTRAST_DB": contrast,
        "SRC_REVIEW_SCORE": score,
        "SRC_REVIEW_OBJECT_ID": ids,
        "SRC_REVIEW_SCALE_BITS": scale_bits,
        "SRC_REVIEW_BUNDLE_RAYS": bundle,
        "SRC_REVIEW_LINKED_SEGMENT_MASK": linked.astype("uint8"),
        **scale_scores,
    }
    return RadialEvidence(arrays, {
        "status": status, "candidate_gates": int(candidate.sum()), "raw_fragment_count": len(fragments),
        "identity_count": int(ids.max(initial=0)), "links": links, "filled_gates": 0,
        "weak_candidate_gates": int((candidate & (z <= 35)).sum()),
        "bundle_candidate_gates": int((candidate & (bundle > 1)).sum()),
        "scores_are_probabilities": False,
    })
