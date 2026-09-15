"""Offline checked five-case or equivalent manifest execution. Atomic new output only."""
from __future__ import annotations
import argparse
import gc
from dataclasses import asdict
import gzip
import json
from pathlib import Path
import shutil
import tempfile
import time
from .config import Config, Policy, load_settings, settings_dict
from .data import RawScan
from .engine import infer
from .io import read_case, sha_file, write_json, write_npz, clean
from .policy import baseline_from_npz, apply_policy
from .report import statistics, gate_rows, write_csv, plots, html_index


def _write_gzip_json(path, d):
    with open(path, 'wb') as f:
        with gzip.GzipFile(fileobj=f, filename='', mode='wb', mtime=0) as z:
            z.write(json.dumps(clean(d), sort_keys=True, ensure_ascii=False, allow_nan=False).encode())


def run_manifest(input_root, output, model, policy, *, plot=False):
    input_root, output = Path(input_root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("output exists; refuse overwrite")
    if output == input_root or input_root in output.parents:
        raise ValueError("write outside immutable input directory")
    manifest = json.loads((input_root/'manifest.json').read_text())
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("nonempty input manifest list required")
    if len({x['case'] for x in manifest}) != len(manifest):
        raise ValueError("duplicate case identity")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='.oc1-', dir=output.parent))
    summaries = []
    try:
        write_json(tmp/'settings.json', settings_dict(model, policy))
        for item in manifest:
            read_t = time.perf_counter()
            d = read_case(input_root, item)
            raw = RawScan.from_arrays(d, full_ppi=item['full_ppi'], phase_period=item['phase_period_deg'], maximum_gates=model.maximum_gates)
            read_seconds = time.perf_counter()-read_t
            e = infer(raw, model)
            b = baseline_from_npz(d)
            o = apply_policy(raw, e, b, policy)
            out = tmp/item['case']; out.mkdir()
            io_start = time.perf_counter()
            write_npz(out/'evidence.npz', e.arrays)
            write_npz(out/'outcome.npz', o.arrays)
            _write_gzip_json(out/'folds.json.gz', e.folds)
            _write_gzip_json(out/'objects.json.gz', {'objects': e.objects, 'links': e.links})
            write_json(out/'identity.json', e.identity)
            write_csv(out/'roi_gates.csv', gate_rows(raw,d,e,o,d['review_roi']))
            relevant = d['baseline_business_visible'] & ((e.arrays['family_code']>0)|o.added_quarantine)
            write_csv(out/'visible_proposals.csv.gz', gate_rows(raw,d,e,o,relevant), compress=True)
            serialization_seconds = time.perf_counter()-io_start
            s = statistics(item['case'],d,e,o)
            s['input_npz_sha256'] = item['sha256']
            s['input_manifest_sha256'] = sha_file(input_root/'manifest.json')
            s['performance']['read_seconds'] = read_seconds
            s['performance']['serialization_and_report_seconds'] = serialization_seconds
            s['performance']['upload_seconds'] = None
            if plot:
                t = time.perf_counter()
                plots(out/'figures',item['case'],raw,b,o,e)
                s['performance']['plot_seconds'] = time.perf_counter()-t
            else:
                s['performance']['plot_seconds'] = None
            # No stored numeric input can change while results are being produced.
            if sha_file(input_root/(item['case']+'.npz')) != item['sha256']:
                raise ValueError("input changed during computation")
            write_json(out/'summary.json', s)
            summaries.append(s)
            print(json.dumps({k:s[k] for k in ('case','roi_gates','roi_source_supported','roi_added_quarantine','outside_roi_visible_additions')}, ensure_ascii=False), flush=True)
            del raw, e, o, b, d
            gc.collect()
        write_json(tmp/'summary.json', {'method':model.method, 'model':asdict(model), 'policy':asdict(policy),
                                        'cases':summaries, 'truth_labels':None, 'operational_eligible':False})
        if plot:
            html_index(tmp,summaries)
        # All result files have checksums; no baseline data/credentials are uploaded.
        inventory = {str(p.relative_to(tmp)):sha_file(p) for p in sorted(tmp.rglob('*')) if p.is_file()}
        write_json(tmp/'files.sha256.json',inventory)
        for name, value in inventory.items():
            if sha_file(tmp/name) != value:
                raise AssertionError("output verification failed")
        tmp.rename(output)
        return summaries
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--config',required=True,type=Path)
    p.add_argument('--plots',action='store_true')
    a=p.parse_args()
    model,policy=load_settings(a.config)
    run_manifest(a.input,a.output,model,policy,plot=a.plots)

if __name__=='__main__':
    main()
