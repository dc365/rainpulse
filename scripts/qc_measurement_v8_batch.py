#!/usr/bin/env python3
"""Sequential, resumable real-data V8 extraction; never publishes online products.

Run inside the measurement image with object-store environment. Input is a JSON
list of scan_id, radar_id, normalized_uri and qc_uri from radar_scan_runs.
"""
import argparse
import json
import shutil
import traceback
from pathlib import Path
from types import SimpleNamespace

from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment
from rainpulse_algo.radar.qc_engine.measurement_v8.cli import freeze
from rainpulse_algo.radar.qc_engine.measurement_v8.io import file_hash
from rainpulse_algo.radar.qc_engine.measurement_v8.schema import load_config
from rainpulse_algo.radar.qc_engine.measurement_v8.workbench import extract_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = json.loads(Path(args.manifest).read_text())
    reader = ArtifactObjectReader(minio_client_from_environment())
    base = Path('/opt/rainpulse-v8')
    cfg = load_config(base / 'configs/qc-experiments/measurement-v8.yaml')
    binary = '/usr/local/bin/emitter-core'
    results = []

    def status(stage, row=None):
        record = dict(stage=stage, current=row, completed=len(results), total=len(rows), results=results)
        tmp = output / 'progress.tmp'
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        tmp.replace(output / 'progress.json')
        print(stage, row and row['scan_id'], flush=True)

    for row in rows:
        # UUID validation prevents manifest-controlled output traversal.
        from uuid import UUID
        root = output / str(UUID(row['scan_id']))
        root.mkdir(exist_ok=True)
        try:
            if not (root / 'features').exists():
                status('downloading', row)
                for key, uri in [('normalized', row['normalized_uri']), ('qc', row['qc_uri'])]:
                    dest = root / (key + '.zarr')
                    if not (dest / '.download-complete').exists():
                        dest.mkdir(exist_ok=True)
                        for name, data in reader.load(uri).items():
                            target = (dest / name).resolve()
                            if not target.is_relative_to(dest):
                                raise ValueError('unsafe object path')
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(data)
                        (dest / '.download-complete').touch()
                for source, name in [('fujian-qc-evidence-graph-v7.yaml', 'profile.yaml'), ('flag-definitions-v2.yaml', 'flags.yaml')]:
                    shutil.copyfile(base / 'configs/qc' / source, root / name)
                if not (root / 'case.json').exists():
                    freeze(SimpleNamespace(root=str(root), normalized='normalized.zarr', qc='qc.zarr', profile='profile.yaml', flags='flags.yaml', context=None, case_id=str(UUID(row['scan_id'])), process='fujian-20260828', partition='inspect', data_kind='real', sweeps='sweep_000', name='case.json'))
                status('extracting', row)
                extract_case(root / 'case.json', cfg, root / 'features', binary=binary, binary_sha=file_hash(binary))
            results.append(dict(scan_id=row['scan_id'], status='completed'))
        except Exception as exc:
            (root / 'error.log').write_text(traceback.format_exc())
            results.append(dict(scan_id=row['scan_id'], status='failed', error=str(exc)))
        status('next')
    status('finished')


if __name__ == '__main__':
    main()
