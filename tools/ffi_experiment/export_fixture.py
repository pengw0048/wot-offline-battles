#!/usr/bin/env python3
"""Extract existing workload fixtures for the Python 2 measurement runner.

The production client source is not translated. Only Python-3-only test-module
imports are replaced with a small portable harness. Selected fixture bodies
remain independently inspectable in the output JSON; the native-query holder
also gains its required receiver type for Python 2 unbound methods.
"""
import argparse
import ast
import json
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[2]


def select(path, names):
    source = (ROOT / path).read_text()
    found = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names:
            found[node.name] = textwrap.dedent(ast.get_source_segment(source, node))
    if set(found) != set(names):
        raise RuntimeError('fixture selection does not match %s' % path)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    bodies = select('tests/test_port_0922_bot_runtime.py', (
        '_Strict1513Component', '_HitTester1513', '_combat_descriptor', '_bot_equipment_contracts'))
    bodies.update(select('tests/test_port_0922_destructibles.py', (
        '_Vector', '_Manager', '_catalog', '_empty_catalog_scan_fixture')))
    bodies.update(select('tools/benchmark_bot_workload.py', ('make_runtime', 'combat_native_queries')))
    bodies['crew_factors_module'] = (ROOT / 'tests/effective_params_fixture.py').read_text()
    bodies['make_runtime'] = bodies['make_runtime'].replace(
        '    import test_port_0922_bot_runtime as fixtures\n', '').replace(
        '    from effective_params_fixture import bot_default_crew_factors\n', '')
    bodies['combat_native_queries'] = '@contextlib.contextmanager\n' + bodies['combat_native_queries'].replace(
        '    import test_port_0922_destructibles as fixtures\n', '')
    # Python 2 checks an unbound method's receiver type. Preserve the fixture's
    # exact attributes while giving its holder the required BattleRuntime base.
    bodies['combat_native_queries'] = bodies['combat_native_queries'].replace(
        '    holder = types.SimpleNamespace(',
        '    class Holder(BattleRuntime):\n'
        '        def __init__(self, **values):\n'
        '            self.__dict__.update(values)\n'
        '    holder = Holder(')
    args.output.write_text(json.dumps(bodies, sort_keys=True))


if __name__ == '__main__':
    main()
