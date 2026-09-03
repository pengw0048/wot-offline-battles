import contextlib
import copy
import importlib.util
import io
import os
import ast
import shutil
import tempfile
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load(name):
    for parent in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922',
                   'gui.mods.offline_lan_0922.account_rpc'):
        if parent not in sys.modules:
            module = types.ModuleType(parent)
            module.__path__ = [str(PACKAGE_ROOT)] if parent.endswith(
                'offline_lan_0922') else [str(PACKAGE_ROOT / 'account_rpc')]
            sys.modules[parent] = module
    sys.modules['gui.mods.offline_lan_0922'].__path__ = [str(PACKAGE_ROOT)]
    sys.modules['gui.mods.offline_lan_0922.account_rpc'].__path__ = [
        str(PACKAGE_ROOT / 'account_rpc')]
    full = 'gui.mods.offline_lan_0922.account_rpc.%s' % name
    sys.modules.pop(full, None)
    spec = importlib.util.spec_from_file_location(
        full, PACKAGE_ROOT / 'account_rpc' / ('%s.py' % name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def _request_modules():
    """Load the handlers and reuse the exact module graph they bound.

    Loading a dependency separately would hand the test a different
    ``GarageError`` class than the handler catches.
    """
    requests = _load('requests')
    return requests, requests.commands, requests.garage


SNAPSHOT = {
    'vehicles': [{
        'id': 9,
        'compDescr': b'veh:9',
        'crew': [101, 102],
        'tankmen': {101: b'tman:101', 102: b'tman:102'},
        'repair': (0, 100),
        'lock': (0, 0),
        'shells': [10010, 20, 10011, 10],
        'shellsLayout': {(7001, 7002): [10010, 20, 10011, 10]},
        'shellsLayoutIdx': (7001, 7002),
        'eqs': [0, 0, 0],
        'eqsLayout': [0, 0, 0],
        'inventoryItems': {
            2: {2002: 1}, 3: {2003: 1}, 4: {2004: 1},
            5: {2005: 1}, 6: {2006: 1}, 7: {2007: 1},
            10: {10010: 20, 10011: 10},
        },
        'vehicleTypeCompactDescr': 50001,
    }],
    # bootstrap's top-level catalogue covers every per-record item plus
    # the account-wide device and equipment stock.
    'inventoryItems': {
        2: {2002: 1}, 3: {2003: 1}, 4: {2004: 1},
        5: {2005: 1}, 6: {2006: 1}, 7: {2007: 1},
        9: {9001: 200, 9002: 200},
        10: {10010: 20, 10011: 10},
        11: {11001: 200},
    },
    'shopItemPrices': dict(
        (compact_descr, {'credits': 0, 'gold': 0})
        for compact_descr in (2002, 2003, 2004, 2005, 2006, 2007,
                              10010, 10011, 9001, 9002, 11001, 50001)),
    'unlockItemCompactDescrs': set(),
    'shopNationCount': 9,
    'customizationItemCount': 1,
}


class _Component(object):
    def __init__(self, compact_descr):
        self.compactDescr = compact_descr


class _Descriptor(object):
    def __init__(self, compact_descr):
        self.compact_descr = compact_descr
        self.devices = {}
        self.components = {}
        # #1513 Vehicle.shellsLayoutIdx reads both compact descriptors.
        self.turret = _Component(7001)
        self.turret.maxAmmo = 45
        self._set_gun(7002)
        try:
            encoded = compact_descr.decode('ascii')
        except AttributeError:
            encoded = str(compact_descr)
        for field in encoded.split('|')[1:]:
            if field.startswith('dev='):
                self.devices = dict(ast.literal_eval(field[4:]))
            elif field.startswith('comp='):
                self.components = dict(ast.literal_eval(field[5:]))
            elif field.startswith('turret='):
                self.turret = _Component(int(field[7:]))
                self.turret.maxAmmo = 45
            elif field.startswith('gun='):
                self._set_gun(int(field[4:]))

    def _set_gun(self, compact_descr):
        self.gun = _Component(compact_descr)
        self.gun.maxAmmo = 45
        self.gun.shots = tuple(
            types.SimpleNamespace(shell=_Component(shell_compact_descr))
            for shell_compact_descr in (20010, 20011))

    def installOptionalDevice(self, compact_descr, slot_index):
        if slot_index in self.devices:
            raise ValueError('slot is occupied')
        self.devices[slot_index] = compact_descr

    def removeOptionalDevice(self, slot_index):
        if slot_index not in self.devices:
            raise ValueError('slot is empty')
        del self.devices[slot_index]

    def installComponent(self, compact_descr, position_index):
        # #1513 installComponent ends in ``assert False`` for a turret.
        if compact_descr // 1000 == 3:
            raise AssertionError(compact_descr)
        self.components[position_index] = compact_descr
        self._set_gun(compact_descr)

    def installTurret(self, turret_compact_descr, gun_compact_descr,
                      position_index):
        self.components[position_index] = turret_compact_descr
        self.turret = _Component(turret_compact_descr)
        self.turret.maxAmmo = 45
        if gun_compact_descr:
            self._set_gun(gun_compact_descr)

    def makeCompactDescr(self):
        return b'veh:9|dev=%s|comp=%s|turret=%d|gun=%d' % (
            repr(sorted(self.devices.items())).encode('ascii'),
            repr(sorted(self.components.items())).encode('ascii'),
            self.turret.compactDescr, self.gun.compactDescr)


class _TankmanDescriptor(object):
    def __init__(self, compact_descr):
        # The real TankmanDescr parses its skills out of the compact
        # descriptor, so the fake must round-trip them too.
        base, _, encoded = compact_descr.partition(b'|')
        encoded, _, xp = encoded.partition(b'#')
        self.compact_descr = base
        self.skills = [name.decode('ascii')
                       for name in encoded.split(b',') if name]
        self.free_xp = int(xp or 0)

    def addSkill(self, name):
        if name in self.skills:
            raise ValueError('already learned')
        self.skills.append(name)

    def dropSkills(self, fraction, throw):
        self.skills = []

    def addXP(self, amount):
        self.free_xp += int(amount)

    def totalXP(self):
        return self.free_xp

    def makeCompactDescr(self):
        result = self.compact_descr + b'|' + ','.join(
            self.skills).encode('ascii')
        return result if not self.free_xp else (
            result + b'#' + str(self.free_xp).encode('ascii'))


def _modules():
    def item_type(compact_descr):
        # 3 turret, 9 optionalDevice, 10 shell, 11 equipment, else a module.
        return {3: 3, 9: 9, 10: 10, 11: 11}.get(compact_descr // 1000, 4)

    vehicles = types.SimpleNamespace(
        VehicleDescr=lambda compactDescr: _Descriptor(compactDescr),
        getDefaultAmmoForGun=lambda gun: [20010, 30, 20011, 15],
        getTypeOfCompactDescr=item_type)
    skill_names = [
        'unused_skill_%d' % index for index in range(61)]
    skill_names[:3] = ['repair', 'camouflage', 'brotherhood']
    skill_names[48] = 'loader_intuition'
    tankmen = types.SimpleNamespace(
        TankmanDescr=_TankmanDescriptor,
        SKILL_NAMES=tuple(skill_names))
    return vehicles, tankmen


class _ParsedOutfit(object):
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def makeCompDescr(self):
        return self.descriptor


class _StyleOutfit(_ParsedOutfit):
    def __init__(self, styleId=0):
        _ParsedOutfit.__init__(self, b'style:%d' % styleId)


class _Customizations(object):
    CustomizationOutfit = _StyleOutfit

    @staticmethod
    def parseOutfitDescr(descriptor):
        if not isinstance(descriptor, bytes) or not descriptor.startswith(
                (b'outfit:', b'style:')):
            raise ValueError('bad stock descriptor')
        return _ParsedOutfit(descriptor)

    @staticmethod
    def parseIntCompactDescr(compact_descr):
        return 12, 1, compact_descr % 1000


class GarageStateTests(unittest.TestCase):

    def setUp(self):
        unused_requests, unused_commands, self.garage = _request_modules()
        vehicles, tankmen = _modules()
        self.state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)

    def _record(self):
        return self.state.snapshot()['vehicles'][0]

    def test_the_snapshot_is_copied_not_aliased(self):
        self.state.equip_equipments(9, [11001, 0, 0])

        self.assertEqual([0, 0, 0], SNAPSHOT['vehicles'][0]['eqs'])

    def test_mounting_consumables_fills_three_slots(self):
        self.state.equip_equipments(9, [11001])

        self.assertEqual([11001, 0, 0], self._record()['eqs'])
        self.assertEqual(
            1, self._record()['inventoryItems'][11][11001])

    def test_the_trailing_battle_booster_slot_is_accepted_and_dropped(self):
        # VehicleEquipment.getConsumablesIntCDs appends the booster slot.
        self.state.equip_equipments(9, [11001, 0, 0, 11002])

        self.assertEqual([11001, 0, 0], self._record()['eqs'])

    def test_mounting_a_fifth_consumable_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.equip_equipments(9, [1, 2, 3, 4, 5])

    def test_shell_counts_keep_the_inventory_and_pair_list_in_step(self):
        self.state.equip_shells(9, [10010, 5, 10011, 40])

        self.assertEqual([10010, 5, 10011, 40], self._record()['shells'])
        self.assertEqual(
            {10010: 5, 10011: 40},
            self._record()['inventoryItems'][10])

    def test_odd_shell_payload_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.equip_shells(9, [10010, 5, 10011])

    def test_mounting_an_optional_device_rebuilds_the_compact_descriptor(self):
        original = self._record()['compDescr']

        self.state.equip_optional_device(9, 9001, 0)

        self.assertNotEqual(original, self._record()['compDescr'])
        self.assertIn(b'9001', self._record()['compDescr'])
        self.assertEqual(1, self._record()['inventoryItems'][9][9001])

    def test_remounting_the_same_slot_replaces_the_device(self):
        self.state.equip_optional_device(9, 9001, 0)
        self.state.equip_optional_device(9, 9002, 0)

        descriptor = self._record()['compDescr']
        self.assertIn(b'9002', descriptor)
        self.assertNotIn(b'9001', descriptor)

    def test_clearing_a_slot_removes_the_device(self):
        self.state.equip_optional_device(9, 9001, 0)
        self.state.equip_optional_device(9, 0, 0)

        self.assertNotIn(b'9001', self._record()['compDescr'])

    def test_a_turret_swap_carries_its_gun_through_install_turret(self):
        self.state.install_component(9, 3333, 4444)

        record = self._record()
        self.assertIn(b'3333', record['compDescr'])
        self.assertEqual((3333, 4444), record['shellsLayoutIdx'])

    def test_a_gun_swap_refills_the_default_ammunition(self):
        self.state.install_component(9, 4444)

        self.assertIn(b'4444', self._record()['compDescr'])
        self.assertEqual([20010, 30, 20011, 15], self._record()['shells'])
        self.assertEqual(
            {20010: 30, 20011: 15},
            self._record()['inventoryItems'][10])

    def test_a_descriptor_failure_leaves_the_old_fitting_and_ammo_intact(self):
        vehicles, tankmen = _modules()

        class RefusedDescriptor(_Descriptor):
            def makeCompactDescr(self):
                raise ValueError('invalid module combination')

        vehicles.VehicleDescr = lambda compactDescr: RefusedDescriptor(
            compactDescr)
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.install_component(9, 4444)

        self.assertEqual(before, state.snapshot())
        self.assertEqual(0, state.revision)
        self.assertEqual(set(), state.touched_vehicles())
        self.assertEqual({}, state.touched_items())

    def test_incompatible_default_ammo_leaves_the_old_fitting_intact(self):
        vehicles, tankmen = _modules()
        vehicles.getDefaultAmmoForGun = lambda gun: [29999, 45]
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.install_component(9, 4444)

        self.assertEqual(before, state.snapshot())
        self.assertEqual(0, state.revision)
        self.assertEqual(set(), state.touched_vehicles())
        self.assertEqual({}, state.touched_items())

    def test_zero_default_ammo_leaves_the_old_fitting_intact(self):
        vehicles, tankmen = _modules()
        vehicles.getDefaultAmmoForGun = lambda gun: [20010, 0]
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.install_component(9, 4444)

        self.assertEqual(before, state.snapshot())
        self.assertEqual(0, state.revision)

    def test_a_turret_cannot_silently_fall_back_to_the_stock_gun(self):
        vehicles, tankmen = _modules()

        class StockGunDescriptor(_Descriptor):
            def installTurret(self, turret_compact_descr, gun_compact_descr,
                              position_index):
                _Descriptor.installTurret(
                    self, turret_compact_descr, 0, position_index)

        vehicles.VehicleDescr = lambda compactDescr: StockGunDescriptor(
            compactDescr)
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.install_component(9, 3333, 4444)

        self.assertEqual(before, state.snapshot())
        self.assertEqual(0, state.revision)

    def test_a_crew_skill_rebuilds_only_that_tankman(self):
        self.state.add_tankman_skill(101, 2)

        self.assertEqual(
            b'tman:101|brotherhood', self._record()['tankmen'][101])
        self.assertEqual(b'tman:102', self._record()['tankmen'][102])

    def test_an_unknown_skill_index_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.add_tankman_skill(101, 99)

    def test_an_unknown_tankman_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.add_tankman_skill(999, 0)

    def test_dropping_skills_clears_them(self):
        self.state.add_tankman_skill(101, 0)
        self.state.drop_tankman_skills(101)

        self.assertEqual(b'tman:101|', self._record()['tankmen'][101])

    def test_an_unknown_vehicle_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.equip_equipments(4242, [11001])

    def test_layouts_decode_shell_pairs_and_equipment_slots(self):
        self.state.set_layouts(
            9, [10010, 12], 0, [11001, 1, 0, 0, 0, 0, 0, 0])

        self.assertEqual({(7001, 7002): [10010, 12]},
                         self._record()['shellsLayout'])
        self.assertEqual([11001, 0, 0], self._record()['eqsLayout'])

    def test_applying_a_layout_also_loads_the_vehicle(self):
        self.state.set_layouts(
            9, [10010, 12], 0, [11001, 1, 0, 0, 0, 0, 0, 0])

        self.assertEqual([11001, 0, 0], self._record()['eqs'])
        self.assertEqual([10010, 12], self._record()['shells'])
        self.assertEqual({10010: 12}, self._record()['inventoryItems'][10])

    def test_an_alternative_price_descriptor_is_stored_unsigned(self):
        # account_shared.LayoutIterator reads a negative descriptor as
        # "buy for the alternative price" and takes its absolute value.
        self.state.set_layouts(
            9, [-10010, 12], 0, [-11001, 1, 0, 0, 0, 0, 0, 0])

        self.assertEqual({(7001, 7002): [10010, 12]},
                         self._record()['shellsLayout'])
        self.assertEqual([11001, 0, 0], self._record()['eqs'])

    def test_a_setting_is_a_flag_value_not_a_bit_index(self):
        # VEHICLE_SETTINGS_FLAG.AUTO_REPAIR is the value #1513 sends.
        self.state.change_vehicle_setting(9, 2, 1)

        self.assertEqual(2, self._record()['settings'])

    def test_clearing_a_setting_leaves_the_other_flags_alone(self):
        self.state.change_vehicle_setting(9, 2, 1)
        self.state.change_vehicle_setting(9, 4, 1)

        self.state.change_vehicle_setting(9, 2, 0)

        self.assertEqual(4, self._record()['settings'])

    def test_mounting_a_consumable_keeps_the_layout_in_step(self):
        # Vehicle.isAutoEquipFull warns whenever the two disagree.
        self.state.equip_equipments(9, [11001, 0, 0])

        record = self._record()
        self.assertEqual(record['eqs'], record['eqsLayout'])

    def test_loading_shells_keeps_the_layout_in_step(self):
        self.state.equip_shells(9, [10010, 5, 10011, 40])

        record = self._record()
        self.assertEqual({(7001, 7002): [10010, 5, 10011, 40]},
                         record['shellsLayout'])

    def test_a_gun_swap_rekeys_the_ammunition_layout(self):
        self.state.install_component(9, 4444)

        record = self._record()
        self.assertEqual((7001, 4444), record['shellsLayoutIdx'])
        self.assertEqual({(7001, 4444): record['shells']},
                         record['shellsLayout'])

    def test_a_battle_booster_layout_leaves_the_regular_slots_alone(self):
        self.state.equip_equipments(9, [11001, 0, 0])

        self.state.set_layouts(9, None, 1, [0, 0, 0, 0, 0, 0, 11002, 1])

        self.assertEqual([11001, 0, 0], self._record()['eqs'])

    def test_an_odd_equipment_layout_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.set_layouts(9, None, 0, [11001, 1, 0])

    def test_outfit_is_stock_parsed_and_committed_atomically(self):
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen,
            customizations_module=_Customizations)

        state.apply_outfit(9, 2, b'outfit:summer')

        self.assertEqual(
            (b'outfit:summer', True),
            state.snapshot()['vehicles'][0]['outfits'][2])

    def test_refused_outfit_cannot_partially_replace_live_record(self):
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen,
            customizations_module=_Customizations)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.apply_outfit(9, 2, b'not-an-outfit')

        self.assertEqual(before, state.snapshot())

    def test_customization_inventory_uses_exact_outfit_shape(self):
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen,
            customizations_module=_Customizations)
        state.buy_customizations(9, [12001, 3])
        state.apply_outfit(9, 2, b'outfit:summer')
        data = _load('data').inventory(state.snapshot(), validate=False)

        self.assertEqual(
            {50001: 3}, data['inventory'][12][1][1][1])
        self.assertEqual(
            (b'outfit:summer', True),
            data['inventory'][12][2][50001][2])


