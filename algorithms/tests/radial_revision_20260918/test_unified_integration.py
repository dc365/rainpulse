"""Real engine actions and writer proof, not only isolated candidate masks."""

import numpy as np
import pytest

from .conftest import evaluate, load
from .test_native_subbands import fixture
from .test_variable_morphology import fixture as variable_fixture

P = "RV2_UNIFIED_"


def attach_canonical(n, arrays):
    """Independent runner/writer fields, never copied from the object proof."""
    arrays["range"] = n.ranges.copy()
    arrays["azimuth"] = n.azimuth.copy()
    arrays["NATIVE_QC_ORDER"] = np.broadcast_to(np.arange(n.shape[0])[:, None], n.shape).astype(
        "uint32"
    )
    arrays["NATIVE_QC_GOOD_MASK"] = np.broadcast_to(n.geometry_good[:, None], n.shape).astype(
        "uint8"
    )
    arrays["NATIVE_QC_GAP_MASK"] = np.broadcast_to(n.gap_after[:, None], n.shape).astype("uint8")
    for name in ("DBZH", "SNR", "RHOHV"):
        if name in n.fields:
            arrays[name + "_RAW"] = n.fields[name].copy()
    return arrays


def config(*, mode="experiment_quarantine", subbands=True, separated=True):
    return load("radial_revision.config").RadialRevisionConfig(
        step=3,
        mode=mode,
        fragment_line={
            "coherent_source_enabled": False,
            "unified_objects_enabled": True,
            "unified_subbands_enabled": subbands,
            "unified_separated_edges_enabled": separated,
        },
    )


@pytest.mark.parametrize("subbands,separated", [(False, False), (True, False), (True, True)])
def test_engine_outer_mode_owns_action_and_never_promotes_source_identity(subbands, separated):
    n = fixture() if subbands else variable_fixture()
    cfg = config(subbands=subbands, separated=separated)
    raw = n.fields["DBZH"].copy()
    fields, report = evaluate(n, cfg)
    hit = fields[P + "PROPOSAL_MASK"] == 1
    assert hit.any()
    assert fields["RV2_GEOMETRY_ACTION_MASK"][hit].all()
    assert fields["RV2_ACTION_PROPOSAL_MASK"][hit].all()
    assert not fields[P + "ACTION_MASK"].any()
    assert not fields["RV2_LEGACY_MATCH_MASK"].any()
    assert "RV2_LINE_SOURCE_MASK" not in fields
    fields["DBZH_RAW"] = raw.copy()
    attach_canonical(n, fields)
    load("radial_revision.validation").validate_revision_fields(
        fields, n.field_available["DBZH"], np.zeros(n.shape, bool), np.zeros(n.shape, bool)
    )
    audit, _ = evaluate(n, cfg.model_copy(update={"mode": "audit"}))
    assert np.array_equal(audit[P + "PROPOSAL_MASK"], hit.astype("uint8"))
    assert not audit["RV2_ACTION_PROPOSAL_MASK"].any()
    assert report["fragment_line"]["unified_objects"]["product_writes"] is False
    assert np.array_equal(raw, n.fields["DBZH"], equal_nan=True)


def writer_fixture():
    n = fixture()
    cfg = load("config").SourceReviewConfig(narrow_enabled=False, radial_revision=config())
    _, arrays, _ = load("source").source_additions(
        n, cfg, np.zeros(n.shape, bool), np.zeros(n.shape, "float32")
    )
    arrays["SRC_REVIEW_REFERENCE_FOLD_ID"] = np.zeros(n.shape, "uint32")
    attach_canonical(n, arrays)
    return n, arrays


def test_source_writer_replays_full_proof_after_native_row_restore():
    n, arrays = writer_fixture()
    hit = arrays[P + "PROPOSAL_MASK"] == 1
    assert arrays["SRC_REVIEW_QUALIFIED_MASK"][hit].all()
    validator = load("source_validation").validate_source_fields
    validator(arrays, n.field_available["DBZH"])
    order = np.random.default_rng(24).permutation(n.shape[0])
    validator(
        {k: v.copy() if k == "range" else v[order].copy() for k, v in arrays.items()},
        n.field_available["DBZH"][order],
    )


