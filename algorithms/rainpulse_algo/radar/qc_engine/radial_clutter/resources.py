"""Optional per-cut prior mapping using a caller-supplied artifact loader."""
from .prior_asset import match_prior


def prepare_priors(profile, load_archive, root, ancillary):
    cfg = getattr(profile, "radial_clutter", None)
    if cfg is None or not cfg.priors:
        return ancillary, {"status": "not_configured", "cuts": {}}
    from ..adapters import adapt_sweep

    result = {key: dict(value) for key, value in ancillary.items()}
    records = {}
    for cut, spec in cfg.priors.items():
        if cut not in root:
            records[cut] = {"status": "cut_absent"}
            continue
        native = adapt_sweep(root, cut, profile)
        archive = load_archive(spec.uri)
        target_time = root.attrs.get("volume_end_time_utc")
        if not target_time:
            raise ValueError("actual observation time required")
        prior = match_prior(archive, spec.sha256, native, str(root.attrs["radar_id"]),
                            spec.partition_id, str(target_time), cfg)
        if prior.get("cut_name") != cut:
            raise ValueError("prior cut identity differs")
        result.setdefault(cut, {})
        for key in ("frequency", "lower", "upper"):
            result[cut]["rc1_prior_" + key] = native.restore(prior[key])
        records[cut] = {"status": "verified", "sha256": spec.sha256}
    return result, {"status": "applied", "cuts": records}


def prior_for_native(ancillary, native):
    maps = ancillary.get(native.name, {})
    names = ("frequency", "lower", "upper")
    if not any("rc1_prior_" + key in maps for key in names):
        return None
    if not all("rc1_prior_" + key in maps for key in names):
        raise ValueError("partial prior evidence")
    return {key: maps["rc1_prior_" + key][native.original_indices] for key in names}
