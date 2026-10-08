"""Reference-only processing cohorts; no contamination or weather authority."""

import numpy as np

from .paired_range import calibrate


def calibrate_components(ranges, z, snr, paired, reference):
    # Preserve the strict validator's input checks and provenance digest.
    original_coefficient, original = calibrate(ranges, z, snr, paired, reference)
    r = np.asarray(ranges, float)
    z, snr = np.asarray(z, float), np.asarray(snr, float)
    paired, reference = np.asarray(paired), np.asarray(reference)
    if original['status'] == 'measured_consistent':
        # Preserve the accepted successful path byte-for-byte: no recalibration.
        return np.full(len(z), original_coefficient), np.ones(len(z), bool), dict(
            original, method='reference-ray-cohorts-v1',
            status='original_measured_consistent',
            qualified_reference_rays=[], operational_eligible=False,
        )
    use = paired & reference[None, :] & np.isfinite(z) & np.isfinite(snr)
    y = z - snr - 20 * np.log10(np.maximum(r, 1) / 1000)[None, :]
    blocks = (r // 50000).astype(int)
    coefficients = np.zeros(len(z))
    qualified = np.zeros(len(z), bool)
    cohorts = {}
    rejected = []
    for ray in range(len(z)):
        if use[ray].sum() < 100 or np.ptp(r[use[ray]]) < 200000:
            continue
        slopes = []
        for fold in (0, 1):
            train = use[ray] & (blocks % 2 == fold)
            held = use[ray] & (blocks % 2 != fold)
            if (train.sum() < 50 or held.sum() < 50
                    or np.ptp(r[train]) < 100000 or np.ptp(r[held]) < 100000):
                break
            x = r[train] / 1000
            dx = x - x.mean()
            slope = float(dx @ (y[ray, train] - y[ray, train].mean()) / (dx @ dx))
            offset = float(np.mean(y[ray, train] - slope * x))
            error = float(np.percentile(abs(y[ray, held] - offset - slope * r[held] / 1000), 90))
            if not -1e-9 <= slope <= .03 or error > .5:
                break
            slopes.append(max(0., slope))
        if len(slopes) != 2 or abs(slopes[0] - slopes[1]) > .002:
            rejected.append(ray)
            continue
        # Fixed bins, not recursive linkage: a chain cannot widen the cohort.
        cohort = int(np.floor(np.mean(slopes) / .002))
        cohorts.setdefault(cohort, []).append(ray)
    reports = []
    for cohort, rays in sorted(cohorts.items()):
        subset = np.zeros_like(use)
        subset[rays] = use[rays]
        coefficient, report = calibrate(r, z, snr, subset, reference)
        reports.append(dict(report, cohort=cohort, reference_rays=rays))
        if report['status'] == 'measured_consistent':
            qualified[rays] = True
            coefficients[rays] = coefficient
    return coefficients, qualified, dict(
        method='reference-ray-cohorts-v1', sha256=original['sha256'],
        status='measured_components' if qualified.any() else 'no_supported_component',
        rejected_reference_rays=rejected, qualified_reference_rays=np.flatnonzero(qualified).tolist(),
        components=reports, operational_eligible=False,
    )
