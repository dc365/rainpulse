import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "filename,expected",
    [
        (
            "rp026-nowcastnet-offline-v1.yaml",
            "b6c42503366053110ac555906b3383a34448e7dbd17dc5d78ea7b05140ba2e8b",
        ),
        (
            "rp024-pysteps-steps-v1.yaml",
            "a74e3381222e14b3c5597dee602d0a7d8f629db5db5a99adda0e75f65d0647f7",
        ),
        (
            "rp016-pysteps-lk-v1.yaml",
            "2e31c4a99a823eec665973798c222659f8da7f3239f16ac66fed3e4fd79b066b",
        ),
    ],
)
def test_frozen_scientific_profiles_are_not_rewritten(filename, expected):
    assert (
        hashlib.sha256((ROOT / "configs/nowcast" / filename).read_bytes()).hexdigest() == expected
    )


def test_active_worker_planner_and_model_identity_move_together():
    import re

    import yaml

    profile = yaml.safe_load((ROOT / "configs/nowcast/prelaunch-pysteps-lk-v2.yaml").read_text())
    event_source = (ROOT / "services/control/internal/orchestration/events.go").read_text()
    model_version = re.search(r'PystepsLKModelVersion\s*=\s*"([^"\n]+)"', event_source)
    assert model_version is not None
    assert model_version.group(1) == profile["model_version"]
    for filename in [
        "deploy/docker-compose.yaml",
        "services/control/internal/controlplane/planner.go",
    ]:
        assert "configs/nowcast/prelaunch-pysteps-lk-v2.yaml" in (ROOT / filename).read_text()
