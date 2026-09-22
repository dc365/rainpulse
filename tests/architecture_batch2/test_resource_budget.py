from copy import deepcopy
import json
import pytest
from ops_modules import budget as b


def model():
    return {"services": {"radar-qc-worker": {
        "image": "rainpulse:near-joint-frozen", "entrypoint": ["python", "-m", "rainpulse_algo.worker"],
        "environment": {"RAINPULSE_WORKER_PROFILE": "radar-qc-basic", "RAINPULSE_RADAR_QC_CONFIG": "/opt/rainpulse/configs/qc/near-on.yaml",
                        "RAINPULSE_OBJECT_STORE_SECRET_KEY": "${RAINPULSE_MINIO_WORKER_SECRET_KEY:?required}",
                        "RAINPULSE_NATS_URL": "nats://nats:4222"},
        "volumes": [{"type":"bind", "source":"/deployment/configs", "target":"/opt/rainpulse/configs", "read_only":True}],
        "user": "65532:65532", "ports": [{"target":8091,"published":"18091"}],
        "networks": {"default": {"aliases":["qc"], "ipv4_address":"192.0.2.7"}},
        "labels": {}, "restart":"unless-stopped",
    }, "gpu": {"environment":{"RAINPULSE_WORKER_PROFILE":"nowcastnet-shadow"}}}}


def spec():
    return {"schema_version":1,"cpu_budget":8,"memory_budget_mib":8192,"services":{
        "radar-qc-worker":{
            "realtime":{"replicas":2,"cpus":2,"memory_mib":2048},
            "background":{"replicas":1,"cpus":1,"memory_mib":1024},
        }}}


def compose_resolved(source, overlay):
    """Test fixture merge, not a replacement for real docker compose config."""
    result=deepcopy(source)
    for name, values in overlay["services"].items():
        target=result["services"].setdefault(name,{})
        env={**target.get("environment",{}),**values.get("environment",{})}
        target.update(deepcopy(values)); target["environment"]=env
    return result


def test_preserves_near_profile_images_mounts_and_removes_bg_port_alias():
    original=model();before=deepcopy(original)
    overlay,summary=b.build_overlay(original,spec(),{"radar-qc-worker":2})
    clone=overlay["services"]["radar-qc-background-worker"]
    assert clone["image"] == before["services"]["radar-qc-worker"]["image"]
    assert clone["environment"]["RAINPULSE_RADAR_QC_CONFIG"].endswith("near-on.yaml")
    assert clone["volumes"] == before["services"]["radar-qc-worker"]["volumes"]
    assert "ports" not in clone and "aliases" not in clone["networks"]["default"]
    assert "ipv4_address" not in clone["networks"]["default"]
    assert original==before
    assert summary["cpu_total"]==5 and summary["memory_total_mib"]==5120
    assert summary["replicas"]["radar-qc-worker"]==2
    assert "${RAINPULSE_MINIO_WORKER_SECRET_KEY" in json.dumps(overlay)
    b.validate_resolved_pairs(compose_resolved(original,overlay),summary)


@pytest.mark.parametrize("field,value",[("cpu_budget",4),("memory_budget_mib",4096),("cpu_budget",0),("cpu_budget",True),("cpu_budget",float("nan")),("memory_budget_mib",None),("schema_version",2)])
def test_invalid_aggregate_budget(field,value):
    s=spec();s[field]=value
    with pytest.raises(b.ResourceError):b.build_overlay(model(),s,{"radar-qc-worker":2})


@pytest.mark.parametrize("field,value",[("cpus",0),("cpus",float("inf")),("cpus",True),("memory_mib",0),("cache_mib",300),("read_concurrency",0),("replicas",0),("native_threads",3),("max_ack_pending",1),("unknown",1)])
def test_invalid_pool_budget(field,value):
    s=spec();s["services"]["radar-qc-worker"]["realtime"][field]=value
    with pytest.raises(b.ResourceError):b.build_overlay(model(),s,{"radar-qc-worker":2})


