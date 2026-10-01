import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('s_web_identity',Path(__file__).resolve().parents[1]/'scripts/s_web_identity.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def payload(raw_scan='actual-web-scan',qc_scan='actual-web-scan'):
    frame=dict(scan_id=raw_scan,sweep_number=0,valid_time='2026-08-28T00:18:00Z',image_url='/raw',asset_id='raw')
    qc={**frame,'scan_id':qc_scan,'image_url':'/qc','asset_id':'qc'}
    return dict(cycle_id='cycle',panels=[dict(panel_id='dbzh_raw:z9598',frames=[frame]),dict(panel_id='dbzh_qc:z9598',frames=[qc])])


def reader(detail):
    def fetch(url):
        if '?limit=' in url:return dict(items=[dict(cycle_id='cycle',issue_time='2026-08-28T00:18:00Z')],next_cursor=None)
        return detail
    return fetch


def test_exact_web_frame_identity_does_not_guess_preceding_volume():
    rows=module.resolve_frames('http://internal','2026-08-28',[('z9598','08:18',0)],reader(payload()))
    assert rows[0]['web_scan_id']=='actual-web-scan'
    assert rows[0]['raw_frame']['scan_id']==rows[0]['qc_frame']['scan_id']


def test_unpaired_qc_missing_or_ambiguous_raw_fail_without_fallback():
    with pytest.raises(ValueError):module.resolve_frames('http://internal','2026-08-28',[('z9598','08:18',0)],reader(payload(qc_scan='stale-other-scan')))
    for duplicate in (False,True):
        detail=payload();frame=detail['panels'][0]['frames'][0]
        detail['panels'][0]['frames']=[frame,frame] if duplicate else []
        with pytest.raises(ValueError):module.resolve_frames('http://internal','2026-08-28',[('z9598','08:18',0)],reader(detail))


def test_missing_cycle_and_response_identity_fail_closed():
    with pytest.raises(ValueError):module.resolve_frames('http://internal','2026-08-28',[('z9598','08:24',0)],reader(payload()))
    detail=payload();detail['cycle_id']='wrong-cycle'
    with pytest.raises(ValueError):module.resolve_frames('http://internal','2026-08-28',[('z9598','08:18',0)],reader(detail))


def test_catalog_cursor_loop_is_not_partial_success():
    def fetch(url):return dict(items=[],next_cursor='repeat')
    with pytest.raises(ValueError):module.resolve_frames('http://internal','2026-08-28',[('z9598','08:18',0)],fetch)
