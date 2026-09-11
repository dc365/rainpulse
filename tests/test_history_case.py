"""Replay-package guards; run with python3 -m unittest discover -s tests -p test_history_case.py."""

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "case", Path(__file__).resolve().parents[1] / "scripts/history_case.py"
)
case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(case)


class HistoryCaseTests(unittest.TestCase):
    def package_fixture(self, root):
        files = {}
        for name, value in {
            "database.json": {},
            "schema.json": {"pk": {}, "fks": [], "columns": {}},
            "cycles.json": [],
        }.items():
            data = json.dumps(value).encode()
            (root / name).write_bytes(data)
            files[name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "format": case.FORMAT,
                    "mode": "replay-only",
                    "date": "2026-08-28",
                    "cycle_count": 0,
                    "files": files,
                }
            )
        )

    def test_verify_rejects_tampered_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.package_fixture(root)
            args = SimpleNamespace(package=root, verify_only=True)
            case.import_case(args)
            (root / "database.json").write_text('{"changed":true}')
            with self.assertRaises(ValueError):
                case.import_case(args)

    def test_existing_case_store_refused_before_object_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.package_fixture(root)
            (root / ".env").write_text(
                "RAINPULSE_RADAR_INGEST_ENABLED=false\nRAINPULSE_PIPELINE_ENABLED=false\n"
            )
            args = SimpleNamespace(
                package=root,
                root=root,
                env_file=root / ".env",
                verify_only=False,
                check_database=False,
            )
            db, objects = Mock(), Mock()
            db.schema.return_value = {"pk": {}, "fks": [], "columns": {}}
            db.query.return_value = [{"n": 1}]
            with (
                patch.object(case, "Database", return_value=db),
                patch.object(case, "Objects", return_value=objects),
            ):
                with self.assertRaises(ValueError):
                    case.import_case(args)
            objects.request.assert_not_called()
            db.run.assert_not_called()

    def test_secret_values_never_enter_package(self):
        case.check_secrets({"config": {"threshold": 10}})
        for value in [
            {"password": "test"},
            {"x": "postgres://u:p@host/db"},
            {"nested": [{"api_key": "test"}]},
        ]:
            with self.assertRaises(ValueError):
                case.check_secrets(value)

    def test_path_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(
                case.safe_path(root, "objects/products/a"), root / "objects/products/a"
            )
            for path in ["../a", "/a", "a/../b", "a\\b", "a//b"]:
                with self.assertRaises(ValueError):
                    case.safe_path(root, path)
            (root / "escape").symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                case.safe_path(root, "escape/other")

    def test_local_day_and_pagination(self):
        pages = [
            {
                "items": [
                    {"cycle_id": "a", "issue_time": "2026-08-27T16:00:00Z"},
                    {"cycle_id": "out", "issue_time": "2026-08-27T15:59:00Z"},
                ],
                "next_cursor": "two",
            },
            {
                "items": [
                    {"cycle_id": "b", "issue_time": "2026-08-28T15:59:00Z"},
                    {"cycle_id": "next", "issue_time": "2026-08-28T16:00:00Z"},
                ]
            },
        ]
        with patch.object(case, "http_json", side_effect=pages):
            self.assertEqual(
                [
                    r["cycle_id"]
                    for r in case.select_day("http://local", "2026-08-28", 8)
                ],
                ["a", "b"],
            )

    def test_foreign_keys_parent_first_and_missing_rejected(self):
        schema = {
            "columns": {"workflow_runs": ["run_id"], "forecast_runs": ["run_id"]},
            "pk": {"workflow_runs": ["run_id"], "forecast_runs": ["run_id"]},
            "fks": [
                {
                    "child": "forecast_runs",
                    "parent": "workflow_runs",
                    "child_keys": ["run_id"],
                    "parent_keys": ["run_id"],
                    "deferred": False,
                }
            ],
        }
        rows = {"forecast_runs": [{"run_id": "a"}], "workflow_runs": [{"run_id": "a"}]}
        self.assertEqual(
            [t for t, _ in case.row_order(rows, schema)],
            ["workflow_runs", "forecast_runs"],
        )
        with self.assertRaises(ValueError):
            case.row_order({"forecast_runs": [{"run_id": "a"}]}, schema)

    def test_queue_rows_are_never_accepted(self):
        with self.assertRaises(ValueError):
            case.row_order(
                {"outbox_events": [{"id": "event"}]},
                {"columns": {}, "pk": {}, "fks": []},
            )

    def test_active_jobs_cannot_restart_after_import(self):
        case.validate_replay_rows({"jobs": [{"status": "SUCCEEDED"}]})
        with self.assertRaises(ValueError):
            case.validate_replay_rows({"jobs": [{"status": "RUNNING"}]})

    def test_repeated_catalog_cursor_is_rejected(self):
        page = {"items": [], "next_cursor": "same"}
        with patch.object(case, "http_json", return_value=page):
            with self.assertRaises(ValueError):
                case.select_day("http://local", "2026-08-28", 8)


if __name__ == "__main__":
    unittest.main()
