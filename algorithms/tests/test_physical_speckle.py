"""RAW physical isolation regressions, with measured noise and weather controls."""

from types import SimpleNamespace

import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.residual_profile import ResidualConfig
from rainpulse_algo.radar.qc_engine.speckle_review import speckle_candidates
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.isolation_config import (
    IsolationConfig,
)


def scene(dr=250.0, shift=0.0):
    ranges = dr / 2 + np.arange(int(40000 / dr)) * dr
    shape = (360, len(ranges))
    z = np.full(shape, np.nan, "float32")
    target = (90, int(20000 / dr))
    z[target] = 15.0
    snr = np.full(shape, -10.0, "float32")
    snr[target] = 4.0
    n = SimpleNamespace(
        name="sweep_000",
        shape=shape,
        ranges=ranges,
        azimuth=(np.arange(360) + shift) % 360,
        elevation=np.full(360, 0.5),
        geometry_good=np.ones(360, bool),
        gap_after=np.zeros(360, bool),
        fields={"DBZH": z, "SNR": snr},
        field_available={"DBZH": np.isfinite(z), "SNR": np.isfinite(snr)},
        gate_spacing_m=dr,
        full_ppi=True,
        audit={"azimuth_spacing_deg": 1.0},
        support=lambda *args: np.ones(shape, bool),
    )
    return n, target


def evaluate(n, eligible=None, protected=None):
    cfg = ResidualConfig().model_copy(
        update={"speckle_physical_support": IsolationConfig(weak_diagnostic_enabled=False)}
    )
    empty = np.zeros(n.shape, bool)
    return speckle_candidates(
        n,
        cfg,
        baseline_eligible=np.isfinite(n.fields["DBZH"]) if eligible is None else eligible,
        protected=empty if protected is None else protected,
        pol_bad=empty,
        low_snr=n.field_available["SNR"] & (n.fields["SNR"] < 8),
    )[0]


def test_observed_noise_surroundings_are_usable_without_fabricated_dbzh():
    n, ix = scene()
    before = n.fields["DBZH"].copy()
    a = evaluate(n)
    assert a["V6_SPECKLE_CANDIDATE_MASK"][ix] == 1
    assert np.array_equal(before, n.fields["DBZH"], equal_nan=True)
    assert not (a["V6_SPECKLE_CANDIDATE_MASK"].astype(bool) & ~n.field_available["DBZH"]).any()


@pytest.mark.parametrize(
    "kind", ["unknown", "nonquiet", "weather", "no_target_noise", "strong", "gap", "protected"]
)
def test_isolation_rejection_controls(kind):
    n, ix = scene()
    protected = np.zeros(n.shape, bool)
    if kind == "unknown":
        n.field_available["SNR"][:] = False
    if kind == "nonquiet":
        n.fields["SNR"][:] = 4.0
    if kind == "weather":
        n.fields["RHOHV"] = np.full(n.shape, np.nan, "float32")
        n.fields["RHOHV"][ix] = 0.99
        n.field_available["RHOHV"] = np.isfinite(n.fields["RHOHV"])
        n.fields["SNR"][ix] = 20.0
    if kind == "no_target_noise":
        n.field_available["SNR"][ix] = False
    if kind == "strong":
        n.fields["DBZH"][ix] = 40.0
    if kind == "gap":
        n.gap_after[89] = True
    if kind == "protected":
        protected[ix] = True
    assert not evaluate(n, protected=protected)["V6_SPECKLE_CANDIDATE_MASK"].any()


def test_complete_raw_parent_cannot_shrink_after_prior_qc():
    n, ix = scene()
    n.fields["DBZH"][90, 40:130] = 15.0
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    n.fields["SNR"][90, 40:130] = 4.0
    eligible = np.zeros(n.shape, bool)
    eligible[ix] = True
    assert not evaluate(n, eligible)["V6_SPECKLE_CANDIDATE_MASK"].any()


@pytest.mark.parametrize("dr", [125.0, 250.0, 500.0])
@pytest.mark.parametrize("shift", [0.0, 270.0])
def test_physical_scale_and_north_seam(dr, shift):
    n, ix = scene(dr, shift)
    assert evaluate(n)["V6_SPECKLE_CANDIDATE_MASK"][ix] == 1


