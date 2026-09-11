import numpy as np

from rainpulse_algo.verification.analysis import common_metrics, spectrum, summarize


def test_common_domain_does_not_reward_missing():
    obs = np.ones((40,40))*10
    good = obs.copy()
    partial = obs.copy()
    partial[:20] = np.nan
    results = common_metrics(obs, {'lk':good,'steps':partial}, [118,25,118.4,25.4])
    assert results['lk']['valid_cells'] == results['steps']['valid_cells'] == 800


def test_psd_identical_sinusoid_crop_missing_and_zero():
    x = np.arange(64)
    field = np.tile(10+np.sin(2*np.pi*x/8), (64,1))
    result = spectrum({'qpe':field,'lk':field.copy()}, [118,25,118.64,25.64])
    assert result['status'] == 'ready'
    np.testing.assert_allclose(result['series']['qpe'], result['series']['lk'])
    scale = result['scale_km'][int(np.argmax(result['series']['qpe']))]
    assert 6 < scale < 10
    masked = field.copy()
    masked[::4] = np.nan
    assert spectrum({'qpe':field,'lk':masked}, [118,25,118.64,25.64])['status'] == 'unavailable'
    zero = spectrum({'qpe':field*0,'lk':field*0}, [118,25,118.64,25.64])
    assert all(v == 0 for v in zero['series']['qpe'])


def test_summary_uses_same_finite_samples_and_macro_not_zero_fill():
    records = [dict(lead_minutes=10,status='ready',metrics={
        'lk':dict(csi=.2,fss=.4,coverage=.5),'steps':dict(csi=None,fss=.6,coverage=.5)}),
        dict(lead_minutes=10,status='ready',metrics={
        'lk':dict(csi=.8,fss=.8,coverage=.8),'steps':dict(csi=.4,fss=.6,coverage=.8)}),
        dict(lead_minutes=20,status='unavailable')]
    summary = summarize(records,['lk','steps'])
    assert summary['overall']['lk']['csi'] == {'mean':.8,'n':1}
    assert np.isclose(summary['overall']['lk']['fss']['mean'],.6)
    assert summary['overall']['steps']['csi']['n'] == 1
    assert summary['matched_records'] == 2 and summary['skipped_records'] == 1
