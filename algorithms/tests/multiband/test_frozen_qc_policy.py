"""A frozen upstream producer policy is checked on fresh and cached inputs."""

import copy
import json

import pytest

from rainpulse_algo.multiband.qc_identity import validate_frozen_qc_identity, validate_s_qc_policy


def fixture():
    recipe = {
        "pipeline_version": "stored",
        "parameters_sha256": "a" * 64,
        "implementation_revision": "python-source-tree-v1:" + "b" * 64,
        "profile": "actual",
        "libraries": {"wradlib": "stored-version"},
    }
    source = {
        "radar_id": "s1",
        "scan_id": "scan",
        "expected_qc_identity": {"radar_id": "s1", "scan_id": "scan", **recipe},
    }
    metadata = {
        "radar_id": "s1",
        "scan_id": "scan",
        "band": "S",
        "qc_pipeline_version": recipe["pipeline_version"],
        "qc_parameters_sha256": recipe["parameters_sha256"],
        "qc_implementation_revision": recipe["implementation_revision"],
        "qc_profile": recipe["profile"],
        "qc_libraries": recipe["libraries"],
    }
    payload = {
        "requested_radars": ["s1", "x1"],
        "requested_s_radars": ["s1"],
        "s_qc_policy": {"s1": recipe},
        "s_qc_policy_complete": True,
        "sources": [source],
    }
    return metadata, source, payload


def test_frozen_declaration_matches_actual_asset_without_mutation(net):
    metadata, source, payload = fixture()
    before = copy.deepcopy(metadata)
    validate_s_qc_policy(payload, net)
    validate_frozen_qc_identity(metadata, source, payload)
    assert metadata == before


def test_new_request_policy_matches_contract_and_incomplete_claim_is_rejected():
    from pathlib import Path

    from jsonschema import Draft202012Validator

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3] / "contracts/internal/multiband/request.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    _, _, payload = fixture()
    source = payload["sources"][0]
    source.update(
        input_uri="s3://fixture/asset",
        volume_start="2026-08-28T00:00:00Z",
        volume_end="2026-08-28T00:01:00Z",
        available_at="2026-08-28T00:02:00Z",
    )
    payload.update(
        mode="sx_composite",
        product_id="v2",
        network_sha256="c" * 64,
        execution_sha256="d" * 64,
        analysis_time="2026-08-28T00:06:00Z",
        input_cutoff="2026-08-28T00:06:01Z",
        analysis_id="analysis",
        output_prefix="s3://rainpulse/operations/fixture",
    )
    request = {
        "schema_version": "1.0",
        "event_type": "ops.multiband.requested.v1",
        "occurred_at": payload["input_cutoff"],
        "payload": payload,
        **{
            k: "00000000-0000-4000-8000-000000000001"
            for k in ("event_id", "job_id", "run_id", "trace_id")
        },
    }
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(request))
    payload["s_qc_policy"]["s1"] = None
    assert list(validator.iter_errors(request))
    payload["s_qc_policy_complete"] = False
    assert not list(validator.iter_errors(request))
    del payload["s_qc_policy_complete"]
    assert list(validator.iter_errors(request))


@pytest.mark.parametrize(
    "field,value",
    [
        ("qc_parameters_sha256", "c" * 64),
        ("qc_implementation_revision", None),
        ("qc_pipeline_version", "other"),
        ("qc_profile", "other"),
        ("qc_libraries", {"wradlib": "different"}),
        ("scan_id", "other"),
    ],
)
def test_asset_cannot_borrow_declared_qc_identity(field, value):
    metadata, source, payload = fixture()
    metadata[field] = value
    with pytest.raises(ValueError, match="QC identity"):
        validate_frozen_qc_identity(metadata, source, payload)


def test_missing_station_keeps_same_policy_and_legacy_stays_unknown(net):
    metadata, source, payload = fixture()
    payload["sources"] = []
    validate_s_qc_policy(payload, net)
    source.pop("expected_qc_identity")
    metadata.pop("qc_implementation_revision")
    validate_frozen_qc_identity(metadata, source, {})
    payload["s_qc_policy"]["s1"] = None
    payload["s_qc_policy_complete"] = False
    validate_frozen_qc_identity(metadata, source, payload)


