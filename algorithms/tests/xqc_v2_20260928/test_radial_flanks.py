"""Flank mode: X interference bleeds into the adjacent ray on one side."""
import numpy as np
from .helpers import config, fixture


def bled_volume():
    # Flat spoke row with a quiet neighbour above and an interference-bleed
    # filling the whole flank window below (6 dB contrast only, two rays at
    # 3 deg spacing): the both-sided gate cannot find a quiet comparison.
    v, row = fixture("flat")
    z = v.sweeps[0].fields["DBZH"]
    snr = v.sweeps[0].fields["SNRH"]
    z[row + 1:row + 3] = 22.
    snr[row + 1:row + 3] = 22.
    return v, row


def quiet_config(**changes):
    return config(receiver_enabled=False, clutter_enabled=False,
                  isolation_enabled=False, **changes)


def test_both_sided_default_rejects_one_sided_bleed():
    v, row = bled_volume()
    ev = evaluate(v, "both")
    assert not ev["XQC_RADIAL_POLAR_MASK"][row].any()


def test_either_mode_admits_one_quiet_side():
    v, row = bled_volume()
    ev = evaluate(v, "either")
    assert ev["XQC_RADIAL_POLAR_MASK"][row].sum() > 400


def evaluate(v, mode):
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
    out = evaluate_cut(v.sweeps[0], v.metadata, quiet_config(radial_flank_mode=mode))
    return out.arrays


def test_either_mode_still_requires_the_polar_conjunction():
    v, row = bled_volume()
    v.sweeps[0].fields["RHOHV"][row] = .99  # rain-like gate on the spoke ray
    ev = evaluate(v, "either")
    assert not ev["XQC_RADIAL_POLAR_MASK"][row].any()
