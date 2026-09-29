"""X-only integration: evidence before phase, explicit legacy-X action mapping.

No S enums/flags are cast into X. Uncalibrated or uncorrected measurements are
uncertain, not automatically nonmeteorological. QPE remains disabled in v2.
"""
from __future__ import annotations
from dataclasses import replace
import copy
import numpy as np
from rainpulse_algo.performance import timed
from .config import XQCConfig
from .core import evaluate_cut, Reason
from .geometry import mask
from . import VERSION


def prepare_phase(cut, blocked, cfg, base_profile):
    """Tighten actual declared masks; never infer LIQUID/initial PIA/anchor."""
    f = cut.fields
    shape = f["DBZH"].shape
    observed = mask(f, "OBSERVED_MASK", shape)
    from ..moment_support import moment_support
    phase = moment_support(f, "PHIDP", shape)
    snr = moment_support(f, "SNR", shape)
    rho = moment_support(f, "RHOHV", shape)
    ref = moment_support(f, "DBZH", shape)
    valid = mask(f, "PHASE_VALID_MASK", shape) & mask(f, "LIQUID_MASK", shape) & observed
    valid &= ref.valid & (ref.values >= -50) & (ref.values <= 100) & ~mask(f, "NO_ECHO_MASK", shape)
    valid &= ~blocked & ~mask(f, "CONFIRMED_NONMET_MASK", shape)
    valid &= ~mask(f, "ATTENUATION_UNRELIABLE_MASK", shape)
    valid &= (phase.valid & snr.valid & rho.valid &
              (snr.values >= max(cfg.phase_minimum_snr_db, base_profile.snr_min_db)) &
              (rho.values >= cfg.phase_minimum_rhohv))
    if "ZDR" in f:
        zdr = moment_support(f, "ZDR", shape)
        valid &= zdr.valid & (abs(zdr.values) < cfg.phase_maximum_abs_zdr_db)
    # Interference may contaminate a vendor phase integral too. Do not invent
    # attenuation-free restarts behind a newly unreliable measurement.
    contaminated_path = np.maximum.accumulate(blocked | mask(f, "CONFIRMED_NONMET_MASK", shape), axis=1)
    fields = dict(f)
    fields["PHASE_VALID_MASK"] = valid.astype("uint8")
    fields["ATTENUATION_UNRELIABLE_MASK"] = (
        mask(f, "ATTENUATION_UNRELIABLE_MASK", shape) | contaminated_path
    ).astype("uint8")
    return replace(cut, fields=fields), valid, contaminated_path


def baseline_candidates(cut, profile):
    from ..candidate_kernel import nonmet_candidate
    f = cut.fields
    shape = f["DBZH"].shape
    if "RHOHV" not in f or shape[1] < 3:
        return np.zeros(shape, bool)
    obs = mask(f, "OBSERVED_MASK", shape)
    no = mask(f, "NO_ECHO_MASK", shape)
    echo = obs & ~no & np.isfinite(f["DBZH"])
    snr = f.get("SNRH", f.get("SNR"))
    snr_good = np.full(shape, not profile.require_snr) if snr is None else (
        np.isfinite(snr) & (snr >= profile.snr_min_db)) | no
    return nonmet_candidate(f, echo, snr_good, profile, cut.range_m)


