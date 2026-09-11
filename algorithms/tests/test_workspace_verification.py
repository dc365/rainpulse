import numpy as np
import pytest

from rainpulse_algo.verification.workspace import spatial_metrics


def test_perfect_and_dry_and_missing():
    values = np.full((51, 51), 12.)
    result = spatial_metrics(values, values, [118,25,118.51,25.51])
    assert result['valid_cells'] == values.size
    assert result['mae'] == 0
    assert result['rows'][0]['csi'] == 1
    assert result['rows'][0]['fss'] == 1
    assert result['rows'][0]['neighborhood_csi'] == 1
    dry = spatial_metrics(values * 0, values * 0, [118,25,118.51,25.51])
    assert dry['rows'][0]['csi'] is None
    assert dry['rows'][0]['fss'] is None
    missing = spatial_metrics(values * np.nan, values, [118,25,118.51,25.51])
    assert missing['valid_cells'] == 0
    assert missing['mae'] is None
    assert all(row['fss'] is None for row in missing['rows'])


def test_shift_and_complete_neighborhood_support():
    obs = np.zeros((51, 51))
    pred = obs.copy()
    obs[25,25] = pred[25,26] = 10
    result = spatial_metrics(obs, pred, [118,25,118.51,25.51])
    rows = result['rows'][:5]
    assert rows[0]['csi'] == 0
    assert rows[0]['fss'] == 0
    assert rows[1]['fss'] > 0
    assert rows[1]['neighborhood_csi'] > 0
    pred[25,25] = np.nan
    changed = spatial_metrics(obs, pred, [118,25,118.51,25.51])
    assert changed['valid_cells'] == obs.size - 1
    assert changed['rows'][1]['neighborhood_cells'] < rows[1]['neighborhood_cells']


def test_threshold_is_strict_and_shape_checked():
    result = spatial_metrics(np.ones((3,3)), np.ones((3,3)), [118,25,118.03,25.03])
    assert result['rows'][0]['csi'] is None
    with pytest.raises(ValueError):
        spatial_metrics(np.ones((3,3)), np.ones((4,3)), [118,25,118.03,25.03])
