#!/usr/bin/env python3
"""Small, dependency-free boundary checks. No services or configuration writes."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import sys


def violations(root: Path) -> list[str]:
    errors: list[str] = []
    def read(path: str) -> str:
        file = root / path
        if not file.is_file():
            errors.append(f'missing boundary source: {path}')
            return ''
        return file.read_text()

    query_root = root / 'services/control/internal/readquery'
    if not query_root.is_dir():
        errors.append('missing readquery package')
    for file in query_root.glob('*.go'):
        if file.name.endswith('_test.go'):
            continue
        source = file.read_text()
        blocks = re.findall(r'(?ms)^import\s*\((.*?)\)', source)
        blocks += re.findall(r'(?m)^import\s+([^\n]+)', source)
        imports = re.findall(r'"([^"]+)"', '\n'.join(blocks))
        forbidden = ('net/http', 'net/http/httptest', 'database/sql', 'os', 'os/exec')
        for item in imports:
            if item in forbidden or 'pgx' in item or re.search(r'/internal/(api|workspace)(/|$)', item):
                errors.append(f'readquery depends on runtime/transport: {item}')
    engine = root / 'algorithms/rainpulse_algo/radar/qc_engine'
    if not engine.is_dir():
        errors.append('missing QC engine package')
    files = list(engine.rglob('*.py'))
    resource = root / 'algorithms/rainpulse_algo/radar/qc_resources.py'
    if not resource.is_file():
        errors.append('missing QC resource boundary')
    else:
        files.append(resource)
    for file in files:
        try:
            tree = ast.parse(file.read_text())
        except SyntaxError as error:
            errors.append(f'{file.relative_to(root)}: {error}')
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '', *(n.name for n in node.names)]
            if any('qc_worker' in name.split('.') for name in names):
                errors.append(f'QC core/resources import worker: {file.relative_to(root)}:{node.lineno}')
    app = read('apps/web/src/App.tsx')
    if re.search(r"import\s+[^\n]+\s+from\s+['\"][^'\"]*(MainWorkspace|AdminWorkspace|QCReviewWorkspace|PipelineInspector)['\"]", app):
        errors.append('App eagerly imports a heavy workspace')
    if 'lazy(' not in app or '<Suspense' not in app or '<RouteBoundary>' not in app:
        errors.append('route lazy/Suspense/error boundary missing')
    native = read('services/control/internal/apiapp/handler.go')
    if native.count('readquery.New(store, store)') != 1 or len(re.findall(r'Queries:\s*queries', native)) != 2:
        errors.append('native API and workspace must share the same query service')
    runtime = read('services/control/internal/workspace/runtime.go')
    if not re.search(r'newHandlerWithQueries\([^\n]+options.Queries\)', runtime):
        errors.append('runtime does not inject typed workspace queries')
    handler = read('services/control/internal/workspace/handler.go')
    for old in ['var page forecastRunPage\n', 'var analyses analysisCyclePage\n']:
        if old in handler:
            errors.append('catalog has reverted to untyped response decoding')
    for call in ['queryForecastPage(', 'queryAnalysisPage(', 'queryAnalysis(', 'queryQPE(', 'queryDiagnostics(']:
        if call not in handler:
            errors.append(f'workspace typed call missing: {call}')
    adapter = read('services/control/internal/workspace/domain_queries.go')
    if 'json.Marshal' in adapter or 'json.Unmarshal' in adapter or 'readCore(' in adapter:
        errors.append('typed adapter serializes/re-enters HTTP')
    policy = read('apps/web/src/workspace/refreshPolicy.ts')
    if re.search(r'\b(fetch|useEffect|setInterval)\s*\(', policy):
        errors.append('refresh policy has runtime side effects')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = violations(args.root.resolve())
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Architecture boundary checks passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
