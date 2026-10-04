import os
import sys
import unittest


CLIENT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'src', 'res', 'scripts', 'client'))
if CLIENT_ROOT not in sys.path:
    sys.path.insert(0, CLIENT_ROOT)

from gui.mods.offline_lan_0922.siege_hud import PersistentSiegeHints
from gui.mods.offline_lan_0922 import siege_mechanics


def _indicator_type():
    class SiegeModeIndicator(object):
        """Stock quota, event routing and visibility guards from 0.9.22.

        The source reference is regional #788, not exact #1513 bytecode.
        Rendering calls are captured here; Windows still owns visual proof.
        """

        def __init__(self, provider, hints_left):
            self.sessionProvider = provider
            self._hintsLeft = hints_left
            self._isInPostmortem = False
            self._isObserver = False
            self._isEnabled = True
            self._siegeState = 0
            self._switchTime = 0.0
            self._switchTimeTable = {
                state: {'normal': duration, 'critical': duration * 2.0,
                        'destroyed': 0.0}
                for state, duration in ((0, 2.0), (1, 2.0),
                                        (2, 1.3), (3, 1.3))}
            self._devices = {'engine': 'normal'}
            self._isHintShown = False
            self.key = 'X'
            self.calls = []
            self.persisted = []
            self.fail_hint = False
            self.view_calls = []
            self.displayed_time = '- -'

        def __updateHintView(self):
            if self._isInPostmortem or self._isObserver:
                return
            if self.fail_hint:
                raise RuntimeError('Flash dispatch failed')
            if self._siegeState not in (1, 3) and self._hintsLeft:
                self.calls.append(('show', self.key, self._siegeState))
                self._isHintShown = True
            elif self._isHintShown:
                self.calls.append(('hide',))
                self._isHintShown = False

        def __updateSiegeState(self, state, time_left):
            if state == 3:
                if not self._isObserver and not self._isInPostmortem:
                    self._hintsLeft = max(0, self._hintsLeft - 1)
            self._siegeState = state
            self._switchTime = time_left
            self.__updateIndicatorView()

        def __updateIndicatorView(self, is_smooth=False):
            engine = self._devices['engine']
            total_time = self._switchTimeTable[self._siegeState][engine]
            self.view_calls.append((total_time, self._switchTime,
                                    self._siegeState, engine, is_smooth))
            # SiegeModePanel.setEngineAndTime uses the SECOND argument for
            # both the next-switch label and an active countdown.
            self.displayed_time = (
                '%.1f' % self._switchTime
                if engine != 'destroyed' and self._switchTime > 0 else '- -')
            self.__updateHintView()

        def on_state(self, state, time_left=0.0):
            if self._isEnabled:
                self.__updateSiegeState(state, time_left)

        def on_key_binding_changed(self):
            if self._isEnabled:
                self.__updateHintView()

        def destroy(self):
            self._isEnabled = False

        def dispose(self):
            self.persisted.append(self._hintsLeft)

    return SiegeModeIndicator


