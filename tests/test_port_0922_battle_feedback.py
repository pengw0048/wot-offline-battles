from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import battle_feedback
from gui.mods.offline_lan_0922.battle_feedback import (
    OBSERVATION_SECONDS, SHELLS_XML_PATH, SIXTH_SENSE_DELAY_SECONDS,
    SixthSenseController, VehicleStatePresenter, gold_shell_names,
    is_gold_shell, reset_shell_price_cache)


class _Scheduler(object):
    def __init__(self):
        self._next = 1
        self.callbacks = {}
        self.cancelled = []

    def schedule(self, delay, callback):
        token = self._next
        self._next += 1
        self.callbacks[token] = (delay, callback)
        return token

    def cancel(self, token):
        self.cancelled.append(token)
        self.callbacks.pop(token, None)

    def invoke(self, token):
        return self.callbacks.pop(token)[1]()


class _Presenter(object):
    def __init__(self):
        self.values = []

    def notify_observed_by_enemy(self, value):
        self.values.append(value)


class SixthSenseTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = _Scheduler()
        self.presenter = _Presenter()
        self.generation = [7]
        self.has_skill = [True]
        self.alive = [True]
        self.battle = [True]
        self.controller = SixthSenseController(
            self.scheduler.schedule, self.scheduler.cancel,
            lambda: self.generation[0], lambda: self.has_skill[0],
            lambda: self.alive[0], lambda: self.battle[0], self.presenter)

    def test_observation_persists_and_delivers_once_after_three_seconds(self):
        self.assertTrue(self.controller.observe(True, 10.0))
        self.assertEqual(1, len(self.scheduler.callbacks))
        token = list(self.scheduler.callbacks)[0]
        self.assertEqual(SIXTH_SENSE_DELAY_SECONDS,
                         self.scheduler.callbacks[token][0])

        self.assertFalse(self.controller.observe(True, 10.0 +
                                                 OBSERVATION_SECONDS - 0.01))
        self.scheduler.invoke(token)
        self.assertEqual([True], self.presenter.values)

        # The repeated sighting extended the ten-second observed window.
        self.assertTrue(self.controller.observe(
            True, 10.0 + OBSERVATION_SECONDS * 2.0))

    def test_no_skill_never_schedules_or_defaults_to_enabled(self):
        self.has_skill[0] = False
        self.assertFalse(self.controller.observe(True, 1.0))
        self.assertEqual({}, self.scheduler.callbacks)

    def test_reset_cancels_the_owned_callback(self):
        self.assertTrue(self.controller.observe(True, 1.0))
        token = list(self.scheduler.callbacks)[0]
        self.controller.reset()
        self.assertEqual([token], self.scheduler.cancelled)
        self.assertEqual({}, self.scheduler.callbacks)

    def test_stale_generation_cannot_present_after_a_new_battle(self):
        self.assertTrue(self.controller.observe(True, 1.0))
        token = list(self.scheduler.callbacks)[0]
        self.generation[0] += 1
        self.scheduler.invoke(token)
        self.assertEqual([], self.presenter.values)

    def test_dead_or_non_battle_owner_cannot_present(self):
        self.assertTrue(self.controller.observe(True, 1.0))
        token = list(self.scheduler.callbacks)[0]
        self.alive[0] = False
        self.scheduler.invoke(token)
        self.assertEqual([], self.presenter.values)

        self.alive[0] = True
        self.assertTrue(self.controller.observe(
            True, 1.0 + OBSERVATION_SECONDS + 0.1))
        token = list(self.scheduler.callbacks)[0]
        self.battle[0] = False
        self.scheduler.invoke(token)
        self.assertEqual([], self.presenter.values)

    def test_constructor_requires_explicit_skill_and_lifecycle_predicates(self):
        with self.assertRaises(ValueError):
            SixthSenseController(None, self.scheduler.cancel,
                                  lambda: 1, lambda: True,
                                  lambda: True, lambda: True,
                                  self.presenter)


