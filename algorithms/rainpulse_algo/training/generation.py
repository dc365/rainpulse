"""NowcastNet generative network, temporal discriminator, and GAN objectives.

The generator is derived from the MIT-licensed NowcastNet inference capsule by
Yuchen Zhang (2022). The temporal discriminator and objectives are implemented
from Zhang et al., Nature 619 (2023), Methods and Extended Data Fig. 1. The
official capsule does not contain its training source, so the discriminator is
an explicit RainPulse reconstruction of the published architecture.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor, nn
from torch.nn import functional as F
from torch.nn.utils import spectral_norm

from .evolution import DoubleConv


class GenerativeEncoder(nn.Module):
    """Encode the nine inputs and twenty normalized evolution fields."""

    def __init__(self, input_channels: int = 29, base_channels: int = 32) -> None:
        super().__init__()
        if input_channels < 1 or base_channels < 1:
            raise ValueError("generative encoder dimensions must be positive")
        self.input_channels = input_channels
        self.input = DoubleConv(input_channels, base_channels)
        self.down1 = nn.Sequential(nn.MaxPool2d(2), DoubleConv(base_channels, base_channels * 2))
        self.down2 = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(base_channels * 2, base_channels * 4),
        )
        self.down3 = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(base_channels * 4, base_channels * 8),
        )

    def forward(self, values: Tensor) -> Tensor:
        if values.ndim != 4 or values.shape[1] != self.input_channels:
            raise ValueError("generative encoder input shape differs")
        if values.shape[-2] % 8 or values.shape[-1] % 8:
            raise ValueError("generative encoder spatial dimensions must be divisible by 8")
        values = self.input(values)
        values = self.down1(values)
        values = self.down2(values)
        return self.down3(values)


class ProjectionBlock(nn.Module):
    """Spectrally normalized residual block used by the noise projector."""

    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        if out_channels <= in_channels:
            raise ValueError("projection block must increase channel count")
        self.shortcut = spectral_norm(
            nn.Conv2d(in_channels, out_channels - in_channels, kernel_size=1)
        )
        self.residual = nn.Sequential(
            spectral_norm(nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1)),
            nn.ReLU(),
            spectral_norm(nn.Conv2d(out_channels, out_channels, kernel_size=3, padding=1)),
        )

    def forward(self, values: Tensor) -> Tensor:
        shortcut = torch.cat((values, self.shortcut(values)), dim=1)
        return shortcut + self.residual(values)


class NoiseProjector(nn.Module):
    """Project Gaussian noise at H/32 to contextual features at H/8."""

    channel_to_space_factor = 4

    def __init__(self, latent_channels: int = 32) -> None:
        super().__init__()
        if latent_channels < 1:
            raise ValueError("latent channels must be positive")
        self.latent_channels = latent_channels
        self.first = spectral_norm(
            nn.Conv2d(latent_channels, latent_channels * 2, kernel_size=3, padding=1)
        )
        self.level1 = ProjectionBlock(latent_channels * 2, latent_channels * 4)
        self.level2 = ProjectionBlock(latent_channels * 4, latent_channels * 8)
        self.level3 = ProjectionBlock(latent_channels * 8, latent_channels * 16)
        self.level4 = ProjectionBlock(latent_channels * 16, latent_channels * 32)

    def forward(self, noise: Tensor) -> Tensor:
        if noise.ndim != 4 or noise.shape[1] != self.latent_channels:
            raise ValueError("noise projector input shape differs")
        values = self.first(noise)
        values = self.level1(values)
        values = self.level2(values)
        values = self.level3(values)
        values = self.level4(values)
        batch, channels, height, width = values.shape
        factor = self.channel_to_space_factor
        if channels % (factor * factor):
            raise ValueError("noise feature channels cannot be moved into space")
        # Preserve the channel-to-space ordering in the official inference capsule.
        return (
            values.reshape(batch, channels // (factor * factor), factor, factor, height, width)
            .permute(0, 1, 4, 5, 2, 3)
            .reshape(batch, channels // (factor * factor), height * factor, width * factor)
        )


class SpatiallyAdaptiveNorm(nn.Module):
    """SPADE-style conditioning on normalized evolution predictions."""

    def __init__(self, feature_channels: int, evolution_channels: int) -> None:
        super().__init__()
        self.evolution_channels = evolution_channels
        self.instance_norm = nn.InstanceNorm2d(feature_channels, affine=False)
        self.shared = nn.Sequential(
            nn.ReflectionPad2d(1),
            nn.Conv2d(evolution_channels, 64, kernel_size=3),
            nn.ReLU(),
        )
        self.pad = nn.ReflectionPad2d(1)
        self.scale = nn.Conv2d(64, feature_channels, kernel_size=3)
        self.bias = nn.Conv2d(64, feature_channels, kernel_size=3)

    def forward(self, values: Tensor, evolution: Tensor) -> Tensor:
        if evolution.ndim != 4 or evolution.shape[1] != self.evolution_channels:
            raise ValueError("SPADE evolution shape differs")
        normalized = self.instance_norm(values)
        resized = F.adaptive_avg_pool2d(evolution, output_size=values.shape[-2:])
        shared = self.shared(resized)
        scale = self.scale(self.pad(shared))
        bias = self.bias(self.pad(shared))
        return normalized * (1.0 + scale) + bias


class GenerativeBlock(nn.Module):
    """Physics-conditioned residual decoder block from the official capsule."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        *,
        evolution_channels: int,
        double_conv: bool = False,
    ) -> None:
        super().__init__()
        middle = min(in_channels, out_channels)
        self.learned_shortcut = in_channels != out_channels
        self.double_conv = double_conv
        self.pad = nn.ReflectionPad2d(1)
        self.norm0 = SpatiallyAdaptiveNorm(in_channels, evolution_channels)
        self.conv0 = spectral_norm(nn.Conv2d(in_channels, middle, kernel_size=3))
        self.norm1 = SpatiallyAdaptiveNorm(middle, evolution_channels)
        self.conv1 = spectral_norm(nn.Conv2d(middle, out_channels, kernel_size=3))
        if self.learned_shortcut:
            self.shortcut_norm = SpatiallyAdaptiveNorm(in_channels, evolution_channels)
            self.shortcut_conv = spectral_norm(
                nn.Conv2d(in_channels, out_channels, kernel_size=1, bias=False)
            )

    def forward(self, values: Tensor, evolution: Tensor) -> Tensor:
        shortcut = values
        if self.learned_shortcut:
            shortcut = self.shortcut_conv(self.shortcut_norm(values, evolution))
        residual = self.conv0(self.pad(F.leaky_relu(self.norm0(values, evolution), 0.2)))
        if self.double_conv:
            residual = self.conv1(
                self.pad(F.leaky_relu(self.norm1(residual, evolution), 0.2))
            )
        return shortcut + residual


