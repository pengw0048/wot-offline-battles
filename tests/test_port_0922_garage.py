import contextlib
import copy
import importlib.util
import io
import json
import os
import ast
import shutil
import tempfile
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'src' / 'res' / 'scripts' /
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


def _load_port_module(name):
    """Load one module from the port package itself, not from account_rpc."""
    _load('data')
    full = 'gui.mods.offline_lan_0922.%s' % name
    sys.modules.pop(full, None)
    spec = importlib.util.spec_from_file_location(
        full, PACKAGE_ROOT / ('%s.py' % name))
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
        # (outstanding repair cost, remaining health), at _Descriptor's max.
        'repair': (0, 1000),
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
        # 3333 and 4444 are the spare turret and gun this account researched
        # and bought; a module has to be owned before it can be mounted.
        2: {2002: 1}, 3: {2003: 1, 3333: 1}, 4: {2004: 1, 4444: 1},
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
    # The crew shop, as bootstrap publishes it: #1513's own ShopCommonStats
    # fallbacks for the two paid rewrites, and the offline recycle-bin policy.
    'crewChangeRoleCost': {'gold': 600},
    'crewPassportCost': {'gold': 50},
    'crewFemalePassportCost': {'gold': 500},
    'tankmenRestoreConfig': {
        'freeDuration': 0,
        'goldDuration': 7 * 24 * 60 * 60,
        'goldCost': 100,
        'limit': 100,
    },
    'recycleBinTankmen': {},
    # The skill-reset choices, as the shop publishes them: give the
    # accumulated skill experience up for nothing, keep most of it for
    # credits, keep all of it for gold.
    'dropSkillsCosts': {
        0: {'credits': 0, 'gold': 0, 'xpReuseFraction': 0.0},
        1: {'credits': 200000, 'gold': 0, 'xpReuseFraction': 0.8},
        2: {'credits': 0, 'gold': 200, 'xpReuseFraction': 1.0},
    },
}


class _Component(object):
    def __init__(self, compact_descr):
        self.compactDescr = compact_descr


# The fixture's only vehicle is nation 0, type 9, with a two-seat crew.
CREW_ROLES = (('commander',), ('driver',))
# A vehicle this fixture's client marks premium, which is what a gold tank is.
PREMIUM_VEHICLE_CD = 50003
VEHICLE_TYPE_ID = 9


class _Descriptor(object):
    def __init__(self, compact_descr):
        self.compact_descr = compact_descr
        self.devices = {}
        self.components = {}
        # getMaxRepairCost's hull term is maxHealth * type.repairCost, so one
        # health point costs type.repairCost.
        self.maxHealth = 1000
        self.type = types.SimpleNamespace(
            id=(0, VEHICLE_TYPE_ID), crewRoles=CREW_ROLES, repairCost=2.0)
        self.chassis = _Component(2002)
        self.engine = _Component(2005)
        self.fuelTank = _Component(2006)
        self.radio = _Component(2007)
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

    def getDevices(self):
        defaults = [2002, 2005, 2006, 2007, 7001, 7002]
        installed = [self.chassis.compactDescr, self.engine.compactDescr,
                     self.fuelTank.compactDescr, self.radio.compactDescr,
                     self.turret.compactDescr, self.gun.compactDescr]
        return defaults, installed, list(self.devices.values())

    def _set_gun(self, compact_descr):
        self.gun = _Component(compact_descr)
        self.gun.maxAmmo = 45
        self.gun.shots = tuple(
            types.SimpleNamespace(shell=_Component(shell_compact_descr))
            for shell_compact_descr in (20010, 20011))

    @property
    def optionalDevices(self):
        # #1513 keeps one entry per slot, None where the slot is empty.
        return [
            None if index not in self.devices
            else _Component(self.devices[index])
            for index in range(3)]

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


# What the fake's passport reads as until a command replaces it.
DEFAULT_PASSPORT = (False, 1, 2, 3)


def _default_role(compact_descr):
    identity = compact_descr.rsplit(b':', 1)[-1]
    try:
        return CREW_ROLES[(int(identity) - 101) % len(CREW_ROLES)][0]
    except ValueError:
        return CREW_ROLES[0][0]


class _TankmanDescriptor(object):
    def __init__(self, compact_descr):
        # The real TankmanDescr parses its skills out of the compact
        # descriptor, so the fake must round-trip them too.
        base, _, encoded = compact_descr.partition(b'|')
        encoded, _, xp = encoded.partition(b'#')
        self.skills = [name.decode('ascii')
                       for name in encoded.split(b',') if name]
        self.free_xp = int(xp or 0)
        # The real descriptor also carries where this crew member belongs,
        # which is what decides whether a seat will take them, plus the role
        # and the passport every crew command rewrites in place.
        self.nationID = 0
        self.vehicleTypeID = VEHICLE_TYPE_ID
        base, _, passport = base.partition(b'~')
        base, _, role = base.partition(b'!')
        base, _, retrained = base.partition(b'@')
        self.compact_descr = base
        if retrained:
            self.vehicleTypeID = int(retrained)
        self.role = _default_role(base)
        if role:
            self.role = role.decode('ascii')
        # The fixture's crew all come from name group 0 and are not premium.
        self.gid = 0
        self.isPremium = False
        (self.isFemale, self.firstNameID, self.lastNameID,
         self.iconID) = DEFAULT_PASSPORT
        if passport:
            female, first, last, icon = passport.split(b',')
            self.isFemale = female == b'1'
            self.firstNameID = int(first)
            self.lastNameID = int(last)
            self.iconID = int(icon)

    def validatePassport(self, isPremium, isFemale, fnGroupID, firstNameID,
                         lnGroupID, lastNameID, iGroupID, iconID):
        """Return what #1513 returns: ``(accepted, reason, replacement)``."""
        if isFemale is None:
            isFemale = self.isFemale
        if firstNameID is None:
            firstNameID = self.firstNameID
        if lastNameID is None:
            lastNameID = self.lastNameID
        if iconID is None:
            iconID = self.iconID
        for group in (fnGroupID, lnGroupID, iGroupID):
            if group != 0:
                return False, 'Invalid group', None
        return True, '', (isFemale, firstNameID, lastNameID, iconID)

    def replacePassport(self, ctx):
        (self.isFemale, self.firstNameID, self.lastNameID,
         self.iconID) = ctx

    def addSkill(self, name):
        if name in self.skills:
            raise ValueError('already learned')
        self.skills.append(name)

    def dropSkills(self, fraction, throw):
        # The real one keeps this share of the accumulated skill experience,
        # which is the whole difference between the shop's three choices.
        self.free_xp = int(self.free_xp * fraction)
        self.skills = []

    def isFreeDropSkills(self):
        # #1513: a crew member who has not finished a first skill has nothing
        # to give up, so retail hands that one reset over for nothing.
        return not self.skills

    def addXP(self, amount):
        self.free_xp += int(amount)

    def respecialize(self, new_vehicle_type_id, min_role_level,
                     vehicle_change_loss, class_change_loss, becomes_premium):
        # The real one works the loss out from the two rates and the vehicle
        # tags; the fake only has to record that it was asked, with what.
        self.vehicleTypeID = int(new_vehicle_type_id)
        self.role_level = int(min_role_level)
        self.losses = (vehicle_change_loss, class_change_loss)
        self.premium = bool(becomes_premium)

    def totalXP(self):
        return self.free_xp

    def makeCompactDescr(self):
        result = self.compact_descr
        if self.vehicleTypeID != VEHICLE_TYPE_ID:
            result += b'@%d' % self.vehicleTypeID
        if self.role != _default_role(self.compact_descr):
            result += b'!' + self.role.encode('ascii')
        passport = (self.isFemale, self.firstNameID, self.lastNameID,
                    self.iconID)
        if passport != DEFAULT_PASSPORT:
            result += b'~%d,%d,%d,%d' % (
                1 if self.isFemale else 0, self.firstNameID,
                self.lastNameID, self.iconID)
        result += b'|' + ','.join(self.skills).encode('ascii')
        return result if not self.free_xp else (
            result + b'#' + str(self.free_xp).encode('ascii'))


