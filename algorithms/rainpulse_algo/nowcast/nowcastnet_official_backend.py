from __future__ import annotations

import hashlib
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from .nowcastnet_profile import NowcastNetProfile


class OfficialNowcastNetBackendError(RuntimeError):
    """Raised when the reviewed official capsule cannot be loaded safely."""


_PATCHED_SOURCE_SHA256 = {
    "code/nowcasting/layers/utils.py": (
        "b44fe50aa3254b082e6cb2eec7ba7ce52a150b618ac8ca73693f0f34e9dd28ce"
    ),
    "code/nowcasting/models/model_factory.py": (
        "cfeef468eee36934490114e54691fbcf9e0ed1808d55f675d90f44c068d68d9c"
    ),
    "code/nowcasting/models/nowcastnet.py": (
        "6465be1e2a1b11338b3850b4b618d98adfaa7db4198e8a8918991a66eb384956"
    ),
}
_CODE_LICENSE_SHA256 = "cc1815af57d92f21f89477195b88b8f3cad31202a409d7c2a32603b9dec6ddb2"
_DATA_LICENSE_SHA256 = "36ffd9dc085d529a7e60e1276d73ae5a030b020313e6c5408593a6ae2af39673"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_sha256(path: str | Path, expected_sha256: str) -> None:
    candidate = Path(path)
    if not candidate.is_file():
        raise OfficialNowcastNetBackendError(f"required artifact is missing: {candidate}")
    actual = sha256_file(candidate)
    if actual != expected_sha256:
        raise OfficialNowcastNetBackendError(
            f"artifact SHA-256 mismatch for {candidate}: {actual}"
        )


def verify_official_capsule(capsule_root: str | Path, profile: NowcastNetProfile) -> Path:
    root = Path(capsule_root).resolve()
    if not profile.source.official_source_reviewed or not profile.source.license_approved:
        raise OfficialNowcastNetBackendError("official source and licenses are not approved")
    if not profile.artifact.weights_reviewed or not profile.artifact.weights_sha256:
        raise OfficialNowcastNetBackendError("official weights are not reviewed")
    markers = {
        "RAINPULSE_CAPSULE_SHA256": profile.artifact.capsule_archive_sha256,
        "RAINPULSE_COMPATIBILITY_PATCH_SHA256": profile.runtime.compatibility_patch_sha256,
    }
    for name, expected in markers.items():
        marker = root / name
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != expected:
            raise OfficialNowcastNetBackendError(f"official capsule marker mismatch: {name}")
    verify_file_sha256(root / "code" / "LICENSE", _CODE_LICENSE_SHA256)
    verify_file_sha256(root / "data" / "LICENSE", _DATA_LICENSE_SHA256)
    for relative_path, expected in _PATCHED_SOURCE_SHA256.items():
        verify_file_sha256(root / relative_path, expected)
    weights_path = root / "data" / "checkpoints" / "mrms_model.ckpt"
    if weights_path.resolve() != profile.weights_path().resolve():
        raise OfficialNowcastNetBackendError(
            "official capsule weights do not resolve to the frozen weights URI"
        )
    verify_file_sha256(weights_path, profile.artifact.weights_sha256)
    return weights_path


def member_seeds(random_seed: int, member_count: int) -> tuple[int, ...]:
    if not 0 <= random_seed <= 2**32 - 1:
        raise OfficialNowcastNetBackendError("random seed is outside uint32")
    if member_count <= 0:
        raise OfficialNowcastNetBackendError("member count must be positive")
    return tuple((random_seed + member) % 2**32 for member in range(member_count))


