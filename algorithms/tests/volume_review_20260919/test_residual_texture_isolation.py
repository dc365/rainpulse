import copy

import numpy as np
from volume_review.data import array_digest
from volume_review.residual_texture_isolation import evaluate_residual_texture_isolation


def make_case(mode: str):
    rows, gates = 48, 180
    az = np.arange(rows, dtype=float) * 7.5
    r = 500.0 + np.arange(gates) * 500.0
    shape = (rows, gates)
    a = {
        "azimuth": az.copy(),
        "range": r.copy(),
        "DBZH_RAW": np.full(shape, -20.0, "float32"),
        "RHOHV_RAW": np.full(shape, 0.99, "float32"),
        "SNR_RAW": np.full(shape, 2.0, "float32"),
        "VALID_MASK": np.ones(shape, "uint8"),
        "REFLECTIVITY_ELIGIBLE_FOR_CR": np.ones(shape, "uint8"),
    }
    if mode == "blob":
        a["DBZH_RAW"][9:33, 32:55] = -5.0
        yy, xx = np.ogrid[12:30, 35:52]
        noisy = 12.0 + 8.0 * np.sin(xx * 1.7) * np.cos(yy * 1.3)
        a["DBZH_RAW"][12:30, 35:52] = noisy
        a["RHOHV_RAW"][12:30, 35:52] = 0.84
        a["SNR_RAW"][12:30, 35:52] = 12.0
    elif mode == "smooth":
        a["DBZH_RAW"][9:33, 32:55] = -5.0
        a["DBZH_RAW"][12:30, 35:52] = 12.0
        a["RHOHV_RAW"][12:30, 35:52] = 0.84
        a["SNR_RAW"][12:30, 35:52] = 12.0
    elif mode == "isolated":
        a["DBZH_RAW"][24, 90] = 12.0
        a["RHOHV_RAW"][24, 90] = 0.84
        a["SNR_RAW"][24, 90] = 12.0
    elif mode == "protected":
        a["DBZH_RAW"][24, 90] = 12.0
        a["RHOHV_RAW"][24, 90] = 0.84
        a["SNR_RAW"][24, 90] = 12.0
        a["CF_WEATHER_PROXY_MASK"] = np.zeros(shape, "uint8")
        a["CF_WEATHER_PROXY_MASK"][24, 90] = 1
    else:
        raise ValueError(mode)
    return a


def test_textured_blob_and_isolated_speckle_are_audit_evidence():
    blob = evaluate_residual_texture_isolation(make_case("blob"))
    assert (blob.arrays["RTI_BLOB_MASK"] == 1).any()
    assert (blob.arrays["RTI_OBJECT_MASK"] == 1).sum() >= 8

    isolated = evaluate_residual_texture_isolation(make_case("isolated"))
    assert (isolated.arrays["RTI_ISOLATED_MASK"] == 1).any()
    assert isolated.summary["action_gates"] == 0
    assert not isolated.summary["changes_measurement_qualification"]


def test_smooth_object_and_weather_protection_are_not_selected():
    smooth = evaluate_residual_texture_isolation(make_case("smooth"))
    assert not (smooth.arrays["RTI_BLOB_MASK"] == 1).any()
    protected = evaluate_residual_texture_isolation(make_case("protected"))
    assert not (protected.arrays["RTI_OBJECT_MASK"] == 1).any()


def test_input_is_not_mutated():
    current = make_case("blob")
    before = array_digest(current)
    evaluate_residual_texture_isolation(current)
    assert array_digest(current) == before
    copy_current = copy.deepcopy(current)
    assert array_digest(copy_current) == before
