"""Complete double-moving fan envelopes retain a measured central bearing."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.polar_morphology import MorphologyPolicy, detect

from .test_polar_morphology import scene, transverse_policy


def centered_scene(*, bearing=100.0, da=1.0, dr=250.0, elevation=0.5, moving=False):
    s, _ = scene(da=da, dr=dr, elevation=elevation)
    distance = s.ranges[None, :]
    # Complete native history expands, returns, then expands again. Both
    # exteriors move; no fixed edge, restart, or clipped far tail is available.
    half = np.where(distance < 15000, 5, np.where(distance < 35000, 9, 5))
    offset = (s.azimuth[:, None] - bearing + 180) % 360 - 180
    center = 12 * (distance - 5000) / 50000 if moving else 0
    body = (abs(offset - center) <= half) & (distance >= 5000) & (distance < 55000)
    fields = {
        "DBZH": np.where(body, 20, 0).astype("float32"),
        "SNR": np.where(body, 15, -2).astype("float32"),
    }
    return replace(s, fields=fields, available={k: np.ones(s.shape, bool) for k in fields}), body


def policy():
    return transverse_policy().model_copy(
        update={
            "version": "x-polar-morphology-20261002-v9",
            "centered_pulsing_fans_enabled": True,
        }
    )


@pytest.mark.parametrize("bearing", [100.0, 359.0])
@pytest.mark.parametrize(
    "dr,da,elevation", [(75.0, 0.5, 0.47), (250.0, 1.0, 3.36), (1000.0, 2.0, 14.55)]
)
def test_complete_centered_pulse_is_withheld_without_fixed_edge(bearing, dr, da, elevation):
    s, body = centered_scene(bearing=bearing, dr=dr, da=da, elevation=elevation)
    before = s.digest
    assert not detect(s, transverse_policy()).mask.any()  # original reproducible miss
    result = detect(s, policy())
    assert result.mask[body].all()
    assert not result.mask[~body].any()
    assert any(o["kind"] == "centered_pulsing_fan" for o in result.objects)
    assert s.digest == before


@pytest.mark.parametrize("kind", ["fixed_km", "curved", "blob"])
def test_complete_weather_histories_remain_counterexamples(kind):
    s, _ = scene(kind)
    assert not detect(s, policy()).mask.any()


def test_moving_center_missing_shoulders_and_protected_gates_do_not_qualify():
    s, _ = centered_scene(moving=True)
    assert not detect(s, policy()).mask.any()
    s, body = centered_scene()
    available = {k: body.copy() for k in s.fields}
    assert not detect(replace(s, available=available), policy()).mask.any()
    assert not detect(s, policy(), protected=body).mask.any()


def test_centered_pulse_requires_explicit_identity_and_contract():
    import json
    from pathlib import Path

    import jsonschema
    from pydantic import ValidationError

    assert not MorphologyPolicy().centered_pulsing_fans_enabled
    value = policy().model_dump(mode="json")
    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/x-qc-v2.schema.json"
        ).read_text()
    )
    jsonschema.validate({"morphology": value}, schema)
    for change in ({"version": "x-polar-morphology-20261002-v8"}, {"pulsing_fans_enabled": False}):
        bad = value | change
        with pytest.raises(ValidationError):
            MorphologyPolicy.model_validate(bad)
        assert list(jsonschema.Draft202012Validator(schema).iter_errors({"morphology": bad}))
