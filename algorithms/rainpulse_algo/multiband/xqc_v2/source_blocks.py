"""Held-out distance-block models for intermittent radial receiver sources.

Missing REF is not no-echo and is not a failed continuity sample. Source
support is measured across distance blocks, while all actions stay on valid
native gates. Receiver SNR and REF range response are paired, not independent
votes. No station, bearing, clock time, or screenshot region is encoded here.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .block_percentiles import measure as block_percentiles
from .reference_fit import ReferenceFit, ReferenceFits


def detect(
    s, cfg, *, protected, fan=False, family_width_deg=None, prepared=None, response_quantile=90,
    domain=None, near_floor_references=False, target_exclusion=None, complete_family=False,
    family_reference_mode="absolute_noise",
):
    out = np.zeros(s.shape, bool)
    z, za = s.moment("DBZH")
    sn, sa = s.moment("SNR")
    # Strong source evidence is evaluated across the valid instrument range.
    # The legacy weak-spoke ceiling is not a precipitation classifier.
    maximum_width = (
        (45.0 if family_width_deg is None else family_width_deg)
        if fan
        else cfg.radial_source_maximum_width_deg
    )
    signal = za & sa & (sn >= cfg.noise_censor_snr_db)
    if near_floor_references:
        if cfg.noise_censor_snr_db is None:
            raise ValueError('near-floor reference research requires a receiver floor contract')
        # Research only: retain paired measurements on both sides of the noise
        # floor as references. This never fills a missing REF/SNR or promotes
        # below-floor measurements to targets. Keep the existing spread bound.
        signal = za & sa & (
            abs(sn - cfg.noise_censor_snr_db) <= cfg.radial_source_maximum_spread_db
        )
    if not fan:
        signal &= z < cfg.radial_maximum_dbzh
    signal &= (s.ranges[None, :] >= cfg.receiver.minimum_range_m) & ~protected
    if domain is not None:
        # A frozen RAW family limits both targets and training references.
        # The receiver shoulders still use all actually measured RAW samples;
        # outside-family measurements never become fictitious quiet receivers.
        if not isinstance(domain, np.ndarray) or domain.shape != s.shape or domain.dtype != bool:
            raise ValueError("source domain must be a boolean native-sweep matrix")
        signal &= domain
    targets = signal & (sn >= cfg.noise_censor_snr_db)
    if target_exclusion is not None:
        if (not (near_floor_references or fan) or not isinstance(target_exclusion, np.ndarray)
                or target_exclusion.shape != s.shape or target_exclusion.dtype != bool):
            raise ValueError(
                'prior proven target exclusion requires a boolean source-fan sweep mask'
            )
        # This affects action targets only. Previously proved source gates
        # remain RAW references for every independently held-out target.
        targets &= ~target_exclusion
    from .source_summary import SourceStatistics

    stats = (prepared or SourceStatistics.build(s, cfg)).use(s, cfg)
    family_geometry, family_record = None, None
    family_details = {}
    if complete_family:
        if not fan or near_floor_references or family_width_deg is not None:
            raise ValueError("complete families require the ordinary full receiver-fan model")
        from .source_family_geometry import complete_geometry
        family_geometry, family_record = complete_geometry(
            s, cfg, stats,
            reference_mode=("relative_receiver" if family_reference_mode == "heldout_family"
                            else family_reference_mode),
            details=family_details if family_reference_mode != "absolute_noise" else None,
        )
    elif family_reference_mode != "absolute_noise":
        raise ValueError("relative receiver references require complete measured families")
    ids, law = stats.ids, stats.law
    n = len(ids)
    receiver, center_range = stats.receiver, stats.center_range
    models = []
    minimum = 3  # per-block median; total reference samples still obey the source contract
    rows = s.good if domain is None else s.good & np.any(signal, axis=1)
    # Empty domain rows previously built a block list only to skip it.
    # They consume no model/geometry counters and cannot alter decisions.
    for row in np.flatnonzero(rows):
        # Release the previous ray's cache before admitting temporary batches
        # against the same summary ceiling. Fits never cross RAW ray identity.
        fits = None
        gates = [g[signal[row, g]] for g in stats.indices]
        count = np.array([len(g) for g in gates])
        if (count >= minimum).sum() < 3:
            continue
        # REF upper/primary modes must use the corresponding receiver mode.
        # Averaging SNR quartiles can mix a changing lower mode with a stable
        # upper source, falsely rejecting its held-out stationary power.
        temporary_allowance = max(
            0, cfg.source_maximum_summary_bytes - stats.workspace_bytes
            - family_details.get("workspace_bytes", 0),
        )
        quantiles = response_quantile if fan else [25, 75]
        powers = block_percentiles(
            sn[row], gates, quantiles, maximum_bytes=temporary_allowance,
        )
        responses = block_percentiles(
            z[row], gates, quantiles, maximum_bytes=temporary_allowance, offsets=(law,),
        )
        if not fan:
            powers = np.mean(powers, axis=1)
            responses = np.mean(responses, axis=1)
        if family_geometry is not None:
            geometry = family_geometry[row]
        else:
            # Source shoulders bound the whole angular lobe. A weaker edge ray
            # must not demand the same absolute contrast as its measured peak.
            peak = powers.copy()
            for direction in (-1, 1):
                current = row
                for step in range(1, s.shape[0]):
                    stats.geometry()
                    other = (row + direction * step) % s.shape[0]
                    edge = current if direction == 1 else other
                    angle = abs(float((s.azimuth[other] - s.azimuth[row] + 180) % 360 - 180))
                    if s.gap_after[edge] or not s.good[other] or angle > maximum_width / 2:
                        break
                    peak = np.fmax(peak, receiver[other])
                    current = other
            distances = []
            for direction in (-1, 1):
                nearest = np.full(n, np.inf)
                current = row
                for step in range(1, s.shape[0]):
                    stats.geometry()
                    other = (row + direction * step) % s.shape[0]
                    edge = current if direction == 1 else other
                    angle = abs(float((s.azimuth[other] - s.azimuth[row] + 180) % 360 - 180))
                    if s.gap_after[edge] or not s.good[other] or angle > maximum_width:
                        break
                    quiet = (receiver[other] < cfg.noise_censor_snr_db) & (
                        peak - receiver[other] >= cfg.radial_flank_contrast_db
                    )
                    nearest = np.where(quiet & ~np.isfinite(nearest), angle, nearest)
                    current = other
                distances.append(nearest)
            geometry = distances[0] + distances[1] <= maximum_width
            # Seed membership is a raw-ray statistic, independent of the held-out
            # target. Compute it once; target/guard blocks are still removed below.
            # Previously every duplicate seed consumed a model trial before dedup,
            # exhausting low-elevation, long-range cuts without fitting a model.
        spread = cfg.radial_source_maximum_spread_db
        reference = (count >= minimum) & geometry
        membership = (
            reference[None, :]
            & (abs(powers[:, None] - powers[None, :]) <= (1.0 if fan else spread))
            & (abs(responses[:, None] - responses[None, :]) <= spread)
        )
        fits = ReferenceFits(
            stats, cfg.source_maximum_summary_bytes - stats.workspace_bytes
            - family_details.get("workspace_bytes", 0),
        )
        target_blocks = count > 0
        if near_floor_references or target_exclusion is not None:
            # Below-floor blocks remain measured training references. A block
            # with no eligible target cannot produce an action or a model
            # record, so do not spend held-out fitting budget on it.
            target_blocks = np.array([targets[row, g].any() for g in stats.indices])
        unknown_target = np.zeros(n, bool)
        if family_details:
            states = family_details['states'][row]
            endpoints = family_details['endpoints'][row]
            # A target with one measured side may be predicted only if the
            # exact first unknown receiver is a measured shoulder in held-out
            # original blocks. Neither an interior hole nor two unknown sides
            # supplies a family. This remains candidate evidence downstream.
            unknown_target = (states == 1).sum(axis=1) == 1
            unknown_target &= (states == 2).sum(axis=1) == 1
            if family_reference_mode == "heldout_family":
                # The whole RAW corridor supplies its identity in independent
                # original distance blocks. Predict only unknown telemetry;
                # known opposing boundaries and native barriers still refuse.
                unknown_target = (states == 2).any(axis=1)
                unknown_target &= np.all(np.isin(states, (1, 2)), axis=1)
        for target in np.flatnonzero(target_blocks & (geometry | unknown_target)):
            target_gates = gates[target]
            if near_floor_references or target_exclusion is not None:
                target_gates = target_gates[targets[row, target_gates]]
            outside = abs(ids - ids[target]) > cfg.receiver.guard_blocks
            eligible = (count >= minimum) & geometry & outside
            same_boundary = np.ones(n, bool)
            unknown_reference = np.zeros(n, bool)
            if family_details:
                same_boundary = np.all(endpoints == endpoints[target], axis=1)
                unknown_reference = same_boundary & ((states == 2).sum(axis=1) == 1)
                unknown_reference &= (states == 1).sum(axis=1) == 1
                if family_reference_mode == "heldout_family":
                    unknown_reference = (states == 2).any(axis=1)
                    unknown_reference &= np.all(np.isin(states, (1, 2)), axis=1)
                elif unknown_target[target]:
                    eligible &= same_boundary
            seen = set()
            for seed in np.flatnonzero(eligible):
                # Accepted gates already carry their first complete proof.
                # Additional models cannot add a gate or evidence record once
                # every eligible target gate is covered.
                if out[row, target_gates].all():
                    break
                stats.seed_comparisons += 1
                members = np.flatnonzero(membership[seed] & outside)
                if unknown_target[target] and family_reference_mode != "heldout_family":
                    members = members[same_boundary[members]]
                identity = tuple(members)
                if identity in seen or len(members) < 3:
                    continue
                seen.add(identity)
                # Coverage is determined by RAW membership, independent of
                # the fitted coefficients. Reject it before spending a fit.
                between = outside & (ids >= ids[members[0]]) & (ids <= ids[members[-1]])
                # Missing telemetry on an independently measured same-family
                # shoulder is unknown, not a failed source continuity sample.
                # Known competing boundaries and native gaps still count.
                between &= ~unknown_reference
                if len(members) / max(int(between.sum()), 1) < cfg.radial_source_minimum_fraction:
                    continue
                # Keys describe the actual post-target/guard reference blocks.
                # The cache is recreated for each RAW ray in this detector call,
                # so RAW domain, quantile and all configuration remain fixed.
                key = np.asarray(members, dtype='<i8').tobytes()
                fit = fits.get(key, lambda: _reference_fit(
                    s, cfg, members, center_range, count, powers, responses, fan, spread
                ))
                if fit is None:
                    continue
                slope, power = fit.slope, fit.power
                trend = (slope * np.log10(np.maximum(s.ranges, s.dr) / 1000)
                         if fan else np.zeros_like(s.ranges))
                if fit.bounds is None:
                    reference_blocks = tuple(gates[i] for i in members)
                    allowance = max(0, fits.maximum_bytes - fits.bytes)
                    response_limits = np.median(block_percentiles(
                        z[row], reference_blocks, [5, 95], maximum_bytes=allowance,
                        offsets=(law, trend),
                    ), axis=0)
                    power_limits = np.median(block_percentiles(
                        sn[row], reference_blocks, [5, 95], maximum_bytes=allowance,
                    ), axis=0)
                    low, high = response_limits + [-1.0, 1.0]
                    power_low, power_high = power_limits + [-1.0, 1.0]
                    fit = replace(
                        fit, bounds=(float(low), float(high), float(power_low), float(power_high))
                    )
                    fits.put(key, fit)
                low, high, power_low, power_high = fit.bounds
                g = target_gates
                response_gate = z[row, g] - law[g] - trend[g]
                # Once REF and independent receiver power establish a source,
                # a REF-only dropout does not make that measurement clean.
                # Stronger unexpected echo and changed receiver power remain.
                lower_ok = np.ones(len(g), bool) if fan else response_gate >= low
                accepted = g[
                    (sn[row, g] >= power_low)
                    & (sn[row, g] <= power_high)
                    & lower_ok
                    & (response_gate <= high)
                ]
                fresh = accepted[~out[row, accepted]]
                if not len(fresh):
                    continue
                out[row, fresh] = True
                stats.model()
                models.append(
                    dict(
                        ray=int(row),
                        target_block=int(ids[target]),
                        reference_blocks=ids[members].tolist(),
                        reference_gates=fit.reference_gates,
                        target_gates=len(fresh),
                        response_quantile=response_quantile if fan else None,
                        response_slope_db_per_decade=slope,
                        response_bounds_detrended=bool(fan),
                        trend_reference_range_m=1000.0,
                        response_lower_bound_applied=not fan,
                        reference_span_m=fit.reference_span,
                        snr_center_db=power,
                        snr_bounds_db=[float(power_low), float(power_high)],
                        response_bounds_db=[float(low), float(high)],
                        **({'target_boundary_unknown': bool(unknown_target[target]),
                            'target_measured_or_predicted_shoulders': endpoints[target].tolist(),
                            'heldout_boundary_match_required': (
                                bool(unknown_target[target])
                                and family_reference_mode != "heldout_family"
                            ),
                            'boundary_nomination_mode': family_reference_mode,
                            'unknown_reference_blocks': ids[
                                unknown_reference & outside
                                & (ids >= ids[members[0]]) & (ids <= ids[members[-1]])
                            ].tolist(),
                            'missing_receiver_filled': False,
                            'boundary_prediction_is_confirmed': False}
                           if family_details else {}),
                    )
                )
    return out, dict(
        method="bilateral-receiver-block-heldout-v3",
        receiver_power_quantile=response_quantile if fan else None,
        source_gates=int(out.sum()),
        models=models,
        missing_ref_is_continuity_failure=False,
        minimum_reference_range_ratio=1.75,
        maximum_response_slope_db_per_decade=8.0,
        **({'complete_family_geometry': family_record} if complete_family else {}),
        **({'diagnostic_only': True, 'near_floor_references': True,
            'below_floor_targets': False} if near_floor_references else {}),
        **({'prior_proven_target_exclusion_gates': int(target_exclusion.sum())}
           if target_exclusion is not None else {}),
    )


def _reference_fit(s, cfg, members, center_range, count, powers, responses, fan, spread):
    ranges = center_range[members]
    span = float(np.ptp(ranges))
    if span < cfg.radial_source_minimum_span_m or max(ranges) / max(min(ranges), s.dr) < 1.75:
        return None
    slope = float(np.polyfit(np.log10(ranges), responses[members], 1)[0])
    if abs(slope) > 8.0:
        return None
    if fan and abs(float(np.polyfit(np.log10(ranges), powers[members], 1)[0])) > 2.0:
        return None
    samples = int(count[members].sum())
    if samples < cfg.receiver.minimum_pair_samples:
        return None
    power, response = float(np.median(powers[members])), float(np.median(responses[members]))
    if (
        max(abs(powers[members] - power)) > spread
        or max(abs(responses[members] - response)) > spread
    ):
        return None
    return ReferenceFit(slope, power, samples, span)