class GenerativeDecoder(nn.Module):
    def __init__(
        self,
        *,
        input_channels: int,
        output_channels: int = 20,
        base_channels: int = 32,
        evolution_channels: int = 20,
    ) -> None:
        super().__init__()
        self.input_channels = input_channels
        self.output_channels = output_channels
        self.input = nn.Conv2d(input_channels, base_channels * 8, kernel_size=3, padding=1)
        self.head = GenerativeBlock(
            base_channels * 8,
            base_channels * 8,
            evolution_channels=evolution_channels,
        )
        self.middle0 = GenerativeBlock(
            base_channels * 8,
            base_channels * 4,
            evolution_channels=evolution_channels,
            double_conv=True,
        )
        self.middle1 = GenerativeBlock(
            base_channels * 4,
            base_channels * 4,
            evolution_channels=evolution_channels,
            double_conv=True,
        )
        self.up0 = GenerativeBlock(
            base_channels * 4,
            base_channels * 2,
            evolution_channels=evolution_channels,
        )
        self.up1 = GenerativeBlock(
            base_channels * 2,
            base_channels,
            evolution_channels=evolution_channels,
            double_conv=True,
        )
        self.up2 = GenerativeBlock(
            base_channels,
            base_channels,
            evolution_channels=evolution_channels,
            double_conv=True,
        )
        self.output = nn.Conv2d(base_channels, output_channels, kernel_size=3, padding=1)

    def forward(self, features: Tensor, evolution: Tensor) -> Tensor:
        if features.ndim != 4 or features.shape[1] != self.input_channels:
            raise ValueError("generative decoder feature shape differs")
        values = self.input(features)
        values = self.head(values, evolution)
        values = F.interpolate(values, scale_factor=2, mode="nearest")
        values = self.middle0(values, evolution)
        values = self.middle1(values, evolution)
        values = F.interpolate(values, scale_factor=2, mode="nearest")
        values = self.up0(values, evolution)
        values = F.interpolate(values, scale_factor=2, mode="nearest")
        values = self.up1(values, evolution)
        values = self.up2(values, evolution)
        return self.output(F.leaky_relu(values, 0.2))


