import types
import unittest
from test_port_0922_battle_runtime import _Vector
from gui.mods.offline_lan_0922.collision_feedback import CollisionFeedback


class CollisionFeedbackTests(unittest.TestCase):
    def test_copied_velocity_reaches_presentation_without_mutating_entity(self):
        feedback = CollisionFeedback()
        first = types.SimpleNamespace(id=1, isStarted=True, filter=types.SimpleNamespace(velocity=_Vector()))
        second = types.SimpleNamespace(id=2, isStarted=True, filter=types.SimpleNamespace(velocity=_Vector()))
        first.marker = object()
        heard = []
        def stock(avatar, a, b, point, now):
            self.assertIs(a.marker, first.marker)
            heard.append(a.filter.velocity.x-b.filter.velocity.x)
        args = (object(), stock, first, second, (5, 0, 0), (-2, 0, 0), (1, 1, 1))
        vector = lambda v: _Vector(*v)
        self.assertTrue(feedback.present(*args, 1., vector))
        self.assertFalse(feedback.present(*args, 1.1, vector))
        self.assertTrue(feedback.present(*args, 1.3, vector))
        self.assertEqual([7, 7], heard)
        self.assertEqual(0, first.filter.velocity.x)
        feedback.observed(first, second, 1.5)
        self.assertTrue(feedback.present(*args, 1.6, vector))
        first.filter.velocity = _Vector(3, 0, 0)
        feedback.observed(first, second, 1.9)
        self.assertFalse(feedback.present(*args, 2., vector))
        first.isStarted = False
        self.assertFalse(feedback.present(*args, 2., vector))
        feedback.clear()
        first.isStarted = True
        self.assertTrue(feedback.present(*args, 0., vector))
