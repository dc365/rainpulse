"""Persist explicit units/reasons and the original pre-receiver numerical view."""

def annotate(group):
    for name in group:
        if not name.startswith("RDR_") or not hasattr(group[name], "attrs"): continue
        meta = {"stage": "receiver-domain-20260921-v1", "operational_eligible": False}
        if name.startswith("RDR_BEFORE_"):
            original = name[len("RDR_BEFORE_"):]
            if original in group and hasattr(group[original], "attrs"):
                meta.update(dict(group[original].attrs))
            meta["semantics"] = "exact_pre_receiver_state"
        elif name.endswith("_MASK"):
            meta.update(units="1", semantics="0:false,1:true; observed-only")
        elif name.endswith("_DB"):
            meta["units"] = "dB"
        elif name == "RDR_STATE":
            meta.update(units="1", semantics="0:missing,1:not_supported,2:source_hypothesis,3:partial,4:mixed,5:protected")
        else: meta["units"] = "1"
        group[name].attrs.update(meta)
