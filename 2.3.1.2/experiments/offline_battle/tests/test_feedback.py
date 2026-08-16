import math
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub


class _BattleEventType(object):
    """The packers the client uses, with the client's own bit layout."""

    @staticmethod
    def packDamage(damage, attackReasonID, *args, **kwargs):
        return (int(damage) & 65535) << 25 | (int(attackReasonID) & 255) << 17

    @staticmethod
    def packCrits(critsCount, attackReasonID, *args, **kwargs):
        return (int(critsCount) & 65535) << 24 | (int(attackReasonID) &
                                                  255) << 16


_common = types.ModuleType('BattleFeedbackCommon')
_common.BATTLE_EVENT_TYPE = _BattleEventType
sys.modules['BattleFeedbackCommon'] = _common

feedback = package_stub.load('feedback')


class _Avatar(object):
    playerVehicleID = 7

    def __init__(self):
        self.events = []
        self.directions = []

    def onBattleEvents(self, events):
        self.events.extend(events)

    def showOwnVehicleHitDirection(self, *args):
        self.directions.append(args)


class DealtEventTests(unittest.TestCase):
    def test_damage_reports_one_event(self):
        events = feedback.dealt_events(5, 42, 0, False)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['eventType'], feedback.EVENT_DAMAGE)
        self.assertEqual(events[0]['targetID'], 5)
        self.assertEqual(events[0]['count'], 42)

    def test_a_crit_adds_its_own_event(self):
        events = feedback.dealt_events(5, 42, 2, False)
        kinds = [event['eventType'] for event in events]
        self.assertEqual(kinds, [feedback.EVENT_DAMAGE, feedback.EVENT_CRIT])
        self.assertEqual(events[1]['count'], 2)

    def test_a_kill_adds_its_own_event(self):
        kinds = [event['eventType']
                 for event in feedback.dealt_events(5, 42, 0, True)]
        self.assertEqual(kinds, [feedback.EVENT_DAMAGE, feedback.EVENT_KILL])

    def test_a_bounce_reports_nothing(self):
        self.assertEqual(feedback.dealt_events(5, 0, 0, False), [])

    def test_the_damage_is_packed_where_the_client_reads_it(self):
        details = feedback.dealt_events(5, 42, 0, False)[0]['details']
        self.assertEqual(details >> 25 & 65535, 42)


class ReceivedEventTests(unittest.TestCase):
    def test_damage_taken_reports_the_attacker(self):
        events = feedback.received_events(9, 30, 0)
        self.assertEqual(events[0]['eventType'],
                         feedback.EVENT_RECEIVED_DAMAGE)
        self.assertEqual(events[0]['targetID'], 9)

    def test_a_crit_taken_adds_its_own_event(self):
        kinds = [event['eventType']
                 for event in feedback.received_events(9, 30, 1)]
        self.assertEqual(kinds, [feedback.EVENT_RECEIVED_DAMAGE,
                                 feedback.EVENT_RECEIVED_CRIT])

    def test_a_bounce_taken_reports_nothing(self):
        self.assertEqual(feedback.received_events(9, 0, 0), [])


class HitDirectionTests(unittest.TestCase):
    def test_an_attacker_straight_ahead_is_zero_yaw(self):
        self.assertAlmostEqual(
            feedback.hit_direction_yaw((0.0, 0.0, 0.0), (0.0, 0.0, 10.0)),
            0.0)

    def test_the_bearing_is_in_the_target_frame(self):
        """An attacker dead ahead of a hull facing east reads as ahead."""
        self.assertAlmostEqual(
            feedback.hit_direction_yaw((0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                                       math.pi / 2.0),
            0.0)

    def test_the_bearing_stays_inside_one_turn(self):
        value = feedback.hit_direction_yaw((0.0, 0.0, 0.0), (0.0, 0.0, -10.0),
                                           math.pi)
        self.assertLessEqual(abs(value), math.pi)

    def test_an_attacker_to_the_right_is_a_quarter_turn(self):
        self.assertAlmostEqual(
            feedback.hit_direction_yaw((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            math.pi / 2.0)

    def test_an_attacker_behind_is_a_half_turn(self):
        self.assertAlmostEqual(
            abs(feedback.hit_direction_yaw((0.0, 0.0, 0.0),
                                           (0.0, 0.0, -10.0))),
            math.pi)


class PublishTests(unittest.TestCase):
    def test_a_dealt_hit_reaches_the_avatar(self):
        avatar = _Avatar()
        feedback.publish_dealt(avatar, 5, 42, 1, False)
        self.assertEqual(len(avatar.events), 2)

    def test_a_received_hit_also_points_the_arrow(self):
        avatar = _Avatar()
        feedback.publish_received(avatar, 9, 30, 0, 1.2)
        self.assertEqual(len(avatar.events), 1)
        self.assertEqual(len(avatar.directions), 1)
        self.assertAlmostEqual(avatar.directions[0][0], 1.2)
        self.assertEqual(avatar.directions[0][1], 9)

    def test_a_bounce_still_points_the_arrow(self):
        avatar = _Avatar()
        feedback.publish_received(avatar, 9, 0, 0, 0.5)
        self.assertEqual(avatar.events, [])
        self.assertEqual(len(avatar.directions), 1)


if __name__ == '__main__':
    unittest.main()