def serialized_scene(n):
    a = evaluate(n)
    return {
        **a,
        **{k + "_RAW": v.copy() for k, v in n.fields.items()},
        "azimuth": n.azimuth.copy(),
        "elevation": n.elevation.copy(),
        "range": n.ranges.copy(),
        "VALID_MASK": n.field_available["DBZH"].astype("uint8"),
        "V6_BASELINE_ELIGIBLE_MASK": n.field_available["DBZH"].astype("uint8"),
    }


def test_replay_acquisition_order_and_raw_tamper():
    from rainpulse_algo.radar.qc_engine.physical_speckle import validate_serialized

    cfg = ResidualConfig(speckle_physical_support=IsolationConfig(weak_diagnostic_enabled=False))
    n, ix = scene(shift=270.0)
    g = serialized_scene(n)
    validate_serialized(g, cfg.model_dump(mode="json"))
    order = np.roll(np.arange(360), 23)
    g = {k: v if k == "range" else v[order] for k, v in g.items()}
    validate_serialized(g, cfg.model_dump(mode="json"))
    g["DBZH_RAW"][np.where(order == ix[0])[0][0], ix[1]] += 0.5
    with pytest.raises(ValueError, match="RAW replay differs"):
        validate_serialized(g, cfg.model_dump(mode="json"))


@pytest.mark.parametrize(
    "key",
    [
        "V6_SPECKLE_CANDIDATE_MASK",
        "V6_PHYSICAL_SPECKLE_KNOWN_FRACTION",
        "V6_PHYSICAL_SPECKLE_INPUT_GAP",
    ],
)
def test_replay_detects_forged_evidence(key):
    from rainpulse_algo.radar.qc_engine.physical_speckle import validate_serialized

    cfg = ResidualConfig(speckle_physical_support=IsolationConfig(weak_diagnostic_enabled=False))
    n, ix = scene()
    g = serialized_scene(n)
    g[key][ix] = 0 if g[key][ix] else 1
    with pytest.raises(ValueError, match="physical speckle"):
        validate_serialized(g, cfg.model_dump(mode="json"))


def test_absent_policy_preserves_serialized_configuration():
    cfg = ResidualConfig()
    assert "speckle_physical_support" not in cfg.model_dump(mode="json")
    with pytest.raises(ValueError, match="evidence only"):
        ResidualConfig(speckle_physical_support=IsolationConfig(mode="quarantine"))


def test_resource_exhaustion_has_no_partial_decision():
    n, _ = scene()
    cfg = ResidualConfig(speckle_physical_support=IsolationConfig(maximum_sample_points=1))
    arrays, report = speckle_candidates(
        n,
        cfg,
        baseline_eligible=n.field_available["DBZH"],
        protected=np.zeros(n.shape, bool),
        pol_bad=np.zeros(n.shape, bool),
        low_snr=n.field_available["SNR"] & (n.fields["SNR"] < 8),
    )
    assert report["status"] == "RESOURCE_LIMIT_ABSTAINED"
    assert not arrays["V6_SPECKLE_CANDIDATE_MASK"].any()


