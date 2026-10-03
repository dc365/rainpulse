"""Measured enclosing receiver families, without an angular-width classifier."""

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit


def complete_geometry(s, cfg, stats, *, reference_mode="absolute_noise", details=None):
    """Find distinct first quiet shoulders; unknowns and geometry remain barriers.

    This nominates reference blocks only. It never labels an echo as a source:
    the unchanged distance-held-out receiver/range model owns that decision.
    """
    if reference_mode not in ("absolute_noise", "relative_receiver"):
        raise ValueError("unknown measured receiver-family reference mode")
    nr, nb = stats.receiver.shape
    workspace = nr * nb * (19 if details is not None else 9) + nb * 128
    expected = stats.workspace_bytes + workspace
    if expected > cfg.source_maximum_summary_bytes:
        raise ResourceLimit("complete receiver-family summary byte budget exceeded")
    result = np.zeros((nr, nb), bool)
    widths = np.zeros((nr, nb), float)
    endpoints = np.full((nr, nb, 2), -1, "int32") if details is not None else None
    states = np.full((nr, nb, 2), 4, "uint8") if details is not None else None
    known = (
        np.isfinite(stats.receiver)
        & (stats.receiver >= -50)
        & (stats.coverage >= cfg.noise_censor_minimum_coverage)
    )
    quiet = known & (stats.receiver < cfg.noise_censor_snr_db)
    for row in np.flatnonzero(s.good):
        shoulders, powers, distances = [], [], []
        peak = stats.receiver[row].copy()
        for side, direction in enumerate((-1, 1)):
            active = known[row].copy()
            if reference_mode == "absolute_noise":
                active &= ~quiet[row]
            shoulder = np.full(nb, -1, int)
            power = np.full(nb, np.nan)
            distance = np.zeros(nb)
            current, angle = row, 0.0
            if details is not None:
                states[row, ~known[row], side] = 2
                endpoints[row, ~known[row], side] = row
            for step in range(1, nr):
                if not active.any():
                    break
                stats.geometry()
                other = (row + direction * step) % nr
                edge = current if direction == 1 else other
                if s.gap_after[edge] or not s.good[other]:
                    if details is not None:
                        states[row, active, side] = 3
                        endpoints[row, active, side] = other
                    break
                # Directed distance, never the shortest arc. Families may
                # cross north but must not wrap around to their own reference.
                angle += float((s.azimuth[(edge + 1) % nr] - s.azimuth[edge]) % 360)
                if details is not None:
                    unknown = active & ~known[other]
                    states[row, unknown, side] = 2
                    endpoints[row, unknown, side] = other
                active &= known[other]
                peak[active] = np.fmax(peak[active], stats.receiver[other, active])
                lower = (
                    quiet[other]
                    if reference_mode == "absolute_noise"
                    else (
                        stats.receiver[other] <= stats.receiver[row] - cfg.radial_flank_contrast_db
                    )
                )
                found = active & lower
                shoulder[found], power[found], distance[found] = (
                    other,
                    stats.receiver[other, found],
                    angle,
                )
                if details is not None:
                    states[row, found, side] = 1
                    endpoints[row, found, side] = other
                active &= ~found
                current = other
            shoulders.append(shoulder)
            powers.append(power)
            distances.append(distance)
        width = distances[0] + distances[1]
        valid = (
            (shoulders[0] >= 0)
            & (shoulders[1] >= 0)
            & (shoulders[0] != shoulders[1])
            & (width < 360)
            & (peak - powers[0] >= cfg.radial_flank_contrast_db)
            & (peak - powers[1] >= cfg.radial_flank_contrast_db)
        )
        result[row] = valid
        widths[row, valid] = width[valid]
    measured = widths[result]
    if details is not None:
        details.update(endpoints=endpoints, states=states, workspace_bytes=workspace)
    return result, {
        "method": "complete-measured-receiver-family-v1",
        "geometry_is_contamination": False,
        "reference_mode": reference_mode,
        "below_floor_shoulders_required": reference_mode == "absolute_noise",
        "angular_width_cutoff_used": False,
        "distinct_measured_shoulders_required": True,
        "eligible_ray_blocks": int(result.sum()),
        "boundary_states": {"measured": 1, "receiver_unknown": 2,
                            "native_geometry_barrier": 3, "no_distinct_boundary": 4},
        "measured_width_deg": np.percentile(measured, [0, 50, 100]).tolist()
        if len(measured)
        else [],
    }


def source_view(cut, cfg):
    """Use the existing X measurement domain, separately from weather stencils.

    The baseline X writer admits [-50,100] dBZ; the shared weather stencil is
    limited to [-32,80]. A valid extreme receiver return must not disappear
    from source review solely because it exceeds that weather-stencil ceiling.
    No source validity bit, value, acquisition row or coordinate is repaired.
    """
    from dataclasses import replace

    from ..moment_support import moment_support
    from .geometry import adapt, mask

    if not cfg.complete_source_families_enabled:
        raise ValueError("full receiver-family support requires explicit candidate activation")
    view = adapt(cut, cfg)
    shape = cut.fields["DBZH"].shape
    support = moment_support(cut.fields, "DBZH", shape)
    valid = (
        support.valid
        & (support.values >= -50)
        & (support.values <= 100)
        & mask(cut.fields, "OBSERVED_MASK", shape)
        & ~mask(cut.fields, "NO_ECHO_MASK", shape)
    )[view.order] & view.sweep.good[:, None]
    return replace(
        view,
        sweep=replace(
            view.sweep,
            available={**view.sweep.available, "DBZH": valid},
        ),
    )
