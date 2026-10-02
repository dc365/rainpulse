"""Causal source footprints provide evidence, never recurrence-only actions."""

import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.source_temporal import (
    SourceFrame,
    diagnose,
)
from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep


def frame(time=1000, *, seed=True, radar="site", scan="past", shift=0):
    shape = (360, 160)
    z = np.full(shape, np.nan, "float32")
    z[90, 40:130] = 15
    snr = np.where(np.isfinite(z), 4, np.nan).astype("float32")
    sweep = Sweep(
        scan,
        (np.arange(360) + shift) % 360,
        np.full(360, 0.5),
        125 + np.arange(160) * 250,
        {"DBZH": z, "SNR": snr},
        {"DBZH": np.isfinite(z), "SNR": np.isfinite(snr)},
        np.ones(360, bool),
        np.zeros(360, bool),
        np.full(360, time),
    )
    ids = np.where(np.isfinite(z) & seed, 7, 0).astype("uint32")
    return SourceFrame(
        sweep,
        ids,
        np.zeros(shape, "uint16"),
        np.zeros(shape, bool),
        radar,
        scan,
        "a" * 64,
        "lineage-v1",
    )


def test_original_seed_matching_has_no_action_authority():
    now, old = frame(1400, seed=False, scan="now"), frame()
    arrays, report = diagnose(now, [old])
    ix = (90, 80)
    assert arrays["ST_MATCH_MASK"][ix] == 1
    assert arrays["ST_ORIGINAL_SOURCE_ID"][ix] == 7
    assert report["actions"] == 0
    assert not report["source_agreement_is_pollution_truth"]
    assert now.sweep.fields["DBZH"][ix] == 15


@pytest.mark.parametrize("kind", ["future", "self", "radar", "lineage", "old", "elevation"])
def test_inapplicable_frames_are_not_votes(kind):
    from dataclasses import replace

    now, old = frame(1400, seed=False, scan="now"), frame()
    if kind == "future":
        old = frame(1500)
    if kind == "self":
        old = replace(old, scan_id="now")
    if kind == "radar":
        old = replace(old, radar_id="other")
    if kind == "lineage":
        old = replace(old, lineage_id="other")
    if kind == "old":
        now = frame(3000, seed=False, scan="now")
    if kind == "elevation":
        old = replace(old, sweep=replace(old.sweep, elevation=np.full(360, 1.5)))
    arrays, report = diagnose(now, [old])
    assert not arrays["ST_MATCH_MASK"].any()
    assert report["rejected"]


def test_recent_measured_conflict_prevents_older_cherry_picking():
    from dataclasses import replace

    now, old = frame(1600, seed=False, scan="now"), frame()
    recent = frame(1400, seed=False, scan="recent")
    z = recent.sweep.fields["DBZH"].copy()
    z[90, 80] = 25
    recent = replace(recent, sweep=replace(recent.sweep, fields={**recent.sweep.fields, "DBZH": z}))
    a, _ = diagnose(now, [old, recent])
    assert a["ST_MEASURED_MASK"][90, 80] == 1
    assert a["ST_MATCH_MASK"][90, 80] == 0
    assert a["ST_SOURCE_INDEX"][90, 80] == 0


def test_unknown_snr_in_latest_dbzh_observation_cannot_select_older_seed():
    from dataclasses import replace

    now, old = frame(1600, seed=False, scan="now"), frame()
    recent = frame(1400, scan="recent")
    av = dict(recent.sweep.available)
    av["SNR"] = av["SNR"].copy()
    av["SNR"][90, 80] = False
    recent = replace(recent, sweep=replace(recent.sweep, available=av))
    a, _ = diagnose(now, [old, recent])
    assert a["ST_MATCH_MASK"][90, 80] == 0
    assert a["ST_SOURCE_INDEX"][90, 80] == 0
    assert a["ST_SNR_MEASURED_MASK"][90, 80] == 0
    assert np.isnan(a["ST_SNR_DELTA_DB"][90, 80])


def test_no_seed_growth_weather_and_gap_guards():
    from dataclasses import replace

    now, old = frame(1400, seed=False, scan="now"), frame()
    seeds = old.seed_id.copy()
    seeds[90, 80] = 0
    a, _ = diagnose(now, [replace(old, seed_id=seeds)])
    assert a["ST_MATCH_MASK"][90, 80] == 0
    barred = old.blocked.copy()
    barred[90, 80] = True
    # Invalid original seed authority is rejected rather than silently accepted.
    with pytest.raises(ValueError, match="seed"):
        replace(old, blocked=barred)
    gaps = old.sweep.gap_after.copy()
    gaps[89] = True
    a, _ = diagnose(now, [replace(old, sweep=replace(old.sweep, gap_after=gaps))])
    assert not a["ST_MATCH_MASK"].any()


def test_duplicate_frames_do_not_increase_support_and_seam_is_physical():
    now, old = frame(1400, seed=False, scan="now", shift=270), frame(shift=270)
    a, r = diagnose(now, [old, old])
    assert a["ST_MATCH_MASK"].sum() == 90
    assert len(r["sources"]) == 1


def test_unqualified_source_boundary_is_only_diagnostic():
    from dataclasses import replace

    now, old = frame(1400, seed=False, scan="now"), frame()
    hold = old.source_hold.copy()
    hold[old.seed_id > 0] = 2
    a, _ = diagnose(now, [replace(old, source_hold=hold)])
    assert a["ST_MATCH_MASK"].sum() == 90
    assert not a["ST_BOUNDARY_SUPPORTED_MASK"].any()


def test_budget_is_checked_before_any_output():
    from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

    with pytest.raises(ResourceLimit):
        diagnose(frame(1400, seed=False, scan="now"), [frame()], maximum_pairs=1)


def test_duplicate_scan_with_different_file_sha_is_not_an_extra_vote():
    from dataclasses import replace

    now, old = frame(1400, seed=False, scan="now"), frame()
    a, r = diagnose(now, [replace(old, source_sha256="b" * 64), old])
    assert a["ST_MATCH_MASK"].sum() == 90
    assert len(r["sources"]) == 1
    ids = old.seed_id.copy()
    ids[90, 80] = 0
    with pytest.raises(ValueError, match="changed RAW"):
        diagnose(now, [old, replace(old, seed_id=ids)])


def test_stationary_weather_is_retained():
    from dataclasses import replace

    now, old = frame(1400, seed=False, scan="now"), frame()
    rho = np.full(old.sweep.shape, 0.99, "float32")
    snr = np.full(old.sweep.shape, 12, "float32")
    old = replace(
        old,
        sweep=replace(
            old.sweep,
            fields={**old.sweep.fields, "SNR": snr, "RHOHV": rho},
            available={**old.sweep.available, "RHOHV": np.ones(old.sweep.shape, bool)},
        ),
    )
    a, _ = diagnose(now, [old])
    assert a["ST_MEASURED_MASK"].sum() == 90
    assert not a["ST_MATCH_MASK"].any()
