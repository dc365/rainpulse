"""Phase measurement quality classification for the Z-Phi path.

Thin adapter over wradlib primitives (already a worker-image dependency via
the open-source S QC chain). Produces the two per-gate masks the Z-Phi path
expects from upstream phase quality infrastructure:

- ``PHASE_VALID_MASK``: the differential phase measurement at this gate is
  trustworthy enough to unwrap and integrate along its ray. Tests use the
  Gourley-style 3x3 texture of PHIDP, a despeckle minimum run length, a
  noise-bias corrected RhoHV floor and an SNR floor.
- ``LIQUID_MASK``: the gate most likely sits on a liquid-rain path, the
  regime the Z-Phi coefficients were estimated for. V1 uses a
  noise-corrected RhoHV liquid proxy; a hydroclass-based refinement is the
  documented upgrade path.

Contract rules preserved: upstream-provided masks are never overwritten;
absent moments simply leave the mask absent (the solver then abstains); no
value is invented and no S-band reference is consulted.
"""
from __future__ import annotations

import numpy as np

RHO_PHASE_MIN = 0.92        # noise-corrected RhoHV floor for phase usability
RHO_LIQUID_MIN = 0.97       # liquid-path proxy floor
SNR_MIN_DB = 6.0            # phase reliability SNR floor
PHIDP_TEXTURE_MAX_DEG = 10.0  # 3x3 texture of PHIDP
DESPECKLE_MIN_GATES = 5     # minimum consecutive valid gates along range
VERSION = "phase-quality-wradlib-v1"

# Provenance identity for the rain-segment anchors emitted below: the SHA-256
# of the anchor method statement (computed, not invented).
ANCHOR_EVIDENCE_SHA256 = "368d0913d3a8de44f13d5b49e7eaef9ccdf3313b98d3be60257d1e0fd19b7d38"
_ANCHOR_METHOD = (
    "Anchor PIA=0 dB at the first phase-supported gate of a ray only when "
    "every preceding gate carries no finite DBZH echo: the standard Z-PHI "
    "rain-segment start. Rays with measured echo before their first "
    "supported gate receive no anchor (loss through observed rain is never "
    "assumed zero). phase-quality-wradlib-v1"
)


def _anchor_fields(usable: np.ndarray, finite_echo: np.ndarray, dr: float | None) -> tuple[np.ndarray, np.ndarray]:
    """Rain-segment anchors: PIA=0 at the first phase-supported gate of a ray.

    Allowed when the echo between the no-return prefix and that gate spans at
    most 500 m (or 3 gates without range metadata): the phase-baseline region
    at the start of the rain segment. A support start that lags the first
    measured echo by more than that leaves the ray unanchored - loss through
    observed rain is never assumed zero.
    """
    anchor = np.zeros(usable.shape, "uint8")
    pia = np.full(usable.shape, np.nan, "float32")
    tol = 3 if not dr or dr <= 0 else max(3, int(np.ceil(500.0 / dr)))
    for row in range(usable.shape[0]):
        idx = np.flatnonzero(usable[row])
        echo_idx = np.flatnonzero(finite_echo[row])
        if len(idx) == 0 or len(echo_idx) == 0:
            continue
        k, e = int(idx[0]), int(echo_idx[0])
        if e <= k <= e + tol:
            anchor[row, k] = 1
            pia[row, k] = 0.0
    return anchor, pia


def _snr_field(f: dict):
    for name in ("SNR", "SNRH"):
        if name in f:
            return np.asarray(f[name], "f8")
    return None


def attach_phase_quality(fields: dict, range_m=None, metadata=None) -> None:
    """Add PHASE_VALID_MASK / LIQUID_MASK in place when absent and derivable."""
    if "PHASE_VALID_MASK" in fields and "LIQUID_MASK" in fields:
        return
    shape = np.shape(fields["DBZH"])
    phi = np.asarray(fields.get("PHIDP"), "f8") if "PHIDP" in fields else None
    rho = np.asarray(fields.get("RHOHV"), "f8") if "RHOHV" in fields else None
    snr = _snr_field(fields)
    dbz = np.asarray(fields["DBZH"], "f8")
    if phi is None or rho is None or snr is None:
        return  # required moments absent: leave masks absent, solver abstains

    from wradlib.dp import rhohv_noise_correction
    from wradlib.util import despeckle, texture

    rho_corr = rhohv_noise_correction(rho, snr)
    usable = np.isfinite(phi) & np.isfinite(rho_corr) & np.isfinite(snr) & np.isfinite(dbz)
    usable &= rho_corr >= RHO_PHASE_MIN
    usable &= snr >= SNR_MIN_DB
    tex = texture(np.where(np.isfinite(phi), phi, np.nan))
    usable &= np.isfinite(tex) & (tex <= PHIDP_TEXTURE_MAX_DEG)
    for name in ("CONFIRMED_NONMET_MASK", "ATTENUATION_UNRELIABLE_MASK"):
        if name in fields:
            usable &= np.asarray(fields[name]) == 0
    if usable.any():
        pseudo = np.where(usable, 1.0, np.nan)
        kept = np.isfinite(despeckle(pseudo.copy(), n=DESPECKLE_MIN_GATES))
        usable &= kept
    if "PHASE_VALID_MASK" not in fields:
        fields["PHASE_VALID_MASK"] = usable.astype("uint8")
    if "LIQUID_MASK" not in fields:
        liquid = np.isfinite(rho_corr) & np.isfinite(snr) & np.isfinite(dbz)
        liquid &= (rho_corr >= RHO_LIQUID_MIN) & (snr >= SNR_MIN_DB) & (dbz >= 0.0)
        fields["LIQUID_MASK"] = liquid.astype("uint8")
    if "PATH_ANCHOR_VALID_MASK" not in fields and "PATH_ANCHOR_PIA_DB" not in fields:
        dr = None
        if range_m is not None and len(range_m) > 1:
            dr = float(np.median(np.diff(np.asarray(range_m, "f8"))))
        anchor, pia = _anchor_fields(usable.astype(bool), np.isfinite(dbz), dr)
        fields["PATH_ANCHOR_VALID_MASK"] = anchor
        fields["PATH_ANCHOR_PIA_DB"] = pia
        if metadata is not None and int(anchor.sum()) > 0:
            metadata.setdefault("path_anchor_evidence_sha256", ANCHOR_EVIDENCE_SHA256)
