"""Regression guards for the management slice's intentionally narrow scope."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def test_native_compute_is_reused_not_reimplemented():
    source=(ROOT/'algorithms/rainpulse_algo/operations/native.py').read_text()
    assert '_execute_basic_qc' in source and '_execute_analysis_diagnostics' in source
    assert 'subprocess' not in source and 'os.system' not in source
    assert 'AtomicObjectPublisher' in source


def test_worker_does_not_publish_legacy_completion_events():
    source=(ROOT/'algorithms/rainpulse_algo/operations/worker.py').read_text()
    assert 'rainpulse.jobs.requested.ops.' in source
    assert 'js.publish(' not in source and 'jetstream.publish(' not in source
    assert 'subscription.fetch(1,' in source


def test_new_store_does_not_mutate_automatic_pipeline_state():
    source=(ROOT/'services/control/internal/operations/store.go').read_text()
    for forbidden in ['UPDATE jobs ', 'UPDATE radar_scan_runs ', 'UPDATE analysis_cycles ', 'UPDATE forecast_runs ', 'DELETE FROM jobs']:
        assert forbidden not in source
    assert 'INSERT INTO outbox_events' in source
    assert 'token_sha256' in source and 'current_attempt' in source


def test_background_services_do_not_silently_replace_existing_workers():
    source=(ROOT/'scripts/admin_opsctl.py').read_text()
    assert "'--no-deps'" in source
    assert "'ops-qc-worker', 'ops-render-worker', 'ops-diagnostics-worker'" in source
    assert 'remove-orphans' not in source
