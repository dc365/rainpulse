from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from rainpulse_algo.training.generation import (  # noqa: E402
    NowcastNetGenerator,
    TemporalDiscriminator,
    generative_loss,
    generative_train_step,
    temporal_discriminator_loss,
)


def test_generator_and_temporal_discriminator_match_published_shapes() -> None:
    random = torch.Generator().manual_seed(17)
    inputs = torch.rand((2, 9, 32, 32), generator=random) * 16.0
    evolution = torch.rand((2, 20, 32, 32), generator=random) * 16.0
    noise = torch.randn((2, 2, 1, 1), generator=random)
    generator = NowcastNetGenerator(base_channels=2)
    discriminator = TemporalDiscriminator()

    generated = generator(inputs, evolution, noise=noise)
    logits = discriminator(inputs, generated)

    assert generated.shape == (2, 20, 32, 32)
    assert logits.shape == (2, 1, 2, 2)
    assert torch.isfinite(generated).all()
    assert torch.isfinite(logits).all()


def test_published_generative_objectives_support_backward() -> None:
    random = torch.Generator().manual_seed(23)
    inputs = torch.rand((2, 9, 32, 32), generator=random) * 16.0
    targets = torch.rand((2, 20, 32, 32), generator=random) * 16.0
    evolution = torch.rand((2, 20, 32, 32), generator=random) * 16.0
    generator = NowcastNetGenerator(base_channels=2)
    discriminator = TemporalDiscriminator()
    ensemble = torch.stack(
        [
            generator(
                inputs,
                evolution,
                noise=torch.randn((2, 2, 1, 1), generator=random),
            )
            for _ in range(2)
        ],
        dim=1,
    )

    discriminator_result = temporal_discriminator_loss(
        discriminator,
        inputs,
        targets,
        ensemble,
    )
    discriminator_result.total.backward()
    discriminator.zero_grad(set_to_none=True)
    generator.zero_grad(set_to_none=True)

    discriminator.requires_grad_(False)
    generator_result = generative_loss(
        discriminator,
        inputs,
        targets,
        ensemble,
        adversarial_weight=6.0,
        pool_weight=20.0,
    )
    generator_result.total.backward()
    discriminator.requires_grad_(True)

    assert torch.isfinite(discriminator_result.total)
    assert torch.isfinite(generator_result.total)
    assert torch.isfinite(generator_result.adversarial)
    assert torch.isfinite(generator_result.pool_regularization)
    assert any(parameter.grad is not None for parameter in generator.parameters())


def test_alternating_generative_train_step_updates_both_players() -> None:
    random = torch.Generator().manual_seed(29)
    inputs = torch.rand((2, 9, 32, 32), generator=random) * 16.0
    targets = torch.rand((2, 20, 32, 32), generator=random) * 16.0
    evolution = torch.rand((2, 20, 32, 32), generator=random) * 16.0
    generator = NowcastNetGenerator(base_channels=2)
    discriminator = TemporalDiscriminator()
    generator_optimizer = torch.optim.Adam(generator.parameters(), lr=3e-5)
    discriminator_optimizer = torch.optim.Adam(discriminator.parameters(), lr=3e-5)

    result = generative_train_step(
        generator=generator,
        discriminator=discriminator,
        generator_optimizer=generator_optimizer,
        discriminator_optimizer=discriminator_optimizer,
        inputs=inputs,
        targets=targets,
        evolution_prediction=evolution,
        ensemble_members=2,
    )

    assert result.ensemble.shape == (2, 2, 20, 32, 32)
    assert torch.isfinite(result.discriminator.total)
    assert torch.isfinite(result.generator.total)
    assert torch.isfinite(result.discriminator_gradient_norm)
    assert torch.isfinite(result.generator_gradient_norm)
