"""Post-VOR/NMR opt-in adapter. Earlier hard rejects and clutter stay unchanged."""
from dataclasses import replace
import hashlib
import numpy as np
from ..data import array_digest, json_bytes, checked_mask, ResourceLimit
from ..integration import from_native
from .core import evaluate, abstained
from .disposition import apply, CR


def attributes(cfg, flag):
    return {"qc_receiver_domain_version": cfg.version, "qc_receiver_domain_sha256": cfg.digest,
            "qc_receiver_domain_config": cfg.model_dump(mode="json"),
            "qc_receiver_domain_low_quality_flag": int(flag), "operational_eligible": False}


def protection_masks(n, group, threshold):
    """Known local coherence is distinct from spatial/external or unattributed protection.

    NP/mixed/strong-small protections are NOT reinterpreted here: their origin may
    include unavailable history/context. This deliberately does not alter clutter.
    A VOR local-only reason can be reviewed; unknown reason bits keep protection.
    """
    shape = n.shape
    def mask(key):
        if key not in group: return np.zeros(shape, bool)
        return checked_mask(group[key], shape, key)[n.original_indices]
    hard = np.zeros(shape, bool); local = hard.copy(); unknown = hard.copy()
    for key in ("V7_VERTICAL_SUPPORT_SCORE", "V7_CROSS_RADAR_SUPPORT_SCORE"):
        if key in group:
            v = np.asarray(group[key])
            if v.shape != shape: raise ValueError("weather score geometry differs")
            hard |= (np.isfinite(v) & (v >= threshold))[n.original_indices]
    if "VOR_REASON" in group:
        why = np.asarray(group["VOR_REASON"])[n.original_indices]
        hard |= (why & (16 | 32)) != 0
        local |= (why & 8) != 0
        unknown |= (why.astype("uint32") & np.uint32(~511 & 0xffffffff)) != 0
    if "VOR_STATE" in group:
        state = np.asarray(group["VOR_STATE"])[n.original_indices]
        previous = np.isin(state, (3, 5))
        unknown |= previous & ~(hard | local)
    # NMR current reliable-weather seeds are derived from the same local moments.
    local |= mask("NMR_WEATHER_PROXY_MASK")
    unknown |= mask("NMR_PREVIOUS_WEATHER_MASK") & ~(hard | local)
    # Retain all specific NP protections, including any unidentified context.
    unknown |= mask("NP_WEATHER_PROTECTED_MASK") | mask("NP_MIXED_MASK") | mask("NP_SMALL_STRONG_PROTECTED_MASK")
    hard |= mask("NMR_EXTERNAL_WEATHER_MASK")
    return hard, local, unknown


