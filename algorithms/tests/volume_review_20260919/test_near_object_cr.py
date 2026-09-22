import json

import numpy as np
from helpers import root_from_sweeps, sweep
from volume_review.composite import build_composite, diagnostic_composite


def near_object_root(*, rho_object=0.55, background=True, protected=False):
    s = sweep()
    root = root_from_sweeps([s])
    root.attrs["qc_near_measurement_sha256"] = "c" * 64
    g = root["sweep_000"]
    shape = s.shape
    g["DBZH_RAW"][:] = 12.0
    g["DBZH_QC"][:] = 12.0
    g["VALID_MASK"][:] = 1
    g["REFLECTIVITY_ELIGIBLE_FOR_CR"][:] = 1
    g["RHOHV_RAW"] = np.full(shape, 0.99, dtype="float32")
    g["SNR_RAW"] = np.full(shape, 15.0, dtype="float32")
    for name in (
        "NMR_NONMET_CANDIDATE_MASK", "NMR_LOW_SNR_UNCERTAIN_MASK",
        "NMR_CR_WITHHELD_MASK", "NMR_PROTECTED_MASK",
        "CF_HARD_WEATHER_MASK", "CF_LOCAL_WEATHER_MASK",
        "CF_BG_STABLE_MASK", "CF_BG_MATCH_MASK",
    ):
        g[name] = np.zeros(shape, "uint8")
    g["CF_BG_DBZH_DEPARTURE_DB"] = np.zeros(shape, "float32")
    # A compact 20-28 km object, away from the immediate station center.
    g["RHOHV_RAW"][8:14, 40:56] = rho_object
    if background:
        g["CF_BG_STABLE_MASK"][8:14, 40:56] = 1
        g["CF_BG_MATCH_MASK"][8:14, 40:56] = 1
    if protected:
        g["NMR_PROTECTED_MASK"][8:14, 40:56] = 1
    return root, s


def test_near_object_evidence_removes_only_supported_object_and_preserves_center():
    root, _ = near_object_root()
    product = build_composite([root], 0, maximum_size=128)
    withheld = np.isfinite(product.arrays["CR_NEAR_OBJECT_WITHHELD"])
    assert withheld.any()
    assert np.isnan(product.arrays["CR_TRUSTED"][withheld]).all()
    # Weak gates near the source remain admitted when they lack object evidence.
    shape = product.arrays["CR_TRUSTED"].shape
    station_box = (
        slice(shape[0] // 2 - 3, shape[0] // 2 + 3),
        slice(shape[1] // 2 - 3, shape[1] // 2 + 3),
    )
    assert np.isfinite(product.arrays["CR_RAW"][station_box]).any()
    assert np.isfinite(product.arrays["CR_TRUSTED"][station_box]).any()
    assert "CR_NEAR_RANGE_WEAK_WITHHELD" not in product.arrays


def test_near_object_requires_weather_evidence_not_proximity_alone():
    root, _ = near_object_root(background=False, rho_object=0.99)
    product = build_composite([root], 0, maximum_size=128)
    # No polarization, background, or uncertainty family is present, so source
    # proximity and weak reflectivity alone must not withhold the gates.
    assert not np.isfinite(product.arrays["CR_NEAR_OBJECT_WITHHELD"]).any()


def test_near_object_is_blocked_by_weather_protection():
    root, _ = near_object_root(protected=True)
    product = build_composite([root], 0, maximum_size=128)
    assert not np.isfinite(product.arrays["CR_NEAR_OBJECT_WITHHELD"]).any()


def test_composite_receipt_describes_object_policy_not_fixed_radius():
    root, _ = near_object_root()
    objects = {}
    diagnostic_composite(
        [root], 0, objects=objects,
        legacy_compositor=lambda *_a, **_kw: (np.full((2, 2), np.nan), [0, 0, 1, 1]),
    )
    meta = json.loads(objects["volume_review/composite.json"])
    assert meta["near_object_policy"]["action"] == "cr_withhold_before_maximum"
    assert meta["near_object_policy"]["minimum_range_m"] == 2_000
    assert "near_range_weak_policy" not in meta
