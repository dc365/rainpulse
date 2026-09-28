"""Same-ray fragment completion keeps the S contract: local evidence or nothing."""
import numpy as np
from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig
from rainpulse_algo.multiband.xqc_v2.fragments import associate

RAYS, GATES, DR = 40, 600, 75.


def make_sweep(z, rho, zdr, phi, snr):
    fields = {"DBZH": z, "SNR": snr, "RHOHV": rho, "ZDR": zdr, "PHIDP": phi}
    available = {k: np.isfinite(v) for k, v in fields.items()}
    return Sweep("sweep_000", np.arange(RAYS) * 360. / RAYS, np.full(RAYS, .5),
                 np.arange(1, GATES + 1) * DR, fields, available, np.ones(RAYS, bool),
                 np.zeros(RAYS, bool), np.linspace(0, 60, RAYS))


def base_config(**changes):
    data = {"fragment_maximum_distance_m": 15000., **changes}
    return XQCConfig.model_validate(data)


def spoke_fields():
    z = np.zeros((RAYS, GATES), "float32")
    snr = np.full((RAYS, GATES), 2., "float32")
    rho = np.full((RAYS, GATES), .99, "float32")
    zdr = np.zeros((RAYS, GATES), "float32")
    phi = np.full((RAYS, GATES), 44., "float32")
    row = 7
    # Confirmed anchor segment and a detached fragment on the same ray; both
    # follow the external source's 20log10(r) power law.
    z[row, 50:66] = 20.; snr[row, 50:66] = 20.; rho[row, 50:66] = .6; zdr[row, 50:66] = 5.
    law = 20. + 20 * np.log10((np.arange(200, 216) + 1) / 56.)
    z[row, 200:216] = law.astype("float32")
    snr[row, 200:216] = 18.; rho[row, 200:216] = .62; zdr[row, 200:216] = 5.
    return z, snr, rho, zdr, phi, row


def test_associate_completes_detached_fragment_on_confirmed_ray():
    z, snr, rho, zdr, phi, row = spoke_fields()
    s = make_sweep(z, rho, zdr, phi, snr)
    anchors = np.zeros(s.shape, bool); anchors[row, 52:60] = True
    jitter = np.zeros(s.shape, "float32")
    linked, record = associate(s, anchors, base_config(),
                               hard=np.zeros(s.shape, bool), local=np.zeros(s.shape, bool),
                               jitter=jitter)
    assert linked[row, 200:216].all()
    assert int(linked.sum()) == int(linked[row].sum())
    assert record["associated_gates"] == int(linked.sum())


def test_rain_rho_is_never_associated():
    z, snr, rho, zdr, phi, row = spoke_fields()
    rho[row, 200:216] = .97  # rain-like gate on the same ray
    s = make_sweep(z, rho, zdr, phi, snr)
    anchors = np.zeros(s.shape, bool); anchors[row, 52:60] = True
    linked, _ = associate(s, anchors, base_config(),
                          hard=np.zeros(s.shape, bool), local=np.zeros(s.shape, bool),
                          jitter=np.zeros(s.shape, "float32"))
    assert not linked[row, 200:216].any()


def test_intensity_mismatch_and_distance_are_respected():
    z, snr, rho, zdr, phi, row = spoke_fields()
    z[row, 200:216] += 20.  # far from the anchor's source law
    s = make_sweep(z, rho, zdr, phi, snr)
    anchors = np.zeros(s.shape, bool); anchors[row, 52:60] = True
    linked, _ = associate(s, anchors, base_config(),
                          hard=np.zeros(s.shape, bool), local=np.zeros(s.shape, bool),
                          jitter=np.zeros(s.shape, "float32"))
    assert not linked[row, 200:216].any()
    near = base_config(fragment_maximum_distance_m=2000.)
    z2, snr2, rho2, zdr2, phi2, _ = spoke_fields()
    s2 = make_sweep(z2, rho2, zdr2, phi2, snr2)
    linked2, _ = associate(s2, anchors, near,
                           hard=np.zeros(s2.shape, bool), local=np.zeros(s2.shape, bool),
                           jitter=np.zeros(s2.shape, "float32"))
    assert not linked2[row, 200:216].any()


def test_sparse_anchor_rays_do_not_associate():
    z, snr, rho, zdr, phi, row = spoke_fields()
    s = make_sweep(z, rho, zdr, phi, snr)
    anchors = np.zeros(s.shape, bool); anchors[row, 52:55] = True  # 3 < 4 gates
    linked, record = associate(s, anchors, base_config(),
                               hard=np.zeros(s.shape, bool), local=np.zeros(s.shape, bool),
                               jitter=np.zeros(s.shape, "float32"))
    assert linked.sum() == 0 and record["anchor_rays"] == 0


def test_phase_circular_variance_alone_confirms_targets():
    # ZDR normal, jitter zero: only the S PHIDP-variance route can associate.
    z, snr, rho, zdr, phi, row = spoke_fields()
    zdr[row, 200:216] = .2
    rng = np.random.default_rng(3)
    phi[row, 200:216] = rng.uniform(0, 360, 216 - 200).astype("float32")
    rho[row, 200:216] = .5
    s = make_sweep(z, rho, zdr, phi, snr)
    anchors = np.zeros(s.shape, bool); anchors[row, 52:60] = True
    linked, _ = associate(s, anchors, base_config(),
                          hard=np.zeros(s.shape, bool), local=np.zeros(s.shape, bool),
                          jitter=np.zeros(s.shape, "float32"))
    assert linked[row, 205:211].any()


def test_protected_and_weather_proxy_gates_are_excluded():
    z, snr, rho, zdr, phi, row = spoke_fields()
    s = make_sweep(z, rho, zdr, phi, snr)
    anchors = np.zeros(s.shape, bool); anchors[row, 52:60] = True
    hard = np.zeros(s.shape, bool); hard[row, 200:216] = True
    linked, _ = associate(s, anchors, base_config(), hard=hard,
                          local=np.zeros(s.shape, bool), jitter=np.zeros(s.shape, "float32"))
    assert not linked[row, 200:216].any()


def test_disabled_by_default_distance_zero():
    cfg = XQCConfig.model_validate({})
    assert cfg.fragment_maximum_distance_m == 0.
