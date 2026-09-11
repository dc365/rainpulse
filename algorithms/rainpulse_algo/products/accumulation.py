"""Issue-relative accumulation of complete five-minute member sequences."""

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

WINDOWS = (("hour_1", 0, 60), ("hour_2", 60, 120), ("total_2h", 0, 120))


@dataclass(frozen=True)
class AccumulationWindows:
    # member x window x latitude x longitude, in WINDOWS order.
    amount_mm: np.ndarray
    valid_mask: np.ndarray
    confidence: np.ndarray

    def statistic(self, *, quantile: float | None = None):
        """Mean or quantile after integration, requiring complete member support."""
        if quantile is not None and not 0 <= quantile <= 1:
            raise ValueError("quantile must be within [0, 1]")
        support = np.all(self.valid_mask, axis=0)
        # Invalid values are excluded from publication, not treated as dry cells.
        values = np.where(self.valid_mask, self.amount_mm, 0).astype(np.float64)
        reduced = (
            np.mean(values, axis=0) if quantile is None else np.quantile(values, quantile, axis=0)
        )
        return (
            np.where(support, reduced, np.nan).astype(np.float32),
            support,
            np.where(support, np.min(self.confidence, axis=0), 0).astype(np.float32),
        )


def accumulate_windows(
    rain_rate: np.ndarray,
    valid_mask: np.ndarray,
    confidence: np.ndarray,
    *,
    lead_minutes: Iterable[int],
) -> AccumulationWindows:
    """Integrate right-endpoint rates in mm/h; never infer or fill missing frames."""
    rates = np.asarray(rain_rate, dtype=np.float64)
    masks = np.asarray(valid_mask)
    quality = np.asarray(confidence, dtype=np.float32)
    if (
        rates.ndim != 4
        or rates.shape != masks.shape
        or rates.shape != quality.shape
        or min(rates.shape) < 1
        or rates.shape[1] != 24
    ):
        raise ValueError("arrays must share member x 24 leads x latitude x longitude shape")
    if list(lead_minutes) != list(range(5, 121, 5)):
        raise ValueError("lead minutes must be exactly +5 through +120 in five-minute steps")
    if np.any((masks != 0) & (masks != 1)):
        raise ValueError("valid mask must be binary")
    valid = masks == 1
    if np.any(~np.isfinite(rates[valid])) or np.any(rates[valid] < 0):
        raise ValueError("valid rain rates must be finite and non-negative")
    if np.any(~np.isfinite(quality[valid])) or np.any((quality[valid] < 0) | (quality[valid] > 1)):
        raise ValueError("valid confidence must be finite and within [0, 1]")

    amounts, supports, qualities = [], [], []
    for _, start, end in WINDOWS:
        window = slice(start // 5, end // 5)
        support = np.all(valid[:, window], axis=1)
        amount = np.sum(np.where(valid[:, window], rates[:, window], 0), axis=1) / 12
        q = np.min(np.where(valid[:, window], quality[:, window], 0), axis=1)
        amounts.append(np.where(support, amount, np.nan).astype(np.float32))
        supports.append(support)
        qualities.append(np.where(support, q, 0).astype(np.float32))
    return AccumulationWindows(
        np.stack(amounts, axis=1), np.stack(supports, axis=1), np.stack(qualities, axis=1)
    )
