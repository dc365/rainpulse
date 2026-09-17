"""Cross-ray, same-distance reference for an already qualified source hypothesis."""

import numpy as np


def distance_polar_reference(
    native, cfg, ray, neighbours, fits, power, target_geometry, valid, veto
):
    # A target and its immediate angular neighbours never train their own envelope.
    summaries = []
    donors = []
    for donor in sorted(neighbours):
        angle = abs((native.azimuth[donor] - native.azimuth[ray] + 180) % 360 - 180)
        if angle <= 1.5:
            continue
        fit = fits[donor]
        use = (
            valid[donor]
            & target_geometry
            & ~veto[donor]
            & (abs(power[donor] - fit[0]) <= cfg.range_residual_db)
            & (native.fields["SNR"][donor] >= fit[5][0] - 1)
            & (native.fields["SNR"][donor] <= fit[5][1] + 1)
        )
        if use.sum() < cfg.minimum_polar_samples:
            continue
        phase = (native.fields["PHIDP"][donor, use] - fit[1] + 180) % 360 - 180
        zdr = native.fields["ZDR"][donor, use]
        rho = native.fields["RHOHV"][donor, use]
        if (
            np.percentile(abs(phase), 90) > cfg.maximum_phase_p90_deg
            or np.percentile(abs(zdr - np.median(zdr)), 90) > cfg.maximum_zdr_p90_db
            or np.any((rho < 0) | (rho > 1))
        ):
            continue
        summaries.append(np.array([np.percentile(v, [5, 95]) for v in (phase, zdr, rho)]))
        donors.append(int(donor))
    diag = {"status": "insufficient_donors", "donor_rays": donors}
    if len(donors) < 4:
        return None, diag
    # Equal weight per ray; disjoint groups prevent a single donor setting a bound.
    values = np.asarray(summaries)
    groups = [np.median(values[i::2], axis=0) for i in (0, 1)]
    if np.any(abs(groups[0] - groups[1]) > np.array([2.0, 0.25, 0.03])[:, None]):
        diag["status"] = "inconsistent_donors"
        return None, diag
    bounds = np.median(values, axis=0)
    diag.update(status="measured_consistent", bounds=bounds.tolist())
    return bounds, diag
