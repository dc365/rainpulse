import importlib.util
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.selection_kernel import select_winners

ROOT = Path(__file__).resolve().parents[3]


def test_generated_policy_roundtrip(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "perf_configure", ROOT / "scripts/configure_performance_batch1.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = tmp_path / "policy.json"
    cfg = mod.generate(p, scratch_parent=str(tmp_path))
    assert (
        cfg.streaming
        and cfg.selection_backend == "numpy"
        and cfg.digest == ExecutionOptions.load(p).digest
    )
    with pytest.raises(FileExistsError):
        mod.generate(p)
    with pytest.raises(ValueError):
        mod.generate(tmp_path / "bad.json", scratch_parent="relative")


def test_execution_schema_matches_python_defaults():
    import jsonschema

    schema = json.loads(
        (ROOT / "contracts/internal/multiband/execution-cpu-v1.schema.json").read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(asdict(ExecutionOptions()), schema)
    jsonschema.validate(asdict(ExecutionOptions(streaming=True, selection_backend="numba")), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"selection_backend": "numba"}, schema)


@pytest.mark.parametrize("strided", [False, True])
def test_kernel_ties_nan_inf_and_age_rounding(strided):
    pytest.importorskip("numba")
    rng = np.random.default_rng(25)
    shape = (4, 8)
    candidate = np.tile([1.0, 1.0, 1.0 + 1e-12, 1.0 - 1e-12, np.nan, np.inf, -np.inf, 0.0], (4, 1))
    score = np.ones(shape)
    score[:, 5] = np.inf
    score[:, 6] = -np.inf
    age = np.full(shape, 4.5, "float32")
    new_age = np.array(age, dtype="float64")
    new_age[0] -= 0.00000001
    new_age[1] += 1
    sample = rng.random(shape).astype("float32")
    ne = np.zeros(shape, bool)
    ne[2] = True
    ray = np.arange(32, dtype="int64").reshape(shape)
    gate = ray.copy()
    height = sample.astype("float64")
    res = np.ones(shape) * 500
    res[2] = 499
    adm = np.ones(shape, bool)
    adm[3] = False

    def state():
        values = [
            score.copy(),
            sample.copy(),
            np.zeros(shape, "int32"),
            np.zeros(shape, "int32"),
            np.zeros(shape, "int32"),
            sample.copy(),
            age.copy(),
            np.ones(shape, "float32") * 500,
        ]
        if strided:
            values = [np.repeat(v, 2, axis=1)[:, ::2] for v in values]
        return values

    a, b = state(), state()
    with np.errstate(invalid="ignore"):
        select_winners(
            adm, candidate, ne, sample, ray, gate, height, new_age, res, 2, *a, backend="numpy"
        )
        select_winners(
            adm, candidate, ne, sample, ray, gate, height, new_age, res, 2, *b, backend="numba"
        )
    for x, y in zip(a, b):
        assert np.array_equal(x, y, equal_nan=True)


def test_explicit_unavailable_numba_is_error(monkeypatch):
    import builtins

    import rainpulse_algo.multiband.selection_kernel as k

    previous = k._COMPILED
    k._COMPILED = None
    old = builtins.__import__

    def fake(name, *a, **kw):
        if name == "numba":
            raise ImportError("not installed")
        return old(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            k.require_numba()
    finally:
        k._COMPILED = previous
