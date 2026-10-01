import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    's_review', Path(__file__).resolve().parents[1] / 'scripts/plot_s_radial_review.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_packet_short_writes_and_interruptions_do_not_drop_bytes():
    function = next(node for node in ast.parse(module.WORKER).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'emit_packet')
    output = bytearray()
    calls = []

    def write(fd, view):
        calls.append(fd)
        if len(calls) == 1:
            raise InterruptedError()
        count = min(2, len(view))
        output.extend(view[:count])
        return count

    namespace = dict(os=SimpleNamespace(write=write),
                     sys=SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: 1)))
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<packet>', 'exec'), namespace)
    namespace['emit_packet']('SNAPSHOT_CHUNK 0 1 payload\n')
    assert output.decode() == 'SNAPSHOT_CHUNK 0 1 payload\n'