@timed("x.v2.pipeline")
def run(volume, station, release_sha256, *, baseline):
    from ..model import Volume
    from ..quality import Flag
    if station.band != "X":
        raise ValueError("X enhancement cannot run on an S observation")
    cfg = XQCConfig.model_validate(station.x_qc.enhancement)
    base_profile = replace(station.x_qc, enhancement=None)
    base_station = replace(station, x_qc=base_profile)
    result = None
    for cut in volume.sweeps:
        # The existing data/identity validator runs before this function.
        from .limited_context import context_for_cut
        context = context_for_cut(volume, cut, cfg)
        ev = evaluate_cut(cut, volume.metadata, cfg, context=context)
        active = cfg.mode != "audit"
        # Exceeding the deletion budget is not evidence that a candidate is clean.
        # Preserve measurements for review while excluding them from composites.
        budget_withheld = (ev.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET)) != 0
        withheld = ((ev.arrays["XQC_PROPOSED_MASK"] == 1) | budget_withheld) & active
        rejected = (ev.arrays["XQC_QUARANTINE_MASK"] == 1) & (cfg.mode == "quarantine")
        work = cut
        phase_valid = np.zeros(cut.fields["DBZH"].shape, bool)
        blocked_path = phase_valid.copy()
        if active:
            # Evidence uses immutable RAW, not a previous stage's deletion holes.
            prior_candidate = baseline_candidates(cut, base_profile)
            work, phase_valid, blocked_path = prepare_phase(cut, withheld | prior_candidate, cfg, base_profile)
        if active and "SNRH" not in work.fields and "SNR" in work.fields:
            # Some native bundles carry only the normalized SNR alias. Supply
            # the existing baseline's name without rewriting the source field.
            work = replace(work, fields={**work.fields, "SNRH": work.fields["SNR"]})
        baseline_result = baseline(Volume(copy.deepcopy(volume.metadata), [work]), base_station, release_sha256)
        if len(baseline_result.sweeps) != 1:
            raise ValueError("baseline X QC returned a different cut list")
        target = baseline_result.sweeps[0]
        f = dict(target.fields)
        # Preserve actual source masks. Effective phase masks get separate names.
        for key in ("PHASE_VALID_MASK", "ATTENUATION_UNRELIABLE_MASK"):
            if key in cut.fields:
                f[key] = cut.fields[key]
            elif key in f:
                del f[key]
        before_action = np.asarray(f["QC_ACTION"]).copy()
        before_cr = np.asarray(f["REFLECTIVITY_ELIGIBLE_FOR_CR"]).copy()
        if active:
            obs = mask(f, "OBSERVED_MASK", f["DBZH"].shape)
            rejected &= obs; withheld &= obs
            flags = np.array(f["MB_QC_FLAGS"], copy=True)
            flags[rejected] |= int(Flag.NONMET_CONFIRMED)
            flags[withheld & ~rejected] |= int(Flag.NONMET_CANDIDATE)
            action = before_action.copy()
            action[withheld & (action != 2)] = 3
            action[rejected] = 2
            from ..moment_support import moment_support
            echo_valid = moment_support(cut.fields, "DBZH", obs.shape).valid
            no_echo = mask(cut.fields, "NO_ECHO_MASK", obs.shape)
            measurement_valid = (echo_valid | no_echo) & obs
            snr = moment_support(cut.fields, "SNR", obs.shape)
            snr_valid = snr.valid | no_echo
            if snr.source is None and not base_profile.require_snr:
                snr_valid = obs.copy()
            unsupported = obs & ~(measurement_valid & snr_valid)
            flags[unsupported] |= int(Flag.INCOMPLETE_POLARIMETRY)
            action[unsupported & (action != 2)] = 3
            eligible = before_cr.astype(bool) & ~withheld & measurement_valid & snr_valid
            f.update(MB_QC_FLAGS=flags, QC_ACTION=action,
                     REFLECTIVITY_ELIGIBLE_FOR_CR=eligible.astype("uint8"),
                     QUALITY_SCORE=np.where(eligible, f["QUALITY_SCORE"], 0).astype("float32"),
                     CR_UNCERTAIN_MASK=(obs & ~eligible & (action != 2)).astype("uint8"),
                     DBZH_QC=np.where(action == 2, np.nan, f["DBZH_QC"]).astype("float32"),
                     # Candidate and budget-held gates are hidden from the QC
                     # display while raw values, action=3 and evidence remain
                     # available for review. This aligns the map with CR
                     # admission without turning a candidate into a confirmed
                     # rejection.
                     DBZH_QC_DISPLAY=np.where(
                         (action == 2) | (withheld & ~rejected),
                         np.nan,
                         f["DBZH_QC_DISPLAY"],
                     ).astype("float32"))
        f.update(ev.arrays)
        f.update(XQC_WITHHELD_MASK=withheld.astype("uint8"),
                 XQC_BUDGET_WITHHELD_MASK=(budget_withheld & active).astype("uint8"),
                 XQC_REJECTED_MASK=rejected.astype("uint8"),
                 XQC_PHASE_VALID_MASK=phase_valid.astype("uint8"),
                 XQC_PHASE_BLOCKED_MASK=blocked_path.astype("uint8"),
                 XQC_BASELINE_ACTION=before_action,
                 XQC_BASELINE_CR_ELIGIBLE_MASK=before_cr.astype("uint8"))
        # This readiness diagnostic is NOT QPE admission or an activation flag.
        ready = (np.asarray(f["REFLECTIVITY_ELIGIBLE_FOR_CR"]) == 1) & station.geometry_verified
        f["XQC_QUANTITATIVE_READY_MASK"] = ready.astype("uint8")
        f["XQC_REASON"] = f["XQC_REASON"].copy()
        f["XQC_REASON"][blocked_path] |= int(Reason.PHASE_PATH_BLOCKED)
        observed = np.asarray(f["OBSERVED_MASK"]) == 1
        if (not station.calibration_verified or volume.metadata.get("calibration_id") != station.calibration_id):
            f["XQC_REASON"][observed] |= int(Reason.CALIBRATION_UNKNOWN)
        f["XQC_REASON"][observed & ((f["MB_QC_FLAGS"] & int(Flag.ATTENUATION_UNKNOWN)) != 0)] |= int(Reason.ATTENUATION_UNKNOWN)
        target.fields = f
        # Per-cut records do not go into Volume.metadata: source-major fusion
        # demands exactly the same volume metadata for all cuts of a source.
        target.xqc_diagnostics = {**ev.record, "version": VERSION, "mode": cfg.mode,
            "parameter_sha256": cfg.digest,
            "input_asset_sha256": volume.metadata["asset_sha256"],
            "radar_id": volume.metadata["radar_id"], "scan_id": volume.metadata["scan_id"],
            "sweep_number": cut.number,
            "withheld_gates": int(withheld.sum()), "rejected_gates": int(rejected.sum()),
            "attenuation_method": base_profile.attenuation,
            "attenuation_anchor_verified": volume.metadata.get("phase_anchor_verified") is True,
            "phase_liquid_path_supplied": all(k in cut.fields for k in ("PHASE_VALID_MASK", "LIQUID_MASK")),
            "quantitative_ready_gates": int(ready.sum()), "qpe_enabled": False,
            "export_native": cfg.export_native,
            "classification_is_ground_truth": False,
            "implementation_revision": "xqc-hardening-20260929-r1"}
        if result is None:
            result = Volume(copy.deepcopy(baseline_result.metadata), [])
        result.sweeps.append(target)
        validate_output(cut, target, cfg)
    if result is None:
        raise ValueError("nonempty X volume required")
    result.metadata.update(processing=VERSION, xqc_parameter_sha256=cfg.digest,
                           xqc_mode=cfg.mode, operational_eligible=False, qpe_enabled=False,
                           xqc_implementation_revision="xqc-hardening-20260929-r1")
    return result


