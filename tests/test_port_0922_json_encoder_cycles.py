"""No hot-path `json.dumps` may force CPython 2.7's pure-Python encoder.

Report `20260910-072722` named the leak with a closed cycle path:

    PYCYCLE found=1  cell -> function@encoder.py:288 -> tuple(len=<=32) -> cell

`encoder.py:288` is `json.encoder._make_iterencode`, whose `_iterencode`,
`_iterencode_dict` and `_iterencode_list` are mutually recursive closures - a
reference cycle on every call. #1513 disables the cyclic collector, so each
call leaks, and the closure's cells retain the whole record being encoded.

CPython 2.7 selects that encoder whenever `indent is not None` **or**
`sort_keys` is true:

    if (_one_shot and c_make_encoder is not None
            and self.indent is None and not self.sort_keys):

Measured on a representative `[BOT MOTION]` record: `sort_keys=True` costs
824 us per call and leaks 34 gc-only objects; without it, 112 us and zero,
for byte-identical output.

File writers are exempt: their content is read by people and diffed, they run
once per save rather than per event, and one cycle per save is not a leak.
"""

import ast
import io
from pathlib import Path
import unittest

PORT = (Path(__file__).resolve().parents[1] / 'src' / 'res' / 'scripts' /
        'client' / 'gui' / 'mods' / 'offline_lan_0922')

# Modules whose json output is a file on disk for a human to read.
FILE_WRITERS = frozenset((
    'config.py',
    'internal_hit_layouts.py',
    'internal_layout_store.py',
))
FORCING_KEYWORDS = ('sort_keys', 'indent')


def _forcing_calls(path):
    """Yield (line, keyword) for each json.dumps that forces pure Python."""
    with io.open(str(path), encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and
                target.attr in ('dumps', 'dump') and
                isinstance(target.value, ast.Name) and
                target.value.id == 'json'):
            continue
        for keyword in node.keywords:
            if keyword.arg not in FORCING_KEYWORDS:
                continue
            # A literal false/None does not force the Python encoder.
            value = keyword.value
            if (isinstance(value, ast.Constant) and
                    value.value in (False, None)):
                continue
            yield node.lineno, keyword.arg


class JsonEncoderCycleTest(unittest.TestCase):
    def test_no_hot_path_module_forces_the_pure_python_encoder(self):
        offenders = []
        for path in sorted(PORT.glob('*.py')):
            if path.name in FILE_WRITERS:
                continue
            for line, keyword in _forcing_calls(path):
                offenders.append('%s:%d passes %s' % (
                    path.name, line, keyword))
        self.assertEqual(
            [], offenders,
            'these json.dumps calls force CPython 2.7 pure-Python encoder, '
            'which leaks a reference cycle per call in a runtime with the '
            'collector disabled:\n  ' + '\n  '.join(offenders))

    def test_the_audit_would_catch_a_regression(self):
        # The audit is only worth having if it fails on the real pattern.
        source = ('import json\n'
                  'def emit(record):\n'
                  '    return json.dumps(record, sort_keys=True)\n')
        scratch = PORT / '_json_audit_probe.py'
        scratch.write_text(source, encoding='utf-8')
        self.addCleanup(scratch.unlink)
        found = list(_forcing_calls(scratch))
        self.assertEqual([(3, 'sort_keys')], found)

    def test_an_explicitly_disabled_keyword_is_not_an_offender(self):
        source = ('import json\n'
                  'def emit(record):\n'
                  '    return json.dumps(record, sort_keys=False, indent=None)\n')
        scratch = PORT / '_json_audit_allowed.py'
        scratch.write_text(source, encoding='utf-8')
        self.addCleanup(scratch.unlink)
        self.assertEqual([], list(_forcing_calls(scratch)))

    def test_the_file_writers_are_a_deliberate_short_list(self):
        # If this grows, someone exempted a hot path instead of fixing it.
        self.assertEqual(3, len(FILE_WRITERS))
        for name in FILE_WRITERS:
            self.assertTrue((PORT / name).exists(), name)


if __name__ == '__main__':
    unittest.main()