class OfficialNowcastNetBackend:
    """Long-lived loader for the reviewed and device-patched Code Ocean capsule."""

    def __init__(
        self,
        capsule_root: str | Path,
        *,
        profile: NowcastNetProfile,
        device: str = "cuda:0",
        allow_cpu_for_smoke: bool = False,
    ) -> None:
        self.profile = profile
        self.capsule_root = Path(capsule_root).resolve()
        self.weights_path = verify_official_capsule(self.capsule_root, profile)
        self.device = device
        self.allow_cpu_for_smoke = allow_cpu_for_smoke
        self._torch = self._import_torch()
        self._network = self._load_network()
        self._recurrent_state = self._capture_recurrent_state()

    def _import_torch(self) -> Any:
        try:
            torch = importlib.import_module("torch")
        except ImportError as exc:
            raise OfficialNowcastNetBackendError("PyTorch is not installed") from exc
        runtime = self.profile.runtime
        if torch.__version__ != runtime.torch_version:
            raise OfficialNowcastNetBackendError(
                f"PyTorch version must be {runtime.torch_version}, got {torch.__version__}"
            )
        if torch.version.cuda != runtime.torch_cuda_version:
            raise OfficialNowcastNetBackendError(
                f"PyTorch CUDA version must be {runtime.torch_cuda_version}, "
                f"got {torch.version.cuda}"
            )
        if self.device == "cpu":
            if not self.allow_cpu_for_smoke:
                raise OfficialNowcastNetBackendError(
                    "RP-026 official backend requires CUDA outside an explicit smoke test"
                )
        elif self.device.startswith("cuda:"):
            if not torch.cuda.is_available():
                raise OfficialNowcastNetBackendError("CUDA is unavailable to PyTorch")
            index = int(self.device.split(":", maxsplit=1)[1])
            capability = ".".join(
                str(value) for value in torch.cuda.get_device_capability(index)
            )
            if capability != runtime.target_compute_capability:
                raise OfficialNowcastNetBackendError(
                    "CUDA device capability must be "
                    f"{runtime.target_compute_capability}, got {capability}"
                )
            torch.backends.cudnn.benchmark = False
            torch.backends.cudnn.deterministic = True
        else:
            raise OfficialNowcastNetBackendError(f"unsupported inference device: {self.device}")
        return torch

    def _load_network(self) -> Any:
        code_root = str(self.capsule_root / "code")
        loaded = sys.modules.get("nowcasting")
        if loaded is not None:
            loaded_path = Path(getattr(loaded, "__file__", "")).resolve()
            if not loaded_path.is_relative_to(Path(code_root)):
                raise OfficialNowcastNetBackendError(
                    "a different nowcasting package is already loaded in this worker"
                )
        if code_root not in sys.path:
            sys.path.insert(0, code_root)
        try:
            module = importlib.import_module("nowcasting.models.nowcastnet")
        except Exception as exc:
            raise OfficialNowcastNetBackendError(
                f"official NowcastNet source import failed: {type(exc).__name__}: {exc}"
            ) from exc
        protocol = self.profile.protocol
        configs = SimpleNamespace(
            input_length=protocol.input_frames,
            total_length=protocol.total_frames,
            img_height=protocol.input_height,
            img_width=protocol.input_width,
            ngf=32,
            evo_ic=protocol.output_frames,
            gen_oc=protocol.output_frames,
            ic_feature=320,
        )
        try:
            network = module.Net(configs).to(self.device)
            try:
                state = self._torch.load(
                    self.weights_path,
                    map_location=self.device,
                    weights_only=True,
                )
            except TypeError:
                state = self._torch.load(self.weights_path, map_location=self.device)
            network.load_state_dict(state)
            network.eval()
        except Exception as exc:
            raise OfficialNowcastNetBackendError(
                f"official NowcastNet model load failed: {type(exc).__name__}: {exc}"
            ) from exc
        return network

    def _capture_recurrent_state(self) -> dict[str, Any]:
        return {
            name: value.detach().clone()
            for name, value in self._network.state_dict().items()
            if name.startswith("proj.") and name.endswith(("weight_u", "weight_v"))
        }

    def _restore_recurrent_state(self) -> None:
        current = self._network.state_dict()
        with self._torch.no_grad():
            for name, saved in self._recurrent_state.items():
                current[name].copy_(saved)

    def __call__(
        self,
        model_frames: np.ndarray,
        member_count: int,
        random_seed: int,
    ) -> np.ndarray:
        protocol = self.profile.protocol
        frames = np.asarray(model_frames, dtype="float32")
        expected = (
            protocol.input_frames,
            protocol.input_height,
            protocol.input_width,
            protocol.input_channels,
        )
        if frames.shape != expected:
            raise OfficialNowcastNetBackendError(
                f"official model input must be {expected}, got {frames.shape}"
            )
        if not np.all(np.isfinite(frames)):
            raise OfficialNowcastNetBackendError("official model input contains non-finite values")
        seeds = member_seeds(random_seed, member_count)
        tensor = self._torch.from_numpy(frames[np.newaxis, ...]).to(self.device)
        members: list[np.ndarray] = []
        try:
            self._restore_recurrent_state()
            with self._torch.inference_mode():
                for seed in seeds:
                    self._torch.manual_seed(seed)
                    if self.device.startswith("cuda:"):
                        self._torch.cuda.manual_seed_all(seed)
                    output = self._network(tensor)
                    values = output[0, ..., 0].detach().to("cpu").numpy().astype(
                        "float32", copy=False
                    )
                    members.append(values)
        except Exception as exc:
            raise OfficialNowcastNetBackendError(
                f"official NowcastNet inference failed: {type(exc).__name__}: {exc}"
            ) from exc
        return np.stack(members, axis=0)

    def runtime_info(self) -> dict[str, object]:
        result: dict[str, object] = {
            "torch_version": self._torch.__version__,
            "torch_cuda_version": self._torch.version.cuda,
            "device": self.device,
            "weights_sha256": self.profile.artifact.weights_sha256,
            "capsule_archive_sha256": self.profile.artifact.capsule_archive_sha256,
        }
        if self.device.startswith("cuda:"):
            index = int(self.device.split(":", maxsplit=1)[1])
            result["device_name"] = self._torch.cuda.get_device_name(index)
            result["device_capability"] = list(
                self._torch.cuda.get_device_capability(index)
            )
        else:
            result["device_name"] = "cpu-smoke-only"
            result["device_capability"] = None
        return result

    def reset_peak_memory_stats(self) -> None:
        if self.device.startswith("cuda:"):
            index = int(self.device.split(":", maxsplit=1)[1])
            self._torch.cuda.reset_peak_memory_stats(index)

    def peak_memory_stats(self) -> dict[str, int | None]:
        if not self.device.startswith("cuda:"):
            return {
                "gpu_peak_allocated_bytes": None,
                "gpu_peak_reserved_bytes": None,
            }
        index = int(self.device.split(":", maxsplit=1)[1])
        return {
            "gpu_peak_allocated_bytes": int(
                self._torch.cuda.max_memory_allocated(index)
            ),
            "gpu_peak_reserved_bytes": int(self._torch.cuda.max_memory_reserved(index)),
        }
