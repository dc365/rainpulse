"""Corrupted grouped evidence must not reach the QC action projection."""

import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.config import ClutterFusionConfig
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.disposition import apply
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.engine import evaluate_volume
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.near_revision_config import (
    NearRevisionConfig,
    StrongNearConfig,
)
from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep


def test_action_projection_rejects_nan_propagated_object_identity():
    shape = (12, 100)
    fields = {
        "DBZH": np.broadcast_to(np.tile([35.0, 42.0, 25.0, 18.0], 25), shape).astype("float32"),
        "RHOHV": np.full(shape, 0.7, "float32"),
        "SNR": np.full(shape, 20.0, "float32"),
        "PHIDP": np.random.default_rng(1).uniform(0, 360, shape).astype("float32"),
    }
    gaps = np.zeros(shape[0], bool)
    gaps[-1] = True
    sweep = Sweep(
        "sweep_000",
        np.arange(shape[0], dtype=float),
        np.full(shape[0], 0.5),
        2000.0 + np.arange(shape[1]) * 250,
        fields,
        {key: np.isfinite(value) for key, value in fields.items()},
        np.ones(shape[0], bool),
        gaps,
        np.zeros(shape[0]),
    )
    config = ClutterFusionConfig(
        mode="quarantine",
        depolarization_backend="numpy_reference",
        near_revision=NearRevisionConfig(
            mode="cr_withhold",
            strong_near=StrongNearConfig(mode="quarantine", object_propagation=True),
        ),
    )
    observed = sweep.observed
    baseline = {
        "DBZH_RAW": fields["DBZH"].copy(),
        "DBZH_QC": fields["DBZH"].copy(),
        "DBZH_USABLE": fields["DBZH"].copy(),
        "QC_FLAGS": np.zeros(shape, "uint32"),
        "QUALITY_INDEX": np.full(shape, 0.8, "float32"),
        "QC_ACTION": np.zeros(shape, "uint8"),
        "LOW_QUALITY_MASK": np.zeros(shape, "uint8"),
        "CR_UNCERTAIN_MASK": np.zeros(shape, "uint8"),
        "CR_QUALIFICATION_REASON": observed.astype("uint16"),
    }
    for key in (
        "VALID_MASK",
        "REFLECTIVITY_TRUST_MASK",
        "QPE_ELIGIBLE_MASK",
        "REFLECTIVITY_ELIGIBLE_FOR_CR",
    ):
        baseline[key] = observed.astype("uint8")
    for key, value in fields.items():
        baseline[key + "_RAW"] = value.copy()
        baseline[key + "_TRUST_MASK"] = (sweep.available[key] & observed).astype("uint8")

    evidence = {
        key: value.copy() for key, value in evaluate_volume([sweep], config)[0].arrays.items()
    }
    propagated = evidence["CF_NR_STRONG_OBJECT_PROPAGATED_MASK"] == 1
    assert propagated.any()
    # The unmodified evidence must pass the complete action boundary first.
    apply(baseline, evidence, config, low_quality_flag=1024)

    evidence["CF_NR_STRONG_OBJECT_ID"] = evidence["CF_NR_STRONG_OBJECT_ID"].astype(float)
    evidence["CF_NR_STRONG_OBJECT_ID"][propagated] = np.nan
    evidence["CF_NR_STRONG_OBJECT_SIZE"][propagated] = 999999
    evidence["CF_NR_STRONG_OBJECT_SEED_FRACTION"][propagated] = 0.001
    with pytest.raises(ValueError, match="NaN"):
        apply(baseline, evidence, config, low_quality_flag=1024)