class NowcastNetGenerator(nn.Module):
    """Generate one stochastic 20-frame member from a detached evolution field."""

    def __init__(
        self,
        *,
        input_frames: int = 9,
        target_frames: int = 20,
        base_channels: int = 32,
        rain_rate_cap_mm_h: float = 128.0,
    ) -> None:
        super().__init__()
        if input_frames < 1 or target_frames < 1 or rain_rate_cap_mm_h <= 0.0:
            raise ValueError("generator dimensions or rain-rate cap are invalid")
        self.input_frames = input_frames
        self.target_frames = target_frames
        self.base_channels = base_channels
        self.rain_rate_cap_mm_h = rain_rate_cap_mm_h
        self.encoder = GenerativeEncoder(input_frames + target_frames, base_channels)
        self.projector = NoiseProjector(base_channels)
        self.decoder = GenerativeDecoder(
            input_channels=base_channels * 10,
            output_channels=target_frames,
            base_channels=base_channels,
            evolution_channels=target_frames,
        )

    def sample_noise(self, inputs: Tensor, *, generator: torch.Generator | None = None) -> Tensor:
        height, width = inputs.shape[-2:]
        if height % 32 or width % 32:
            raise ValueError("generator spatial dimensions must be divisible by 32")
        return torch.randn(
            inputs.shape[0],
            self.base_channels,
            height // 32,
            width // 32,
            device=inputs.device,
            dtype=inputs.dtype,
            generator=generator,
        )

    def forward(
        self,
        inputs: Tensor,
        evolution_prediction: Tensor,
        *,
        noise: Tensor | None = None,
    ) -> Tensor:
        if inputs.ndim != 4 or inputs.shape[1] != self.input_frames:
            raise ValueError("generator input shape differs")
        expected = (inputs.shape[0], self.target_frames, *inputs.shape[-2:])
        if tuple(evolution_prediction.shape) != expected:
            raise ValueError("generator evolution prediction shape differs")
        if inputs.shape[-2] % 32 or inputs.shape[-1] % 32:
            raise ValueError("generator spatial dimensions must be divisible by 32")
        if noise is None:
            noise = self.sample_noise(inputs)
        expected_noise = (
            inputs.shape[0],
            self.base_channels,
            inputs.shape[-2] // 32,
            inputs.shape[-1] // 32,
        )
        if tuple(noise.shape) != expected_noise:
            raise ValueError("generator noise shape differs")
        normalized_evolution = evolution_prediction / self.rain_rate_cap_mm_h
        encoded = self.encoder(torch.cat((inputs, normalized_evolution), dim=1))
        projected = self.projector(noise)
        if projected.shape[-2:] != encoded.shape[-2:]:
            raise ValueError("projected noise and encoded context shapes differ")
        return self.decoder(torch.cat((encoded, projected), dim=1), normalized_evolution)


class _TemporalResidualBlock(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, *, downsample: bool) -> None:
        super().__init__()
        self.downsample = downsample
        self.residual = DoubleConv(in_channels, out_channels)

    def forward(self, values: Tensor) -> Tensor:
        if self.downsample:
            values = F.interpolate(
                values,
                scale_factor=0.5,
                mode="bilinear",
                align_corners=True,
            )
        return self.residual(values)


