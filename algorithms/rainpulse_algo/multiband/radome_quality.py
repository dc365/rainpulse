"""Wet-radome assessment for the X path admission chain.

The path contract (attenuation.correct_sweep) admits radome validity only from
``radome_status`` + ``radome_evidence_sha256`` in the volume metadata. This
module is the honest producer of that declaration, derived from the volume's
own measurement via wradlib's empirical wet-radome loss estimator
(Merceret 2000 approach): rain over the radome is proxied by the strongest
near-radar echo gate, converted with the standard Z-R relation inside wradlib,
and mapped to a two-way loss estimate.

Conservative by construction:

- the near-radar statistic is the gate maximum, not the mean;
- the material parameter assumes a standard (non-hydrophobic) radome;
- ``verified_negligible`` is claimed only when every measured ray of the cut
  stays at or below ``NEGLIGIBLE_DB``, and only for rays that actually carry a
  near-radar measurement (unmeasured rays keep RADOME_UNVERIFIED);
- wradlib documents its empirical fit as C/S-band; using it at 9.4 GHz is an
  extrapolation and the evidence statement says so explicitly.

Contract rules preserved: an existing declaration is never overwritten; a cut
without near-radar measurements leaves the status absent (admission abstains);
no value is invented.
"""
from __future__ import annotations

import numpy as np

NEAR_RANGE_M = 10_000.0     # rain-at-the-station proxy window
MIN_MEASURED_RAYS = 30      # below this the cut abstains entirely
NEGLIGIBLE_DB = 1.0         # two-way wet-radome loss considered negligible
HYDROPHOBICITY = 0.165      # standard radome material (worst common case)
STATISTIC = "maximum near-radar gate reflectivity"
VERSION = "radome-quality-wradlib-v1"

# Provenance identity: SHA-256 of the assessment method statement.
RADOME_EVIDENCE_SHA256 = "placeholder-replaced-below"

_METHOD = (
    "RainPulse X-band wet-radome assessment. Method: wradlib.atten."
    "correct_radome_attenuation_empirical (Merceret 2000 empirical two-way "
    "wet-radome loss, k = 2 * hydrophobicity * R * tanh(f/10)^2) fed with the "
    "maximum near-radar (<=10 km) finite DBZH gate per ray as the "
    "rain-over-radome proxy, standard material hydrophobicity 0.165, station "
    "frequency from metadata. wradlib documents the empirical fit for C/S "
    "band; the 9.4 GHz use is an extrapolation and only negligibility "
    "(all measured rays <= 1.0 dB two-way) is claimed, never a correction. "
    "Rays without a near-radar measurement stay radome-unverified. "
    "radome-quality-wradlib-v1"
)


def _evidence_sha() -> str:
    import hashlib

    return hashlib.sha256(_METHOD.encode()).hexdigest()


RADOME_EVIDENCE_SHA256 = _evidence_sha()


def assess_radome(fields: dict, range_m, metadata: dict) -> None:
    """Derive the radome declaration for one cut when none is declared.

    Refusal is sticky; a previous negligible claim is re-checked and downgraded
    when a later cut of the same volume measures a wetter radome.
    """
    declared = metadata.get("radome_status")
    if declared not in (None, "verified_negligible"):
        return
    dbz = np.asarray(fields.get("DBZH"), "f8")
    if dbz.ndim != 2 or range_m is None or len(range_m) < 2:
        return
    rng = np.asarray(range_m, "f8")
    frequency_hz = metadata.get("frequency_hz")
    if not isinstance(frequency_hz, (int, float)) or isinstance(frequency_hz, bool):
        return
    frequency_ghz = float(frequency_hz) / 1e9
    if not 8.0 <= frequency_ghz <= 12.0:
        return  # the empirical formula is not usable outside X band here
    near = rng <= NEAR_RANGE_M
    if not near.any():
        return
    window = np.where(near[None, :], dbz, np.nan)
    measured = np.any(np.isfinite(window), axis=1)
    if int(measured.sum()) < MIN_MEASURED_RAYS:
        metadata["radome_assessment"] = {
            "version": VERSION, "status": "unmeasured_near_radar",
            "measured_rays": int(measured.sum()), "required": MIN_MEASURED_RAYS}
        return
    peak = np.nanmax(window, axis=1)
    peak = np.where(measured, peak, np.nan)
    from wradlib.atten import correct_radome_attenuation_empirical
    rows = np.where(np.isfinite(peak), peak, -95.0)
    gateset = np.repeat(rows[:, None], 2, axis=1)
    k = correct_radome_attenuation_empirical(
        gateset, frequency=frequency_ghz, hydrophobicity=HYDROPHOBICITY,
        n_r=2, stat=np.mean)
    k_ray = np.asarray(k)[:, 0]
    k_meas = k_ray[measured]
    worst = float(np.max(k_meas))
    record = {"version": VERSION, "measured_rays": int(measured.sum()),
              "near_range_m": NEAR_RANGE_M, "worst_two_way_db": round(worst, 3),
              "threshold_db": NEGLIGIBLE_DB, "statistic": STATISTIC}
    if worst <= NEGLIGIBLE_DB:
        metadata["radome_status"] = "verified_negligible"
        metadata["radome_evidence_sha256"] = RADOME_EVIDENCE_SHA256
        record["status"] = "verified_negligible"
        mask = np.zeros(dbz.shape, "uint8")
        mask[measured, :] = 1
        fields.setdefault("RADOME_VALID_MASK", mask)
    else:
        record["status"] = "estimated_wet"
        metadata["radome_status"] = "estimated_wet"
        if declared == "verified_negligible":
            metadata.pop("radome_evidence_sha256", None)
    metadata["radome_assessment"] = record
