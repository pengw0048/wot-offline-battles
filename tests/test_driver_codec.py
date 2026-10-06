import base64
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import driver_codec as codec, native_math


INTEGER_BOUNDARIES = (-(2 ** 63), -(2 ** 31), -1, 0, 2 ** 31 - 1,
                      2 ** 31, 2 ** 63 - 1, 2 ** 63,
                      10551269363652952322, 2 ** 64 - 1)


def damage_sticker_message():
    # Reduced from the Test13 server_tick=1391 event that closed the bridge.
    return {'type': 'driver_message', 'generation': 1, 'round_id': 1,
            'player_id': 16, 'message': {
                'type': 'events', 'round_id': 1, 'server_tick': 1391,
                'events': [{'kind': 'bot_bot_hit', 'event_id': '1:1391:1',
                            'attacker_bot': 30, 'target_bot': 14,
                            'damage': 98, 'health': 502,
                            'damage_sticker': 10551269363652952322}]}}


class DriverCodecTests(unittest.TestCase):
    def test_native_cold_load_uses_exact_existing_game_loader(self):
        backend = types.SimpleNamespace(driver_encode=lambda value: b'bytes',
                                        driver_decode=lambda data, constructor: (None,))
        with mock.patch.dict(sys.modules, {'offline_math_batch_native': None}), \
                mock.patch.object(native_math, '_load', return_value=backend) as load:
            self.assertIs(backend, codec._native())
        load.assert_called_once_with()

    def test_native_already_loaded_backend_is_reused_without_cold_loader(self):
        backend = types.SimpleNamespace()
        with mock.patch.dict(sys.modules, {'offline_math_batch_native': backend}), \
                mock.patch.object(native_math, '_load') as load:
            self.assertIs(backend, codec._native())
        load.assert_not_called()

    def test_missing_native_backend_is_explicit_failure_without_fallback(self):
        with mock.patch.dict(sys.modules, {'offline_math_batch_native': None}), \
                mock.patch.object(native_math, '_load', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'matching native'):
                codec._native()

    def test_plain_roundtrip_utf8_tuple_int64_and_integer_object_keys(self):
        value = {'text': '\u5766\u514b', 'bytes': b'valid', 'tuple': (None, True, False),
                 'numbers': [-(2 ** 63), 2 ** 63 - 1, -.0, 1.125],
                 'shell_ammo': {1: 30}}
        result = codec.decode(codec.encode(value))
        self.assertEqual(['valid', [None, True, False]],
                         [result['bytes'], result['tuple']])
        self.assertEqual({'1': 30}, result['shell_ammo'])
        self.assertEqual(value['numbers'], result['numbers'])
        self.assertEqual(-1., math.copysign(1., result['numbers'][2]))

    def test_rejects_nonplain_nonfinite_int_overflow_cycles_and_invalid_utf8(self):
        cycle = []; cycle.append(cycle)
        for value in [object(), set(), complex(1, 2), float('nan'), float('inf'),
                      -(2 ** 63) - 1, 2 ** 64, b'\xff', cycle, lambda: None,
                      (lambda: None).__code__, {1: 'a', '1': 'b'}]:
            with self.subTest(value=type(value)):
                with self.assertRaises((ValueError, UnicodeError)):
                    codec.encode(value)

    def test_signed_wire_is_preserved_and_unsigned_identifiers_stay_exact(self):
        for value in INTEGER_BOUNDARIES:
            with self.subTest(value=value):
                tag, fmt = (b'i', '<q') if value < 2 ** 63 else (b'u', '<Q')
                self.assertEqual(codec.MAGIC + tag + struct.pack(fmt, value),
                                 codec.encode(value))
                self.assertEqual(value, codec.decode(codec.encode(value)))
        self.assertEqual(list(INTEGER_BOUNDARIES),
                         codec.decode(codec.encode(INTEGER_BOUNDARIES)))

    def test_captured_damage_sticker_event_preserves_integer_type_and_value(self):
        message = damage_sticker_message()
        decoded = codec.decode(codec.encode(message))
        self.assertEqual(message, decoded)
        sticker = decoded['message']['events'][0]['damage_sticker']
        self.assertIs(type(sticker), int)
        self.assertEqual(10551269363652952322, sticker)

    def test_uint64_integer_keys_follow_existing_decimal_key_contract(self):
        keys = dict((value, value) for value in INTEGER_BOUNDARIES)
        self.assertEqual(dict((str(key), value) for key, value in keys.items()),
                         codec.decode(codec.encode(keys)))
        for value in ({2 ** 64: None}, {-(2 ** 63) - 1: None},
                      {2 ** 63: True, str(2 ** 63): False}):
            with self.assertRaises(ValueError):
                codec.encode(value)

    def test_unsigned_wire_truncation_and_low_values_are_handled_exactly(self):
        for value in (0, 1, 2 ** 63, 2 ** 64 - 1):
            encoded = codec.MAGIC + b'u' + struct.pack('<Q', value)
            self.assertEqual(value, codec.decode(encoded))
            for length in range(len(encoded)):
                with self.assertRaises(ValueError):
                    codec.decode(encoded[:length])

    def test_rejects_duplicate_keys_trailing_unknown_tags_and_oversize_counts(self):
        text = b's' + struct.pack('<I', 1) + b'x'
        duplicate = codec.MAGIC + b'm' + struct.pack('<I', 2) + text + b'n' + text + b't'
        cases = [duplicate, codec.encode(None) + b'n', codec.MAGIC + b'c',
                 codec.MAGIC + b'l' + struct.pack('<I', 2 ** 32 - 1),
                 codec.MAGIC + b's' + struct.pack('<I', 1) + b'\xff',
                 codec.MAGIC + b'd' + struct.pack('<d', float('inf'))]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises((ValueError, UnicodeError)):
                    codec.decode(data)

    def test_truncation_is_rejected_at_every_byte_boundary(self):
        data = codec.encode({'list': ['a', 4, 1.25, None], 'nested': {'x': True}})
        for length in range(len(data)):
            with self.assertRaises(ValueError):
                codec.decode(data[:length])

    def test_depth_and_byte_bounds_are_enforced_before_delivery(self):
        value = None
        for unused in range(codec.MAX_DEPTH + 2):
            value = [value]
        with self.assertRaises(ValueError):
            codec.encode(value)
        with self.assertRaises(ValueError):
            codec.decode(codec.MAGIC + (b'l' + struct.pack('<I', 1)) * 34 + b'n')
        with self.assertRaises(ValueError):
            codec.encode('x' * codec.MAX_BYTES)

    def test_freeze_does_not_alias_mutable_caller_values(self):
        value = {'actor': {'critical': ['track'], 'pose': [1., 2., 3.]}}
        encoded = codec.encode(value)
        value['actor']['critical'].append('engine')
        value['actor']['pose'][0] = 999.
        self.assertEqual({'actor': {'critical': ['track'], 'pose': [1., 2., 3.]}},
                         codec.decode(encoded))


