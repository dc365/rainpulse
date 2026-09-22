import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('configure_admin_ops',ROOT/'scripts/configure_admin_ops.py')
ops=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


def model():
    return {'services':{name:{'image':'rainpulse-cpu-worker:actual-near-config',
      'environment':{'RAINPULSE_RADAR_QC_CONFIG':'/configs/current-near.yaml',
       'RAINPULSE_DIAGNOSTIC_CONFIG':'/configs/current-render.yaml',
       'RAINPULSE_QC_FLAG_DEFINITIONS':'/configs/flags-v2.yaml',
       'RAINPULSE_OBJECT_STORE_SECRET_KEY':'do-not-copy',
       'RAINPULSE_QC_SECRET_TOKEN':'do-not-copy',
       'RAINPULSE_OBJECT_STORE_ENDPOINT':'http://minio:9000'},
      'volumes':[{'type':'bind','source':'/actual/configs','target':'/configs','read_only':True}],
      'ports':[{'published':'8091','target':8091}], 'privileged':True}
      for name in ['radar-qc-worker','analysis-diagnostics-worker']}}


def build(value=None,kinds=None,budget=3):
    return ops.build_override(value or model(),kinds or ['qc','render'],
      {'qc':(1,2048*ops.MIB),'render':(1,1024*ops.MIB),'diagnostics':(1,1024*ops.MIB)},budget,4096*ops.MIB)


def test_preserves_actual_image_configs_and_mounts():
    result=build()
    for name,service in result['services'].items():
        assert service['image']=='rainpulse-cpu-worker:actual-near-config'
        assert service['environment']['RAINPULSE_RADAR_QC_CONFIG']=='/configs/current-near.yaml'
        assert service['volumes'][0]['source']=='/actual/configs'
        assert service['profiles']==['ops']
        assert 'ports' not in service and 'privileged' not in service
        assert service['entrypoint'][2]=='rainpulse_algo.operations.worker'


def test_does_not_embed_any_credentials():
    import json
    output=json.dumps(build())
    assert 'do-not-copy' not in output
    assert '${RAINPULSE_OPS_WORKER_TOKEN:' in output


@pytest.mark.parametrize('budget',[0,1,float('nan'),float('inf')])
def test_budget_overflow_or_nonfinite_rejected(budget):
    with pytest.raises(ValueError):build(budget=budget)


def test_no_guessing_missing_flags():
    value=model();value['services']['radar-qc-worker']['environment'].pop('RAINPULSE_QC_FLAG_DEFINITIONS')
    with pytest.raises(ValueError):build(value)


def test_no_copy_of_docker_socket():
    value=model();value['services']['radar-qc-worker']['volumes'].append({'target':'/var/run/docker.sock'})
    with pytest.raises(ValueError):build(value)


def test_host_network_cannot_silently_duplicate_ports():
    value=model();value['services']['radar-qc-worker']['network_mode']='host'
    with pytest.raises(ValueError):build(value)


def test_output_is_exclusive_and_private(tmp_path):
    path=tmp_path/'config.json';ops.write_new(path,build())
    assert path.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):ops.write_new(path,build())


def test_manifest_paths_cannot_escape(tmp_path):
    with pytest.raises((ValueError,FileNotFoundError)):
        ops.compose_command(tmp_path,{'mode':'unified','schema_version':1,'project_name':'rainpulse','overrides':['../no']},tmp_path/'env')


def test_legacy_deployment_not_guessed(tmp_path):
    with pytest.raises(ValueError):ops.compose_command(tmp_path,{'mode':'legacy','schema_version':1},tmp_path/'env')


def test_operations_helper_targets_only_management_services(tmp_path):
    import importlib.util
    import sys
    scripts = ROOT / 'scripts'
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location('opsctl_test', scripts / 'admin_opsctl.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in module.compose_command.__globals__['BASE']:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('services: {}')
    runtime = tmp_path / 'runtime/deploy'
    runtime.mkdir(parents=True)
    (runtime / 'active-compose.json').write_text(json.dumps({'schema_version':1,'mode':'unified','project_name':'existing','overrides':[]}))
    (runtime / 'ops-workers.compose.json').write_text(json.dumps({'services':{'ops-qc-worker':{}}}))
    command = module.managed_command(tmp_path, tmp_path/'deploy/.env', 'runtime/deploy/ops-workers.compose.json', 'up')
    assert command[-1] == 'ops-qc-worker'
    assert '--no-deps' in command and '--no-build' in command
    assert command[command.index('--pull')+1] == 'never'
    (runtime / 'ops-workers.compose.json').write_text(json.dumps({'services':{'radar-qc-worker':{}}}))
    with pytest.raises(ValueError, match='非管理'):
        module.managed_command(tmp_path, tmp_path/'deploy/.env', 'runtime/deploy/ops-workers.compose.json', 'up')