def test_no_cpu_service_silently_unbudgeted():
    m=model();m["services"]["analysis-qpe-worker"]=deepcopy(m["services"]["radar-qc-worker"])
    m["services"]["analysis-qpe-worker"]["environment"]["RAINPULSE_WORKER_PROFILE"]="analysis-qpe-basic"
    with pytest.raises(b.ResourceError,match="every active"):b.build_overlay(m,spec(),{})


@pytest.mark.parametrize("secret",["a-real-password",42,"prefix-${SECRET}","http://user:secret@service"])
def test_expanded_credentials_never_saved(secret):
    m=model();m["services"]["radar-qc-worker"]["environment"]["RAINPULSE_OBJECT_STORE_SECRET_KEY"]=secret
    with pytest.raises(b.ResourceError,match="literal"):b.build_overlay(m,spec(),{})


@pytest.mark.parametrize("key,value",[("network_mode","host"),("container_name","pinned"),("gpus","all"),("devices",["/dev/x"]),("cpuset","0-3")])
def test_unsupported_host_device_topology_rejects(key,value):
    m=model();m["services"]["radar-qc-worker"][key]=value
    with pytest.raises(b.ResourceError):b.build_overlay(m,spec(),{})


def test_profile_disabled_excluded_but_explicit_replica_included():
    m=model();m["services"]["extra-worker"]=deepcopy(m["services"]["radar-qc-worker"])
    m["services"]["extra-worker"]["profiles"]=["optional"]
    assert "extra-worker" not in b.worker_services(m,{})
    assert "extra-worker" in b.worker_services(m,{"extra-worker":1})


@pytest.mark.parametrize("drift",["image","volumes","algorithm","ports","cpus","mem_limit","entrypoint"])
def test_resolved_configuration_drift_rejected(drift):
    m=model();overlay,summary=b.build_overlay(m,spec(),{})
    resolved=compose_resolved(m,overlay);bg=resolved["services"]["radar-qc-background-worker"]
    if drift=="algorithm":bg["environment"]["RAINPULSE_RADAR_QC_CONFIG"]="/wrong.yaml"
    else:bg[drift]={"image":"wrong","volumes":[],"ports":[8091],"cpus":8,"mem_limit":0,"entrypoint":["wrong"]}[drift]
    with pytest.raises(b.ResourceError):b.validate_resolved_pairs(resolved,summary)


def container_fixture(pool,image):
    return {"State":{"Running":True},"Image":image,"HostConfig":{
        "NanoCpus":int(pool["cpus"]*1e9),"Memory":pool["memory_mib"]*b.MIB,
        "CpuShares":128,"PidsLimit":128},
        "Config":{"Env":[k+"="+v for k,v in b.resource_environment(pool,"background").items()]}}


def test_actual_cgroup_quota_memory_weight_process_limit_checked():
    _,summary=b.build_overlay(model(),spec(),{})
    pool=summary["pools"]["radar-qc-background-worker"]
    item=container_fixture(pool,"sha256:x")
    b.validate_container(item,pool,"sha256:x")
    item["HostConfig"].update(NanoCpus=0,CpuPeriod=100000,CpuQuota=100000)
    b.validate_container(item,pool,"sha256:x")
    for key in ["Memory","CpuQuota","CpuShares","PidsLimit"]:
        damaged=deepcopy(item);damaged["HostConfig"][key]=0
        with pytest.raises(b.ResourceError):b.validate_container(damaged,pool,"sha256:x")


def test_template_requires_explicit_unknown_host_budget():
    result=b.template(model(),{"radar-qc-worker":4})
    assert result["cpu_budget"]==0
    assert result["services"]["radar-qc-worker"]["realtime"]["replicas"]==4
    with pytest.raises(b.ResourceError):b.build_overlay(model(),result,{})


def test_budget_json_schema_matches_valid_examples():
    from pathlib import Path
    import jsonschema
    schema=json.loads((Path(__file__).resolve().parents[2]/"contracts/schemas/resource-budget-v1.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(spec(),schema)
    invalid=spec();invalid["services"]["radar-qc-worker"]["background"]["cpus"]=0
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(invalid,schema)
