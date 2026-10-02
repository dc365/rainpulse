"""Published residual attribution must distinguish ownership from acceptance."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "source_extent_audit",
    Path(__file__).resolve().parents[1] / "scripts/audit_s_complete_source.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize("case", ["angular", "range", "qualified", "absent", "blocked"])
def test_exact_extent_attribution(case):
    shape = (5, 10)
    observed = np.ones(shape, bool)
    native = SimpleNamespace(
        shape=shape, ranges=np.arange(10) * 1000.,
        geometry_good=np.ones(5, bool), field_available={"DBZH": observed},
    )
    parent = np.ones(shape, "uint32")
    seed = np.zeros(shape, "uint32")
    seed[1:3, 2:8] = 7
    # A different source's far gates cannot enlarge source 7's range.
    parent[1:3, 9] = 2
    seed[1:3, 9] = 8
    group = {"RV2_RAW_FAN_ID": parent, "RV2_SOURCE_LEDGER_SEED_ID": seed}
    candidate = np.ones(shape, "uint8")
    code = np.full(shape, 4, "uint8")
    blocked = np.zeros(shape, bool)
    i, j = (4, 4) if case == "angular" else (1, 0)
    expected = "outside_original_extent:" + case
    if case == "qualified":
        code[i, j] = 0
        expected = "qualified"
    elif case in ("absent", "blocked"):
        candidate[i, j] = 0
        code[i, j] = 0  # Off-candidate zero must not masquerade as acceptance.
        if case == "blocked":
            blocked[i, j] = True
        else:
            parent[i, j] = 0
        expected = "outside_candidate:" + ("blocked" if case == "blocked" else "no_raw_parent")
    targets = np.zeros(shape, bool)
    targets[i, j] = True
    result = {
        m.module.PREFIX + "CANDIDATE_MASK": candidate,
        m.module.PREFIX + "REJECTION_CODE": code,
    }
    before = seed.copy()
    assert m.extent_decisions(native, group, blocked, result, targets) == {expected: 1}
    assert np.array_equal(seed, before)