class TemporalDiscriminator(nn.Module):
    """Patch discriminator reconstructed from Extended Data Fig. 1c."""

    def __init__(self, *, context_frames: int = 4, target_frames: int = 20) -> None:
        super().__init__()
        if context_frames != 4 or target_frames != 20:
            raise ValueError("published temporal discriminator requires 4+20 frames")
        self.context_frames = context_frames
        self.target_frames = target_frames
        total_frames = context_frames + target_frames
        self.spatial = spectral_norm(
            nn.Conv2d(total_frames, 64, kernel_size=9, stride=2, padding=4)
        )
        self.short_temporal = spectral_norm(
            nn.Conv3d(1, 4, kernel_size=(4, 9, 9), stride=(1, 2, 2), padding=(0, 4, 4))
        )
        self.long_temporal = spectral_norm(
            nn.Conv3d(
                1,
                8,
                kernel_size=(target_frames, 9, 9),
                stride=(1, 2, 2),
                padding=(0, 4, 4),
            )
        )
        # 64 + 4 * (24 - 4 + 1) + 8 * (24 - 20 + 1) = 188.
        self.level1 = _TemporalResidualBlock(188, 128, downsample=True)
        self.level2 = _TemporalResidualBlock(128, 256, downsample=True)
        self.level3 = _TemporalResidualBlock(256, 512, downsample=True)
        self.level4 = _TemporalResidualBlock(512, 512, downsample=False)
        self.output = nn.Sequential(
            nn.BatchNorm2d(512),
            nn.LeakyReLU(0.2),
            spectral_norm(nn.Conv2d(512, 1, kernel_size=3, padding=1)),
        )

    def forward(self, inputs: Tensor, sequence: Tensor) -> Tensor:
        if inputs.ndim != 4 or sequence.ndim != 4:
            raise ValueError("discriminator inputs must be BCHW sequences")
        if (
            inputs.shape[0] != sequence.shape[0]
            or inputs.shape[-2:] != sequence.shape[-2:]
            or inputs.shape[1] < self.context_frames
            or sequence.shape[1] != self.target_frames
            or inputs.shape[-2] % 16
            or inputs.shape[-1] % 16
        ):
            raise ValueError("discriminator input shapes differ")
        frames = torch.cat((inputs[:, -self.context_frames :], sequence), dim=1)
        spatial = self.spatial(frames)
        temporal_input = frames.unsqueeze(1)
        short = self.short_temporal(temporal_input)
        short = short.flatten(1, 2)
        long = self.long_temporal(temporal_input)
        long = long.flatten(1, 2)
        values = torch.cat((spatial, short, long), dim=1)
        values = self.level1(values)
        values = self.level2(values)
        values = self.level3(values)
        values = self.level4(values)
        return self.output(values)


@dataclass(frozen=True)
class DiscriminatorLoss:
    total: Tensor
    real: Tensor
    fake: Tensor


@dataclass(frozen=True)
class GenerativeLoss:
    total: Tensor
    adversarial: Tensor
    pool_regularization: Tensor


def temporal_discriminator_loss(
    discriminator: TemporalDiscriminator,
    inputs: Tensor,
    targets: Tensor,
    generated: Tensor,
) -> DiscriminatorLoss:
    if generated.ndim == 4:
        generated = generated.unsqueeze(1)
    if generated.ndim != 5 or generated.shape[0] != targets.shape[0]:
        raise ValueError("generated discriminator members must be [B,K,T,H,W]")
    real_logits = discriminator(inputs, targets)
    real = F.binary_cross_entropy_with_logits(real_logits, torch.ones_like(real_logits))
    fake_losses = []
    for member in generated.unbind(dim=1):
        fake_logits = discriminator(inputs, member.detach())
        fake_losses.append(
            F.binary_cross_entropy_with_logits(fake_logits, torch.zeros_like(fake_logits))
        )
    fake = torch.stack(fake_losses).mean()
    return DiscriminatorLoss(total=real + fake, real=real, fake=fake)


