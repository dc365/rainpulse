"""The delivered network releases must carry the measured X radial tuning."""
from rainpulse_algo.multiband.xqc_v2.profiles import RADIAL_TUNING, generate


def parent():
    return {
        "schema_version": "1.0",
        "release_id": "x-tuning-parent-v1",
        "stations": {
            "x1": {"band": "X", "source": "normalized_zarr", "x_qc_enabled": True,
                   "x_qc": {"attenuation": "none", "require_snr": True}},
            "s1": {"band": "S", "source": "normalized_zarr", "enabled": True},
        },
        "products": {},
        "maximum_input_bytes": 512 * 1024**2,
    }


def test_generated_profiles_carry_radial_tuning_and_input_budget():
    profiles = generate(parent(), ["x1"], "wradlib")
    assert set(profiles) == {"audit", "radial", "all-cr", "all-quarantine"}
    for name, network in profiles.items():
        enhancement = network["stations"]["x1"]["x_qc"]["enhancement"]
        for key, value in RADIAL_TUNING.items():
            assert enhancement[key] == value, name + " " + key
        # Dual-pol 40-cut X volumes decode past the legacy 512 MiB bound.
        assert network["maximum_input_bytes"] >= 1024 * 1024**2
        assert network["stations"]["x1"]["x_qc"]["attenuation"] == "none"
        # S stations and absent enhancements stay exactly on v1 behavior.
        assert "x_qc" not in network["stations"]["s1"]


def test_radial_tuning_stays_inside_conservative_bounds():
    # Rain protection: RHOHV above the cap can never be flagged.
    assert RADIAL_TUNING["radial_maximum_rhohv"] <= .90
    # Intensity alone never triggers removal: the cap is not an action gate.
    assert RADIAL_TUNING["radial_maximum_dbzh"] <= 45.
    assert RADIAL_TUNING["radial_flank_contrast_db"] >= 6.
    assert RADIAL_TUNING["radial_minimum_snr_db"] >= 8.
    # Fragment completion stays a bounded, locally corroborated extension.
    assert 0 < RADIAL_TUNING["fragment_maximum_distance_m"] <= 20000.
    assert RADIAL_TUNING["fragment_minimum_anchor_gates"] >= 3
    assert RADIAL_TUNING["fragment_association_difference_db"] <= 10.