@pytest.mark.parametrize(
    "field",
    [
        "SNR_RAW",
        "RHOHV_RAW",
        "range",
        "azimuth",
        "NATIVE_QC_ORDER",
        "NATIVE_QC_GOOD_MASK",
        "NATIVE_QC_GAP_MASK",
    ],
)
def test_writer_rejects_proof_disagreement_with_independent_native_fields(field):
    n, arrays = writer_fixture()
    if field == "RHOHV_RAW":
        arrays[field] = np.full(n.shape, 0.99, "float32")
    elif field in ("range", "azimuth"):
        arrays[field] *= 0.1
    elif field == "SNR_RAW":
        arrays[field][[21, 49], :] = np.nan
    elif field == "NATIVE_QC_ORDER":
        arrays[field][[0, 1]] = arrays[field][[1, 0]]
    else:
        arrays[field][0] ^= 1
    with pytest.raises(ValueError):
        load("source_validation").validate_source_fields(arrays, n.field_available["DBZH"])


def test_writer_rejects_invented_weather_clearance_even_with_consistent_decision_proof():
    n, arrays = writer_fixture()
    arrays["SNR_RAW"][23, 500] = 20
    arrays["RHOHV_RAW"] = np.full(n.shape, np.nan, "float32")
    arrays["RHOHV_RAW"][23, 500] = 0.99
    assert arrays[P + "PROPOSAL_MASK"][23, 500] == 1
    with pytest.raises(ValueError):
        load("source_validation").validate_source_fields(arrays, n.field_available["DBZH"])


@pytest.mark.parametrize(
    "case",
    [
        "decision",
        "raw",
        "policy",
        "order",
        "version",
        "budget",
        "partial",
        "extra",
        "missing_raw",
        "missing_mode",
    ],
)
def test_writer_rejects_unbound_or_forged_unified_proof(case):
    n, arrays = writer_fixture()
    if case == "decision":
        arrays[P + "PROPOSAL_MASK"][23, 500] ^= 1
    elif case == "raw":
        arrays[P + "MEASURED_DBZH"][23, 500] += 1
    elif case == "policy":
        arrays[P + "POLICY_CODE"][0, 0] = 1
    elif case == "order":
        arrays[P + "NATIVE_ORDER"][0, 1] = 1
    elif case == "version":
        arrays[P + "VERSION_CODE"][:] = 2
    elif case == "budget":
        arrays[P + "MAXIMUM_OBJECTS"][:] = 0
    elif case == "partial":
        del arrays[P + "PROPOSAL_MASK"]
    elif case == "missing_raw":
        del arrays["DBZH_RAW"]
    elif case == "missing_mode":
        del arrays["RV2_MODE_CODE"]
        arrays["SRC_REVIEW_QUALIFIED_MASK"][:] = 0
    else:
        arrays[P + "UNSUPPORTED_MASK"] = np.zeros(n.shape, "uint8")
    with pytest.raises(ValueError):
        load("source_validation").validate_source_fields(arrays, n.field_available["DBZH"])


def test_new_provider_obeys_external_and_raw_volume_weather_barriers():
    n = fixture()
    protected = n.field_available["DBZH"].copy()
    for option in ("weather", "geometry_weather_protection"):
        fields, _ = evaluate(n, config(), **{option: protected})
        assert not fields[P + "PROPOSAL_MASK"].any()
        assert not fields["RV2_GEOMETRY_ACTION_MASK"].any()


def test_provider_resource_failure_discards_every_partial_path(monkeypatch):
    module = load("radial_revision.native_subbands")

    def fail(*args, **kwargs):
        raise load("radial_revision.geometry").ResourceLimit("injected combined object budget")

    monkeypatch.setattr(module, "nominate", fail)
    fields, report = evaluate(fixture(), config())
    assert report["status"] == "resource_limit_abstained"
    assert not fields["RV2_ACTION_PROPOSAL_MASK"].any()
    assert not any(k.startswith(P) for k in fields)


def test_flags_default_off_strict_and_require_their_original_provider():
    cls = load("radial_revision.config").FragmentLineConfig
    base = cls()
    assert not base.unified_objects_enabled
    assert not base.unified_subbands_enabled
    assert not base.unified_separated_edges_enabled
    for values in (
        {"unified_subbands_enabled": True},
        {"unified_separated_edges_enabled": True},
        {"unified_objects_enabled": "true"},
    ):
        with pytest.raises(ValueError):
            cls(**values)