def _modules():
    def item_type(compact_descr):
        # 3 turret, 9 optionalDevice, 10 shell, 11 equipment, else a module.
        return {3: 3, 9: 9, 10: 10, 11: 11}.get(compact_descr // 1000, 4)

    vehicles = types.SimpleNamespace(
        VehicleDescr=lambda compactDescr: _Descriptor(compactDescr),
        getDefaultAmmoForGun=lambda gun: [20010, 30, 20011, 15],
        # The fixture's own vehicle is 50001; anything else is a different
        # type, which is what makes a retraining observable.
        # 9002 is the complex device: #1513's own descriptor marks it as not
        # removable, and removeOptionalDevice destroys such a device unless
        # the player paid to take it off.
        getItemByCompactDescr=lambda compact_descr: types.SimpleNamespace(
            removable=(compact_descr != 9002)),
        getVehicleType=lambda compact_descr: types.SimpleNamespace(
            id=(0, VEHICLE_TYPE_ID if compact_descr == 50001
                else compact_descr % 1000),
            crewRoles=CREW_ROLES,
            # gui_items.Vehicle.isPremium is 'premium' in type.tags, and
            # #1513's own VehicleType carries a frozenset.  50003 is this
            # fixture's gold tank.
            tags=frozenset(
                ('premium',) if compact_descr == PREMIUM_VEHICLE_CD else ())),
        getTypeOfCompactDescr=item_type)
    # #1513's own table, out of items/components/skills_constants.pyc: the
    # five roles come first and every command sends an index into it.
    skill_names = [
        'unused_skill_%d' % index for index in range(61)]
    skill_names[:10] = [
        'reserved', 'commander', 'radioman', 'driver', 'gunner', 'loader',
        'repair', 'fireFighting', 'camouflage', 'brotherhood']
    skill_names[48] = 'loader_intuition'

    def generate_tankmen(nation_id, vehicle_type_id, roles, is_premium,
                         role_level, skills_mask, is_preview):
        return [b'tman:new:%s#%d' % (role[0].encode('ascii'), role_level)
                for role in roles]

    tankmen = types.SimpleNamespace(
        TankmanDescr=_TankmanDescriptor,
        commanderTutorXpBonusFactorForCrew=lambda crew, ammo: 0.0,
        SKILL_NAMES=tuple(skill_names),
        ROLES=('commander', 'radioman', 'driver', 'gunner', 'loader'),
        getSkillsMask=lambda names: 0,
        # #1513's own rule: a crew member can change role only when their
        # name group offers more than one.  Group 0 does; nothing else here.
        tankmenGroupCanChangeRole=(
            lambda nationID, groupID, isPremium: groupID == 0),
        generateTankmen=generate_tankmen)
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

    def test_a_gun_swap_leaves_the_rack_empty_and_the_layout_to_buy(self):
        """Fitting a gun is not buying its ammunition."""
        self.state.install_component(9, 4444)

        record = self._record()
        self.assertIn(b'4444', record['compDescr'])
        self.assertEqual([20010, 0, 20011, 0], record['shells'])
        self.assertEqual({20010: 0, 20011: 0}, record['inventoryItems'][10])
        # The new gun's default load is what the resupply button and the
        # vehicle's own auto-load switch will pay for.
        self.assertEqual(
            {(7001, 4444): [20010, 30, 20011, 15]}, record['shellsLayout'])
        self.assertEqual(
            0, self.state.snapshot()['inventoryItems'][10][20010])

    def test_a_module_the_account_does_not_own_cannot_be_mounted(self):
        """Mounting is not buying: #1513 sells a module before it fits one."""
        snapshot = copy.deepcopy(SNAPSHOT)
        del snapshot['inventoryItems'][4][4444]
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.install_component(9, 4444)

        self.assertEqual(before, state.snapshot())

    def test_a_bought_module_can_then_be_mounted(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        del snapshot['inventoryItems'][4][4444]
        snapshot['wallet'] = {'credits': 100000, 'gold': 0, 'freeXP': 0}
        snapshot['shopItemPrices'][4444] = {'credits': 3000}
        snapshot['unlockItemCompactDescrs'] = set()
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

        state.buy_item(4444)
        state.install_component(9, 4444)

        self.assertIn(b'4444', state.snapshot()['vehicles'][0]['compDescr'])
        self.assertEqual(
            100000 - 3000, state.snapshot()['wallet']['credits'])

    def test_an_optional_device_the_account_does_not_own_is_refused(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['inventoryItems'][9] = {9001: 0, 9002: 200}
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.equip_optional_device(9, 9001, 0)

        self.assertEqual(before, state.snapshot())

    def test_one_optional_device_cannot_be_mounted_on_two_vehicles(self):
        """A device belongs to the account, so one lot of it fits one slot."""
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['inventoryItems'][9] = {9001: 1}
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['vehicleTypeCompactDescr'] = 50002
        second['crew'] = [201, 202]
        second['tankmen'] = {201: b'tman:201', 202: b'tman:202'}
        snapshot['vehicles'].append(second)
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)
        state.equip_optional_device(9, 9001, 0)

        with self.assertRaises(self.garage.GarageError):
            state.equip_optional_device(10, 9001, 0)

    def test_a_device_mounted_before_a_restart_still_holds_its_lot(self):
        """A restored record carries the saved descriptor and a stock row.

        ``vehicle_records.build_record`` never writes an optional-device row,
        so a device mounted before a restart lives only in the descriptor.
        Counting the row alone would let the same one lot of it mount again.
        """
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['inventoryItems'][9] = {9001: 1}
        restored = snapshot['vehicles'][0]
        restored['compDescr'] = (
            b"veh:9|dev=[(0, 9001)]|comp=[]|turret=7001|gun=7002")
        restored['inventoryItems'].pop(9, None)
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['compDescr'] = b'veh:9'
        second['vehicleTypeCompactDescr'] = 50002
        second['crew'] = [201, 202]
        second['tankmen'] = {201: b'tman:201', 202: b'tman:202'}
        snapshot['vehicles'].append(second)
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

        with self.assertRaises(self.garage.GarageError):
            state.equip_optional_device(10, 9001, 0)

    def test_selling_a_restored_vehicle_refunds_the_device_it_carries(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['inventoryItems'][9] = {9001: 1}
        snapshot['shopItemPrices'][9001] = {'credits': 4000}
        snapshot['wallet'] = {'credits': 0, 'gold': 0, 'freeXP': 0}
        restored = snapshot['vehicles'][0]
        restored['compDescr'] = (
            b"veh:9|dev=[(0, 9001)]|comp=[]|turret=7001|gun=7002")
        restored['inventoryItems'].pop(9, None)
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['compDescr'] = b'veh:9'
        second['vehicleTypeCompactDescr'] = 50002
        second['crew'] = [201, 202]
        second['tankmen'] = {201: b'tman:201', 202: b'tman:202'}
        snapshot['vehicles'].append(second)
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

        state.sell_vehicle(9, items_from_vehicle=[9001])

        self.assertEqual(2000, state.snapshot()['wallet']['credits'])

    def test_moving_a_device_between_slots_needs_no_second_one(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['inventoryItems'][9] = {9001: 1}
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)
        state.equip_optional_device(9, 9001, 0)

        state.equip_optional_device(9, 0, 0)
        state.equip_optional_device(9, 9001, 2)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual({9001: 1}, record['inventoryItems'][9])

    def test_taking_a_device_off_leaves_the_record_saying_so(self):
        state = self.state
        state.equip_optional_device(9, 9001, 0)

        state.equip_optional_device(9, 0, 0)

        self.assertEqual({}, self._record()['inventoryItems'][9])

    def test_a_turret_swap_keeps_the_gun_it_arrived_with(self):
        """#1513 sends a gun only when the mounted one will not fit.

        ``TurretInstaller.__init__`` starts at ``gunCD = 0`` and only calls
        ``_findAvailableGun`` when the current gun fails ``mayInstall``, so the
        ordinary turret swap must not need a second gun to be owned.
        """
        state = self.state

        state.install_component(9, 3333, 0, 0)

        record = self._record()
        self.assertEqual((3333, 7002), tuple(record['shellsLayoutIdx']))

    def test_a_turret_swap_refuses_a_gun_the_account_sold(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        del snapshot['inventoryItems'][4][4444]
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.install_component(9, 3333, 4444, 0)

        self.assertEqual(before, state.snapshot())

    def test_the_stock_gun_goes_back_on_after_a_swap(self):
        """Installing a module must not spend the one it replaced."""
        state = self.state
        state.install_component(9, 4444, 0, 0)

        state.install_component(9, 2004, 0, 0)

        self.assertEqual((7001, 2004), tuple(self._record()['shellsLayoutIdx']))

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
        self.state.add_tankman_skill(101, 9)

        self.assertEqual(
            b'tman:101|brotherhood', self._record()['tankmen'][101])
        self.assertEqual(b'tman:102', self._record()['tankmen'][102])

    def test_an_unknown_skill_index_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.add_tankman_skill(101, 99)

    def test_an_unknown_tankman_is_refused(self):
        with self.assertRaises(self.garage.GarageError):
            self.state.add_tankman_skill(999, 6)

    def test_dropping_skills_clears_them(self):
        self.state.add_tankman_skill(101, 6)
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

    def test_an_alternative_price_layout_keeps_its_currency_choice(self):
        # account_shared.LayoutIterator reads a negative descriptor as
        # "buy for the alternative price" and takes its absolute value.
        self.state.set_layouts(
            9, [-10010, 12], 0, [-11001, 1, 0, 0, 0, 0, 0, 0])

        self.assertEqual({(7001, 7002): [-10010, 12]},
                         self._record()['shellsLayout'])
        self.assertEqual([11001, 0, 0], self._record()['eqs'])

    def _priced_supply_state(self):
        snapshot = self.state.snapshot()
        snapshot['wallet'] = {'credits': 100000, 'gold': 100, 'freeXP': 0}
        snapshot['shopItemPrices'][10010] = {'gold': 2}
        snapshot['shopItemPrices'][11001] = {'gold': 5}
        snapshot['inventoryItems'][11] = {}
        return snapshot

    def test_supply_currency_matches_each_signed_layout_choice(self):
        for shell_sign, equipment_sign, credits, gold in (
                (1, 1, 100000, 85), (-1, -1, 94000, 100),
                (-1, 1, 96000, 95), (1, -1, 98000, 90)):
            self.setUp()
            snapshot = self._priced_supply_state()
            shells = [shell_sign * 10010, 25, 10011, 10]
            eqs = [equipment_sign * 11001, 1, 0, 0, 0, 0, 0, 0]
            self.state.set_layouts(9, shells, 0, eqs)
            self.assertEqual(credits, snapshot['wallet']['credits'])
            self.assertEqual(gold, snapshot['wallet']['gold'])
            self.assertEqual([10010, 25, 10011, 10], self._record()['shells'])
            self.assertEqual([11001, 0, 0], self._record()['eqs'])
            # Reapplying the filled layout spends neither currency again.
            self.state.set_layouts(9, shells, 0, eqs)
            self.assertEqual(credits, snapshot['wallet']['credits'])
            self.assertEqual(gold, snapshot['wallet']['gold'])

    def test_second_supply_failure_rolls_back_the_whole_operation(self):
        snapshot = self._priced_supply_state()
        snapshot['wallet']['gold'] = 10
        before = copy.deepcopy(snapshot)
        revision = self.state.revision
        with self.assertRaises(self.garage.GarageError):
            self.state.set_layouts(9, [10010, 25, 10011, 10], 0,
                                   [11001, 1, 0, 0, 0, 0, 0, 0])
        self.assertEqual(before, self.state.snapshot())
        self.assertEqual(revision, self.state.revision)
        self.assertFalse(self.state.touched_vehicles())
        self.assertFalse(self.state.touched_items())

    def test_automatic_resupply_uses_the_saved_currency_choice(self):
        for sign, credits, gold in ((1, 100000, 78), (-1, 91200, 100)):
            self.setUp()
            snapshot = self._priced_supply_state()
            self.state.set_layouts(9, [sign * 10010, 25, 10011, 10], 0,
                                   [sign * 11001, 1, 0, 0, 0, 0, 0, 0])
            self._record()['settings'] = 12
            fired_shell = types.SimpleNamespace(compactDescr=10010)
            descriptor = types.SimpleNamespace(gun=types.SimpleNamespace(
                shots=[types.SimpleNamespace(shell=fired_shell)]))
            with mock.patch.object(self.state, '_descriptor',
                                   return_value=descriptor):
                self.assertEqual({10010: 1},
                    self.state.settle_battle_ammunition(50001, {0: 1}))
            self.state.settle_battle_consumables(50001, [11001])
            store = _load('garage_store')
            store._settle_automatically(self.state, 9, (2, 4, 8),
                                       self.garage.GarageError)
            self.assertEqual(credits, snapshot['wallet']['credits'])
            self.assertEqual(gold, snapshot['wallet']['gold'])
            self.assertEqual(sign * 11001, self._record()['eqsLayout'][0])

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
        self.assertEqual([20010, 30, 20011, 15],
                         record['shellsLayout'][(7001, 4444)])

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

    # ---- barracks -------------------------------------------------------

    def _two_seat_state(self, berths=4, barracks=None):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = berths
        if barracks is not None:
            snapshot['barracksTankmen'] = dict(barracks)
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def test_unloading_one_seat_leaves_it_empty_and_fills_a_berth(self):
        """#1513 reads a None crew entry as an empty seat, not a short crew."""
        state = self._two_seat_state()

        state.equip_tankman(9, 0, -1)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([None, 102], record['crew'])
        self.assertEqual({102: b'tman:102'}, record['tankmen'])
        self.assertEqual(
            {101: b'tman:101'}, state.snapshot()['barracksTankmen'])
        self.assertEqual(3, state.free_berths())

    def test_a_slot_of_minus_one_unloads_the_whole_crew(self):
        """That is what the barracks' own unload-crew button sends."""
        state = self._two_seat_state()

        self.assertEqual(2, state.equip_tankman(9, -1, -1))

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([None, None], record['crew'])
        self.assertEqual({}, record['tankmen'])
        self.assertEqual(
            {101: b'tman:101', 102: b'tman:102'},
            state.snapshot()['barracksTankmen'])

    def test_a_barracks_without_room_refuses_to_take_a_crew(self):
        state = self._two_seat_state(berths=1)

        with self.assertRaises(self.garage.GarageError):
            state.equip_tankman(9, -1, -1)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([101, 102], record['crew'])
        self.assertEqual({}, state.snapshot().get('barracksTankmen', {}))

    def test_a_crew_member_in_the_barracks_can_take_their_seat_back(self):
        state = self._two_seat_state()
        state.equip_tankman(9, 0, -1)

        self.assertEqual(101, state.equip_tankman(9, 0, 101))

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([101, 102], record['crew'])
        self.assertEqual(b'tman:101', record['tankmen'][101])
        self.assertEqual({}, state.snapshot()['barracksTankmen'])

    def test_a_seat_refuses_a_crew_member_trained_for_another_role(self):
        """Retraining does not exist yet, and the restore checks the role."""
        state = self._two_seat_state()
        state.equip_tankman(9, 0, -1)

        with self.assertRaises(self.garage.GarageError):
            state.equip_tankman(9, 1, 101)

        self.assertEqual(
            {101: b'tman:101'}, state.snapshot()['barracksTankmen'])

    def test_seating_someone_in_a_taken_seat_sends_its_occupant_away(self):
        state = self._two_seat_state(barracks={201: b'tman:201'})

        state.equip_tankman(9, 0, 201)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([201, 102], record['crew'])
        self.assertEqual(b'tman:201', record['tankmen'][201])
        self.assertEqual(
            {101: b'tman:101'}, state.snapshot()['barracksTankmen'])

    def test_a_swap_out_of_the_barracks_needs_no_berth_of_its_own(self):
        """One crew member leaves the barracks as the other arrives."""
        state = self._two_seat_state(berths=1, barracks={201: b'tman:201'})

        state.equip_tankman(9, 0, 201)

        self.assertEqual(
            {101: b'tman:101'}, state.snapshot()['barracksTankmen'])

    def _two_vehicle_state(self, berths=4):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = berths
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['vehicleTypeCompactDescr'] = 50002
        second['crew'] = [201, 202]
        second['tankmen'] = {201: b'tman:201', 202: b'tman:202'}
        snapshot['vehicles'].append(second)
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def test_a_crew_member_can_move_straight_from_one_vehicle_to_another(self):
        state = self._two_vehicle_state()

        state.equip_tankman(9, 0, 201)

        first, second = state.snapshot()['vehicles']
        self.assertEqual([201, 102], first['crew'])
        self.assertEqual(b'tman:201', first['tankmen'][201])
        self.assertEqual([None, 202], second['crew'])
        self.assertNotIn(201, second['tankmen'])
        # The seat's own occupant is the one who needed a berth.
        self.assertEqual(
            {101: b'tman:101'}, state.snapshot()['barracksTankmen'])

    def test_a_full_barracks_refuses_the_move_rather_than_lose_the_occupant(
            self):
        state = self._two_vehicle_state(berths=0)

        with self.assertRaises(self.garage.GarageError):
            state.equip_tankman(9, 0, 201)

        first, second = state.snapshot()['vehicles']
        self.assertEqual([101, 102], first['crew'])
        self.assertEqual([201, 202], second['crew'])
        self.assertEqual({}, state.snapshot().get('barracksTankmen', {}))

    def test_dismissing_a_crew_member_in_the_barracks_frees_the_berth(self):
        state = self._two_seat_state(barracks={201: b'tman:201'})

        state.dismiss_tankman(201)

        self.assertEqual({}, state.snapshot()['barracksTankmen'])
        self.assertEqual(4, state.free_berths())

    def test_dismissing_a_seated_crew_member_empties_the_seat(self):
        state = self._two_seat_state()

        state.dismiss_tankman(101)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([None, 102], record['crew'])
        self.assertNotIn(101, record['tankmen'])
        self.assertEqual({}, state.snapshot().get('barracksTankmen', {}))

    def test_an_unknown_crew_member_cannot_be_dismissed(self):
        state = self._two_seat_state()

        with self.assertRaises(self.garage.GarageError):
            state.dismiss_tankman(999)

    def test_a_crew_member_in_the_barracks_still_learns_and_trains(self):
        state = self._two_seat_state()
        state.equip_tankman(9, 0, -1)

        state.add_tankman_skill(101, 9)
        state.train_tankman(101, 10)

        # Ten free experience buys ten times as much crew experience.
        self.assertEqual(
            b'tman:101|brotherhood#100',
            state.snapshot()['barracksTankmen'][101])

    def test_an_empty_seat_earns_nothing_and_the_rest_of_the_crew_do(self):
        state = self._two_seat_state()
        state.equip_tankman(9, 0, -1)

        state.award_battle_crew_xp(50001, 300, 0)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual({102: b'tman:102|#300'}, record['tankmen'])
        # The crew member in the barracks fought no battle.
        self.assertEqual(
            {101: b'tman:101'}, state.snapshot()['barracksTankmen'])

    # ---- recruiting -----------------------------------------------------

    CAREER_COSTS = (
        {'credits': 0, 'gold': 0, 'roleLevel': 50,
         'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': False},
        {'credits': 20000, 'gold': 0, 'roleLevel': 75,
         'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': False},
        {'credits': 0, 'gold': 200, 'roleLevel': 100,
         'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': True},
    )

    def _recruiting_state(self, berths=4, credits_amount=100000, gold=1000,
                          unlocks=(50001,)):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = berths
        snapshot['tankmanCosts'] = self.CAREER_COSTS
        snapshot['unlockItemCompactDescrs'] = set(unlocks)
        snapshot['wallet'] = {
            'credits': credits_amount, 'gold': gold, 'freeXP': 0}
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def test_a_recruit_arrives_in_the_barracks_at_the_school_paid_for(self):
        """The window prices the three schools from the same table."""
        state = self._recruiting_state()

        # SKILL_NAMES[3] is 'driver'; the recruit window sends that index.
        tankman_id = state.buy_tankman(50001, 3, 1)

        self.assertEqual(103, tankman_id)
        self.assertEqual(
            {103: b'tman:new:driver#75'},
            state.snapshot()['barracksTankmen'])
        self.assertEqual(100000 - 20000, state.snapshot()['wallet']['credits'])

    def test_the_hundred_per_cent_school_is_paid_for_in_gold(self):
        state = self._recruiting_state()

        state.buy_tankman(50001, 1, 2)

        self.assertEqual(1000 - 200, state.snapshot()['wallet']['gold'])
        self.assertEqual(100000, state.snapshot()['wallet']['credits'])
        self.assertEqual(
            b'tman:new:commander#100',
            state.snapshot()['barracksTankmen'][103])

    def test_an_account_that_cannot_pay_recruits_nobody(self):
        state = self._recruiting_state(credits_amount=100)

        with self.assertRaises(self.garage.GarageError):
            state.buy_tankman(50001, 3, 1)

        self.assertEqual({}, state.snapshot().get('barracksTankmen', {}))
        self.assertEqual(100, state.snapshot()['wallet']['credits'])

    def test_a_full_barracks_recruits_nobody_and_keeps_the_money(self):
        state = self._recruiting_state(berths=0)

        with self.assertRaises(self.garage.GarageError):
            state.buy_tankman(50001, 3, 1)

        self.assertEqual(100000, state.snapshot()['wallet']['credits'])

    def test_a_role_the_vehicle_does_not_crew_is_refused(self):
        state = self._recruiting_state()

        # SKILL_NAMES[5] is 'loader', and this vehicle has none.
        with self.assertRaises(self.garage.GarageError):
            state.buy_tankman(50001, 5, 0)

    def test_an_index_that_is_a_skill_rather_than_a_role_is_refused(self):
        state = self._recruiting_state()

        # SKILL_NAMES[6] is 'repair', which is a skill, not a crew role.
        with self.assertRaises(self.garage.GarageError):
            state.buy_tankman(50001, 6, 0)

    def test_an_unknown_school_is_refused(self):
        state = self._recruiting_state()

        with self.assertRaises(self.garage.GarageError):
            state.buy_tankman(50001, 3, 7)

    def test_a_recruit_can_be_bought_straight_into_an_empty_seat(self):
        state = self._recruiting_state()
        state.equip_tankman(9, 1, -1)

        tankman_id = state.buy_and_equip_tankman(9, 1, 0)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([101, tankman_id], record['crew'])
        self.assertEqual(b'tman:new:driver#50', record['tankmen'][tankman_id])
        # Only the crew member who was unloaded is in the barracks.
        self.assertEqual(
            {102: b'tman:102'}, state.snapshot()['barracksTankmen'])

    def test_a_recruit_bought_into_a_taken_seat_needs_a_berth_for_its_own(self):
        state = self._recruiting_state(berths=0)

        with self.assertRaises(self.garage.GarageError):
            state.buy_and_equip_tankman(9, 1, 0)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([101, 102], record['crew'])
        self.assertEqual(100000, state.snapshot()['wallet']['credits'])

    def test_a_recruit_bought_into_a_taken_seat_replaces_its_occupant(self):
        state = self._recruiting_state()

        tankman_id = state.buy_and_equip_tankman(9, 1, 1)

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([101, tankman_id], record['crew'])
        self.assertEqual(
            {102: b'tman:102'}, state.snapshot()['barracksTankmen'])
        self.assertEqual(100000 - 20000, state.snapshot()['wallet']['credits'])

    def test_a_crew_can_be_hired_before_the_vehicle_is_bought(self):
        """The recruit window lists unlocked vehicles, not owned ones."""
        state = self._recruiting_state(unlocks=(50001, 50002))

        state.buy_tankman(50002, 3, 0)

        self.assertEqual(
            {103: b'tman:new:driver#50'}, state.snapshot()['barracksTankmen'])

    def test_a_vehicle_the_account_has_not_researched_hires_nobody(self):
        state = self._recruiting_state()

        with self.assertRaises(self.garage.GarageError):
            state.buy_tankman(50002, 3, 0)

        self.assertEqual({}, state.snapshot().get('barracksTankmen', {}))

    def test_a_sandbox_recruits_for_nothing(self):
        """Its published table is the one the window shows, and it is free."""
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        snapshot['unlockItemCompactDescrs'] = {50001}
        snapshot['wallet'] = {'credits': 0, 'gold': 0, 'freeXP': 0}
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

        state.buy_tankman(50001, 3, 2)

        self.assertEqual(
            b'tman:new:driver#100',
            state.snapshot()['barracksTankmen'][103])

    # ---- damage and repair ----------------------------------------------

    def _damaged_state(self, credits_amount=100000):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {
            'credits': credits_amount, 'gold': 0, 'freeXP': 0}
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def test_a_battle_leaves_the_repair_bill_the_client_reads(self):
        """invData['repair'] is (outstanding cost, remaining health)."""
        state = self._damaged_state()

        # 400 of 1000 health left: 600 points at 2 credits each.
        self.assertEqual((1200, 400), state.settle_battle_damage(50001, 400))
        self.assertEqual((1200, 400), state.snapshot()['vehicles'][0]['repair'])

    def test_a_destroyed_vehicle_comes_back_at_zero_health(self):
        """Vehicle.modelState reads 0 health beside a bill as DESTROYED."""
        state = self._damaged_state()

        self.assertEqual((2000, 0), state.settle_battle_damage(50001, 0))

    def test_an_untouched_vehicle_owes_nothing(self):
        state = self._damaged_state()

        self.assertEqual((0, 1000), state.settle_battle_damage(50001, 1000))

    def test_a_stale_receipt_cannot_heal_past_the_maximum(self):
        state = self._damaged_state()

        self.assertEqual((0, 1000), state.settle_battle_damage(50001, 9999))

    def test_repairing_pays_the_bill_and_restores_the_health(self):
        state = self._damaged_state()
        state.settle_battle_damage(50001, 400)

        self.assertEqual(1200, state.repair_vehicle(9))

        self.assertEqual((0, 1000), state.snapshot()['vehicles'][0]['repair'])
        self.assertEqual(100000 - 1200, state.snapshot()['wallet']['credits'])

    def test_an_account_that_cannot_pay_leaves_the_vehicle_broken(self):
        """isBroken is the bill alone, and a broken tank cannot fight."""
        state = self._damaged_state(credits_amount=100)
        state.settle_battle_damage(50001, 400)

        with self.assertRaises(self.garage.GarageError):
            state.repair_vehicle(9)

        self.assertEqual((1200, 400), state.snapshot()['vehicles'][0]['repair'])
        self.assertEqual(100, state.snapshot()['wallet']['credits'])

    def test_a_vehicle_that_needs_nothing_is_not_repaired_twice(self):
        state = self._damaged_state()

        with self.assertRaises(self.garage.GarageError):
            state.repair_vehicle(9)

    # ---- ammunition ------------------------------------------------------

    def _loaded_state(self, credits_amount=100000):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {
            'credits': credits_amount, 'gold': 0, 'freeXP': 0}
        snapshot['shopItemPrices'][10010] = {'credits': 100}
        snapshot['shopItemPrices'][10011] = {'credits': 200}
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def test_a_battle_takes_the_rounds_it_fired_out_of_the_tank(self):
        """The receipt counts by the shell's index in the gun's shot order."""
        state = self._loaded_state()

        # _Descriptor's gun fires 20010 at index 0 and 20011 at index 1; the
        # fixture loads 10010 and 10011, which this gun does not fire.
        spent = state.settle_battle_ammunition(50001, {0: 5})

        self.assertEqual({}, spent)

    def _matching_state(self, credits_amount=100000):
        """A vehicle whose loaded rounds are the ones its gun fires."""
        state = self._loaded_state(credits_amount)
        record = state.snapshot()['vehicles'][0]
        record['shells'] = [20010, 30, 20011, 15]
        record['shellsLayout'] = {(7001, 7002): [20010, 30, 20011, 15]}
        record['inventoryItems'][10] = {20010: 30, 20011: 15}
        state.snapshot()['inventoryItems'][10] = {20010: 30, 20011: 15}
        state.snapshot()['shopItemPrices'][20010] = {'credits': 100}
        state.snapshot()['shopItemPrices'][20011] = {'credits': 200}
        return state

    def test_the_rounds_fired_leave_the_tank_and_the_account(self):
        state = self._matching_state()

        spent = state.settle_battle_ammunition(50001, {0: 12, 1: 3})

        self.assertEqual({20010: 12, 20011: 3}, spent)
        record = state.snapshot()['vehicles'][0]
        self.assertEqual([20010, 18, 20011, 12], list(record['shells']))
        self.assertEqual({20010: 18, 20011: 12}, record['inventoryItems'][10])
        self.assertEqual(
            {20010: 18, 20011: 12},
            state.snapshot()['inventoryItems'][10])

    def test_a_receipt_cannot_fire_more_rounds_than_the_tank_carried(self):
        state = self._matching_state()

        spent = state.settle_battle_ammunition(50001, {0: 500})

        self.assertEqual({20010: 30}, spent)
        self.assertEqual(
            [20010, 0, 20011, 15],
            list(state.snapshot()['vehicles'][0]['shells']))

    def test_a_battle_that_fired_nothing_changes_nothing(self):
        state = self._matching_state()

        self.assertEqual({}, state.settle_battle_ammunition(50001, {}))
        self.assertEqual(
            [20010, 30, 20011, 15],
            list(state.snapshot()['vehicles'][0]['shells']))

    def test_reloading_buys_the_rounds_the_depot_is_short_of(self):
        """SET_AND_FILL_LAYOUTS fills a layout the depot cannot."""
        state = self._matching_state()
        state.settle_battle_ammunition(50001, {0: 12, 1: 3})

        state.equip_shells(9, [20010, 30, 20011, 15])

        # 12 rounds at 100 credits and 3 at 200.
        self.assertEqual(
            100000 - 1200 - 600, state.snapshot()['wallet']['credits'])
        self.assertEqual(
            {20010: 30, 20011: 15},
            state.snapshot()['inventoryItems'][10])

    def test_an_account_that_cannot_pay_loads_nothing(self):
        state = self._matching_state(credits_amount=100)
        state.settle_battle_ammunition(50001, {0: 12})

        with self.assertRaises(self.garage.GarageError):
            state.equip_shells(9, [20010, 30, 20011, 15])

        self.assertEqual(
            [20010, 18, 20011, 15],
            list(state.snapshot()['vehicles'][0]['shells']))
        self.assertEqual(100, state.snapshot()['wallet']['credits'])

    def test_unloading_rounds_costs_nothing_and_keeps_them(self):
        state = self._matching_state()

        state.equip_shells(9, [20010, 10, 20011, 15])

        self.assertEqual(100000, state.snapshot()['wallet']['credits'])
        self.assertEqual(
            {20010: 30, 20011: 15},
            state.snapshot()['inventoryItems'][10])

    def _second_vehicle(self, state, loaded=True):
        """Put one more vehicle in the garage, loaded or empty."""
        snapshot = state.snapshot()
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['vehicleTypeCompactDescr'] = 50002
        second['crew'] = [201, 202]
        second['tankmen'] = {201: b'tman:201', 202: b'tman:202'}
        if not loaded:
            second['shells'] = [20010, 0, 20011, 0]
            second['shellsLayout'] = {(7001, 7002): [20010, 0, 20011, 0]}
            second['inventoryItems'][10] = {20010: 0, 20011: 0}
        snapshot['vehicles'].append(second)
        return second

    def test_a_second_vehicle_buys_its_own_rounds(self):
        """One lot of rounds cannot be loaded into two tanks."""
        state = self._matching_state()
        snapshot = state.snapshot()
        self._second_vehicle(state, loaded=False)

        state.equip_shells(10, [20010, 30, 20011, 15])

        # The first tank still holds 30 and 15, so the account has to buy the
        # second load: 30 rounds at 100 credits and 15 at 200.
        self.assertEqual(
            100000 - 3000 - 3000, snapshot['wallet']['credits'])
        self.assertEqual(
            {20010: 60, 20011: 30}, snapshot['inventoryItems'][10])

    def test_a_vehicle_already_carrying_its_layout_buys_nothing(self):
        """The depot count covers the whole garage, loaded rounds included."""
        state = self._matching_state()
        snapshot = state.snapshot()
        self._second_vehicle(state, loaded=True)
        # A garage the builders published: the depot holds what both tanks do.
        snapshot['inventoryItems'][10] = {20010: 60, 20011: 30}

        state.equip_shells(10, [20010, 30, 20011, 15])

        self.assertEqual(100000, snapshot['wallet']['credits'])
        self.assertEqual(
            {20010: 60, 20011: 30}, snapshot['inventoryItems'][10])

    # ---- retraining ------------------------------------------------------

    def test_a_whole_crew_retrains_at_the_school_the_player_chose(self):
        """The popover sends the seated crew and the school for each seat."""
        state = self._recruiting_state(unlocks=(50001,))

        self.assertEqual([101, 102], state.retrain_crew(50001, [101, 1, 102, 1]))

        self.assertEqual(
            100000 - 40000, state.snapshot()['wallet']['credits'])

    def test_a_crew_retraining_the_wallet_cannot_finish_changes_nothing(self):
        """A crew half retrained is worse than one not retrained."""
        state = self._recruiting_state(unlocks=(50001,))
        # One school costs 20000 credits, so this pays for the first seat and
        # not the second.
        state.snapshot()['wallet']['credits'] = 30000
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.retrain_crew(50001, [101, 1, 102, 1])

        self.assertEqual(before['wallet'], state.snapshot()['wallet'])
        self.assertEqual(
            before['vehicles'][0]['tankmen'],
            state.snapshot()['vehicles'][0]['tankmen'])


    def test_retraining_asks_the_client_for_the_school_the_player_chose(self):
        """TankmanDescr.respecialize is the client's own implementation."""
        state = self._recruiting_state(unlocks=(50001, 50002))
        state.equip_tankman(9, 0, -1)

        state.retrain_tankman(101, 1, 50002)

        # 50002 is in-nation type 2 for this fixture.
        self.assertEqual(
            b'tman:101@2|', state.snapshot()['barracksTankmen'][101])
        self.assertEqual(
            100000 - 20000, state.snapshot()['wallet']['credits'])

    def test_a_seated_crew_member_is_not_retrained_out_of_their_seat(self):
        """The restore boundary needs every seated crew member to match."""
        state = self._recruiting_state(unlocks=(50001, 50002))

        with self.assertRaises(self.garage.GarageError):
            state.retrain_tankman(101, 1, 50002)

        self.assertEqual(
            b'tman:101', state.snapshot()['vehicles'][0]['tankmen'][101])
        self.assertEqual(100000, state.snapshot()['wallet']['credits'])

    def test_retraining_for_the_vehicle_they_are_already_in_is_allowed(self):
        state = self._recruiting_state()

        state.retrain_tankman(101, 2, 50001)

        self.assertEqual(
            1000 - 200, state.snapshot()['wallet']['gold'])

    def test_a_vehicle_the_account_has_not_researched_trains_nobody(self):
        state = self._recruiting_state()
        state.equip_tankman(9, 0, -1)

        with self.assertRaises(self.garage.GarageError):
            state.retrain_tankman(101, 1, 50002)

        self.assertEqual(100000, state.snapshot()['wallet']['credits'])

    def test_an_account_that_cannot_pay_retrains_nobody(self):
        state = self._recruiting_state(credits_amount=100)
        state.equip_tankman(9, 0, -1)

        with self.assertRaises(self.garage.GarageError):
            state.retrain_tankman(101, 1, 50001)

        self.assertEqual(
            b'tman:101', state.snapshot()['barracksTankmen'][101])

    def test_a_whole_crew_is_retrained_in_one_command(self):
        state = self._recruiting_state()

        state.retrain_crew(50001, [101, 0, 102, 0])

        record = state.snapshot()['vehicles'][0]
        self.assertEqual(b'tman:101|', record['tankmen'][101])
        self.assertEqual(b'tman:102|', record['tankmen'][102])

    def test_a_malformed_crew_retraining_is_refused(self):
        state = self._recruiting_state()

        with self.assertRaises(self.garage.GarageError):
            state.retrain_crew(50001, [101, 0, 102])

    # ---- taking an optional device off -----------------------------------

    def _device_state(self, gold=1000):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {'credits': 100000, 'gold': gold, 'freeXP': 0}
        snapshot['deviceRemovalCost'] = {'gold': 10}
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def test_a_simple_device_comes_off_for_nothing(self):
        state = self._device_state()
        state.equip_optional_device(9, 9001, 0)

        state.equip_optional_device(9, 0, 0)

        self.assertEqual(1000, state.snapshot()['wallet']['gold'])
        # The fixture owns 200 of it and none of them were lost.
        self.assertEqual(
            200, state.snapshot()['inventoryItems'][9][9001])

    def test_a_complex_device_taken_off_for_nothing_is_destroyed(self):
        """removeOptionalDevice returns it as destroyed, not returned."""
        state = self._device_state()
        state.equip_optional_device(9, 9002, 0)
        self.assertEqual(200, state.snapshot()['inventoryItems'][9][9002])

        state.equip_optional_device(9, 0, 0)

        self.assertEqual(1000, state.snapshot()['wallet']['gold'])
        self.assertEqual(
            199, state.snapshot()['inventoryItems'][9][9002])

    def test_a_paid_removal_keeps_the_complex_device_and_costs_gold(self):
        state = self._device_state()
        state.equip_optional_device(9, 9002, 0)

        state.equip_optional_device(9, 0, 0, paid_removal=True)

        self.assertEqual(1000 - 10, state.snapshot()['wallet']['gold'])
        self.assertEqual(200, state.snapshot()['inventoryItems'][9][9002])

    def test_a_paid_removal_the_account_cannot_afford_is_refused(self):
        state = self._device_state(gold=5)
        state.equip_optional_device(9, 9002, 0)

        with self.assertRaises(self.garage.GarageError):
            state.equip_optional_device(9, 0, 0, paid_removal=True)

        self.assertEqual(200, state.snapshot()['inventoryItems'][9][9002])
        self.assertEqual(5, state.snapshot()['wallet']['gold'])

    def test_a_sandbox_publishes_no_removal_price_and_charges_none(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {'credits': 0, 'gold': 0, 'freeXP': 0}
        snapshot['deviceRemovalCost'] = {'gold': 0}
        vehicles, tankmen = _modules()
        state = self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)
        state.equip_optional_device(9, 9002, 0)

        state.equip_optional_device(9, 0, 0, paid_removal=True)

        self.assertEqual(200, state.snapshot()['inventoryItems'][9][9002])

    # ---- sending a crew back ---------------------------------------------

    def test_a_crew_unloaded_to_the_barracks_can_be_sent_back(self):
        """The popover reads lastCrew as one inventory id per seat."""
        state = self._two_seat_state()
        state.equip_tankman(9, -1, -1)

        self.assertEqual([101, 102], state.return_crew(9))

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([101, 102], record['crew'])
        self.assertEqual({}, state.snapshot()['barracksTankmen'])

    def test_only_the_seat_that_emptied_is_remembered(self):
        state = self._two_seat_state()
        state.equip_tankman(9, 1, -1)

        self.assertEqual(
            [None, 102], list(state.snapshot()['vehicles'][0]['lastCrew']))

    def test_a_vehicle_that_never_lost_a_crew_has_nobody_to_send_back(self):
        state = self._two_seat_state()

        with self.assertRaises(self.garage.GarageError):
            state.return_crew(9)

    def test_a_dismissed_crew_member_cannot_be_sent_back(self):
        state = self._two_seat_state()
        state.dismiss_tankman(101)

        with self.assertRaises(self.garage.GarageError):
            state.return_crew(9)

        self.assertEqual(
            [101, None], list(state.snapshot()['vehicles'][0]['lastCrew']))

    def test_a_crew_member_who_took_another_seat_comes_back_from_it(self):
        """The popover works out where each of them is before it offers."""
        state = self._two_vehicle_state()
        state.equip_tankman(9, 0, -1)
        state.equip_tankman(10, 0, 101)

        self.assertEqual([101], state.return_crew(9))

        first, second = state.snapshot()['vehicles']
        self.assertEqual([101, 102], first['crew'])
        self.assertEqual([None, 202], second['crew'])

    # ---- consumables -----------------------------------------------------

    def _consumable_state(self, credits_amount=100000):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {
            'credits': credits_amount, 'gold': 0, 'freeXP': 0}
        snapshot['shopItemPrices'][11001] = {'credits': 3000}
        snapshot['inventoryItems'][11] = {11001: 2}
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def test_a_consumable_used_in_battle_leaves_its_slot_empty(self):
        """The layout still names it, which is what auto-equip refills."""
        state = self._consumable_state()
        state.equip_equipments(9, [11001, 0, 0])

        self.assertEqual(
            [11001], state.settle_battle_consumables(50001, [11001]))

        record = state.snapshot()['vehicles'][0]
        self.assertEqual([0, 0, 0], list(record['eqs']))
        self.assertEqual([11001, 0, 0], list(record['eqsLayout']))
        self.assertEqual(1, state.snapshot()['inventoryItems'][11][11001])

    def test_a_consumable_that_was_not_mounted_is_not_charged(self):
        state = self._consumable_state()

        self.assertEqual([], state.settle_battle_consumables(50001, [11001]))
        self.assertEqual(2, state.snapshot()['inventoryItems'][11][11001])

    def test_refilling_a_used_consumable_buys_another(self):
        state = self._consumable_state()
        state.equip_equipments(9, [11001, 0, 0])
        state.settle_battle_consumables(50001, [11001])

        state.equip_equipments(9, [11001, 0, 0])

        # Two owned, one used, one mounted: nothing to buy yet.
        self.assertEqual(100000, state.snapshot()['wallet']['credits'])
        state.settle_battle_consumables(50001, [11001])
        state.equip_equipments(9, [11001, 0, 0])
        self.assertEqual(
            100000 - 3000, state.snapshot()['wallet']['credits'])

    def test_an_account_that_cannot_pay_mounts_nothing(self):
        state = self._consumable_state(credits_amount=100)
        state.snapshot()['inventoryItems'][11] = {}

        with self.assertRaises(self.garage.GarageError):
            state.equip_equipments(9, [11001, 0, 0])

        self.assertEqual([0, 0, 0], list(
            state.snapshot()['vehicles'][0]['eqs']))
        self.assertEqual(100, state.snapshot()['wallet']['credits'])

    def test_a_consumable_already_owned_is_mounted_for_nothing(self):
        state = self._consumable_state()

        state.equip_equipments(9, [11001, 0, 0])

        self.assertEqual(100000, state.snapshot()['wallet']['credits'])


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
        snapshot = self.state.snapshot()
        record = snapshot['vehicles'][0]
        self.assertEqual((3333, 4444), record['shellsLayoutIdx'])
        # The turret was bought; its gun's ammunition was not.
        self.assertEqual([20010, 0, 20011, 0], record['shells'])
        self.assertEqual(
            [20010, 30, 20011, 15], record['shellsLayout'][(3333, 4444)])
        # The mount is also a purchase, so the account owns one more turret
        # than the spare it started with.
        self.assertEqual(2, snapshot['inventoryItems'][3][3333])

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
                ([77, 3355, 9, 0, 0, 4444],))

        self.assertEqual(self.commands.RES_FAILURE, result.result_id)
        self.assertEqual(before, self.state.snapshot())
        self.assertNotIn(
            3355, self.state.snapshot()['inventoryItems'].get(3, {}))
        self.assertEqual([], self.pushed)

    def test_equip_shells_updates_the_published_inventory(self):
        result = self._dispatch(
            self.commands.CMD_EQUIP_SHELLS, ([9, 10010, 7],))
        result.before_response()

        self.assertEqual(
            [10010, 7], self.pushed[0]['inventory'][1]['shells'][9])
        # Unloading rounds does not spend them: the account still owns the 20
        # it had, 7 of them now sitting in the tank.
        self.assertEqual({10010: 13, 10011: 10}, self.pushed[0]['inventory'][10])

    def test_add_skill_uses_the_int3_payload(self):
        # #1513 sends tankmen.SKILL_INDICES[name]; 'repair' is 6.
        result = self._dispatch(self.commands.CMD_TMAN_ADD_SKILL, (101, 6, 0))

        self.assertEqual(self.commands.RES_SUCCESS, result.result_id)
        self.assertEqual(
            b'tman:101|repair',
            self.state.snapshot()['vehicles'][0]['tankmen'][101])

    def test_drop_skills_uses_shop_revision_then_tankman_id(self):
        self.state.add_tankman_skill(101, 6)

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


class CrewShopTests(unittest.TestCase):
    """The three crew commands #1513 prices through the shop."""

    def setUp(self):
        unused_requests, unused_commands, self.garage = _request_modules()

    def _state(self, gold=10000, berths=5, barracks=None):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {'credits': 0, 'gold': gold, 'freeXP': 0}
        snapshot['accountBerths'] = berths
        snapshot['barracksTankmen'] = dict(
            barracks if barracks is not None else {201: b'tman:201'})
        vehicles, tankmen = _modules()
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    # ---- the recycle bin ------------------------------------------------

    def test_a_dismissed_crew_member_waits_in_the_recycle_bin(self):
        """#1513's barracks offers to hire them back, so they are kept."""
        state = self._state()

        state.dismiss_tankman(201)

        bin_rows = state.snapshot()['recycleBinTankmen']
        self.assertEqual(b'tman:201', bin_rows[201][0])
        self.assertGreater(bin_rows[201][1], 0)
        self.assertEqual({}, state.snapshot()['barracksTankmen'])

    def test_hiring_one_back_costs_gold_and_uses_a_berth(self):
        state = self._state(gold=10000)
        state.dismiss_tankman(201)

        state.restore_tankman(201)

        self.assertEqual(
            b'tman:201', state.snapshot()['barracksTankmen'][201])
        self.assertEqual({}, state.snapshot()['recycleBinTankmen'])
        self.assertEqual(10000 - 100, state.snapshot()['wallet']['gold'])

    def test_hiring_one_back_without_the_gold_changes_nothing(self):
        state = self._state(gold=50)
        state.dismiss_tankman(201)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.restore_tankman(201)

        self.assertEqual(before, state.snapshot())

    def test_hiring_one_back_with_no_free_berth_is_refused(self):
        """#1513 adds a BarracksSlotsValidator for exactly one berth."""
        state = self._state(berths=2, barracks={201: b'tman:201'})
        state.dismiss_tankman(201)
        state.snapshot()['barracksTankmen'].update(
            {301: b'tman:301', 302: b'tman:302'})

        with self.assertRaises(self.garage.GarageError):
            state.restore_tankman(201)

    def test_a_dismissal_older_than_the_window_can_no_longer_be_undone(self):
        state = self._state()
        state.dismiss_tankman(201)
        descriptor, dismissed_at = state.snapshot()['recycleBinTankmen'][201]
        state.snapshot()['recycleBinTankmen'][201] = (
            descriptor, dismissed_at - 8 * 24 * 60 * 60)

        with self.assertRaises(self.garage.GarageError):
            state.restore_tankman(201)

    def test_nobody_else_can_be_hired_out_of_the_bin(self):
        state = self._state()

        with self.assertRaises(self.garage.GarageError):
            state.restore_tankman(999)

    def test_the_bin_holds_only_as_many_as_the_shop_published(self):
        state = self._state(
            barracks=dict((300 + index, b'tman:%d' % (300 + index))
                          for index in range(4)))
        state.snapshot()['tankmenRestoreConfig']['limit'] = 2

        for index in range(4):
            state.dismiss_tankman(300 + index)

        self.assertEqual(2, len(state.snapshot()['recycleBinTankmen']))

    def test_a_sold_vehicles_dismissed_crew_lands_in_the_bin(self):
        """#1513 counts these separately but recovers them the same way."""
        state = self._state()
        snapshot = state.snapshot()
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['vehicleTypeCompactDescr'] = 50002
        second['crew'] = [301, 302]
        second['tankmen'] = {301: b'tman:301', 302: b'tman:302'}
        snapshot['vehicles'].append(second)
        snapshot['shopItemPrices'][50002] = {'credits': 0, 'gold': 0}

        state.sell_vehicle(10, dismiss_crew=True)

        self.assertEqual(
            set([301, 302]), set(state.snapshot()['recycleBinTankmen']))

    def test_a_new_recruit_never_takes_a_dismissed_members_id(self):
        """One collection is keyed by id over the crew and the bin together.

        ``ItemsRequester.getTankmen`` builds ``result[invID]`` from the active
        crew and then from the recycle bin, so a reissued id would put a
        dismissed crew member where a live one belongs.
        """
        state = self._state(barracks={})
        snapshot = state.snapshot()
        snapshot['barracksTankmen'] = {103: b'tman:103'}
        snapshot['shopItemPrices'][50001] = {'credits': 0, 'gold': 0}
        snapshot['tankmanCosts'] = [
            {'credits': 0, 'gold': 0, 'roleLevel': 50,
             'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
             'isPremium': False}] * 3
        snapshot['unlockItemCompactDescrs'] = {50001}
        state.dismiss_tankman(103)

        # 1 is the commander, the fixture vehicle's first seat.
        recruited = state.buy_tankman(50001, 1, 0)

        self.assertNotIn(recruited, state.snapshot()['recycleBinTankmen'])

    # ---- the skill reset ------------------------------------------------

    def _skilled(self, gold=10000, credits_amount=1000000):
        state = self._state(gold=gold)
        state.snapshot()['wallet']['credits'] = credits_amount
        state.add_tankman_skill(201, 6)
        rows = state.snapshot()['barracksTankmen']
        rows[201] = rows[201] + b'#1000'
        return state

    def test_the_dearest_reset_keeps_everything_and_costs_gold(self):
        state = self._skilled()

        state.drop_tankman_skills(201, 2)

        descriptor = _TankmanDescriptor(
            state.snapshot()['barracksTankmen'][201])
        self.assertEqual([], descriptor.skills)
        self.assertEqual(1000, descriptor.totalXP())
        self.assertEqual(10000 - 200, state.snapshot()['wallet']['gold'])

    def test_the_middle_reset_keeps_most_of_it_and_costs_credits(self):
        state = self._skilled()

        state.drop_tankman_skills(201, 1)

        descriptor = _TankmanDescriptor(
            state.snapshot()['barracksTankmen'][201])
        self.assertEqual(800, descriptor.totalXP())
        self.assertEqual(
            1000000 - 200000, state.snapshot()['wallet']['credits'])
        self.assertEqual(10000, state.snapshot()['wallet']['gold'])

    def test_the_free_reset_gives_the_experience_up_instead(self):
        """Nothing is free; the cheapest choice is paid for in experience."""
        state = self._skilled()

        state.drop_tankman_skills(201, 0)

        descriptor = _TankmanDescriptor(
            state.snapshot()['barracksTankmen'][201])
        self.assertEqual(0, descriptor.totalXP())
        self.assertEqual(1000000, state.snapshot()['wallet']['credits'])
        self.assertEqual(10000, state.snapshot()['wallet']['gold'])

    def test_a_crew_member_with_nothing_to_lose_is_not_charged(self):
        """``TankmanDescr.isFreeDropSkills`` is the client's own exemption."""
        state = self._state()

        state.drop_tankman_skills(201, 2)

        self.assertEqual(10000, state.snapshot()['wallet']['gold'])

    def test_a_reset_the_account_cannot_pay_for_changes_nothing(self):
        state = self._skilled(gold=50)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.drop_tankman_skills(201, 2)

        self.assertEqual(before, state.snapshot())

    def test_a_reset_choice_the_shop_never_offered_is_refused(self):
        state = self._skilled()

        with self.assertRaises(self.garage.GarageError):
            state.drop_tankman_skills(201, 7)

    # ---- the role change ------------------------------------------------

    def test_a_role_change_costs_the_price_the_client_shows(self):
        state = self._state()

        state.change_tankman_role(201, 3, 50002)

        descriptor = _TankmanDescriptor(
            state.snapshot()['barracksTankmen'][201])
        self.assertEqual('driver', descriptor.role)
        self.assertEqual(2, descriptor.vehicleTypeID)
        self.assertEqual(10000 - 600, state.snapshot()['wallet']['gold'])

    def test_a_role_change_the_account_cannot_pay_for_changes_nothing(self):
        state = self._state(gold=100)
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            state.change_tankman_role(201, 3, 50002)

        self.assertEqual(before, state.snapshot())

    def test_a_role_a_vehicle_has_no_seat_for_is_refused(self):
        state = self._state()

        with self.assertRaises(self.garage.GarageError):
            # 5 is the loader; this fixture's vehicle seats two.
            state.change_tankman_role(201, 5, 50002)

    def test_a_group_that_offers_one_role_cannot_change_it(self):
        """``tankmen.tankmenGroupCanChangeRole`` is the client's own rule."""
        state = self._state()
        vehicles, tankmen = _modules()
        tankmen.tankmenGroupCanChangeRole = (
            lambda nationID, groupID, isPremium: False)
        state._tankmen = tankmen

        with self.assertRaises(self.garage.GarageError):
            state.change_tankman_role(201, 3, 50002)

    def test_a_seated_crew_member_keeps_the_seat_they_still_fit(self):
        """The restore boundary requires a seat and its occupant to match."""
        state = self._state()

        with self.assertRaises(self.garage.GarageError):
            # 101 is the commander of the fixture's vehicle; a driver does
            # not belong in the commander's seat.
            state.change_tankman_role(101, 3, 50001)

    def test_a_role_index_that_names_a_skill_is_refused(self):
        state = self._state()

        with self.assertRaises(self.garage.GarageError):
            # 6 is 'repair', a skill rather than a crew role.
            state.change_tankman_role(201, 6, 50002)

    # ---- the passport ---------------------------------------------------

    def test_a_passport_replacement_costs_what_the_shop_shows(self):
        state = self._state()

        state.change_tankman_passport(201, 0, -1, 0, 11, 0, 22, 0, 33)

        descriptor = _TankmanDescriptor(
            state.snapshot()['barracksTankmen'][201])
        self.assertEqual((11, 22, 33), (descriptor.firstNameID,
                                        descriptor.lastNameID,
                                        descriptor.iconID))
        self.assertFalse(descriptor.isFemale)
        self.assertEqual(10000 - 50, state.snapshot()['wallet']['gold'])

    def test_a_field_the_player_left_alone_arrives_as_minus_one(self):
        state = self._state()

        state.change_tankman_passport(201, 0, -1, 0, 11, 0, -1, 0, -1)

        descriptor = _TankmanDescriptor(
            state.snapshot()['barracksTankmen'][201])
        self.assertEqual((11, 2, 3), (descriptor.firstNameID,
                                      descriptor.lastNameID,
                                      descriptor.iconID))

    def test_a_female_crew_members_passport_costs_more(self):
        state = self._state()
        state.change_tankman_passport(201, 0, 1, 0, 11, 0, 22, 0, 33)
        spent = 10000 - state.snapshot()['wallet']['gold']
        self.assertEqual(50, spent)

        # She is female now, so the second replacement is the dearer one.
        state.change_tankman_passport(201, 0, -1, 0, 12, 0, 23, 0, 34)

        self.assertEqual(
            10000 - 50 - 500, state.snapshot()['wallet']['gold'])

    def test_a_passport_the_client_refuses_charges_nothing(self):
        state = self._state()
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            # Name group 7 is not one this account may buy from.
            state.change_tankman_passport(201, 0, -1, 7, 11, 0, 22, 0, 33)

        self.assertEqual(before, state.snapshot())

    def test_a_passport_naming_the_wrong_crew_kind_is_refused(self):
        state = self._state()

        with self.assertRaises(self.garage.GarageError):
            state.change_tankman_passport(201, 1, -1, 0, 11, 0, 22, 0, 33)


class CrewShopFailureTests(unittest.TestCase):
    """A crew command the client refuses halfway must not cost money.

    ``_fitting`` reports one result for the whole command and has no way to
    undo a charge, so every one of these charges only once the client has
    accepted the change and written it down.
    """

    def setUp(self):
        unused_requests, unused_commands, self.garage = _request_modules()

    def _state(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {'credits': 1000000, 'gold': 10000, 'freeXP': 0}
        snapshot['accountBerths'] = 5
        snapshot['barracksTankmen'] = {201: b'tman:201'}
        vehicles, tankmen = _modules()

        class _Refusing(_TankmanDescriptor):
            def makeCompactDescr(self):
                raise ValueError('the descriptor could not be written')

        tankmen.TankmanDescr = _Refusing
        return self.garage.GarageState(
            snapshot, vehicles_module=vehicles, tankmen_module=tankmen)

    def _assert_unchanged(self, mutate):
        state = self._state()
        before = copy.deepcopy(state.snapshot())

        with self.assertRaises(self.garage.GarageError):
            mutate(state)

        self.assertEqual(before, state.snapshot())

    def test_a_role_change_the_client_cannot_write_down_costs_nothing(self):
        self._assert_unchanged(
            lambda state: state.change_tankman_role(201, 3, 50002))

    def test_a_passport_the_client_cannot_write_down_costs_nothing(self):
        self._assert_unchanged(
            lambda state: state.change_tankman_passport(
                201, 0, -1, 0, 11, 0, 22, 0, 33))

    def test_a_reset_the_client_cannot_write_down_costs_nothing(self):
        self._assert_unchanged(
            lambda state: state.drop_tankman_skills(201, 2))


class StockRuleTests(unittest.TestCase):
    """The builders and the garage must agree on what the account owns."""

    def test_the_record_factory_and_the_garage_share_one_rule(self):
        unused_requests, unused_commands, garage = _request_modules()
        records = _load_port_module('vehicle_records')

        self.assertEqual(
            set(garage.STOCKED_ITEM_TYPES),
            set(records.STOCKED_ITEM_TYPES))


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

    def _restart(self, fresh=None):
        """Rebuild the bootstrap snapshot and overlay the saved garage."""
        fresh = copy.deepcopy(SNAPSHOT) if fresh is None else fresh
        self._store().apply(fresh)
        return fresh

    @staticmethod
    def _matching_snapshot():
        """A freshly built garage whose load is what its own gun fires.

        This is what the next start hands the restore: the stock fitting, the
        stock rounds and a depot that holds exactly them.
        """
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {'credits': 100000, 'gold': 0, 'freeXP': 0}
        record = snapshot['vehicles'][0]
        record['shells'] = [20010, 30, 20011, 15]
        record['shellsLayout'] = {(7001, 7002): [20010, 30, 20011, 15]}
        record['inventoryItems'][10] = {20010: 30, 20011: 15}
        snapshot['inventoryItems'][10] = {20010: 30, 20011: 15}
        for compact_descr in (20010, 20011):
            snapshot['shopItemPrices'][compact_descr] = {'credits': 100}
        return snapshot

    def _two_vehicle_snapshot(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        second = copy.deepcopy(snapshot['vehicles'][0])
        second['id'] = 10
        second['vehicleTypeCompactDescr'] = 50002
        snapshot['vehicles'].append(second)
        snapshot['vehicleTypeCompactDescrs'] = {50001, 50002}
        snapshot['shopItemPrices'][50002] = {'credits': 0, 'gold': 0}
        return snapshot

    def test_the_rounds_a_battle_fired_stay_spent_across_a_restart(self):
        """A restart that refilled the racks would be free ammunition."""
        state = self._state(self._matching_snapshot())
        state.settle_battle_ammunition(50001, {0: 12, 1: 3})
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        restored = self._restart(self._matching_snapshot())

        self.assertEqual(
            {20010: 18, 20011: 12}, restored['inventoryItems'][10])
        self.assertEqual(
            [20010, 18, 20011, 12], list(restored['vehicles'][0]['shells']))

    def test_the_layout_a_battle_emptied_survives_a_restart(self):
        """It is what auto-load and the resupply button buy back."""
        state = self._state(self._matching_snapshot())
        state.settle_battle_ammunition(50001, {0: 12, 1: 3})

        self.assertEqual(
            {(7001, 7002): [20010, 30, 20011, 15]},
            state.snapshot()['vehicles'][0]['shellsLayout'])
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        restored = self._restart(self._matching_snapshot())

        self.assertEqual(
            {(7001, 7002): [20010, 30, 20011, 15]},
            restored['vehicles'][0]['shellsLayout'])

    def test_a_rack_a_battle_emptied_comes_back_empty(self):
        state = self._state(self._matching_snapshot())
        state.settle_battle_ammunition(50001, {0: 30, 1: 15})
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        restored = self._restart(self._matching_snapshot())

        self.assertEqual(
            [20010, 0, 20011, 0], list(restored['vehicles'][0]['shells']))
        self.assertEqual(
            {20010: 0, 20011: 0}, restored['inventoryItems'][10])

    def test_the_consumable_a_battle_used_stays_used_across_a_restart(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['inventoryItems'][11] = {11001: 2}
        state = self._state(snapshot)
        state.equip_equipments(9, [11001, 0, 0])
        state.settle_battle_consumables(50001, [11001])
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        fresh = copy.deepcopy(SNAPSHOT)
        fresh['inventoryItems'][11] = {11001: 2}
        restored = self._restart(fresh)

        self.assertEqual({11001: 1}, restored['inventoryItems'][11])
        self.assertEqual([0, 0, 0], list(restored['vehicles'][0]['eqs']))
        self.assertEqual(
            [11001, 0, 0], list(restored['vehicles'][0]['eqsLayout']))

    def test_a_restored_vehicle_says_which_consumables_it_holds(self):
        """Otherwise a second vehicle mounts the same one for nothing."""
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['inventoryItems'][11] = {11001: 1}
        state = self._state(snapshot)
        state.equip_equipments(9, [11001, 0, 0])
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        fresh = copy.deepcopy(SNAPSHOT)
        fresh['inventoryItems'][11] = {11001: 1}
        restored = self._restart(fresh)

        self.assertEqual(
            {11001: 1}, restored['vehicles'][0]['inventoryItems'][11])

    def test_a_bought_round_the_stock_garage_never_had_survives(self):
        """A save may hold what this start's fresh build did not carry."""
        snapshot = self._matching_snapshot()
        snapshot['shopItemPrices'][20012] = {'credits': 500}
        state = self._state(snapshot)
        record = state.snapshot()['vehicles'][0]
        record['shells'] = [20010, 30, 20012, 5]
        record['inventoryItems'][10] = {20010: 30, 20012: 5}
        state.snapshot()['inventoryItems'][10] = {20010: 30, 20012: 5}
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        fresh = self._matching_snapshot()
        fresh['shopItemPrices'][20012] = {'credits': 500}
        restored = self._restart(fresh)

        self.assertEqual(
            {20010: 30, 20012: 5}, restored['inventoryItems'][10])
        self.assertEqual(
            [20010, 30, 20012, 5], list(restored['vehicles'][0]['shells']))

    def test_a_saved_round_this_client_no_longer_prices_is_dropped(self):
        snapshot = self._matching_snapshot()
        snapshot['shopItemPrices'][20012] = {'credits': 500}
        state = self._state(snapshot)
        state.snapshot()['inventoryItems'][10][20012] = 5
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        restored = self._restart(self._matching_snapshot())

        self.assertNotIn(20012, restored['inventoryItems'][10])

    def test_a_save_written_before_the_depot_keeps_the_stock_supply(self):
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(copy.deepcopy(SNAPSHOT)))
        import json
        with io.open(self.path, encoding='utf-8') as stream:
            saved = json.load(stream)
        del saved['owned']['10']
        with io.open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(saved))

        restored = self._restart()

        self.assertEqual(
            SNAPSHOT['inventoryItems'][10], restored['inventoryItems'][10])

    def test_a_damaged_vehicle_comes_back_damaged(self):
        """A restart that repaired the tank would be a free repair."""
        state = self._state()
        state.settle_battle_damage(50001, 400)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        restored = self._restart()

        self.assertEqual([1200, 400], list(restored['vehicles'][0]['repair']))

    def test_a_save_written_before_the_repair_state_restores_undamaged(self):
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(copy.deepcopy(SNAPSHOT)))
        import json
        with io.open(self.path, encoding='utf-8') as stream:
            saved = json.load(stream)
        for stored in saved['vehicles'].values():
            stored.pop('repair', None)
        with io.open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(saved))

        restored = self._restart()

        self.assertEqual((0, 1000), restored['vehicles'][0]['repair'])

    def test_an_unloaded_crew_survives_a_restart_in_the_barracks(self):
        """A seat left out of the save would be refilled by the next start."""
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        state = self._state(snapshot)
        state.equip_tankman(9, 0, -1)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        restored = self._restart()

        record = restored['vehicles'][0]
        self.assertEqual([None, 102], record['crew'])
        self.assertNotIn(101, record['tankmen'])
        self.assertEqual(
            [b'tman:101'], sorted(restored['barracksTankmen'].values()))
        self.assertEqual(4, restored['accountBerths'])

    def test_a_restored_barracks_never_reuses_a_seated_inventory_id(self):
        """Two claims on one id make the whole next restore invalid."""
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        snapshot['barracksTankmen'] = {201: b'tman:201'}
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(snapshot))

        restored = self._restart()

        seated = set(restored['vehicles'][0]['tankmen'])
        self.assertEqual(set(), seated & set(restored['barracksTankmen']))

    def test_a_save_written_before_the_barracks_restores_without_one(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(snapshot))
        with io.open(self.path, encoding='utf-8') as stream:
            import json
            saved = json.load(stream)
        del saved['ledger']['barracks']
        saved['schema'] = 5
        with io.open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(saved))

        restored = self._restart()

        self.assertEqual({}, restored.get('barracksTankmen', {}))
        self.assertEqual([101, 102], restored['vehicles'][0]['crew'])

    def test_the_saved_garage_names_every_vehicle_the_account_owns(self):
        """Startup rebuilds the garage from this list, so it must be whole.

        A saved set that dropped even one vehicle would look exactly like a
        vehicle the player had sold, and the next start would build a garage
        without it.
        """
        snapshot = self._two_vehicle_snapshot()
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(snapshot))

        self.assertEqual(
            set(int(record['vehicleTypeCompactDescr'])
                for record in snapshot['vehicles']),
            set(self._store().owned_vehicle_types()))

    def test_a_sold_vehicle_leaves_the_saved_garage(self):
        snapshot = self._two_vehicle_snapshot()
        state = self._state(snapshot)
        state.sell_vehicle(10)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        owned = set(self._store().owned_vehicle_types())
        self.assertNotIn(50002, owned)
        self.assertEqual({50001}, owned)

    def test_the_saved_garage_names_the_vehicles_it_holds(self):
        """The launcher reads this file with no client to resolve ids with."""
        snapshot = self._two_vehicle_snapshot()
        for record in snapshot['vehicles']:
            record['vehicleTypeName'] = 'ussr:R%d' % record['id']
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(snapshot))

        self.assertEqual(
            ['ussr:R10', 'ussr:R9'],
            self._store().owned_vehicle_names())

    def test_a_save_written_before_names_reports_the_ones_it_has(self):
        snapshot = self._two_vehicle_snapshot()
        snapshot['vehicles'][0]['vehicleTypeName'] = 'ussr:R11_MS-1'
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(snapshot))

        self.assertEqual(
            ['ussr:R11_MS-1'], self._store().owned_vehicle_names())
        # The compact descriptors are unaffected: ownership still resolves.
        self.assertEqual(
            {50001, 50002}, set(self._store().owned_vehicle_types()))

    def test_an_unwritten_save_owns_nothing_rather_than_guessing(self):
        """A first launch has to fall back to the save's seed."""
        self.assertEqual([], self._store().owned_vehicle_types())

    def test_a_full_loadout_survives_a_restart(self):
        state = self._state()
        state.equip_optional_device(9, 9001, 0)
        state.equip_optional_device(9, 9002, 1)
        state.equip_equipments(9, [11001, 0, 0])
        state.equip_shells(9, [10010, 38, 10011, 9])
        state.set_layouts(
            9, [-10010, 38, 10011, 9], 0, [-11001, 1, 0, 0, 0, 0, 0, 0])
        state.add_tankman_skill(101, 9)
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
        self.assertEqual({(7001, 7002): [-10010, 38, 10011, 9]},
                         restored['shellsLayout'])
        self.assertEqual([-11001, 0, 0], restored['eqsLayout'])
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
        # The gun was fitted and never loaded, so the rack is still empty and
        # the layout still names what buying it back would cost.
        self.assertEqual([20010, 0, 20011, 0], restored['shells'])
        self.assertEqual({20010: 0, 20011: 0},
                         restored['inventoryItems'][10])
        self.assertEqual(
            [20010, 30, 20011, 15],
            restored['shellsLayout'][(7001, 4444)])
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
        vehicles, tankmen = _modules()
        store = self._store()

        first = store.apply_battle_crew_xp(
            snapshot, 'server:7:1', 50001, 100, 1,
            tankmen_module=tankmen, vehicles_module=vehicles)
        duplicate = store.apply_battle_crew_xp(
            snapshot, 'server:7:1', 50001, 100, 1,
            tankmen_module=tankmen, vehicles_module=vehicles)

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
            tankmen_module=tankmen, vehicles_module=vehicles)
        self.assertFalse(after_restart['applied'])
        self.assertEqual(200, _TankmanDescriptor(
            restarted['vehicles'][0]['tankmen'][101]).totalXP())
        self.assertEqual(100, _TankmanDescriptor(
            restarted['vehicles'][0]['tankmen'][102]).totalXP())

    def test_a_retried_receipt_bills_the_same_damage_only_once(self):
        """The award, the earnings and the damage share one transaction."""
        snapshot = copy.deepcopy(SNAPSHOT)
        vehicles, tankmen = _modules()
        store = self._store()

        first = store.apply_battle_crew_xp(
            snapshot, 'server:8:1', 50001, 100, 1, tankmen_module=tankmen,
            health=400, vehicles_module=vehicles)
        duplicate = store.apply_battle_crew_xp(
            snapshot, 'server:8:1', 50001, 100, 1, tankmen_module=tankmen,
            health=400, vehicles_module=vehicles)

        self.assertTrue(first['applied'])
        self.assertEqual((1200, 400), first['repair'])
        self.assertFalse(duplicate['applied'])
        # The second application must not settle a second time against the
        # already-damaged vehicle.
        self.assertEqual((1200, 400), snapshot['vehicles'][0]['repair'])

    def _settling_snapshot(self, credits_amount=100000):
        """A vehicle with all three refill switches on and one consumable."""
        snapshot = self._matching_snapshot()
        snapshot['wallet']['credits'] = credits_amount
        snapshot['shopItemPrices'][11001] = {'credits': 3000}
        snapshot['inventoryItems'][11] = {11001: 1}
        record = snapshot['vehicles'][0]
        # AccountCommands.VEHICLE_SETTINGS_FLAG: XP_TO_TMAN 1, AUTO_REPAIR 2,
        # AUTO_LOAD 4, AUTO_EQUIP 8.  A fresh garage vehicle carries all four.
        record['settings'] = 15
        record['eqs'] = [11001, 0, 0]
        record['eqsLayout'] = [11001, 0, 0]
        record['inventoryItems'][11] = {11001: 1}
        return snapshot

    def test_a_battle_repairs_reloads_and_restocks_what_it_can(self):
        """Retail settles all three switches at the end of a battle."""
        snapshot = self._settling_snapshot()
        vehicles, tankmen = _modules()

        self._store().apply_battle_crew_xp(
            snapshot, 'server:11:1', 50001, 100, 1, tankmen_module=tankmen,
            health=400, vehicles_module=vehicles, shells_fired={0: 12},
            equipment_used=[11001], auto_settings=(2, 4, 8))

        record = snapshot['vehicles'][0]
        self.assertEqual((0, 1000), tuple(record['repair']))
        self.assertEqual([20010, 30, 20011, 15], list(record['shells']))
        self.assertEqual([11001, 0, 0], list(record['eqs']))
        # A 1200-credit repair, twelve rounds at 100 and one 3000 consumable.
        self.assertEqual(
            100000 - 1200 - 1200 - 3000, snapshot['wallet']['credits'])

    def test_a_battle_leaves_the_switches_the_player_turned_off_alone(self):
        snapshot = self._settling_snapshot()
        snapshot['vehicles'][0]['settings'] = 1
        vehicles, tankmen = _modules()

        self._store().apply_battle_crew_xp(
            snapshot, 'server:12:1', 50001, 100, 1, tankmen_module=tankmen,
            health=400, vehicles_module=vehicles, shells_fired={0: 12},
            equipment_used=[11001], auto_settings=(2, 4, 8))

        record = snapshot['vehicles'][0]
        self.assertEqual((1200, 400), tuple(record['repair']))
        self.assertEqual([20010, 18, 20011, 15], list(record['shells']))
        self.assertEqual([0, 0, 0], list(record['eqs']))
        self.assertEqual(100000, snapshot['wallet']['credits'])

    def test_an_account_that_cannot_pay_keeps_the_battle_settlement(self):
        """A refused refill is what the player sees, not a failed battle."""
        snapshot = self._settling_snapshot(credits_amount=1500)
        vehicles, tankmen = _modules()

        result = self._store().apply_battle_crew_xp(
            snapshot, 'server:13:1', 50001, 100, 1, tankmen_module=tankmen,
            health=400, vehicles_module=vehicles, shells_fired={0: 12},
            equipment_used=[11001], auto_settings=(2, 4, 8))

        self.assertTrue(result['applied'])
        record = snapshot['vehicles'][0]
        # The repair fits; the rounds and the consumable no longer do.
        self.assertEqual((0, 1000), tuple(record['repair']))
        self.assertEqual([20010, 18, 20011, 15], list(record['shells']))
        self.assertEqual([0, 0, 0], list(record['eqs']))
        self.assertEqual(300, snapshot['wallet']['credits'])

    def test_a_battle_reports_the_depot_rows_it_moved(self):
        """The client only drops what the pushed diff names."""
        snapshot = self._settling_snapshot()
        vehicles, tankmen = _modules()

        result = self._store().apply_battle_crew_xp(
            snapshot, 'server:14:1', 50001, 100, 1, tankmen_module=tankmen,
            health=400, vehicles_module=vehicles, shells_fired={0: 12},
            equipment_used=[11001], auto_settings=(2, 4, 8))

        self.assertEqual([20010, 20011], result['touched_items'][10])
        self.assertEqual([11001], result['touched_items'][11])

    def _earning_snapshot(self, percent=None, vehicle_type=50001):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['wallet'] = {'credits': 0, 'gold': 0, 'freeXP': 0}
        snapshot['vehicles'][0]['vehicleTypeCompactDescr'] = vehicle_type
        snapshot['vehicleTypeCompactDescrs'] = {vehicle_type}
        snapshot['shopItemPrices'][vehicle_type] = {'credits': 0, 'gold': 0}
        if percent is not None:
            snapshot['earningsPercent'] = percent
        return snapshot

    REWARDS = {'credits': 1000, 'xp': 200, 'free_xp': 10}

    def _settle(self, snapshot, receipt_id):
        vehicles, tankmen = _modules()
        return self._store().apply_battle_crew_xp(
            snapshot, receipt_id,
            int(snapshot['vehicles'][0]['vehicleTypeCompactDescr']),
            self.REWARDS['xp'], 1, tankmen_module=tankmen,
            rewards=dict(self.REWARDS), vehicles_module=vehicles)

    def test_a_save_without_a_multiplier_earns_what_the_battle_paid(self):
        snapshot = self._earning_snapshot()

        result = self._settle(snapshot, 'server:20:1')

        self.assertEqual(1000, snapshot['wallet']['credits'])
        self.assertEqual(10, snapshot['wallet']['freeXP'])
        self.assertEqual(200, snapshot['vehicleXP'][50001])
        self.assertEqual(
            {'credits': 1000, 'xp': 200, 'free_xp': 10}, result['awarded'])

    def test_the_launchers_multiplier_moves_credits_and_experience(self):
        """One number on the save, applied to everything a battle pays."""
        snapshot = self._earning_snapshot(percent=250)

        result = self._settle(snapshot, 'server:21:1')

        self.assertEqual(2500, snapshot['wallet']['credits'])
        self.assertEqual(25, snapshot['wallet']['freeXP'])
        self.assertEqual(500, snapshot['vehicleXP'][50001])
        self.assertEqual(
            {'credits': 2500, 'xp': 500, 'free_xp': 25}, result['awarded'])
        # The crew was trained on the multiplied experience too: #1513 gives
        # each crew member the battle's whole experience, not a share of it.
        self.assertEqual(500, _TankmanDescriptor(
            snapshot['vehicles'][0]['tankmen'][101]).totalXP())

    def test_a_gold_tank_earns_more_credits_and_the_same_experience(self):
        """#1513 calls it premium; retail sells it for the credits."""
        snapshot = self._earning_snapshot(vehicle_type=PREMIUM_VEHICLE_CD)

        result = self._settle(snapshot, 'server:22:1')

        self.assertEqual(1500, snapshot['wallet']['credits'])
        self.assertEqual(200, snapshot['vehicleXP'][PREMIUM_VEHICLE_CD])
        self.assertEqual(10, snapshot['wallet']['freeXP'])
        self.assertEqual(
            {'credits': 1500, 'xp': 200, 'free_xp': 10}, result['awarded'])

    def test_a_gold_tank_on_a_multiplied_save_takes_both(self):
        snapshot = self._earning_snapshot(
            percent=200, vehicle_type=PREMIUM_VEHICLE_CD)

        self._settle(snapshot, 'server:23:1')

        self.assertEqual(3000, snapshot['wallet']['credits'])
        self.assertEqual(400, snapshot['vehicleXP'][PREMIUM_VEHICLE_CD])

    def test_a_multiplier_the_save_cannot_mean_is_ignored(self):
        snapshot = self._earning_snapshot(percent='lots')

        self._settle(snapshot, 'server:24:1')

        self.assertEqual(1000, snapshot['wallet']['credits'])

    def test_a_receipt_with_no_earnings_row_still_trains_on_the_multiplier(
            self):
        """Crew experience is experience, whether or not credits came with it.

        A receipt written before the earnings row existed carries only the
        battle experience, and that is still what the save multiplies.
        """
        snapshot = self._earning_snapshot(percent=250)
        unused_vehicles, tankmen = _modules()

        self._store().apply_battle_crew_xp(
            snapshot, 'server:25:1', 50001, 200, 1, tankmen_module=tankmen)

        # Only the earnings row banks vehicle experience, so a receipt
        # without one trains the crew and pays nothing.
        self.assertEqual(500, _TankmanDescriptor(
            snapshot['vehicles'][0]['tankmen'][101]).totalXP())
        self.assertNotIn('vehicleXP', snapshot)

    def test_a_dismissed_crew_member_survives_a_restart(self):
        """Retail keeps the recovery window across a session, so this does."""
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['barracksTankmen'] = {201: b'tman:201'}
        snapshot['accountBerths'] = 5
        state = self._state(snapshot)
        state.dismiss_tankman(201)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        fresh = copy.deepcopy(SNAPSHOT)
        self.assertTrue(self._store().apply(fresh))

        recycled = fresh['recycleBinTankmen']
        self.assertEqual(1, len(recycled))
        descriptor, dismissed_at = list(recycled.values())[0]
        self.assertEqual(b'tman:201', descriptor)
        self.assertGreater(dismissed_at, 0)

    def test_a_restored_bin_uses_ids_nothing_else_in_the_garage_holds(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['barracksTankmen'] = {201: b'tman:201', 202: b'tman:202'}
        snapshot['accountBerths'] = 5
        state = self._state(snapshot)
        state.dismiss_tankman(202)
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))

        fresh = copy.deepcopy(SNAPSHOT)
        self.assertTrue(self._store().apply(fresh))

        used = set(fresh['barracksTankmen'])
        used.update(
            int(tankman_id)
            for record in fresh['vehicles']
            for tankman_id in (record.get('tankmen') or ()))
        self.assertFalse(used & set(fresh['recycleBinTankmen']))

    def test_a_save_written_before_the_bin_existed_restores_empty(self):
        state = self._state()
        store = self._store()
        store.mark_dirty()
        self.assertTrue(store.flush(state.snapshot()))
        with io.open(self.path, 'r', encoding='utf-8') as stream:
            saved = json.loads(stream.read())
        saved['ledger'].pop('recycleBin', None)
        with io.open(self.path, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(saved))

        fresh = copy.deepcopy(SNAPSHOT)
        self.assertTrue(self._store().apply(fresh))

        self.assertEqual({}, fresh['recycleBinTankmen'])

    def test_a_receipt_without_a_health_reading_bills_nothing(self):
        """A receipt written before the settlement existed stays readable."""
        snapshot = copy.deepcopy(SNAPSHOT)
        unused_vehicles, tankmen = _modules()
        store = self._store()

        result = store.apply_battle_crew_xp(
            snapshot, 'server:9:1', 50001, 100, 1, tankmen_module=tankmen)

        self.assertNotIn('repair', result)
        self.assertEqual((0, 1000), snapshot['vehicles'][0]['repair'])

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
        # Both publications report an empty depot beside the loaded rounds;
        # the delta uses the client's removal marker for a zero balance.
        self.assertEqual(
            {key: count or None for key, count in full['inventory'][10].items()},
            narrow['inventory'][10])

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

    def _moved_to_barracks(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        state = self.garage.GarageState(
            snapshot, vehicles_module=self.vehicles,
            tankmen_module=self.tankmen)
        state.equip_tankman(9, 0, -1)
        return state

    def test_a_crew_member_sent_to_the_barracks_is_moved_not_removed(self):
        """synchronizeDicts pops the key a diff carries as None.

        Publishing the unloaded crew member that way would delete them from
        the client's cache; the account still holds them, so the diff has to
        say where they went instead.
        """
        state = self._moved_to_barracks()

        delta = self.data.inventory(
            state.snapshot(), validate=False,
            only_vehicles=state.touched_vehicles(), only_items={},
            touched_tankmen=state.touched_tankmen())

        tankmen = delta['inventory'][self.data.TANKMAN_ITEM_TYPE]
        self.assertEqual(b'tman:101', tankmen['compDescr'][101])
        self.assertEqual(
            self.data.BARRACKS_VEHICLE_ID, tankmen['vehicle'][101])
        # The seat itself is now empty.
        vehicle_type = self.data.VEHICLE_ITEM_TYPE
        self.assertEqual(
            [None, 102], delta['inventory'][vehicle_type]['crew'][9])

    def test_a_dismissed_crew_member_is_still_removed(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        state = self.garage.GarageState(
            snapshot, vehicles_module=self.vehicles,
            tankmen_module=self.tankmen)
        state.dismiss_tankman(101)

        delta = self.data.inventory(
            state.snapshot(), validate=False,
            only_vehicles=state.touched_vehicles(), only_items={},
            touched_tankmen=state.touched_tankmen())

        tankmen = delta['inventory'][self.data.TANKMAN_ITEM_TYPE]
        self.assertIsNone(tankmen['compDescr'][101])
        self.assertIsNone(tankmen['vehicle'][101])

    def test_an_untouched_barracks_stays_out_of_a_delta(self):
        """Every published descriptor evicts one GUI item."""
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        snapshot['barracksTankmen'] = {201: b'tman:201'}

        delta = self.data.inventory(
            snapshot, only_vehicles=set([9]), only_items={})

        tankmen = delta['inventory'][self.data.TANKMAN_ITEM_TYPE]
        self.assertNotIn(201, tankmen['compDescr'])

    def test_a_full_sync_publishes_the_whole_barracks(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        snapshot['barracksTankmen'] = {201: b'tman:201'}

        full = self.data.inventory(snapshot)

        tankmen = full['inventory'][self.data.TANKMAN_ITEM_TYPE]
        self.assertEqual(b'tman:201', tankmen['compDescr'][201])
        self.assertEqual(
            self.data.BARRACKS_VEHICLE_ID, tankmen['vehicle'][201])

    def test_a_barracks_larger_than_its_berths_is_refused(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 1
        snapshot['barracksTankmen'] = {201: b'tman:201', 202: b'tman:202'}

        with self.assertRaises(ValueError):
            self.data.inventory(snapshot)

    def test_one_crew_member_cannot_be_in_a_tank_and_the_barracks_at_once(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['accountBerths'] = 4
        snapshot['barracksTankmen'] = {101: b'tman:101'}

        with self.assertRaises(ValueError):
            self.data.inventory(snapshot)
