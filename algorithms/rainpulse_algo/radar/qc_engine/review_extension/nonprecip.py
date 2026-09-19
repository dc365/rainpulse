"""Conservative rule baseline, with explicit abstention and separate evidence families.

Scores are NOT calibrated probabilities. Only configured classes may enter
experimental quarantine; fixed ground is the default action-enabled class.
"""
from dataclasses import dataclass
from enum import IntEnum, IntFlag
import numpy as np
from .arrays import mask, numeric, moment
from .physical import component_areas


class EchoClass(IntEnum):
    UNKNOWN = 0
    WEATHER = 1
    FIXED_GROUND = 2
    ANOMALOUS_PROPAGATION = 3
    SEA_CLUTTER = 4
    BIOLOGICAL = 5
    MIXED_OR_AMBIGUOUS = 6
    MISSING = 7
    BELOW_NO_RAIN_THRESHOLD = 8
    NEAR_NONMET = 9


class Family(IntFlag):
    HISTORY = 1
    DOPPLER = 2
    POLARIZATION = 4
    REFLECTIVITY_STRUCTURE = 8
    TEMPORAL = 16
    VERTICAL = 32
    MARINE_CONTEXT = 64  # scene prior, excluded from measurement-family count
    WEATHER_SUPPORT = 128


@dataclass(frozen=True)
class Classification:
    arrays: dict[str, np.ndarray]
    summary: dict


