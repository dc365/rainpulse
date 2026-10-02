"""A width admission cap cannot erase original negative boundary history."""

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.polar_morphology import MorphologyPolicy, detect

from .test_centered_pulsing_morphology import policy as current_policy
from .test_polar_morphology import scene


def contracted_parent(*, dr=250.0, da=1.0, bearing=100.0, elevation=0.5, width_limit=45.0):
    s, _ = scene(dr=dr, da=da, bearing=bearing, elevation=elevation)
    r = np.arange(dr, 140000.0 + dr / 2, dr)
    offset = (s.azimuth[:, None] - bearing + 180.0) % 360.0 - 180.0
    half = np.where(r[None, :] < 70000.0, width_limit / 2 + 10, 5.0)
    body = (abs(offset) <= half) & (r[None, :] >= 5000.0)
    fields = {
        "DBZH": np.where(body, 30.0, 0.0).astype("float32"),
        "SNR": np.where(body, 15.0, -2.0).astype("float32"),
    }
    sweep = replace(
        s,
        ranges=r,
        fields=fields,
        available={k: np.ones(body.shape, bool) for k in fields},
        no_echo=np.zeros(body.shape, bool),
    )
    return sweep, body


@pytest.mark.parametrize("version", ["original", "current"])
@pytest.mark.parametrize("bearing", [100.0, 359.0])
@pytest.mark.parametrize(
    "dr,da,elevation", [(75.0, 0.5, 0.47), (250.0, 1.0, 3.36), (1000.0, 2.0, 14.55)]
)
def test_oversize_original_parent_cannot_restart_at_its_narrow_tail(
    version, bearing, dr, da, elevation
):
    s, body = contracted_parent(dr=dr, da=da, bearing=bearing, elevation=elevation)
    cfg = MorphologyPolicy() if version == "original" else current_policy()
    before = s.digest
    result = detect(s, cfg)
    assert not result.mask.any(), "wide original predecessor was lost before tail qualification"
    assert body.any() and s.digest == before
    assert any(
        r["angular_width_deg"] > cfg.maximum_width_deg for r in result.record["review_objects"]
    )


def test_width_cap_stays_a_local_qualification_cap_without_vetoing_other_objects():
    s, body = contracted_parent(width_limit=32.0)
    other = (
        (s.azimuth[:, None] == 270.0)
        & (s.ranges[None, :] >= 5000.0)
        & (s.ranges[None, :] < 135000.0)
    )
    fields = {k: v.copy() for k, v in s.fields.items()}
    fields["DBZH"][other] = 30.0
    fields["SNR"][other] = 15.0
    result = detect(replace(s, fields=fields), MorphologyPolicy(maximum_width_deg=32.0))
    assert result.mask[other].all()
    assert not result.mask[body].any()
