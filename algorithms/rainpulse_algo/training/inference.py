"""Minimal inference-only composition for RainPulse-trained NowcastNet weights."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import torch
from torch import Tensor, nn

from .evolution import EvolutionNetwork, rollout_evolution
from .generation import NowcastNetGenerator


class InferenceExportError(RuntimeError):
    """Raised when a minimal inference export violates its manifest."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NowcastNetInference(nn.Module):
    """Compose a frozen evolution model and stochastic generator for inference."""

    def __init__(
        self,
        *,
        input_frames: int = 9,
        target_frames: int = 20,
        evolution_base_channels: int = 32,
        generator_base_channels: int = 32,
        rain_rate_cap_mm_h: float = 128.0,
    ) -> None:
        super().__init__()
        self.input_frames = input_frames
        self.target_frames = target_frames
        self.generator_base_channels = generator_base_channels
        self.rain_rate_cap_mm_h = rain_rate_cap_mm_h
        self.evolution = EvolutionNetwork(
            input_frames=input_frames,
            target_frames=target_frames,
            base_channels=evolution_base_channels,
        )
        self.generator = NowcastNetGenerator(
            input_frames=input_frames,
            target_frames=target_frames,
            base_channels=generator_base_channels,
            rain_rate_cap_mm_h=rain_rate_cap_mm_h,
        )

    def forward(self, inputs: Tensor, noise: Tensor) -> Tensor:
        if inputs.ndim != 4 or inputs.shape[1] != self.input_frames:
            raise ValueError("inference input must be [B,input_frames,H,W]")
        if noise.ndim != 5 or noise.shape[0] != inputs.shape[0]:
            raise ValueError("inference noise must be [B,K,C,H/32,W/32]")
        expected_noise = (
            self.generator_base_channels,
            inputs.shape[-2] // 32,
            inputs.shape[-1] // 32,
        )
        if tuple(noise.shape[2:]) != expected_noise:
            raise ValueError("inference noise dimensions differ")
        intensity, motion = self.evolution(inputs)
        evolution_prediction = rollout_evolution(inputs, intensity, motion)
        members = [
            self.generator(inputs, evolution_prediction, noise=member_noise)
            for member_noise in noise.unbind(dim=1)
        ]
        return torch.stack(members, dim=1)

    def clip_to_product_range(self, raw_prediction: Tensor) -> Tensor:
        return torch.clamp(raw_prediction, min=0.0, max=self.rain_rate_cap_mm_h)


def _safe_export_file(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise InferenceExportError("inference export path is unsafe")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise InferenceExportError("inference export file is missing or escapes its root")
    return resolved


def load_inference_export(
    export_dir: Path,
    *,
    device: str | torch.device = "cpu",
) -> tuple[NowcastNetInference, dict[str, Any]]:
    """Load a hash-verified inference-only export without optimizer state."""

    manifest_path = export_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        architecture = manifest["architecture"]
        evolution_reference = manifest["artifacts"]["evolution_state"]
        generator_reference = manifest["artifacts"]["generator_state"]
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise InferenceExportError(f"cannot read inference export manifest: {exc}") from exc
    if (
        manifest.get("schema_version") != "rainpulse.nowcastnet-inference-export/1.0"
        or manifest.get("operational_eligible") is not False
        or architecture.get("input_frames") != 9
        or architecture.get("target_frames") != 20
        or architecture.get("evolution_base_channels") != 32
        or architecture.get("generator_base_channels") != 32
        or architecture.get("rain_rate_cap_mm_h") != 128.0
        or architecture.get("raw_output_semantics") != "unbounded_generator_mm_h"
        or architecture.get("product_clip_range_mm_h") != [0.0, 128.0]
    ):
        raise InferenceExportError("inference export manifest differs from v1")

    evolution_path = _safe_export_file(export_dir, str(evolution_reference["path"]))
    generator_path = _safe_export_file(export_dir, str(generator_reference["path"]))
    if (
        _sha256(evolution_path) != str(evolution_reference["sha256"])
        or _sha256(generator_path) != str(generator_reference["sha256"])
    ):
        raise InferenceExportError("inference export artifact SHA-256 differs")

    model = NowcastNetInference(
        input_frames=int(architecture["input_frames"]),
        target_frames=int(architecture["target_frames"]),
        evolution_base_channels=int(architecture["evolution_base_channels"]),
        generator_base_channels=int(architecture["generator_base_channels"]),
        rain_rate_cap_mm_h=float(architecture["rain_rate_cap_mm_h"]),
    )
    evolution_state = torch.load(evolution_path, map_location="cpu", weights_only=True)
    generator_state = torch.load(generator_path, map_location="cpu", weights_only=True)
    model.evolution.load_state_dict(evolution_state, strict=True)
    model.generator.load_state_dict(generator_state, strict=True)
    model.eval().requires_grad_(False)
    return model.to(device), manifest
