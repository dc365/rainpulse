"""Explicit SNR geometry policy and full-original held-out support controls."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.snr_carriers import SNRCarrierPolicy, review
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .test_fragmented_carriers import policy
from .test_polar_morphology import scene


def configured(**kwargs):
    return SNRCarrierPolicy(geometry=policy(), levels_db=(5.0, 15.0, 25.0), **kwargs)


@pytest.mark.parametrize("bearing,elevation", [(53, 0.5), (178, 3.36), (359, 8)])
@pytest.mark.parametrize("kind", ["line", "fan", "broken"])
def test_original_snr_geometry_nominates_rotated_native_members(kind, bearing, elevation):
    s, body = scene(kind, bearing=bearing, elevation=elevation)
    before = s.digest
    result = review(s, configured(), target=body)
    assert result.candidate.sum() > body.sum() * 0.4
    assert not result.candidate[~body].any()
    assert s.digest == before and result.record["raw_unchanged"]
    assert result.record["moment"] == "SNR" and result.record["levels_db"] == [5.0, 15.0, 25.0]
    assert result.record["work"] <= configured().maximum_work
    assert not result.record["weather_truth"] and not result.record["production_enabled"]


@pytest.mark.parametrize("kind", ["blob", "fixed_km", "curved"])
@pytest.mark.parametrize("bearing", [53, 359])
def test_original_weather_geometry_does_not_restart_as_a_source_tail(kind, bearing):
    s, body = scene(kind, bearing=bearing)
    assert not review(s, configured(), target=body).candidate.any()


def test_explicit_protection_and_native_gaps_remain_barriers():
    s, body = scene("fan")
    assert not review(s, configured(), target=body, protected=body).candidate.any()
    gaps = s.gap_after.copy()
    gaps[100] = True
    assert not review(replace(s, gap_after=gaps), configured(), target=body).candidate.any()
    missing, body = scene("fan", missing_shoulders=True)
    assert not review(missing, configured(), target=body).candidate.any()


def test_localized_power_enhancement_is_not_disguised_by_the_geometry():
    s, body = scene("fan")
    fields = {k: v.copy() for k, v in s.fields.items()}
    core = body & (s.ranges[None, :] >= 25000) & (s.ranges[None, :] < 30000)
    fields["SNR"][core] += 15
    s = replace(s, fields=fields)
    result = review(s, configured(), target=body)
    assert not result.candidate[core].any() and result.local_excess[core].any()
    assert result.candidate.sum() > 100


def test_missing_snr_and_dbzh_availability_never_supply_measurement_votes():
    s, body = scene("fan")
    available = {k: v.copy() for k, v in s.available.items()}
    available["SNR"][:] = False
    assert not review(replace(s, available=available), configured(), target=body).candidate.any()
    available = {k: v.copy() for k, v in s.available.items()}
    available["DBZH"][:] = False
    assert not review(replace(s, available=available), configured(), target=body).candidate.any()


def test_geometry_and_profile_work_have_one_atomic_limit():
    s, body = scene("fan")
    with pytest.raises(ResourceLimit):
        review(s, configured(maximum_work=1), target=body)
    with pytest.raises(ResourceLimit):
        review(s, configured(maximum_summary_bytes=1), target=body)


def test_membership_refusal_precedes_carrier_array_allocation(monkeypatch):
    s, body = scene("fan")
    base = s.shape[0] * s.shape[1] * 25 + s.shape[1] * 8
    concatenations = []
    original = np.concatenate

    def observe(parts, *args, **kwargs):
        if isinstance(parts, list) and parts and all(p.dtype == np.uint32 for p in parts):
            concatenations.append(sum(p.nbytes for p in parts))
        return original(parts, *args, **kwargs)

    monkeypatch.setattr(np, "concatenate", observe)
    with pytest.raises(ResourceLimit, match="membership"):
        review(s, configured(maximum_summary_bytes=base), target=body)
    assert not concatenations


def test_policy_declares_snr_units_and_rejects_invalid_or_implicit_parameters():
    with pytest.raises(ValueError):
        SNRCarrierPolicy(geometry=policy())
    with pytest.raises(ValueError):
        configured(levels_dbz=(5.0, 15.0, 25.0))
    with pytest.raises(ValueError):
        SNRCarrierPolicy(geometry=policy(), levels_db=(5.0, 5.0))
    with pytest.raises(ValueError):
        configured(maximum_work=True)
    s, body = scene("line")
    result = review(
        s, SNRCarrierPolicy(geometry=policy(), levels_db=(10.0, 20.0, 30.0)), target=body
    )
    assert result.record["levels_db"] == [10.0, 20.0, 30.0]


def test_increment_target_is_exact_and_returned_arrays_are_immutable():
    s, body = scene("line")
    target = body.copy()
    target[:, :60] = False
    assert target.any() and (body & ~target).any()
    result = review(s, configured(), target=target)
    assert result.candidate.any() and not result.candidate[~target].any()
    for array in (result.candidate, result.local_excess, result.unknown):
        assert not array.flags.writeable
    with pytest.raises(ValueError):
        review(s, configured(), target=np.zeros((1, 2), bool))
