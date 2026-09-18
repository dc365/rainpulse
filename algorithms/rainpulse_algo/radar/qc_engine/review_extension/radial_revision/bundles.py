"""Stage 3: outside measured flanks + non-uniform bundles; raw identity only."""
import numpy as np
from ..arrays import native_geometry, moment, runs
from .geometry import angular_stencil, angular_span, window_fields, ResourceLimit


def bundle_candidates(native, cfg, blocked):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    observed = observed & good[:, None]
    candidate = np.zeros(native.shape, bool)
    contrast = np.full(native.shape, np.nan, "float32")
    widths = np.zeros(native.shape, "uint8")
    scales = np.zeros(native.shape, "uint16")
    for bit, scale in enumerate(cfg.scales_m):
        fraction, measured_mean = window_fields(z, observed, blocked | ~good[:, None], dr, scale)
        support = (fraction >= cfg.bundle_measured_fraction) & ~blocked & good[:, None]
        # Consider larger outside boundaries, not just the immediately adjacent
        # polluted ray. A candidate still needs a source measurement elsewhere.
        for count in range(1, cfg.bundle_maximum_rays+1, 2):
            half = count//2
            for row in range(half+1, native.shape[0]-half-1):
                center = np.arange(row-half, row+half+1)
                span = angular_span(az, center)
                if span > cfg.maximum_width_deg:
                    continue
                physical = r*np.deg2rad(span) <= cfg.maximum_width_m
                for offset in range(1, cfg.bundle_flank_search_rays+1):
                    ix = angular_stencil(native, row-half-offset, row+half+offset+1, good, gaps)
                    if ix is None:
                        continue
                    left, right = int(ix[0]), int(ix[-1])
                    measured = support[left] & support[right] & physical
                    background = np.maximum(measured_mean[left], measured_mean[right])
                    central = support[center] & (measured_mean[center] >= background[None, :] + cfg.bundle_contrast_db)
                    vote = central.sum(axis=0)/len(center) >= cfg.bundle_internal_fraction
                    admissible = measured & vote & ~blocked[center].any(axis=0)
                    for target in center:
                        delta = z[target] - background
                        match = admissible & observed[target] & ~blocked[target] & (delta >= cfg.bundle_contrast_db)
                        candidate[target] |= match
                        better = match & (~np.isfinite(contrast[target]) | (delta > contrast[target]))
                        contrast[target, better] = delta[better]
                        widths[target, better] = count
                        scales[target, match] |= 1 << bit
    # Short raw segments survive; unknown gaps remain outside every mask.
    for row in range(native.shape[0]):
        for lo, hi in runs(candidate[row]):
            if (hi-lo)*dr < cfg.minimum_fragment_m:
                candidate[row, lo:hi] = False
    contrast[~candidate] = np.nan
    widths[~candidate] = 0
    scales[~candidate] = 0
    return {"RV2_BUNDLE_MASK": candidate.astype("uint8"),
            "RV2_BUNDLE_CONTRAST_DB": contrast, "RV2_BUNDLE_RAYS": widths,
            "RV2_BUNDLE_SCALE_BITS": scales}


def fragment_identities(native, cfg, candidate, blocked):
    """Identity association never adds a candidate or source qualification.

    An observed noncandidate interval is a barrier, even without independent
    weather information. An unknown interval may be crossed for identity only.
    """
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    snr, snr_available = moment(native, "SNR")
    ids = np.zeros(native.shape, "uint32")
    linked = np.zeros(native.shape, bool)
    total, links = 0, 0
    for row in range(native.shape[0]):
        previous = None
        origin = None
        origin_signature = None
        for lo, hi in runs(candidate[row] & observed[row] & ~blocked[row]):
            measured_snr = snr[row, lo:hi][snr_available[row, lo:hi]]
            signature = float(np.median(measured_snr)) if len(measured_snr) else None
            connect = (previous is not None and signature is not None and
                       origin_signature is not None and
                       (lo-previous[1])*dr <= cfg.maximum_link_gap_m and
                       (hi-origin)*dr <= cfg.maximum_identity_span_m and
                       not blocked[row, previous[1]:lo].any() and
                       not (observed[row, previous[1]:lo] & ~candidate[row, previous[1]:lo]).any() and
                       abs(signature-origin_signature) <= cfg.maximum_link_delta_db)
            if connect:
                identity = previous[2]
                linked[row, previous[0]:previous[1]] = True
                linked[row, lo:hi] = True
                links += 1
            else:
                total += 1
                if total > cfg.maximum_objects:
                    raise ResourceLimit("fragment identity budget")
                identity = total
                origin = lo
                origin_signature = signature
            ids[row, lo:hi] = identity
            previous = (lo, hi, identity)
    return {"RV2_OBJECT_ID": ids, "RV2_LINKED_SEGMENT_MASK": linked.astype("uint8")}, {
        "identity_count": total, "identity_links": links, "filled_gates": 0}
