"""Join source-fit measurements with narrow/multiscale morphology, not vice versa."""
import numpy as np
from .arrays import mask, numeric, moment
from .multiscale import multiscale_radials


def source_additions(native, cfg, reference, residual, *, weather=None, conflicts=None, reference_available=None, revision_records=None):
    shape = native.shape
    observed = moment(native, "DBZH")[1]
    protected = mask(weather, shape, "weather")
    conflict = mask(conflicts, shape, "conflicts")
    ref = mask(reference, shape, "source_reference")
    available = ref if reference_available is None else mask(reference_available, shape, "reference_available")
    if np.any(ref & ~available):
        raise ValueError("matched target has no held-out reference")
    delta = numeric(residual, shape, "heldout_residual")
    source = ref & observed & np.isfinite(delta) & (abs(delta) <= cfg.maximum_source_residual_db)
    narrow = np.zeros(shape, bool)
    narrow_reason = np.zeros(shape, "uint16")
    if cfg.narrow_enabled:
        from ..narrow_source import narrow_source
        narrow, narrow_reason = narrow_source(native, ref, delta, weather=protected, conflicts=conflict)
    arrays = {}
    summary = {"status": "disabled", "candidate_gates": 0}
    multiscale = np.zeros(shape, bool)
    if cfg.multiscale_enabled:
        ev = multiscale_radials(native, cfg, weather=protected, conflicts=conflict)
        arrays.update(ev.arrays)
        summary = ev.summary
        multiscale = ev.arrays["SRC_REVIEW_CANDIDATE_MASK"] == 1
    qualified = (narrow | multiscale) & source & ~protected & ~conflict
    revision_summary = {}
    if cfg.radial_revision is not None:
        from .radial_revision.engine import evaluate
        extra, detail = evaluate(
            native, cfg.radial_revision, source, delta,
            weather=protected, conflicts=conflict, records_out=revision_records,
        )
        arrays.update(extra)
        qualified |= extra["RV2_ACTION_PROPOSAL_MASK"] == 1
        revision_summary = {"radial_revision": detail}
    arrays.update({
        "SRC_REVIEW_NARROW_MASK": narrow.astype("uint8"),
        "SRC_REVIEW_NARROW_REASON": narrow_reason,
        "SRC_REVIEW_SOURCE_AVAILABLE_MASK": (available & observed).astype("uint8"),
        "SRC_REVIEW_TARGET_MATCH_MASK": (ref & observed).astype("uint8"),
        "SRC_REVIEW_RESIDUAL_DB": delta,
        "SRC_REVIEW_WEATHER_PROTECTED_MASK": (protected & observed).astype("uint8"),
        "SRC_REVIEW_CONFLICT_MASK": (conflict & observed).astype("uint8"),
        "SRC_REVIEW_SOURCE_MATCH_MASK": source.astype("uint8"),
        "SRC_REVIEW_QUALIFIED_MASK": qualified.astype("uint8"),
    })
    return qualified, arrays, {
        "status": "experimental_source_supported", "narrow_gates": int(narrow.sum()),
        "qualified_gates": int(qualified.sum()), "multiscale": summary,
        **revision_summary,
        "confirmed_gates": 0, "operational_eligible": False,
    }


def target_reference(native, ray, fit, power, target, *, maximum_residual_db):
    """Only target measurements checked against an already held-out RAW fit.

    Fitting and target/guard exclusion remain in the frozen broad-source code.
    Angular width is deliberately NOT a qualification for this narrow branch.
    """
    intercept, center, phase_bounds, zdr_bounds, rho_bounds, snr_bounds = fit
    f = native.fields
    delta = power[ray] - intercept
    phase = (f["PHIDP"][ray] - center + 180) % 360 - 180
    match = (
        target & np.isfinite(delta) & (abs(delta) <= maximum_residual_db)
        & (f["SNR"][ray] >= snr_bounds[0] - 1) & (f["SNR"][ray] <= snr_bounds[1] + 1)
        & (phase >= phase_bounds[0] - .5) & (phase <= phase_bounds[1] + .5)
        & (f["ZDR"][ray] >= zdr_bounds[0] - .125) & (f["ZDR"][ray] <= zdr_bounds[1] + .125)
        & (f["RHOHV"][ray] >= max(0, rho_bounds[0] - .01))
        & (f["RHOHV"][ray] <= min(1, rho_bounds[1] + .01))
    )
    return match, delta
