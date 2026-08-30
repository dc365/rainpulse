from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from rainpulse_algo.training.evolution import (  # noqa: E402
    EvolutionNetwork,
    evolution_loss,
)


def test_evolution_network_and_loss_support_backward() -> None:
    generator = torch.Generator().manual_seed(7)
    inputs = torch.rand((2, 9, 32, 32), generator=generator) * 16.0
    targets = torch.rand((2, 20, 32, 32), generator=generator) * 16.0
    model = EvolutionNetwork(input_frames=9, target_frames=20, base_channels=4)

    intensity, motion = model(inputs)
    result = evolution_loss(
        inputs,
        targets,
        intensity,
        motion,
        motion_regularization_lambda=0.01,
        weight_cap=24.0,
    )
    result.total.backward()

    assert intensity.shape == (2, 20, 32, 32)
    assert motion.shape == (2, 40, 32, 32)
    assert result.prediction.shape == targets.shape
    assert torch.isfinite(result.total)
    assert torch.isfinite(result.accumulation)
    assert torch.isfinite(result.motion_regularization)
    assert any(parameter.grad is not None for parameter in model.parameters())
