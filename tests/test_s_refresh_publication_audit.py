"""Prove public lineage and image integrity, not just a SUCCEEDED job count."""
import importlib.util
from pathlib import Path
import struct
import zlib

import pytest


def load():
    spec = importlib.util.spec_from_file_location(
        "s_refresh_audit", Path(__file__).parents[1] / "scripts/audit_s_refresh_publication.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png():
    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body +
                struct.pack(">I", zlib.crc32(kind + body)))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\0\x10\x20\x30\xff")) + chunk(b"IEND", b""))


def test_png_checks_crc_and_decompression():
    m = load()
    assert m.png_receipt(png())["width"] == 1
    damaged = bytearray(png())
    damaged[20] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        m.png_receipt(bytes(damaged))
    with pytest.raises(ValueError):
        m.png_receipt(png()[:-12])
    with pytest.raises(ValueError):
        m.png_receipt(png() + b"trailing")


def test_png_refuses_excess_before_inflating():
    with pytest.raises(ValueError, match="byte limit"):
        load().png_receipt(png(), maximum=8)


def test_unique_membership_cannot_accept_old_or_duplicate_inputs():
    m = load()
    expected = {"scan": ("s1", "new")}
    m.require_members([{ "scan_id": "scan", "radar_id": "s1", "qc_uri": "new"}],
                      expected, "qc_uri")
    for inputs in ([{"scan_id": "scan", "radar_id": "s1", "qc_uri": "old"}],
                   [{"scan_id": "scan", "radar_id": "s2", "qc_uri": "new"}],
                   [{"scan_id": "scan", "radar_id": "s1", "qc_uri": "new"}]*2):
        with pytest.raises(ValueError, match="membership"):
            m.require_members(inputs, expected, "qc_uri")


def fixture():
    job = "00000000-0000-0000-0000-000000000001"
    t = "2026-08-28T00:12:00Z"
    def frame(sweep=0):
        return dict(scan_id="scan", sweep_number=sweep, valid_time=t, lead_time_minutes=0,
                    image_url=f"/api/v1/diagnostics/{job}/layers/example")
    detail = dict(cycle_id="cycle", analysis_id="analysis", panels=[
        dict(panel_id="dbzh_raw:s1", frames=[frame(0), frame(5)]),
        dict(panel_id="dbzh_qc:s1", frames=[frame(0), frame(5)]),
        dict(panel_id="analysis:dbzh_qc", frames=[frame()]),
        dict(panel_id="qpe", frames=[dict(frame(), valid_time="2026-08-28T00:06:00Z"), frame()])])
    group = dict(cycle_id="cycle", issue_time=t, scans=["scan"], missing_sites=["s2"])
    return detail, group, job


def test_every_sweep_and_exact_t0_public_generation_are_verified():
    m = load()
    detail, group, job = fixture()
    assert len(m.public_frames(detail, group, {"scan": "s1"}, "analysis", job)) == 6
    detail["panels"][1]["frames"][1]["image_url"] = detail["panels"][1]["frames"][1][
        "image_url"].replace(job, "00000000-0000-0000-0000-000000000099")
    with pytest.raises(ValueError, match="generation"):
        m.public_frames(detail, group, {"scan": "s1"}, "analysis", job)


def test_missing_is_not_silently_treated_as_quiet_and_pairs_are_required():
    m = load()
    detail, group, job = fixture()
    detail["panels"][1]["frames"].pop()
    with pytest.raises(ValueError, match="sweep"):
        m.public_frames(detail, group, {"scan": "s1"}, "analysis", job)
    detail, group, job = fixture()
    detail["panels"].append(dict(panel_id="dbzh_raw:s2", frames=[detail["panels"][0]["frames"][0]]))
    with pytest.raises(ValueError, match="missing"):
        m.public_frames(detail, group, {"scan": "s1"}, "analysis", job)


def test_successful_job_requires_same_envelope_identity():
    m = load()
    row = dict(job_id="job", status="SUCCEEDED", request_payload=dict(job_id="other", payload={}))
    with pytest.raises(ValueError, match="job"):
        m.job_payload(row)


