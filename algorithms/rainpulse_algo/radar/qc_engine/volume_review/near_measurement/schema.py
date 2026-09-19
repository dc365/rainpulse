"""Field units and meanings persisted after the ordinary QC writer."""

def annotate(group):
    for name in group:
        if not name.startswith("NMR_") or not hasattr(group[name], "attrs"):
            continue
        if name.startswith("NMR_BEFORE_"):
            original = name[len("NMR_BEFORE_"):]
            meta = dict(group[original].attrs) if original in group and hasattr(group[original], "attrs") else {}
            meta["stage_semantics"] = "exact_pre_near_measurement_state"
        else:
            units = "dB" if name == "NMR_DR_DB" else "degree" if name == "NMR_PHIDP_CIRCSTD_DEG" else "1"
            meta = {"units": units, "definition_version": "near-measurement-20260919-v1",
                    "scores_are_calibrated_probabilities": False}
            if name == "NMR_DR_DB": meta["finite_floor_db"] = -100.
            if name == "NMR_REASON":
                meta["definition"] = "1:polar_candidate,2:low_SNR_uncertain,4:prior_weather,8:weather_proxy,16:strong_echo,32:unsafe_geometry,64:polar_unavailable,128:SNR_unavailable,256:ZDR_tail_guard"
            if name.endswith("_MASK"): meta["values"] = "0:false,1:true"
        group[name].attrs.update(meta)
