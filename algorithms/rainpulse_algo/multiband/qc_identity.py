"""Declared station QC identities, independent of fusion qualification."""

import re
from copy import deepcopy

FROZEN_S_QC_POLICY_VERSION = "s-qc-policy-v1"

ASSET_IDENTITY_FIELDS = (
    "qc_pipeline_version",
    "qc_parameters_sha256",
    "qc_implementation_revision",
    "qc_profile",
    "qc_libraries",
    "processing",
    "xqc_parameter_sha256",
    "xqc_implementation_revision",
    "xqc_mode",
    "xqc_base_parameter_sha256",
)


def copy_asset_qc_identity(attrs, metadata):
    """Copy stored declarations without substituting current deployment settings."""
    metadata.update({key: deepcopy(attrs[key]) for key in ASSET_IDENTITY_FIELDS if key in attrs})


def source_qc_identity(metadata):
    is_x = metadata.get("band") == "X"
    pipeline = (
        (metadata.get("processing") or metadata.get("qc_pipeline_version"))
        if is_x
        else (metadata.get("qc_pipeline_version") or metadata.get("processing"))
    )
    identity = {
        "schema": "station-qc-identity-v1",
        "pipeline_version": pipeline,
        "parameters_sha256": metadata.get(
            "xqc_parameter_sha256" if is_x else "qc_parameters_sha256"
        ),
        "implementation_revision": metadata.get(
            "xqc_implementation_revision" if is_x else "qc_implementation_revision"
        ),
        "profile": None if is_x else metadata.get("qc_profile"),
        "mode": metadata.get("xqc_mode") if is_x else None,
        "libraries": None if is_x else deepcopy(metadata.get("qc_libraries")),
        "base_parameters_sha256": metadata.get("xqc_base_parameter_sha256") if is_x else None,
    }
    identity["unreported_fields"] = [
        key
        for key in ("pipeline_version", "parameters_sha256", "implementation_revision")
        if not identity[key]
    ]
    if (
        is_x
        and identity["mode"] in ("audit", "cr_only", "quarantine")
        and not identity["base_parameters_sha256"]
    ):
        identity["unreported_fields"].append("base_parameters_sha256")
    return identity


_RECIPE_KEYS = {
    "pipeline_version",
    "parameters_sha256",
    "implementation_revision",
    "profile",
    "libraries",
}


def _recipe_complete(recipe):
    if recipe is None:
        return False
    if not isinstance(recipe, dict) or set(recipe) - _RECIPE_KEYS:
        raise ValueError("invalid frozen S QC policy recipe")
    for key, value in recipe.items():
        if key == "libraries":
            if (
                not isinstance(value, dict)
                or len(value) > 32
                or any(
                    not isinstance(k, str)
                    or not 1 <= len(k) <= 128
                    or not isinstance(v, str)
                    or not 1 <= len(v) <= 128
                    for k, v in value.items()
                )
            ):
                raise ValueError("invalid libraries in S QC policy")
        elif not isinstance(value, str) or not 1 <= len(value) <= 512:
            raise ValueError("invalid identity in S QC policy")
        elif key == "parameters_sha256" and not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("invalid parameters in S QC policy")
        elif key == "implementation_revision" and not re.fullmatch(
            r"python-source-tree-v1:[a-f0-9]{64}", value
        ):
            raise ValueError("invalid revision in S QC policy")
    return _RECIPE_KEYS <= set(recipe) and bool(recipe.get("libraries"))


def validate_s_qc_policy(payload, network):
    keys = {"requested_s_radars", "s_qc_policy", "s_qc_policy_complete"}
    if not (keys & set(payload)):
        return  # frozen legacy tasks do not acquire a current identity
    if not keys <= set(payload):
        raise ValueError("incomplete S QC policy contract")
    requested, policy = payload["requested_s_radars"], payload["s_qc_policy"]
    if (
        not isinstance(requested, list)
        or not isinstance(policy, dict)
        or len(requested) > 32
        or any(not isinstance(r, str) for r in requested)
        or len(set(requested)) != len(requested)
        or set(policy) != set(requested)
    ):
        raise ValueError("invalid requested stations in S QC policy")
    expected = {
        r
        for r in payload.get("requested_radars", [])
        if r in network.stations and network.stations[r].band == "S"
    }
    if set(requested) != expected:
        raise ValueError("S QC policy differs from requested network stations")
    completeness = [_recipe_complete(recipe) for recipe in policy.values()]
    if type(payload["s_qc_policy_complete"]) is not bool or payload["s_qc_policy_complete"] != all(
        completeness
    ):
        raise ValueError("S QC policy completeness contradicts declared fields")
    for source in payload["sources"]:
        declaration = source.get("expected_qc_identity")
        if declaration is None:
            continue
        if (
            not isinstance(declaration, dict)
            or source["radar_id"] not in policy
            or declaration.get("radar_id") != source["radar_id"]
            or declaration.get("scan_id") != source["scan_id"]
        ):
            raise ValueError("source declaration differs from S QC policy")
        recipe = {k: v for k, v in declaration.items() if k not in ("radar_id", "scan_id")}
        _recipe_complete(recipe)
        if policy[source["radar_id"]] is not None and recipe != policy[source["radar_id"]]:
            raise ValueError("source recipe differs from S QC policy")


def validate_frozen_qc_identity(metadata, source, payload):
    declaration = source.get("expected_qc_identity")
    recipe = payload.get("s_qc_policy", {}).get(source["radar_id"])
    if declaration is None and recipe is None:
        return
    if metadata.get("band") != "S":
        raise ValueError("S QC identity cannot be used for another band")
    actual = source_qc_identity(metadata)
    expected = dict(recipe or {})
    _recipe_complete(expected)
    if declaration is not None:
        if (
            not isinstance(declaration, dict)
            or declaration.get("radar_id") != metadata.get("radar_id")
            or declaration.get("scan_id") != metadata.get("scan_id")
        ):
            raise ValueError("frozen QC identity differs from actual station/scan")
        source_recipe = {k: v for k, v in declaration.items() if k not in ("radar_id", "scan_id")}
        if any(key in expected and expected[key] != value for key, value in source_recipe.items()):
            raise ValueError("source QC identity contradicts frozen window policy")
        expected.update(source_recipe)
    _recipe_complete(expected)
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError("frozen QC identity differs from verified input metadata")
