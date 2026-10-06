import math
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import driver_codec as codec, native_math


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
                      -(2 ** 63) - 1, 2 ** 63, b'\xff', cycle, lambda: None,
                      (lambda: None).__code__, {1: 'a', '1': 'b'}]:
            with self.subTest(value=type(value)):
                with self.assertRaises((ValueError, UnicodeError)):
                    codec.encode(value)

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
