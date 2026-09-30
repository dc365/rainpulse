# ruff: noqa: E501, I001
from .helpers import station
from rainpulse_algo.multiband.adapters import _station_registered_calibration


def test_station_registration_extends_to_volumes_without_identity():
    st = station()  # calibration_verified=True, calibration_id='synthetic-cal'
    assert _station_registered_calibration({}, st) == 'synthetic-cal'
    assert _station_registered_calibration({'calibration_id': 'unverified'}, st) == 'synthetic-cal'


def test_explicit_volume_identity_is_never_overridden():
    st = station()
    assert _station_registered_calibration({'calibration_id': 'site-report-2026-01'}, st) == 'site-report-2026-01'


def test_unverified_station_inherits_nothing():
    st = station(calibration_verified=False)
    assert _station_registered_calibration({}, st) == 'unverified'
    assert _station_registered_calibration({'calibration_id': 'other-cal'}, st) == 'other-cal'
