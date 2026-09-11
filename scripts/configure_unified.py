#!/usr/bin/env python3
"""Prepare the host service from existing Compose configuration, without starting it.

Run under sudo. Secrets stay in /etc/rainpulse/control.env (0600), never stdout.
Existing runtime mounts/data are reused. Does not stop/start/delete services.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import grp
import subprocess


def quote(value):
    value = str(value)
    if '\n' in value or '\r' in value or '\x00' in value:
        raise ValueError('multiline service setting is not supported')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def configure(root, user, envfile=None):
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo; no sudo password is recorded.')
    pwd.getpwnam(user)
    try:
        data_group = grp.getgrgid(65532).gr_name
    except KeyError:
        subprocess.run(['groupadd', '--system', '--gid', '65532', 'rainpulse-data'], check=True)
        data_group = grp.getgrgid(65532).gr_name
    binary = root / '.build/linux-amd64/rainpulse'
    if not binary.is_file():
        raise SystemExit('Build the unified Linux binary first.')
    command = ['docker', 'compose', '--profile', '*', '--env-file', str(envfile or root / 'deploy/.env'),
               '-f', str(root / 'deploy/docker-compose.yaml'),
               '-f', str(root / 'deploy/docker-compose.realtime-shadow.yaml')]
    config = json.loads(subprocess.check_output(command + ['config', '--format', 'json']))
    services = config['services']
    environment = {}
    mounts = {}
    for name in ['api', 'web', 'orchestrator', 'radar-ingest']:
        service = services[name]
        environment.update(service.get('environment', {}))
        for mount in service.get('volumes', []):
            source = mount['source']
            if mount['type'] == 'volume':
                volume = config['volumes'][source]['name']
                subprocess.run(['docker', 'volume', 'create', volume], check=True, stdout=subprocess.DEVNULL)
                source = json.loads(subprocess.check_output(['docker', 'volume', 'inspect', volume]))[0]['Mountpoint']
            target = mount['target']
            if target in mounts and mounts[target][0] != source:
                raise ValueError('conflicting runtime mount: ' + target)
            mounts[target] = (source, bool(mount.get('read_only', False)))

    def port(name, target):
        for item in services[name]['ports']:
            if int(item['target']) == target:
                return str(item['published'])
        raise ValueError('missing published port for ' + name)

    environment.update({
        'RAINPULSE_DATABASE_HOST': '127.0.0.1',
        'RAINPULSE_DATABASE_PORT': port('postgres', 5432),
        'RAINPULSE_OBJECT_STORE_ENDPOINT': 'http://127.0.0.1:' + port('minio', 9000),
        'RAINPULSE_WEB_ADDR': ':4173',
        'RAINPULSE_WEB_ROOT': str(root / 'apps/web/dist'),
        'RAINPULSE_INGEST_HEALTH_ADDR': '127.0.0.1:8092',
        'RAINPULSE_ORCHESTRATOR_HEALTH_ADDR': '127.0.0.1:8090',
        'RAINPULSE_INGEST_STATUS_URL': 'http://127.0.0.1:8092/status',
        'RAINPULSE_NOWCASTNET_SHADOW_STATUS_URL': 'http://127.0.0.1:18094/status',
        'RAINPULSE_INTERVAL_WORKER_URL': 'http://127.0.0.1:18091',
        'RAINPULSE_PROMETHEUS_URL': 'http://127.0.0.1:' + port('prometheus',9090),
        'RAINPULSE_ALERTMANAGER_URL': 'http://127.0.0.1:' + port('alertmanager',9093),
    })
    environment['RAINPULSE_NATS_URL'] = environment['RAINPULSE_NATS_URL'].replace('@nats:4222', '@127.0.0.1:' + port('nats',4222))
    if '@nats:' in environment['RAINPULSE_NATS_URL']:
        raise ValueError('unresolved NATS address')
    settings = Path('/etc/rainpulse')
    settings.mkdir(mode=0o700, exist_ok=True)
    envfile = settings / 'control.env'
    # Restrictive permissions are applied before writing credentials.
    fd = os.open(envfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        for key, value in sorted(environment.items()):
            stream.write(key + '=' + quote(value) + '\n')
    lines = ['[Unit]', 'Description=RainPulse unified Go control service',
             'After=network-online.target docker.service', 'Wants=network-online.target',
             '[Service]', 'Type=simple', 'User=' + user, 'Group=' + pwd.getpwnam(user).pw_name,
             'SupplementaryGroups=' + data_group,
             'WorkingDirectory=' + str(root).replace('%','%%'),
             'EnvironmentFile=/etc/rainpulse/control.env',
             'ExecStart=' + quote(str(binary).replace('%','%%')),
             'Restart=on-failure', 'RestartSec=5', 'TimeoutStopSec=60',
             'NoNewPrivileges=true', 'PrivateTmp=true']
    for target, (source, readonly) in sorted(mounts.items()):
        if source == target:
            continue
        if not Path(source).exists():
            raise ValueError('runtime mount is missing: ' + target)
        if any(ch.isspace() for ch in source + target):
            raise ValueError('runtime mount paths must not contain whitespace')
        lines.append(('BindReadOnlyPaths=' if readonly else 'BindPaths=') + (source + ':' + target).replace('%','%%'))
        if not readonly:
            # Preserve UID and data, grant the old container group access to the
            # host service. Both old UID 65532 and the new service can roll back.
            for parent, directories, files in os.walk(source):
                for path in [Path(parent)] + [Path(parent)/name for name in files]:
                    if path.is_symlink():
                        continue
                    os.chown(path, -1, 65532)
                    os.chmod(path, path.stat().st_mode | (0o070 if path.is_dir() else 0o060))
    lines.extend(['[Install]', 'WantedBy=multi-user.target', ''])
    Path('/etc/systemd/system/rainpulse.service').write_text('\n'.join(lines))
    subprocess.run(['systemd-analyze', 'verify', '/etc/systemd/system/rainpulse.service'],check=True)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('Prepared rainpulse.service; no services started or stopped.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    configure(args.root.resolve(strict=True), args.user, args.env_file)
