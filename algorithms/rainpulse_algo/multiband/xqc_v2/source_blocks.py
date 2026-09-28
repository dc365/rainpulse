"""Held-out distance-block models for intermittent radial receiver sources.

Missing REF is not no-echo and is not a failed continuity sample. Source
support is measured across distance blocks, while all actions stay on valid
native gates. Receiver SNR and REF range response are paired, not independent
votes. No station, bearing, clock time, or screenshot region is encoded here.
"""
from __future__ import annotations
import warnings
import numpy as np


def detect(s, cfg, *, protected):
    out = np.zeros(s.shape, bool)
    z, za = s.moment('DBZH'); sn, sa = s.moment('SNR')
    signal = za & sa & (sn >= cfg.noise_censor_snr_db) & (z < cfg.radial_maximum_dbzh)
    signal &= (s.ranges[None, :] >= cfg.receiver.minimum_range_m) & ~protected
    blocks = (s.ranges // cfg.receiver.block_m).astype(int)
    ids = np.unique(blocks); n = len(ids)
    law = 20*np.log10(np.maximum(s.ranges, s.dr/2)/1000)
    receiver = np.full((s.shape[0], n), np.nan)
    center_range = np.array([np.median(s.ranges[blocks == b]) for b in ids])
    for i,b in enumerate(ids):
        gate = blocks == b
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            receiver[:,i] = np.nanmedian(np.where(sa[:,gate], sn[:,gate], np.nan), axis=1)
        receiver[np.mean(sa[:,gate],axis=1)<cfg.noise_censor_minimum_coverage,i] = np.nan
    models = []
    minimum = 3  # per-block median; total reference samples still obey the source contract
    for row in np.flatnonzero(s.good):
        gates = [np.flatnonzero(signal[row] & (blocks == b)) for b in ids]
        count = np.array([len(g) for g in gates])
        if (count >= minimum).sum() < 3:
            continue
        powers = np.array([np.mean(np.percentile(sn[row,g],[25,75])) if len(g) else np.nan for g in gates])
        responses = np.array([np.mean(np.percentile(z[row,g]-law[g],[25,75])) if len(g) else np.nan for g in gates])
        # Source shoulders bound the whole angular lobe. A weaker edge ray
        # must not demand the same absolute contrast as its measured peak.
        peak = powers.copy()
        for direction in (-1, 1):
            current = row
            for step in range(1, s.shape[0]):
                other = (row + direction*step) % s.shape[0]
                edge = current if direction == 1 else other
                angle = abs(float((s.azimuth[other]-s.azimuth[row]+180)%360-180))
                if s.gap_after[edge] or not s.good[other] or angle > cfg.radial_source_maximum_width_deg/2:
                    break
                peak = np.fmax(peak, receiver[other])
                current = other
        distances = []
        for direction in (-1,1):
            nearest = np.full(n,np.inf); current = row
            for step in range(1,s.shape[0]):
                other = (row+direction*step)%s.shape[0]
                edge = current if direction == 1 else other
                angle = abs(float((s.azimuth[other]-s.azimuth[row]+180)%360-180))
                if s.gap_after[edge] or not s.good[other] or angle > cfg.radial_source_maximum_width_deg:
                    break
                quiet = (receiver[other] < cfg.noise_censor_snr_db) & (peak-receiver[other] >= cfg.radial_flank_contrast_db)
                nearest = np.where(quiet & ~np.isfinite(nearest),angle,nearest)
                current = other
            distances.append(nearest)
        geometry = distances[0]+distances[1] <= cfg.radial_source_maximum_width_deg
        for target in np.flatnonzero((count>0)&geometry):
            outside = abs(ids-ids[target]) > cfg.receiver.guard_blocks
            eligible = (count>=minimum)&geometry&outside
            seen = set()
            for seed in np.flatnonzero(eligible):
                spread = cfg.radial_source_maximum_spread_db
                members = np.flatnonzero(eligible & (abs(powers-powers[seed])<=spread) & (abs(responses-responses[seed])<=spread))
                identity = tuple(members)
                if identity in seen or len(members)<3:
                    continue
                seen.add(identity)
                ranges = center_range[members]
                if np.ptp(ranges)<cfg.radial_source_minimum_span_m or max(ranges)/max(min(ranges),s.dr)<1.75:
                    continue
                # Require a resolved response slope as well as range leverage.
                # Flat REF has -20 dB/decade residual slope and cannot qualify
                # by selecting a short far-range window with a small spread.
                slope = float(np.polyfit(np.log10(ranges), responses[members], 1)[0])
                if abs(slope) > 8.:
                    continue
                between = outside & (ids>=ids[members[0]]) & (ids<=ids[members[-1]])
                if len(members)/max(int(between.sum()),1)<cfg.radial_source_minimum_fraction:
                    continue
                train = np.concatenate([gates[i] for i in members])
                if len(train)<cfg.receiver.minimum_pair_samples:
                    continue
                power = float(np.median(powers[members])); response = float(np.median(responses[members]))
                if max(abs(powers[members]-power))>spread or max(abs(responses[members]-response))>spread:
                    continue
                # Block-center stability and an empirical gate envelope serve
                # different purposes: speckled sources need not be smooth at
                # each gate. The envelope is bounded and target-independent.
                response_limits = np.median([np.percentile(z[row,gates[i]]-law[gates[i]],[5,95]) for i in members],axis=0)
                power_limits = np.median([np.percentile(sn[row,gates[i]],[5,95]) for i in members],axis=0)
                low, high = response_limits + [-1.,1.]
                power_low, power_high = power_limits + [-1.,1.]
                g = gates[target]
                accepted = g[(sn[row,g]>=power_low)&(sn[row,g]<=power_high)&(z[row,g]-law[g]>=low)&(z[row,g]-law[g]<=high)]
                fresh = accepted[~out[row,accepted]]
                if not len(fresh):
                    continue
                out[row,fresh] = True
                models.append(dict(ray=int(row),target_block=int(ids[target]),reference_blocks=ids[members].tolist(),
                    reference_gates=len(train),target_gates=len(fresh),response_slope_db_per_decade=slope,
                    reference_span_m=float(np.ptp(ranges)),snr_center_db=power,snr_bounds_db=[float(power_low),float(power_high)],response_bounds_db=[float(low),float(high)]))
    return out, dict(method='bilateral-receiver-block-heldout-v2',source_gates=int(out.sum()),models=models,
                     missing_ref_is_continuity_failure=False,minimum_reference_range_ratio=1.75, maximum_response_slope_db_per_decade=8.)
