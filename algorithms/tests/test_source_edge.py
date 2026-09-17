from types import SimpleNamespace

import numpy as np

from rainpulse_algo.radar.qc_engine.source_edge import source_edge


def test_edge_nonrecursive_protection_missing_and_gap():
    z = np.full((5, 800), 50.0)
    n = SimpleNamespace(
        shape=z.shape,
        fields={"DBZH": z},
        field_available={"DBZH": np.ones(z.shape, bool)},
        geometry_good=np.ones(5, bool),
        gap_after=np.array([0, 0, 0, 0, 1], bool),
        full_ppi=False,
        azimuth=np.arange(5.0),
        gate_spacing_m=250.0,
    )
    seed = np.zeros(z.shape, bool)
    seed[1] = True
    ref = np.ones(z.shape, bool)
    residual = np.zeros(z.shape)
    weather = np.zeros(z.shape, bool)
    conflict = weather.copy()
    weather[2, 100] = True
    conflict[2, 101] = True
    residual[2, 102] = 5
    args = dict(weather=weather, conflicts=conflict)
    mask, _ = source_edge(n, seed, ref, residual, **args)
    assert mask[2, 200] and not mask[3].any()
    assert not mask[2, 100:103].any()
    assert not mask[4].any()
    n.field_available["DBZH"][1, 200] = False
    mask, _ = source_edge(n, seed, ref, residual, **args)
    assert not mask[2, 200] and mask[2, 201]
    n.gap_after[1] = True
    mask, _ = source_edge(n, seed, ref, residual, **args)
    assert not mask[2].any()


def test_integrated_edge_respects_snr_weather_and_conflict():
    from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig, infer_broad_source

    from .test_broad_source import scene

    n = scene()
    # Break angular intercept agreement at a source edge, retaining its own range law.
    n.fields["DBZH"][0] -= 4
    old, _ = infer_broad_source(n, BroadSourceConfig(radial_opening=True))
    weather = np.zeros(n.shape, bool)
    weather[0, 400:420] = True
    conflicts = np.zeros(n.shape, bool)
    conflicts[0, 430:450] = True
    n.fields["SNR"][0, 460:470] = 10
    new, summary = infer_broad_source(
        n,
        BroadSourceConfig(radial_opening=True, source_edge=True),
        weather=weather,
        conflicts=conflicts,
    )
    assert not old["BWS_CANDIDATE_MASK"][0].any()
    assert new["BWS_CANDIDATE_MASK"][0, 600]
    assert not new["BWS_CANDIDATE_MASK"][0, 400:420].any()
    assert not new["BWS_CANDIDATE_MASK"][0, 430:450].any()
    assert not new["BWS_CANDIDATE_MASK"][0, 460:470].any()
    assert summary["source_edge"]["candidate_gates"] > 0
    assert new["BWS_REASON"][0, 600] & 128
