"""Read-only replay must be bounded before loading objects or array headers."""

import importlib.util
import io
from pathlib import Path

import numpy as np
import pytest


def audit():
    path = Path(__file__).resolve().parents[3] / "scripts/audit_x_polar_morphology.py"
    spec = importlib.util.spec_from_file_location("morphology_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stdin_task_limit_precedes_json_parsing():
    module = audit()
    assert module.read_task(io.BytesIO(b'{"id":"test"}')) == {"id": "test"}
    with pytest.raises(ValueError, match="task JSON"):
        module.read_task(io.BytesIO(b" " * (1024**2 + 1)))


def test_native_array_shapes_checked_for_every_field():
    module = audit()
    fields = {
        "DBZH_RAW": np.zeros((4, 5)),
        "azimuth": np.zeros(4),
        "range_m": np.zeros(5),
        "elevation": np.zeros(4),
        "ray_time_epoch": np.zeros(4),
    }
    module.validate_native_arrays(fields, (4, 5))
    with pytest.raises(ValueError, match="shape"):
        module.validate_native_arrays({**fields, "XQC_PROPOSED_MASK": np.zeros((4, 6))}, (4, 5))
    with pytest.raises(ValueError, match="shape"):
        module.validate_native_arrays({**fields, "elevation": np.zeros((4, 5))}, (4, 5))


def test_declared_size_rejected_before_object_download():
    from types import SimpleNamespace

    class Objects:
        session = SimpleNamespace(
            index=SimpleNamespace(logical={"native.npz": ("pack", 0, 65 * 1024**2)})
        )

        def __getitem__(self, key):
            pytest.fail("oversize object downloaded before preflight")

    with pytest.raises(ValueError, match="byte budget"):
        audit().load_bounded(Objects(), ["native.npz"], 64 * 1024**2)


def test_staging_limits_pack_count_even_when_byte_count_is_zero():
    from types import SimpleNamespace

    session = SimpleNamespace(
        index=SimpleNamespace(schema="3.0", size=0, physical=dict.fromkeys(range(257)))
    )
    with pytest.raises(ValueError, match="pack count"):
        audit().check_staging_budget(session, 512 * 1024**2)
