"""Whether the engine still holds last round's world.

Peng's 2026-09-10 case: our code makes the game create something each round
and never cleans it up.  The memory is C++, so `PYHEAP` and the obmalloc
arena count both attribute it to nobody, but the cause is ours.  A count that
climbs with the round number is last round's world still resident.
"""

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import world_census


class _BigWorld(object):
    def __init__(self, entities=None, user_data_objects=None, spaces=None):
        if entities is not None:
            self.entities = entities
        if user_data_objects is not None:
            self.userDataObjects = user_data_objects
        if spaces is not None:
            self.spaces = spaces


class WorldCensusTest(unittest.TestCase):
    def setUp(self):
        self._reader = world_census._bigworld
        self.addCleanup(setattr, world_census, '_bigworld', self._reader)

    def _install(self, bigworld):
        world_census._bigworld = lambda: bigworld

    def test_each_registry_is_counted(self):
        self._install(_BigWorld(entities={1: 'a', 2: 'b'},
                                user_data_objects={9: 'u'},
                                spaces=[0]))
        state = world_census.snapshot()
        self.assertEqual(2, state['entities'])
        self.assertEqual(1, state['userDataObjects'])
        self.assertEqual(1, state['spaces'])

    def test_entities_left_over_from_last_round_are_visible(self):
        # 30 vehicles still resident at the next round's start is the exact
        # shape being hunted: each one drags a compound model, appearance,
        # tracks and sound sources with it.
        self._install(_BigWorld(entities=dict((i, i) for i in range(30)),
                                user_data_objects={}))
        line = world_census.format_line('round_start', 8)
        self.assertIn('WORLD phase=round_start round=8', line)
        self.assertIn('entities=30', line)
        self.assertIn('userDataObjects=0', line)

    def test_an_unproven_registry_reports_unavailable_not_zero(self):
        # No stock script reads BigWorld.spaces, so its shape is unproven.
        # Reporting 0 for something that could not be counted would be a lie
        # that reads as "nothing leaked".
        self._install(_BigWorld(entities={1: 'a'}, user_data_objects={}))
        state = world_census.snapshot()
        self.assertEqual(-1, state['spaces'])
        self.assertIn('spaces=n/a', world_census.format_line('round_end', 1))

    def test_a_registry_that_has_no_length_is_unavailable(self):
        self._install(_BigWorld(entities=object(), user_data_objects={}))
        self.assertEqual(-1, world_census.snapshot()['entities'])

    def test_a_client_without_the_api_reports_nothing(self):
        self._install(None)
        self.assertIsNone(world_census.snapshot())
        self.assertIsNone(world_census.format_line('round_start', 1))

    def test_a_client_exposing_none_of_them_reports_nothing(self):
        self._install(_BigWorld())
        self.assertIsNone(world_census.snapshot())
        self.assertIsNone(world_census.format_line('round_start', 1))

    def test_logging_never_raises_into_the_round(self):
        class _Hostile(object):
            @property
            def entities(self):
                raise RuntimeError('native registry unavailable')

        self._install(_Hostile())
        world_census.log('round_start', 1)


if __name__ == '__main__':
    unittest.main()
