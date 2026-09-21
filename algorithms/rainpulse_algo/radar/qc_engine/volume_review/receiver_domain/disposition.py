"""Separate compatibility from action; never revive an earlier rejected gate."""
import numpy as np
from ..data import checked_mask
from ..disposition import DERIVED_FIELDS, derived_invalidation
from .core import evidence_dtypes

CR = "REFLECTIVITY_ELIGIBLE_FOR_CR"
FIXED = {CR, "CR_UNCERTAIN_MASK", "CR_QUALIFICATION_REASON", "QPE_ELIGIBLE_MASK",
         "QC_ACTION", "DBZH_USABLE", "QC_FLAGS", "QUALITY_INDEX", "LOW_QUALITY_MASK",
         "QI_METEO", "QI_INTERFERENCE", "P2_ADMIN_PENALTY_REMOVED_MASK"}


def mutable_names(group):
    return sorted(k for k in group if not k.startswith(("RDR_", "NMR_", "VOR_")) and
                  (k in FIXED or k in DERIVED_FIELDS or k.endswith("_TRUST_MASK")))


def check_evidence(e, observed, cfg):
    shape = observed.shape
    for key, dt in evidence_dtypes(cfg).items():
        value = np.asarray(e.get("RDR_"+key))
        if value.shape != shape or value.dtype != np.dtype(dt):
            raise ValueError("invalid receiver evidence " + key)
        if key.endswith("_MASK") and (not np.isin(value, (0, 1)).all() or np.any((value == 1) & ~observed)):
            raise ValueError("receiver evidence created observation " + key)
    m = lambda key: e["RDR_"+key] == 1
    full, partial, available = m("FULL_MATCH_MASK"), m("PARTIAL_MATCH_MASK"), m("MODEL_AVAILABLE_MASK")
    if np.any((full | partial) & (~available | ~m("TARGET_POWER_MATCH_MASK") | m("TARGET_POLAR_CONFLICT_MASK") | m("TARGET_TAIL_MASK"))):
        raise ValueError("source target lacks compatible independent moments")
    if (np.any(full & partial) or np.any(full & (e["RDR_TARGET_POLAR_COUNT"] != 3)) or
            np.any(partial & (e["RDR_TARGET_POLAR_COUNT"] >= 3)) or
            np.any((full | partial) & ((abs(e["RDR_RESIDUAL_DB"]) > cfg.maximum_target_residual_db) |
            ~np.isfinite(e["RDR_RESIDUAL_DB"]) | ~np.isfinite(e["RDR_SNR_DELTA_DB"]) |
            (abs(e["RDR_SNR_DELTA_DB"]) > cfg.maximum_snr_p90_db)))):
        raise ValueError("invalid full/partial source contract")
    if not np.array_equal(e["RDR_MODEL_ID"] > 0, available):
        raise ValueError("source model index does not match availability")
    if cfg.segment_reference is not None:
        seg = m("SEGMENT_REFERENCE_MASK")
        ambiguity = m("SEGMENT_AMBIGUOUS_MASK")
        matches = e["RDR_SEGMENT_MATCH_COUNT"]
        distance = e["RDR_SEGMENT_REFERENCE_DISTANCE_M"]
        if (np.any(seg & ~available) or np.any(matches > cfg.segment_reference.maximum_states)
                or not np.array_equal(ambiguity, seg & (matches > 1))
                or not np.array_equal((full | partial) & seg, (matches == 1) & seg)
                or np.any((full | partial) & ambiguity)
                or np.any(seg & (~np.isfinite(distance) | (distance < 0)))
                or np.any(~seg & (np.isfinite(distance) | (matches != 0)))
                or np.any((full | partial) & seg & (distance > cfg.segment_reference.maximum_reference_distance_m))
                or np.any(seg & ~m("SEGMENT_SIDE_MEASURED_MASK") & ~m("TARGET_SIDE_CONFLICT_MASK"))):
            raise ValueError("invalid finite receiver reference provenance")
    if cfg.source_family is not None:
        from .family_validation import check_family_evidence
        check_family_evidence(e, observed, cfg)
    hard = m("INDEPENDENT_WEATHER_MASK") | m("UNKNOWN_PROTECTION_MASK")
    local = m("LOCAL_COHERENCE_MASK")
    side_conflict = m("TARGET_SIDE_CONFLICT_MASK")
    expected = full & ~hard & ~side_conflict & ~(local & (cfg.local_policy == "retain_conflict"))
    mixed = (full | partial) & (hard | local | side_conflict) & ~expected
    if not np.array_equal(m("SOURCE_MASK"), expected) or not np.array_equal(m("MIXED_MASK"), mixed):
        raise ValueError("source/weather joint disposition differs")
    if not np.array_equal(m("LOCAL_REVIEWED_MASK"), expected & local):
        raise ValueError("local review provenance differs")
    state = np.where(observed, 1, 0).astype("uint8")
    state[partial] = 3; state[full] = 2; state[mixed] = 4; state[hard] = 5; state[expected] = 2
    if not np.array_equal(state, e["RDR_STATE"]):
        raise ValueError("source state differs from evidence")