def test_engine_withholds_data_and_serialized_reason_is_checked():
    from rainpulse_algo.radar.qc_engine.decision import Decision
    from rainpulse_algo.radar.qc_engine.physical_speckle import validate_serialized
    from rainpulse_algo.radar.qc_engine.residual import residual_decision
    from rainpulse_algo.radar.qc_engine.residual_validation import validate_residual_fields

    n, ix = scene()
    n.fields["SNR"][ix] = 2.0
    cfg = ResidualConfig(
        repair_range_links=False,
        narrow_enabled=False,
        association_enabled=False,
        speckle_physical_support=IsolationConfig(weak_diagnostic_enabled=False),
    )
    profile = SimpleNamespace(
        residual=cfg,
        residual_repair=None,
        echo=SimpleNamespace(low_snr_db=3.0),
        context=SimpleNamespace(strong_support=0.8),
        geometry=SimpleNamespace(phase_period_deg=360.0),
        literature=SimpleNamespace(
            fusion=SimpleNamespace(
                low_rhohv=0.8,
                zdr_outlier_db=(-4.0, 7.0),
                phase_window_gates=7,
                phase_pair_jump_deg=30.0,
                phase_minimum_pair_fraction=0.6,
                phase_bad_pair_fraction=0.5,
            )
        ),
        flag_masks={"RADIAL_INTERFERENCE": 1, "NON_METEOROLOGICAL": 2, "LOW_QUALITY": 4},
    )
    observed = n.field_available["DBZH"]
    empty = np.zeros(n.shape, "uint8")
    a = {
        "QC_ACTION": np.where(observed, 0, 3).astype("uint8"),
        "QPE_ELIGIBLE_MASK": observed.astype("uint8"),
        "RFI_QUARANTINE_MASK": empty.copy(),
        "RFI_RISK_STATE": empty.copy(),
        "RFI_MIXED_MASK": empty.copy(),
    }
    for field in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
        a[field + "_TRUST_MASK"] = observed.astype("uint8")
    baseline = Decision(
        a, np.zeros(n.shape, "uint32"), np.where(observed, 0.9, np.nan).astype("float32")
    )
    result, report = residual_decision(n, baseline, profile)
    assert report["quarantined_additions"] == 1
    assert result.arrays["QPE_ELIGIBLE_MASK"][ix] == 0
    assert np.isnan(result.arrays["DBZH_USABLE"][ix])
    assert n.fields["DBZH"][ix] == 15.0
    validate_residual_fields(
        result.arrays,
        observed,
        result.arrays["QC_ACTION"] == 2,
        result.arrays["RFI_QUARANTINE_MASK"] == 1,
    )
    group = {
        **result.arrays,
        **{k + "_RAW": v for k, v in n.fields.items()},
        "azimuth": n.azimuth,
        "elevation": n.elevation,
        "range": n.ranges,
        "VALID_MASK": observed.astype("uint8"),
    }
    validate_serialized(group, cfg.model_dump(mode="json"))


def test_explicit_raw_no_echo_support_without_snr_background():
    n, ix = scene()
    n.fields["SNR"][:] = np.nan
    n.fields["SNR"][ix] = 2.0
    n.field_available["SNR"] = np.isfinite(n.fields["SNR"])
    n.no_echo = ~n.field_available["DBZH"]
    assert evaluate(n)["V6_SPECKLE_CANDIDATE_MASK"][ix] == 1
    n.no_echo[:] = False
    assert not evaluate(n)["V6_SPECKLE_CANDIDATE_MASK"].any()


def test_tiny_weather_neighbour_and_far_coarse_cell_are_preserved():
    n, ix = scene()
    neighbour = (85, ix[1])
    n.fields["DBZH"][neighbour] = 55.0
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    assert not evaluate(n)["V6_SPECKLE_CANDIDATE_MASK"].any()
    n, ix = scene()
    n.ranges = n.ranges + 200000.0
    assert not evaluate(n)["V6_SPECKLE_CANDIDATE_MASK"].any()


def test_parent_cutting_has_a_positive_legacy_control():
    n, ix = scene()
    n.fields["DBZH"][:] = -20.0
    n.fields["DBZH"][90, 40:130] = 15.0
    n.field_available["DBZH"][:] = True
    n.fields["SNR"][90, 40:130] = 2.0
    eligible = np.zeros(n.shape, bool)
    eligible[ix] = True
    common = dict(
        baseline_eligible=eligible,
        protected=np.zeros(n.shape, bool),
        pol_bad=np.zeros(n.shape, bool),
        low_snr=n.fields["SNR"] < 3,
    )
    legacy = ResidualConfig(speckle_maximum_raw_echo_fraction=0.4)
    old, _ = speckle_candidates(n, legacy, **common)
    physical = legacy.model_copy(update={"speckle_physical_support": IsolationConfig()})
    new, _ = speckle_candidates(n, physical, **common)
    assert old["V6_SPECKLE_CANDIDATE_MASK"][ix] == 1
    assert new["V6_SPECKLE_CANDIDATE_MASK"][ix] == 0
