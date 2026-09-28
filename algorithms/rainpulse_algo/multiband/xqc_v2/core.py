"""Pure per-cut evidence. Shared S core calls; one X action owner downstream."""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntFlag
from datetime import datetime, timezone
import json
import numpy as np
from rainpulse_algo.performance import timed
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
from rainpulse_algo.radar.qc_engine.volume_review.geometry import wrap
from rainpulse_algo.radar.qc_engine.volume_review.objects import extract_objects
from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.core import evaluate as receiver_evaluate
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.features import extract as extract_features
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.engine import evaluate_volume
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.background import empty as empty_background
from .geometry import adapt, mask


class Reason(IntFlag):
    RECEIVER_FULL = 1
    RECEIVER_PARTIAL = 2
    RADIAL_POLAR = 4
    CLUTTER = 8
    ISOLATED = 16
    WEATHER_PROTECTED = 32
    GEOMETRY_UNAVAILABLE = 64
    SUPPORT_INSUFFICIENT = 128
    ACTION_BUDGET = 256
    PHASE_PATH_BLOCKED = 512
    CALIBRATION_UNKNOWN = 1024
    ATTENUATION_UNKNOWN = 2048
    BACKGROUND = 4096


@dataclass(frozen=True)
class Evidence:
    arrays: dict
    record: dict


def empty(cut, status, reason=""):
    shape = cut.fields["DBZH"].shape
    masks = ("RECEIVER", "PARTIAL", "RADIAL_OBJECT", "RADIAL_POLAR", "CLUTTER", "ISOLATED",
             "HARD_WEATHER", "LOCAL_WEATHER", "PROPOSED", "QUARANTINE", "AVAILABLE")
    a = {"XQC_" + k + "_MASK": np.zeros(shape, "uint8") for k in masks}
    a.update(XQC_REASON=np.zeros(shape, "uint32"), XQC_CLASS=np.zeros(shape, "uint8"),
             XQC_RECEIVER_MODEL_ID=np.zeros(shape, "uint32"),
             XQC_RECEIVER_RESIDUAL_DB=np.full(shape, np.nan, "float32"))
    if status != "EVALUATED":
        a["XQC_REASON"][cut.fields["OBSERVED_MASK"] == 1] |= int(Reason.SUPPORT_INSUFFICIENT)
    return Evidence(a, {"status": status, "detail": reason, "candidate_gates": 0,
                       "cross_cut_context": "NOT_BOUND_SINGLE_CUT"})


def background(s, metadata, cfg):
    from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.background import load, compare
    from rainpulse_algo.radar.qc_engine.volume_review.episode_background.data import Sample
    sid = str(metadata.get("radar_id", "")).lower()
    binding = cfg.clutter.background.assets.get(sid)
    if binding is None:
        return empty_background(s.shape, cfg.clutter), {"status": "NO_ASSET_BOUND"}
    processing = metadata.get("radar_config_version")
    if not processing:
        return empty_background(s.shape, cfg.clutter), {"status": "PROCESSING_ID_UNAVAILABLE"}
    # Missing assets / hash mismatch are explicit errors, not guessed backgrounds.
    model = load(binding.path, binding.sha256, cfg.clutter.background.maximum_asset_bytes)
    stamp = datetime.fromtimestamp(float(np.max(s.ray_time_s)), timezone.utc).isoformat()
    sample = Sample(radar_id=sid, scan_id=metadata["scan_id"], sweep_id=s.name,
        processing_id=processing, observed_at=stamp, source_sha256=metadata["asset_sha256"],
        azimuth=s.azimuth, elevation=s.elevation, ranges=s.ranges,
        fields=dict(s.fields), available=dict(s.available), geometry_good=s.good)
    arrays, record = compare(sample, model, cfg.clutter)
    return arrays, {**record, "asset_sha256": binding.sha256}


def measured_flanks(s, cfg):
    z, za = s.moment("DBZH"); sn, sa = s.moment("SNR")
    result = np.zeros(s.shape, bool)
    for row in np.flatnonzero(s.good):
        sides = []
        for direction in (-1, 1):
            supported = np.zeros(s.shape[1], bool)
            current = row
            for step in range(1, s.shape[0]):
                other = (row + direction * step) % s.shape[0]
                edge = current if direction == 1 else other
                if s.gap_after[edge] or not s.good[other]:
                    break
                if abs(float(wrap(s.azimuth[other] - s.azimuth[row]))) > cfg.radial_flank_deg:
                    break
                measured = za[row] & za[other] & sa[row] & sa[other]
                supported |= measured & (z[row] - z[other] >= cfg.radial_flank_contrast_db) & (sn[row] - sn[other] >= 6.)
                current = other
            sides.append(supported)
        result[row] = sides[0] & sides[1]
    return result