class FittingRequestTests(unittest.TestCase):

    def setUp(self):
        self.requests, self.commands, self.garage = _request_modules()
        vehicles, tankmen = _modules()
        self.pushed = []
        self.state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)
        self.context = {
            'selected_vehicle': copy.deepcopy(SNAPSHOT),
            'garage': self.state,
            'push_update': self.pushed.append,
        }

    def _dispatch(self, command, args):
        return self.requests.dispatch(command, self.context, args)

    def _customization_state(self):
        vehicles, tankmen = _modules()
        vehicles.g_cache = types.SimpleNamespace(
            customization20=lambda: types.SimpleNamespace(styles={7: object()}))
        self.state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen,
            customizations_module=_Customizations)
        self.context['garage'] = self.state
        return self.state

    def test_equip_eqs_decodes_the_exact_1513_payload(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_EQS, ([9, 11001, 0, 0],))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        result.before_response()
        self.assertEqual(1, len(self.pushed))
        self.assertEqual(
            [11001, 0, 0], self.pushed[0]['inventory'][1]['eqs'][9])

    def test_cmd_119_decodes_and_publishes_the_exact_outfit_payload(self):
        self._customization_state()

        result = self._dispatch(
            self.commands.CMD_VEH_APPLY_OUTFIT,
            ([77, 9, 2], [b'outfit:summer']))
        result.before_response()

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            (b'outfit:summer', True),
            self.pushed[0]['inventory'][12][2][50001][2])

    def test_cmd_118_and_117_update_vehicle_bound_ownership(self):
        self._customization_state()

        bought = self._dispatch(
            self.commands.CMD_BUY_C11N_ITEMS, ([77, 9, 12001, 3],))
        sold = self._dispatch(
            self.commands.CMD_SELL_C11N_ITEMS, (77, 12001, 2, 9))

        self.assertEqual(self.commands.RES_SUCCESS, bought.result_id)
        self.assertEqual(self.commands.RES_SUCCESS, sold.result_id)
        self.assertEqual(
            1, self.state.snapshot()['customizationItems'][1][1][50001])

    def test_cmd_116_uses_the_stock_style_serializer(self):
        self._customization_state()

        result = self._dispatch(
            self.commands.CMD_VEH_APPLY_STYLE, (77, 9, 7))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            (b'style:7', True),
            self.state.snapshot()['vehicles'][0]['outfits'][15])

    def test_custom_outfit_replaces_the_style_that_would_mask_it(self):
        self._customization_state()
        self._dispatch(self.commands.CMD_VEH_APPLY_STYLE, (77, 9, 7))

        result = self._dispatch(
            self.commands.CMD_VEH_APPLY_OUTFIT,
            ([77, 9, 2], [b'outfit:summer']))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            {2: (b'outfit:summer', True)},
            self.state.snapshot()['vehicles'][0]['outfits'])

    def test_equip_optdev_skips_the_leading_shop_revision(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_OPTDEV, ([77, 9, 9001, 1, 0],))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertIn(
            b'9001', self.state.snapshot()['vehicles'][0]['compDescr'])

    def test_buy_and_equip_carries_the_selected_gun_for_a_turret(self):
        result = self._dispatch(
            self.commands.CMD_BUY_AND_EQUIP_ITEM,
            ([77, 3333, 9, 0, 0, 4444],))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        record = self.state.snapshot()['vehicles'][0]
        self.assertEqual((3333, 4444), record['shellsLayoutIdx'])
        self.assertEqual([20010, 30, 20011, 15], record['shells'])

    def test_refused_buy_and_equip_does_not_publish_new_ownership(self):
        vehicles, tankmen = _modules()
        vehicles.getDefaultAmmoForGun = lambda gun: [29999, 45]
        self.state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen)
        self.context['garage'] = self.state
        before = copy.deepcopy(self.state.snapshot())

        with contextlib.redirect_stdout(io.StringIO()):
            result = self._dispatch(
                self.commands.CMD_BUY_AND_EQUIP_ITEM,
                ([77, 3333, 9, 0, 0, 4444],))

        self.assertEqual(self.commands.RES_FAILURE, result.result_id)
        self.assertEqual(before, self.state.snapshot())
        self.assertNotIn(
            3333, self.state.snapshot()['inventoryItems'].get(3, {}))
        self.assertEqual([], self.pushed)

    def test_equip_shells_updates_the_published_inventory(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_SHELLS, ([9, 10010, 7],))
        result.before_response()

        self.assertEqual(
            [10010, 7], self.pushed[0]['inventory'][1]['shells'][9])
        self.assertEqual({10010: 7}, self.pushed[0]['inventory'][10])

    def test_add_skill_uses_the_int3_payload(self):
        result = self._dispatch(self.commands.CMD_TMAN_ADD_SKILL, (101, 0, 0))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            b'tman:101|repair',
            self.state.snapshot()['vehicles'][0]['tankmen'][101])

    def test_drop_skills_uses_shop_revision_then_tankman_id(self):
        self.state.add_tankman_skill(101, 0)

        result = self._dispatch(
            self.commands.CMD_TMAN_DROP_SKILLS, (77, 101, 0))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            b'tman:101|',
            self.state.snapshot()['vehicles'][0]['tankmen'][101])

    def test_free_xp_training_uses_the_pinned_conversion_rate(self):
        result = self._dispatch(
            self.commands.CMD_TRAINING_TMAN, (77, 101, 25))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            b'tman:101|#250',
            self.state.snapshot()['vehicles'][0]['tankmen'][101])

    def test_set_and_fill_layouts_decodes_both_counted_blocks(self):
        # The exact payload TechnicalMaintenance sends when the player picks
        # one consumable: eight equipment values, four descriptor/count pairs.
        payload = [77, 9, 2, 10010, 12, 0,
                   8, 11001, 1, 0, 0, 0, 0, 0, 0]

        result = self._dispatch(
            self.commands.CMD_SET_AND_FILL_LAYOUTS, (payload,))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        record = self.state.snapshot()['vehicles'][0]
        self.assertEqual({(7001, 7002): [10010, 12]}, record['shellsLayout'])
        self.assertEqual([11001, 0, 0], record['eqsLayout'])
        self.assertEqual([11001, 0, 0], record['eqs'])

    def test_battle_xp_reaches_every_crew_member_and_accelerates_the_weakest(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['vehicles'][0]['settings'] = 1
        snapshot['vehicles'][0]['tankmen'][102] = b'tman:102|#50'
        vehicles, tankmen_module = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles,
            tankmen_module=tankmen_module)

        result = state.award_battle_crew_xp(50001, 100, 1)

        tankmen = state.snapshot()['vehicles'][0]['tankmen']
        self.assertTrue(result['accelerated'])
        self.assertEqual(101, result['weakest_tankman_id'])
        self.assertEqual(200, _TankmanDescriptor(tankmen[101]).totalXP())
        self.assertEqual(150, _TankmanDescriptor(tankmen[102]).totalXP())

    def test_battle_xp_does_not_accelerate_when_the_vehicle_setting_is_off(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['vehicles'][0]['settings'] = 0
        vehicles, tankmen_module = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles,
            tankmen_module=tankmen_module)

        result = state.award_battle_crew_xp(50001, 75, 1)

        self.assertFalse(result['accelerated'])
        self.assertEqual(0, result['weakest_tankman_id'])
        self.assertEqual(75, _TankmanDescriptor(
            state.snapshot()['vehicles'][0]['tankmen'][101]).totalXP())
        self.assertEqual(75, _TankmanDescriptor(
            state.snapshot()['vehicles'][0]['tankmen'][102]).totalXP())

    def test_mounting_a_consumable_from_the_ammunition_window_succeeds(self):
        payload = [77, 9, 4, 10010, 20, 10011, 10, 0,
                   8, 11001, 1, 0, 0, 0, 0, 0, 0]

        result = self._dispatch(
            self.commands.CMD_SET_AND_FILL_LAYOUTS, (payload,))
        result.before_response()

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            [11001, 0, 0], self.pushed[0]['inventory'][1]['eqs'][9])

    def test_equip_eqs_accepts_the_battle_booster_slot(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_EQS, ([9, 11001, 0, 0, 0],))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)

    def test_a_refused_fitting_returns_failure_and_pushes_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            result = self._dispatch(
                self.commands.CMD_EQUIP_EQS, ([4242, 11001],))

        self.assertEqual(self.commands.RES_FAILURE, result.result_id)
        self.assertEqual([], self.pushed)

    def test_a_refused_fitting_names_the_command_handler_and_shape(self):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self._dispatch(self.commands.CMD_EQUIP_EQS, ([4242, 11001],))

        line = log.getvalue()
        self.assertIn('command 104', line)
        self.assertIn('_equip_equipments', line)
        self.assertIn('unknown vehicle inventory id', line)
        self.assertIn('list[2]', line)
        self.assertNotIn('4242', line.split('payload')[1])

    def test_an_unsupported_command_is_logged_once(self):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            result = self._dispatch(999999, ())

        self.assertEqual(self.commands.RES_FAILURE, result.result_id)
        self.assertIn('command 999999', log.getvalue())
        self.assertIn('UNSUPPORTED_OFFLINE_COMMAND', log.getvalue())

    def test_a_malformed_payload_never_reaches_the_garage(self):
        for command in (self.commands.CMD_EQUIP_EQS,
                        self.commands.CMD_EQUIP_SHELLS,
                        self.commands.CMD_EQUIP_OPTDEV,
                        self.commands.CMD_SET_AND_FILL_LAYOUTS):
            with contextlib.redirect_stdout(io.StringIO()):
                result = self._dispatch(command, ([],))
            self.assertEqual(
                self.commands.RES_FAILURE, result.result_id, command)
        self.assertEqual([], self.pushed)

    def test_the_context_snapshot_follows_the_mutation(self):
        self._dispatch(self.commands.CMD_EQUIP_EQS, ([9, 11001, 0, 0],))

        self.assertIs(
            self.state.snapshot(), self.context['selected_vehicle'])