def classify(native, evidence, cfg, *, background=None, context=None, weather_support=None,
             no_rain_below_dbz=-10., strong_weather_support=.7):
    shape = native.shape
    z, obs = moment(native, "DBZH")
    obs &= native.geometry_good[:, None]
    echo = obs & (z >= no_rain_below_dbz)
    context = context or {}
    background = background or {}
    rho, arho = moment(native, "RHOHV")
    zdr, azdr = moment(native, "ZDR")
    snr, asnr = moment(native, "SNR")
    vr, avr = moment(native, "VR")
    sw, asw = moment(native, "SW")
    pol_ready = arho & azdr & asnr & (snr >= cfg.minimum_pol_snr_db)
    nonmet_pol = pol_ready & (rho <= cfg.nonmet_rhohv)
    raw_weather = pol_ready & (rho >= cfg.weather_rhohv) & (zdr >= -.5) & (zdr <= 3.5)
    ws = numeric(weather_support, shape, "weather_support", lower=0., upper=1.)
    weather = raw_weather | (np.isfinite(ws) & (ws >= strong_weather_support))
    texture = mask(evidence.get("OS_GABELLA_CANDIDATE_MASK"), shape, "gabella") | mask(evidence.get("OS_DBZH_TEXTURE_CANDIDATE_MASK"), shape, "z_texture")
    doppler = avr & asw & (abs(vr) <= cfg.maximum_abs_velocity_ms) & (sw >= 0) & (sw <= cfg.maximum_spectrum_width_ms)
    paired_available = np.zeros(shape,bool)
    if cfg.paired_doppler_enabled:
        paired_available = mask(context.get('NP_PAIRED_DOPPLER_AVAILABLE_MASK'),shape,'paired_doppler')
        pv=numeric(context.get('NP_PAIRED_VR'),shape,'paired_velocity')
        pw=numeric(context.get('NP_PAIRED_SW'),shape,'paired_width')
        age=numeric(context.get('NP_PAIRED_AGE_SECONDS'),shape,'paired_age',lower=0)
        if np.any(paired_available & (~np.isfinite(pv)|~np.isfinite(pw)|~np.isfinite(age)|(pw<0)|(age>cfg.paired_doppler_maximum_seconds))):
            raise ValueError('paired Doppler lacks measured valid support')
        doppler |= paired_available & ~(avr|asw) & (abs(pv)<=cfg.maximum_abs_velocity_ms) & (pw<=cfg.maximum_spectrum_width_ms)
    fixed = numeric(context.get("NP_FIXED_MATCH_FRACTION"), shape, "fixed_recurrence", lower=0, upper=1)
    count = numeric(context.get("NP_FIXED_SAMPLE_COUNT", np.zeros(shape)), shape, "fixed_count", lower=0, upper=3)
    if not np.isfinite(count).all() or np.any(count != np.floor(count)) or np.any((count == 0) & np.isfinite(fixed)) or np.any((count > 0) & ~np.isfinite(fixed)):
        raise ValueError("raw temporal fraction/count contract differs")
    recurrent = (count >= cfg.minimum_temporal_samples) & (fixed >= cfg.temporal_match_fraction)
    va = mask(context.get("NP_VERTICAL_NEGATIVE_AVAILABLE_MASK"), shape, "vertical_availability")
    vn = mask(context.get("NP_VERTICAL_NEGATIVE_MASK"), shape, "vertical_absence")
    if np.any(vn & ~va):
        raise ValueError("vertical absence lacks comparability")
    marine = mask(context.get("NP_VERIFIED_MARINE_MASK"), shape, "marine")
    lowbeam = mask(context.get("NP_VERIFIED_LOW_BEAM_MASK"), shape, "low_beam")
    history = np.zeros(shape, bool)
    history_available = np.zeros(shape, bool)
    hist_mean = np.full(shape, np.nan, "float32")
    history_receipt = background.get("receipt", {})
    if background and history_receipt.get("verified") is not True:
        raise ValueError("nonprecip history requires a verified asset receipt")
    if background:
        b = background["fields"]
        freq = numeric(b.get("ground_clutter"), shape, "background_frequency", lower=0, upper=1)
        lower = numeric(b.get("confidence_lower_bound"), shape, "background_lower", lower=0, upper=1)
        days = numeric(b.get("day_count"), shape, "background_days", lower=0)
        observations = numeric(b.get("observed_count"), shape, "background_count", lower=0)
        qualified = mask(b.get("qualified_mask"), shape, "background_qualified")
        hist_mean = numeric(b.get("dbzh_mean"), shape, "background_dbzh")
        history_available = qualified & np.isfinite(freq) & np.isfinite(lower) & (days >= cfg.minimum_history_days) & (observations >= cfg.minimum_history_observations) & np.isfinite(hist_mean)
        history = history_available & (freq >= cfg.minimum_history_lower_bound) & (lower >= cfg.minimum_history_lower_bound)
    enhancement = history & (z > hist_mean + cfg.maximum_history_enhancement_db)
    ids, area = component_areas(native, echo)
    strong_small = echo & (z >= cfg.strong_echo_dbz) & (area <= cfg.small_object_max_area_km2)
    weather |= strong_small
    ground = echo & history & doppler & ~enhancement & (recurrent | vn)
    ap = echo & texture & doppler & vn & nonmet_pol
    sea = echo & marine & lowbeam & texture & nonmet_pol & (vn | recurrent)
    # Biological is a candidate only without a dedicated validated temporal/context model.
    from .near_clutter import candidates
    near, near_available, near_fraction = candidates(native, cfg, texture)
    # Keep existing specific diagnoses; near_nonmet deliberately claims no cause.
    near &= echo & ~ground & ~ap & ~sea
    biological = echo & nonmet_pol & (zdr >= 3.) & (z <= 25.) & ~marine & ~history & ~near
    classes = {EchoClass.FIXED_GROUND: ground, EchoClass.ANOMALOUS_PROPAGATION: ap, EchoClass.SEA_CLUTTER: sea, EchoClass.BIOLOGICAL: biological, EchoClass.NEAR_NONMET: near}
    bits = np.zeros(shape, "uint16")
    family_count = np.zeros(shape, "uint8")
    for value, family, counted in ((history, Family.HISTORY, True), (doppler, Family.DOPPLER, True), (nonmet_pol, Family.POLARIZATION, True), (texture, Family.REFLECTIVITY_STRUCTURE, True), (recurrent, Family.TEMPORAL, True), (vn, Family.VERTICAL, True), (marine & lowbeam, Family.MARINE_CONTEXT, False), (weather, Family.WEATHER_SUPPORT, False)):
        bits[value & echo] |= int(family)
        if counted:
            family_count += (value & echo).astype("uint8")
    code = np.full(shape, EchoClass.UNKNOWN, "uint8")
    class_bits = np.zeros(shape, "uint16")
    nclass = np.zeros(shape, "uint8")
    for cls, selected in classes.items():
        code[selected] = int(cls)
        class_bits[selected] |= 1 << int(cls)
        nclass += selected
    code[weather & echo] = EchoClass.WEATHER
    mixed = echo & ((nclass > 1) | (weather & ((nclass > 0) | history)) | enhancement)
    code[mixed] = EchoClass.MIXED_OR_AMBIGUOUS
    code[obs & ~echo] = EchoClass.BELOW_NO_RAIN_THRESHOLD
    code[~obs] = EchoClass.MISSING
    allow = {getattr(EchoClass, name.upper()) for name in cfg.quarantine_classes}
    enough = (family_count >= cfg.minimum_evidence_families) | (near & (family_count >= 2))
    proposal = echo & (nclass == 1) & ~weather & ~mixed & enough & np.isin(code, [int(x) for x in allow])
    # Biology has no validated local/motion model in v1: always abstain from action.
    proposal &= code != EchoClass.BIOLOGICAL
    arrays = {
        "NP_CLASS": code, "NP_CLASS_BITS": class_bits, "NP_EVIDENCE_BITS": bits,
        "NP_EVIDENCE_FAMILY_COUNT": family_count, "NP_CANDIDATE_MASK": ((nclass > 0) & echo).astype("uint8"),
        "NP_PROPOSAL_MASK": proposal.astype("uint8"), "NP_CONFIRMED_MASK": np.zeros(shape, "uint8"),
        "NP_WEATHER_PROTECTED_MASK": (weather & echo).astype("uint8"),
        "NP_HISTORY_AVAILABLE_MASK": (history_available & obs).astype("uint8"),
        "NP_HISTORY_STRONG_MASK": (history & obs).astype("uint8"), "NP_MIXED_MASK": mixed.astype("uint8"),
        "NP_COMPONENT_ID": ids, "NP_COMPONENT_AREA_KM2": area,
        "NP_SMALL_STRONG_PROTECTED_MASK": strong_small.astype("uint8"),
        "NP_FIXED_SAMPLE_COUNT": count.astype("uint8"), "NP_FIXED_MATCH_FRACTION": fixed,
        "NP_NEAR_CANDIDATE_MASK": near.astype("uint8"),
        "NP_NEAR_AVAILABLE_MASK": near_available.astype("uint8"),
        "NP_NEAR_ABNORMAL_FRACTION": near_fraction,
    }
    return Classification(arrays, {
        "class_counts": {c.name.lower(): int((code == c).sum()) for c in EchoClass},
        "proposed_gates": int(proposal.sum()), "confirmed_gates": 0,
        "history_status": "verified" if background else "unavailable_abstained",
        "sea_scene_status": "verified_gates_present" if marine.any() else "unavailable_abstained",
        "vertical_negative_status": "verified_gates_present" if va.any() else "unavailable_abstained",
        "biological_action_status": "abstained_unvalidated_local_model",
        "motion_compensated_status": "unavailable_not_inferred",
        "independent_weather_gates": int((np.isfinite(ws) & (ws >= strong_weather_support)).sum()),
        "scores_are_probabilities": False,
        "near_candidate_gates": int(near.sum()),
        "near_available_gates": int(near_available.sum()),
        "paired_doppler_available_gates": int((paired_available&echo).sum()),
        "near_evidence_policy": "polarization_and_structure_with_measured_neighbourhood_agreement",
    })
