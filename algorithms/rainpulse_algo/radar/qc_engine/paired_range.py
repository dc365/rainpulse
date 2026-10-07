"""Paired processing calibration; never independent contamination evidence."""

import hashlib

import numpy as np


def calibrate(ranges, z, snr, paired, reference):
    r = np.asarray(ranges, float)
    z, snr = np.asarray(z, float), np.asarray(snr, float)
    paired, reference = np.asarray(paired), np.asarray(reference)
    if (r.ndim != 1 or z.ndim != 2 or z.shape != snr.shape
            or z.shape[1] != len(r) or paired.shape != z.shape
            or reference.shape != r.shape or paired.dtype != bool
            or reference.dtype != bool or not np.isfinite(r).all()
            or np.any(r < 0) or np.any(np.diff(r) <= 0)):
        raise ValueError("invalid paired range calibration geometry or masks")
    use = paired & reference[None, :] & np.isfinite(z) & np.isfinite(snr)
    y = z-snr-20*np.log10(np.maximum(r, 1)/1000)[None, :]
    h = hashlib.sha256()
    for a in (r[reference], use[:, reference],
              np.where(use[:, reference], y[:, reference], np.nan)):
        h.update(str(a.shape).encode())
        h.update(str(a.dtype).encode())
        h.update(np.ascontiguousarray(a).tobytes())
    report = dict(method="ray-offset-crossfit-v1", sha256=h.hexdigest(), groups=[])

    def abstain(status):
        return 0., dict(report, status=status)

    blocks = (r//50000).astype(int)
    slopes = []
    for parity in (0, 1):
        rows = [i for i in range(parity, len(z), 2)
                if use[i].sum() >= 100 and np.ptp(r[use[i]]) >= 200000]
        if len(rows) < 5:
            return abstain("insufficient_paired_support")
        fold_slopes = []
        for fold in (0, 1):
            refs = []
            numerator = denominator = 0.
            for i in rows:
                train = use[i] & (blocks % 2 == fold)
                held = use[i] & (blocks % 2 != fold)
                if (train.sum() < 50 or held.sum() < 50
                        or np.ptp(r[train]) < 100000 or np.ptp(r[held]) < 100000):
                    continue
                x = r[train]/1000
                yy = y[i, train]
                xx = x-x.mean()
                numerator += float(xx @ (yy-yy.mean()))
                denominator += float(xx @ xx)
                refs.append((i, train, held))
            if len(refs) < 5 or denominator <= 0:
                return abstain("insufficient_paired_support")
            slope = numerator/denominator
            errors = []
            ray_errors = []
            for i, train, held in refs:
                # Fit an individual intercept on training blocks only.
                offset = float(np.mean(y[i, train]-slope*r[train]/1000))
                residual = abs(y[i, held]-offset-slope*r[held]/1000)
                errors.extend(residual)
                ray_errors.append(float(np.percentile(residual, 90)))
            error = float(np.percentile(errors, 90))
            report['groups'].append(dict(parity=parity, fit_block_parity=fold,
                                         ray_count=len(refs), validation_gates=len(errors),
                                         slope_db_per_km=slope, residual_p90_db=error,
                                         maximum_ray_p90_db=max(ray_errors)))
            if not -1e-9 <= slope <= .03 or max(ray_errors) > .5:
                return abstain("unsupported_range_relation")
            fold_slopes.append(max(0., slope))
        if abs(fold_slopes[0]-fold_slopes[1]) > .002:
            return abstain("inconsistent_range_folds")
        slopes.append(float(np.mean(fold_slopes)))
    if abs(slopes[0]-slopes[1]) > .002:
        return abstain("inconsistent_ray_groups")
    coefficient = float(np.mean(slopes))
    return coefficient, dict(report, status="measured_consistent",
                             coefficient_db_per_km=coefficient)
