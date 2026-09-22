#!/usr/bin/env python3
"""Operate only explicit ops-* workers; do not touch realtime services or manifests."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from configure_admin_ops import compose_command, effective_model, relative_file


def managed_command(root: Path, env: Path, override: str, command: str) -> list[str]:
    manifest = json.loads((root / 'runtime/deploy/active-compose.json').read_text())
    base = compose_command(root, manifest, env)
    file = relative_file(root, override)
    value = json.loads(file.read_text())
    services = value.get('services', {})
    allowed = {'ops-qc-worker', 'ops-render-worker', 'ops-diagnostics-worker'}
    if not services or set(services) - allowed:
        raise ValueError('覆盖文件包含非管理服务；拒绝操作')
    # Never permit the helper to pass a user-supplied raw Compose command.
    base += ['-f', str(file), '--profile', 'ops']
    if command == 'check':
        resolved = effective_model(base)
        for name in services:
            worker = resolved['services'].get(name, {})
            if worker.get('labels', {}).get('rainpulse.managed') != 'operations-v1':
                raise ValueError('管理Worker标签不匹配')
        return []
    actions = {
        'up': ['up', '-d', '--no-build', '--pull', 'never', '--no-deps', '--wait'],
        'status': ['ps'],
        'stop': ['stop', '--timeout', '120'],
    }
    if command not in actions:
        raise ValueError('未知管理命令')
    return base + actions[command] + sorted(services)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['check', 'up', 'status', 'stop'])
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--env-file', type=Path, default=Path('deploy/.env'))
    p.add_argument('--override', default='runtime/deploy/ops-workers.compose.json')
    args = p.parse_args()
    root = args.root.resolve(strict=True)
    env = args.env_file if args.env_file.is_absolute() else root / args.env_file
    try:
        command = managed_command(root, env, args.override, args.command)
        if command:
            result = subprocess.run(command, cwd=root, check=False)
            if result.returncode:
                raise SystemExit(result.returncode)
        else:
            print('管理Worker组合检查通过。未启动服务，未更改实际部署清单。')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        p.error(str(error))


if __name__ == '__main__':
    main()
