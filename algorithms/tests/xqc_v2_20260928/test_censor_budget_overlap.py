"""An independently qualified detection floor is not a new heuristic action."""

from types import SimpleNamespace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2 import polar_morphology, radial_source
from rainpulse_algo.multiband.xqc_v2.core import Reason, evaluate_cut

from .helpers import config, fixture


@pytest.mark.parametrize("module", ["baseline", "morphology"])
@pytest.mark.parametrize("novel_rows,abstained", [(1, False), (4, True)])
def test_floor_overlap_is_not_charged_twice_and_real_excess_still_refuses(
    monkeypatch, module, novel_rows, abstained
):
    volume, _ = fixture("empty", rays=120, gates=180)
    cut = volume.sweeps[0]
    cut.fields["DBZH"][:] = 18.0
    cut.fields["SNRH"][:] = 25.0
    cut.fields["RHOHV"][:] = 0.6
    cut.fields["ZDR"][:] = 5.0
    floor = np.zeros(cut.fields["DBZH"].shape, bool)
    floor[20:26, 50:150] = True
    cut.fields["SNRH"][floor] = 1.0
    novel = np.zeros(floor.shape, bool)
    novel[30:30+novel_rows, 50:100] = True
    nomination = floor | novel
    original = {k:v.copy() for k,v in cut.fields.items()}
    if module == "baseline":
        monkeypatch.setattr(
            radial_source, "detect",
            lambda *a, **kw: (nomination.copy(), {"status":"EVALUATED"}),
        )
    else:
        monkeypatch.setattr(
            polar_morphology, "detect",
            lambda *a, **kw: SimpleNamespace(
                mask=nomination.copy(), counterexample_mask=np.zeros(floor.shape, bool),
                object_id=np.zeros(floor.shape, "uint32"), record={"status":"EVALUATED"},
            ),
        )
    cfg = config(
        receiver_enabled=False, radial_objects_enabled=False, clutter_enabled=False,
        isolation_enabled=False, radial_source_enabled=module == "baseline",
        morphology={} if module == "morphology" else None,
        noise_censor_snr_db=3.0, maximum_new_exclusion_fraction=0.005,
    )
    out = evaluate_cut(cut, volume.metadata, cfg)
    assert out.arrays["XQC_NOISE_FLOOR_MASK"][floor].all()
    marked = (out.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET)) != 0
    assert novel.any() and floor.any()
    if abstained:
        assert out.record["status"] == "ACTION_BUDGET_ABSTAINED"
        assert marked[novel].all()
        assert not out.arrays["XQC_PROPOSED_MASK"][novel].any()
    else:
        assert out.record["status"] == "EVALUATED"
        assert not marked.any()
        assert out.arrays["XQC_PROPOSED_MASK"][novel].all()
    assert out.arrays["XQC_PROPOSED_MASK"][floor].all()
    assert out.arrays["XQC_QUARANTINE_MASK"][floor].all()
    for name, value in original.items():
        np.testing.assert_array_equal(cut.fields[name], value)