def apply(group, evidence, cfg, *, low_quality_flag):
    if not isinstance(low_quality_flag, (int, np.integer)) or not 0 < int(low_quality_flag) < 2**32:
        raise ValueError("invalid receiver LOW_QUALITY flag")
    if any(k.startswith("RDR_") for k in group):
        raise ValueError("receiver stage cannot be applied twice")
    obs = checked_mask(group["VALID_MASK"], np.shape(group["DBZH_RAW"]), "observed")
    check_evidence(evidence, obs, cfg)
    a = {k: np.array(v, copy=True) for k, v in group.items()}
    trust = checked_mask(a["REFLECTIVITY_TRUST_MASK"], obs.shape, "trust")
    cr = checked_mask(a[CR], obs.shape, "CR")
    qpe = checked_mask(a["QPE_ELIGIBLE_MASK"], obs.shape, "QPE")
    if np.any((cr | qpe) & (~trust | ~obs)) or not np.array_equal(a["QC_ACTION"] == 3, ~obs):
        raise ValueError("invalid receiver baseline qualification")
    source = evidence["RDR_SOURCE_MASK"] == 1
    partial = evidence["RDR_PARTIAL_MATCH_MASK"] == 1
    hard = (evidence["RDR_INDEPENDENT_WEATHER_MASK"] == 1) | (evidence["RDR_UNKNOWN_PROTECTION_MASK"] == 1)
    local = evidence["RDR_LOCAL_COHERENCE_MASK"] == 1
    active = cfg.mode != "audit"
    route_active = np.ones(obs.shape, bool)
    partial_allowed = np.full(obs.shape, cfg.partial_policy == "cr_withhold", bool)
    segment = np.zeros(obs.shape, bool)
    if cfg.segment_reference is not None:
        segment = evidence["RDR_SEGMENT_REFERENCE_MASK"] == 1
        route_active[segment] = cfg.segment_reference.mode == "experiment"
        partial_allowed[segment] = cfg.segment_reference.partial_policy == "cr_withhold"
    family = np.zeros(obs.shape, bool)
    if cfg.source_family is not None:
        family = evidence["RDR_FAMILY_REFERENCE_MASK"] == 1
        route_active[family] = cfg.source_family.mode == "experiment"
        partial_allowed[family] = cfg.source_family.partial_policy == "cr_withhold"
    partial_action = (partial & ~hard & ~local & (evidence["RDR_TARGET_SIDE_CONFLICT_MASK"] == 0)
                      & partial_allowed & route_active & active)
    selected = (source & active & route_active) | partial_action
    if np.any(selected & ((a["DBZH_RAW"] < cfg.no_rain_below_dbz) | ~np.isfinite(a["DBZH_RAW"]))):
        raise ValueError("receiver action crosses raw/no-rain boundary")
    q = source & route_active & trust & obs & (a["QC_ACTION"] != 2) & (cfg.mode == "quarantine")
    if cfg.source_family is not None and cfg.source_family.full_policy != "quarantine":
        q &= ~family
    loss = cr & selected
    for k in mutable_names(a):
        a["RDR_BEFORE_"+k] = a[k].copy()
    a.update({k: np.array(v, copy=True) for k, v in evidence.items()})
    a["RDR_CR_WITHHELD_MASK"] = loss.astype("uint8")
    a["RDR_QUARANTINE_MASK"] = q.astype("uint8")
    a["RDR_PARTIAL_CR_WITHHELD_MASK"] = (loss & partial_action).astype("uint8")
    if cfg.source_family is not None:
        a["RDR_FAMILY_CR_WITHHELD_MASK"] = (loss & family).astype("uint8")
        a["RDR_FAMILY_QUARANTINE_MASK"] = (q & family).astype("uint8")
    a[CR][loss] = 0
    if active:
        a["CR_UNCERTAIN_MASK"][(partial | (evidence["RDR_MIXED_MASK"] == 1)) & route_active] = 1
        a["CR_QUALIFICATION_REASON"][loss] |= np.uint16(1024)
        a["CR_QUALIFICATION_REASON"][loss] &= np.uint16(65534)
    if active and cfg.source_family is not None and cfg.source_family.mode == "experiment":
        a["CR_UNCERTAIN_MASK"][evidence["RDR_FAMILY_UNRESOLVED_MASK"] == 1] = 1
    derived = derived_invalidation(trust, q)
    a["RDR_DERIVED_INVALIDATION_MASK"] = derived.astype("uint8")
    for key in DERIVED_FIELDS:
        if key in a:
            a[key][derived] = 0 if key.endswith("_MASK") else np.nan
    a["QC_ACTION"][q] = 1
    a["QC_FLAGS"][q] |= np.uint32(low_quality_flag)
    for key in ("QUALITY_INDEX", "QI_METEO", "QI_INTERFERENCE"):
        if key in a:
            a[key][q] = np.minimum(a[key][q], cfg.quarantine_quality)
    a["LOW_QUALITY_MASK"][q] = 1
    a["DBZH_USABLE"][q] = np.nan
    a["QPE_ELIGIBLE_MASK"][q] = 0
    for key in mutable_names(group):
        if key.endswith("_TRUST_MASK") or key == "P2_ADMIN_PENALTY_REMOVED_MASK":
            a[key][q] = 0
    if np.any((a[CR] == 1) & ~cr) or np.any((a["QPE_ELIGIBLE_MASK"] == 1) & ~qpe):
        raise RuntimeError("receiver stage revived a parent measurement")
    cr_frac = float(loss.sum()/max(1, cr.sum()))
    qp_loss = q & qpe; qp_frac = float(qp_loss.sum()/max(1, qpe.sum()))
    return a, {"added_quarantine_gates": int(q.sum()), "cr_loss_gates": int(loss.sum()),
        "partial_cr_loss_gates": int((loss & partial_action).sum()), "qpe_loss_gates": int(qp_loss.sum()),
        "cr_loss_fraction": cr_frac, "qpe_loss_fraction": qp_frac,
        "review_required": cr_frac > cfg.maximum_new_cr_loss_fraction or qp_frac > cfg.maximum_new_qpe_loss_fraction,
        "budget_policy": "retain_withholding_require_review", "confirmed_gates": 0, "filled_gates": 0,
        **({"segment_cr_loss_gates": int((loss & segment).sum()),
             "segment_qpe_loss_gates": int((qp_loss & segment).sum()),
             "segment_audit": cfg.segment_reference.mode == "audit"}
            if cfg.segment_reference is not None else {}),
        **({"family_cr_loss_gates": int((loss & family).sum()),
             "family_qpe_loss_gates": int((qp_loss & family).sum()),
             "family_quarantine_gates": int((q & family).sum()),
             "family_audit": cfg.source_family.mode == "audit"}
            if cfg.source_family is not None else {})}