class GaragePersistenceTests(unittest.TestCase):
    """Persist -> reload -> same state, across a simulated client restart."""

    def setUp(self):
        unused_requests, unused_commands, self.garage = _request_modules()
        self.store_module = _load('garage_store')
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, 'garage_state.json')

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _state(self, snapshot=None):
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot if snapshot is not None else SNAPSHOT,
            vehicles_module=vehicles, tankmen_module=tankmen)

    def _store(self):
        return self.store_module.GarageStore(path=self.path)

    def _restart(self):
        """Rebuild the bootstrap snapshot and overlay the saved garage."""
        fresh = copy.deepcopy(SNAPSHOT)
        self._store().apply(fresh)
        return fresh

    def test_a_full_loadout_survives_a_restart(self):
        state = self._state()
        state.equip_optional_device(9, 9001, 0)
        state.equip_optional_device(9, 9002, 1)
        state.equip_equipments(9, [11001, 0, 0])
        state.equip_shells(9, [10010, 38, 10011, 9])
        state.set_layouts(
            9, [10010, 38, 10011, 9], 0, [11001, 1, 0, 0, 0, 0, 0, 0])
        state.add_tankman_skill(101, 2)
        # VEHICLE_SETTINGS_FLAG.AUTO_EQUIP, the value #1513 itself sends.
        state.change_vehicle_setting(9, 8, 1)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        restored = self._restart()['vehicles'][0]

        self.assertEqual(state.snapshot()['vehicles'][0]['compDescr'],
                         restored['compDescr'])
        self.assertEqual([11001, 0, 0], restored['eqs'])
        self.assertEqual([10010, 38, 10011, 9], restored['shells'])
        self.assertEqual({(7001, 7002): [10010, 38, 10011, 9]},
                         restored['shellsLayout'])
        self.assertEqual([11001, 0, 0], restored['eqsLayout'])
        self.assertEqual(8, restored['settings'])
        self.assertEqual(b'tman:101|brotherhood', restored['tankmen'][101])
        # The reloaded shells must still match the shell inventory that
        # data._validate_selected_vehicle cross-checks.
        self.assertEqual({10010: 38, 10011: 9},
                         restored['inventoryItems'][10])

    def test_an_alternate_gun_and_its_shells_survive_a_restart(self):
        state = self._state()
        state.install_component(9, 4444)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        # bootstrap publishes every mountable gun's ammunition in this
        # account-level whitelist even though only the mounted gun is loaded
        # on the per-vehicle row.
        fresh = copy.deepcopy(SNAPSHOT)
        fresh['inventoryItems'][10].update({20010: 30, 20011: 15})
        fresh['shopItemPrices'].update({
            20010: {'credits': 0, 'gold': 0},
            20011: {'credits': 0, 'gold': 0},
        })

        self.assertTrue(self._store().apply(fresh))
        restored = fresh['vehicles'][0]
        self.assertEqual((7001, 4444),
                         tuple(restored['shellsLayoutIdx']))
        self.assertEqual([20010, 30, 20011, 15], restored['shells'])
        self.assertEqual({20010: 30, 20011: 15},
                         restored['inventoryItems'][10])
        self.assertEqual(30, fresh['inventoryItems'][10][20010])
        self.assertEqual(15, fresh['inventoryItems'][10][20011])
        _load('data')._validate_selected_vehicle(fresh)

    def test_a_saved_shell_outside_the_current_catalogue_falls_back_atomically(self):
        state = self._state()
        state.install_component(9, 4444)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        fresh = copy.deepcopy(SNAPSHOT)
        before = copy.deepcopy(fresh)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertFalse(self._store().apply(fresh))

        self.assertIn('inconsistent', log.getvalue())
        self.assertEqual(before, fresh)

    def test_a_native_validation_failure_falls_back_atomically(self):
        state = self._state()
        state.equip_equipments(9, [11001, 0, 0])
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        fresh = copy.deepcopy(SNAPSHOT)
        before = copy.deepcopy(fresh)

        def reject(unused_snapshot):
            raise ValueError('native descriptor rejected')

        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertFalse(self._store().apply(fresh, validator=reject))

        self.assertIn('native descriptor rejected', log.getvalue())
        self.assertEqual(before, fresh)

    def test_a_mounted_optional_device_survives_a_restart(self):
        state = self._state()
        state.equip_optional_device(9, 9001, 0)
        mounted = state.snapshot()['vehicles'][0]['compDescr']
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        restored = self._restart()['vehicles'][0]

        self.assertEqual(mounted, restored['compDescr'])
        self.assertNotEqual(SNAPSHOT['vehicles'][0]['compDescr'],
                            restored['compDescr'])

    def test_an_applied_outfit_survives_a_restart(self):
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=vehicles, tankmen_module=tankmen,
            customizations_module=_Customizations)
        state.apply_outfit(9, 2, b'outfit:summer')
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        restored = self._restart()['vehicles'][0]

        self.assertEqual(
            (b'outfit:summer', True), restored['outfits'][2])

    def test_a_learned_crew_skill_survives_a_restart(self):
        state = self._state()
        state.add_tankman_skill(101, 48)
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        restored = self._restart()['vehicles'][0]

        self.assertEqual(
            b'tman:101|loader_intuition', restored['tankmen'][101])

    def test_battle_crew_receipt_and_descriptors_commit_once_together(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['vehicles'][0]['settings'] = 1
        unused_vehicles, tankmen = _modules()
        store = self._store()

        first = store.apply_battle_crew_xp(
            snapshot, 'server:7:1', 50001, 100, 1,
            tankmen_module=tankmen)
        duplicate = store.apply_battle_crew_xp(
            snapshot, 'server:7:1', 50001, 100, 1,
            tankmen_module=tankmen)

        self.assertTrue(first['applied'])
        self.assertFalse(duplicate['applied'])
        self.assertEqual(200, _TankmanDescriptor(
            snapshot['vehicles'][0]['tankmen'][101]).totalXP())
        restarted = copy.deepcopy(SNAPSHOT)
        restarted['vehicles'][0]['settings'] = 1
        restarted_store = self._store()
        self.assertTrue(restarted_store.apply(restarted))
        after_restart = restarted_store.apply_battle_crew_xp(
            restarted, 'server:7:1', 50001, 100, 1,
            tankmen_module=tankmen)
        self.assertFalse(after_restart['applied'])
        self.assertEqual(200, _TankmanDescriptor(
            restarted['vehicles'][0]['tankmen'][101]).totalXP())
        self.assertEqual(100, _TankmanDescriptor(
            restarted['vehicles'][0]['tankmen'][102]).totalXP())

    def test_schema_three_garage_upgrades_without_losing_the_loadout(self):
        state = self._state()
        state.equip_equipments(9, [11001, 0, 0])
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))
        with io.open(self.path, 'r', encoding='utf-8') as stream:
            saved = stream.read().replace('"schema": 4', '"schema": 3')
        with io.open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(saved)

        fresh = copy.deepcopy(SNAPSHOT)
        self.assertTrue(self._store().apply(fresh))

        self.assertEqual([11001, 0, 0], fresh['vehicles'][0]['eqs'])

    def test_a_saved_setting_wins_over_the_bootstrap_default(self):
        state = self._state()
        # The player clears AUTO_LOAD, leaving XP_TO_TMAN|AUTO_REPAIR|AUTO_EQUIP.
        state.change_vehicle_setting(9, 4, 0)
        state.change_vehicle_setting(9, 11, 1)
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        fresh = copy.deepcopy(SNAPSHOT)
        fresh['vehicles'][0]['settings'] = 15
        self._store().apply(fresh)

        self.assertEqual(11, fresh['vehicles'][0]['settings'])

    def test_purchases_survive_a_restart(self):
        state = self._state()
        state.buy_item(9002, 3)
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        restored = self._restart()

        self.assertGreaterEqual(restored['inventoryItems'][9][9002], 3)

    def test_an_item_the_catalogue_no_longer_offers_is_not_restored(self):
        state = self._state()
        state.buy_item(9002, 3)
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        fresh = copy.deepcopy(SNAPSHOT)
        del fresh['inventoryItems'][9][9002]
        del fresh['shopItemPrices'][9002]
        self._store().apply(fresh)

        self.assertNotIn(9002, fresh['inventoryItems'][9])
        self.assertNotIn(9002, fresh['shopItemPrices'])

    def test_a_reload_is_keyed_on_the_vehicle_type_not_the_inventory_id(self):
        state = self._state()
        state.equip_equipments(9, [11001, 0, 0])
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        # A different configured vehicle renumbers inventory ids; the saved
        # loadout must still land on the same vehicle type.
        renumbered = copy.deepcopy(SNAPSHOT)
        renumbered['vehicles'][0]['id'] = 47
        self._store().apply(renumbered)

        self.assertEqual([11001, 0, 0], renumbered['vehicles'][0]['eqs'])

    def test_an_unknown_schema_falls_back_to_the_stock_garage(self):
        with io.open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(u'{"schema": 999, "vehicles": {}}')

        fresh = copy.deepcopy(SNAPSHOT)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertFalse(self._store().apply(fresh))

        self.assertIn('schema', log.getvalue())
        self.assertEqual([0, 0, 0], fresh['vehicles'][0]['eqs'])

    def test_corrupt_content_falls_back_to_the_stock_garage(self):
        with io.open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(u'{ this is not json')

        fresh = copy.deepcopy(SNAPSHOT)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            self._store().apply(fresh)

        self.assertIn('unreadable', log.getvalue())
        self.assertEqual([0, 0, 0], fresh['vehicles'][0]['eqs'])

    def test_a_missing_file_is_not_an_error(self):
        fresh = copy.deepcopy(SNAPSHOT)

        self.assertFalse(self._store().apply(fresh))

        self.assertEqual([0, 0, 0], fresh['vehicles'][0]['eqs'])

    def test_nothing_is_written_without_a_pending_change(self):
        store = self._store()

        self.assertFalse(store.flush(SNAPSHOT))

        self.assertFalse(os.path.exists(self.path))

    def test_an_unknown_saved_vehicle_type_is_skipped(self):
        state = self._state()
        state.equip_equipments(9, [11001, 0, 0])
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        fresh = copy.deepcopy(SNAPSHOT)
        fresh['vehicles'][0]['vehicleTypeCompactDescr'] = 999999
        self._store().apply(fresh)

        self.assertEqual([0, 0, 0], fresh['vehicles'][0]['eqs'])

    def test_the_write_is_atomic_and_leaves_no_temporary_file(self):
        state = self._state()
        state.equip_equipments(9, [11001, 0, 0])
        store = self._store()
        store.mark_dirty()
        store.flush(state.snapshot())

        self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + '.tmp'))
        self.assertFalse(os.path.exists(self.path + '.bak'))


