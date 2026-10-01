import importlib.util
from pathlib import Path
import numpy as np

spec = importlib.util.spec_from_file_location('fragment_shapes', Path(__file__).resolve().parents[1]/'scripts/audit_s_fragment_shapes.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def test_radial_and_tangential_measurements_rotate_with_geometry():
    ranges = np.arange(100000., 201000., 1000.)
    azimuth = np.arange(30., 51.)
    for rotation in (0., 315.):
        radial = m.component_measurements(np.full(101, 10), np.arange(101),
            (azimuth + rotation) % 360, ranges, 1000.)
        arc = m.component_measurements(np.arange(21), np.full(21, 50),
            (azimuth + rotation) % 360, ranges, 1000.)
        assert radial['pca_axis_to_radial_deg'] < 1e-4
        assert arc['pca_axis_to_radial_deg'] > 89
        assert radial['radial_span_m'] == 101000
        assert arc['radial_span_m'] == 1000


def test_selection_measures_full_raw_parent_and_never_bridges_gap():
    raw = np.full((6, 10), 20.); available = np.ones_like(raw, bool)
    selected = np.zeros_like(available); selected[2, -1] = selected[3, -1] = True
    args = (raw, available, np.arange(6.), np.arange(10.) * 1000,
            np.ones(6, bool), np.array([0, 0, 1, 0, 0, 0]), selected, 10.)
    records, ids = m.measure_components(*args)
    assert len(records) == 2 and all(item['gates'] == 30 for item in records)
    assert records[0]['radial_span_m'] == 10000
    assert ids[2, -1] != ids[3, -1]
    available[2:4] = False
    records, _ = m.measure_components(*args)
    assert records == []


def test_fragment_groups_report_sparse_history_without_filling_or_chaining():
    ranges = np.arange(101.) * 1000
    ids = np.zeros((3, 101), np.uint32)
    ids[0, 0:2] = 1; ids[1, 50:52] = 2; ids[2, 99:101] = 3
    records = [dict(component_id=i, segment_id=0, bearing_deg=40.1,
        selected_remaining_gates=2, pca_axis_to_radial_deg=89.) for i in (1, 2, 3)]
    groups = m.measure_fragment_groups(records, ids, ranges)
    assert groups and all(g['radial_span_m'] == 101000 for g in groups)
    assert all(g['actual_range_support_m'] == 6000 for g in groups)
    assert all(g['support_fraction'] == 6/101 for g in groups)
    records[-1]['segment_id'] = 1
    assert m.measure_fragment_groups(records, ids, ranges) == []
