#!/usr/bin/env python
"""Check projection reuse and primitive leaves without a native engine."""
from __future__ import print_function
from array import array
import unittest

from kernel_adapter import ENGINE_FIELDS
from kernel_engine import EngineLeaves, _ACTOR_ARGS


class Object(object):
    def __init__(self, **values):
        self.__dict__.update(values)


def packet(kind, source, target=None, args=()):
    result = array('d', [0]) * 256
    result[0] = kind
    at = 1
    for actor in (source, target or {}):
        result[at] = actor.get('kind') == 'human'
        result[at + 1] = actor.get('network_id', actor.get('id', 0))
        mask = 0
        for index, name in enumerate(ENGINE_FIELDS):
            if actor.get(name) is not None:
                mask |= 1 << index
                result[at + 3 + index] = actor[name]
        result[at + 2] = mask
        at += 3 + len(ENGINE_FIELDS)
        for name in ('position', 'velocity'):
            if name in actor:
                result[at] = 1
                result[at + 1:at + 4] = array('d', actor[name])
            at += 4
    assert at == _ACTOR_ARGS
    result[at:at + len(args)] = array('d', args)
    return result


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.state = dict(id=3, x=1.0, y=2.0, z=3.0, yaw=.4, speed=5.0,
                          alive=True, vehicle='test', position=(99, 98, 97))
        self.runtime = Object(states={3: self.state}, _descriptor_pairs={}, _descriptors={})
        self.module = Object(_position=lambda state: tuple(state.get(name, 0.0) for name in ('x', 'y', 'z')))
        self.leaves = EngineLeaves(self.module, self.runtime)
        self.leaves.owned = True

    def no_actors(self):
        def rejected(*unused):
            raise AssertionError('primitive leaf decoded a vehicle')
        self.leaves.actor = rejected

    def test_exact_variants_and_mutation_isolation(self):
        first = packet(751, self.state)
        second = packet(751, dict(self.state, x=7, alive=False))
        a = self.leaves.actor(first, 1)[0]
        a['x'] = 1000
        b = self.leaves.actor(second, 1)[0]
        b.pop('vehicle')
        again = self.leaves.actor(first, 1)[0]
        self.assertEqual(again['x'], 1)
        self.assertEqual(again['vehicle'], 'test')
        self.assertIs(again['alive'], True)
        self.assertIsInstance(again['id'], int)
        self.assertEqual(a['x'], 1000)
        self.assertEqual(self.leaves.actor_decodes, 2)
        self.assertEqual(len(self.leaves.actor_cache), 2)
        self.leaves.actor_cache.clear()
        self.leaves.actor(first, 1)
        self.assertEqual(self.leaves.actor_decodes, 3)

    def test_presence_and_actor_identity(self):
        raw = dict(self.state)
        del raw['x']
        raw.pop('position')
        value = self.leaves.actor(packet(751, raw), 1)[0]
        self.assertNotIn('x', value)
        self.assertEqual(value['position'], (0, 2, 3))
        self.leaves.players = {3: dict(vehicle='human')}
        human = self.leaves.actor(packet(751, dict(self.state, kind='human')), 1)[0]
        self.assertEqual(human['vehicle'], 'human')
        self.assertEqual(human['kind'], 'human')

    def test_standalone_cache_stays_bounded(self):
        self.leaves.owned = False
        for index in range(100):
            self.leaves.actor(packet(751, dict(self.state, x=index)), 1)
        self.assertEqual(len(self.leaves.actor_cache), 1)

    def test_water_reads_xyz_and_contains_failure(self):
        self.no_actors()
        positions = []
        def depth(position):
            positions.append(position)
            return float('nan') if len(positions) > 1 else 2.5
        self.runtime._water_depth_probe = depth
        query = packet(769, self.state)
        self.leaves(query)
        self.assertEqual(positions, [(1, 2, 3)])
        self.assertEqual(list(query[:2]), [1, 2.5])
        query = packet(769, self.state)
        self.leaves(query)
        self.assertEqual(list(query[:2]), [0, -1])

    def test_ground_and_report_need_no_descriptor(self):
        self.no_actors()
        calls = []
        self.runtime._physics_ground_probe = lambda *args: calls.append(args)
        self.runtime.motion_report = lambda *args: calls.append(args)
        self.leaves(packet(760, self.state, args=(630, 3, 1, 2, 3)))
        self.leaves(packet(760, self.state, args=(616, 3, 1, 4, 5)))
        self.assertEqual(calls, [(1, 2, 3), (3, 'crushed', 4, 5)])

    def test_scan_cancel_required_fields(self):
        self.no_actors()
        calls = []
        self.runtime.destructible_body_scan = lambda *args: calls.append(args)
        self.runtime.artillery_launch_cancel = lambda value: calls.append(value)
        self.leaves(packet(771, self.state))
        self.leaves(packet(766, self.state))
        self.assertEqual(calls, [(3, (1, 2, 3), .4, 5), dict(id=3)])
        missing = dict(self.state)
        del missing['yaw']
        with self.assertRaises(KeyError):
            self.leaves(packet(771, missing))
        self.leaves(packet(766, {}))
        self.assertEqual(len(calls), 2)

    def test_aim_reads_only_borrowed_request(self):
        class BorrowedPacket(object):
            # The provider owns 32,768 doubles, but this native request lends
            # only its 256-double packet. The remaining capacity is unrelated.
            def __init__(self, values):
                self.values = values
            def __getitem__(self, key):
                if isinstance(key, slice):
                    if key.stop is None or key.stop > len(self.values):
                        raise AssertionError('Read beyond borrowed request')
                elif key >= len(self.values):
                    raise AssertionError('Read beyond borrowed request')
                return self.values[key]
            def __setitem__(self, key, value):
                self.values[key] = value

        calls = []
        def origin(*args):
            calls.append(args)
            return (4, 5, 6)
        self.runtime.direct_launch_origin_probe = origin
        query = BorrowedPacket(packet(761, self.state, args=(2, 3, .4, .5, .6)))
        self.leaves(query)
        self.assertEqual(list(query[:4]), [1, 4, 5, 6])
        self.assertEqual(calls[0][2:], (2, 3, .4, .5, .6))


if __name__ == '__main__':
    unittest.main()
