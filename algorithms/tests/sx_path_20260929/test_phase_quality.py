# ruff: noqa: E501, I001
import numpy as np
import pytest
from .helpers import station, volume, SHA
from rainpulse_algo.multiband.quality import x_qc

wradlib = pytest.importorskip("wradlib")


def phase_fields(shape=(40, 200)):
    """Synthetic sweep: a smooth rain segment with clean phase and RhoHV, a
    low-RhoHV sector, a noisy-phase sector and an isolated speckle pixel."""
    rng = np.arange(shape[1]) * 250.0
    phi = np.tile(np.clip(rng / 20000.0 * 30.0, 0, None), (shape[0], 1)).astype("float32")
    rho = np.full(shape, 0.99, "float32")
    snr = np.full(shape, 20.0, "float32")
    dbz = np.full(shape, 35.0, "float32")
    r0, r1 = shape[0] // 4, shape[0] // 2
    n0, n1 = shape[1] // 2, shape[1] // 2 + max(4, shape[1] // 5)
    rho[:r0] = 0.70            # uncorrelated sector: phase unusable
    phi[r0:r1, n0:n1] += np.random.default_rng(7).normal(0, 8, (r1 - r0, n1 - n0)).astype("float32")  # noisy phase
    obs = np.ones(shape, "uint8")
    noe = np.zeros(shape, "uint8")
    fields = dict(DBZH=dbz, PHIDP=phi.astype("float32"), RHOHV=rho, SNR=snr.astype("float32"),
                  OBSERVED_MASK=obs, NO_ECHO_MASK=noe)
    return fields


def test_masks_derived_shape_and_binary():
    from rainpulse_algo.multiband.phase_quality import attach_phase_quality
    f = phase_fields()
    attach_phase_quality(f)
    assert set(("PHASE_VALID_MASK", "LIQUID_MASK")) <= set(f)
    pv = f["PHASE_VALID_MASK"]
    assert pv.dtype == np.uint8 and pv.shape == (40, 200) and np.isin(pv, (0, 1)).all()
    r0 = 40 // 4
    clean = slice(2 * r0, None)
    assert pv[clean].all()          # clean sector valid
    assert not pv[:r0].any()        # low RhoHV sector invalid
    lq = f["LIQUID_MASK"]
    assert lq[clean].all() and not lq[:r0].any()


def test_existing_masks_never_overwritten():
    from rainpulse_algo.multiband.phase_quality import attach_phase_quality
    f = phase_fields()
    f["PHASE_VALID_MASK"] = np.zeros((40, 200), "uint8")
    f["LIQUID_MASK"] = np.ones((40, 200), "uint8")
    attach_phase_quality(f)
    assert f["PHASE_VALID_MASK"].sum() == 0 and f["LIQUID_MASK"].all()


def test_absent_moments_leave_masks_absent():
    from rainpulse_algo.multiband.phase_quality import attach_phase_quality
    f = phase_fields()
    del f["SNR"]
    attach_phase_quality(f)
    assert "PHASE_VALID_MASK" not in f


def test_zphi_admits_eligible_echo_without_upstream_masks():
    st = station()
    v = volume(st)
    f = v.sweeps[0].fields
    # drop the helper-provided masks and supply realistic moments instead
    for k in ("PHASE_VALID_MASK", "LIQUID_MASK"):
        f.pop(k, None)
    synthetic = phase_fields(f["DBZH"].shape)
    f.update({k: synthetic[k] for k in ("PHIDP", "RHOHV", "SNR")})
    out = x_qc(v, st, SHA)
    g = out.sweeps[0].fields
    assert int((g["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1).sum()) > 0
    assert np.isfinite(g["PIA_DB"]).any()
