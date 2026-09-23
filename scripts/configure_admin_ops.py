#!/usr/bin/env python3
"""Generate an explicit, resource-bounded management-worker override.

Reads the adopted deployment manifest and effective Compose model. Never alters
active manifests, credentials, running containers or source algorithm profiles.
JSON output is valid Compose YAML; values containing secrets are parameterized.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
from pathlib import Path

BASE = ['deploy/docker-compose.yaml', 'deploy/docker-compose.realtime-shadow.yaml',
        'deploy/docker-compose.unified.yaml']
KIND_SERVICE = {'qc': 'radar-qc-worker', 'render': 'analysis-diagnostics-worker',
                'diagnostics': 'analysis-diagnostics-worker'}
MIB = 1024**2


def relative_file(root: Path, name: str) -> Path:
    target = (root / name).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('部署配置必须是仓库内存在的普通文件')
    return target


def compose_command(root: Path, manifest: dict, env_file: Path) -> list[str]:
    if manifest.get('schema_version') != 1 or manifest.get('mode') != 'unified':
        raise ValueError('首版仅支持已登记的 unified 部署，不能猜测旧部署')
    project = manifest.get('project_name', '')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}', project):
        raise ValueError('Compose项目名非法')
    overrides = manifest.get('overrides')
    if not isinstance(overrides, list) or len(overrides)>64:
        raise ValueError('缺少实际覆盖文件清单')
    files = BASE + overrides
    if len(set(files)) != len(files):
        raise ValueError('覆盖文件重复')
    command = ['docker','compose','--project-name',project,'--env-file',str(env_file)]
    for name in files:
        command += ['-f', str(relative_file(root, name))]
    return command


def effective_model(command: list[str]) -> dict:
    # stderr may contain interpolation values: never echo it to the console.
    result = subprocess.run(command+['config','--format','json'],capture_output=True,check=False,timeout=45)
    if result.returncode or len(result.stdout)>16*1024**2:
        raise ValueError('Compose配置解析失败，请在本机检查实际部署配置与.env')
    value=json.loads(result.stdout)
    if not isinstance(value.get('services'),dict):raise ValueError('Compose没有服务清单')
    return value


def build_override(model: dict, kinds: list[str], budgets: dict[str,tuple[float,int]],
                   available_cpu: float, available_memory: int) -> dict:
    if not kinds or len(set(kinds))!=len(kinds) or any(k not in KIND_SERVICE for k in kinds):
        raise ValueError('执行器类型非法或重复')
    if not math.isfinite(available_cpu) or available_cpu<=0 or available_memory<=0:
        raise ValueError('必须声明本机评估后的额外CPU和内存预算')
    if sum(budgets[k][0] for k in kinds)>available_cpu or sum(budgets[k][1] for k in kinds)>available_memory:
        raise ValueError('新增Worker总配额超过声明的额外预算')
    result={'services':{}}
    for kind in kinds:
        cpu,memory=budgets[kind]
        if not .25<=cpu<=128 or not 256*MIB<=memory<=1024**4:
            raise ValueError('单Worker资源配额非法')
        source=model['services'].get(KIND_SERVICE[kind])
        if not isinstance(source,dict) or not source.get('image'):
            raise ValueError('未找到现行QC/诊断服务镜像，不采用仓库默认实验版本')
        if source.get('network_mode')=='host':
            raise ValueError('host网络需单独规划健康端口，生成器拒绝复制冲突端口')
        env={}
        for key,value in source.get('environment',{}).items():
            if key.startswith(('RAINPULSE_RADAR_','RAINPULSE_QC_','RAINPULSE_DIAGNOSTIC_',
                               'RAINPULSE_ANCILLARY_','RAINPULSE_ARTIFACT_CACHE_')) or key in {
                'RAINPULSE_OBJECT_STORE_ENDPOINT','RAINPULSE_OBJECT_STORE_BUCKET',
                'RAINPULSE_OBJECT_STORE_MAX_WORKERS','RAINPULSE_MAX_INPUT_ARTIFACT_BYTES'}:
                text=str(value or '')
                if re.search(r'PASSWORD|TOKEN|SECRET|ACCESS_KEY|API_KEY',key):continue
                if re.search(r'://[^/@\s]+:[^/@\s]+@',text):
                    raise ValueError('配置含嵌入式凭据；请改为独立秘密配置，不写入生成文件')
                env[key]=text.replace('$','$$')
        if not env.get('RAINPULSE_QC_FLAG_DEFINITIONS'):
            raise ValueError('现行服务未显式挂载标志表，不能猜测新旧flag版本')
        if kind=='qc' and not env.get('RAINPULSE_RADAR_QC_CONFIG'):
            raise ValueError('现行QC配置路径为空')
        if kind=='diagnostics' and not env.get('RAINPULSE_DIAGNOSTIC_CONFIG'):
            raise ValueError('现行诊断配置路径为空')
        # Storage layout only: QC bytes/digests/algorithms remain unchanged.
        # Respect explicit operator 0; renders/diagnostics keep individual PNGs.
        if kind == 'qc':
            env.setdefault('RAINPULSE_QC_PACKED_STORAGE', '1')
        env.update({
            'RAINPULSE_OPS_CONTROL_URL':'${RAINPULSE_OPS_CONTROL_URL:?set native control URL}',
            'RAINPULSE_OPS_WORKER_TOKEN':'${RAINPULSE_OPS_WORKER_TOKEN:?set dedicated worker credential}',
            'RAINPULSE_NATS_URL':'${RAINPULSE_OPS_NATS_URL:?set existing NATS URL}',
            'RAINPULSE_OBJECT_STORE_ACCESS_KEY':'${RAINPULSE_MINIO_WORKER_ACCESS_KEY:?required}',
            'RAINPULSE_OBJECT_STORE_SECRET_KEY':'${RAINPULSE_MINIO_WORKER_SECRET_KEY:?required}',
            'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
            'NUMEXPR_NUM_THREADS':'1','RAINPULSE_OPS_HEALTH_PORT':'8095','TZ':'UTC'})
        worker={k:copy.deepcopy(source[k]) for k in ['volumes','networks','extra_hosts','user','group_add'] if k in source}
        # No ports, privileged mode, host PID namespace, Docker socket or devices copied.
        for mount in worker.get('volumes',[]):
            target=mount.get('target','') if isinstance(mount,dict) else str(mount)
            if 'docker.sock' in target:raise ValueError('拒绝给计算Worker挂载Docker socket')
        if 'extra_hosts' not in worker:worker['extra_hosts']=['host.docker.internal:host-gateway']
        worker.update({'image':source['image'],'profiles':['ops'],
            'entrypoint':['python','-m','rainpulse_algo.operations.worker','--kind',kind],
            'environment':env,'cpus':str(cpu),'mem_limit':memory,'memswap_limit':memory,
            'pids_limit':256,'restart':'unless-stopped','stop_grace_period':'120s',
            'labels':{'rainpulse.managed':'operations-v1','rainpulse.candidate-only':'true'},
            'healthcheck':{'test':['CMD','python','-c',
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/healthz', timeout=2)"],
                'interval':'10s','timeout':'3s','retries':6,'start_period':'30s'}})
        result['services']['ops-'+kind+'-worker']=worker
    return result


def write_new(path:Path,value:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:
        os.chmod(path,0o600)
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--env-file',type=Path,default=Path('deploy/.env'))
    p.add_argument('--output',type=Path,default=Path('runtime/deploy/ops-workers.compose.json'))
    p.add_argument('--kinds',default='qc,render')
    p.add_argument('--extra-cpus',type=float,required=True)
    p.add_argument('--extra-memory-mib',type=int,required=True)
    for kind in KIND_SERVICE:
        p.add_argument('--'+kind+'-cpus',type=float,default=1)
        p.add_argument('--'+kind+'-memory-mib',type=int)
    args=p.parse_args();root=args.root.resolve(strict=True)
    manifest=json.loads((root/'runtime/deploy/active-compose.json').read_text())
    env=args.env_file if args.env_file.is_absolute() else root/args.env_file
    if not env.is_file():p.error('实际.env不存在')
    kinds=args.kinds.split(',');budgets={}
    for kind in kinds:
        if kind not in KIND_SERVICE:p.error('未知Worker类型')
        memory=getattr(args,kind+'_memory_mib')
        if memory is None:p.error('每个选中的Worker必须明确内存配额')
        budgets[kind]=(getattr(args,kind+'_cpus'),memory*MIB)
    try:
        model=effective_model(compose_command(root,manifest,env))
        value=build_override(model,kinds,budgets,args.extra_cpus,args.extra_memory_mib*MIB)
        output=args.output if args.output.is_absolute() else root/args.output
        write_new(output,value)
    except (ValueError,OSError,subprocess.TimeoutExpired) as e:p.error(str(e))
    print('已生成管理Worker覆盖文件。未写入凭据、未改active-compose清单、未启动或替换任何服务。')
    print(str(output))

if __name__=='__main__':main()