def generative_loss(
    discriminator: TemporalDiscriminator,
    inputs: Tensor,
    targets: Tensor,
    ensemble: Tensor,
    *,
    adversarial_weight: float = 6.0,
    pool_weight: float = 20.0,
    pool_kernel_size: int = 5,
    pool_stride: int = 2,
    weight_cap: float = 24.0,
) -> GenerativeLoss:
    if (
        ensemble.ndim != 5
        or ensemble.shape[0] != targets.shape[0]
        or ensemble.shape[2:] != targets.shape[1:]
        or adversarial_weight < 0.0
        or pool_weight < 0.0
        or pool_kernel_size < 1
        or pool_stride < 1
        or weight_cap <= 0.0
    ):
        raise ValueError("generative loss inputs or weights are invalid")
    adversarial_losses = []
    pooled_members = []
    for member in ensemble.unbind(dim=1):
        logits = discriminator(inputs, member)
        adversarial_losses.append(
            F.binary_cross_entropy_with_logits(logits, torch.ones_like(logits))
        )
        pooled_members.append(
            F.max_pool2d(member, kernel_size=pool_kernel_size, stride=pool_stride)
        )
    adversarial = torch.stack(adversarial_losses).mean()
    pooled_target = F.max_pool2d(
        targets,
        kernel_size=pool_kernel_size,
        stride=pool_stride,
    )
    pooled_mean = torch.stack(pooled_members, dim=1).mean(dim=1)
    weights = torch.clamp(1.0 + pooled_target, max=weight_cap)
    pool_regularization = torch.mean(weights * torch.abs(pooled_target - pooled_mean))
    total = adversarial_weight * adversarial + pool_weight * pool_regularization
    return GenerativeLoss(
        total=total,
        adversarial=adversarial,
        pool_regularization=pool_regularization,
    )


@dataclass(frozen=True)
class GenerativeStepResult:
    discriminator: DiscriminatorLoss
    generator: GenerativeLoss
    ensemble: Tensor
    discriminator_gradient_norm: Tensor
    generator_gradient_norm: Tensor


def generative_train_step(
    *,
    generator: NowcastNetGenerator,
    discriminator: TemporalDiscriminator,
    generator_optimizer: torch.optim.Optimizer,
    discriminator_optimizer: torch.optim.Optimizer,
    inputs: Tensor,
    targets: Tensor,
    evolution_prediction: Tensor,
    ensemble_members: int = 4,
    adversarial_weight: float = 6.0,
    pool_weight: float = 20.0,
    gradient_clip_norm: float = 1.0,
) -> GenerativeStepResult:
    """Run one discriminator update followed by one generator update."""

    if ensemble_members < 1 or gradient_clip_norm <= 0.0:
        raise ValueError("ensemble size and gradient clip norm must be positive")
    ensemble = torch.stack(
        [generator(inputs, evolution_prediction) for _ in range(ensemble_members)],
        dim=1,
    )

    discriminator_optimizer.zero_grad(set_to_none=True)
    discriminator_result = temporal_discriminator_loss(
        discriminator,
        inputs,
        targets,
        ensemble,
    )
    if not bool(torch.isfinite(discriminator_result.total)):
        raise FloatingPointError("temporal discriminator loss is non-finite")
    discriminator_result.total.backward()
    discriminator_gradient_norm = torch.nn.utils.clip_grad_norm_(
        discriminator.parameters(),
        gradient_clip_norm,
    )
    if not bool(torch.isfinite(discriminator_gradient_norm)):
        raise FloatingPointError("temporal discriminator gradient is non-finite")
    discriminator_optimizer.step()

    generator_optimizer.zero_grad(set_to_none=True)
    discriminator.requires_grad_(False)
    try:
        generator_result = generative_loss(
            discriminator,
            inputs,
            targets,
            ensemble,
            adversarial_weight=adversarial_weight,
            pool_weight=pool_weight,
        )
        if not bool(torch.isfinite(generator_result.total)):
            raise FloatingPointError("generative loss is non-finite")
        generator_result.total.backward()
        generator_gradient_norm = torch.nn.utils.clip_grad_norm_(
            generator.parameters(),
            gradient_clip_norm,
        )
        if not bool(torch.isfinite(generator_gradient_norm)):
            raise FloatingPointError("generator gradient is non-finite")
        generator_optimizer.step()
    finally:
        discriminator.requires_grad_(True)

    return GenerativeStepResult(
        discriminator=discriminator_result,
        generator=generator_result,
        ensemble=ensemble.detach(),
        discriminator_gradient_norm=discriminator_gradient_norm.detach(),
        generator_gradient_norm=generator_gradient_norm.detach(),
    )