def validate_output(raw, qc, cfg):
    """Independent numerical/visual/admission contract, not only a PNG check."""
    from ..quality import Flag
    from ..moment_support import binary_mask
    f = qc.fields
    shape = raw.fields["DBZH"].shape
    if qc.number != raw.number:
        raise ValueError("X cut identity changed")
    for key in ("azimuth_deg", "range_m", "elevation_deg", "ray_time_epoch"):
        if not np.array_equal(getattr(raw, key), getattr(qc, key), equal_nan=True):
            raise ValueError("X native coordinate/order changed: " + key)
    for key, before in raw.fields.items():
        # These are input moments/masks, not any preexisting QC output contract.
        if key in ("DBZH", "SNR", "SNRH", "RHOHV", "ZDR", "PHIDP", "VR", "SW",
                   "OBSERVED_MASK", "NO_ECHO_MASK", "PHASE_VALID_MASK", "LIQUID_MASK",
                   "ATTENUATION_UNRELIABLE_MASK") or key.endswith(("_AVAILABLE_MASK", "_VALID_MASK")):
            if key not in f or np.asarray(f[key]).dtype != np.asarray(before).dtype or not np.array_equal(f[key], before, equal_nan=True):
                raise ValueError("X raw moment/availability changed: " + key)
    if not np.array_equal(f["DBZH_RAW"], raw.fields["DBZH"], equal_nan=True):
        raise ValueError("X enhancement changed raw reflectivity")
    for name, value in f.items():
        if name.startswith("XQC_") and np.shape(value) != shape:
            raise ValueError("X diagnostic native shape differs: " + name)
        if name.endswith("_MASK"):
            binary_mask(f, name, shape)
    observed = binary_mask(f, "OBSERVED_MASK", shape)
    rejected = binary_mask(f, "XQC_REJECTED_MASK", shape)
    withheld = binary_mask(f, "XQC_WITHHELD_MASK", shape)
    eligible = binary_mask(f, "REFLECTIVITY_ELIGIBLE_FOR_CR", shape)
    action = np.asarray(f["QC_ACTION"])
    flags = np.asarray(f["MB_QC_FLAGS"])
    if action.shape != shape or action.dtype.kind not in 'ui' or not np.isin(action, (0, 1, 2, 3)).all():
        raise ValueError("invalid X action codes")
    if flags.shape != shape or flags.dtype.kind not in 'ui':
        raise ValueError("invalid X cause flags")
    if np.any(rejected & ~withheld) or np.any(withheld & ~observed):
        raise ValueError("invalid X disposition domain")
    if np.any(eligible & (withheld | (action == 2) | ~observed)):
        raise ValueError("excluded X gate regained CR admission")
    for name in ("DBZH_QC", "DBZH_QC_DISPLAY", "QUALITY_SCORE"):
        if np.shape(f[name]) != shape:
            raise ValueError("X numerical output shape differs: " + name)
    # Baseline audit retains legacy fields exactly, including historical v1
    # rejects. Every NEW rejection must satisfy the stronger contract.
    numerical_reject = (action == 2) if cfg.mode != "audit" else rejected
    if np.any(numerical_reject & (np.isfinite(f["DBZH_QC"]) | np.isfinite(f["DBZH_QC_DISPLAY"]))):
        raise ValueError("rejected X gate remains in numerical/display QC")
    if np.any(rejected & ((action != 2) | ((flags & int(Flag.NONMET_CONFIRMED)) == 0))):
        raise ValueError("X rejection/action/cause mismatch")
    if np.any(withheld & ~rejected & (action != 2) & ((action != 3) | ((flags & int(Flag.NONMET_CANDIDATE)) == 0))):
        raise ValueError("X withholding/action/cause mismatch")
    if np.any(withheld & (np.asarray(f["XQC_REASON"]) == 0)):
        raise ValueError("X exclusion lacks a reason")
    quality = np.asarray(f["QUALITY_SCORE"])
    if not np.isfinite(quality).all() or np.any((quality < 0) | (quality > 1)) or np.any(~eligible & (quality != 0)):
        raise ValueError("X quality and eligibility disagree")
    before = binary_mask(f, "XQC_BASELINE_CR_ELIGIBLE_MASK", shape)
    if np.any(eligible & ~before):
        raise ValueError("X enhancement resurrected baseline exclusion")
    if np.any(f["QPE_ELIGIBLE_MASK"]):
        raise ValueError("candidate X release cannot enable QPE")
    if cfg.mode == "audit" and (rejected.any() or withheld.any() or not np.array_equal(action, f["XQC_BASELINE_ACTION"]) or not np.array_equal(eligible, before)):
        raise ValueError("audit caused an action")