def test_actual_broad_stage_and_final_projection_isolate_data_without_changing_raw():
    from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig
    from rainpulse_algo.radar.qc_engine.finalize import finalize_decision
    from rainpulse_algo.radar.qc_engine.generalization import broad_source_review

    from ..test_generalization_p0p2 import health, keep, oc_fixture, profile

    n = fixture()
    n.audit = {"azimuth_spacing_deg": 1.0}
    raw = n.fields["DBZH"].copy()
    p = profile()
    broad = BroadSourceConfig(
        mode="experiment_quarantine",
        source_review={
            "mode": "experiment_quarantine",
            "narrow_enabled": False,
            "radial_revision": config().model_dump(mode="json"),
        },
    )
    p = p.model_copy(
        update={"generalization": p.generalization.model_copy(update={"broad_source": broad})}
    )
    e, o = oc_fixture(n, bits=0)
    e.arrays["family_code"][:] = 0
    before = keep(n)
    decision, _ = broad_source_review(n, before, p, e, o)
    hit = decision.arrays[P + "PROPOSAL_MASK"] == 1
    assert hit.any()
    assert decision.arrays["P2_ADDED_QUARANTINE_MASK"][hit].all()
    assert (decision.arrays["QC_ACTION"][hit] == 1).all()
    finalize_decision(n, decision, p, health())
    assert not decision.arrays["QPE_ELIGIBLE_MASK"][hit].any()
    assert not decision.arrays["REFLECTIVITY_TRUST_MASK"][hit].any()
    assert np.isnan(decision.arrays["DBZH_USABLE"][hit]).all()
    assert np.array_equal(raw, n.fields["DBZH"], equal_nan=True)
    assert (decision.arrays["QC_ACTION"][~n.field_available["DBZH"]] == 3).all()
    audit = p.model_copy(
        update={
            "generalization": p.generalization.model_copy(
                update={"broad_source": broad.model_copy(update={"mode": "audit"})}
            )
        }
    )
    retained, _ = broad_source_review(n, before, audit, e, o)
    assert np.array_equal(retained.arrays["QPE_ELIGIBLE_MASK"], before.arrays["QPE_ELIGIBLE_MASK"])


def test_actual_runner_and_zarr_writer_bind_independent_raw_and_native_coordinates():
    import zarr
    from zarr.storage import MemoryStore

    from rainpulse_algo.radar.qc import apply_basic_qc
    from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig
    from rainpulse_algo.radar.qc_zarr import build_validated_qc_zarr_store

    from ..test_generalization_p0p2 import profile
    from ..test_radar_qc import synthetic_normalized_fixture

    n = fixture()
    # Actual canonical input order differs from angular native order.
    order = np.random.default_rng(62).permutation(n.shape[0])
    objects = synthetic_normalized_fixture(
        n.fields["DBZH"][order],
        azimuth_deg=n.azimuth[order],
        range_m=n.ranges,
        moments={k: v[order] for k, v in n.fields.items() if k != "DBZH"},
    )
    p = profile()
    broad = BroadSourceConfig(
        mode="experiment_quarantine",
        source_review={
            "mode": "experiment_quarantine",
            "narrow_enabled": False,
            "radial_revision": config().model_dump(mode="json"),
        },
    )
    p = p.model_copy(
        update={"generalization": p.generalization.model_copy(update={"broad_source": broad})}
    )
    result = apply_basic_qc(objects, p)
    proof = result.sweeps[0].optional_qc_fields
    assert proof[P + "PROPOSAL_MASK"].any()
    assert np.array_equal(proof["NATIVE_QC_ORDER"], proof[P + "NATIVE_ORDER"])
    product, _ = build_validated_qc_zarr_store(
        objects,
        result,
        asset_id="00000000-0000-4000-8000-000000000062",
        normalized_volume_uri="s3://rainpulse/test/unified-normalized.zarr",
    )
    store = MemoryStore()
    store.update(product)
    group = zarr.open_group(store, mode="r")["sweep_000"]
    assert np.array_equal(group["azimuth"][:], n.azimuth[order])
    assert np.array_equal(group["SNR_RAW"][:], n.fields["SNR"][order], equal_nan=True)
    load("source_validation").validate_source_fields(group, group["VALID_MASK"][:] == 1)
