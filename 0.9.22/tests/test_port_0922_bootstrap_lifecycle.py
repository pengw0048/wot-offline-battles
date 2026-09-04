import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')
BOOTSTRAP = PACKAGE_ROOT / 'bootstrap.py'


def _real_module(name):
    spec = importlib.util.spec_from_file_location(
        'gui.mods.offline_lan_0922.' + name, PACKAGE_ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VEHICLE_BLACKLIST = _real_module('vehicle_blacklist')
VEHICLE_CONFIGURATION = _real_module('vehicle_configuration')


MAX_SKILL_LEVEL = 100
# The exact #1513 tankmen._makeLevelXpCosts, with _LEVELUP_K1/_LEVELUP_K2.
SKILL_XP_COSTS = [0]
for _level in range(1, MAX_SKILL_LEVEL + 1):
    SKILL_XP_COSTS.append(SKILL_XP_COSTS[-1] + int(round(
        50.0 * pow(100.0, float(_level - 1) / MAX_SKILL_LEVEL))))


class _TankmanDescr(object):
    """Reproduces the #1513 TankmanDescr skill/XP surface bootstrap uses."""

    def __init__(self, compact_descr):
        passport, _, tail = compact_descr.partition(b'|')
        skills, _, free_xp = tail.partition(b'|')
        nation_id, vehicle_type_id, role = passport.decode('ascii').split(':')
        self.nationID = int(nation_id)
        self.vehicleTypeID = int(vehicle_type_id)
        self.role = role
        self._passport = passport
        self.skills = [name for name in skills.decode('ascii').split(',')
                       if name]
        self.freeSkillsNumber = 0
        self.freeXP = int(free_xp or 0)

    @property
    def lastSkillNumber(self):
        return len(self.skills)

    @staticmethod
    def levelUpXpCost(from_skill_level, skill_sequence_number):
        return 2 ** skill_sequence_number * (
            SKILL_XP_COSTS[from_skill_level + 1] -
            SKILL_XP_COSTS[from_skill_level])

    def addXP(self, amount):
        self.freeXP += int(amount)

    def makeCompactDescr(self):
        return b'%s|%s|%d' % (self._passport,
                              ','.join(self.skills).encode('ascii'),
                              self.freeXP)


class _ProgressTankmanDescr(object):
    """A compact fake that also retains #1513's current skill progress."""

    def __init__(self, compact_descr):
        fields = compact_descr.split(b'|')
        self._passport = fields[0]
        self.skills = [name for name in fields[1].decode('ascii').split(',')
                       if name]
        self.freeXP = int(fields[2])
        self.lastSkillLevel = int(fields[3])
        self.freeSkillsNumber = int(fields[4])
        self.roleLevel = int(fields[5])

    @property
    def lastSkillNumber(self):
        return len(self.skills)

    @staticmethod
    def levelUpXpCost(from_skill_level, skill_sequence_number):
        return _TankmanDescr.levelUpXpCost(
            from_skill_level, skill_sequence_number)

    def addXP(self, amount):
        self.freeXP += int(amount)
        while self.roleLevel < MAX_SKILL_LEVEL:
            cost = self.levelUpXpCost(self.roleLevel, 0)
            if cost > self.freeXP:
                return
            self.freeXP -= cost
            self.roleLevel += 1
        paid_skills = self.lastSkillNumber - self.freeSkillsNumber
        while paid_skills and self.lastSkillLevel < MAX_SKILL_LEVEL:
            cost = self.levelUpXpCost(self.lastSkillLevel, paid_skills)
            if cost > self.freeXP:
                return
            self.freeXP -= cost
            self.lastSkillLevel += 1

    def makeCompactDescr(self):
        return b'%s|%s|%d|%d|%d|%d' % (
            self._passport, ','.join(self.skills).encode('ascii'),
            self.freeXP, self.lastSkillLevel, self.freeSkillsNumber,
            self.roleLevel)


def new_skill_count(descriptor, active_skills):
    """The exact #1513 Tankman.newSkillCount loop, as a pure simulation."""
    if getattr(descriptor, 'roleLevel', MAX_SKILL_LEVEL) != MAX_SKILL_LEVEL:
        return 0
    available = list(active_skills)
    count = 0
    last_skill_level = getattr(
        descriptor, 'lastSkillLevel', MAX_SKILL_LEVEL)
    free_xp = descriptor.freeXP
    skills = list(descriptor.skills)
    while last_skill_level == MAX_SKILL_LEVEL or not skills:
        if not available:
            break
        name = available.pop()
        if name in skills:
            continue
        skills.append(name)
        count += 1
        last_skill_level = 0
        sequence = len(skills) - descriptor.freeSkillsNumber
        while last_skill_level < MAX_SKILL_LEVEL:
            cost = descriptor.levelUpXpCost(last_skill_level, sequence)
            if cost > free_xp:
                break
            free_xp -= cost
            last_skill_level += 1
    return count


class _Callbacks(object):
    def __init__(self):
        self.pending = []

    def callback(self, delay, function):
        self.pending.append((delay, function))
        return len(self.pending)

    def cancelCallback(self, callback_id):
        return None

    def run_next(self):
        unused_delay, function = self.pending.pop(0)
        function()


class _Compatibility(object):
    def __init__(self, events):
        self.events = events
        self.connect_calls = []

    def connect(self, show_lobby=False, account_context=None):
        self.events.append('connect')
        self.connect_calls.append((show_lobby, account_context))

    def is_ready(self):
        return False

    def fini(self):
        return None


class _AppLoader(object):
    def __init__(self, undefined):
        self.space_id = undefined
        self.lobby = types.SimpleNamespace(initialized=True)

    def getSpaceID(self):
        return self.space_id

    def getDefLobbyApp(self):
        return self.lobby


def _package(name):
    module = types.ModuleType(name)
    module.__path__ = []
    return module


class BootstrapLifecycleTests(unittest.TestCase):
    def _load(self):
        events = []
        callbacks = _Callbacks()
        spaces = types.SimpleNamespace(
            UNDEFINED=0, INTRO_VIDEO=1, LOGIN=2, LOBBY=3)
        app_loader = _AppLoader(spaces.UNDEFINED)
        compatibility = _Compatibility(events)

        bigworld = types.ModuleType('BigWorld')
        bigworld.callback = callbacks.callback
        bigworld.cancelCallback = callbacks.cancelCallback
        compat_module = types.ModuleType(
            'gui.mods.offline_lan_0922.compat')
        compat_module.g_compatibility = compatibility
        config_module = types.ModuleType(
            'gui.mods.offline_lan_0922.config')
        config_module.PLAYER_MODE = 'player'
        config_module.SIMULATION_WORKER_MODE = 'simulation_worker'
        config_module.CLIENT_MODE_ENV = 'OFFLINE_LAN_0922_CLIENT_MODE'
        config_module.load = lambda: {
            'enabled': True, 'startupTimeoutSeconds': 30.0,
            'vehicle': 'ussr:R11_MS-1'}
        config_module.client_mode = lambda unused_config: (
            config_module.PLAYER_MODE)
        state_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.state')
        state_module.AccountState = types.SimpleNamespace
        postbattle_store = types.SimpleNamespace()
        postbattle_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.postbattle_store')
        postbattle_module.PostBattleStore = lambda: postbattle_store
        instance_guard_module = types.ModuleType(
            'gui.mods.offline_lan_0922.instance_guard')
        instance_guard_module.release_if_requested = lambda: False
        session = types.SimpleNamespace(
            install=lambda: events.append('install_battle_router') or True,
            stop=lambda **unused_kwargs: None)
        lan_session_module = types.ModuleType(
            'gui.mods.offline_lan_0922.lan_session')
        lan_session_module.LANSession = lambda *args, **kwargs: session
        announcement_ui = types.SimpleNamespace(
            install=lambda: events.append('install_announcement_router'),
            uninstall=lambda: events.append('uninstall_announcement_router'))
        lobby_ui_module = types.ModuleType(
            'gui.mods.offline_lan_0922.lobby_ui')
        lobby_ui_module.ServerAnnouncementUI = lambda: announcement_ui
        app_loader_module = _package('gui.app_loader')
        app_loader_module.g_appLoader = app_loader
        settings_module = types.ModuleType('gui.app_loader.settings')
        settings_module.GUI_GLOBAL_SPACE_ID = spaces

        def _part(compact_descr, level, guns=None):
            part = types.SimpleNamespace(
                compactDescr=compact_descr, level=level)
            if guns is not None:
                part.guns = guns
            return part

        def make_descriptor(nation_id, vehicle_type_id, base, tags=()):
            # Two entries per slot, stock first, exactly as #1513 orders them.
            guns = [_part(base + 4, 3), _part(base + 14, 6)]
            turrets = [[_part(base + 3, 2, guns[:1]),
                        _part(base + 13, 5, guns)]]
            vehicle_type = types.SimpleNamespace(
                id=(nation_id, vehicle_type_id),
                name='nation-%d:vehicle-%d' % (nation_id, vehicle_type_id),
                crewRoles=(('commander',), ('driver',)),
                tags=frozenset(tags),
                chassis=[_part(base + 2, 2), _part(base + 12, 5)],
                turrets=turrets,
                engines=[_part(base + 5, 2), _part(base + 15, 5)],
                fuelTanks=[_part(base + 6, 1)],
                radios=[_part(base + 7, 2), _part(base + 17, 5)])
            descriptor = types.SimpleNamespace(
                type=vehicle_type,
                chassis=vehicle_type.chassis[0],
                turret=turrets[0][0],
                gun=guns[0],
                engine=vehicle_type.engines[0],
                fuelTank=vehicle_type.fuelTanks[0],
                radio=vehicle_type.radios[0],
                maxHealth=100 + vehicle_type_id,
                makeCompactDescr=lambda: (
                    'vehicle-%d-%d' %
                    (nation_id, vehicle_type_id)).encode('ascii'))

            def find(components, compact_descr):
                for component in components:
                    if component.compactDescr == compact_descr:
                        return component
                raise KeyError(compact_descr)

            def install_component(compact_descr, position_index=0):
                for attribute, mounted in (('chassis', 'chassis'),
                                           ('engines', 'engine'),
                                           ('radios', 'radio'),
                                           ('fuelTanks', 'fuelTank')):
                    components = getattr(vehicle_type, attribute)
                    if any(item.compactDescr == compact_descr
                           for item in components):
                        previous = getattr(descriptor, mounted)
                        setattr(descriptor, mounted,
                                find(components, compact_descr))
                        return (previous.compactDescr,)
                # #1513 ends installComponent in ``assert False`` for a turret.
                raise AssertionError(compact_descr)

            def install_turret(turret_cd, gun_cd, position_index=0):
                previous_turret = descriptor.turret
                previous_gun = descriptor.gun
                turret = find(vehicle_type.turrets[position_index], turret_cd)
                descriptor.gun = find(turret.guns, gun_cd)
                descriptor.turret = turret
                return [previous_turret.compactDescr,
                        previous_gun.compactDescr]

            descriptor.installComponent = install_component
            descriptor.installTurret = install_turret
            return descriptor

        descriptors = {
            (0, 11): make_descriptor(0, 11, 2000),
            (0, 12): make_descriptor(0, 12, 3000),
            (1, 7): make_descriptor(1, 7, 4000),
            (1, 8): make_descriptor(1, 8, 5000),
            (1, 9): make_descriptor(1, 9, 6000),
            (2, 1): make_descriptor(2, 1, 7000, ('event_battles',)),
            (2, 2): make_descriptor(2, 2, 8000, ('premiumIGR',)),
            (2, 3): make_descriptor(2, 3, 9000, ('observer',)),
            (2, 4): make_descriptor(2, 4, 10000, ('SPG', 'secret',
                                                   'unrecoverable')),
        }
        delattr(descriptors[(1, 9)], 'gun')
        descriptors[(1, 9)].type.turrets = [[]]

        attempted_type_ids = []

        def vehicle_descr(**kwargs):
            if 'typeName' in kwargs:
                return descriptors[(0, 11)]
            type_id = tuple(kwargs['typeID'])
            attempted_type_ids.append(type_id)
            return descriptors[type_id]

        class _VehicleList(object):
            def getList(self, nation_id):
                return {
                    0: {11: object(), 12: object()},
                    1: {7: object(), 8: object(), 9: object()},
                    2: {1: object(), 2: object(), 3: object(), 4: object()},
                }.get(nation_id, {})

        customization = types.SimpleNamespace(
            paints={12001: types.SimpleNamespace(compactDescr=12001)},
            camouflages={12002: types.SimpleNamespace(compactDescr=12002)},
            decals={12003: types.SimpleNamespace(compactDescr=12003)},
            modifications={12004: types.SimpleNamespace(compactDescr=12004)},
            styles={12005: types.SimpleNamespace(compactDescr=12005)})
        optional_devices = dict(
            (9000 + index, types.SimpleNamespace(
                compactDescr=9000 + index, tags=frozenset()))
            for index in range(4))
        equipments = dict(
            (11000 + index, types.SimpleNamespace(
                compactDescr=11000 + index, tags=frozenset(),
                equipmentType=0))
            for index in range(3))
        # #1513 tags the artillery and airstrike consumables 'avatar' and
        # gives every battle booster a non-regular equipmentType.
        equipments[11100] = types.SimpleNamespace(
            compactDescr=11100, tags=frozenset(('avatar', 'trigger')),
            equipmentType=0)
        equipments[11200] = types.SimpleNamespace(
            compactDescr=11200, tags=frozenset(('notForSale',)),
            equipmentType=1)
        equipment_ids = {'autoExtinguishers': 11000, 'largeMedkit': 11001,
                         'largeRepairkit': 11002}
        crew_type_ids = []
        crew_skill_masks = []
        vehicles = types.SimpleNamespace(
            VehicleDescr=vehicle_descr,
            getDefaultAmmoForGun=lambda gun: [gun.compactDescr + 10000, 20],
            makeIntCompactDescrByID=lambda unused_name, nation_id, type_id: (
                90000 + nation_id * 1000 + type_id),
            g_list=_VehicleList(),
            attemptedTypeIDs=attempted_type_ids,
            crewTypeIDs=crew_type_ids,
            g_cache=types.SimpleNamespace(
                customization20=lambda: customization,
                optionalDevices=lambda: optional_devices,
                equipmentIDs=lambda: equipment_ids,
                equipments=lambda: equipments))

        def generate_tankmen(nation_id, vehicle_type_id, roles,
                             is_premium, role_level, skills_mask, is_preview):
            crew_type_ids.append((nation_id, vehicle_type_id))
            crew_skill_masks.append(skills_mask)
            if (nation_id, vehicle_type_id) == (1, 8):
                raise ValueError('unloadable crew definition')
            return [
                ('%d:%d:%s|%s|0' % (
                    nation_id, vehicle_type_id, role[0],
                    'commander_sixthSense'
                    if skills_mask and role[0] == 'commander' else '')).encode(
                        'ascii')
                for role in roles]

        tankmen = types.SimpleNamespace(
            MAX_SKILL_LEVEL=100,
            getSkillsMask=lambda skills: (
                1 << 18 if tuple(skills) ==
                ('commander_sixthSense',) else 0),
            generateTankmen=generate_tankmen,
            TankmanDescr=_TankmanDescr,
            generatedSkillMasks=crew_skill_masks)
        items = types.ModuleType('items')
        items.EQUIPMENT_TYPES = types.SimpleNamespace(
            regular=0, battleBoosters=1)
        items.ITEM_TYPE_INDICES = {
            'vehicle': 1, 'vehicleChassis': 2, 'vehicleTurret': 3,
            'vehicleGun': 4, 'vehicleEngine': 5,
            'vehicleFuelTank': 6, 'vehicleRadio': 7, 'tankman': 8,
            'optionalDevice': 9, 'shell': 10, 'equipment': 11,
            'customization': 12,
        }
        items.tankmen = tankmen
        items.vehicles = vehicles
        nations = types.ModuleType('nations')
        nations.NAMES = tuple('nation-%d' % index for index in range(9))
        # The exact #1513 scripts/common/AccountCommands.pyc values.
        account_commands = types.ModuleType('AccountCommands')
        account_commands.VEHICLE_SETTINGS_FLAG = types.SimpleNamespace(
            NONE=0, XP_TO_TMAN=1, AUTO_REPAIR=2, AUTO_LOAD=4, AUTO_EQUIP=8,
            GROUP_0=16, ORIGINAL_CREW=32, NO_BATTLE=64,
            AUTO_EQUIP_BOOSTER=128, AUTO_RENT_CUSTOMIZATION=256)

        modules = {
            'AccountCommands': account_commands,
            'BigWorld': bigworld,
            'gui': _package('gui'),
            'gui.mods': _package('gui.mods'),
            'gui.mods.offline_lan_0922': _package(
                'gui.mods.offline_lan_0922'),
            'gui.mods.offline_lan_0922.account_rpc': _package(
                'gui.mods.offline_lan_0922.account_rpc'),
            'gui.mods.offline_lan_0922.account_rpc.postbattle_store': (
                postbattle_module),
            'gui.mods.offline_lan_0922.account_rpc.state': state_module,
            'gui.mods.offline_lan_0922.compat': compat_module,
            'gui.mods.offline_lan_0922.config': config_module,
            'gui.mods.offline_lan_0922.instance_guard': (
                instance_guard_module),
            'gui.mods.offline_lan_0922.lan_session': lan_session_module,
            'gui.mods.offline_lan_0922.lobby_ui': lobby_ui_module,
            'gui.mods.offline_lan_0922.vehicle_blacklist': VEHICLE_BLACKLIST,
            'gui.mods.offline_lan_0922.vehicle_configuration': (
                VEHICLE_CONFIGURATION),
            'gui.app_loader': app_loader_module,
            'gui.app_loader.settings': settings_module,
            'items': items,
            'nations': nations,
        }
        name = 'test_offline_lan_0922_bootstrap_lifecycle'
        spec = importlib.util.spec_from_file_location(name, BOOTSTRAP)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        return (module, callbacks, compatibility, app_loader, spaces, events,
                modules)

    ACTIVE_SKILLS = ('repair', 'camouflage', 'brotherhood', 'firefighting',
                     'commander_sixthSense', 'commander_eagleEye',
                     'driver_virtuoso', 'driver_smoothDriving',
                     'gunner_smoothTurret', 'loader_intuition')

    def test_session_log_distinguishes_visible_and_worker_builds(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events,
         unused_modules) = self._load()
        bootstrap.port_config.session_identity = lambda: {
            'semanticVersion': '0.6.1',
            'buildIdentity': 'installed-build',
            'launcherSemanticVersion': '0.6.1',
            'launcherBuildIdentity': 'launcher-build',
        }
        writer = mock.Mock()

        with mock.patch.object(bootstrap.sys.stdout, 'write', writer):
            bootstrap._log_session_identity('simulation_worker')

        payload = ''.join(call.args[0] for call in writer.call_args_list)
        self.assertIn('version=0.6.1', payload)
        self.assertIn('build=installed-build', payload)
        self.assertIn('role=hidden-worker', payload)
        self.assertIn('launcher_build=launcher-build', payload)

    def test_a_fresh_garage_vehicle_starts_with_the_refill_switches_on(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        # XP_TO_TMAN | AUTO_REPAIR | AUTO_LOAD | AUTO_EQUIP
        self.assertEqual(15, selected['settings'])
        for record in selected['vehicles']:
            self.assertEqual(15, record['settings'])

    def test_every_crewman_starts_with_eight_skills_left_to_pick(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        for record in selected['vehicles']:
            for compact_descr in record['tankmen'].values():
                descriptor = _TankmanDescr(compact_descr)
                self.assertEqual(
                    8, new_skill_count(descriptor, self.ACTIVE_SKILLS))

    def test_no_crew_skill_is_chosen_for_the_player(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        for record in selected['vehicles']:
            for compact_descr in record['tankmen'].values():
                skills = _TankmanDescr(compact_descr).skills
                self.assertEqual([], skills)

    def test_saved_unselected_crew_with_two_choices_is_migrated_to_eight(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()
        tankmen = modules['items'].tankmen
        descriptor = _TankmanDescr(b'0:11:commander||0')
        descriptor.freeXP = sum(
            descriptor.levelUpXpCost(level, 1)
            for level in range(MAX_SKILL_LEVEL))
        snapshot = {
            'vehicles': [{
                'crew': [100001],
                'tankmen': {100001: descriptor.makeCompactDescr()},
            }],
        }
        before = _TankmanDescr(snapshot['vehicles'][0]['tankmen'][100001])
        self.assertEqual(2, new_skill_count(before, self.ACTIVE_SKILLS))

        migrated = bootstrap._migrate_saved_crew_skill_slots(
            snapshot, tankmen)

        after = _TankmanDescr(snapshot['vehicles'][0]['tankmen'][100001])
        self.assertEqual(1, migrated)
        self.assertEqual([], after.skills)
        self.assertEqual(8, new_skill_count(after, self.ACTIVE_SKILLS))

    def test_saved_crew_with_enough_xp_is_left_byte_for_byte_unchanged(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()
        tankmen = modules['items'].tankmen
        descriptor = _TankmanDescr(
            b'0:11:commander|repair,camouflage|0')
        descriptor.freeXP = bootstrap._new_skill_xp(
            tankmen, descriptor, 2, choices=6) + 1
        compact_descr = descriptor.makeCompactDescr()
        snapshot = {
            'vehicles': [{
                'crew': [100001],
                'tankmen': {100001: compact_descr},
            }],
        }

        migrated = bootstrap._migrate_saved_crew_skill_slots(
            snapshot, tankmen)

        self.assertEqual(0, migrated)
        self.assertEqual(
            compact_descr, snapshot['vehicles'][0]['tankmen'][100001])

    def test_partial_learned_skill_is_kept_and_completed_to_open_slots(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, unused_modules) = (
             self._load())
        tankmen = types.SimpleNamespace(
            MAX_SKILL_LEVEL=MAX_SKILL_LEVEL,
            TankmanDescr=_ProgressTankmanDescr)
        descriptor = _ProgressTankmanDescr(
            b'0:11:commander|repair,camouflage|17|42|0|100')
        original_skills = list(descriptor.skills)
        snapshot = {
            'vehicles': [{
                'crew': [100001],
                'tankmen': {100001: descriptor.makeCompactDescr()},
            }],
        }
        self.assertEqual(0, new_skill_count(descriptor, self.ACTIVE_SKILLS))

        migrated = bootstrap._migrate_saved_crew_skill_slots(
            snapshot, tankmen)

        after = _ProgressTankmanDescr(
            snapshot['vehicles'][0]['tankmen'][100001])
        self.assertEqual(1, migrated)
        self.assertEqual(original_skills, after.skills)
        self.assertEqual(MAX_SKILL_LEVEL, after.lastSkillLevel)
        self.assertEqual(6, new_skill_count(after, self.ACTIVE_SKILLS))
        self.assertGreaterEqual(after.freeXP, 17)
        migrated_descr = snapshot['vehicles'][0]['tankmen'][100001]
        self.assertEqual(
            0, bootstrap._migrate_saved_crew_skill_slots(snapshot, tankmen))
        self.assertEqual(
            migrated_descr, snapshot['vehicles'][0]['tankmen'][100001])

    def test_eight_selected_skills_are_not_expanded_or_rewritten(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()
        tankmen = modules['items'].tankmen
        compact_descr = (
            b'0:11:commander|repair,camouflage,brotherhood,firefighting,'
            b'commander_sixthSense,commander_eagleEye,driver_virtuoso,'
            b'driver_smoothDriving|0')
        snapshot = {
            'vehicles': [{
                'crew': [100001],
                'tankmen': {100001: compact_descr},
            }],
        }

        migrated = bootstrap._migrate_saved_crew_skill_slots(
            snapshot, tankmen)

        self.assertEqual(0, migrated)
        self.assertEqual(
            compact_descr, snapshot['vehicles'][0]['tankmen'][100001])

    def test_free_skill_prefix_is_preserved_and_excluded_from_xp_sequence(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, unused_modules) = (
             self._load())
        tankmen = types.SimpleNamespace(
            MAX_SKILL_LEVEL=MAX_SKILL_LEVEL,
            TankmanDescr=_ProgressTankmanDescr)
        descriptor = _ProgressTankmanDescr(
            b'0:11:commander|commander_sixthSense,repair|0|100|1|100')
        snapshot = {
            'vehicles': [{
                'crew': [100001],
                'tankmen': {100001: descriptor.makeCompactDescr()},
            }],
        }

        self.assertEqual(
            1, bootstrap._migrate_saved_crew_skill_slots(snapshot, tankmen))

        after = _ProgressTankmanDescr(
            snapshot['vehicles'][0]['tankmen'][100001])
        self.assertEqual(
            ['commander_sixthSense', 'repair'], after.skills)
        self.assertEqual(1, after.freeSkillsNumber)
        self.assertEqual(6, new_skill_count(after, self.ACTIVE_SKILLS))

    def test_saved_crew_migration_is_flushed_during_restore(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()
        descriptor = _TankmanDescr(
            b'0:11:commander|repair,camouflage|0')
        descriptor.freeXP = sum(
            descriptor.levelUpXpCost(level, 3)
            for level in range(MAX_SKILL_LEVEL))
        snapshot = {
            'vehicles': [{
                'crew': [100001],
                'tankmen': {100001: descriptor.makeCompactDescr()},
            }],
        }

        class _Store(object):
            def __init__(self):
                self.dirty = False
                self.flushed = None

            def apply(self, candidate, validator=None):
                validator(candidate)
                return True

            def mark_dirty(self):
                self.dirty = True

            def flush(self, candidate):
                self.flushed = candidate
                return True

        store = _Store()
        bootstrap._store = store
        with mock.patch.dict(sys.modules, modules), mock.patch.object(
                bootstrap, '_validate_restored_garage', return_value=True):
            self.assertTrue(bootstrap._restore_garage(snapshot))

        after = _TankmanDescr(snapshot['vehicles'][0]['tankmen'][100001])
        self.assertTrue(store.dirty)
        self.assertIs(snapshot, store.flushed)
        self.assertEqual(['repair', 'camouflage'], after.skills)
        self.assertEqual(6, new_skill_count(after, self.ACTIVE_SKILLS))

    def test_battle_progress_commits_against_the_live_garage_snapshot(self):
        (bootstrap, unused_callbacks, compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()
        initial = {'vehicles': [{'id': 1, 'compDescr': b'stock'},
                                {'id': 2, 'compDescr': b'stock'}]}
        live = {'vehicles': [{'id': 1, 'compDescr': b'stock'},
                             {'id': 2, 'compDescr': b'top-fitting'}]}
        applied = []
        bound = []

        class _GarageStore(object):
            def apply_battle_crew_xp(self, snapshot, receipt_id,
                                     vehicle_type_cd, xp, xp_flag,
                                     tankmen_module=None):
                applied.append(snapshot)
                self.assert_not_used = tankmen_module
                return {'vehicle_id': 1, 'applied': True}

        bootstrap._postbattle_store = types.SimpleNamespace(
            set_progress_applier=lambda callback: bound.append(callback))
        compatibility.garage_state = lambda: types.SimpleNamespace(
            snapshot=lambda: live)
        context = {
            'garage_store': _GarageStore(),
            'selected_vehicle': initial,
        }

        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(bootstrap._bind_battle_progress(context))
            bound[0]({
                'vehicle': 'ussr:R11_MS-1',
                'receipt_id': 'server:1:1',
                'rewards': {'xp': 100},
            })

        self.assertEqual([live], applied)
        self.assertIs(live, context['selected_vehicle'])
        self.assertEqual(
            b'top-fitting', live['vehicles'][1]['compDescr'])

    def test_the_ammunition_layout_mirrors_the_loaded_shells(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle({'vehicle': 'ussr:R11_MS-1'})

        # Vehicle.isAutoLoadFull compares every loaded count with this layout.
        for record in selected['vehicles']:
            key = record['shellsLayoutIdx']
            self.assertEqual({key: record['shells']}, record['shellsLayout'])

    def test_the_garage_never_offers_a_vehicle_the_client_cannot_load(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events,
         modules) = self._load()

        with mock.patch.dict(sys.modules, modules), \
                mock.patch.dict(
                    VEHICLE_BLACKLIST.UNUSABLE_VEHICLES,
                    {'nation-0:vehicle-12': ('vehicles/x/collision_client/'
                                             'Hull.model',)}):
            selected = bootstrap._selected_vehicle(
                {'vehicle': 'ussr:R11_MS-1'})

        self.assertEqual({90011, 91007},
                         selected['vehicleTypeCompactDescrs'])

    def test_selected_vehicle_snapshot_is_relationally_complete(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            selected = bootstrap._selected_vehicle(
                {'vehicle': 'ussr:R11_MS-1'})

        self.assertEqual([100001, 100002], selected['crew'])
        skill_xp = sum(
            _TankmanDescr.levelUpXpCost(level, step)
            for step in range(1, 8)
            for level in range(MAX_SKILL_LEVEL))
        self.assertEqual(
            [b'0:11:commander||%d' % skill_xp,
             b'0:11:driver||%d' % skill_xp],
            [selected['tankmen'][100001], selected['tankmen'][100002]])
        self.assertEqual((0, 111), selected['repair'])
        self.assertEqual((0, 0), selected['lock'])
        # Every tank starts with the extinguisher, the large first aid kit
        # and the large repair kit already mounted.
        self.assertEqual([11000, 11001, 11002], selected['eqs'])
        self.assertEqual([11000, 11001, 11002], selected['eqsLayout'])
        # The top modules are mounted, not the stock ones: base 2000 gives
        # the stock parts 2002-2007 and the top parts 2012-2017.
        self.assertEqual((2013, 2014), selected['shellsLayoutIdx'])
        self.assertEqual([12014, 20], selected['shells'])
        # The loaded rows contain only each mounted top gun, but the
        # account-level catalogue must also retain every alternate gun's
        # ammunition so a saved stock-gun fitting can close prices/unlocks on
        # the next startup.
        self.assertEqual(
            {12004, 12014, 13004, 13014, 14004, 14014},
            set(selected['inventoryItems'][10]))
        # 9 is optionalDevice and 11 is equipment: account-wide catalogues
        # the garage needs before it can offer a mount.
        self.assertEqual(set(range(2, 8)) | {9, 10, 11},
                         set(selected['inventoryItems']))
        required_prices = set()
        for item_type in tuple(range(2, 8)) + (9, 10, 11):
            required_prices.update(selected['inventoryItems'][item_type])
        self.assertTrue(
            required_prices.issubset(selected['shopItemPrices']))
        self.assertTrue(
            required_prices.issubset(selected['unlockItemCompactDescrs']))
        self.assertEqual(4, selected['optionalDeviceCount'])
        # The avatar strike and the battle booster stay out of the catalogue.
        self.assertEqual(3, selected['equipmentCount'])
        for compact_descr in (11100, 11200):
            self.assertNotIn(compact_descr, selected['inventoryItems'][11])
            self.assertNotIn(compact_descr, selected['shopItemPrices'])
        for compact_descr in (9000, 9003, 11000, 11002):
            self.assertEqual(
                200,
                selected['inventoryItems'][
                    9 if compact_descr < 10000 else 11][compact_descr])
        self.assertEqual(9, selected['shopNationCount'])
        self.assertEqual(5, selected['customizationItemCount'])
        self.assertTrue(
            {12001, 12002, 12003, 12004, 12005}.issubset(
                selected['shopItemPrices']))
        for compact_descr in (12001, 12002, 12003, 12004, 12005):
            self.assertEqual(
                {'credits': 0}, selected['shopItemPrices'][compact_descr])
        self.assertEqual(3, len(selected['vehicles']))
        self.assertEqual([1, 2, 3], [
            record['id'] for record in selected['vehicles']])
        self.assertEqual(
            {90011, 90012, 91007},
            selected['vehicleTypeCompactDescrs'])
        self.assertTrue(
            selected['vehicleTypeCompactDescrs'].issubset(
                selected['unlockItemCompactDescrs']))
        all_tankman_ids = []
        for record in selected['vehicles']:
            all_tankman_ids.extend(record['crew'])
            self.assertEqual(set(record['crew']), set(record['tankmen']))
            for item_type in tuple(range(2, 8)) + (10,):
                self.assertTrue(record['inventoryItems'][item_type])
        self.assertEqual(len(all_tankman_ids), len(set(all_tankman_ids)))
        runtime_vehicles = modules['items'].vehicles
        self.assertTrue(
            {(1, 8), (1, 9), (2, 1), (2, 2), (2, 3), (2, 4)}.issubset(
                set(runtime_vehicles.attemptedTypeIDs)))
        self.assertTrue({(1, 8), (1, 9)}.issubset(
                        set(runtime_vehicles.crewTypeIDs)))
        self.assertEqual(
            {0},
            set(modules['items'].tankmen.generatedSkillMasks))
        self.assertTrue(
            {(2, 1), (2, 2), (2, 3), (2, 4)}.isdisjoint(
                set(runtime_vehicles.crewTypeIDs)))

    def test_account_is_created_after_login_state_clear_and_next_tick(self):
        (bootstrap, callbacks, compatibility, app_loader,
         spaces, events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            bootstrap._run_once()
            self.assertEqual([], compatibility.connect_calls)

            app_loader.space_id = spaces.INTRO_VIDEO
            callbacks.run_next()
            self.assertEqual([], compatibility.connect_calls)

            # Exact #1513 LoginState.init() clears client-only entities before
            # the state becomes observable as LOGIN.
            events.append('clear_entities_and_spaces')
            app_loader.space_id = spaces.LOGIN
            callbacks.run_next()
            self.assertEqual([], compatibility.connect_calls)

            callbacks.run_next()

        self.assertEqual(
            ['clear_entities_and_spaces', 'install_announcement_router',
             'install_battle_router', 'connect'],
            events)
        self.assertEqual(1, len(compatibility.connect_calls))
        self.assertTrue(compatibility.connect_calls[0][0])

        with mock.patch.dict(sys.modules, modules):
            self.assertIsNone(bootstrap._cleanup_runtime())
        self.assertEqual('uninstall_announcement_router', events[-1])

    def test_lobby_view_load_immediately_notifies_the_lan_session(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events,
         unused_modules) = self._load()
        notified = []
        bootstrap._session = types.SimpleNamespace(
            on_lobby_view_loaded=lambda: notified.append(True))

        bootstrap._on_lobby_view_loaded(None)

        self.assertTrue(bootstrap._lobby_view_loaded)
        self.assertEqual([True], notified)

    def test_cleanup_retains_session_until_stop_can_be_retried(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events,
         modules) = self._load()
        attempts = []

        def stop(**unused_kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError('native session stop failed')

        session = types.SimpleNamespace(stop=stop)
        bootstrap._session = session

        with mock.patch.dict(sys.modules, modules):
            self.assertRegex(
                str(bootstrap._cleanup_runtime()),
                'native session stop failed')
            self.assertIs(session, bootstrap._session)
            self.assertIsNone(bootstrap._cleanup_runtime())

        self.assertIsNone(bootstrap._session)
        self.assertEqual([True, True], attempts)

    def test_cleanup_retains_callback_id_until_cancel_succeeds(self):
        (bootstrap, unused_callbacks, unused_compatibility,
         unused_app_loader, unused_spaces, unused_events,
         modules) = self._load()
        cancel = mock.Mock(side_effect=(
            RuntimeError('callback cancel failed'), None))
        bootstrap.BigWorld.cancelCallback = cancel
        bootstrap._callback_id = 17

        with mock.patch.dict(sys.modules, modules):
            self.assertRegex(
                str(bootstrap._cleanup_runtime()),
                'callback cancel failed')
            self.assertEqual(17, bootstrap._callback_id)
            self.assertIsNone(bootstrap._cleanup_runtime())

        self.assertIsNone(bootstrap._callback_id)
        self.assertEqual([mock.call(17), mock.call(17)], cancel.call_args_list)

    def test_login_space_must_remain_stable_for_the_deferred_tick(self):
        (bootstrap, callbacks, compatibility, app_loader,
         spaces, unused_events, modules) = self._load()

        with mock.patch.dict(sys.modules, modules):
            app_loader.space_id = spaces.LOGIN
            bootstrap._run_once()
            self.assertEqual([], compatibility.connect_calls)

            app_loader.space_id = spaces.UNDEFINED
            callbacks.run_next()

        self.assertEqual([], compatibility.connect_calls)
        self.assertEqual(1, len(callbacks.pending))


class TopModuleRuleTests(unittest.TestCase):
    """Top fittings reproduce the exact #1513 comparison sequence."""

    def setUp(self):
        self.bootstrap = BootstrapLifecycleTests._load(self)[0]

    @staticmethod
    def _part(name, level, compact_descr=0, price=0, **values):
        fields = dict(name=name, userString=name, level=level,
                      compactDescr=compact_descr, price=price)
        fields.update(values)
        return types.SimpleNamespace(**fields)

    def test_a_low_tier_howitzer_listed_last_is_not_the_top_gun(self):
        # usa:A63_M46_Patton lists _105mm_SPH_M4_L23 (level 5) after the
        # level 9 gun.
        guns = [self._part('_90mm_Gun_M3', 7),
                self._part('_90mm_Gun_M36', 7),
                self._part('_90mm_Gun_T15E2M2', 8),
                self._part('_105mm_Gun_T5E1M2', 9),
                self._part('_105mm_SPH_M4_L23', 5)]

        self.assertEqual('_105mm_Gun_T5E1M2',
                         self.bootstrap._top_component(guns).name)

    def test_a_tie_takes_the_later_entry(self):
        parts = [self._part('stock', 5), self._part('later', 5)]

        self.assertEqual('later', self.bootstrap._top_component(parts).name)

    def test_unique_highest_level_wins_before_research_cost(self):
        lower = self._part('lower', 4, 101)
        highest = self._part('highest', 5, 102)
        vehicle_type = types.SimpleNamespace(
            unlocksDescrs=((99999, lower.compactDescr),
                           (1, highest.compactDescr)))

        selected = self.bootstrap._top_component(
            (lower, highest), vehicle_type, 'engine')

        self.assertIs(highest, selected)

    def test_a_level_tie_compares_research_cost_across_every_candidate(self):
        lower = self._part('lower-but-expensive', 4, 101)
        tied_a = self._part('tied-a', 5, 102)
        tied_b = self._part('tied-b', 5, 103)
        vehicle_type = types.SimpleNamespace(
            unlocksDescrs=((99999, lower.compactDescr),
                           (100, tied_a.compactDescr),
                           (200, tied_b.compactDescr)))

        selected = self.bootstrap._top_component(
            (lower, tied_a, tied_b), vehicle_type, 'engine')

        self.assertIs(lower, selected)

    def test_research_cost_rule_covers_all_known_affected_vehicles(self):
        # Each pair has the same maximum level.  The old list-order rule chose
        # ``wrong``; #1513 TopModulesChecker chooses ``expected``.  The costs
        # are synthetic and exercise the observed ordering, not package data.
        cases = (
            ('ussr:R40_T-54', 'gun', '_100mm_D-10T2S', 59300,
             '_100mm_D54_obr_45', 59300),
            ('germany:G08_Pz38t', 'gun', '_47mm_PaK_38t_L43', 130,
             '_20mm_Flak_38_L112', 160),
            ('germany:G10_PzIII_AusfJ', 'gun', '_75mm_KwK_37_L24', 800,
             '_50mm_KwK_39_L60', 1350),
            ('germany:G86_VK2001DB', 'gun', '_75mm_KwK_37_L24', 800,
             '_50mm_KwK_39_L60', 1350),
            ('usa:A02_M2_lt', 'gun', '_37mm_M-5', 90,
             '_20mm_Hispano_Suiza_Birgikt_gun', 140),
            ('usa:A57_M8A1', 'gun', '_75mm_AT_Gun_M7_L50', 2250,
             '_57mm_Gun_M1_L50', 2350),
            ('usa:A46_T3', 'gun',
             'Ordnance_QF_2pdr_AT_Gun_Mk.X', 500,
             '_75mm_Howitzer_M1A1', 660),
            ('china:Ch05_T34_2', 'gun', '_100mm_59-100T', 18000,
             '_122-mm_37-122JT', 19000),
            ('france:F12_Hotchkiss_H35', 'gun', '_37mm_SA38', 75,
             '_25mm_Canon_Raccourci_Mle.1934', 150),
            ('france:F02_D1', 'gun', '_47mm_SA34', 95,
             '_25mm_Canon_Raccourci_Mle.1934', 150),
            ('france:F50_FCM36_20t', 'gun', '_37mm_SA38', 75,
             '_25mm_Canon_Raccourci_Mle.1934', 150),
            ('france:F49_RenaultR35', 'gun', '_37mm_SA38', 75,
             '_25mm_Canon_Raccourci_Mle.1934', 150),
            ('uk:GB07_Matilda', 'gun', 'QF_2pdr_Littlejohn', 1800,
             '_76mm_Howitzer_MkI', 2100),
            ('uk:GB15_Stuart_I', 'gun', '_2pdr_Gun_Mk_IX', 300,
             '_37mm_M-6_L53', 350),
            ('uk:GB11_Caernarvon', 'engine',
             'Rolls_Royse_Meteor_M120', 27000,
             'Rolls_Royse_Meteor_MkIVC', 31000),
            ('uk:GB60_Covenanter', 'gun', '_40mm_Bofors_MkI', 1200,
             '_76mm_Howitzer_MkI', 2100),
            ('japan:J09_Chi_He', 'gun', '_57mm_Gun_Shin', 2000,
             '_75mm_Gun_Type_99', 2200),
            ('sweden:S04_Lago_I', 'gun', '_75mm_kan_m_41_strv', 2050,
             '_57mm_pvkan_m_43', 3750),
        )
        for index, (vehicle_name, kind, wrong_name, wrong_cost,
                    expected_name, expected_cost) in enumerate(cases):
            expected = self._part(
                expected_name, 5, 1000 + index * 2, price=100)
            wrong = self._part(
                wrong_name, 5, 1001 + index * 2, price=100)
            vehicle_type = types.SimpleNamespace(
                name=vehicle_name,
                unlocksDescrs=((expected_cost, expected.compactDescr),
                               (wrong_cost, wrong.compactDescr)))

            with self.subTest(vehicle=vehicle_name):
                selected = self.bootstrap._top_component(
                    (expected, wrong), vehicle_type, kind)
                self.assertEqual(expected_name, selected.name)

    def test_no_research_cost_uses_the_module_valuable_parameter(self):
        stronger = self._part('stronger', 5, 101, power=700)
        later = self._part('later', 5, 102, power=650)
        vehicle_type = types.SimpleNamespace(unlocksDescrs=())

        selected = self.bootstrap._top_component(
            (stronger, later), vehicle_type, 'engine')

        self.assertIs(stronger, selected)

    def test_gun_valuable_parameter_uses_exact_clip_and_burst_dpm(self):
        shell = types.SimpleNamespace(damage=(100, 75))
        shot = types.SimpleNamespace(shell=shell)
        burst = self._part(
            'burst', 5, 101, id=(0, 1), shots=(shot,),
            reloadTime=10.0, clip=(6, 2.0), burst=(3, 0.5))
        later = self._part(
            'later', 5, 102, id=(0, 2), shots=(shot,),
            reloadTime=10.0, clip=(1, 0.0), burst=(1, 0.0))
        vehicle_type = types.SimpleNamespace(unlocksDescrs=(), turrets=())
        vehicle = types.SimpleNamespace(
            type=vehicle_type,
            turret=types.SimpleNamespace(guns=(burst, later)))

        selected = self.bootstrap._top_component(
            (burst, later), vehicle_type, 'gun', vehicle)

        self.assertIs(burst, selected)

    def test_rejected_research_candidate_is_excluded_and_reselected(self):
        fallback = self._part('fallback', 5, 101, power=600)
        rejected = self._part('rejected', 5, 102, power=700)
        vehicle_type = types.SimpleNamespace(
            unlocksDescrs=((200, fallback.compactDescr),
                           (300, rejected.compactDescr)))
        checked = []

        def may_install(component):
            checked.append(component.name)
            return (component is not rejected, 'incompatible')

        selected = self.bootstrap._top_component(
            (fallback, rejected), vehicle_type, 'engine', None,
            may_install)

        self.assertIs(fallback, selected)
        self.assertEqual(['rejected', 'fallback'], checked)

    def test_install_top_modules_reselects_a_compatible_component(self):
        fallback = self._part('fallback', 4, 101, maxLoad=30)
        rejected = self._part('rejected', 5, 102, maxLoad=40)
        vehicle_type = types.SimpleNamespace(
            chassis=(fallback, rejected), turrets=(), engines=(), radios=(),
            fuelTanks=(), unlocksDescrs=())
        descriptor = types.SimpleNamespace(type=vehicle_type,
                                           chassis=fallback)
        checked = []

        def may_install(compact_descr, position=0):
            checked.append(compact_descr)
            return (compact_descr != rejected.compactDescr, 'too heavy')

        def install(compact_descr, position=0):
            previous = descriptor.chassis
            descriptor.chassis = next(
                part for part in vehicle_type.chassis
                if part.compactDescr == compact_descr)
            return (previous.compactDescr,)

        descriptor.mayInstallComponent = may_install
        descriptor.installComponent = install

        self.bootstrap._install_top_modules(descriptor)

        self.assertIs(fallback, descriptor.chassis)
        self.assertEqual([rejected.compactDescr, fallback.compactDescr],
                         checked)

    def test_install_component_fails_closed_when_mount_did_not_change(self):
        selected = self._part('selected', 5, 102, maxLoad=40)
        mounted = self._part('mounted', 1, 101, maxLoad=20)
        vehicle_type = types.SimpleNamespace(
            chassis=(selected,), turrets=(), engines=(), radios=(),
            fuelTanks=(), unlocksDescrs=())
        descriptor = types.SimpleNamespace(
            type=vehicle_type, chassis=mounted,
            mayInstallComponent=lambda compact_descr, position=0: (True, None),
            installComponent=lambda compact_descr, position=0: (
                mounted.compactDescr,))

        with self.assertRaisesRegex(ValueError, 'top chassis'):
            self.bootstrap._install_top_modules(descriptor)

    def test_install_component_fails_closed_when_every_candidate_is_rejected(self):
        selected = self._part('selected', 5, 102, maxLoad=40)
        mounted = self._part('mounted', 1, 101, maxLoad=20)
        vehicle_type = types.SimpleNamespace(
            chassis=(selected,), turrets=(), engines=(), radios=(),
            fuelTanks=(), unlocksDescrs=())
        descriptor = types.SimpleNamespace(
            type=vehicle_type, chassis=mounted,
            mayInstallComponent=lambda compact_descr, position=0: (
                False, 'too heavy'),
            installComponent=mock.Mock())

        with self.assertRaisesRegex(ValueError, 'no installable top chassis'):
            self.bootstrap._install_top_modules(descriptor)

        descriptor.installComponent.assert_not_called()

    def test_install_turret_fails_closed_when_mount_did_not_change(self):
        gun = self._part('gun', 5, 202, shots=())
        turret = self._part('turret', 5, 201, guns=(gun,),
                            primaryArmor=(100, 80, 80))
        mounted_gun = self._part('mounted-gun', 1, 212, shots=())
        mounted_turret = self._part(
            'mounted-turret', 1, 211, guns=(mounted_gun,),
            primaryArmor=(20, 20, 20))
        vehicle_type = types.SimpleNamespace(
            chassis=(), turrets=((turret,),), engines=(), radios=(),
            fuelTanks=(), unlocksDescrs=())
        descriptor = types.SimpleNamespace(
            type=vehicle_type, turret=mounted_turret, gun=mounted_gun,
            mayInstallTurret=lambda turret_cd, gun_cd, position=0: (
                True, None),
            installTurret=lambda turret_cd, gun_cd, position=0: [
                mounted_turret.compactDescr, mounted_gun.compactDescr])

        with self.assertRaisesRegex(ValueError, 'top turret'):
            self.bootstrap._install_top_modules(descriptor)

    def test_install_turret_fails_closed_when_every_pair_is_rejected(self):
        gun = self._part('gun', 5, 202, shots=())
        turret = self._part('turret', 5, 201, guns=(gun,),
                            primaryArmor=(100, 80, 80))
        mounted_gun = self._part('mounted-gun', 1, 212, shots=())
        mounted_turret = self._part(
            'mounted-turret', 1, 211, guns=(mounted_gun,),
            primaryArmor=(20, 20, 20))
        vehicle_type = types.SimpleNamespace(
            chassis=(), turrets=((turret,),), engines=(), radios=(),
            fuelTanks=(), unlocksDescrs=())
        descriptor = types.SimpleNamespace(
            type=vehicle_type, turret=mounted_turret, gun=mounted_gun,
            mayInstallTurret=lambda turret_cd, gun_cd, position=0: (
                False, 'need gun'),
            installTurret=mock.Mock())

        with self.assertRaisesRegex(
                ValueError, 'no installable top turret/gun'):
            self.bootstrap._install_top_modules(descriptor)

        descriptor.installTurret.assert_not_called()

    def test_a_single_entry_keeps_the_retail_fitting(self):
        parts = [self._part('only', 1)]

        self.assertEqual('only', self.bootstrap._top_component(parts).name)

    def test_an_empty_list_has_no_top(self):
        self.assertIsNone(self.bootstrap._top_component(()))
        self.assertIsNone(self.bootstrap._top_component(None))


if __name__ == '__main__':
    unittest.main()