def runtime_fixture(module, monkeypatch):
    detail, group, diagnostic = fixture()
    scan = "00000000-0000-0000-0000-000000000002"
    group['scans'] = [scan]
    for panel in detail['panels']:
        for frame in panel['frames']:
            frame['scan_id'] = scan
    plan = dict(identity=dict(config_sha256=dict(qc='profile')),
                scans=[dict(scan_id=scan, radar_id='s1')], groups=[group])
    bodies = dict(qc=dict(scan_id=scan, radar_id='s1', qc_profile_sha256='profile',
                          input_uri='raw', output_prefix='qc/'),
                  grid=dict(scan_id=scan, radar_id='s1', input_uri='qc/volume.zarr',
                            output_prefix='grid/'),
                  mosaic=dict(analysis_id='analysis', analysis_time=group['issue_time'],
                              inputs=[dict(scan_id=scan, radar_id='s1', grid_uri='grid/grid.zarr')],
                              output_prefix='mosaic/'),
                  qpe=dict(analysis_id='analysis', analysis_time=group['issue_time'],
                           input_uri='mosaic/mosaic.zarr', output_prefix='qpe/'),
                  diagnostics=dict(analysis_id='analysis', analysis_time=group['issue_time'],
                                   input_uri='qpe/analysis.zarr', radar_inputs=[dict(
                                       scan_id=scan, radar_id='s1', qc_uri='qc/volume.zarr')]))
    handles, rows = [], []
    for i, (kind, payload) in enumerate(bodies.items(), 10):
        job = diagnostic if kind == 'diagnostics' else f'00000000-0000-0000-0000-{i:012d}'
        key = kind+':'+(scan if kind in ('qc', 'grid') else group['issue_time'])
        handles.append(dict(key=key, job_id=job, analysis_id='analysis'))
        rows.append(dict(job_id=job, status='SUCCEEDED', request_payload=dict(
            job_id=job, payload=payload)))
    state = dict(status='DONE', scans={scan: dict(qc_uri='qc/volume.zarr',
                                                grid_uri='grid/grid.zarr')},
                 jobs=handles, completed=[group['issue_time']])
    def query(sql):
        if 'FROM jobs' in sql:
            return rows
        return [dict(scan_id=scan, normalized_uri='raw', **state['scans'][scan])]
    monkeypatch.setattr(module, 'query', query)
    monkeypatch.setattr(module, 'fetch_json', lambda _: detail)
    cache = {group['issue_time']: dict(diagnostic_job_id=diagnostic,
             images={f'/api/v1/diagnostics/{diagnostic}/layers/example': module.png_receipt(png())})}
    return plan, state, bodies, cache


@pytest.mark.parametrize('stage', ['qc', 'grid', 'mosaic', 'qpe', 'diagnostics'])
def test_complete_pipeline_rejects_any_stale_stage(stage, monkeypatch):
    m = load()
    plan, state, bodies, cache = runtime_fixture(m, monkeypatch)
    result = m.audit(plan, state, cache, 'http://example.invalid')
    assert result['status'] == 'VERIFIED'
    assert result['meteorological_effectiveness_verified'] is False
    if stage == 'qc':
        bodies[stage]['qc_profile_sha256'] = 'old'
    elif stage == 'mosaic':
        bodies[stage]['inputs'][0]['grid_uri'] = 'old'
    else:
        bodies[stage]['input_uri'] = 'old'
    with pytest.raises(ValueError):
        m.audit(plan, state, cache, 'http://example.invalid')


def test_controller_done_cannot_hide_unfinished_scans(monkeypatch):
    m = load()
    plan, state, bodies, cache = runtime_fixture(m, monkeypatch)
    plan['scans'].append(dict(scan_id='unprocessed', radar_id='s2'))
    with pytest.raises(ValueError, match='incomplete'):
        m.audit(plan, state, cache, 'http://example.invalid')
