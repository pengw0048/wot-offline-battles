"""Intermittent physical contacts must not indefinitely disable recovery."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import LocalDriver
from gui.mods.offline_lan_0922.ai.navigation import TerrainNavigator
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime


class ContactProgressTests(unittest.TestCase):
    def test_intermittent_contacts_allow_real_driver_and_navigator_recovery(self):
        navigator = TerrainNavigator(lambda *args: 0., cell_size=1.)
        adapter = BotAdapter.__new__(BotAdapter)
        adapter.driver = LocalDriver()
        runtime = BotRuntime.__new__(BotRuntime)
        runtime.navigator, runtime.adapter = navigator, adapter
        runtime.states = {1: dict(team=1)}
        runtime._artillery_intents, runtime._artillery_reproofs = {}, {}
        adapter.navigation_target = runtime._navigation_target
        neighbour = dict(id=2, alive=True, team=1, position=(0., 0., 7.),
                         yaw=0., half_length=3.5, half_width=1.5)
        order = dict(move_position=(0., 0., 12.), face_position=(0., 0., 12.),
                     combat_mode='base_defense', fire_allowed=False)
        for frame in range(150):
            state = dict(id=1, slot=0, position=(0., 0., 0.), yaw=0., speed=0.,
                         dt=.2, now=(frame + 1) * .2, neighbours=[neighbour],
                         half_length=3.5, half_width=1.5, pose_clear=lambda yaw: True)
            adapter.decide_with_order(state, order, lambda *args: True)
            # Contact is delivered after the real decision, as in BotRuntime.
            if frame % 2 == 0:
                adapter.driver.wait_for_traffic(1, .2)
        self.assertGreater(adapter.driver.states[1]['recovery_count'], 0)
        self.assertGreater(navigator.bot_direct_progress[1]['replans'], 0)

    def test_protection_expires_and_only_real_progress_or_hold_renews_it(self):
        driver = LocalDriver()
        def decide(position=(0., 0., 0.), yaw=.8, intent=True):
            return driver.drive(1, 0, position, yaw, 0., .2, (0., 0., 12.), [],
                                lambda *args: True, movement_intent=intent)
        decide()
        driver.wait_for_traffic(1, 1.5)
        self.assertTrue(driver.states[1]['traffic_waiting'])
        driver.wait_for_traffic(1, .001)
        self.assertFalse(driver.states[1]['traffic_waiting'])
        decide(yaw=.7)
        self.assertEqual(driver.states[1]['traffic_wait_time'], 0.)
        driver.wait_for_traffic(1, .2)
        decide(yaw=.8)
        decide(yaw=.7)
        self.assertGreater(driver.states[1]['traffic_wait_time'], 0.)
        decide(position=(0., 0., .1), yaw=.7)
        self.assertEqual(driver.states[1]['traffic_wait_time'], 0.)
        driver.wait_for_traffic(1, 2.)
        decide(position=(0., 0., .1), intent=False)
        self.assertEqual(driver.states[1]['traffic_wait_time'], 0.)


if __name__ == '__main__':
    unittest.main()
