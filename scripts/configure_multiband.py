#!/usr/bin/env python3
# ruff: noqa: E501, E701, E702, I001, E402
"""Generate (not start) a bounded S/X management worker from actual deployment.

No CPU/memory expansion, image selection or X calibration is guessed. This file
adds one existing operations kind, not another scheduler/service framework.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from configure_admin_ops import MIB, build_override, compose_command, effective_model, write_new


def multiband_override(model: dict, network_file: Path, image: str, cpu: float, memory: int,
                       available_cpu: float, available_memory: int) -> dict:
    # Import inside the command: tests/importers can inspect CLI without loading
    # geospatial libraries; construction itself validates the real network model.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))
    from rainpulse_algo.multiband.model import Network

    n = Network.load(network_file)
    spatial_station_enabled = any(s.enabled for s in n.stations.values())
    standalone_x_qc_only = not n.products and any(
        s.band == 'X' and s.x_qc_enabled for s in n.stations.values()
    )
    if not spatial_station_enabled and not standalone_x_qc_only:
        raise ValueError('需启用空间核验站，或配置无空间产品的独立X-QC候选网络')
    if not image or len(image) > 256 or any(c.isspace() for c in image) or '$' in image:
        raise ValueError('必须提供已构建新代码的明确镜像身份')
    if not (':' in image or '@sha256:' in image) or image.endswith(':latest'):
        raise ValueError('请提供固定镜像标签或digest，不采用latest')
    # This is an admission budget check, not a prediction of real peak memory.
    tile_bytes = max(
        (min(g.tile_rows,g.height)*g.width*len(g.levels_m_msl)*48 for g in n.products.values()),
        default=0,
    )
    output_bytes = max((g.width*g.height*100 for g in n.products.values()), default=0)
    if memory < 3*n.maximum_input_bytes + n.cache_max_bytes + tile_bytes + output_bytes:
        raise ValueError('声明内存不足以容纳输入副本、解码缓存、分层tile和产物预算；请缩小网格/输入或增加配额')
    base = build_override(model,['qc'],{'qc':(cpu,memory)},available_cpu,available_memory)
    worker = base['services']['ops-qc-worker']
    worker['image'] = image
    worker['entrypoint'] = ['python','-m','rainpulse_algo.operations.worker','--kind','multiband']
    env = worker['environment']
    for name in list(env):
        if name.startswith(('RAINPULSE_RADAR_', 'RAINPULSE_DIAGNOSTIC_', 'RAINPULSE_ANCILLARY_')) or name == 'RAINPULSE_QC_PACKED_STORAGE':
            del env[name]
    env['RAINPULSE_MULTIBAND_CONFIG'] = '/opt/rainpulse/multiband/network.json'
    env['RAINPULSE_MAX_INPUT_ARTIFACT_BYTES'] = str(n.maximum_input_bytes)
    # Two-object native packs / four-object composite bundles already bound
    # object counts. Do not wrap previews in the old QC-specific pack format.
    target = env['RAINPULSE_MULTIBAND_CONFIG']
    volumes = copy.deepcopy(worker.get('volumes',[]))
    if any((v.get('target') == target if isinstance(v,dict) else target in v.split(':')[1:]) for v in volumes):
        raise ValueError('network目标挂载重复')
    volumes.append({'type':'bind','source':str(network_file.resolve(strict=True)), 'target':target, 'read_only':True, 'bind':{'create_host_path':False}})
    worker['volumes'] = volumes
    worker['labels'].update({'rainpulse.managed':'multiband-v1','rainpulse.network-release':n.release_id,'rainpulse.network-sha256':n.sha256})
    return {'services': {'ops-multiband-worker':worker}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--env-file',type=Path,default=Path('deploy/.env'))
    p.add_argument('--network',type=Path,required=True)
    p.add_argument('--image',required=True)
    p.add_argument('--cpus',type=float,required=True)
    p.add_argument('--memory-mib',type=int,required=True)
    p.add_argument('--extra-cpus',type=float,required=True)
    p.add_argument('--extra-memory-mib',type=int,required=True)
    p.add_argument('--output',type=Path,default=Path('runtime/deploy/multiband.compose.json'))
    a=p.parse_args();root=a.root.resolve(strict=True)
    try:
        manifest=json.loads((root/'runtime/deploy/active-compose.json').read_text())
        env=a.env_file if a.env_file.is_absolute() else root/a.env_file
        if not env.is_file():raise ValueError('实际.env不存在')
        network=a.network if a.network.is_absolute() else root/a.network
        effective=effective_model(compose_command(root,manifest,env))
        value=multiband_override(effective,network,a.image,a.cpus,a.memory_mib*MIB,a.extra_cpus,a.extra_memory_mib*MIB)
        output=a.output if a.output.is_absolute() else root/a.output
        write_new(output,value)
    except (ValueError,OSError,KeyError) as e:p.error(str(e))
    print('已生成显式资源受限的S/X管理Worker覆盖文件，未修改或启动服务。')
    print(str(output))


if __name__=='__main__':main()
