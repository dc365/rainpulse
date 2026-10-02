"""Beam sampling must tolerate native angle jitter without inventing quiet flanks."""

import numpy as np
import pytest

from .conftest import Native, load


def fixture(spacing=0.99, shift=0):
    z = np.zeros((13, 160), "float32")
    z[6, 20:140] = 20
    n = Native(z, start=0.0)
    n.azimuth = (shift + np.arange(13) * spacing) % 360
    seed = np.zeros(n.shape, bool)
    seed[6, 20:100] = True
    targets = np.zeros(n.shape, bool)
    targets[6, 110:115] = True
    return n, seed, targets


@pytest.mark.parametrize("spacing,shift", [(0.99, 0), (1.01, 0), (0.49, 0), (0.99, 355)])
def test_measured_single_ray_is_not_lost_to_outward_rounding(spacing, shift):
    n, seed, targets = fixture(spacing, shift)
    out, _ = load("radial_revision.source_ledger").freeze(
        n, np.zeros(n.shape, bool), seed, targets, beam_width=1 if spacing > 0.5 else 0.5
    )
    assert not out["RV2_SOURCE_LEDGER_SOURCE_HOLD"][seed].any()
    assert out["RV2_SOURCE_LEDGER_LINK_MASK"][targets].all()


def test_new_nearest_flanks_cannot_treat_missing_as_quiet():
    n, seed, targets = fixture()
    n.fields["DBZH"][5] = np.nan
    n.fields["DBZH"][7] = np.nan
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    out, _ = load("radial_revision.source_ledger").freeze(
        n, np.zeros(n.shape, bool), seed, targets, beam_width=1
    )
    assert (out["RV2_SOURCE_LEDGER_SOURCE_HOLD"][seed] & 2).all()
    assert not out["RV2_SOURCE_LEDGER_LINK_MASK"].any()


def test_new_nearest_flanks_respect_protection_and_actual_broad_echo():
    n, seed, targets = fixture()
    blocked = np.zeros(n.shape, bool)
    blocked[5, 60] = True
    out, _ = load("radial_revision.source_ledger").freeze(n, blocked, seed, targets, beam_width=1)
    assert not out["RV2_SOURCE_LEDGER_LINK_MASK"].any()
    n.fields["DBZH"][1:12, 20:140] = 20
    out, _ = load("radial_revision.source_ledger").freeze(
        n, np.zeros(n.shape, bool), seed, targets, beam_width=1
    )
    assert not out["RV2_SOURCE_LEDGER_LINK_MASK"].any()


@pytest.mark.parametrize(
    "known,snr_value,recovered",
    [(True, 0.0, True), (True, 8.0, False), (False, 0.0, False), (True, -100.0, False)],
)
def test_quiet_receiver_support_is_measured_and_bounded(known, snr_value, recovered):
    n, seed, targets = fixture()
    n.fields["DBZH"][5] = np.nan
    n.fields["DBZH"][7] = np.nan
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    n.fields["SNR"] = np.full(n.shape, snr_value, "float32")
    n.field_available["SNR"] = np.full(n.shape, known, bool)
    out, _ = load("radial_revision.source_ledger").freeze(
        n, np.zeros(n.shape, bool), seed, targets, beam_width=1
    )
    assert bool(out["RV2_SOURCE_LEDGER_LINK_MASK"][targets].all()) == recovered


def test_recovered_envelope_preserves_raw_and_original_angular_distance_bound():
    n, seed, targets = fixture()
    raw = n.fields["DBZH"].copy()
    out, _ = load("radial_revision.source_envelope").detect(
        n, np.zeros(n.shape, bool), seed, targets, beam_width=1
    )
    assert out["RV2_ENVELOPE_MASK"][targets].all()
    assert not out["RV2_ENVELOPE_MASK"][seed].any()
    assert not out["RV2_ENVELOPE_MASK"][8:].any()
    assert np.array_equal(raw, n.fields["DBZH"])
    # A second-hop target cannot borrow authority from a recovered first hop.
    n.fields["DBZH"][7, 110:115] = 5
    n.fields["DBZH"][8, 116:119] = 5
    out, _ = load("radial_revision.source_envelope").detect(
        n, np.zeros(n.shape, bool), seed, targets, beam_width=1
    )
    assert not out["RV2_ENVELOPE_MASK"][8, 116:119].any()


def test_supplemental_sampling_cannot_expand_legacy_protection_stencil():
    n, seed, targets = fixture()
    n.fields["DBZH"][5:8, 20:140] = 20
    blocked = np.zeros(n.shape, bool)
    blocked[10, 60] = True
    out, _ = load("radial_revision.source_ledger").freeze(n, blocked, seed, targets, beam_width=1.4)
    assert not out["RV2_SOURCE_LEDGER_SOURCE_HOLD"][seed].any()
    assert out["RV2_SOURCE_LEDGER_LINK_MASK"][targets].all()
