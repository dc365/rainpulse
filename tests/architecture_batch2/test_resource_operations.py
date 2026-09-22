"""Fake Docker/SQL responses exercise real deployment code; no server is contacted."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from ops_modules import budget as b, operations as r, ctl
from test_resource_budget import model, spec, compose_resolved, container_fixture


IMAGE = "sha256:" + "a" * 64


class Runner:
    def __init__(self, root):
        self.root = root;self.calls=[];self.host_cpu=16;self.host_memory=16*1024*b.MIB
        self.model=model();self.items=[];self.jobs=0;self.intents=0;self.outbox=0
        self.probes={};self.fail_config=False
    def run(self, argv, *, stdin=None, timeout=60):
        self.calls.append((argv,stdin))
        if argv[:2]==["docker","info"]:return json.dumps({"NCPU":self.host_cpu,"MemTotal":self.host_memory})
        if argv[:3]==["docker","image","inspect"]:return IMAGE
        if argv[:2]==["docker","ps"]:return " ".join(item["Id"] for item in self.items)
        if argv[:2]==["docker","inspect"]:return json.dumps(self.items)
        if argv[:2]==["docker","exec"]:return json.dumps(self.probes[argv[2]])
        if "config" in argv:
            if self.fail_config:raise ctl.OpsError("unsupported Compose configuration flags")
            if "--no-interpolate" in argv:
                assert "--no-env-resolution" in argv
                return json.dumps(self.model)
            files=[Path(argv[i+1]) for i,v in enumerate(argv[:-1]) if v=="-f"]
            current=deepcopy(self.model)
            for file in files:
                if file.name.endswith(".compose.json"):
                    current=compose_resolved(current,json.loads(file.read_text()))
            return json.dumps(current)
        if stdin:
            if "resource_schema_ready" in stdin:return json.dumps({"resource_schema_ready":True})
            if "qc_batch_items" in stdin:return json.dumps({"jobs":0,"outbox":0,"intents":self.intents})
            if "json_build_object" in stdin:return json.dumps({"jobs":self.jobs,"outbox":self.outbox})
            return ""
        raise AssertionError(argv)


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.delenv("RAINPULSE_RELEASE_GATE_FILE",raising=False)
    for file in ctl.BASE_FILES+[ctl.UNIFIED_FILE,"deploy/near.yaml"]:
        path=tmp_path/file;path.parent.mkdir(parents=True,exist_ok=True);path.write_text("services: {}\n")
    runner=Runner(tmp_path);ops=ctl.Operations(tmp_path,runner)
    ops.init(adopt=False,new=True,overrides=["deploy/near.yaml"],project="rainpulse-test",legacy=False,qc_replicas=2)
    budget=tmp_path/"budget.json";budget.write_text(json.dumps(spec()))
    path=tmp_path/"runtime/deploy/resource-test.json"
    plan=r.make_plan(ops,budget,path)
    release={"schema_version":1,"release_id":"test-release","execution_mode":"realtime_shadow",
        "image_id":IMAGE,"qc_config_sha256":"b"*64,"qc_flags_sha256":"c"*64,
        "runtime_sha256":"d"*64,"qc_replicas":2}
    release=r.bind_release_plan(ops,release,path)
    ctl.write_json(ops.gate,{"schema_version":1,"state":"paused","release_id":"test-release"})
    return ops,runner,path,plan,release


def test_plan_is_unactivated_preserves_override_and_binds_both_qc_pools(env):
    ops,runner,path,plan,release=env
    assert "resource_profile" not in ops.manifest()
    assert ops.manifest()["overrides"]==["deploy/near.yaml"]
    assert release["qc_services"]=={"radar-qc-worker":2,"radar-qc-background-worker":1}
    assert release["qc_replicas"]==3
    ops.validate_plan(release)
    assert r.load_plan(ops,path)==plan
    assert any("--no-interpolate" in a for a,_ in runner.calls)
    assert not any("up" in a for a,_ in runner.calls)
    assert not any("secret-value" in f.read_text() for f in path.parent.iterdir())


def test_resource_plan_refuses_unsafe_host_capacity(env):
    ops,runner,path,_,_=env;runner.host_cpu=1
    budget=ops.root/"budget.json"
    new=path.with_name("too-large.json")
    with pytest.raises(b.ResourceError,match="host capacity"):r.make_plan(ops,budget,new)
    assert not new.exists() and not new.with_suffix(".compose.json").exists()


def test_no_fallback_when_uninterpolated_compose_unavailable(env):
    ops,runner,path,_,_=env;runner.fail_config=True
    with pytest.raises(ctl.OpsError):r.make_plan(ops,ops.root/"budget.json",path.with_name("failed.json"))
    assert "--no-interpolate" in runner.calls[-1][0]


def test_resource_registration_requires_global_drain_and_owned_pause(env):
    ops,runner,path,_,release=env
    runner.jobs=1
    with pytest.raises(b.ResourceError,match="drain"):r.register_plan(ops,path,release)
    assert "resource_profile" not in ops.manifest()
    runner.jobs=0; runner.outbox=1
    with pytest.raises(b.ResourceError,match="drain"):r.register_plan(ops,path,release)
    runner.outbox=0;runner.intents=1
    with pytest.raises(b.ResourceError,match="intents"):r.register_plan(ops,path,release)
    runner.intents=0
    r.register_plan(ops,path,release)
    assert ops.manifest()["replicas"]["radar-qc-background-worker"]==1
    assert r.validate_active(ops,ops.manifest()) is not None
    assert ops.gate.exists()
    assert not any("up" in argv for argv,_ in runner.calls)


def test_registration_rejects_other_release_and_inventory_changes(env):
    ops,_,path,_,release=env
    bad=deepcopy(release);bad["resource_profile_sha256"]="e"*64
    with pytest.raises(b.ResourceError,match="bind"):r.register_plan(ops,path,bad)
    bad=deepcopy(release);bad["release_id"]="other"
    with pytest.raises(ctl.OpsError,match="own"):r.register_plan(ops,path,bad)
    manifest=ops.manifest();manifest["replicas"]["radar-qc-worker"]=4;ctl.write_json(ops.manifest_path,manifest)
    with pytest.raises(b.ResourceError,match="inventory"):r.register_plan(ops,path,release)


@pytest.mark.parametrize("target",["source","overlay","plan"])
def test_mutated_frozen_source_or_plan_cannot_activate(env,target):
    ops,_,path,plan,release=env
    changed={"source":ops.root/"deploy/near.yaml","overlay":ops.root/plan["overlay"],"plan":path}[target]
    if target=="plan":
        data=json.loads(changed.read_text());data["image_ids"]={};changed.write_text(json.dumps(data))
    else:changed.write_text(changed.read_text()+"\n# changed")
    with pytest.raises((b.ResourceError,ValueError)):r.register_plan(ops,path,release)
    assert ops.gate.exists() and "resource_profile" not in ops.manifest()


def live_inventory(ops,runner,plan):
    for name,pool in plan["summary"]["pools"].items():
        for i in range(pool["replicas"]):
            item=container_fixture(pool,IMAGE)
            item["Id"]=name+str(i)
            item["HostConfig"]["CpuShares"]=128 if pool["lane"]=="background" else 1024
            item["HostConfig"]["PidsLimit"]=128 if pool["lane"]=="background" else 256
            item["Config"]["Labels"]={"com.docker.compose.project":"rainpulse-test","com.docker.compose.service":name}
            item["Config"]["Env"] = [k+"="+v for k,v in b.resource_environment(pool,pool["lane"]).items()]
            item["Config"]["Env"].append("RAINPULSE_WORKER_PROFILE="+pool["profile"])
            runner.items.append(item)
            subject="rainpulse.jobs.requested."+("background." if pool["lane"]=="background" else "")+"radar_qc"
            consumer="qc"+("-background" if pool["lane"]=="background" else "")
            runner.probes[item["Id"]]={"pending":0,"ack_pending":0,"subject":subject,"consumer":consumer,"filter_subject":subject,"max_ack_pending":pool["max_ack_pending"],
                "health":{"status":"ready","subject":subject,"consumer":consumer,
                    "resources":{"schema_version":1,"lane":pool["lane"],"native_threads":pool["native_threads"]},
                    "asset_cache":{"limit_bytes":pool["cache_mib"]*b.MIB,"limit_inflight":pool["read_concurrency"]}}}


def test_running_resources_require_budget_and_resolved_native_flag(env,monkeypatch):
    ops,runner,path,plan,release=env
    r.register_plan(ops,path,release);live_inventory(ops,runner,plan)
    monkeypatch.setattr(ops,"planner_identity",lambda:{"resource_routing":{"schema_version":1,"enabled":False}})
    assert r.check_resources(ops,expect_routing="disabled")["verified_containers"]==3
    with pytest.raises(b.ResourceError,match="mode differs"):r.check_resources(ops)
    monkeypatch.setattr(ops,"planner_identity",lambda:{"resource_routing":{"schema_version":1,"enabled":True}})
    result=r.check_resources(ops)
    assert result["verified_containers"]==3 and result["cpu_limit_total"]==5
    with pytest.raises(ctl.OpsError,match="resources-plan"):ops.set_overrides(["deploy/near.yaml"])


@pytest.mark.parametrize("failure",["extra","missing","image","cpu","memory","cache","nats_filter","stopped","pending_limit","native_threads"])
def test_actual_resource_drift_fails_closed(env,monkeypatch,failure):
    ops,runner,path,plan,release=env
    r.register_plan(ops,path,release);live_inventory(ops,runner,plan)
    monkeypatch.setattr(ops,"planner_identity",lambda:{"resource_routing":{"schema_version":1,"enabled":True}})
    item=runner.items[-1];health=runner.probes[item["Id"]]["health"]
    if failure=="extra":
        other=deepcopy(item);other["Config"]["Labels"]["com.docker.compose.service"]="unbudgeted-worker";runner.items.append(other)
    elif failure=="missing":runner.items.pop()
    elif failure=="image":item["Image"]="sha256:"+"0"*64
    elif failure=="cpu":item["HostConfig"]["NanoCpus"]=0
    elif failure=="memory":item["HostConfig"]["Memory"]=0
    elif failure=="cache":health["asset_cache"]["limit_bytes"]=0
    elif failure=="nats_filter":runner.probes[item["Id"]]["filter_subject"]="wrong"
    elif failure=="stopped":item["State"]["Running"]=False
    elif failure=="pending_limit":runner.probes[item["Id"]]["max_ack_pending"]=999
    else:health["resources"]["native_threads"]=99
    with pytest.raises(b.ResourceError):r.check_resources(ops)
    assert ops.gate.exists()


def test_active_manifest_drifts_are_detected_before_compose_command(env):
    ops,_,path,_,release=env;r.register_plan(ops,path,release)
    current=ops.manifest();current["replicas"]["radar-qc-background-worker"]=2
    ctl.write_json(ops.manifest_path,current)
    with pytest.raises(b.ResourceError,match="inventory differs"):ops.compose("up")


def test_rainpulsectl_has_all_resource_commands(capsys):
    with pytest.raises(SystemExit) as done:ctl.main(["--help"])
    assert done.value.code==0
    help=capsys.readouterr().out
    for name in ["resources-template","resources-plan","resources-schema","resources-register","resources-check"]:assert name in help
