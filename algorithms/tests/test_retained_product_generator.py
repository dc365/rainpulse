from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def test_wrapper_promotes_only_success_and_keeps_two_versions(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    wrapper = repository_root / "scripts/run_retained_product_generator.py"
    generator = tmp_path / "generator.py"
    generator.write_text(
        """
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument('--output-root', type=Path, required=True)
parser.add_argument('--issue-time', required=True)
args = parser.parse_args()
identity = str(uuid4())
path = args.output_root / identity
path.mkdir(parents=True)
(path / 'payload.bin').write_bytes(b'payload')
(path / 'manifest.json').write_text(json.dumps({
    'contract_name': 'rainpulse.nowcastnet-shadow-product-bundle',
    'contract_version': '1.1',
    'bundle_id': identity,
    'issue_time': args.issue_time,
    'grid_id': 'fujian-grid',
    'model_id': 'nowcastnet',
    'created_at': datetime.now(UTC).isoformat(),
}), encoding='utf-8')
""".strip()
        + "\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "products"
    issue_time = datetime(2026, 8, 28, 2, 5, tzinfo=UTC).isoformat()

    for _ in range(3):
        completed = subprocess.run(
            [
                sys.executable,
                str(wrapper),
                "--output-root",
                str(output_root),
                "--keep-versions",
                "2",
                "--",
                sys.executable,
                str(generator),
                "--output-root",
                "{staging_root}",
                "--issue-time",
                issue_time,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        assert report["keep_versions"] == 2

    bundles = [
        path
        for path in output_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert len(bundles) == 2
    assert all((path / "manifest.json").is_file() for path in bundles)


def test_wrapper_leaves_existing_products_when_generator_fails(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    wrapper = repository_root / "scripts/run_retained_product_generator.py"
    output_root = tmp_path / "products"
    existing = output_root / "00000000-0000-4000-8000-000000000001"
    existing.mkdir(parents=True)
    (existing / "manifest.json").write_text(
        json.dumps(
            {
                "contract_name": "rainpulse.nowcastnet-shadow-product-bundle",
                "contract_version": "1.1",
                "bundle_id": existing.name,
                "issue_time": "2026-08-28T02:05:00+00:00",
                "grid_id": "fujian-grid",
                "model_id": "nowcastnet",
                "created_at": "2026-08-28T03:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(wrapper),
            "--output-root",
            str(output_root),
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
            "{staging_root}",
        ],
        check=False,
    )
    assert completed.returncode == 7
    assert existing.is_dir()
