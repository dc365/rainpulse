"""Independent repeated-shard geometry hypotheses, without QC authority.

All members and lower parents are frozen from complete RAW. Actual nonquiet
flanks are allowed in this alternate hypothesis, never relabelled quiet.
Weather absence is not independent weather truth, so this proof is shadow only.
"""
import numpy as np


def assess(members, parents, ranges, dr, beam, parent_hold, *, angular_spacing_deg=None):
    from .fragment_constellation import short_segment_assessment
    base = short_segment_assessment(members, ranges, dr, beam, parent_hold)
    holds = [h for h in base['hold_reasons']
             if h != 'short_requires_complete_bilateral_observations']
    # Every member has already passed the complete RAW transverse PCA test.
    # A few arbitrary points on one bearing do not satisfy this hypothesis.
    if len(members) < 6 or len(parents) < 4:
        holds.append('insufficient_independent_transverse_shards')
    if base['radial_span_m'] < 20000:
        holds.append('insufficient_geometry_span')
    if base['center_basis'] != 'complete_original_edges':
        holds.append('complete_original_edges_required')
    if base['center_drift_deg'] > .1*beam:
        holds.append('geometry_center_drift')
    widths = np.array([m['angular_width_deg'] for m in members])
    if widths.min() < 1.5*beam or np.ptp(widths) > .25*beam:
        holds.append('unresolved_or_unstable_transverse_width')
    if any(m['geometry_known_fraction'] < .9 or m['geometry_flank_protected']
           for m in members):
        holds.append('unobserved_or_protected_original_flanks')
    if any(p['weather_gates'] or p['protected_gates'] for p in parents):
        holds.append('weather_or_protected_complete_parent')
    # Native angular quantization can hide the narrowing of a fixed-km ribbon.
    # Report the competing explanation rather than calling unchanged cells
    # proof of interference. Antenna blur can only widen this uncertainty.
    uncertainty=max(beam,angular_spacing_deg or beam)
    distance=np.array([(m['range_min_m']+m['range_max_m'])/2 for m in members])
    lower=float(np.max(distance*np.deg2rad(np.maximum(0,widths-uncertainty))))
    upper=float(np.min(distance*np.deg2rad(widths+uncertainty)))
    fixed_width_compatible=lower<=upper
    return dict(base, qualified=not holds, hold_reasons=holds,
                hypothesis='repeated_transverse_shards_in_fixed_native_fan',
                requires_quiet_flanks=False, nonquiet_is_not_dry=True,
                independent_original_parents=len(parents),
                original_transverse_members=len(members),
                fixed_physical_width_compatible=bool(fixed_width_compatible),
                compatible_physical_width_interval_m=(
                    [lower,upper] if fixed_width_compatible else None),
                native_width_uncertainty_deg=float(uncertainty),
                independent_weather_evidence_required=bool(fixed_width_compatible),
                production_eligible=False)
