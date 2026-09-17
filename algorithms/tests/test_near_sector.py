from types import SimpleNamespace

import numpy as np

from rainpulse_algo.radar.qc_engine.near_sector import near_sector


def test_source_coherence_gaps_weather_and_missing():
    n = SimpleNamespace(
        shape=(8, 240),
        ranges=np.arange(125, 60000, 250.0),
        gate_spacing_m=250.0,
        gap_after=np.array([0, 0, 0, 0, 0, 0, 0, 1], bool),
    )
    source = np.ones(n.shape, bool)
    protected = np.zeros(n.shape, bool)
    protected[:, 70:75] = True
    result = near_sector(n, source, weather=protected, conflicts=np.zeros(n.shape, bool))
    assert result[3, 140] and not result[:, 70:75].any()
    assert not result[:, n.ranges >= 50000].any()
    source[3] = False
    n.gap_after[:] = True
    assert not near_sector(n, source, weather=protected, conflicts=protected).any()


def test_integrated_sector_quarantine_candidate_and_vetoes():
    from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig, infer_broad_source

    from .test_broad_source import scene

    n = scene()
    n.ranges = np.arange(125, 400000, 250.0)
    n.fields["DBZH"][:] = 15 + 20 * np.log10(n.ranges[None, :] / 1000)
    near = n.ranges < 50000
    n.fields["PHIDP"][:, near] += 90
    old, _ = infer_broad_source(n, BroadSourceConfig(near_range_targets=True))
    weather = np.zeros(n.shape, bool)
    weather[5, 100:110] = True
    conflicts = np.zeros(n.shape, bool)
    conflicts[5, 130:140] = True
    new, summary = infer_broad_source(
        n,
        BroadSourceConfig(near_range_targets=True, near_sector_consensus=True),
        weather=weather,
        conflicts=conflicts,
    )
    assert not old["BWS_CANDIDATE_MASK"][:, near].any()
    assert new["BWS_CANDIDATE_MASK"][2, 140]
    assert not new["BWS_CANDIDATE_MASK"][5, 100:110].any()
    assert not new["BWS_CANDIDATE_MASK"][5, 130:140].any()
    assert new["BWS_REASON"][2, 140] & 512 and new["BWS_REASON"][2, 140] & 16
    assert summary["near_sector"]["candidate_gates"] > 0
