#!/usr/bin/env python3
"""Read-only live verification probe; no model reruns or persistent outputs."""
import argparse
import json
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
parser.add_argument('--cycle-id', required=True)
args = parser.parse_args()
for algorithm in ['lk', 'steps', 'nowcastnet']:
    for lead in [30, 110]:
        request = urllib.request.Request(args.base_url.rstrip('/')+'/api/v1/workspace/verification',
            data=json.dumps(dict(cycle_id=args.cycle_id, algorithm=algorithm, lead_minutes=lead)).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
        assert result['cycle_id'] == args.cycle_id and result['algorithm'] == algorithm
        assert result['lead_minutes'] == lead
        if result['status'] != 'ready':
            raise RuntimeError(f'{algorithm} +{lead}: {result.get("reason")}')
        metrics = result['metrics']
        assert 0 < metrics['valid_cells'] <= metrics['total_cells']
        assert len(metrics['rows']) == 25
        for row in metrics['rows']:
            for name in ['csi','fss','neighborhood_csi']:
                assert row[name] is None or 0 <= row[name] <= 1
        row = next(r for r in metrics['rows'] if r['threshold']==5 and r['window_km']==10)
        print(json.dumps(dict(algorithm=algorithm,lead=lead,valid_cells=metrics['valid_cells'],
            coverage=metrics['coverage'],csi=row['csi'],fss=row['fss'],neighborhood_csi=row['neighborhood_csi']),ensure_ascii=False),flush=True)