def review_result(result, native):
    cfg = getattr(result.profile.volume_review, "receiver_domain", None)
    if cfg is None: return result
    if cfg.no_rain_below_dbz != result.profile.echo.no_rain_below_dbz:
        raise ValueError("receiver no-rain semantics differ from source")
    if cfg.quarantine_quality >= result.profile.quality_index.quantitative_minimum:
        raise ValueError("receiver quarantine quality must be below quantitative threshold")
    lookup = {n.name: n for n in native}
    if len(lookup) != len(native) or set(lookup) != {x.name for x in result.sweeps}:
        raise ValueError("receiver native/QC sweep identity differs")
    prepared = {}
    sweeps = {n.name: from_native(n) for n in native}
    try:
        if sum(int(np.prod(n.shape)) for n in native) > cfg.maximum_volume_gates:
            raise ResourceLimit("receiver-domain volume gate budget")
        for old in result.sweeps:
            n = lookup[old.name]
            hard, local, unknown = protection_masks(n, old.optional_qc_fields, result.profile.context.strong_support)
            prepared[old.name] = evaluate(sweeps[old.name], cfg, independent_weather=hard,
                local_coherence=local, unknown_protection=unknown)
    except ResourceLimit as exc:
        # Discard ALL partial sweep results. Parent products stay available.
        prepared = {name: abstained(s, cfg, str(exc)) for name,s in sweeps.items()}
    updated = []; records = []; model_records = []
    summary = dict(result.summary)
    sweep_summary = {k: dict(v) for k, v in summary["sweeps"].items()}
    for old in result.sweeps:
        n = lookup[old.name]
        if not np.array_equal(n.restore(n.fields["DBZH"]), old.dbzh_raw, equal_nan=True):
            raise ValueError("receiver RAW identity differs from native")
        group = {**old.optional_qc_fields, **old.qi_components, "DBZH_RAW": old.dbzh_raw,
            "DBZH_QC": old.dbzh_qc, "VALID_MASK": old.valid_mask, "QC_FLAGS": old.qc_flags,
            "QUALITY_INDEX": old.quality_index, "LOW_QUALITY_MASK": old.low_quality_mask}
        sweep = sweeps[old.name]
        before = sweep.digest
        evidence = prepared[old.name]
        restored = {k: n.restore(v) for k, v in evidence.arrays.items()}
        after, delta = apply(group, restored, cfg, low_quality_flag=result.profile.flag_masks["LOW_QUALITY"])
        if before != sweep.digest: raise RuntimeError("receiver stage mutated original measurements")
        optional = {k: after[k] for k in old.optional_qc_fields}
        optional.update({k: v for k, v in after.items() if k.startswith("RDR_")})
        updated.append(replace(old, optional_qc_fields=optional, qc_flags=after["QC_FLAGS"],
            quality_index=after["QUALITY_INDEX"], low_quality_mask=after["LOW_QUALITY_MASK"],
            qi_components={k: after[k] for k in old.qi_components}))
        for m in evidence.models:
            rec = dict(m, sweep=old.name, native_sorted_ray=m["ray"], ray=int(n.original_indices[m["ray"]]))
            rec["shoulders"] = [dict(side, ray=int(n.original_indices[side["ray"]])) for side in m["shoulders"]]
            model_records.append(rec)
        record = {"sweep": old.name, "native_digest": before, "evidence": evidence.summary, "disposition": delta}
        records.append(record); sweep_summary[old.name]["receiver_domain"] = record
        sweep_summary[old.name]["quantitative_eligible_gates"] = int(after["QPE_ELIGIBLE_MASK"].sum())
        sweep_summary[old.name]["action_counts"] = {label: int((after["QC_ACTION"] == i).sum())
            for i, label in enumerate(("KEEP", "DOWNWEIGHT", "REJECT", "MISSING"))}
    artifacts = dict(getattr(result, "volume_review_artifacts", None) or {})
    payload = json_bytes({"schema": "rainpulse.receiver-domain-evidence-v1", "config": cfg.model_dump(mode="json"),
        "config_sha256": cfg.digest, "sweeps": records, "models": model_records,
        "record_ray_order": "original_acquisition_order", "reference_end_exclusive": True,
        "confirmed_gates": 0, "operational_eligible": False})
    path = "qc/volume_review/receiver_domain.json"; artifacts[path] = payload
    summary["receiver_domain"] = {"status": "RESOURCE_ABSTAINED" if any(e.summary["status"] == "RESOURCE_ABSTAINED" for e in prepared.values()) else "EVALUATED",
        "version": cfg.version, "mode": cfg.mode, "config_sha256": cfg.digest,
        "evidence_path": path, "evidence_sha256": hashlib.sha256(payload).hexdigest(),
        "review_required": any(x["disposition"]["review_required"] for x in records),
        **{key: sum(x["disposition"][key] for x in records) for key in (
            "added_quarantine_gates", "cr_loss_gates", "partial_cr_loss_gates", "qpe_loss_gates")},
        "confirmed_gates": 0}
    summary["sweeps"] = sweep_summary
    values = np.concatenate([s.quality_index[np.isfinite(s.quality_index)] for s in updated])
    summary["mean_quality_index"] = float(values.mean()) if values.size else 0.
    summary["low_quality_gate_count"] = sum(int(s.low_quality_mask.sum()) for s in updated)
    return replace(result, sweeps=tuple(updated), summary=summary, volume_review_artifacts=artifacts)
