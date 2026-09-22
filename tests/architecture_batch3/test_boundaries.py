"""Check the architectural guard using deliberately small source fixtures.

These fixtures verify detection behavior; they are not a full repository audit.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = spec_from_file_location("batch3_boundaries", ROOT / "scripts/check_architecture_boundaries.py")
CHECK = module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def base(root):
    sources = {
        "services/control/internal/readquery/service.go": 'package readquery\nimport "context"\n',
        "algorithms/rainpulse_algo/radar/qc_engine/context.py": 'from ..qc_resources import load_geometry_resources\n',
        "algorithms/rainpulse_algo/radar/qc_resources.py": 'from pathlib import Path\n',
        "apps/web/src/App.tsx": 'const Main = lazy(() => import("./workspace/MainWorkspace")); <RouteBoundary><Suspense /></RouteBoundary>',
        "services/control/internal/apiapp/handler.go": 'q := readquery.New(store, store)\nQueries: queries,\nQueries: queries,',
        "services/control/internal/workspace/runtime.go": 'x := newHandlerWithQueries(core, nil, options.Queries)',
        "services/control/internal/workspace/handler.go": 'queryForecastPage(); queryAnalysisPage(); queryAnalysis(); queryQPE(); queryDiagnostics()',
        "services/control/internal/workspace/domain_queries.go": 'package workspace\n',
        "apps/web/src/workspace/refreshPolicy.ts": 'export const x = true',
    }
    for path, text in sources.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    return root


def test_accepted_shape(tmp_path):
    assert CHECK.violations(base(tmp_path)) == []


@pytest.mark.parametrize("dependency", ["net/http", "net/http/httptest", "database/sql", "os", "os/exec", "github.com/jackc/pgx/v5", "example/internal/api", "example/internal/workspace"])
def test_query_transport_dependency(tmp_path, dependency):
    base(tmp_path)
    (tmp_path / "services/control/internal/readquery/service.go").write_text(f'package readquery\nimport "{dependency}"\n')
    assert any("runtime/transport" in e for e in CHECK.violations(tmp_path))


@pytest.mark.parametrize("statement", ["from ..qc_worker import helper", "import rainpulse_algo.radar.qc_worker", "from .. import qc_worker"])
def test_no_reverse_worker_import(tmp_path, statement):
    base(tmp_path)
    (tmp_path / "algorithms/rainpulse_algo/radar/qc_engine/context.py").write_text(statement)
    assert any("import worker" in e for e in CHECK.violations(tmp_path))


def test_test_only_go_dependency_allowed(tmp_path):
    base(tmp_path)
    (tmp_path / "services/control/internal/readquery/service_test.go").write_text('package readquery\nimport "net/http/httptest"')
    assert not CHECK.violations(tmp_path)


@pytest.mark.parametrize("path,text,needle", [
    ("apps/web/src/App.tsx", "import { MainWorkspace } from './workspace/MainWorkspace'", "eagerly"),
    ("services/control/internal/apiapp/handler.go", "Queries: independentQueries,", "same query"),
    ("services/control/internal/workspace/runtime.go", "newHandler(core,nil)", "inject"),
    ("services/control/internal/workspace/handler.go", "var page forecastRunPage\n", "untyped"),
    ("services/control/internal/workspace/domain_queries.go", "json.Unmarshal(data, &out)", "serializes"),
    ("apps/web/src/workspace/refreshPolicy.ts", "fetch('/api')", "side effects"),
])
def test_protected_seams(tmp_path, path, text, needle):
    base(tmp_path)
    (tmp_path / path).write_text(text)
    assert any(needle in e for e in CHECK.violations(tmp_path))


def test_missing_checkout_is_not_success(tmp_path):
    assert CHECK.violations(tmp_path)


def test_syntax_error_reported(tmp_path):
    base(tmp_path)
    (tmp_path / "algorithms/rainpulse_algo/radar/qc_engine/context.py").write_text("def broken(")
    assert any("context.py" in e for e in CHECK.violations(tmp_path))