if __name__ == '__main__':
    unittest.main()


class NarrowInventoryDiffTests(unittest.TestCase):
    """A fitting republished all 632 vehicles and ~3500 tankmen, 15310 items
    for a one-slot change. Inventory.synchronize merges a partial diff per
    item type through synchronizeDicts, so only the touched rows are needed."""

    def setUp(self):
        unused_requests, unused_commands, self.garage = _request_modules()
        self.data = _load('data')
        self.vehicles, self.tankmen = _modules()

    def test_only_the_touched_vehicle_is_published(self):
        full = self.data.inventory(SNAPSHOT)
        narrow = self.data.inventory(SNAPSHOT, only_vehicles=set([9]))

        vehicle_type = self.data.VEHICLE_ITEM_TYPE
        self.assertIn(
            9, full['inventory'][vehicle_type]['compDescr'])
        self.assertEqual(
            set([9]), set(narrow['inventory'][vehicle_type]['compDescr']))
        # The account-wide artefact counts stay whole: they are small and a
        # mount changes them.
        self.assertEqual(
            full['inventory'][10], narrow['inventory'][10])

    def test_a_mutation_records_the_vehicle_it_touched(self):
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=self.vehicles,
            tankmen_module=self.tankmen)
        state.touched_vehicles()

        state.equip_equipments(9, [11001, 0, 0])

        self.assertEqual(set([9]), state.touched_vehicles())
        # Reading the set clears it, so the next fitting starts clean.
        self.assertEqual(set(), state.touched_vehicles())

    def test_a_mutation_records_the_owned_items_it_touched(self):
        state = self.garage.GarageState(
            SNAPSHOT, vehicles_module=self.vehicles,
            tankmen_module=self.tankmen)
        state.touched_items()

        state.equip_equipments(9, [11001, 0, 0])

        self.assertEqual({11: set([11001])}, state.touched_items())
        self.assertEqual({}, state.touched_items())

    def test_an_untouched_item_type_is_left_out_of_the_delta(self):
        delta = self.data.inventory(
            SNAPSHOT, only_vehicles=set([9]), only_items={11: set([11001])})

        self.assertEqual(set([11001]), set(delta['inventory'][11]))
        # ItemsRequester.invalidateCache evicts one GUI item per published
        # descriptor, so an unchanged type must not appear at all.
        self.assertNotIn(10, delta['inventory'])
        self.assertNotIn(9, delta['inventory'])

    def test_an_empty_item_delta_still_publishes_the_vehicle_row(self):
        delta = self.data.inventory(
            SNAPSHOT, only_vehicles=set([9]), only_items={})

        vehicle_type = self.data.VEHICLE_ITEM_TYPE
        self.assertEqual(
            set([9]), set(delta['inventory'][vehicle_type]['compDescr']))
        self.assertEqual([vehicle_type, self.data.TANKMAN_ITEM_TYPE],
                         sorted(delta['inventory']))

    def test_a_full_sync_still_carries_every_item_type(self):
        full = self.data.inventory(SNAPSHOT)

        self.assertEqual(set(self.data.ITEM_TYPE_INDICES),
                         set(full['inventory']))