@unittest.skipUnless(os.environ.get('WOT_NATIVE_MATH_EXTENSION'),
                     'Set WOT_NATIVE_MATH_EXTENSION for Python 2 native codec conformance')
class DriverCodecNativeConformanceTests(unittest.TestCase):
    def run_native(self, script, request=None):
        interpreter = os.environ.get('WOT_NATIVE_MATH_PYTHON27') or shutil.which('python2.7')
        self.assertIsNotNone(interpreter, 'Set WOT_NATIVE_MATH_PYTHON27')
        setup = r'''
import base64, imp, json, os, sys, types
backend = imp.load_dynamic('offline_math_batch_native', sys.argv[1])
sys.path.insert(0, sys.argv[2])
for name in ('gui', 'gui.mods'):
    package = types.ModuleType(name)
    package.__path__ = [os.path.join(sys.argv[2], *name.split('.'))]
    sys.modules[name] = package
from gui.mods.offline_lan_0922 import driver_codec as codec
request = json.load(sys.stdin)
'''
        completed = subprocess.run(
            [interpreter, '-c', setup + script, os.environ['WOT_NATIVE_MATH_EXTENSION'],
             str(Path(__file__).resolve().parents[1] / 'src/res/scripts/client')],
            input=json.dumps(request), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=60)
        self.assertEqual(0, completed.returncode, completed.stderr)
        return json.loads(completed.stdout)

    def test_native_and_reference_bidirectional_integer_contract(self):
        values = list(INTEGER_BOUNDARIES) + [damage_sticker_message()]
        request = {'values': values, 'encoded': [
            base64.b64encode(codec.encode(value)).decode('ascii') for value in values]}
        script = r'''
result = {'encoded': [], 'decoded': []}
for value, encoded in zip(request['values'], request['encoded']):
    native = codec.encode(value)
    assert codec.decode(native) == value
    assert codec._reference_decode(native) == value
    assert codec.decode(codec._reference_encode(value)) == value
    result['encoded'].append(base64.b64encode(native))
    result['decoded'].append(codec.decode(base64.b64decode(encoded)))
keys = dict((value, value) for value in request['values'][:-1])
assert codec.decode(codec.encode(keys)) == dict((str(k), v) for k, v in keys.items())
for value in (-(2 ** 63) - 1, 2 ** 64, {2 ** 64: None},
              {2 ** 63: True, str(2 ** 63): False}):
    try:
        codec.encode(value)
    except ValueError:
        pass
    else:
        raise AssertionError('Native codec accepted out-of-range/duplicate integer')
for value in (0, 1, 2 ** 63, 2 ** 64 - 1):
    import struct
    encoded = codec.MAGIC + 'u' + struct.pack('<Q', value)
    assert codec.decode(encoded) == value
    for length in range(len(encoded)):
        try:
            codec.decode(encoded[:length])
        except ValueError:
            pass
        else:
            raise AssertionError('Native codec accepted truncated uint64')
json.dump(result, sys.stdout)
'''
        result = self.run_native(script, request)
        self.assertEqual(values, result['decoded'])
        for index, (value, encoded) in enumerate(zip(values, result['encoded'])):
            native = base64.b64decode(encoded)
            self.assertEqual(value, codec.decode(native))
            if index < len(INTEGER_BOUNDARIES):
                self.assertEqual(codec.encode(value), native)

    def test_wire_limit_and_bounded_marshal_expansion_cover_all_plain_types(self):
        result = self.run_native(r'''
import marshal, struct
cases = [('unsigned_70000', [2 ** 63] * 70000)]
count = (codec.MAX_BYTES - 9) // 9
cases.append(('unsigned_wire_limit', [2 ** 64 - 1] * count))
for encoder in (codec.encode, codec._reference_encode):
    try:
        encoder([2 ** 64 - 1] * (count + 1))
    except ValueError:
        pass
    else:
        raise AssertionError('Wire limit was enlarged')
text_bytes = codec.MAX_BYTES - 9
text = u'\u5766\u514b' * (text_bytes // 6) + u'x' * (text_bytes % 6)
cases.append(('utf8_exact_wire_limit', text))
cases.append(('list_exact_wire_limit', [None] * (codec.MAX_BYTES - 9)))
row = [None, True, False, -(2 ** 63), 2 ** 64 - 1, 1.25,
       u'\u5766\u514b\U0001f680', {'sticker': 2 ** 64 - 1, 'empty': []}]
row_bytes = len(codec._reference_encode(row)) - len(codec.MAGIC)
cases.append(('mixed_near_wire_limit', [row] * ((codec.MAX_BYTES - 9) // row_bytes)))
deep = [2 ** 64 - 1] * ((codec.MAX_BYTES - 4 - 5 * codec.MAX_DEPTH) // 9)
for unused in range(codec.MAX_DEPTH - 1):
    deep = [deep]
cases.append(('depth_limit_near_wire_limit', deep))
results = []
for name, value in cases:
    encoded = codec.encode(value)
    assert len(encoded) <= codec.MAX_BYTES
    assert codec._reference_decode(encoded) == value
    assert codec.decode(codec._reference_encode(value)) == value
    observed = []
    def construct(data):
        observed.append(len(data))
        assert len(data) <= 5 + 2 * (len(encoded) - 4)
        return marshal.loads(data)
    assert backend.driver_decode(encoded, construct) == (value,)
    assert len(observed) == 1
    results.append({'case': name, 'wire': len(encoded), 'marshal': observed[0]})
for encoded in (codec.MAGIC + 'l' + struct.pack('<I', codec.MAX_BYTES - 8) +
                'n' * (codec.MAX_BYTES - 8),
                codec.MAGIC + ('l' + struct.pack('<I', 1)) * (codec.MAX_DEPTH + 1) + 'n'):
    observed = []
    assert backend.driver_decode(encoded, lambda data: observed.append(data)) is None
    assert observed == []
    try:
        codec._reference_decode(encoded)
    except ValueError:
        pass
    else:
        raise AssertionError('Reference accepted excessive wire size or depth')
json.dump(results, sys.stdout)
''')
        by_name = {item['case']: item for item in result}
        self.assertEqual(6, len(result))
        self.assertEqual(630009, by_name['unsigned_70000']['wire'])
        self.assertGreater(by_name['unsigned_70000']['marshal'], codec.MAX_BYTES)
        self.assertEqual(codec.MAX_BYTES, by_name['utf8_exact_wire_limit']['wire'])
        self.assertEqual(codec.MAX_BYTES, by_name['list_exact_wire_limit']['wire'])
