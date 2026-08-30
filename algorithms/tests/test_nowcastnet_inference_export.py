from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from rainpulse_algo.training.evolution import rollout_evolution  # noqa: E402
from rainpulse_algo.training.inference import NowcastNetInference  # noqa: E402


def test_inference_composition_matches_explicit_fixed_noise_path() -> None:
    random = torch.Generator().manual_seed(41)
    inputs = torch.rand((2, 9, 32, 32), generator=random) * 16.0
    noise = torch.randn((2, 3, 2, 1, 1), generator=random)
    model = NowcastNetInference(
        evolution_base_channels=2,
        generator_base_channels=2,
    ).eval()

    with torch.no_grad():
        intensity, motion = model.evolution(inputs)
        evolution = rollout_evolution(inputs, intensity, motion)
        expected = torch.stack(
            [
                model.generator(inputs, evolution, noise=member_noise)
                for member_noise in noise.unbind(dim=1)
            ],
            dim=1,
        )
        actual = model(inputs, noise)

    torch.testing.assert_close(actual, expected, rtol=0.0, atol=0.0)
    assert actual.shape == (2, 3, 20, 32, 32)
    clipped = model.clip_to_product_range(actual)
    assert torch.all(clipped >= 0.0)
    assert torch.all(clipped <= 128.0)
