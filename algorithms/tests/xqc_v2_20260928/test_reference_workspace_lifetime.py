"""Live reference caches and percentile allowances share one byte ceiling."""

import weakref

import numpy as np

from rainpulse_algo.multiband.xqc_v2 import source_blocks
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.source_summary import SourceStatistics

from .helpers import config
from .test_radial_source import narrow_source
from .test_reference_fit_reuse import original_detector


def test_live_ray_cache_and_batch_allowance_never_exceed_shared_ceiling(monkeypatch):
    volume, row = narrow_source()
    cut = volume.sweeps[0]
    cfg = config(noise_censor_snr_db=3.0, radial_source_enabled=True)
    for name in ("SNRH", "DBZH"):
        cut.fields[name][row + 20] = cut.fields[name][row] + 3
    cut.fields["OBSERVED_MASK"][:] = np.isfinite(cut.fields["DBZH"])
    sweep = adapt(cut, cfg).sweep
    raw_digest = sweep.digest
    protected = np.zeros(sweep.shape, bool)
    expected, expected_record = original_detector()(sweep, cfg, fan=True, protected=protected)
    stats = SourceStatistics.build(sweep, cfg)
    caches, allocations, stored_bytes = [], [], []
    original_cache = source_blocks.ReferenceFits
    original_measure = source_blocks.block_percentiles

    class ObservedCache(original_cache):
        def __init__(self, *args):
            super().__init__(*args)
            caches.append(weakref.ref(self))

        def put(self, key, value):
            super().put(key, value)
            stored_bytes.append(self.bytes)

    def observed_measure(*args, maximum_bytes, **kwargs):
        # Weak references observe real lifetimes without keeping caches alive.
        resident = sum(ref().bytes for ref in caches if ref() is not None)
        allocations.append((resident, maximum_bytes))
        assert resident + maximum_bytes + stats.workspace_bytes <= cfg.source_maximum_summary_bytes
        return original_measure(*args, maximum_bytes=maximum_bytes, **kwargs)

    monkeypatch.setattr(source_blocks, "ReferenceFits", ObservedCache)
    monkeypatch.setattr(source_blocks, "block_percentiles", observed_measure)
    actual, record = source_blocks.detect(sweep, cfg, fan=True, protected=protected, prepared=stats)
    assert len(caches) >= 2 and max(stored_bytes) > 0 and allocations
    np.testing.assert_array_equal(actual, expected)
    assert record == expected_record
    assert sweep.digest == raw_digest
