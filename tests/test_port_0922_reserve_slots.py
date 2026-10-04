"""Reserve activation, header and Flash layout must share the three-slot limit."""

import unittest

import test_port_0922_garage as fixture
from test_port_0922_offline_services_ui import native_modules


class ReserveSlotTests(unittest.TestCase):
    def test_preimported_single_slot_consumers_are_updated_and_restored(self):
        ui = fixture._load_port_module('offline_services_ui')
        self.addCleanup(ui.uninstall)
        names = (
            'gui.goodies.goodie_items',
            'gui.Scaleform.daapi.view.lobby.boosters.BoostersWindow',
            'gui.Scaleform.daapi.view.lobby.boosters.BoostersPanelComponent')
        layout = {'slotsCount': 1, 'slotWidth': 50, 'paddings': 64}
        exports = dict((name, {'MAX_ACTIVE_BOOSTERS_COUNT': 1}) for name in names)
        exports[names[2]]['_GUI_SLOTS_PROPS'] = layout
        with native_modules(exports) as modules:
            goodie, window, panel = (modules[name] for name in names)
            # These consumer expressions follow 0.9.22 #788's Python
            # contracts. Seed each independently with the reported one-slot
            # limit: changing only the producer must not pass this test.
            exec('''
def ready(count, inactive, active_types, resource):
    return count > 0 and inactive and len(active_types) < MAX_ACTIVE_BOOSTERS_COUNT and resource not in active_types
''', goodie.__dict__)
            exec('''
def active_text(active):
    return '%d/%d' % (active, MAX_ACTIVE_BOOSTERS_COUNT)
''', window.__dict__)
            exec('''
def build_slots(active):
    active_count = min(len(active), MAX_ACTIVE_BOOSTERS_COUNT)
    result = list(active[:active_count])
    result.extend([None for idx in range(active_count, MAX_ACTIVE_BOOSTERS_COUNT)])
    return _GUI_SLOTS_PROPS, result
''', panel.__dict__)
            self.assertFalse(goodie.ready(1, True, ['xp'], 'credits'))
            self.assertEqual('1/1', window.active_text(1))
            self.assertEqual((layout, ['xp']), panel.build_slots(['xp']))

            ui._install_reserve_slots()
            self.assertEqual('1/3', window.active_text(1))
            self.assertTrue(goodie.ready(1, True, ['xp'], 'credits'))
            self.assertTrue(goodie.ready(1, True, ['xp', 'credits'], 'crew_xp'))
            self.assertFalse(goodie.ready(1, True, ['xp'], 'xp'))
            self.assertFalse(goodie.ready(1, True,
                ['xp', 'credits', 'crew_xp'], 'free_xp'))
            self.assertFalse(goodie.ready(0, True, [], 'xp'))
            self.assertFalse(goodie.ready(1, False, [], 'xp'))
            for active in ([], ['xp'], ['xp', 'credits'],
                           ['xp', 'credits', 'crew_xp']):
                props, slots = panel.build_slots(active)
                self.assertEqual(3, props['slotsCount'])
                self.assertEqual(3, len(slots))
                self.assertEqual(active, slots[:len(active)])
                self.assertEqual(50, props['slotWidth'])
                self.assertEqual(64, props['paddings'])
            self.assertEqual(1, layout['slotsCount'])

            ui.uninstall()
            self.assertEqual('1/1', window.active_text(1))
            self.assertFalse(goodie.ready(1, True, ['xp'], 'credits'))
            self.assertIs(layout, panel._GUI_SLOTS_PROPS)
            self.assertEqual((layout, ['xp']), panel.build_slots(['xp']))


if __name__ == '__main__':
    unittest.main()
