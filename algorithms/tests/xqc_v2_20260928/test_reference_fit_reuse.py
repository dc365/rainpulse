"""Exact training fits may be reused; held-out target actions may not."""

import numpy as np

from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.source_blocks import detect
from rainpulse_algo.multiband.xqc_v2.source_summary import SourceStatistics

from .helpers import config
from .test_radial_source import narrow_source


def recurring_references():
    volume, row = narrow_source()
    cfg = config(noise_censor_snr_db=3.0, radial_source_enabled=True)
    cut = volume.sweeps[0]
    blocks = (cut.range_m // cfg.receiver.block_m).astype(int)
    for block in np.unique(blocks):
        if block % 5 == 1:
            for name in ("SNRH", "DBZH"):
                cut.fields[name][row, blocks == block] += 7
    return adapt(cut, cfg).sweep, cfg, row


def test_repeated_training_sets_complete_with_frozen_17_fit_budget_and_same_actions():
    sweep, cfg, _ = recurring_references()
    before = sweep.digest
    protected = np.zeros(sweep.shape, bool)
    expected, models = detect(sweep, cfg, fan=True, protected=protected)
    assert expected.sum() == 781 and len(models["models"]) == 13
    bounded = cfg.model_copy(update={"source_maximum_trials": 17})
    stats = SourceStatistics.build(sweep, bounded)
    actual, record = detect(sweep, bounded, fan=True, protected=protected, prepared=stats)
    np.testing.assert_array_equal(actual, expected)
    assert record["models"] == models["models"]
    assert stats.trials <= 17
    assert sweep.digest == before


def original_detector():
    """Execute the frozen pre-cache module in RAM, without another checkout."""
    import subprocess
    import types

    source = subprocess.check_output(
        [
            "git",
            "show",
            "d586bfb6aaf6b4c123060a436d2c7b619a797ec4:algorithms/rainpulse_algo/multiband/xqc_v2/source_blocks.py",
        ],
        text=True,
    )
    module = types.ModuleType("rainpulse_algo.multiband.xqc_v2._frozen_reference_blocks")
    module.__package__ = "rainpulse_algo.multiband.xqc_v2"
    exec(compile(source, "<frozen-source-blocks-d586bfb>", "exec"), module.__dict__)
    return module.detect


def test_cached_actions_and_records_equal_frozen_original_across_modes_and_domains():
    old = original_detector()
    sweep, cfg, row = recurring_references()
    before = sweep.digest
    protected = np.zeros(sweep.shape, bool)
    protected[row, 70:80] = True
    domain = np.zeros(sweep.shape, bool)
    domain[row, 15:] = True
    total_fits = 0
    for fan in (False, True):
        for quantile in (25, 75, 90):
            for selected_domain in (None, domain):
                kwargs = dict(
                    fan=fan, response_quantile=quantile, protected=protected, domain=selected_domain
                )
                expected, old_record = old(sweep, cfg, **kwargs)
                stats = SourceStatistics.build(sweep, cfg)
                actual, record = detect(sweep, cfg, prepared=stats, **kwargs)
                np.testing.assert_array_equal(actual, expected)
                assert record == old_record
                total_fits += stats.trials
                for model in record["models"]:
                    assert all(
                        abs(block - model["target_block"]) > cfg.receiver.guard_blocks
                        for block in model["reference_blocks"]
                    )
    # Avoiding a redundant fit can eliminate the need for a cache hit.
    # Actual decisions and complete model records remain the comparison gate.
    assert total_fits > 0
    assert sweep.digest == before


def test_zero_headroom_recomputes_without_changing_actions_or_inheriting_fit():
    sweep, cfg, _ = recurring_references()
    protected = np.zeros(sweep.shape, bool)
    expected, original_record = original_detector()(sweep, cfg, fan=True, protected=protected)
    workspace = SourceStatistics.build(sweep, cfg).workspace_bytes
    bounded = cfg.model_copy(update={"source_maximum_summary_bytes": workspace})
    stats = SourceStatistics.build(sweep, bounded)
    actual, record = detect(sweep, bounded, fan=True, protected=protected, prepared=stats)
    np.testing.assert_array_equal(actual, expected)
    assert record == original_record
    assert stats.fit_cache_hits == stats.fit_cache_peak_bytes == 0
    assert 0 < stats.trials < 23  # Original redundant fits, even without a cache.


def test_reference_cache_bound_eviction_failed_fits_and_trial_guard():
    import pytest

    from rainpulse_algo.multiband.xqc_v2.reference_fit import ReferenceFit, ReferenceFits
    from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

    sweep, cfg, _ = recurring_references()
    stats = SourceStatistics.build(sweep, cfg.model_copy(update={"source_maximum_trials": 3}))
    cache = ReferenceFits(stats, 769)
    calls = []

    def fit():
        calls.append(True)
        return ReferenceFit(0.0, 10.0, 20, 15000.0)

    one = cache.get(b"a", fit)
    assert cache.get(b"a", fit) is one
    assert len(calls) == 1
    assert cache.get(b"b", lambda: None) is None
    assert cache.get(b"b", fit) is None  # A failed training fit is immutable too.
    assert cache.get(b"a", fit) == one
    assert stats.fit_cache_evictions == 2
    assert cache.bytes == stats.fit_cache_peak_bytes == 769
    with pytest.raises(ResourceLimit, match="model-trial"):
        cache.get(b"c", fit)
    assert len(calls) == 2  # Guard fires before any unbudgeted fitting.


def test_independent_raw_rays_and_changed_targets_equal_original():
    volume, row = narrow_source()
    cut = volume.sweeps[0]
    cfg = config(noise_censor_snr_db=3.0, radial_source_enabled=True)
    # A second separated ray has a different receiver power and response.
    for name in ("SNRH", "DBZH"):
        cut.fields[name][row + 20] = cut.fields[name][row] + 3
    cut.fields["OBSERVED_MASK"][:] = np.isfinite(cut.fields["DBZH"])
    old = original_detector()
    for target_change in (0, 12):
        cut.fields["DBZH"][row, 200:240] += target_change
        for guard in (1, 2):
            current = cfg.model_copy(
                update={"receiver": cfg.receiver.model_copy(update={"guard_blocks": guard})}
            )
            sweep = adapt(cut, current).sweep
            protected = np.zeros(sweep.shape, bool)
            expected, old_record = old(sweep, current, fan=True, protected=protected)
            stats = SourceStatistics.build(sweep, current)
            actual, record = detect(sweep, current, fan=True, protected=protected, prepared=stats)
            np.testing.assert_array_equal(actual, expected)
            assert record == old_record
            assert stats.fit_cache_misses > 0
            assert (
                stats.fit_cache_peak_bytes + stats.workspace_bytes
                <= current.source_maximum_summary_bytes
            )
        cut.fields["DBZH"][row, 200:240] -= target_change
