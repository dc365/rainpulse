from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml
from jsonschema import Draft202012Validator

from rainpulse_algo.nowcast.nowcastnet_adapter import (
    NowcastNetInputError,
    prepare_nowcastnet_input,
    run_nowcastnet_batch_fields,
    run_nowcastnet_fields,
    validate_nowcastnet_backend_output,
)
from rainpulse_algo.nowcast.nowcastnet_official_backend import (
    OfficialNowcastNetBackendError,
    member_seeds,
    verify_file_sha256,
)
from rainpulse_algo.nowcast.nowcastnet_profile import (
    load_nowcastnet_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "nowcast" / "rp026-nowcastnet-offline-v1.yaml"
SCHEMA_PATH = REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-profile.schema.json"


@pytest.fixture(scope="module")
def profile():
    return load_nowcastnet_profile(PROFILE_PATH)


def _input(profile, fill: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    shape = (
        profile.protocol.input_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    return np.full(shape, fill, dtype="float32"), np.ones(shape, dtype="uint8")


def test_profile_schema_and_offline_only_boundary(profile) -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    raw = yaml.safe_load(PROFILE_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(raw)

    assert profile.offline_readiness_blockers() == ()
    profile.require_offline_ready()
    assert profile.weights_path() == Path(
        "/opt/rainpulse/nowcastnet/official-v1/data/checkpoints/mrms_model.ckpt"
    )
    assert profile.activation.realtime_shadow_enabled is False
    assert profile.activation.product_publication_enabled is False
    assert profile.activation.operational_eligible is False


def test_preparation_preserves_no_rain_and_records_clipping(profile) -> None:
    rate, valid = _input(profile, fill=0.0)
    rate[0, 0, 0] = 200.0

    prepared = prepare_nowcastnet_input(rate, valid, profile=profile)

    assert prepared.clipped_pixel_count == 1
    assert prepared.model_frames.shape == (9, 512, 512, 2)
    assert prepared.model_frames[0, 0, 0, 0] == 128.0
    assert prepared.model_frames[0, 0, 1, 0] == 0.0
    assert np.all(prepared.model_frames[..., 1] == 1.0)


def test_preparation_rejects_missing_instead_of_converting_it_to_no_rain(profile) -> None:
    rate, valid = _input(profile)
    valid[0, 0, 0] = 0
    rate[0, 0, 0] = np.nan

    with pytest.raises(NowcastNetInputError, match="rejects any missing"):
        prepare_nowcastnet_input(rate, valid, profile=profile)


def test_backend_output_contract_rejects_bad_shape_and_non_finite_values(profile) -> None:
    with pytest.raises(NowcastNetInputError, match="output must be"):
        validate_nowcastnet_backend_output(np.zeros((1, 1, 1), dtype="float32"), profile=profile)
    expected = (
        profile.protocol.ensemble_members,
        profile.protocol.output_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    invalid = np.zeros(expected, dtype="float32")
    invalid[0, 0, 0, 0] = np.nan
    with pytest.raises(NowcastNetInputError, match="non-finite"):
        validate_nowcastnet_backend_output(invalid, profile=profile)


def test_ready_adapter_runs_an_injected_backend_without_becoming_operational(profile) -> None:
    configured = profile
    rate, valid = _input(configured)

    def backend(values: np.ndarray, members: int, seed: int) -> np.ndarray:
        assert values.shape == (*rate.shape, 2)
        assert np.array_equal(values[..., 0], rate)
        assert np.all(values[..., 1] == 1.0)
        assert members == 4
        assert seed == 20260830
        output = np.broadcast_to(
            values[-1, ..., 0],
            (members, configured.protocol.output_frames, *values.shape[1:3]),
        ).copy()
        output[0, 0, 0, 0] = -0.5
        return output

    result = run_nowcastnet_fields(
        rate,
        valid,
        profile=configured,
        backend=backend,
        random_seed=20260830,
    )

    assert result.rain_rate_mm_h.shape == (4, 20, 512, 512)
    assert np.all(result.valid_mask == 1)
    assert result.rain_rate_mm_h[0, 0, 0, 0] == 0.0
    assert result.clipped_negative_output_pixel_count == 1
    assert result.operational_eligible is False
    assert result.random_seed == 20260830


def test_batch_adapter_preserves_tile_axis_and_clipping(profile) -> None:
    configured = replace(
        profile,
        protocol=replace(profile.protocol, input_height=32, input_width=32),
    )
    rate = np.ones((2, 9, 32, 32), dtype="float32")
    rate[1, 0, 0, 0] = 200.0
    valid = np.ones_like(rate, dtype="uint8")

    class Backend:
        def infer_batch(self, fields: np.ndarray, members: int, seed: int) -> np.ndarray:
            assert fields.shape == (2, 9, 32, 32, 2)
            assert members == 4
            assert seed == 42
            return np.broadcast_to(
                fields[np.newaxis, :, np.newaxis, -1, ..., 0],
                (members, 2, configured.protocol.output_frames, 32, 32),
            ).copy()

    result = run_nowcastnet_batch_fields(
        rate,
        valid,
        profile=configured,
        backend=Backend(),
        random_seed=42,
    )

    assert result.rain_rate_mm_h.shape == (4, 2, 20, 32, 32)
    assert np.all(result.valid_mask == 1)
    assert result.clipped_input_pixel_count == 1
    assert result.clipped_negative_output_pixel_count == 0


def test_official_artifact_hash_and_member_seed_schedule(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"rainpulse")
    verify_file_sha256(
        artifact,
        "ac0fcda5c72696277a93db0800cf40202b7b88612a07e4c0423e8161bbc9e54d",
    )
    with pytest.raises(OfficialNowcastNetBackendError, match="SHA-256 mismatch"):
        verify_file_sha256(artifact, "0" * 64)

    assert member_seeds(2**32 - 2, 4) == (2**32 - 2, 2**32 - 1, 0, 1)
