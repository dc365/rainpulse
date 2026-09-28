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
    phase = f.get("PHIDP")
    snr = f.get("SNRH", f.get("SNR"))
    rho = f.get("RHOHV")
    valid = mask(f, "PHASE_VALID_MASK", shape) & mask(f, "LIQUID_MASK", shape) & observed
    valid &= ~blocked & ~mask(f, "CONFIRMED_NONMET_MASK", shape)
    valid &= ~mask(f, "ATTENUATION_UNRELIABLE_MASK", shape)
    if phase is None or snr is None or rho is None:
        valid[:] = False
    else:
        valid &= (np.isfinite(phase) & np.isfinite(snr) & np.isfinite(rho) &
                  (snr >= max(cfg.phase_minimum_snr_db, base_profile.snr_min_db)) &
                  (rho >= cfg.phase_minimum_rhohv) & (rho <= 1))
        for key in ("PHIDP_AVAILABLE_MASK", "RHOHV_AVAILABLE_MASK", "SNR_AVAILABLE_MASK", "SNRH_AVAILABLE_MASK"):
            if key in f:
                valid &= mask(f, key, shape)
    if "ZDR" in f:
        zdr = f["ZDR"]
        valid &= np.isfinite(zdr) & (abs(zdr) < cfg.phase_maximum_abs_zdr_db)
        if "ZDR_AVAILABLE_MASK" in f:
            valid &= mask(f, "ZDR_AVAILABLE_MASK", shape)
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
        ev = evaluate_cut(cut, volume.metadata, cfg)
        active = cfg.mode != "audit"
        withheld = (ev.arrays["XQC_PROPOSED_MASK"] == 1) & active
        rejected = (ev.arrays["XQC_QUARANTINE_MASK"] == 1) & (cfg.mode == "quarantine")
        work = cut
        phase_valid = np.zeros(cut.fields["DBZH"].shape, bool)
        blocked_path = phase_valid.copy()
        if active:
            # Evidence uses immutable RAW, not a previous stage's deletion holes.
            prior_candidate = baseline_candidates(cut, base_profile)
            work, phase_valid, blocked_path = prepare_phase(cut, withheld | prior_candidate, cfg, base_profile)
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
            eligible = before_cr.astype(bool) & ~withheld
            f.update(MB_QC_FLAGS=flags, QC_ACTION=action,
                     REFLECTIVITY_ELIGIBLE_FOR_CR=eligible.astype("uint8"),
                     QUALITY_SCORE=np.where(eligible, f["QUALITY_SCORE"], 0).astype("float32"),
                     CR_UNCERTAIN_MASK=(obs & ~eligible & (action != 2)).astype("uint8"),
                     DBZH_QC=np.where(rejected, np.nan, f["DBZH_QC"]).astype("float32"),
                     DBZH_QC_DISPLAY=np.where(action == 2, np.nan, f["DBZH_QC_DISPLAY"]).astype("float32"))
        f.update(ev.arrays)
        f.update(XQC_WITHHELD_MASK=withheld.astype("uint8"),
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
            "classification_is_ground_truth": False}
        if result is None:
            result = Volume(copy.deepcopy(baseline_result.metadata), [])
        result.sweeps.append(target)
        validate_output(cut, target, cfg)
    if result is None:
        raise ValueError("nonempty X volume required")
    result.metadata.update(processing=VERSION, xqc_parameter_sha256=cfg.digest,
                           xqc_mode=cfg.mode, operational_eligible=False, qpe_enabled=False)
    return result


def validate_output(raw, qc, cfg):
    """Gate contract consumed by both numerical and visual X products."""
    f = qc.fields
    if not np.array_equal(f["DBZH_RAW"], raw.fields["DBZH"], equal_nan=True):
        raise ValueError("X enhancement changed the raw reflectivity")
    for name, old in raw.fields.items():
        if name.endswith("_AVAILABLE_MASK") and not np.array_equal(f[name], old):
            raise ValueError("X enhancement changed raw availability")
    rejected = f["XQC_REJECTED_MASK"] == 1
    withheld = f["XQC_WITHHELD_MASK"] == 1
    if np.any(rejected & ~withheld) or np.any(withheld & (f["OBSERVED_MASK"] != 1)):
        raise ValueError("invalid X disposition domain")
    if np.any((f["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1) & withheld):
        raise ValueError("excluded X gate regained CR admission")
    if np.any(rejected & ((f["QC_ACTION"] != 2) | np.isfinite(f["DBZH_QC_DISPLAY"]))):
        raise ValueError("rejected X gate remains in clean display")
    if np.any(f["QPE_ELIGIBLE_MASK"]):
        raise ValueError("candidate X release cannot enable QPE")
    if cfg.mode == "audit" and (rejected.any() or withheld.any()):
        raise ValueError("audit caused an action")
