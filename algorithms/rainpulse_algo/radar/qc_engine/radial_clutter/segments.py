"""Only measured intervals receive membership; clear-air gates are barriers."""
import numpy as np

from .geometry import runs


def associate(echo, observed, spacing_m, cfg):
    result = np.zeros(echo.shape, bool)
    for ray in range(echo.shape[0]):
        groups = []
        for lo, hi in runs(echo[ray]):
            if (hi - lo) * spacing_m < cfg.minimum_fragment_m:
                continue
            if groups:
                previous = groups[-1]
                start, end = previous[0][0], previous[-1][1]
                actual = sum(b - a for a, b in previous) + hi - lo
                gap = (lo - end) * spacing_m
                missing_only = not observed[ray, end:lo].any()
                fraction = 1 - actual / (hi - start)
                if missing_only and gap <= cfg.maximum_gap_m and fraction <= cfg.maximum_gap_fraction:
                    previous.append((lo, hi))
                    continue
            groups.append([(lo, hi)])
        for group in groups:
            actual = sum(b - a for a, b in group) * spacing_m
            if actual >= cfg.minimum_measured_m:
                for lo, hi in group:
                    result[ray, lo:hi] = True
    return result
