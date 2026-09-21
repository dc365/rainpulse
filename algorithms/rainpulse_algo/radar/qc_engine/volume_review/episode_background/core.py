"""Partial-moment background comparison, not a universal weather classifier.

A matching background does not establish zero rainfall. Current positive
nonmet evidence is required for the action-grade route. Unknown/tail moments
never become fabricated precise values; no probability is reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import numpy as np
from .builder import Background
from .config import BuildConfig, EpisodeConfig
from .data import Sample, MOMENTS, binary, map_footprints, utc, wrap


class State(IntEnum):
    MISSING = 0
    MODEL_UNAVAILABLE = 1
    DYNAMIC_OR_UNSUPPORTED = 2
    OUTSIDE_BACKGROUND = 3
    BACKGROUND_COMPATIBLE_UNDETERMINED = 4
    BACKGROUND_NONMET_SUPPORTED = 5
    WEATHER_SUPPORTED = 6
    MIXED = 7
    OUTSIDE_DOMAIN = 8


class Reason(IntFlag):
    MODEL_AVAILABLE = 1
    STABLE_CORE = 2
    BACKGROUND_MATCH = 4
    PARTIAL_MOMENTS = 8
    TAIL_STATE_MATCH = 16
    CURRENT_NONMET = 32
    HARD_WEATHER = 64
    LOCAL_WEATHER = 128
    LEGACY_PROTECTION = 256
    ENHANCEMENT = 512
    OUTSIDE_DOMAIN = 1024
    EVIDENCE_INSUFFICIENT = 2048
    MIXED = 4096
    MODEL_UNAVAILABLE = 8192
    STRONG_ECHO = 16384


MASKS = ("MODEL_AVAILABLE", "STABLE_CORE", "BACKGROUND_MATCH", "PARTIAL", "TAIL_STATE_MATCH",
         "CURRENT_NONMET", "HARD_WEATHER", "LOCAL_WEATHER", "LEGACY_PROTECTED", "STRONG",
         "ENHANCEMENT", "DOMAIN", "OBSERVED", "SUPPORTED", "MIXED", "ACTION_CANDIDATE")
FLOATS = ("MAX_DISTANCE", "DBZH_DEPARTURE_DB", "MAP_AZ_ERROR_DEG", "MAP_RANGE_ERROR_M")


@dataclass(frozen=True)
class Evidence:
    arrays: dict[str, np.ndarray]
    summary: dict


def empty_arrays(shape):
    arrays = {"EBG_" + k + "_MASK": np.zeros(shape, "uint8") for k in MASKS}
    arrays.update({"EBG_" + k: np.full(shape, np.nan, "float32") for k in FLOATS})
    arrays.update(EBG_STATE=np.zeros(shape, "uint8"), EBG_REASON=np.zeros(shape, "uint32"),
                  EBG_FEATURE_COUNT=np.zeros(shape, "uint8"), EBG_EVIDENCE_FAMILY_COUNT=np.zeros(shape, "uint8"))
    return arrays


def applicable(s: Sample, model: Background, cfg: EpisodeConfig, *, validation_holdout=False):
    m = model.metadata
    if m["radar_id"] != s.radar_id.lower():
        return "RADAR_MISMATCH"
    if m["processing_id"] != s.processing_id:
        return "PROCESSING_MISMATCH"
    rec = next((r for r in m["sweeps"] if r["sweep_id"] == s.sweep_id), None)
    if rec is None: return "SWEEP_NOT_BOUND"
    if (any(s.scan_id in r["source_scan_ids"] for r in m["sweeps"]) or
            s.source_sha256 in rec["source_sha256"]):
        raise ValueError("background/target source overlap")
    if validation_holdout:
        if cfg.mode != "audit":
            raise ValueError("in-episode holdout is audit only")
        return "IN_EPISODE_HOLDOUT"
    t, start, end = utc(s.observed_at), utc(m["start_time"]), utc(m["end_time"])
    if start <= t <= end:
        return "TARGET_IN_TRAINING_INTERVAL"
    if cfg.temporal_policy == "causal":
        if t <= end: return "FUTURE_BACKGROUND_REFUSED"
        if (t - end).total_seconds() > cfg.maximum_age_hours * 3600:
            return "BACKGROUND_TOO_OLD"
    elif t.date().isoformat() not in cfg.retrospective_target_dates:
        return "OUTSIDE_RETROSPECTIVE_DATE"
    return "APPLICABLE"


def evaluate(s: Sample, model: Background | None, cfg: EpisodeConfig, *,
             hard_weather=None, local_weather=None, legacy_protected=None,
             validation_holdout=False) -> Evidence:
    before = s.raw_digest
    shape = s.shape
    z, obs = s.moment("DBZH")
    hard = np.zeros(shape, bool) if hard_weather is None else binary(hard_weather, shape, "hard_weather")
    local = np.zeros(shape, bool) if local_weather is None else binary(local_weather, shape, "local_weather")
    prior = np.zeros(shape, bool) if legacy_protected is None else binary(legacy_protected, shape, "legacy_protected")
    a = empty_arrays(shape)
    state = a["EBG_STATE"]; state[obs] = State.MODEL_UNAVAILABLE
    a["EBG_OBSERVED_MASK"] = obs.astype("uint8")
    status = "NO_ASSET_BOUND" if model is None else applicable(s, model, cfg, validation_holdout=validation_holdout)
    summary = {"version": cfg.version, "config_sha256": cfg.digest, "status": status,
               "operational_eligible": False, "confirmed_gates": 0, "filled_gates": 0,
               "probabilities_calibrated": False, "raw_digest": before, "candidate_gates": 0}
    if status not in ("APPLICABLE", "IN_EPISODE_HOLDOUT"):
        a["EBG_REASON"][obs] |= int(Reason.MODEL_UNAVAILABLE)
        return Evidence(a, summary)
    build = BuildConfig.model_validate(model.metadata["build_config"])
    if build.no_rain_below_dbz != cfg.no_rain_below_dbz:
        raise ValueError("background and target no-rain semantics differ")
    rec = next(r for r in model.metadata["sweeps"] if r["sweep_id"] == s.sweep_id)
    p = rec["prefix"]; src = model.arrays
    rows, gates, mapped, azerr, rerr = map_footprints(src[p+"azimuth"], src[p+"elevation"], src[p+"range"],
        src[p+"geometry_good"], s.azimuth, s.elevation, s.ranges, build)
    get = lambda key: src[p + key][np.ix_(rows, gates)]
    mapped &= np.asarray(s.geometry_good, bool)[:, None]
    stable = mapped & (get("stable_measured_core") == 1)
    domain = obs & (z >= cfg.no_rain_below_dbz) & (s.ranges[None, :] >= cfg.minimum_range_m) & (s.ranges[None, :] <= cfg.maximum_range_m)
    strong = obs & (z >= cfg.protected_dbz)
    current = {k: s.moment(k) for k in MOMENTS}
    use = {}; distances = {}; feature_count = np.zeros(shape, "uint8")
    floors = {"DBZH": 2., "SNR": 2., "RHOHV": .04, "ZDR": .4, "PHIDP": 10., "VR": 1., "SW": .5}
    maximum = np.zeros(shape, float)
    for k in MOMENTS:
        v, ok = current[k]
        center, scale = get(k + "_median"), np.maximum(1.4826 * get(k + "_mad"), floors[k])
        usable = mapped & ok & np.isfinite(center) & np.isfinite(scale) & (get(k + "_n") >= cfg.minimum_feature_samples)
        if k == "ZDR": usable &= abs(v) < build.zdr_tail_db
        if k == "PHIDP": usable &= get("PHIDP_resultant") >= .6
        delta = wrap(v - center) if k == "PHIDP" else v - center
        d = np.divide(abs(delta), scale, out=np.full(shape, np.nan), where=usable)
        use[k] = usable; distances[k] = d
        maximum = np.maximum(maximum, np.where(usable, d, 0))
        feature_count += usable.astype("uint8")
    zdr, z_ok = current["ZDR"]
    tail = mapped & z_ok & (abs(zdr) >= build.zdr_tail_db)
    tail_frequency = np.where(zdr >= 0, get("zdr_tail_positive_fraction"), get("zdr_tail_negative_fraction"))
    tail_match = tail & (get("zdr_state_n") >= cfg.minimum_feature_samples) & (tail_frequency >= cfg.tail_match_fraction)
    feature_count += tail_match.astype("uint8")
    # A contradictory observed tail is a mismatch, not a dropped feature.
    tail_conflict = tail & (get("zdr_state_n") >= cfg.minimum_feature_samples) & ~tail_match
    departure = z - get("DBZH_median")
    enhanced = mapped & obs & np.isfinite(departure) & (departure > cfg.maximum_dbzh_enhancement_db)
    receiver = use["DBZH"] & use["SNR"]
    polar = use["RHOHV"] | use["ZDR"] | use["PHIDP"] | tail_match
    motion = use["VR"] & use["SW"]
    # DBZH and SNR form one family. Tail, DR/RHO/ZDR/PHIDP are not counted as
    # independent measurements of clutter merely because there are many features.
    family_count = receiver.astype("uint8") + polar.astype("uint8") + motion.astype("uint8")
    compatible = domain & stable & receiver & (maximum <= cfg.maximum_normalized_distance) & ~tail_conflict & ~enhanced
    rho, rho_ok = current["RHOHV"]; snr, snr_ok = current["SNR"]; phi, ph_ok = current["PHIDP"]
    nonmet = (compatible & use["RHOHV"] & (rho <= cfg.nonmet_rho) &
              (get("RHOHV_median") <= cfg.nonmet_rho) & snr_ok & (snr >= cfg.minimum_snr_db) & (family_count >= 2))
    # Low velocity alone is deliberately not an action path in this release.
    # A missing or questionable ZDR can be ignored without discarding measured rho.
    partial = domain & (~use["RHOHV"] | ~use["ZDR"] | ~use["PHIDP"])
    pair = np.zeros(shape, bool)
    pair[:, 1:] = ph_ok[:, 1:] & ph_ok[:, :-1] & (abs(wrap(np.diff(phi, axis=1))) <= 10.)
    local_current = (domain & rho_ok & (rho >= .97) & snr_ok & (snr >= 12.) & z_ok &
                     (zdr >= -.5) & (zdr <= 3.) & pair)
    local |= local_current
    hard &= obs; local &= obs; prior &= obs
    any_weather = hard | local | prior
    mixed = compatible & any_weather
    supported = nonmet & ~any_weather & ~strong & ~enhanced
    # Unattributed prior protection and independent weather never get overridden.
    # Known local-only conflicts can be withheld as MIXED under a separate opt-in.
    mixed_action = (mixed & local & ~hard & ~prior & ~strong & ~enhanced &
                    cfg.review_local_weather_conflicts & cfg.withhold_mixed)
    action = supported | mixed_action
    state[obs] = State.OUTSIDE_BACKGROUND
    state[obs & ~mapped] = State.MODEL_UNAVAILABLE
    state[domain & mapped & ~stable] = State.DYNAMIC_OR_UNSUPPORTED
    state[compatible] = State.BACKGROUND_COMPATIBLE_UNDETERMINED
    state[any_weather & obs & ~compatible] = State.WEATHER_SUPPORTED
    state[supported] = State.BACKGROUND_NONMET_SUPPORTED
    state[mixed] = State.MIXED
    state[obs & ~domain] = State.OUTSIDE_DOMAIN
    masks = {"MODEL_AVAILABLE": mapped & obs, "STABLE_CORE": stable & obs,
             "BACKGROUND_MATCH": compatible, "PARTIAL": partial, "TAIL_STATE_MATCH": tail_match & obs,
             "CURRENT_NONMET": nonmet, "HARD_WEATHER": hard, "LOCAL_WEATHER": local,
             "LEGACY_PROTECTED": prior, "STRONG": strong, "ENHANCEMENT": enhanced,
             "DOMAIN": domain, "OBSERVED": obs, "SUPPORTED": supported, "MIXED": mixed,
             "ACTION_CANDIDATE": action}
    for k, v in masks.items(): a["EBG_" + k + "_MASK"] = (v & obs).astype("uint8")
    a["EBG_FEATURE_COUNT"] = np.where(obs & mapped, feature_count, 0).astype("uint8")
    a["EBG_EVIDENCE_FAMILY_COUNT"] = np.where(obs & mapped, family_count, 0).astype("uint8")
    a["EBG_MAX_DISTANCE"] = np.where(mapped & obs & receiver, maximum, np.nan).astype("float32")
    a["EBG_DBZH_DEPARTURE_DB"] = np.where(mapped & obs, departure, np.nan).astype("float32")
    a["EBG_MAP_AZ_ERROR_DEG"] = np.where(mapped & obs, azerr[:, None], np.nan).astype("float32")
    a["EBG_MAP_RANGE_ERROR_M"] = np.where(mapped & obs, rerr[None, :], np.nan).astype("float32")
    for test, bit in ((mapped, Reason.MODEL_AVAILABLE), (stable, Reason.STABLE_CORE),
                      (compatible, Reason.BACKGROUND_MATCH), (partial, Reason.PARTIAL_MOMENTS),
                      (tail_match, Reason.TAIL_STATE_MATCH), (nonmet, Reason.CURRENT_NONMET),
                      (hard, Reason.HARD_WEATHER), (local, Reason.LOCAL_WEATHER),
                      (prior, Reason.LEGACY_PROTECTION), (enhanced, Reason.ENHANCEMENT),
                      (~domain, Reason.OUTSIDE_DOMAIN), (compatible & ~supported, Reason.EVIDENCE_INSUFFICIENT),
                      (mixed, Reason.MIXED), (strong, Reason.STRONG_ECHO)):
        a["EBG_REASON"][test & obs] |= int(bit)
    if s.raw_digest != before:
        raise RuntimeError("background comparison mutated raw source")
    return Evidence(a, {**summary, "candidate_gates": int(action.sum()),
        "background_compatible_gates": int(compatible.sum()), "mixed_gates": int(mixed.sum()),
        "partial_moment_supported_gates": int((supported & partial).sum()),
        "tail_supported_gates": int((supported & tail_match).sum()),
        "state_counts": {x.name: int((state == x).sum()) for x in State},
        "statistical_semantics": "prototype_compatibility_not_clutter_probability"})
