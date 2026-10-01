# ruff: noqa: E501, I001
import numpy as np
import pytest
from .helpers import station, volume, SHA
from rainpulse_algo.multiband.quality import x_qc

wradlib = pytest.importorskip("wradlib")


def radome_fields(shape=(60, 200), near_dbz=25.0, near_rays=None):
    """Sweep with a near-radar echo annulus at near_dbz and 40 dBZ elsewhere."""
    rng = np.arange(shape[1]) * 250.0
    dbz = np.full(shape, 40.0, "float32")
    dbz[:, rng > 80_000] = np.nan
    rows = np.arange(shape[0]) if near_rays is None else np.asarray(near_rays)
    near = rng <= 10_000
    dbz[:, near] = np.nan
    dbz[np.ix_(rows, np.flatnonzero(near))] = near_dbz
    fields = dict(DBZH=dbz, OBSERVED_MASK=np.isfinite(dbz).astype("uint8"),
                  NO_ECHO_MASK=np.zeros(shape, "uint8"))
    return fields, rng


def md():
    return dict(frequency_hz=9.4e9)


def test_light_near_radar_rain_verifies_negligible():
    from rainpulse_algo.multiband.radome_quality import (assess_radome,
        RADOME_EVIDENCE_SHA256, NEGLIGIBLE_DB)
    f, rng = radome_fields(near_dbz=25.0)  # ~0.6 mm/h near radar
    meta = md()
    assess_radome(f, rng, meta)
    assert meta["radome_status"] == "verified_negligible"
    assert meta["radome_evidence_sha256"] == RADOME_EVIDENCE_SHA256
    rec = meta["radome_assessment"]
    assert rec["measured_rays"] == 60 and rec["worst_two_way_db"] <= NEGLIGIBLE_DB
    assert np.all(f["RADOME_VALID_MASK"] == 1)


def test_heavy_near_radar_rain_refuses():
    from rainpulse_algo.multiband.radome_quality import assess_radome
    f, rng = radome_fields(near_dbz=45.0)  # ~28 mm/h -> several dB two-way
    meta = md()
    assess_radome(f, rng, meta)
    assert meta.get("radome_status") == "estimated_wet"
    assert "radome_evidence_sha256" not in meta
    assert "RADOME_VALID_MASK" not in f


def test_no_near_radar_echo_abstains():
    from rainpulse_algo.multiband.radome_quality import assess_radome
    f, rng = radome_fields(near_dbz=25.0, near_rays=range(0, 5))  # too few rays
    meta = md()
    assess_radome(f, rng, meta)
    assert "radome_status" not in meta
    assert meta["radome_assessment"]["status"] == "unmeasured_near_radar"


def test_declared_status_never_overwritten():
    from rainpulse_algo.multiband.radome_quality import assess_radome
    f, rng = radome_fields(near_dbz=45.0)
    meta = md()
    meta["radome_status"] = "upstream_corrected"
    meta["radome_evidence_sha256"] = "a" * 64
    assess_radome(f, rng, meta)
    assert meta["radome_status"] == "upstream_corrected"


def test_wet_later_cut_downgrades_previous_negligible_claim():
    from rainpulse_algo.multiband.radome_quality import assess_radome
    f1, rng = radome_fields(near_dbz=25.0)
    meta = md()
    assess_radome(f1, rng, meta)
    assert meta["radome_status"] == "verified_negligible"
    f2, _ = radome_fields(near_dbz=45.0)
    assess_radome(f2, rng, meta)
    assert meta["radome_status"] == "estimated_wet"
    assert "radome_evidence_sha256" not in meta


def test_x_qc_drops_radome_unverified_when_negligible():
    import rainpulse_algo.multiband.radome_quality as rq
    st = station()
    v = volume(st)
    for k in ("radome_status", "radome_evidence_sha256"):
        v.metadata.pop(k, None)  # force the producer path, not the helper's declaration
    f = v.sweeps[0].fields
    sw = v.sweeps[0]
    rng = np.asarray(sw.range_m, "f8")  # the producer reads the sweep grid; build on it
    dbz = np.full(f["DBZH"].shape, 40.0, "float32")
    near = rng <= 10_000
    dbz[:, near] = 25.0
    f["DBZH"] = dbz
    f["OBSERVED_MASK"] = np.isfinite(dbz).astype("uint8")
    from rainpulse_algo.multiband.attenuation import PathReason
    rq.MIN_MEASURED_RAYS = 1  # helper volume carries only two rays
    try:
        out = x_qc(v, st, SHA)
    finally:
        rq.MIN_MEASURED_RAYS = 30
    g = out.sweeps[0].fields
    assert int(np.count_nonzero(np.asarray(g["PATH_REASON"]) & int(PathReason.RADOME_UNVERIFIED))) == 0
    assert int((np.asarray(g["RADOME_QUALIFIED_MASK"]) == 1).sum()) > 0
    assert v.metadata["radome_status"] == "verified_negligible"