@timed("x.v2.evidence")
def evaluate_cut(cut, metadata, cfg):
    """Known resource limits abstain for this cut; unexpected errors remain errors."""
    try:
        return _evaluate(cut, metadata, cfg)
    except ResourceLimit as error:
        return empty(cut, "RESOURCE_OR_GEOMETRY_ABSTAINED", str(error))


def _evaluate(cut, metadata, cfg):
    doppler_ok = bool(cfg.doppler_verified and
        metadata.get("doppler_verification_id") == cfg.doppler_verification_id and
        metadata.get("doppler_waveform") == cfg.doppler_waveform and
        metadata.get("nyquist_velocity_mps") == cfg.nyquist_velocity_mps)
    view = adapt(cut, cfg.model_copy(update={"doppler_verified": doppler_ok}))
    s = view.sweep
    original = s.digest
    observed = s.observed
    hard = mask(cut.fields, "WEATHER_PROTECTED_MASK", cut.fields["DBZH"].shape)[view.order]
    hard |= mask(cut.fields, "MIXED_WEATHER_MASK", cut.fields["DBZH"].shape)[view.order]
    features = extract_features(s, cfg.clutter)
    local = features.arrays["CF_WEATHER_PROXY_MASK"] == 1
    result = empty(cut, "EVALUATED")
    a = result.arrays
    records = {"status": "EVALUATED", "geometry": view.report,
               "index_space": "sorted_rays; sorted_to_original_ray below",
               "sorted_to_original_ray": view.order.tolist(),
               "scores_are_probabilities": False, "module_records": {}}
    receiver = partial = radial = noisy = np.zeros(s.shape, bool)
    quarantine = np.zeros(s.shape, bool)
    why = np.zeros(s.shape, "uint32")
    why[~s.good, :] |= int(Reason.GEOMETRY_UNAVAILABLE)
    if cfg.receiver_enabled:
        ev = receiver_evaluate(s, cfg.receiver, independent_weather=hard, local_coherence=local)
        if cfg.receiver.source_family is not None:
            from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.family_validation import check_family_evidence, validate_family_records
            check_family_evidence(ev.arrays, observed, cfg.receiver)
            validate_family_records(ev.models, s, cfg.receiver)
        receiver = ev.arrays["RDR_SOURCE_MASK"] == 1
        partial = ((ev.arrays["RDR_PARTIAL_MATCH_MASK"] == 1) & ~hard & ~local &
                   (ev.arrays["RDR_TARGET_SIDE_CONFLICT_MASK"] == 0) &
                   (ev.arrays["RDR_TARGET_POLAR_CONFLICT_MASK"] == 0))
        a["XQC_RECEIVER_MODEL_ID"] = view.restore(ev.arrays["RDR_MODEL_ID"])
        a["XQC_RECEIVER_RESIDUAL_DB"] = view.restore(ev.arrays["RDR_RESIDUAL_DB"])
        records["module_records"]["receiver"] = {"summary": ev.summary, "models": ev.models}
        why[receiver] |= int(Reason.RECEIVER_FULL)
        why[partial] |= int(Reason.RECEIVER_PARTIAL)
        receiver_quarantine = receiver.copy()
        if cfg.receiver.source_family is not None and cfg.receiver.source_family.full_policy == "cr_only":
            receiver_quarantine &= ev.arrays["RDR_FAMILY_REFERENCE_MASK"] == 0
        quarantine |= receiver_quarantine
    else:
        records["module_records"]["receiver"] = {"status": "DISABLED"}
    if cfg.radial_objects_enabled:
        objects, rec, cap = extract_objects(s, cfg.objects)
        radial = objects["VOR_RADIAL_GEOMETRY_MASK"] == 1
        z, az = s.moment("DBZH"); sn, ass = s.moment("SNR")
        rho, ar = s.moment("RHOHV"); zdr, ad = s.moment("ZDR")
        _, ap = s.moment("PHIDP")
        jitter = features.arrays["CF_PHI_JITTER_DEG"]
        zdr_abnormal = ad & (abs(zdr) < cfg.clutter.maximum_abs_zdr_db) & ((zdr < -1.) | (zdr > 4.))
        polar = ar & ass & (rho <= cfg.radial_maximum_rhohv) & (sn >= cfg.radial_minimum_snr_db)
        polar &= zdr_abnormal | (ap & np.isfinite(jitter) & (jitter >= cfg.radial_phase_jitter_deg))
        noisy = (radial & polar & az & (z < cfg.radial_maximum_dbzh) &
                 ~hard & ~local & measured_flanks(s, cfg))
        why[noisy] |= int(Reason.RADIAL_POLAR)
        quarantine |= noisy
        records["module_records"]["radial_objects"] = {"capability": cap, "objects": rec,
            "qualified_gates": int(noisy.sum()), "shape_alone_actions": 0}
    else:
        records["module_records"]["radial_objects"] = {"status": "DISABLED"}
    clutter = isolated = np.zeros(s.shape, bool)
    if cfg.clutter_enabled:
        cf_cfg = cfg.clutter.model_copy(update={"isolated_objects": cfg.clutter.isolated_objects if cfg.isolation_enabled else None})
        bg = background(s, metadata, cfg)
        ev = evaluate_volume([s], cf_cfg, backgrounds=[bg], protections=[(hard, local, np.zeros(s.shape, bool))])[0]
        cf = ev.arrays
        if cfg.isolation_enabled and "CF_ISO_CANDIDATE_MASK" in cf:
            from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.isolated_objects import validate
            validate(cf, cf_cfg)
        clutter = cf["CF_NONMET_SUPPORTED_MASK"] == 1
        quarantine |= cf["CF_QUARANTINE_SUPPORTED_MASK"] == 1
        if cfg.isolation_enabled and "CF_ISO_CANDIDATE_MASK" in cf:
            isolated = cf["CF_ISO_CANDIDATE_MASK"] == 1
            quarantine |= cf["CF_ISO_QUARANTINE_CANDIDATE_MASK"] == 1
        why[clutter] |= int(Reason.CLUTTER)
        why[isolated] |= int(Reason.ISOLATED)
        why[cf["CF_BG_MATCH_MASK"] == 1] |= int(Reason.BACKGROUND)
        a["XQC_CLASS"] = view.restore(cf["CF_CLASS"])
        # Keep selected diagnostics, not every intermediate array resident.
        diagnostic_names = {"CF_Z_TEXTURE_DB", "CF_PHI_JITTER_DEG", "CF_DR_DB", "CF_BG_MATCH_MASK",
                            "CF_BG_STABLE_MASK", "CF_ISO_OBJECT_ID", "CF_ISO_STATE"}
        diagnostic_names.update(k for k in cf if k.startswith("CF_ISO_") and "WEAK" in k)
        for name in sorted(diagnostic_names):
            if name in cf:
                a["XQC_" + name[3:]] = view.restore(cf[name])
        records["module_records"]["clutter"] = ev.summary
    else:
        records["module_records"]["clutter"] = {"status": "DISABLED"}
    proposed = (receiver | partial | noisy | clutter | isolated) & observed & ~hard
    quarantine &= proposed
    why[hard] |= int(Reason.WEATHER_PROTECTED)
    for key, value in (("RECEIVER", receiver), ("PARTIAL", partial), ("RADIAL_OBJECT", radial),
        ("RADIAL_POLAR", noisy), ("CLUTTER", clutter), ("ISOLATED", isolated),
        ("HARD_WEATHER", hard), ("LOCAL_WEATHER", local), ("PROPOSED", proposed),
        ("QUARANTINE", quarantine), ("AVAILABLE", observed)):
        a["XQC_" + key + "_MASK"] = view.restore(value.astype("uint8"))
    denominator = int(observed.sum())
    if int(proposed.sum()) > cfg.maximum_new_exclusion_fraction * max(denominator, 1):
        # All proposed changes from this enhancement abstain together.
        why[proposed] |= int(Reason.ACTION_BUDGET)
        a["XQC_PROPOSED_MASK"][:] = 0; a["XQC_QUARANTINE_MASK"][:] = 0
        records["status"] = "ACTION_BUDGET_ABSTAINED"
    a["XQC_REASON"] = view.restore(why)
    records.update(candidate_gates=int(a["XQC_PROPOSED_MASK"].sum()),
                   quarantine_supported_gates=int(a["XQC_QUARANTINE_MASK"].sum()), raw_digest=original)
    if s.digest != original:
        raise RuntimeError("shared X core mutated original measurements")
    serialized = json.dumps(records, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(serialized) > cfg.maximum_evidence_bytes:
        raise ResourceLimit("X evidence record budget exceeded")
    return Evidence(a, records)