class VehicleStatePresenterTests(unittest.TestCase):
    def test_uses_confirmed_1513_vehicle_state_entry(self):
        calls = []
        vehicle_state = types.SimpleNamespace(
            notifyStateChanged=lambda state, value: calls.append((state, value)))
        session_provider = types.SimpleNamespace(
            shared=types.SimpleNamespace(vehicleState=vehicle_state))
        observed = object()
        view_state = types.SimpleNamespace(OBSERVED_BY_ENEMY=observed)

        presenter = VehicleStatePresenter(session_provider, view_state)
        presenter.notify_observed_by_enemy(1)

        self.assertEqual([(observed, True)], calls)


class _Section(object):
    """One ``shells.xml`` shell subsection.

    #1513 ``_xml.readPrice`` probes ``<subsection>/gold`` and reads ``None``
    as "priced in credits", so only that access has to be reproduced.
    """

    def __init__(self, currency):
        self.currency = currency

    def __getitem__(self, path):
        if path == 'price/gold' and self.currency == 'gold':
            return 12
        return None


class _ResMgr(object):
    def __init__(self, sections, available=True):
        self.sections = sections
        self.available = available
        self.opened = []
        self.purged = []

    def openSection(self, path):
        self.opened.append(path)
        if not self.available:
            return None
        rows = [('icons', _Section(None)), ('xmlns:xmlref', _Section('gold'))]
        rows.extend((name, _Section(currency))
                    for name, currency in sorted(self.sections.items()))
        return types.SimpleNamespace(items=lambda: list(rows))

    def purge(self, path, recursive):
        self.purged.append((path, recursive))


class ShellPriceTests(unittest.TestCase):
    """``Shell.isGold`` is cell-app only, so the flag comes from item defs."""

    def setUp(self):
        reset_shell_price_cache()
        self.addCleanup(reset_shell_price_cache)

    def test_gold_priced_shells_come_from_the_exact_resource(self):
        res_mgr = _ResMgr({'_37mm_UBR-160P': 'gold', '_37mm_UBR-167': None})

        names = gold_shell_names('ussr', res_mgr)

        self.assertEqual(set(('_37mm_UBR-160P',)), names)
        self.assertEqual([SHELLS_XML_PATH % ('ussr',)], res_mgr.opened)
        self.assertEqual([(SHELLS_XML_PATH % ('ussr',), True)],
                         res_mgr.purged)

    def test_non_shell_sections_are_skipped_like_the_stock_reader(self):
        res_mgr = _ResMgr({'_85mm_UBR-365K': None})

        self.assertEqual(set(), gold_shell_names('ussr', res_mgr))

    def test_one_nation_is_resolved_once(self):
        res_mgr = _ResMgr({'_37mm_UBR-160P': 'gold'})

        self.assertTrue(is_gold_shell('ussr', '_37mm_UBR-160P', res_mgr))
        self.assertFalse(is_gold_shell('ussr', '_37mm_UBR-167', res_mgr))

        self.assertEqual(1, len(res_mgr.opened))

    def test_an_unavailable_resource_is_contained(self):
        res_mgr = _ResMgr({}, available=False)

        self.assertIsNone(gold_shell_names('ussr', res_mgr))
        self.assertFalse(is_gold_shell('ussr', '_37mm_UBR-160P', res_mgr))
        self.assertEqual(1, len(res_mgr.opened))

    def test_a_failing_resource_is_contained(self):
        class _Broken(object):
            def openSection(self, unused_path):
                raise RuntimeError('resource subsystem is down')

        self.assertIsNone(gold_shell_names('ussr', _Broken()))
        self.assertFalse(is_gold_shell('ussr', '_37mm_UBR-160P', _Broken()))

    def test_the_cache_survives_without_a_purge_hook(self):
        class _NoPurge(object):
            def openSection(self, unused_path):
                return types.SimpleNamespace(
                    items=lambda: [('_37mm_UBR-160P', _Section('gold'))])

        self.assertTrue(is_gold_shell('ussr', '_37mm_UBR-160P', _NoPurge()))

    def test_the_resource_path_matches_the_stock_component_layout(self):
        self.assertEqual(
            'scripts/item_defs/vehicles/ussr/components/shells.xml',
            battle_feedback.SHELLS_XML_PATH % ('ussr',))


if __name__ == '__main__':
    unittest.main()
