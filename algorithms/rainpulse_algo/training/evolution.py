"""Trainable NowcastNet evolution network and physics-informed loss.

The two-path U-Net architecture is derived from the MIT-licensed NowcastNet
reference implementation by Yuchen Zhang (2022). The full upstream notice is
kept in ``LICENSE.nowcastnet.txt`` next to this module. The rollout and loss are
implemented here from the method published in Zhang et al., Nature 619 (2023).
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor, nn
from torch.nn import functional as F
from torch.nn.utils import spectral_norm


class DoubleConv(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        *,
        mid_channels: int | None = None,
    ) -> None:
        super().__init__()
        middle = mid_channels or out_channels
        self.double_conv = nn.Sequential(
            nn.BatchNorm2d(in_channels),
            nn.ReLU(inplace=True),
            spectral_norm(nn.Conv2d(in_channels, middle, kernel_size=3, padding=1)),
            nn.BatchNorm2d(middle),
            nn.ReLU(inplace=True),
            spectral_norm(nn.Conv2d(middle, out_channels, kernel_size=3, padding=1)),
        )
        self.shortcut = nn.Sequential(
            nn.BatchNorm2d(in_channels),
            spectral_norm(nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1)),
        )

    def forward(self, values: Tensor) -> Tensor:
        return self.double_conv(values) + self.shortcut(values)


class Down(nn.Module):
    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(in_channels, out_channels),
        )

    def forward(self, values: Tensor) -> Tensor:
        return self.layers(values)


class Up(nn.Module):
    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        self.conv = DoubleConv(
            in_channels,
            out_channels,
            mid_channels=in_channels // 2,
        )

    def forward(self, values: Tensor, skip: Tensor) -> Tensor:
        values = F.interpolate(values, scale_factor=2, mode="bilinear", align_corners=True)
        difference_y = skip.size(2) - values.size(2)
        difference_x = skip.size(3) - values.size(3)
        values = F.pad(
            values,
            [
                difference_x // 2,
                difference_x - difference_x // 2,
                difference_y // 2,
                difference_y - difference_y // 2,
            ],
        )
        return self.conv(torch.cat([skip, values], dim=1))


class _Decoder(nn.Module):
    def __init__(self, base_channels: int, output_channels: int) -> None:
        super().__init__()
        self.up1 = Up(base_channels * 16, base_channels * 4)
        self.up2 = Up(base_channels * 8, base_channels * 2)
        self.up3 = Up(base_channels * 4, base_channels)
        self.up4 = Up(base_channels * 2, base_channels)
        self.output = nn.Conv2d(base_channels, output_channels, kernel_size=1)

    def forward(
        self,
        encoded: Tensor,
        skip4: Tensor,
        skip3: Tensor,
        skip2: Tensor,
        skip1: Tensor,
    ) -> Tensor:
        values = self.up1(encoded, skip4)
        values = self.up2(values, skip3)
        values = self.up3(values, skip2)
        values = self.up4(values, skip1)
        return self.output(values)


class EvolutionNetwork(nn.Module):
    """Two-path U-Net predicting 20 intensity and 20 two-component motion fields."""

    def __init__(
        self,
        *,
        input_frames: int = 9,
        target_frames: int = 20,
        base_channels: int = 32,
    ) -> None:
        super().__init__()
        if input_frames < 1 or target_frames < 1 or base_channels < 1:
            raise ValueError("evolution network dimensions must be positive")
        self.input_frames = input_frames
        self.target_frames = target_frames
        self.encoder1 = DoubleConv(input_frames, base_channels)
        self.encoder2 = Down(base_channels, base_channels * 2)
        self.encoder3 = Down(base_channels * 2, base_channels * 4)
        self.encoder4 = Down(base_channels * 4, base_channels * 8)
        self.encoder5 = Down(base_channels * 8, base_channels * 8)
        self.intensity_decoder = _Decoder(base_channels, target_frames)
        self.motion_decoder = _Decoder(base_channels, target_frames * 2)
        self.intensity_scale = nn.Parameter(torch.zeros(1, target_frames, 1, 1))

    def forward(self, inputs: Tensor) -> tuple[Tensor, Tensor]:
        if inputs.ndim != 4 or inputs.shape[1] != self.input_frames:
            raise ValueError("evolution input must have shape [batch,input_frames,height,width]")
        if inputs.shape[-2] % 16 or inputs.shape[-1] % 16:
            raise ValueError("evolution input spatial dimensions must be divisible by 16")
        skip1 = self.encoder1(inputs)
        skip2 = self.encoder2(skip1)
        skip3 = self.encoder3(skip2)
        skip4 = self.encoder4(skip3)
        encoded = self.encoder5(skip4)
        intensity = self.intensity_decoder(encoded, skip4, skip3, skip2, skip1)
        intensity = intensity * self.intensity_scale
        motion = self.motion_decoder(encoded, skip4, skip3, skip2, skip1)
        return intensity, motion


def make_grid(reference: Tensor) -> Tensor:
    if reference.ndim != 4:
        raise ValueError("grid reference must be BCHW")
    batch, _, height, width = reference.shape
    y, x = torch.meshgrid(
        torch.arange(height, device=reference.device, dtype=reference.dtype),
        torch.arange(width, device=reference.device, dtype=reference.dtype),
        indexing="ij",
    )
    return torch.stack((x, y), dim=0).unsqueeze(0).expand(batch, -1, -1, -1)


def warp(
    values: Tensor,
    flow: Tensor,
    grid: Tensor,
    *,
    mode: str,
) -> Tensor:
    if values.ndim != 4 or flow.shape != (values.shape[0], 2, *values.shape[-2:]):
        raise ValueError("warp values or flow shape differs")
    _, _, height, width = values.shape
    displaced = grid + flow
    x = 2.0 * displaced[:, 0] / max(width - 1, 1) - 1.0
    y = 2.0 * displaced[:, 1] / max(height - 1, 1) - 1.0
    sampling_grid = torch.stack((x, y), dim=-1)
    return F.grid_sample(
        values,
        sampling_grid,
        mode=mode,
        padding_mode="border",
        align_corners=True,
    )


def _weighted_l1(target: Tensor, prediction: Tensor, weight_cap: float) -> Tensor:
    weights = torch.clamp(1.0 + target, max=weight_cap)
    return torch.mean(weights * torch.abs(target - prediction))


def _motion_regularization(motion: Tensor, targets: Tensor, weight_cap: float) -> Tensor:
    batch, target_frames, _, height, width = motion.shape
    flattened = motion.reshape(batch * target_frames * 2, 1, height, width)
    horizontal = motion.new_tensor(
        [[1.0, 0.0, -1.0], [2.0, 0.0, -2.0], [1.0, 0.0, -1.0]]
    ).reshape(1, 1, 3, 3)
    vertical = motion.new_tensor(
        [[1.0, 2.0, 1.0], [0.0, 0.0, 0.0], [-1.0, -2.0, -1.0]]
    ).reshape(1, 1, 3, 3)
    gradient_x = F.conv2d(flattened, horizontal, padding=1)
    gradient_y = F.conv2d(flattened, vertical, padding=1)
    gradient_energy = (gradient_x.square() + gradient_y.square()).reshape(
        batch,
        target_frames,
        2,
        height,
        width,
    )
    weights = torch.clamp(1.0 + targets, max=weight_cap).unsqueeze(2)
    return torch.mean(gradient_energy * weights)


def rollout_evolution(
    inputs: Tensor,
    intensity: Tensor,
    motion: Tensor,
    *,
    mode: str = "nearest",
) -> Tensor:
    """Roll out detached evolution steps without requiring future observations."""

    if inputs.ndim != 4 or intensity.ndim != 4 or motion.ndim != 4:
        raise ValueError("evolution rollout inputs must be BCHW sequences")
    batch, target_frames, height, width = intensity.shape
    if (
        inputs.shape[0] != batch
        or inputs.shape[-2:] != (height, width)
        or motion.shape != (batch, target_frames * 2, height, width)
        or mode not in {"nearest", "bilinear"}
    ):
        raise ValueError("evolution rollout shapes or interpolation mode differ")
    motion_fields = motion.reshape(batch, target_frames, 2, height, width)
    current = inputs[:, -1:].detach()
    grid = make_grid(current)
    predictions = []
    for step in range(target_frames):
        current = warp(current.detach(), motion_fields[:, step], grid, mode=mode)
        current = current + intensity[:, step : step + 1]
        predictions.append(current)
    return torch.cat(predictions, dim=1)


@dataclass(frozen=True)
class EvolutionLoss:
    total: Tensor
    accumulation: Tensor
    motion_regularization: Tensor
    prediction: Tensor
    bilinear_advection: Tensor


def evolution_loss(
    inputs: Tensor,
    targets: Tensor,
    intensity: Tensor,
    motion: Tensor,
    *,
    motion_regularization_lambda: float = 0.01,
    weight_cap: float = 24.0,
) -> EvolutionLoss:
    """Roll out the semi-Lagrangian operator and compute the evolution objective."""

    if inputs.ndim != 4 or targets.ndim != 4:
        raise ValueError("evolution inputs and targets must be BCHW sequences")
    batch, target_frames, height, width = targets.shape
    if (
        intensity.shape != targets.shape
        or motion.shape != (batch, target_frames * 2, height, width)
        or inputs.shape[0] != batch
        or inputs.shape[-2:] != (height, width)
    ):
        raise ValueError("evolution output, target, or input shapes differ")
    if motion_regularization_lambda < 0.0 or weight_cap <= 0.0:
        raise ValueError("evolution loss weights are invalid")

    motion_fields = motion.reshape(batch, target_frames, 2, height, width)
    intensity_fields = intensity.reshape(batch, target_frames, 1, height, width)
    current = inputs[:, -1:].detach()
    grid = make_grid(current)
    predictions = []
    bilinear_advections = []
    accumulation = current.new_zeros(())
    for step in range(target_frames):
        previous = current.detach()
        bilinear = warp(previous, motion_fields[:, step], grid, mode="bilinear")
        nearest = warp(previous, motion_fields[:, step], grid, mode="nearest")
        current = nearest + intensity_fields[:, step]
        truth = targets[:, step : step + 1]
        accumulation = accumulation + _weighted_l1(truth, bilinear, weight_cap)
        accumulation = accumulation + _weighted_l1(truth, current, weight_cap)
        bilinear_advections.append(bilinear)
        predictions.append(current)

    prediction = torch.cat(predictions, dim=1)
    bilinear_advection = torch.cat(bilinear_advections, dim=1)
    regularization = _motion_regularization(motion_fields, targets, weight_cap)
    total = accumulation + motion_regularization_lambda * regularization
    return EvolutionLoss(
        total=total,
        accumulation=accumulation,
        motion_regularization=regularization,
        prediction=prediction,
        bilinear_advection=bilinear_advection,
    )