class PersistentSiegeHintTests(unittest.TestCase):
    def setUp(self):
        self.indicator_type = _indicator_type()
        self.provider = object()
        self.hints = PersistentSiegeHints(self.indicator_type, self.provider)
        self.addCleanup(self.hints.close)

    def test_exhausted_hint_quota_survives_more_than_ten_round_trips(self):
        indicator = self.indicator_type(self.provider, 0)
        self.assertTrue(self.hints.install())
        self.assertFalse(self.hints.install())
        indicator.on_state(0)
        self.assertTrue(indicator._isHintShown)
        for unused_cycle in range(15):
            for state in (1, 2, 3, 0):
                indicator.on_state(state, 2.0 if state in (1, 3) else 0.0)
                self.assertEqual(state in (0, 2), indicator._isHintShown)
                self.assertEqual(0, indicator._hintsLeft)
        indicator.key = 'Y'
        indicator.on_key_binding_changed()
        self.assertEqual(('show', 'Y', 0), indicator.calls[-1])
        indicator.dispose()
        self.assertEqual([0], indicator.persisted)

    def test_existing_persisted_quota_is_neither_spent_nor_increased(self):
        indicator = self.indicator_type(self.provider, 7)
        self.hints.install()
        for unused_cycle in range(15):
            for state in (1, 2, 3, 0):
                indicator.on_state(state)
        indicator.dispose()
        self.assertEqual([7], indicator.persisted)

    def test_native_death_observer_and_postmortem_guards_are_preserved(self):
        self.hints.install()
        for mode in ('dead', 'observer', 'postmortem'):
            with self.subTest(mode=mode):
                indicator = self.indicator_type(self.provider, 0)
                if mode == 'dead':
                    indicator.destroy()
                elif mode == 'observer':
                    indicator._isObserver = True
                else:
                    indicator._isInPostmortem = True
                indicator.on_state(0)
                indicator.on_key_binding_changed()
                self.assertEqual([], indicator.calls)
                self.assertEqual(0, indicator._hintsLeft)

    def test_other_session_keeps_stock_finite_hint_policy(self):
        indicator = self.indicator_type(object(), 1)
        self.hints.install()
        indicator.on_state(0)
        self.assertTrue(indicator._isHintShown)
        indicator.on_state(3)
        indicator.on_state(0)
        self.assertFalse(indicator._isHintShown)
        self.assertEqual(0, indicator._hintsLeft)

    def test_exception_restores_counter_and_unload_restores_all_methods(self):
        methods = (PersistentSiegeHints._HINT_METHOD,
                   PersistentSiegeHints._STATE_METHOD,
                   PersistentSiegeHints._VIEW_METHOD)
        original = {name: self.indicator_type.__dict__[name]
                    for name in methods}
        indicator = self.indicator_type(self.provider, 7)
        self.hints.install()
        indicator.fail_hint = True
        with self.assertRaisesRegex(RuntimeError, 'Flash dispatch failed'):
            indicator.on_state(3)
        self.assertEqual(7, indicator._hintsLeft)
        indicator.fail_hint = False
        self.hints.close()
        self.hints.close()
        for name in methods:
            self.assertIs(original[name], self.indicator_type.__dict__[name])
        indicator.on_state(3)
        self.assertEqual(6, indicator._hintsLeft)

    def test_stable_modes_show_the_next_descriptor_duration_for_all_siege_tds(self):
        self.hints.install()
        for name, params in siege_mechanics.VEHICLE_PARAMS.items():
            with self.subTest(vehicle=name):
                indicator = self.indicator_type(self.provider, 0)
                for state, duration in ((0, params[0]), (1, params[0]),
                                        (2, params[1]), (3, params[1])):
                    indicator._switchTimeTable[state]['normal'] = duration
                indicator.on_state(0, 0.0)
                self.assertEqual('2.0', indicator.displayed_time)
                self.assertEqual(0.0, indicator._switchTime)
                indicator.on_state(2, 0.0)
                self.assertEqual('2.0' if 'UDES' in name else '1.3',
                                 indicator.displayed_time)
                self.assertEqual(0.0, indicator._switchTime)

    def test_switching_modes_preserve_the_authoritative_remaining_time(self):
        indicator = self.indicator_type(self.provider, 0)
        self.hints.install()
        for state, remaining in ((1, 1.6), (3, 0.8)):
            indicator.on_state(state, remaining)
            self.assertEqual(remaining, indicator.view_calls[-1][1])
            self.assertEqual(remaining, indicator._switchTime)
            self.assertEqual('%.1f' % remaining, indicator.displayed_time)

    def test_timer_uses_engine_state_and_the_native_destroyed_guard(self):
        indicator = self.indicator_type(self.provider, 0)
        self.hints.install()
        indicator._devices['engine'] = 'critical'
        indicator.on_state(0)
        self.assertEqual('4.0', indicator.displayed_time)
        indicator.on_state(2)
        self.assertEqual('2.6', indicator.displayed_time)
        indicator._devices['engine'] = 'destroyed'
        indicator.on_state(2)
        self.assertEqual('- -', indicator.displayed_time)

    def test_timer_has_no_effect_on_other_sessions_and_restores_after_exception(self):
        self.hints.install()
        indicator = self.indicator_type(object(), 0)
        indicator.on_state(0)
        self.assertEqual('- -', indicator.displayed_time)
        indicator = self.indicator_type(self.provider, 0)
        indicator.fail_hint = True
        with self.assertRaisesRegex(RuntimeError, 'Flash dispatch failed'):
            indicator.on_state(0)
        self.assertEqual(0.0, indicator._switchTime)
        self.hints.close()
        indicator.fail_hint = False
        indicator.on_state(0)
        self.assertEqual('- -', indicator.displayed_time)

    def test_unload_preserves_a_later_hook_and_retires_our_retained_wrapper(self):
        self.hints.install()
        name = PersistentSiegeHints._HINT_METHOD
        previous = self.indicator_type.__dict__[name]

        def newer(indicator):
            return previous(indicator)

        setattr(self.indicator_type, name, newer)
        self.hints.close()
        self.assertIs(newer, self.indicator_type.__dict__[name])
        indicator = self.indicator_type(self.provider, 0)
        indicator.on_state(0)
        self.assertFalse(indicator._isHintShown)


if __name__ == '__main__':
    unittest.main()