@pytest.mark.parametrize("field,value", [
    ("parameters_sha256", "c" * 64),
    ("pipeline_version", "other"),
    ("implementation_revision", "python-source-tree-v1:" + "d" * 64),
    ("profile", "other"),
    ("libraries", {"wradlib": "other"}),
])
def test_source_declaration_cannot_override_window_policy(field, value):
    metadata, source, payload = fixture()
    payload["s_qc_policy"]["s1"][field] = value
    with pytest.raises(ValueError, match="QC identity"):
        validate_frozen_qc_identity(metadata, source, payload)


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(s_qc_policy_complete=False),
        lambda p: p.update(requested_s_radars=[]),
        lambda p: p["s_qc_policy"].update(s1=None),
        lambda p: p["sources"][0]["expected_qc_identity"].update(parameters_sha256="c" * 64),
    ],
)
def test_inconsistent_policy_or_completeness_is_refused(net, change):
    _, _, payload = fixture()
    change(payload)
    with pytest.raises(ValueError, match="QC policy"):
        validate_s_qc_policy(payload, net)


@pytest.mark.parametrize("streaming", [False, True])
def test_real_executor_cache_hit_still_checks_frozen_upstream_identity(tmp_path, streaming):
    import hashlib

    from test_io_executor import setup_inputs

    from rainpulse_algo.multiband.codec import decode_volume, encode_volume
    from rainpulse_algo.multiband.execution import ExecutionOptions
    from rainpulse_algo.multiband.managed import Executor
    from rainpulse_algo.worker.asset_access import VerifiedArtifactReader, artifact_digest

    config, request, _, bundles = setup_inputs(tmp_path)
    station = request["payload"]["sources"][0]
    uri = station["input_uri"]
    original = decode_volume(
        bundles[uri], maximum_bytes=10 * 1024**2, asset_sha256=artifact_digest(bundles[uri])
    )
    metadata, _, policy = fixture()
    metadata["qc_pipeline_version"] = policy["s_qc_policy"]["s1"]["pipeline_version"] = (
        "s-fixture-v1"
    )
    original.metadata.update({k: v for k, v in metadata.items() if k.startswith("qc_")})
    bundles[uri] = encode_volume(original)
    station["expected_qc_identity"] = {
        **policy["sources"][0]["expected_qc_identity"],
        "scan_id": station["scan_id"],
        "pipeline_version": metadata["qc_pipeline_version"],
    }
    request["payload"].update(
        {
            k: policy[k]
            for k in (
                "requested_radars",
                "requested_s_radars",
                "s_qc_policy",
                "s_qc_policy_complete",
            )
        }
    )
    options = ExecutionOptions(streaming=streaming)
    request["payload"]["execution_sha256"] = options.digest
    storage = {}
    for input_uri, objects in bundles.items():
        entries = [
            {"key": k, "sha256": hashlib.sha256(v).hexdigest(), "size_bytes": len(v)}
            for k, v in objects.items()
        ]
        storage[input_uri + "/_SUCCESS.json"] = json.dumps(
            {
                "schema_version": "2.0",
                "sha256": artifact_digest(objects),
                "size_bytes": sum(map(len, objects.values())),
                "objects": entries,
                "data_prefix": "",
            }
        ).encode()
        storage.update({input_uri + "/" + k: v for k, v in objects.items()})
    reader = VerifiedArtifactReader(
        lambda bucket, key, limit: storage[f"s3://{bucket}/{key}"], namespace=str(tmp_path)
    )
    executor = Executor(config, execution=options)
    first, _, _ = executor.execute(request, reader, artifact_digest=artifact_digest)
    second, _, _ = executor.execute(request, reader, artifact_digest=artifact_digest)
    assert first == second
    assert (executor.cut_cache if streaming else executor.cache).hits > 0
    manifest = json.loads(first["manifest.json"])
    assert manifest["s_qc_policy"] == request["payload"]["s_qc_policy"]
    assert manifest["s_qc_policy_complete"] is True
    changed = copy.deepcopy(request)
    changed["payload"]["s_qc_policy"]["s1"]["parameters_sha256"] = "c" * 64
    changed["payload"]["sources"][0]["expected_qc_identity"]["parameters_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="QC identity"):
        executor.execute(changed, reader, artifact_digest=artifact_digest)
