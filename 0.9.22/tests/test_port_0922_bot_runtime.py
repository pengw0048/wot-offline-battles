import copy
import importlib.util
import io
import json
import math
from pathlib import Path
import random
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (
    BattleState, CLIENT_BUILD_0922, ClientHandler,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY, PLAYER_ENVIRONMENT_CAPABILITY,
    PLAYER_FIRE_INTENT_CAPABILITY, Player,
    PROJECTILE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
    RICOCHET_CONTINUATION_CAPABILITY,
    SIMULATION_WORKER_AUTHORITY_ID, SIMULATION_WORKER_CAPABILITY,
    SimulationWorker)
from server_bot_ai import BotPlanner

PACKAGE_ROOT = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922'


def _graph(map_name='01_karelia', waypoint_count=2):
    waypoints = tuple((float(index * 4), 0.0, False)
                      for index in range(waypoint_count))
    reverse = tuple(reversed(waypoints))
    return {
        'format': 'offline-lan-0922-navgraph', 'version': 2,
        'game_version': '0.9.22.0.1-cn-1513', 'map': map_name,
        'cell_size': 4.0, 'origin': (0.0, 0.0), 'bounds': (0, 0, 8, 0),
        'width': 3, 'height': 1, 'heights_mm': (0, 0, 0),
        'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
        'hazards': (0, 0, 0),
        'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
        'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
        'spawn_formations': {
            '1': tuple((float(slot % 5) * 12.0, 0.0,
                        -100.0 + float(slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple((float(slot % 5) * 12.0, 0.0,
                        100.0 - float(slot // 5) * 12.0, 3.14159)
                       for slot in range(15)),
        },
        'routes': {
            '1': ({'id': 'safe-1', 'waypoints': waypoints},),
            '2': ({'id': 'safe-2', 'waypoints': reverse},),
        },
        'bake': {'max_grade': 0.30},
    }


def _spawn_resolver(team, slot):
    point = _graph()['spawn_formations'][str(int(team))][int(slot)]
    return ((point[0], point[1], point[2]), point[3])


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name); module.__path__ = [str(PACKAGE_ROOT)]; sys.modules[name] = module
    for name in ('gui.mods.offline_lan_0922.ai',):
        if name not in sys.modules:
            module = types.ModuleType(name); module.__path__ = [str(PACKAGE_ROOT / 'ai')]; sys.modules[name] = module
    name = 'gui.mods.offline_lan_0922.bot_runtime'; sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, PACKAGE_ROOT / 'bot_runtime.py')
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module

class _Director(object):
    def __init__(self): self.registered = []
    def register_profile(self, *args): self.registered.append(args)

class _Adapter(object):
    def __init__(self, *unused):
        self.director = _Director(); self.calls = []; self.server_orders = []
    def register(self, *args): self.director.registered.append(args)
    def decide(self, state, clear):
        self.calls.append((state, clear(state['yaw'])))
        target_id = (state['contacts'][0]['id']
                     if state.get('contacts') else None)
        return {'target_yaw': 0.0, 'throttle': 1.0, 'shell_index': 2,
                'fire_allowed': True, 'target_id': target_id,
                'fire_range': 500.0}
    def decide_with_order(self, state, strategic, clear):
        self.server_orders.append(dict(strategic))
        command = self.decide(state, clear)
        command.update({
            'target_id': strategic.get('target_id'),
            'fire_allowed': bool(strategic.get('fire_allowed')),
            'shell_index': int(strategic.get('shell_index', 0)),
            'fire_range': float(strategic.get('fire_range', 0.0)),
        })
        return command


class _FixedAdapter(_Adapter):
    def __init__(self, command):
        super().__init__()
        self.command = dict(command)

    def decide(self, state, clear):
        self.calls.append((state, clear(state['yaw'])))
        return dict(self.command)

    def decide_with_order(self, state, strategic, clear):
        self.server_orders.append(dict(strategic))
        return self.decide(state, clear)


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class _HitTester1513(object):
    def __init__(self, minimum, maximum):
        # Exact #1513 bbox exposes min, max and a third derived value.
        self.bbox = (minimum, maximum, None)


def _combat_descriptor(reload_time=0.5, clip=(2, 0.2),
                       turret_yaw_limits=(-math.pi, math.pi),
                       turret_speed=10.0, gun_speed=10.0,
                       dispersion=0.03, max_ammo=None):
    gun = types.SimpleNamespace(
        shots=({'shell': {'effectsIndex': 0}, 'speed': 1000.0,
                'gravity': 10.0, 'maxDistance': 5000.0},),
        reloadTime=reload_time,
        clip=clip, turretYawLimits=turret_yaw_limits,
        pitchLimits={'absolute': (-0.35, 0.15)}, rotationSpeed=gun_speed,
        shotDispersionAngle=dispersion,
        maxHealth=54, maxRegenHealth=27)
    if max_ammo is not None:
        gun.maxAmmo = int(max_ammo)
    chassis = _Strict1513Component(
        hitTester=_HitTester1513(
            (-1.5, -0.8, -3.5), (1.5, 0.8, 3.5)),
        hullPosition=(0.0, 0.6, 0.0), rotationSpeed=0.75,
        topRightCarryingPoint=(1.5, 3.5),
        shotDispersionFactors=(0.14, 0.14),
        maxHealth=170, maxRegenHealth=130)
    hull = _Strict1513Component(
        hitTester=_HitTester1513(
            (-1.7, -0.2, -3.5), (1.7, 1.4, 3.5)),
        turretPositions=((0.0, 1.0, 0.0),))
    return types.SimpleNamespace(
        gun=gun, turret={'rotationSpeed': turret_speed,
                         'circularVisionRadius': 445.0},
        physics={'speedLimits': (14.0, 7.0)}, chassis=chassis,
        hull=hull, maxHealth=1000)


def _critical_descriptor():
    descriptor = _combat_descriptor()
    descriptor.chassis.maxHealth = 170
    descriptor.chassis.maxRegenHealth = 130
    descriptor.fuelTank = {'maxHealth': 100, 'maxRegenHealth': 40}
    descriptor.miscAttrs = {}
    return descriptor


def _critical_payload(*records, **values):
    return {
        'devices': [dict(record) for record in records],
        'destroyed': list(values.get('destroyed', ())),
        'crew_ko': list(values.get('crew_ko', ())),
        'fire': bool(values.get('fire', False)),
        'ammo_rack_death': False, 'events': [],
    }


def _bot_equipment_contracts(module, reuse_count=1):
    descriptors = (
        types.SimpleNamespace(
            id=(11, 21), compactDescr=421,
            name='autoExtinguishers', tags=(),
            reuseCount=reuse_count, cooldownSeconds=90.0,
            autoactivate=True, fireStartingChanceFactor=0.9),
        types.SimpleNamespace(
            id=(11, 22), compactDescr=422,
            name='largeMedkit', tags=('medkit',),
            reuseCount=reuse_count, cooldownSeconds=90.0,
            repairAll=True, bonusValue=0.30),
        types.SimpleNamespace(
            id=(11, 23), compactDescr=423,
            name='largeRepairkit', tags=('repairkit',),
            reuseCount=reuse_count, cooldownSeconds=90.0,
            repairAll=True, bonusValue=0.10),
    )
    return tuple(module.equipment_mechanics.project_equipment(value)
                 for value in descriptors)


def _effective_params_snapshot(mass=25000.0, base_moving=0.171,
                               base_still=0.228, shot_factor=0.10,
                               ramming_bonus=0.0,
                               spall_coefficient=1.0):
    return {
        'version': 1,
        'loadout': {
            'crew_level': 100.0, 'commander_level': 100.0,
            'effective_crew_level': 100.0, 'crew_multiplier': 1.0,
            'crew_factor': 1.0, 'gun_rotation_factor': 1.0,
            'reload_factor': 1.0, 'aim_time_factor': 1.0,
            'dispersion_factor': 1.0, 'repair_factor': 1.0,
            'vehicle_rotation_factor': 1.0, 'radio_factor': 1.0,
            'bloom_move_factor': 1.0, 'bloom_rotation_factor': 1.0,
            'bloom_turret_factor': 1.0,
            'terrain_resistance_factors': [1.0, 1.0, 1.0],
            'has_big_kit': False, 'from_client_factors': True,
            'has_rammer': False, 'has_aim_drives': False,
            'has_ventilation': False, 'has_stabiliser': False,
            'has_rations': False, 'has_brotherhood': False,
            'has_snap_shot': False, 'has_smooth_ride': False,
            'has_sixth_sense': False,
        },
        'physics': {
            'mass': float(mass), 'powerW': 500000.0,
            'speedFwd': 14.0, 'speedBwd': 7.0, 'rotSpd': 0.75,
            'terrainResist': [1.0, 1.0, 1.0],
            'specificFriction': 1.0, 'brakeDecel': 4.0,
            'trackCenter': 2.0, 'minPlaneNormalY': 0.2,
            'nativePowerRatio': 1.0,
        },
        'spotting': {
            'commander_level': 100.0, 'recon_level': 0.0,
            'situational_level': 0.0, 'camouflage_level': 0.0,
            'binocular_factor': 1.0, 'binocular_delay': 3.0,
            'camouflage_net_bonus': 0.0, 'camouflage_net_delay': 3.0,
            'has_binoculars': False, 'has_camouflage_net': False,
            'vision_factor': 1.0, 'camouflage_factor': 0.57,
            'invisibility_moving': [0.0, 1.0],
            'invisibility_still': [0.0, 1.0],
            'from_client_factors': True,
        },
        'ramming': {
            'spall_coefficient': float(spall_coefficient),
            'ramming_bonus': float(ramming_bonus),
        },
        'ammo': [[1, 20]],
        'camouflage': {
            'camouflage_id': None,
            'base_moving': float(base_moving),
            'base_still': float(base_still),
            'shot_factor': float(shot_factor),
        },
        'skills': {'deadeye': False, 'intuition_chances': 0},
        'crew': {
            'members': [{
                'instance': 'commander', 'roles': ['commander'],
                'skills': [],
            }],
            'dynamic_spotting': {
                'crew': ['commander'],
                'states': dict(
                    ('%d:%d' % (mask, fire), {
                        'vision': 1.0, 'signal': 1.0,
                        'camouflage': 1.0,
                        'base_moving': float(base_moving),
                        'base_still': float(base_still),
                        'invisibility_moving': [0.0, 1.0],
                        'invisibility_still': [0.0, 1.0],
                    })
                    for mask in (0, 1) for fire in (0, 1)),
            },
        },
        'gun': {
            'clip_size': 1,
            'shots': [{
                'compact_descr': 1,
                'source_shot': {
                    'speed': 800.0, 'gravity': 9.81,
                    'maxDistance': 500.0,
                    'piercingPower': [100.0, 80.0],
                    'deadeye': False,
                    'shell': {
                        'kind': 'ARMOR_PIERCING', 'caliber': 37.0,
                        'damage': [40.0, 20.0],
                        'explosionRadius': 0.0,
                    },
                },
            }],
        },
    }


def _admit_player(value, **snapshot_overrides):
    result = dict(value)
    result['effective_params'] = _effective_params_snapshot(
        **snapshot_overrides)
    return result


def _admit_players(*values):
    return [_admit_player(value) for value in values]


def _snapshot_bot(bot_id=11, health=1000, alive=True, critical=None,
                  revision=0, base_revision=0, ack_seq=0,
                  fire_elapsed=0.0, fire_timer=0.0, **values):
    result = {
        'id': bot_id, 'health': health, 'alive': alive,
        'critical': dict(critical or {}),
        'combat_revision': revision,
        'combat_base_revision': base_revision,
        'combat_ack_seq': ack_seq,
        'combat_fire_elapsed': fire_elapsed,
        'combat_fire_timer': fire_timer,
        'stun_end_server_time_ms': 0,
    }
    result.update(values)
    return result


class _CaptureSocket(object):
    def __init__(self):
        self.payloads = []

    def sendall(self, payload):
        self.payloads.append(payload)


class ServerBotStateRevisionTests(unittest.TestCase):
    @staticmethod
    def _server(clock=None, before_manifest=None, equipment_states=None):
        server = BattleState(map_name='04_himmelsdorf', clock=clock)
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 450
        authority_socket = _CaptureSocket()
        host_socket = _CaptureSocket()
        guest_socket = _CaptureSocket()
        server.players[1] = Player(
            1, host_socket, ('127.0.0.1', 1), team=1, slot=0)
        server.players[2] = Player(
            2, guest_socket, ('127.0.0.1', 2), team=2, slot=0)
        server.simulation_worker = SimulationWorker(
            authority_socket, ('127.0.0.1', 3), capabilities=(
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                SIMULATION_WORKER_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY,
                RICOCHET_CONTINUATION_CAPABILITY,
            ))
        server.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        server.bot_roster = [{
            'id': 11, 'team': 1, 'slot': 1, 'name': 'Revision-11'}]
        manifest_bot = {
            'id': 11, 'team': 1, 'slot': 1, 'name': 'Revision-11',
            'vehicle': 'ussr:R11_MS-1',
            'health': 1000, 'max_health': 1000,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'profile': {'shells': [{
                'index': 0, 'kind': 'ARMOR_PIERCING',
                'penetration': 50.0, 'damage': 50.0, 'speed': 500.0,
            }]},
            'shell_index': 0, 'next_shell_index': 0,
            'ammo_remaining': [20], 'ammo_reload_pending': False,
            'reload_time': 0.5, 'reload_duration': 0.5,
        }
        if equipment_states is not None:
            manifest_bot['equipment_states'] = list(equipment_states)
        if before_manifest is not None:
            before_manifest()
        assert server.update_bot_manifest(SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': server.round_id, 'bots': [manifest_bot],
            'player_collision_profiles': [
                {
                    'id': player.player_id,
                    'vehicle': player.vehicle,
                    'mass': 5730.0,
                    'shape': [1.5, 3.5, -0.8, 1.6],
                    'ram_profile': {
                        'spall_coefficient': 1.0,
                        'ramming_bonus': 0.0,
                    },
                }
                for player in (server.players[1], server.players[2])
            ],
        })
        return server, manifest_bot, authority_socket

    @staticmethod
    def _publication(server, x):
        bot = dict(server.bot_states[11])
        bot['x'] = float(x)
        bot['combat_seq'] = bot['combat_ack_seq']
        return {'round_id': server.round_id, 'bots': [bot]}

    @staticmethod
    def _canonical_bot_commit_snapshot(server):
        return {
            'bot_source_time_us': server.bot_source_time_us,
            'bot_source_receipt_time_us':
                server.bot_source_receipt_time_us,
            'bot_source_batch_horizon_us':
                server.bot_source_batch_horizon_us,
            'bot_launch_clock_offset_us':
                server.bot_launch_clock_offset_us,
            'bot_state_time_us': server.bot_state_time_us,
            'motion_time_offset_us': server.motion_time_offset_us,
            'bot_states': copy.deepcopy(server.bot_states),
            'bot_state_revision': server.bot_state_revision,
            'pending_events': copy.deepcopy(server.pending_events),
            'bot_pending_projectile_launches': copy.deepcopy(
                server.bot_pending_projectile_launches),
            'bot_pending_projectile_metadata': copy.deepcopy(
                server.bot_pending_projectile_metadata),
            'human_ram_probe_requests': copy.deepcopy(
                server.human_ram_probe_requests),
            'human_ram_probe_fingerprints': copy.deepcopy(
                server.human_ram_probe_fingerprints),
            'human_ram_retired_probe_pairs': copy.deepcopy(
                server.human_ram_retired_probe_pairs),
            'battle_result': copy.deepcopy(server.battle_result),
        }

    def test_revision_survives_player_departure_and_resets(self):
        server, manifest_bot, authority_socket = self._server()
        self.assertEqual(0, server.bot_state_revision)

        self.assertFalse(server.update_bot_states(
            2, self._publication(server, 1.0)))
        self.assertEqual(0, server.bot_state_revision)
        self.assertFalse(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': server.round_id, 'bots': [],
            }))
        self.assertEqual(0, server.bot_state_revision)
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._publication(server, 1.0)))
        self.assertEqual(1, server.bot_state_revision)

        server.tick_once(1.0 / 30.0)
        messages = [json.loads(payload.decode('utf-8'))
                    for payload in authority_socket.payloads]
        snapshots = [
            message for message in messages
            if message.get('type') == 'snapshot']
        self.assertEqual(1, snapshots[-1]['bot_state_revision'])

        server.remove_player(1)
        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, server.bot_authority_id)
        self.assertEqual(1, server.bot_state_revision)
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._publication(server, 2.0)))
        self.assertEqual(2, server.bot_state_revision)

        server._reset_round()
        self.assertEqual(0, server.bot_state_revision)
        self.assertEqual(0, server.bot_state_time_us)
        self.assertIsNone(server.bot_source_time_us)
        self.assertIsNone(server.bot_source_receipt_time_us)

    def test_bot_consumables_survive_server_publication_and_takeover(self):
        module = _load()
        contracts = _bot_equipment_contracts(module)
        equipments = [module.equipment_mechanics.EquipmentState(contract)
                      for contract in contracts]
        self.assertIsNotNone(equipments[2].activate(
            0.0, _critical_payload(
                {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
                 'state': 'destroyed'},
                destroyed=('leftTrackHealth',))))
        snapshots = [equipment.snapshot(12.0)
                     for equipment in equipments]
        server, unused_manifest, unused_socket = self._server(
            equipment_states=snapshots)
        self.assertAlmostEqual(
            78.0,
            server.bot_states[11]['equipment_states'][2][
                'cooldownTimeLeft'])

        restored = module.equipment_mechanics.restore_equipment_states(
            server.bot_states[11]['equipment_states'],
            contracts=contracts, now=0.0)
        self.assertIsNotNone(restored[1].activate(
            0.0, _critical_payload(crew_ko=('commander',))))
        publication = self._publication(server, 1.0)
        publication['bots'][0]['equipment_states'] = [
            equipment.snapshot(0.0) for equipment in restored]
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, publication),
            server.last_bot_state_reject)

        takeover = server.current_battle_message()
        persisted = takeover['bot_manifest'][0]['equipment_states']
        self.assertEqual(1, persisted[1]['usesLeft'])
        self.assertAlmostEqual(90.0, persisted[1]['cooldownTimeLeft'])
        self.assertAlmostEqual(78.0, persisted[2]['cooldownTimeLeft'])

        invalid = self._publication(server, 2.0)
        invalid['bots'][0]['equipment_states'] = json.loads(json.dumps(
            invalid['bots'][0]['equipment_states']))
        invalid['bots'][0]['equipment_states'][1]['usesLeft'] = 2
        self.assertFalse(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, invalid))
        self.assertEqual('combat_contract',
                         server.last_bot_state_reject_code)

    def test_bot_large_medkit_clears_stun_once_and_survives_takeover(self):
        module = _load()
        contracts = _bot_equipment_contracts(module)
        snapshots = [module.equipment_mechanics.EquipmentState(
            contract).snapshot(0.0) for contract in contracts]
        server, unused_manifest, unused_socket = self._server(
            equipment_states=snapshots)
        stun_end = server._server_time_ms() + 5000
        self.assertTrue(server._set_canonical_stun(
            ('player', 2), ('bot', 11), stun_end))

        equipments = module.equipment_mechanics.restore_equipment_states(
            server.bot_states[11]['equipment_states'],
            contracts=contracts, now=0.0)
        effect = equipments[1].activate(
            0.2, critical={}, stunned=True)
        self.assertTrue(effect['clearStun'])
        publication = self._publication(server, 1.0)
        publication['bots'][0]['equipment_states'] = [
            equipment.snapshot(0.2) for equipment in equipments]
        publication['bots'][0]['stun_end_server_time_ms'] = 0
        publication['bots'][0]['combat_seq'] = (
            server.bot_states[11]['combat_ack_seq'] + 1)
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, publication),
            server.last_bot_state_reject)
        self.assertEqual(0, server.bot_states[11][
            'stun_end_server_time_ms'])
        self.assertEqual(1, server.bot_states[11][
            'equipment_states'][1]['usesLeft'])
        clear_events = [event for event in server.pending_events
                        if event.get('kind') == 'stun' and
                        not event.get('active', False)]
        self.assertEqual(1, len(clear_events))

        repeated = self._publication(server, 2.0)
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, repeated),
            server.last_bot_state_reject)
        clear_events = [event for event in server.pending_events
                        if event.get('kind') == 'stun' and
                        not event.get('active', False)]
        self.assertEqual(1, len(clear_events))
        takeover = server.current_battle_message()['bot_manifest'][0]
        self.assertEqual(0, takeover['stun_end_server_time_ms'])
        self.assertEqual(1, takeover['equipment_states'][1]['usesLeft'])

    def test_delayed_bot_stun_state_is_fenced_after_server_expiry(self):
        server, unused_manifest, unused_socket = self._server()
        stun_end = server._server_time_ms() + 5000
        self.assertTrue(server._set_canonical_stun(
            ('player', 2), ('bot', 11), stun_end))
        delayed = self._publication(server, 1.0)

        self.assertEqual(1, server._expire_stuns(stun_end))
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, delayed),
            server.last_bot_state_reject)
        self.assertEqual(0, server.bot_states[11][
            'stun_end_server_time_ms'])

    def test_delayed_bot_stun_state_is_fenced_after_new_server_stun(self):
        server, unused_manifest, unused_socket = self._server()
        first_end = server._server_time_ms() + 3000
        second_end = first_end + 2000
        self.assertTrue(server._set_canonical_stun(
            ('player', 2), ('bot', 11), first_end))
        delayed = self._publication(server, 1.0)

        self.assertTrue(server._set_canonical_stun(
            ('player', 2), ('bot', 11), second_end))
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, delayed),
            server.last_bot_state_reject)
        self.assertEqual(second_end, server.bot_states[11][
            'stun_end_server_time_ms'])

    def test_snapshot_carries_real_bot_receipt_time_and_queue_age(self):
        now = [100.0]
        server, _, authority_socket = self._server(
            clock=lambda: now[0],
            before_manifest=lambda: now.__setitem__(0, 100.025))
        self.assertEqual(25000, server.bot_state_time_us)

        now[0] = 100.065
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._publication(server, 1.0)))
        self.assertEqual(65000, server.bot_state_time_us)
        self.assertIsNone(server.bot_source_time_us)

        now[0] = 100.080
        server.tick_once(1.0 / 30.0)
        snapshots = [
            json.loads(payload.decode('utf-8'))
            for payload in authority_socket.payloads
            if json.loads(payload.decode('utf-8')).get('type') == 'snapshot']
        self.assertTrue(snapshots)
        self.assertEqual(65000, snapshots[-1]['bot_state_time_us'])
        self.assertEqual(80000, snapshots[-1]['motion_time_us'])

    def test_source_sample_clock_excludes_variable_worker_completion_time(self):
        now = [100.0]
        server, _, authority_socket = self._server(
            clock=lambda: now[0],
            before_manifest=lambda: now.__setitem__(0, 100.025))

        now[0] = 100.065
        first = self._publication(server, 0.4)
        first['sample_time_us'] = 40000
        first['source_batch_horizon_us'] = 40000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, first))
        first_mapped_time = server.bot_state_time_us
        self.assertEqual(65000, first_mapped_time)

        # The worker spends an extra 95 ms finishing this callback, although
        # the pose itself integrated exactly one more 40 ms step.
        now[0] = 100.200
        delayed = self._publication(server, 0.8)
        delayed['sample_time_us'] = 80000
        delayed['source_batch_horizon_us'] = 80000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, delayed))
        self.assertEqual(first_mapped_time + 40000,
                         server.bot_state_time_us)

        # A fast following callback keeps the same pose-time interval instead
        # of inheriting the preceding callback's completion-time spike.
        now[0] = 100.210
        fast = self._publication(server, 1.2)
        fast['sample_time_us'] = 120000
        fast['source_batch_horizon_us'] = 120000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, fast))
        self.assertEqual(first_mapped_time + 80000,
                         server.bot_state_time_us)

        server.tick_once(1.0 / 30.0)
        snapshots = [
            json.loads(payload.decode('utf-8'))
            for payload in authority_socket.payloads
            if json.loads(payload.decode('utf-8')).get('type') == 'snapshot']
        self.assertNotIn('sample_time_us', snapshots[-1])
        self.assertEqual(server.bot_state_time_us,
                         snapshots[-1]['bot_state_time_us'])

        repeated = self._publication(server, 1.2)
        repeated['sample_time_us'] = 120000
        repeated['source_batch_horizon_us'] = 120000
        self.assertFalse(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, repeated))
        self.assertEqual('sample_time_order',
                         server.last_bot_state_reject_code)

        self.assertFalse(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._publication(server, 1.6)))
        self.assertEqual('sample_time_missing',
                         server.last_bot_state_reject_code)

        server.remove_player(1)
        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, server.bot_authority_id)
        self.assertEqual(120000, server.bot_source_time_us)
        self.assertEqual(210000, server.bot_source_receipt_time_us)

    def test_source_lead_keeps_snapshot_motion_clock_advancing_uniformly(self):
        now = [100.0]
        server, _, authority_socket = self._server(
            clock=lambda: now[0],
            before_manifest=lambda: now.__setitem__(0, 100.025))

        # A slow publication establishes its source epoch at a late receipt.
        now[0] = 100.200
        slow = self._publication(server, 0.4)
        slow['sample_time_us'] = 40000
        slow['source_batch_horizon_us'] = 40000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, slow))
        self.assertEqual(200000, server.bot_state_time_us)
        self.assertEqual(0, server.motion_time_offset_us)

        # The next pose integrates 100 ms but arrives only 10 ms later. Its
        # mapped source time is therefore 90 ms ahead of the raw server clock.
        now[0] = 100.210
        fast = self._publication(server, 1.4)
        fast['sample_time_us'] = 140000
        fast['source_batch_horizon_us'] = 140000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, fast))
        self.assertEqual(300000, server.bot_state_time_us)
        self.assertEqual(90000, server.motion_time_offset_us)

        # The offset must not decay through max(raw, sample). Even without a
        # third bot revision, every server snapshot advances by the raw 10 ms
        # interval instead of remaining pinned at 300 ms until raw catches up.
        for raw_time in (100.220, 100.230, 100.240):
            now[0] = raw_time
            server.tick_once(1.0 / 30.0)

        # A following normal-cadence pose must join that same logical clock;
        # otherwise the clock-only snapshots above could pass while the next
        # real revision still reintroduced a catch-up hold.
        now[0] = 100.250
        steady = self._publication(server, 1.8)
        steady['sample_time_us'] = 180000
        steady['source_batch_horizon_us'] = 180000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, steady))
        self.assertEqual(340000, server.bot_state_time_us)
        server.tick_once(1.0 / 30.0)
        snapshots = [
            json.loads(payload.decode('utf-8'))
            for payload in authority_socket.payloads
            if json.loads(payload.decode('utf-8')).get('type') == 'snapshot']
        motion_times = [message['motion_time_us']
                        for message in snapshots[-4:]]
        self.assertEqual(
            [310000, 320000, 330000, 340000], motion_times)
        self.assertEqual(
            [10000, 10000, 10000],
            [motion_times[index] - motion_times[index - 1]
             for index in range(1, len(motion_times))])
        self.assertTrue(all(
            message['bot_state_time_us'] == 300000
            for message in snapshots[-4:-1]))
        self.assertEqual(340000, snapshots[-1]['bot_state_time_us'])

        # Several publications can disappear before reaching the server. A
        # source delta larger than one 200 ms BotRuntime step remains valid
        # when the same amount of raw receipt time has elapsed.
        now[0] = 100.490
        skipped = self._publication(server, 2.2)
        skipped['sample_time_us'] = 420000
        skipped['source_batch_horizon_us'] = 420000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, skipped))
        self.assertEqual(580000, server.bot_state_time_us)
        self.assertEqual(90000, server.motion_time_offset_us)

        # An instantaneous source leap beyond real elapsed time plus one
        # maximum integration step accepts the complete checkpoint as a new
        # baseline without synthesizing events for its unobserved interval.
        now[0] = 100.500
        revision_before_rebase = server.bot_state_revision
        launches_before_rebase = copy.deepcopy(
            server.bot_pending_projectile_launches)
        oversized = self._publication(server, 9.0)
        oversized['sample_time_us'] = 1000000
        oversized['source_batch_horizon_us'] = 1000000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, oversized))
        self.assertEqual('', server.last_bot_state_reject_code)
        self.assertEqual(590000, server.bot_state_time_us)
        self.assertEqual(1000000, server.bot_source_time_us)
        self.assertEqual(500000, server.bot_source_receipt_time_us)
        self.assertEqual(1000000, server.bot_source_batch_horizon_us)
        self.assertEqual(90000, server.motion_time_offset_us)
        self.assertEqual(9.0, server.bot_states[11]['x'])
        self.assertEqual(
            revision_before_rebase + 1, server.bot_state_revision)
        self.assertEqual(
            launches_before_rebase,
            server.bot_pending_projectile_launches)

        now[0] = 100.530
        recovered = self._publication(server, 2.6)
        recovered['sample_time_us'] = 1040000
        recovered['source_batch_horizon_us'] = 1040000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, recovered))
        self.assertEqual(630000, server.bot_state_time_us)
        self.assertEqual(1040000, server.bot_source_time_us)
        self.assertEqual(530000, server.bot_source_receipt_time_us)
        self.assertEqual(100000, server.motion_time_offset_us)

        # A visible player departure cannot perturb the dedicated worker's
        # source clock or promote another player to authority.
        server.remove_player(1)
        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, server.bot_authority_id)
        self.assertEqual(1040000, server.bot_source_time_us)
        self.assertEqual(530000, server.bot_source_receipt_time_us)
        self.assertEqual(100000, server.motion_time_offset_us)

        server._reset_round()
        self.assertIsNone(server.bot_source_receipt_time_us)
        self.assertEqual(0, server.motion_time_offset_us)

    def test_malformed_future_publications_do_not_rebase_any_clock_or_state(self):
        now = [100.0]
        server, _, unused_socket = self._server(
            clock=lambda: now[0],
            before_manifest=lambda: now.__setitem__(0, 100.025))

        now[0] = 100.200
        baseline = self._publication(server, 0.4)
        baseline['sample_time_us'] = 40000
        baseline['source_batch_horizon_us'] = 40000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, baseline))
        committed = self._canonical_bot_commit_snapshot(server)

        malformed = []

        empty_batch = self._publication(server, 1.0)
        empty_batch['bots'] = []
        malformed.append(('batch_shape', empty_batch))

        bad_row = self._publication(server, 1.0)
        bad_row['bots'][0].pop('x')
        malformed.append(('bot_shape', bad_row))

        bad_ram = self._publication(server, 1.0)
        bad_ram['human_ram_armors'] = [{}]
        malformed.append(('human_ram_armors', bad_ram))

        bad_combat = self._publication(server, 1.0)
        bad_combat['bots'][0]['combat_seq'] = (
            bad_combat['bots'][0]['combat_ack_seq'] + 2)
        malformed.append(('combat_contract', bad_combat))

        bad_ammo = self._publication(server, 1.0)
        bad_ammo['bots'][0]['ammo_remaining'] = list(
            bad_ammo['bots'][0]['ammo_remaining'])
        bad_ammo['bots'][0]['ammo_remaining'][0] += 1
        malformed.append(('ammo_contract', bad_ammo))

        for index, (reject_code, publication) in enumerate(malformed):
            now[0] = 100.210 + index * 0.005
            publication['sample_time_us'] = 1000000 + index * 10000
            publication['source_batch_horizon_us'] = (
                publication['sample_time_us'])
            self.assertFalse(server.update_bot_states(
                SIMULATION_WORKER_AUTHORITY_ID, publication))
            self.assertEqual(reject_code, server.last_bot_state_reject_code)
            self.assertEqual(
                committed, self._canonical_bot_commit_snapshot(server),
                reject_code)

        # Because every malformed future packet left the accepted source
        # frontier untouched, the producer can resume from its last-good clock.
        now[0] = 100.260
        recovered = self._publication(server, 0.8)
        recovered['sample_time_us'] = 80000
        recovered['source_batch_horizon_us'] = 80000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, recovered),
            server.last_bot_state_reject)
        self.assertEqual(80000, server.bot_source_time_us)
        self.assertEqual(2, server.bot_state_revision)
        self.assertEqual(0.8, server.bot_states[11]['x'])

    def test_dispatcher_counts_validated_clock_rebase_as_advancement(self):
        now = [100.0]
        server, _, unused_socket = self._server(
            clock=lambda: now[0],
            before_manifest=lambda: now.__setitem__(0, 100.025))
        now[0] = 100.200
        baseline = self._publication(server, 0.4)
        baseline['sample_time_us'] = 40000
        baseline['source_batch_horizon_us'] = 40000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, baseline))

        handler = object.__new__(ClientHandler)
        wrapper = types.SimpleNamespace(state=server)
        malformed = self._publication(server, 1.0)
        malformed['sample_time_us'] = 1000000
        malformed['source_batch_horizon_us'] = 1000000
        malformed['bots'] = []
        malformed['type'] = 'bot_state'
        committed = self._canonical_bot_commit_snapshot(server)

        now[0] = 100.210
        self.assertFalse(handler._dispatch_simulation_worker_message(
            wrapper, server.simulation_worker, malformed))
        self.assertEqual('batch_shape', server.last_bot_state_reject_code)
        self.assertEqual(
            committed, self._canonical_bot_commit_snapshot(server))
        self.assertIs(server.simulation_worker,
                      wrapper.state.simulation_worker)
        self.assertTrue(server.simulation_worker.connected)

        complete = self._publication(server, 1.0)
        complete['sample_time_us'] = 1000000
        complete['source_batch_horizon_us'] = 1000000
        complete['type'] = 'bot_state'
        self.assertTrue(handler._dispatch_simulation_worker_message(
            wrapper, server.simulation_worker, complete))
        self.assertEqual('', server.last_bot_state_reject_code)
        self.assertEqual(1000000, server.bot_source_time_us)
        self.assertEqual(1.0, server.bot_states[11]['x'])

    def test_clock_rebase_absorbs_unobserved_fire_gap_without_freezing_bot(self):
        now = [100.0]
        server, _, unused_socket = self._server(
            clock=lambda: now[0],
            before_manifest=lambda: now.__setitem__(0, 100.025))
        now[0] = 100.200
        baseline = self._publication(server, 0.4)
        baseline['sample_time_us'] = 40000
        baseline['source_batch_horizon_us'] = 40000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, baseline))

        now[0] = 100.210
        rebased = self._publication(server, 1.0)
        rebased['sample_time_us'] = 1000000
        rebased['source_batch_horizon_us'] = 1000000
        bot = rebased['bots'][0]
        bot['fire_seq'] = 2
        bot['ammo_remaining'] = [18]
        bot['ammo_reload_pending'] = True
        bot['clip'] = 0
        bot.update(BattleState._ordinary_bot_burst(2, 0))

        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, rebased),
            server.last_bot_state_reject)
        self.assertEqual(2, server.bot_states[11]['fire_seq'])
        self.assertEqual([18], server.bot_states[11]['ammo_remaining'])
        self.assertEqual(set(), server.bot_pending_projectile_launches)

        now[0] = 100.240
        recovered = self._publication(server, 1.4)
        recovered['sample_time_us'] = 1040000
        recovered['source_batch_horizon_us'] = 1040000
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, recovered),
            server.last_bot_state_reject)

        now[0] = 100.270
        next_shot = self._publication(server, 1.8)
        next_shot['sample_time_us'] = 1080000
        next_shot['source_batch_horizon_us'] = 1080000
        bot = next_shot['bots'][0]
        bot['fire_seq'] = 3
        bot['ammo_remaining'] = [17]
        bot['ammo_reload_pending'] = True
        bot['clip'] = 0
        bot.update(BattleState._ordinary_bot_burst(3, 0))
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, next_shot),
            server.last_bot_state_reject)
        self.assertEqual(3, server.bot_states[11]['fire_seq'])
        self.assertIn((11, 3), server.bot_pending_projectile_launches)

    def test_bot_state_after_battle_result_is_an_idempotent_noop(self):
        server, unused_worker, unused_authority_socket = self._server()
        revision = server.bot_state_revision
        states = dict((bot_id, dict(state))
                      for bot_id, state in server.bot_states.items())
        server.battle_result = {'winner': 1, 'reason': 'all_destroyed'}

        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._publication(server, 99.0)))
        self.assertEqual(revision, server.bot_state_revision)
        self.assertEqual(states, server.bot_states)
        self.assertEqual('', server.last_bot_state_reject_code)

    def test_revision_advances_inside_one_coarse_clock_quantum(self):
        now = [100.0]
        server, _, authority_socket = self._server(
            clock=lambda: now[0],
            before_manifest=lambda: now.__setitem__(0, 100.025))

        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._publication(server, 1.0)))
        first_time_us = server.bot_state_time_us
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._publication(server, 2.0)))

        self.assertEqual(2, server.bot_state_revision)
        self.assertGreater(server.bot_state_time_us, first_time_us)
        server.tick_once(1.0 / 30.0)
        snapshots = [
            json.loads(payload.decode('utf-8'))
            for payload in authority_socket.payloads
            if json.loads(payload.decode('utf-8')).get('type') == 'snapshot']
        self.assertEqual(2, snapshots[-1]['bot_state_revision'])
        self.assertEqual(
            snapshots[-1]['bot_state_time_us'],
            snapshots[-1]['motion_time_us'])


class ServerReportedHealthTests(unittest.TestCase):
    @staticmethod
    def _server_with_bot():
        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        connection = _CaptureSocket()
        player = Player(
            1, connection, ('127.0.0.1', 1), team=1, slot=0,
            health=1000, max_health=1000)
        server.players[player.player_id] = player
        server.players[2] = Player(
            2, _CaptureSocket(), ('127.0.0.1', 2), team=2, slot=0)
        server.bot_states[28] = {
            'id': 28, 'team': 2, 'alive': True, 'frags': 0}
        return server, player, connection

    @staticmethod
    def _broadcast_health_event(connection):
        messages = [json.loads(payload.decode('utf-8'))
                    for payload in connection.payloads]
        events_message = next(message for message in messages
                              if message.get('type') == 'events')
        return next(event for event in events_message['events']
                    if event.get('kind') == 'health')

    def test_critical_lineage_does_not_repeat_vehicle_outfits(self):
        player = Player(
            1, _CaptureSocket(), ('127.0.0.1', 1),
            outfits={'1': 'large-seasonal-descriptor'})

        lineage = BattleState._commit_external_player_critical(
            player, _critical_payload({
                'name': 'engineHealth', 'hp': 25.0, 'max_hp': 100.0,
                'state': 'critical'}))

        self.assertEqual({
            'critical_revision': 1,
            'critical_base_revision': 1,
            'critical_ack_seq': 0,
        }, lineage)

    def test_modern_nonfatal_client_health_report_is_rejected(self):
        server, player, unused_connection = self._server_with_bot()
        critical_before = dict(player.critical)

        self.assertFalse(server._apply_reported_health(player, {
            'reported_health': 1000,
            'reported_critical': _critical_payload({
                'name': 'engineHealth', 'hp': 25.0, 'max_hp': 100.0,
                'state': 'critical'}),
            'reported_critical_base_revision': 0,
            'reported_critical_seq': 1,
            'reported_attacker': 2,
        }))
        self.assertEqual([], server.pending_events)
        self.assertEqual(critical_before, player.critical)
        self.assertEqual((0, 0), (
            player.critical_revision, player.critical_ack_seq))
        self.assertEqual(('', 0),
                         (player.death_attacker_kind,
                          player.death_attacker_id))
        self.assertEqual(0, server.players[2].frags)
        self.assertEqual(0, server.bot_states[28]['frags'])

    def test_modern_fatal_client_health_report_cannot_create_death(self):
        server, player, unused_connection = self._server_with_bot()
        player.health = 100

        self.assertFalse(server._apply_reported_health(player, {
            'reported_health': 0,
            'reported_reason': 1,
            'reported_attacker_bot': 28,
        }))
        self.assertEqual(('', 0),
                         (player.death_attacker_kind,
                          player.death_attacker_id))
        self.assertEqual(100, player.health)
        self.assertTrue(player.alive)
        self.assertEqual(0, server.bot_states[28]['frags'])
        self.assertEqual([], server.pending_events)

        self.assertFalse(server._apply_reported_health(player, {
            'reported_health': 0,
            'reported_reason': 1,
            'reported_attacker_bot': 28,
        }))
        self.assertEqual(0, server.bot_states[28]['frags'])

        pending_count = len(server.pending_events)
        critical_before = player.critical
        self.assertFalse(server._apply_reported_health(player, {
            'reported_health': 0,
            'reported_critical': _critical_payload({
                'name': 'leftTrackHealth', 'hp': 100.0,
                'max_hp': 100.0, 'state': 'normal'}),
            'reported_critical_base_revision': 0,
            'reported_critical_seq': 1,
        }))
        self.assertEqual(critical_before, player.critical)
        self.assertEqual((0, 0),
                         (player.critical_revision,
                          player.critical_ack_seq))
        self.assertEqual(pending_count, len(server.pending_events))

        calls = []
        server._apply_reported_health = lambda *args: calls.append(args)
        server.update_input(player.player_id, {
            'round_id': server.round_id,
            'reported_health': 0,
            'reported_critical': {'events': []},
        })
        self.assertEqual([], calls)


class ServerBotObservationRelayTests(unittest.TestCase):
    @staticmethod
    def _server():
        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        authority_socket = _CaptureSocket()
        guest_socket = _CaptureSocket()
        server.players[1] = Player(
            1, authority_socket, ('127.0.0.1', 1), team=1, slot=0)
        server.players[2] = Player(
            2, guest_socket, ('127.0.0.1', 2), team=1, slot=1)
        server.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        server.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        server.bot_manifest = [{
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Enemy',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
        }]
        server.bot_states[11] = {
            'id': 11, 'team': 2, 'alive': True,
            'health': 1000, 'max_health': 1000,
        }
        return server, authority_socket, guest_socket

    @staticmethod
    def _message(round_id, visible=True, target_id=2):
        observer_ids = [11] if visible else []
        return {
            'type': 'bot_observation', 'round_id': round_id,
            'contacts': [{
                'observing_team': 2, 'target_kind': 'human',
                'target_id': target_id, 'target_team': 1,
                'visible': bool(visible),
                'fresh': bool(visible),
                'time_left': 10.0 if visible else 0.0,
                'visible_by_bot_ids': observer_ids,
                'visible_by_player_ids': [],
                'shootable_by_bot_ids': [],
                'x': 10.0, 'y': 0.0, 'z': 20.0,
                'health': 1000, 'max_health': 1000,
            }],
            'affordances': [],
        }

    @staticmethod
    def _human_message(round_id, observer_ids=(1,), visible=True):
        observer_ids = list(observer_ids) if visible else []
        return {
            'type': 'bot_observation', 'round_id': round_id,
            'contacts': [{
                'observing_team': 1, 'target_kind': 'bot',
                'target_id': 11, 'target_team': 2,
                'visible': bool(visible),
                'fresh': bool(visible),
                'time_left': 10.0 if visible else 0.0,
                'visible_by_bot_ids': [],
                'visible_by_player_ids': observer_ids,
                'shootable_by_bot_ids': [],
                'x': 20.0, 'y': 0.0, 'z': 20.0,
                'health': 1000, 'max_health': 1000,
            }],
            'affordances': [],
        }

    def test_validated_visibility_is_relayed_once_to_every_participant(self):
        server, authority_socket, guest_socket = self._server()

        relay = server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id))

        self.assertIsInstance(relay, dict)
        self.assertEqual(frozenset((('player', 2),)),
                         server.bot_spotted[11])
        self.assertEqual({
            'observing_team': 2, 'target_kind': 'human',
            'target_id': 2, 'target_team': 1, 'visible': True,
            'fresh': True, 'time_left': 10.0,
            'visible_by_bot_ids': [11],
            'visible_by_player_ids': [],
            'shootable_by_bot_ids': [],
        }, relay['contacts'][0])
        self.assertTrue(server.broadcast_bot_observation(relay))
        for connection in (authority_socket, guest_socket):
            payloads = [json.loads(value.decode('utf-8'))
                        for value in connection.payloads]
            self.assertEqual([relay], payloads)

    def test_memory_contact_rejects_freshness_and_shooter_mismatches(self):
        server, unused_authority_socket, unused_guest_socket = self._server()
        bad_time = self._message(server.round_id)
        bad_time['contacts'][0]['time_left'] = 0.0
        remembered_shooter = self._message(server.round_id)
        remembered_shooter['contacts'][0].update({
            'fresh': False,
            'visible_by_bot_ids': [],
            'shootable_by_bot_ids': [11],
        })

        self.assertFalse(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, bad_time))
        self.assertFalse(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, remembered_shooter))

    def test_valid_first_hidden_observation_is_a_noop_not_a_rejection(self):
        server, authority_socket, guest_socket = self._server()

        self.assertTrue(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id, visible=False)))
        self.assertFalse(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id - 1, visible=True)))

        self.assertEqual([], authority_socket.payloads)
        self.assertEqual([], guest_socket.payloads)

    def test_worker_human_spot_is_idempotent_and_earns_assist(self):
        server, unused_authority_socket, unused_guest_socket = self._server()
        message = self._human_message(server.round_id)

        self.assertIsInstance(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, message), dict)
        self.assertIsInstance(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, message), dict)
        self.assertEqual(frozenset((('bot', 11),)),
                         server.player_spotted[1])
        self.assertEqual(1, server._statistics_row('player', 1)['spotted'])

        server._record_damage(('player', 2), ('bot', 11), 75, {})
        self.assertEqual(
            75, server._statistics_row(
                'player', 1)['damage_assisted_radio'])

    def test_worker_human_spot_rejects_forged_observers(self):
        server, unused_authority_socket, unused_guest_socket = self._server()
        malformed = self._human_message(server.round_id)
        malformed['contacts'][0].pop('visible_by_player_ids')
        duplicate = self._human_message(server.round_id, (1, 1))
        wrong_team = self._human_message(server.round_id, (1,))
        server.players[1].team = 2

        self.assertFalse(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, malformed))
        self.assertFalse(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, duplicate))
        self.assertFalse(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, wrong_team))
        self.assertNotIn(1, server.player_spotted)

    def test_disconnect_and_complete_worker_batch_converge_spot_ledger(self):
        server, unused_authority_socket, unused_guest_socket = self._server()
        message = self._human_message(server.round_id)
        self.assertIsInstance(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, message), dict)

        removed, unused_changed = server.remove_player(1)
        self.assertIsNotNone(removed)
        self.assertNotIn(1, server.player_spotted)
        server.players[1] = Player(
            1, _CaptureSocket(), ('127.0.0.1', 10), team=1, slot=0)

        self.assertIsInstance(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, message), dict)
        self.assertEqual(frozenset((('bot', 11),)),
                         server.player_spotted[1])
        hidden = self._human_message(server.round_id, (), visible=False)
        self.assertTrue(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, hidden))
        self.assertEqual(frozenset(), server.player_spotted[1])

    def test_modern_human_direct_spot_does_not_override_worker_observation(self):
        server, unused_authority_socket, unused_guest_socket = self._server()
        reporter_socket = _CaptureSocket()
        server.players[3] = Player(
            3, reporter_socket, ('127.0.0.1', 3), team=2, slot=0)
        server.player_spotted[3] = frozenset((('player', 2),))

        relay = server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id, visible=False, target_id=2))

        self.assertIs(True, relay)

    def test_retired_observation_targets_are_quiet_noops(self):
        server, unused_authority_socket, unused_guest_socket = self._server()
        server.players[2].participating = False

        self.assertTrue(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id, target_id=2)))
        self.assertFalse(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id, target_id=999)))

    def test_dead_target_and_observer_rows_are_quiet_noops(self):
        server, unused_authority_socket, unused_guest_socket = self._server()
        server.players[2].alive = False
        self.assertTrue(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id, target_id=2)))

        server.players[2].alive = True
        server.bot_states[11]['alive'] = False
        server.bot_states[11]['health'] = 0
        self.assertTrue(server.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID,
            self._message(server.round_id, target_id=2)))
        self.assertEqual(frozenset(), server.bot_spotted[11])


class BotRuntimeTests(unittest.TestCase):
    def setUp(self):
        self._modules = dict((key, value) for key, value in sys.modules.items()
                             if key == 'gui' or key.startswith('gui.'))
        self.module = _load(); self.adapters = []
        def factory(*args):
            adapter = _Adapter(*args); self.adapters.append(adapter); return adapter
        self.runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=factory,
            direction_probe=lambda position, yaw: {'clear': True, 'slope': .2},
            ground_probe=lambda unused_x, unused_z, unused_hint: 0.0,
            physics_ground_probe=lambda unused_x, unused_z, unused_hint: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        self.start = {'round_id': 5, 'map': '01_karelia', 'bot_authority_id': 1,
                      'bots': [{'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot'}]}

    def tearDown(self):
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self._modules)

    def test_probe_totals_count_only_real_query_seams_and_are_pull_only(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            direction_probe=lambda *unused: {'clear': True},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            physics_ground_probe=lambda *unused: 0.0)
        source = {'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                  'view_range': 500.0}
        target = {'id': 12, 'network_id': 12, 'kind': 'bot', 'team': 2,
                  'x': 100.0, 'y': 0.0, 'z': 0.0,
                  'position': (100.0, 0.0, 0.0), 'fire_seq': 0,
                  'speed': 0.0}
        before = runtime.probe_totals()

        runtime._probe_direction((0.0, 0.0, 0.0), 0.0)
        self.assertTrue(runtime._visible(source, target, 1.0))
        self.assertTrue(runtime._visible(source, target, 1.01))
        self.assertTrue(runtime._shot_clear(source, target, 1.0))
        self.assertTrue(runtime._shot_clear(source, target, 1.01))
        runtime._terrain_support({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'half_length': 3.0})

        after = runtime.probe_totals()
        self.assertEqual(after, runtime.probe_totals())
        self.assertEqual(
            {'visibility': 1, 'lane': 1, 'cover': 0,
             'ground': 1, 'motion': 1},
            dict(zip(self.module.PROBE_KINDS,
                     (after[index] - before[index]
                      for index in range(len(after))))))

    def test_visibility_observation_is_2_5hz_while_state_stays_30hz(self):
        self.runtime.battle_start(self.start)
        publications = 0
        observation_times = []

        for frame in range(120):
            now = 1.0 + (frame + 1) / 60.0
            outgoing = self.runtime.update(1.0 / 60.0, now)
            publications += sum(
                message['type'] == 'bot_state' for message in outgoing)
            if any(message['type'] == 'bot_observation'
                   for message in outgoing):
                observation_times.append(now)

        self.assertEqual(60, publications)
        self.assertEqual(5, len(observation_times))
        self.assertGreaterEqual(
            observation_times[1] - observation_times[0],
            self.module.OBSERVATION_SECONDS - 1.0e-6)

    def test_direction_probe_receives_speed_and_descriptor_contract(self):
        calls = []
        descriptor = _combat_descriptor()

        def direction(position, yaw, speed, type_descriptor):
            calls.append((position, yaw, speed, type_descriptor))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        runtime = self.module.BotRuntime(1, direction_probe=direction)

        result = runtime._probe_direction(
            (1.0, 2.0, 3.0), 0.25, 7.5, descriptor)

        self.assertTrue(result['clear'])
        self.assertEqual(1, len(calls))
        self.assertEqual(((1.0, 2.0, 3.0), 0.25, 7.5), calls[0][:3])
        self.assertIs(descriptor, calls[0][3])

    def test_direction_probe_body_type_error_is_not_retried_as_old_arity(self):
        calls = []

        def direction(position, yaw, speed, descriptor):
            calls.append((position, yaw, speed, descriptor))
            raise TypeError('probe body failed after native work')

        runtime = self.module.BotRuntime(1, direction_probe=direction)

        result = runtime._probe_direction(
            (1.0, 2.0, 3.0), 0.25, 7.5, _combat_descriptor())

        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])
        self.assertEqual(1, len(calls))

    def test_submerged_planner_defers_baked_hazard_to_native_escape_probe(self):
        class Grid(object):
            prebaked = True

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_hazards):
                return True

            def segment_clear(self, unused_start, unused_end):
                raise AssertionError('fatal hazard should decide first')

        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(grid=Grid())
        runtime.baked_graph = {'bake': {
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': (3.0,),
        }}

        self.assertFalse(runtime._planner_corridor_clear(
            (0.0, 0.0, 0.0), 0.0, 0.0))
        self.assertIsNone(runtime._planner_corridor_clear(
            (0.0, 0.0, 0.0), 0.0, 0.0, wet_escape=True))

    def test_baked_planner_rejects_unplanned_shallow_but_allows_astar_step(self):
        class Grid(object):
            prebaked = True
            cell_size = 4.0

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, hazard_mask):
                return bool(hazard_mask & self_module.BAKED_SHALLOW_WATER)

            def segment_clear(self, unused_start, unused_end):
                return True

        self_module = self.module
        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(grid=Grid())
        runtime.baked_graph = {'bake': {
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': (3.0,),
        }}

        self.assertFalse(runtime._planner_corridor_clear(
            (0.0, 0.0, 0.0), 0.0, 0.0))
        self.assertTrue(runtime._planner_corridor_clear(
            (0.0, 0.0, 0.0), 0.0, 0.0, allow_shallow=True))

    def test_controlled_shallow_step_cannot_admit_a_fatal_baked_hazard(self):
        class Grid(object):
            prebaked = True
            cell_size = 4.0

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, hazard_mask):
                return bool(hazard_mask & self_module.BAKED_FATAL_HAZARDS)

            def segment_clear(self, unused_start, unused_end):
                raise AssertionError('fatal hazard should decide first')

        self_module = self.module
        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(grid=Grid())
        runtime.baked_graph = {'bake': {
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': (3.0,),
        }}

        self.assertFalse(runtime._planner_corridor_clear(
            (0.0, 0.0, 0.0), 0.0, 0.0, allow_shallow=True))

    def test_repeated_water_veto_reports_a_blocked_step_for_that_bot(self):
        aim = (0.0, 0.0, 200.0)
        command = self._stationary_command()
        command.update({
            'throttle': 1.0, 'combat_mode': 'route',
            'aim_position': aim, 'face_position': aim, 'move_position': aim,
            'recovery_mode': 'drive', 'movement_intent': True,
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': False, 'collision': False, 'water': True,
                'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        reports = []
        runtime.navigator.report_blocked_step = (
            lambda *args: reports.append(args))
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)

        runtime.update(.04, 1.0)

        self.assertEqual([(11, (0.0, 0.0, 0.0), aim, 1.0)], reports)
        self.assertEqual((0.0, 0.0), (state['x'], state['z']))

    def test_post_turn_travel_yaw_cannot_enter_unplanned_shallow(self):
        graph = _graph()
        graph['hazards'] = (0, self.module.BAKED_SHALLOW_WATER, 0)
        graph['bake'] = {
            'max_grade': 0.30,
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': (3.0,),
        }
        command = self._stationary_command()
        command.update({
            'target_yaw': math.pi / 2.0,
            'throttle': 1.0,
            'turn': 1.0,
            'combat_mode': 'route',
            'move_position': (100.0, 0.0, 0.0),
            'recovery_mode': 'drive',
            'movement_intent': True,
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=graph)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=8.0,
                     grounded_once=True)
        original_traverse = self.module.vehicle_physics.traverse_step
        original_longitudinal = self.module.vehicle_physics.longitudinal_step
        self.module.vehicle_physics.traverse_step = (
            lambda *unused, **kwargs: math.pi / (2.0 * 0.04))
        self.module.vehicle_physics.longitudinal_step = (
            lambda *unused, **kwargs: 8.0)
        try:
            runtime.update(0.04, 1.0)
        finally:
            self.module.vehicle_physics.traverse_step = original_traverse
            self.module.vehicle_physics.longitudinal_step = original_longitudinal

        self.assertAlmostEqual(math.pi / 2.0, state['yaw'])
        self.assertEqual((0.0, 0.0), (state['x'], state['z']))
        self.assertEqual(0, state['movement_dir'])
        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)

    def test_post_turn_travel_yaw_allows_only_its_controlled_shallow_step(self):
        graph = _graph()
        graph['hazards'] = (0, self.module.BAKED_SHALLOW_WATER, 0)
        graph['bake'] = {
            'max_grade': 0.30,
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': (3.0,),
        }
        command = self._stationary_command()
        command.update({
            'target_yaw': math.pi / 2.0,
            'throttle': 1.0,
            'turn': 1.0,
            'combat_mode': 'route',
            'move_position': (100.0, 0.0, 0.0),
            'recovery_mode': 'drive',
            'movement_intent': True,
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=graph)
        runtime.battle_start(self.start)
        admitted_yaws = []

        def controlled(unused_bot_id, unused_position, sample_yaw):
            admitted_yaws.append(sample_yaw)
            return abs(self.module._angle_delta(
                sample_yaw, math.pi / 2.0)) <= 1.0e-6

        runtime.navigator.controlled_shallow_step = controlled
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=8.0,
                     grounded_once=True)
        original_traverse = self.module.vehicle_physics.traverse_step
        original_longitudinal = self.module.vehicle_physics.longitudinal_step
        self.module.vehicle_physics.traverse_step = (
            lambda *unused, **kwargs: math.pi / (2.0 * 0.04))
        self.module.vehicle_physics.longitudinal_step = (
            lambda *unused, **kwargs: 8.0)
        try:
            runtime.update(0.04, 1.0)
        finally:
            self.module.vehicle_physics.traverse_step = original_traverse
            self.module.vehicle_physics.longitudinal_step = original_longitudinal

        self.assertAlmostEqual(math.pi / 2.0, state['yaw'])
        self.assertGreater(state['x'], 0.0)
        self.assertEqual(1, state['movement_dir'])
        self.assertTrue(any(abs(self.module._angle_delta(
            sample_yaw, math.pi / 2.0)) <= 1.0e-6
                            for sample_yaw in admitted_yaws))

    def test_baked_planner_ranks_only_the_next_link_at_a_tight_turn(self):
        ends = []

        class Grid(object):
            prebaked = True
            cell_size = 4.0

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_hazards):
                return False

            def segment_clear(self, unused_start, end):
                ends.append(end)
                return True

        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(grid=Grid())
        runtime.baked_graph = {'bake': {
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': (3.0,),
        }}

        self.assertTrue(runtime._planner_corridor_clear(
            (10.0, 0.0, 20.0), math.pi / 2.0, 20.0))
        self.assertEqual(1, len(ends))
        self.assertAlmostEqual(14.0, ends[0][0])
        self.assertAlmostEqual(20.0, ends[0][2])

    def test_baked_corridor_negative_defers_to_native_candidate_probe(self):
        class Grid(object):
            prebaked = True
            cell_size = 4.0

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_hazards):
                return False

            def segment_clear(self, unused_start, unused_end):
                return False

        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(grid=Grid())
        runtime.baked_graph = {'bake': {
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': (3.0,),
        }}

        self.assertIsNone(runtime._planner_corridor_clear(
            (10.0, 0.0, 20.0), math.pi / 2.0, 20.0))

    def test_bot_drowning_requires_ten_continuous_seconds_and_publishes_death(self):
        depth = [2.0]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _critical_descriptor(),
            adapter_factory=lambda *unused: _Adapter(),
            direction_probe=lambda *unused: {'clear': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph(),
            water_depth_probe=lambda unused_position: depth[0])
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update({
            'health': 640, 'alive': True, 'display_health': 640,
            'speed': 7.0, 'movement_dir': 1, 'rotation_dir': -1,
            'target_kind': 'human', 'target_id': 1,
        })
        runtime._friendly_repositions[11] = {'until': 99.0}
        payload = _critical_payload(
            {'name': 'engineHealth', 'hp': 0.0, 'max_hp': 100.0,
             'state': 'destroyed'},
            destroyed=('engineHealth',), crew_ko=('commander', 'driver'))
        original = self.module.critical_damage.apply_death
        drowning_calls = []

        def apply_death(shadow, cause):
            drowning_calls.append(shadow.health)
            self.assertEqual('drowning', cause)
            return payload

        self.module.critical_damage.apply_death = apply_death
        try:
            for unused in range(20):
                self.assertFalse(runtime._advance_bot_drowning(state, 0.3))
            depth[0] = 0.5
            self.assertFalse(runtime._advance_bot_drowning(state, 0.3))
            self.assertEqual(0.0, state['_drown_time'])
            self.assertEqual(0.5, state['_water_depth'])
            depth[0] = 2.0
            for unused in range(33):
                self.assertFalse(runtime._advance_bot_drowning(state, 0.3))
            self.assertTrue(state['alive'])
            self.assertTrue(runtime._advance_bot_drowning(state, 0.3))
        finally:
            self.module.critical_damage.apply_death = original

        self.assertEqual([640], drowning_calls)
        self.assertEqual(0, state['health'])
        self.assertFalse(state['alive'])
        self.assertEqual(640, state['display_health'])
        self.assertEqual(5, state['death_reason'])
        self.assertEqual(['commander', 'driver'],
                         state['critical']['crew_ko'])
        self.assertEqual((0.0, 0, 0), (
            state['speed'], state['movement_dir'], state['rotation_dir']))
        self.assertIsNone(state['target_kind'])
        self.assertIsNone(state['target_id'])
        self.assertNotIn(11, runtime._friendly_repositions)
        self.assertTrue(runtime._mark_combat_publication(state))
        projected = self.module.lan_client.project_bot_state(state)
        self.assertEqual((0, False, 640, 5), (
            projected['health'], projected['alive'],
            projected['display_health'], projected['death_reason']))
        server, unused_manifest, unused_socket = \
            ServerBotStateRevisionTests._server()
        for name in ('shell_index', 'next_shell_index', 'ammo_remaining',
                     'ammo_reload_pending', 'clip', 'clip_size'):
            server.bot_states[11][name] = projected[name]
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': server.round_id, 'bots': [projected],
            }),
            server.last_bot_state_reject)
        self.assertEqual((0, False, 640, 5), (
            server.bot_states[11]['health'],
            server.bot_states[11]['alive'],
            server.bot_states[11]['display_health'],
            server.bot_states[11]['death_reason']))

    def test_drowning_accounts_for_the_whole_slow_callback_interval(self):
        runtime = self.module.BotRuntime(
            1, water_depth_probe=lambda unused_position: 2.0)
        runtime._descriptors[11] = _critical_descriptor()
        state = {
            'id': 11, 'health': 640, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'critical': {}, 'combat_fire_timer': 0.0,
        }

        self.assertFalse(runtime._advance_bot_drowning(state, 1.25))

        self.assertEqual(1.25, state['_drown_time'])
        self.assertEqual(0.0, state['_drown_check'])

    def test_bot_water_sensor_uses_turret_boundary_pose_and_recovery(self):
        depth = [1.6]
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused: _Adapter(),
            direction_probe=lambda *unused: {'clear': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            water_depth_probe=lambda unused_position: depth[0])
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
        })

        turret_offset, carrying_point = self.module._water_sensor_geometry(
            descriptor)
        self.assertEqual((0.0, 1.6, 0.0), turret_offset)
        self.assertEqual((1.5, 3.5), carrying_point)
        self.assertFalse(runtime._advance_bot_drowning(state, 0.3))
        self.assertFalse(state['_drowning'])

        depth[0] = 1.600001
        self.assertFalse(runtime._advance_bot_drowning(state, 0.3))
        self.assertTrue(state['_drowning'])
        self.assertEqual(0.3, state['_drown_time'])
        depth[0] = 1.6
        self.assertFalse(runtime._advance_bot_drowning(state, 0.3))
        self.assertFalse(state['_drowning'])
        self.assertEqual(0.0, state['_drown_time'])

        state['pitch'] = math.pi * 0.5
        depth[0] = 0.01
        self.assertFalse(runtime._advance_bot_drowning(state, 0.3))
        self.assertTrue(state['_drowning'])

    def test_bot_overturn_matches_ignore_recovery_and_death_law(self):
        runtime = self.module.BotRuntime(1)
        runtime._descriptors[11] = _critical_descriptor()
        state = {
            'id': 11, 'alive': True, 'health': 640,
            'display_health': 640, 'pitch': math.radians(71.0),
            'roll': 0.0, 'speed': 7.0, 'movement_dir': 1,
            'rotation_dir': -1, 'target_kind': 'human', 'target_id': 2,
            'critical': {}, 'combat_fire_timer': 0.0,
        }
        runtime._turn_speeds[11] = 0.4

        self.assertFalse(runtime._advance_bot_overturn(state, 0.099))
        self.assertEqual(0, state.get('_overturn_level', 0))
        self.assertFalse(runtime._advance_bot_overturn(state, 0.001))
        self.assertEqual(1, state['_overturn_level'])
        self.assertFalse(state['_overturned'])
        state['pitch'] = 0.0
        self.assertFalse(runtime._advance_bot_overturn(state, 0.01))
        self.assertEqual((0.0, 0.0, 0, False), (
            state['_overturn_check'], state['_overturn_time'],
            state['_overturn_level'], state['_overturned']))

        state['pitch'] = math.radians(81.0)
        self.assertFalse(runtime._advance_bot_overturn(state, 0.1))
        self.assertTrue(state['_overturned'])
        self.assertEqual((0.0, 0, 0, 0.0), (
            state['speed'], state['movement_dir'], state['rotation_dir'],
            runtime._turn_speeds[11]))
        state['_overturn_time'] = 29.9
        self.assertTrue(runtime._advance_bot_overturn(state, 0.1))
        self.assertEqual((0, False, 0, 7), (
            state['health'], state['alive'], state['display_health'],
            state['death_reason']))
        self.assertIsNone(state['target_kind'])
        self.assertIsNone(state['target_id'])

    def test_countdown_prewarms_all_receipts_and_all_bots_start_together(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Prewarm-%d' % index}
            for index in range(29)
        ]
        direction_calls = []
        receipt_calls = []

        class StaticGrid(object):
            prebaked = True

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_mask):
                return False

            def segment_clear(self, unused_start, unused_end):
                return True

        def direction(position, yaw, speed, unused_descriptor):
            direction_calls.append((tuple(position), float(yaw), float(speed)))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_calls.append((tuple(position), float(yaw), float(speed)))
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        graph = _graph()
        graph['bake'].update({
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': [3.0, 6.0],
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **unused_kwargs:
                _FixedAdapter(command),
            direction_probe=direction, world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=graph)
        runtime.battle_start(dict(self.start, bots=roster))
        runtime.navigator.grid = StaticGrid()
        initial_poses = dict((bot_id, (
            state['x'], state['y'], state['z'], state['yaw']))
            for bot_id, state in runtime.states.items())

        for frame in range(29):
            before_direction = len(direction_calls)
            before_receipt = len(receipt_calls)
            before_ground = runtime.probe_totals()[3]
            self.assertTrue(runtime.prewarm_world_receipts(
                1.0 + frame / 24.0))
            self.assertEqual(1, len(direction_calls) - before_direction)
            self.assertEqual(1, len(receipt_calls) - before_receipt)
            self.assertEqual(4, runtime.probe_totals()[3] - before_ground)
            self.assertTrue(all(
                state['movement_dir'] == 0
                for state in runtime.states.values()))
            self.assertEqual(initial_poses, dict((bot_id, (
                state['x'], state['y'], state['z'], state['yaw']))
                for bot_id, state in runtime.states.items()))

        self.assertEqual(29, len(runtime._motion_probe_cache))
        self.assertTrue(all(
            'pose_sample' in state for state in runtime.states.values()))
        self.assertTrue(all(
            self.module.BotRuntime._world_receipt_contains(
                cached['result']['world_receipt'], cached['position'],
                cached['yaw'], 0.000001, 0.1)
            for cached in runtime._motion_probe_cache.values()))

        direction_before_live = len(direction_calls)
        receipts_before_live = len(receipt_calls)
        ground_before_live = runtime.probe_totals()[3]
        runtime.update(.04, 3.0)

        self.assertEqual(29, len(direction_calls) - direction_before_live)
        self.assertEqual(0, len(receipt_calls) - receipts_before_live)
        self.assertEqual(29, runtime.probe_totals()[3] - ground_before_live)
        self.assertTrue(all(
            state['movement_dir'] == 1
            for state in runtime.states.values()))
        # Production _direction_probe is eight native rays, a receipt is nine,
        # and one visual pose is four. Countdown peak is 21 raw rays; the
        # first live tick is 232 motion plus 29 centre supports = 261.
        self.assertEqual(21, 8 + 9 + 4)
        self.assertEqual(261, 29 * 8 + 29)

    def test_countdown_prewarm_rejects_malformed_receipt_fail_closed(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **unused_kwargs: _FixedAdapter({
                'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
                'shell_index': 0, 'fire_allowed': False,
                'target_id': None, 'fire_range': 0.0,
                'combat_mode': 'route', 'aim_position': None,
                'face_position': None, 'move_position': None,
                'recovery_mode': 'drive', 'movement_intent': True,
            }),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=lambda *unused: {'bad': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(self.start)

        self.assertFalse(runtime.prewarm_world_receipts(1.0))
        self.assertEqual({}, runtime._motion_probe_cache)
        self.assertEqual(0, runtime.states[11]['movement_dir'])

    def test_contained_receipts_refresh_before_full_roster_motion_stalls(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Refresh-%d' % index}
            for index in range(29)
        ]
        frame = [0]
        frame_receipts = [0, 0, 0]

        def receipt(position, yaw, speed, unused_descriptor):
            if frame[0] >= 0:
                frame_receipts[frame[0]] += 1
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **unused_kwargs:
                _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))
        frame[0] = -1
        for index in range(29):
            self.assertTrue(runtime.prewarm_world_receipts(
                1.0 + index / 24.0))
        runtime.adapter.decide = lambda unused_state, unused_clear: dict(
            command)
        for state in runtime.states.values():
            yaw = state['yaw']
            state['x'] += math.sin(yaw) * 6.1
            state['z'] += math.cos(yaw) * 6.1
            state['grounded_once'] = True

        for frame_index, now in enumerate((3.0, 3.04, 3.08)):
            frame[0] = frame_index
            runtime.update(.04, now)
            self.assertLessEqual(
                frame_receipts[frame_index],
                self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
            self.assertTrue(all(
                state['movement_dir'] == 1
                for state in runtime.states.values()))

        self.assertEqual([13, 13, 3], frame_receipts)
        self.assertTrue(all(
            abs(cached['result']['world_receipt']['origin'][0] -
                cached['position'][0]) <= 1e-9 and
            abs(cached['result']['world_receipt']['origin'][2] -
                cached['position'][2]) <= 1e-9
            for cached in runtime._motion_probe_cache.values()))

    def test_final_world_receipt_runs_once_after_seven_planning_candidates(self):
        descriptor = _combat_descriptor()
        planning_yaws = tuple(index * 0.17 for index in range(7))
        direction_calls = []
        receipt_calls = []
        command = {
            'target_yaw': 0.00004, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        adapter = _FixedAdapter(command)

        def direction(position, yaw, speed, type_descriptor):
            direction_calls.append((position, yaw, speed, type_descriptor))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        def receipt(position, yaw, speed, type_descriptor):
            receipt_calls.append((position, yaw, speed, type_descriptor))
            return {
                'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **unused_kwargs: adapter,
            direction_probe=direction, world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(yaw=0.00004, speed=4.0, grounded_once=True)

        def decide(unused_state, clear):
            for candidate in planning_yaws:
                self.assertTrue(clear(candidate))
            return dict(command)

        adapter.decide = decide
        runtime.update(.04, 1.0)

        # This synthetic graph lacks the shipped static-corridor contract, so
        # all seven planner candidates use the native fallback.  The selected
        # unrounded heading must still receive an independent commit probe.
        self.assertEqual(8, len(direction_calls))
        self.assertEqual(1, len(receipt_calls))
        self.assertAlmostEqual(0.00004, receipt_calls[0][1])
        self.assertIs(descriptor, receipt_calls[0][3])
        cached = runtime._motion_probe_cache[11]['result']
        self.assertAlmostEqual(
            0.00004, cached['world_receipt']['yaw'])

    def test_baked_planner_ranking_never_replaces_selected_native_gate(self):
        descriptor = _combat_descriptor()
        planning_yaws = tuple(index * 0.17 for index in range(7))
        direction_calls = []
        receipt_calls = []
        graph_calls = []
        command = {
            'target_yaw': 0.00004, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        adapter = _FixedAdapter(command)

        class StaticGrid(object):
            prebaked = True

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_mask):
                return False

            def segment_clear(self, start, end):
                graph_calls.append((start, end))
                return True

        def direction(
                position, yaw, speed, type_descriptor, maximum_distance):
            direction_calls.append((
                position, yaw, speed, type_descriptor, maximum_distance))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        def receipt(
                position, yaw, speed, type_descriptor, maximum_distance):
            receipt_calls.append((
                position, yaw, speed, type_descriptor, maximum_distance))
            return {
                'distance': float(maximum_distance),
                'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        graph = _graph()
        graph['bake'].update({
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': [3.0, 6.0],
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **unused_kwargs: adapter,
            direction_probe=direction, world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=graph)
        runtime.battle_start(self.start)
        runtime.navigator.grid = StaticGrid()
        state = runtime.states[11]
        state.update(yaw=0.00004, speed=4.0, grounded_once=True)

        def decide(unused_state, clear):
            for candidate in planning_yaws:
                self.assertTrue(clear(candidate))
            return dict(command)

        adapter.decide = decide
        runtime.update(.04, 1.0)

        self.assertEqual(7, len(graph_calls))
        self.assertEqual(1, len(direction_calls))
        self.assertEqual(1, len(receipt_calls))
        self.assertAlmostEqual(0.00004, direction_calls[0][1])
        self.assertAlmostEqual(0.00004, receipt_calls[0][1])
        self.assertAlmostEqual(6.0, direction_calls[0][4])
        self.assertAlmostEqual(6.0, receipt_calls[0][4])
        self.assertEqual(1, runtime.states[11]['movement_dir'])

    def test_baked_planner_clear_cannot_bypass_native_selected_wall(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        adapter = _FixedAdapter(command)
        receipt_calls = []

        class StaticGrid(object):
            prebaked = True

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_mask):
                return False

            def segment_clear(self, unused_start, unused_end):
                return True

        graph = _graph()
        graph['bake'].update({
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': [3.0, 6.0],
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **unused_kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': False, 'collision': True, 'slope': 0.0},
            world_receipt_probe=lambda *args: receipt_calls.append(args),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=graph)
        runtime.battle_start(self.start)
        runtime.navigator.grid = StaticGrid()

        runtime.update(.04, 1.0)

        self.assertEqual([], receipt_calls)
        self.assertEqual(0, runtime.states[11]['movement_dir'])

    def test_baked_planner_negative_restores_native_candidate_fallback(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        adapter = _FixedAdapter(command)
        calls = []

        class NegativeGrid(object):
            prebaked = True

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_mask):
                return False

            def segment_clear(self, unused_start, unused_end):
                return False

        graph = _graph()
        graph['bake'].update({
            'vehicle_half_width': 2.15,
            'edge_clearance_radii': [3.0, 6.0],
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **unused_kwargs: adapter,
            direction_probe=lambda *unused: calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=graph)
        runtime.battle_start(self.start)
        runtime.navigator.grid = NegativeGrid()

        runtime.update(.04, 1.0)

        # _FixedAdapter asks once, followed by the independent selected gate.
        self.assertEqual(2, len(calls))
        self.assertEqual(1, runtime.states[11]['movement_dir'])

    def test_reverse_final_world_receipt_receives_exact_travel_heading(self):
        command = {
            'target_yaw': 0.0, 'throttle': -1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'reverse_turn', 'movement_intent': True,
        }
        receipt_calls = []

        def receipt(
                position, yaw, speed, unused_descriptor, maximum_distance):
            receipt_calls.append((
                tuple(position), yaw, speed, maximum_distance))
            return {
                'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11].update(
            yaw=0.0, speed=-4.0, grounded_once=True)

        runtime.update(.04, 1.0)

        self.assertEqual(1, len(receipt_calls))
        self.assertAlmostEqual(math.pi, receipt_calls[0][1])
        self.assertLess(receipt_calls[0][2], 0.0)
        self.assertAlmostEqual(6.0, receipt_calls[0][3])
        cached = runtime._motion_probe_cache[11]['result']['world_receipt']
        self.assertAlmostEqual(math.pi, cached['yaw'])
        self.assertEqual(-1, cached['direction'])

    def test_deferred_final_world_receipt_uses_generic_and_retries(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        receipt_calls = []

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_calls.append((tuple(position), yaw, speed))
            if len(receipt_calls) == 1:
                return 'deferred'
            return {
                'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        runtime.update(.04, 1.0)
        first = runtime._motion_probe_cache[11]['result']
        self.assertTrue(first['_world_receipt_pending'])
        self.assertTrue(runtime._probe_is_clear(first))
        self.assertEqual(1, runtime.states[11]['movement_dir'])

        runtime.update(.04, 1.04)
        self.assertEqual(2, len(receipt_calls))
        self.assertIn(11, runtime._motion_probe_cache)
        self.assertIn(
            'world_receipt', runtime._motion_probe_cache[11]['result'])

    def test_hard_final_world_receipt_blocks_the_selected_motion(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=lambda *unused: False,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        runtime.update(.04, 1.0)

        state = runtime.states[11]
        self.assertEqual(0, state['movement_dir'])
        cached = runtime._motion_probe_cache[11]['result']
        self.assertFalse(runtime._probe_is_clear(cached))
        self.assertNotIn('world_receipt', cached)

    def test_full_roster_world_receipts_are_bounded_and_drain_in_three_frames(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Receipt-%d' % index}
            for index in range(29)
        ]
        frame = [0]
        receipt_counts = [0, 0, 0]

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_counts[frame[0]] += 1
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        expected_exact = (13, 26, 29)
        for frame_index, now in enumerate((1.0, 1.04, 1.08)):
            frame[0] = frame_index
            runtime.update(.04, now)
            self.assertLessEqual(
                receipt_counts[frame_index],
                self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
            self.assertEqual(29, len(runtime._motion_probe_cache))
            exact = sum(
                1 for cached in runtime._motion_probe_cache.values()
                if isinstance(cached['result'].get('world_receipt'), dict))
            self.assertTrue(all(
                runtime.states[bot_id]['movement_dir'] == 1
                for bot_id in runtime.states))
            self.assertEqual(expected_exact[frame_index], exact)
            self.assertTrue(all(
                ('world_receipt' in cached['result'] or
                 cached['result'].get('_world_receipt_pending', False))
                for cached in runtime._motion_probe_cache.values()))

        self.assertEqual([13, 13, 3], receipt_counts)

    def test_ineligible_receiptless_bots_do_not_block_receipt_refreshes(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class MixedAdapter(_FixedAdapter):
            def decide(self, state, clear):
                result = _FixedAdapter.decide(self, state, clear)
                if state['id'] == 11:
                    result['throttle'] = 0.0
                return result

        descriptors = {}

        def descriptor(vehicle_name):
            if vehicle_name not in descriptors:
                value = _combat_descriptor()
                value.test_role = vehicle_name
                descriptors[vehicle_name] = value
            return descriptors[vehicle_name]

        def direction(position, yaw, speed, type_descriptor):
            if type_descriptor.test_role == 'receipt-hard':
                return {'clear': False, 'collision': True, 'slope': 0.0}
            return {'clear': True, 'collision': False, 'slope': 0.0}

        frame = [0]
        receipt_counts = [0, 0, 0]

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_counts[frame[0]] += 1
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Idle',
             'vehicle': 'receipt-idle'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Hard',
             'vehicle': 'receipt-hard'},
        ] + [
            {'id': 13 + index, 'team': 1, 'slot': 2 + index,
             'name': 'Moving-%d' % index, 'vehicle': 'receipt-moving'}
            for index in range(13)
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor,
            adapter_factory=lambda *unused, **kwargs: MixedAdapter(command),
            direction_probe=direction, world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        expected_receipts = (13, 0, 0)
        for frame_index, now in enumerate((1.0, 1.2, 1.4)):
            frame[0] = frame_index
            runtime.update(.04, now)
            self.assertEqual(
                expected_receipts[frame_index],
                receipt_counts[frame_index])
            self.assertLessEqual(
                receipt_counts[frame_index],
                self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
            self.assertTrue(all(
                runtime.states[bot_id]['movement_dir'] == 1
                for bot_id in range(13, 26)))

        for bot_id in (11, 12):
            result = runtime._motion_probe_cache[bot_id]['result']
            self.assertNotIn('world_receipt', result)

    def test_persistent_deferred_receipts_rotate_across_full_roster(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class FailureDriver(object):
            def __init__(self):
                self.calls = []

            def remember_failure(self, *args):
                self.calls.append(args)

        failure_driver = FailureDriver()
        adapter = _FixedAdapter(command)
        adapter.driver = failure_driver
        descriptors = {}

        def descriptor(vehicle_name):
            if vehicle_name not in descriptors:
                value = _combat_descriptor()
                value.test_bot_id = int(vehicle_name.rsplit('-', 1)[1])
                descriptors[vehicle_name] = value
            return descriptors[vehicle_name]

        receipt_attempts = []

        def receipt(position, yaw, speed, type_descriptor):
            receipt_attempts.append(type_descriptor.test_bot_id)
            return 'deferred'

        resolver_calls = []
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Deferred-%d' % index,
             'vehicle': 'deferred-%d' % (11 + index)}
            for index in range(29)
        ]

        def resolve_motion(*args):
            resolver_calls.append(args)
            return 'clear'

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor,
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            motion_resolver=resolve_motion)
        runtime.battle_start(dict(self.start, bots=roster))
        poses = {}
        for bot_id, state in runtime.states.items():
            state.update(speed=4.0, grounded_once=True)
            poses[bot_id] = (state['x'], state['y'], state['z'])

        original_step = self.module.vehicle_physics.longitudinal_step
        self.module.vehicle_physics.longitudinal_step = \
            lambda *unused, **unused_kwargs: 4.0
        try:
            cohorts = []
            service_counts = dict((bot_id, 0) for bot_id in range(11, 40))
            for frame_index in range(60):
                before = len(receipt_attempts)
                runtime.update(.04, 1.0 + frame_index * .04)
                frame_attempts = receipt_attempts[before:]
                cohort = set(frame_attempts)
                if frame_index < 3:
                    cohorts.append(cohort)
                    self.assertTrue(cohort)
                self.assertLessEqual(
                    len(frame_attempts),
                    self.module.MAX_WORLD_RECEIPTS_PER_FRAME)
                for bot_id in frame_attempts:
                    service_counts[bot_id] += 1
        finally:
            self.module.vehicle_physics.longitudinal_step = original_step

        self.assertNotEqual(cohorts[0], cohorts[1])
        self.assertEqual(set(range(11, 40)), set().union(*cohorts))
        self.assertLessEqual(
            max(service_counts.values()) - min(service_counts.values()), 1)
        self.assertEqual([], failure_driver.calls)
        self.assertEqual(60 * 29, len(resolver_calls))
        self.assertEqual(29, len(runtime._motion_probe_cache))
        for bot_id, state in runtime.states.items():
            start_x, start_y, start_z = poses[bot_id]
            travelled = (
                (state['x'] - start_x) * math.sin(state['yaw']) +
                (state['z'] - start_z) * math.cos(state['yaw']))
            self.assertAlmostEqual(4.0 * .04 * 60, travelled, places=8)
            self.assertAlmostEqual(start_y, state['y'])
            self.assertAlmostEqual(4.0, state['speed'])

    def test_persistent_deferred_bot_does_not_starve_receipt_refresh(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        descriptors = {}

        def descriptor(vehicle_name):
            if vehicle_name not in descriptors:
                value = _combat_descriptor()
                value.test_role = vehicle_name
                descriptors[vehicle_name] = value
            return descriptors[vehicle_name]

        attempts = []

        def receipt(position, yaw, speed, type_descriptor):
            attempts.append(type_descriptor.test_role)
            if type_descriptor.test_role == 'receipt-stuck':
                return 'deferred'
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Stuck',
             'vehicle': 'receipt-stuck'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Moving',
             'vehicle': 'receipt-moving'},
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        runtime.update(.04, 1.0)
        initial_moving_attempts = attempts.count('receipt-moving')
        self.assertEqual(1, initial_moving_attempts)
        self.assertEqual(1, runtime.states[12]['movement_dir'])
        for frame_index in range(1, 8):
            runtime.update(.04, 1.0 + frame_index * .04)
        self.assertGreater(attempts.count('receipt-stuck'), 1)
        self.assertEqual(
            initial_moving_attempts,
            attempts.count('receipt-moving'))
        self.assertEqual(1, runtime.states[12]['movement_dir'])

    def test_deferred_receipt_spike_advances_complete_wall_time(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        receipt_calls = []
        resolver_calls = []

        def receipt(*unused):
            receipt_calls.append(True)
            return 'deferred'

        def resolve_motion(*args):
            resolver_calls.append(args)
            return 'clear'

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **unused_kwargs:
                _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            motion_resolver=resolve_motion)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(speed=4.0, grounded_once=True)
        start_x, start_z, travel_yaw = state['x'], state['z'], state['yaw']

        original_step = self.module.vehicle_physics.longitudinal_step
        self.module.vehicle_physics.longitudinal_step = \
            lambda *unused, **unused_kwargs: 4.0
        try:
            runtime.update(.04, 1.0)
            runtime.update(.24, 1.24)
        finally:
            self.module.vehicle_physics.longitudinal_step = original_step

        travelled = (
            (state['x'] - start_x) * math.sin(travel_yaw) +
            (state['z'] - start_z) * math.cos(travel_yaw))
        self.assertAlmostEqual(4.0 * .28, travelled, places=9)
        self.assertEqual(3, len(resolver_calls))
        # The 0.24-second catch-up uses two bounded simulation slices, but the
        # exact native optimisation still runs at most once per render call.
        self.assertEqual(2, len(receipt_calls))

    def test_initial_motion_deadlines_fill_one_cycle_without_exceeding_it(self):
        now = 10.0
        interval = self.module.MOTION_PROBE_SECONDS
        offsets = sorted(
            self.module._motion_probe_deadline(now, bot_id, True) - now
            for bot_id in range(11, 40))

        self.assertEqual(29, len(set(round(value, 12)
                                     for value in offsets)))
        for index, offset in enumerate(offsets, 1):
            self.assertAlmostEqual(
                interval * index / 29.0, offset, places=12)
            self.assertGreater(offset, 0.0)
            self.assertLessEqual(offset, interval + 1e-12)

    def test_deferred_motion_probe_is_not_cached_and_retries_next_frame(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        results = [
            {'clear': True, 'collision': False, 'slope': 0.0,
             'deferred': True,
             'world_receipt': {
                 'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                 'origin': (0.0, 0.0, 0.0), 'yaw': 0.0,
                 'direction': 1}},
            {'clear': True, 'collision': False, 'slope': 0.0},
        ]
        calls = []

        def direction(*unused):
            calls.append(len(calls) + 1)
            return dict(results[min(len(calls) - 1, len(results) - 1)])

        adapter = _FixedAdapter(command)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=direction,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.adapter.decide = lambda unused_state, unused_clear: dict(
            command)

        runtime.update(.04, 1.0)
        self.assertEqual([1], calls)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertIn(11, runtime._decision_cache)

        runtime.update(.04, 1.04)
        self.assertEqual([1, 2], calls)
        self.assertIn(11, runtime._motion_probe_cache)
        self.assertIn(11, runtime._decision_cache)
        self.assertFalse(runtime._motion_probe_cache[11]['result'].get(
            'deferred', False))

    def test_fixed_bot_order_cannot_starve_deferred_probe_cohort(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        frame_budget = [24]
        frame_recasts = [0]

        def direction(*unused):
            # Model one full-width soft obstacle: six native recasts cover the
            # dual-height three-lane corridor. Four Bots fit the frame cap;
            # the fifth must defer and become the first uncached retry.
            if frame_budget[0] < 6:
                return {'clear': True, 'collision': False, 'slope': 0.0,
                        'deferred': True}
            frame_budget[0] -= 6
            frame_recasts[0] += 6
            return {'clear': True, 'collision': False, 'slope': 0.0}

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=direction,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        roster = [
            {'id': 11 + index, 'team': 2, 'slot': index,
             'name': 'Soft-%d' % index}
            for index in range(5)]
        runtime.battle_start(dict(self.start, bots=roster))
        runtime.adapter.decide = lambda unused_state, unused_clear: dict(
            command)

        runtime.update(.04, 1.0)
        self.assertLessEqual(frame_recasts[0], 24)
        self.assertEqual(4, len(runtime._motion_probe_cache))
        self.assertNotIn(15, runtime._motion_probe_cache)

        frame_budget[0] = 24
        frame_recasts[0] = 0
        runtime.update(.04, 1.04)
        self.assertLessEqual(frame_recasts[0], 24)
        self.assertEqual({11, 12, 13, 14, 15},
                         set(runtime._motion_probe_cache))

    def test_bot_soft_motion_contact_preserves_speed_without_moving(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        calls = []

        def resolver(*args):
            calls.append(args)
            return 'soft'

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)
        before_position = (state['x'], state['y'], state['z'])

        runtime.update(.04, 1.0)

        self.assertEqual(1, len(calls))
        self.assertEqual(11, calls[0][0])
        self.assertEqual(before_position,
                         (state['x'], state['y'], state['z']))
        self.assertGreater(state['speed'], 4.0)
        soft_speed = state['speed']

        runtime.update(.04, 1.04)

        self.assertEqual(before_position,
                         (state['x'], state['y'], state['z']))
        self.assertAlmostEqual(soft_speed, state['speed'])
        self.assertEqual(2, len(calls))

        hard_calls = []

        def hard_resolver(*args):
            hard_calls.append(args)
            return 'hard'

        hard_runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=hard_resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        hard_runtime.battle_start(self.start)
        hard_state = hard_runtime.states[11]
        hard_state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                          grounded_once=True)

        hard_runtime.update(.04, 1.0)

        self.assertEqual(5, len(hard_calls))
        self.assertEqual(before_position,
                         (hard_state['x'], hard_state['y'], hard_state['z']))
        self.assertAlmostEqual(
            self.module.vehicle_physics.hard_contact_step(
                soft_speed, .04)[0],
            hard_state['speed'])

    def test_five_hz_lookahead_collision_defers_to_exact_motion_resolver(self):
        command = {
            'target_yaw': math.pi * 0.5, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (200.0, 0.0, 0.0),
            'face_position': (200.0, 0.0, 0.0),
            'move_position': (200.0, 0.0, 0.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        exact_calls = []

        def resolver(*args):
            exact_calls.append(args)
            return 'clear'

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': False, 'collision': True, 'water': False,
                'slope': 0.0},
            motion_resolver=resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=math.pi * 0.5, speed=4.0,
                     grounded_once=True)
        positions = []

        for now in (1.0, 1.2, 1.4):
            runtime.update(.2, now)
            positions.append(state['x'])

        self.assertEqual(3, len(exact_calls))
        self.assertEqual(1, state['movement_dir'])
        self.assertLess(0.0, positions[0])
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])
        self.assertEqual(0, runtime._hard_contact_grinds.get(11, 0))

    def test_bot_hard_contact_uses_shared_second_glancing_path(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        calls = []
        destroyed = []

        def resolver(*args):
            commit_enabled = args[7] if len(args) > 7 else True
            calls.append((args, commit_enabled))
            if len(calls) == 1:
                return 'hard'
            if commit_enabled:
                destroyed.append(args[2])
            if args[2] == -0.55:
                return 'crushed' if commit_enabled else 'clear'
            return 'hard'

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)

        runtime.update(.04, 1.0)

        contact_speed = calls[0][0][3]
        expected_speed, delta_x, delta_z = \
            self.module.vehicle_physics.hard_contact_step(
                contact_speed, .04, grinding=False, slide_yaw=-0.55)
        self.assertEqual(4, len(calls))
        self.assertEqual([0.55, -0.55, -0.55],
                         [call[0][2] for call in calls[1:]])
        self.assertEqual([False, False, True],
                         [call[1] for call in calls[1:]])
        self.assertEqual([-0.55], destroyed)
        self.assertAlmostEqual(expected_speed, state['speed'])
        self.assertAlmostEqual(delta_x, state['x'])
        self.assertAlmostEqual(delta_z, state['z'])
        self.assertEqual(
            self.module.vehicle_physics.HARD_CONTACT_GRIND_TICKS,
            runtime._hard_contact_grinds[11])

    def test_realised_hard_contact_invalidates_cached_command_and_probe(self):
        attempted_yaw = 0.25
        aim = (math.sin(attempted_yaw) * 200.0, 0.0,
               math.cos(attempted_yaw) * 200.0)
        command = {
            'target_yaw': attempted_yaw, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': aim, 'face_position': aim,
            'move_position': aim,
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class FailureDriver(object):
            def __init__(self):
                self.calls = []

            def remember_failure(self, *args):
                self.calls.append(args)

        adapter = _FixedAdapter(command)
        adapter.driver = FailureDriver()
        status = ['hard']
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': False, 'collision': True, 'water': False,
                'slope': 0.0},
            motion_resolver=lambda *unused: status[0],
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        contact_reports = []
        runtime.navigator.report_blocked_step = (
            lambda *args: contact_reports.append(args))
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=attempted_yaw,
                     speed=4.0, grounded_once=True)
        before_position = (state['x'], state['y'], state['z'])

        runtime.update(.04, 1.0)

        self.assertEqual(before_position,
                         (state['x'], state['y'], state['z']))
        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertEqual([(11, attempted_yaw, 5.0)],
                         adapter.driver.calls)
        self.assertEqual(1, len(contact_reports))
        self.assertEqual((11, before_position, aim, 1.0),
                         contact_reports[0])
        self.assertEqual(1, len(adapter.calls))

        status[0] = 'clear'
        runtime.update(.04, 1.04)

        self.assertNotEqual(before_position,
                            (state['x'], state['y'], state['z']))
        self.assertEqual(2, len(adapter.calls))
        self.assertIn(11, runtime._decision_cache)
        self.assertIn(11, runtime._motion_probe_cache)

    def test_nonhard_realised_contacts_keep_cached_command_and_probe(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }

        class FailureDriver(object):
            def __init__(self):
                self.calls = []

            def remember_failure(self, *args):
                self.calls.append(args)

        for motion_status in ('soft', 'cap_crushed'):
            with self.subTest(motion_status=motion_status):
                adapter = _FixedAdapter(command)
                adapter.driver = FailureDriver()
                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: adapter,
                    direction_probe=lambda *unused: {
                        'clear': True, 'collision': False, 'slope': 0.0},
                    motion_resolver=lambda *unused, value=motion_status: value,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, baked_graph=_graph())
                runtime.battle_start(self.start)
                runtime.states[11].update(
                    x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                    grounded_once=True)

                runtime.update(.04, 1.0)

                self.assertIn(11, runtime._decision_cache)
                self.assertIn(11, runtime._motion_probe_cache)
                self.assertEqual([], adapter.driver.calls)

    def test_bot_cap_crush_keeps_real_speed_then_moves_next_tick(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        statuses = iter(('cap_crushed', 'crushed'))
        calls = []

        def resolver(*args):
            calls.append(args)
            return next(statuses)

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            motion_resolver=resolver,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)
        before_position = (state['x'], state['y'], state['z'])

        runtime.update(.04, 1.0)
        first_speed = state['speed']
        first_position = (state['x'], state['y'], state['z'])
        runtime.update(.04, 1.04)

        self.assertEqual(before_position, first_position)
        self.assertEqual(4.0, first_speed)
        self.assertGreater(state['speed'], first_speed)
        self.assertGreater(state['z'], before_position[2])
        self.assertEqual(2, len(calls))
        self.assertNotIn('destructible_contact_speed', state)

    def test_probe_duration_totals_measure_queries_without_driving_work(self):
        clock_value = [0.0]

        def probe_clock():
            clock_value[0] += 0.001
            return clock_value[0]

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            direction_probe=lambda *unused: {'clear': True},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            physics_ground_probe=lambda *unused: 0.0,
            probe_clock=probe_clock)
        source = {'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                  'view_range': 500.0}
        target = {'id': 12, 'network_id': 12, 'kind': 'bot', 'team': 2,
                  'x': 100.0, 'y': 0.0, 'z': 0.0,
                  'position': (100.0, 0.0, 0.0), 'fire_seq': 0,
                  'speed': 0.0}
        before = runtime.probe_duration_totals()

        runtime._probe_direction((0.0, 0.0, 0.0), 0.0)
        self.assertTrue(runtime._visible(source, target, 1.0))
        self.assertTrue(runtime._shot_clear(source, target, 1.0))
        runtime._terrain_support({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'half_length': 3.0})

        after = runtime.probe_duration_totals()
        self.assertEqual(after, runtime.probe_duration_totals())
        elapsed = dict(zip(
            self.module.PROBE_KINDS,
            (after[index] - before[index]
             for index in range(len(after)))))
        for name in ('visibility', 'lane', 'ground', 'motion'):
            self.assertAlmostEqual(0.001, elapsed[name])
        self.assertEqual(0.0, elapsed['cover'])

    def test_bounded_probe_clock_starts_on_authority_tick_and_expires(self):
        clock_calls = [0]

        def probe_clock():
            clock_calls[0] += 1
            return clock_calls[0] * 0.0001

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            probe_clock=probe_clock, probe_timing_seconds=0.10)
        runtime.battle_start(self.start)

        self.assertEqual('pending', runtime.probe_timing_state())
        self.assertEqual(0, clock_calls[0])
        runtime.update(0.04, 1.0)
        self.assertEqual('active', runtime.probe_timing_state())
        self.assertGreater(clock_calls[0], 0)
        self.assertTrue(any(
            value > 0.0 for value in runtime.probe_duration_totals()))

        calls_before_expiry = clock_calls[0]
        runtime.update(0.12, 1.12)
        self.assertEqual('complete', runtime.probe_timing_state())
        self.assertEqual(calls_before_expiry, clock_calls[0])
        runtime.update(0.04, 1.16)
        self.assertEqual(calls_before_expiry, clock_calls[0])

    def test_disabling_probe_clock_preserves_wire_state_and_probe_sequence(self):
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Clock-A'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Clock-B'},
        ]

        def exercise(probe_clock):
            probes = []

            def direction(position, yaw, speed=0.0):
                probes.append(('motion', tuple(position), yaw, speed))
                return {'clear': True, 'collision': False,
                        'water': False, 'slope': 0.0}

            def visibility(source, target, fired_recently=False):
                probes.append(('visibility', source['id'],
                               target['network_id'], fired_recently))
                return True

            def firing_lane(source, target):
                probes.append(('lane', source['id'],
                               target['network_id']))
                return True

            def ground(x, z, hint):
                probes.append(('ground', x, z, hint))
                return 0.0

            runtime = self.module.BotRuntime(
                1, descriptor_resolver=lambda unused: _combat_descriptor(),
                adapter_factory=lambda *args: _Adapter(*args),
                direction_probe=direction,
                visibility_probe=visibility,
                firing_lane_probe=firing_lane,
                ground_probe=ground, physics_ground_probe=ground,
                spawn_resolver=_spawn_resolver, baked_graph=_graph(),
                probe_clock=probe_clock)
            wire = list(runtime.battle_start(
                dict(self.start, bots=roster)))
            for frame in range(20):
                wire.extend(runtime.update(
                    0.05, 1.0 + frame * 0.05, players=[]))
            return (wire, runtime.presentation_states(), probes,
                    runtime.probe_totals(),
                    runtime.probe_duration_totals())

        clock_value = [0.0]

        def clock():
            clock_value[0] += 0.0001
            return clock_value[0]

        untimed = exercise(None)
        timed = exercise(clock)

        self.assertEqual(untimed[:4], timed[:4])
        self.assertTrue(any(untimed[3]))
        self.assertEqual((0.0,) * len(self.module.PROBE_KINDS), untimed[4])
        self.assertTrue(any(value > 0.0 for value in timed[4]))

    def test_ground_support_samples_ends_only_when_centre_is_missing(self):
        calls = []

        def ground(unused_x, z, unused_y):
            calls.append(z)
            if abs(z) < 1e-9:
                return None
            return 2.0 if z > 0.0 else 1.0

        runtime = self.module.BotRuntime(
            1, physics_ground_probe=ground)

        self.assertEqual((2.0, None), runtime._terrain_support({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'half_length': 3.0}))
        self.assertEqual([0.0, 3.0, -3.0], calls)
        self.assertEqual(3, dict(zip(
            self.module.PROBE_KINDS, runtime.probe_totals()))['ground'])

    def test_vertical_motion_uses_centre_without_sampling_higher_ends(self):
        calls = []

        def ground(x, z, unused_y):
            calls.append((x, z))
            return 2.0 if abs(x) < 1e-9 and abs(z) < 1e-9 else 20.0

        runtime = self.module.BotRuntime(
            1, physics_ground_probe=ground)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 0.0, 'half_length': 3.0,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': False, 'last_drive_pitch': 0.0,
        }

        support_blocked = runtime._update_vertical_motion(state, 0.1)

        self.assertEqual([(0.0, 0.0)], calls)
        self.assertEqual(2.0, state['y'])
        self.assertFalse(state['airborne'])
        self.assertFalse(support_blocked)

    def test_grounded_bot_rejects_raised_centre_support_and_recovers(self):
        failures = []
        driver = types.SimpleNamespace(
            remember_failure=lambda *args: failures.append(args))
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: 1.4)
        runtime.adapter = types.SimpleNamespace(driver=driver)
        runtime._turn_speeds[11] = 0.5
        runtime._decision_cache[11] = object()
        runtime._motion_probe_cache[11] = object()
        state = {
            'id': 11, 'x': 2.0, 'y': 0.0, 'z': 3.0, 'yaw': 0.25,
            'speed': 4.0, 'half_length': 3.0,
            'movement_dir': 1, 'rotation_dir': 1,
            'push_x': 0.4, 'push_z': -0.3,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True, 'last_drive_pitch': 0.0,
            'destructible_contact_speed': 4.0,
        }

        support_blocked = runtime._update_vertical_motion(
            state, 0.1, (0.0, 0.0, 0.0), 0.75)

        self.assertTrue(support_blocked)
        self.assertEqual((0.0, 0.0, 0.0),
                         (state['x'], state['y'], state['z']))
        self.assertEqual((0.0, 0, 0, 0.0, 0.0), (
            state['speed'], state['movement_dir'], state['rotation_dir'],
            state['push_x'], state['push_z']))
        self.assertEqual(0.0, runtime._turn_speeds[11])
        self.assertFalse(state['airborne'])
        self.assertNotIn('destructible_contact_speed', state)
        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertEqual([(11, 0.75, 5.0)], failures)

    def test_vertical_motion_uses_edge_fallback_or_ballistic_fall(self):
        def edge_ground(unused_x, z, unused_y):
            if abs(z) < 1e-9:
                return None
            return 3.0 if z > 0.0 else 1.0

        runtime = self.module.BotRuntime(
            1, physics_ground_probe=edge_ground)
        supported = {
            'id': 11, 'x': 0.0, 'y': 8.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 0.0, 'half_length': 3.0,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': False, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(supported, 0.1)

        self.assertEqual(3.0, supported['y'])
        self.assertFalse(supported['airborne'])

        falling_runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: None)
        falling = dict(supported)
        falling.update(y=3.0, vertical_speed=0.0, airborne=False,
                       grounded_once=True)
        falling_runtime._update_vertical_motion(falling, 0.1)
        self.assertTrue(falling['airborne'])
        self.assertLess(falling['vertical_speed'], 0.0)
        self.assertLess(falling['y'], 3.0)

    def test_bot_landing_applies_the_shared_fall_damage_once(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: 0.0)
        runtime._descriptors[11] = _critical_descriptor()
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'speed': 0.0, 'half_length': 3.0,
            'health': 1000, 'max_health': 1000,
            'display_health': 1000, 'alive': True, 'critical': {},
            'vertical_speed': -20.0, 'airborne': True,
            'grounded_once': True, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(state, 0.1)

        self.assertEqual(700, state['health'])
        self.assertEqual(700, state['display_health'])
        self.assertTrue(state['alive'])
        self.assertEqual(0.0, state['vertical_speed'])
        self.assertFalse(state['airborne'])
        runtime._update_vertical_motion(state, 0.1)
        self.assertEqual(700, state['health'])

    def test_fatal_bot_landing_uses_world_collision_terminal_state(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: 0.0)
        runtime._descriptors[11] = _critical_descriptor()
        state = {
            'id': 11, 'x': 0.0, 'y': 0.2, 'z': 0.0,
            'speed': 3.0, 'half_length': 3.0,
            'health': 1000, 'max_health': 1000,
            'display_health': 1000, 'alive': True, 'critical': {},
            'movement_dir': 1, 'rotation_dir': -1,
            'push_x': 0.5, 'push_z': -0.5,
            'target_kind': 'human', 'target_id': 2,
            'combat_fire_timer': 1.0,
            'vertical_speed': -50.0, 'airborne': True,
            'grounded_once': True, 'last_drive_pitch': 0.0,
        }
        runtime._turn_speeds[11] = 0.4

        runtime._update_vertical_motion(state, 0.1)

        self.assertEqual((0, False, 0, 3), (
            state['health'], state['alive'], state['display_health'],
            state['death_reason']))
        self.assertEqual((0.0, 0, 0, 0.0, 0.0, 0.0), (
            state['speed'], state['movement_dir'], state['rotation_dir'],
            state['push_x'], state['push_z'], runtime._turn_speeds[11]))
        self.assertIsNone(state['target_kind'])
        self.assertIsNone(state['target_id'])
        self.assertIn('devices', state['critical'])

    def test_probe_clock_failure_never_changes_probe_result_or_call_count(self):
        calls = []

        def fail_clock():
            raise RuntimeError('diagnostic clock failed')

        runtime = self.module.BotRuntime(
            1,
            direction_probe=lambda *unused: calls.append(1) or {
                'clear': True},
            probe_clock=fail_clock)

        self.assertTrue(runtime._probe_is_clear(
            runtime._probe_direction((0.0, 0.0, 0.0), 0.0)))
        self.assertTrue(runtime._probe_is_clear(
            runtime._probe_direction((0.0, 0.0, 0.0), 0.0)))
        self.assertEqual([1, 1], calls)
        self.assertEqual((0.0,) * len(self.module.PROBE_KINDS),
                         runtime.probe_duration_totals())

        clock_calls = [0]

        def fail_on_finish():
            clock_calls[0] += 1
            if clock_calls[0] == 2:
                raise RuntimeError('diagnostic finish clock failed')
            return 1.0

        finish_calls = []
        runtime = self.module.BotRuntime(
            1,
            direction_probe=lambda *unused: finish_calls.append(1) or {
                'clear': True},
            probe_clock=fail_on_finish)
        self.assertTrue(runtime._probe_is_clear(
            runtime._probe_direction((0.0, 0.0, 0.0), 0.0)))
        self.assertEqual([1], finish_calls)
        self.assertEqual((0.0,) * len(self.module.PROBE_KINDS),
                         runtime.probe_duration_totals())

    def test_full_roster_keeps_support_and_budgets_visual_ground_samples(self):
        calls = []
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Ground-%d' % index}
            for index in range(29)
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *args: calls.append(args) or 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))

        runtime.update(0.04, 1.0)

        # Spawn tick: every Bot receives one centre-support query, while only
        # the bounded rotating cohort receives a four-point visual target.
        first_frame = 29 + 4 * self.module.MAX_SLOPE_POSE_SAMPLES_PER_FRAME
        self.assertEqual(first_frame, len(calls))
        self.assertEqual(first_frame, dict(zip(
            self.module.PROBE_KINDS, runtime.probe_totals()))['ground'])

        # The remaining visual targets rotate through the roster without ever
        # dropping the per-frame centre support sample.
        for frame in range(1, 6):
            before = len(calls)
            runtime.update(0.04, 1.0 + frame * 0.04)
            remaining = max(
                0, 29 - frame * self.module.MAX_SLOPE_POSE_SAMPLES_PER_FRAME)
            sampled = min(
                self.module.MAX_SLOPE_POSE_SAMPLES_PER_FRAME, remaining)
            self.assertEqual(29 + 4 * sampled, len(calls) - before)
        self.assertTrue(all(
            'pose_sample' in state for state in runtime.states.values()))

        # Stationary bots keep the sampled pose; only support remains.
        before = len(calls)
        runtime.update(0.04, 2.0)
        self.assertEqual(29, len(calls) - before)

    def test_traffic_without_forward_teammate_skips_coast_integral(self):
        source = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 8.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        behind = {
            'id': 22, 'team': 2,
            'position': (0.0, 0.0, -8.0), 'yaw': 0.0,
            'velocity': (0.0, 0.0, 8.0),
            'half_length': 3.5, 'half_width': 1.7,
        }
        original = self.module.BotRuntime._traffic_stopping_distance
        self.module.BotRuntime._traffic_stopping_distance = staticmethod(
            lambda *unused: (_ for _ in ()).throw(
                AssertionError('unused coast integral ran')))
        try:
            self.assertEqual((1.0, False), self.module.BotRuntime.
                             _traffic_throttle(source, {
                                 'throttle': 1.0,
                                 'target_yaw': 0.0,
                                 'turn': 0.0,
                             }, [behind]))
        finally:
            self.module.BotRuntime._traffic_stopping_distance = staticmethod(
                original)

    def test_traffic_stopping_cache_reuses_only_identical_inputs(self):
        runtime = self.module.BotRuntime(1)
        source = {
            'id': 21, 'speed': 8.0, 'last_drive_pitch': 0.1,
        }
        command = {'turn': 0.0}
        params = self.module.vehicle_physics.derive_params({})
        calls = []
        original = self.module.BotRuntime._traffic_stopping_distance

        def stopping(*args):
            calls.append(args)
            return 3.25

        self.module.BotRuntime._traffic_stopping_distance = staticmethod(
            stopping)
        try:
            self.assertEqual(3.25, runtime.
                             _cached_traffic_stopping_distance(
                                 source, command, params))
            self.assertEqual(3.25, runtime.
                             _cached_traffic_stopping_distance(
                                 source, command, params))
            self.assertEqual(1, len(calls))
            source['speed'] = 8.000001
            runtime._cached_traffic_stopping_distance(
                source, command, params)
            self.assertEqual(2, len(calls))
        finally:
            self.module.BotRuntime._traffic_stopping_distance = staticmethod(
                original)

    def test_traffic_lateral_bound_never_skips_a_possible_obb_hit(self):
        cases = (
            (1.7, 3.5, 1.7, 3.5),
            (2.5, 5.5, 1.1, 2.4),
            (0.8, 1.8, 2.8, 6.0),
        )
        for own_width, own_length, other_width, other_length in cases:
            lateral = (
                math.hypot(own_width, own_length) +
                math.hypot(other_width, other_length) + .001)
            self.assertTrue(self.module.BotRuntime.
                            _traffic_lateral_separated(
                                lateral, own_width, own_length,
                                other_width, other_length))
            for own_yaw in (-2.4, 0.0, 1.7):
                for other_yaw in (-1.1, .8, 2.9):
                    for forward in (-20.0, 0.0, 35.0):
                        self.assertEqual(float('inf'), self.module.BotRuntime.
                                         _traffic_obb_clearance(
                                             lateral, forward, 0.0,
                                             own_yaw, own_width, own_length,
                                             other_yaw, other_width,
                                             other_length))

    def test_friendly_crossing_traffic_brakes_both_inside_safe_clearance(self):
        lower = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        higher = {
            'id': 23, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 8.0,
            'yaw': math.pi, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        lower_command = {'throttle': 1.0, 'target_yaw': 0.0}
        higher_command = {'throttle': 1.0, 'target_yaw': math.pi}

        traffic_throttle = self.module.BotRuntime._traffic_throttle
        lower_throttle, lower_waiting = traffic_throttle(
            lower, lower_command, [dict(
                higher, position=(higher['x'], higher['y'], higher['z']),
                velocity=(0.0, 0.0, -5.0))])
        higher_throttle, higher_waiting = traffic_throttle(
            higher, higher_command, [dict(
                lower, position=(lower['x'], lower['y'], lower['z']),
                velocity=(0.0, 0.0, 5.0))])

        self.assertEqual((0.0, True),
                         (lower_throttle, lower_waiting))
        self.assertEqual((0.0, True),
                         (higher_throttle, higher_waiting))

    def test_friendly_crossing_traffic_keeps_right_of_way_at_safe_distance(self):
        lower = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        higher = {
            'id': 23, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 15.0,
            'yaw': math.pi, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        lower_command = {'throttle': 1.0, 'target_yaw': 0.0}
        higher_command = {'throttle': 1.0, 'target_yaw': math.pi}

        traffic_throttle = self.module.BotRuntime._traffic_throttle
        lower_throttle, lower_waiting = traffic_throttle(
            lower, lower_command, [dict(
                higher, position=(higher['x'], higher['y'], higher['z']),
                velocity=(0.0, 0.0, -5.0))])
        higher_throttle, higher_waiting = traffic_throttle(
            higher, higher_command, [dict(
                lower, position=(lower['x'], lower['y'], lower['z']),
                velocity=(0.0, 0.0, 5.0))])

        self.assertEqual((1.0, False),
                         (lower_throttle, lower_waiting))
        self.assertLess(higher_throttle, 1.0)
        self.assertFalse(higher_waiting)

    def test_crossing_nearfield_uses_copied_stopping_distance(self):
        vehicle_physics = self.module.vehicle_physics
        params = vehicle_physics.derive_params(_combat_descriptor())
        speed = 13.0
        stopping = self.module.BotRuntime._traffic_stopping_distance(
            speed, params)
        simulated = 0.0
        current = speed
        while current > self.module.TRAFFIC_DIRECTION_SPEED_EPSILON:
            current = vehicle_physics.longitudinal_step(
                params, current, 0.0, False, 0.0,
                self.module.PUBLICATION_SECONDS)
            simulated += max(0.0, current) * self.module.PUBLICATION_SECONDS
        self.assertAlmostEqual(simulated, stopping, places=9)

        source = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': speed,
            'half_length': 3.5, 'half_width': 1.7,
            'last_drive_pitch': 0.0,
        }
        command = {'throttle': 1.0, 'target_yaw': 0.0, 'turn': 0.0}
        edge_threshold = (
            self.module.TRAFFIC_STANDSTILL_CLEARANCE + stopping)

        def crossing(clearance):
            return {
                'id': 23, 'team': 2,
                'position': (0.0, 0.0, 7.0 + clearance),
                'yaw': math.pi, 'velocity': (0.0, 0.0, -speed),
                'half_length': 3.5, 'half_width': 1.7,
            }

        self.assertEqual((0.0, True), self.module.BotRuntime.
                         _traffic_throttle(
                             source, command,
                             [crossing(edge_threshold - 0.01)], params))
        self.assertEqual((1.0, False), self.module.BotRuntime.
                         _traffic_throttle(
                             source, command,
                             [crossing(edge_threshold + 0.01)], params))

    def test_stopped_crossing_keeps_hull_yaw_and_nearfield_gate(self):
        source = {
            'id': 10, 'team': 1,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 0.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        command = {
            'throttle': 1.0, 'target_yaw': 0.0, 'turn': 0.0,
        }

        def stopped_crossing(clearance):
            # Perpendicular projected forward support is its half-width.
            return {
                'id': 12, 'team': 1,
                'position': (0.0, 0.0, 3.5 + 1.7 + clearance),
                'yaw': math.pi * 0.5,
                'velocity': (0.0, 0.0, 0.0),
                'half_length': 3.5, 'half_width': 1.7,
            }

        self.assertEqual((0.0, False), self.module.BotRuntime.
                         _traffic_throttle(source, command, [
                             stopped_crossing(
                                 self.module.
                                 TRAFFIC_STANDSTILL_CLEARANCE - 0.01)]))
        self.assertEqual((1.0, False), self.module.BotRuntime.
                         _traffic_throttle(source, command, [
                             stopped_crossing(
                                 self.module.
                                 TRAFFIC_STANDSTILL_CLEARANCE + 0.01)]))

    def test_rotated_obb_corners_that_miss_the_corridor_do_not_block(self):
        source = {
            'id': 12, 'team': 1,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 0.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        other = {
            'id': 15, 'team': 1,
            'position': (5.3, 0.0, 6.0),
            'yaw': math.pi * 0.5,
            'velocity': (0.0, 0.0, 0.0),
            'half_length': 3.5, 'half_width': 1.7,
        }

        self.assertEqual(float('inf'), self.module.BotRuntime.
                         _traffic_obb_clearance(
                             5.3, 6.0, 0.0, 0.0, 1.7, 3.5,
                             math.pi * 0.5, 1.7, 3.5))
        self.assertEqual((1.0, False), self.module.BotRuntime.
                         _traffic_throttle(source, {
                             'throttle': 1.0,
                             'target_yaw': 0.0,
                             'turn': 0.0,
                         }, [other]))

    def test_lower_id_fast_bot_brakes_for_rotated_stopped_teammate(self):
        source = {
            'id': 23, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.641, 'speed': 13.163,
            'half_length': 3.5, 'half_width': 1.7,
        }
        forward = 11.0
        other = {
            'id': 30, 'team': 2,
            'position': (
                math.sin(source['yaw']) * forward, 0.0,
                math.cos(source['yaw']) * forward),
            'yaw': 2.582,
            'velocity': (-0.0010, 0.0, 0.0016),
            'half_length': 3.5, 'half_width': 1.7,
        }

        self.assertEqual((0.0, True), self.module.BotRuntime.
                         _traffic_throttle(source, {
                             'throttle': 1.0,
                             'target_yaw': source['yaw'],
                         }, [other]))

    def test_rotated_stopped_teammate_uses_projected_hull_width(self):
        source = {
            'id': 19, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 8.59,
            'half_length': 3.5, 'half_width': 1.7,
        }
        other = {
            'id': 30, 'team': 2,
            # The centre is outside the parallel-width corridor.  Its long
            # hull is nearly perpendicular, however, and occupies that lane
            # inside the follower's copied stopping clearance.
            'position': (4.5, 0.0, 9.0),
            'yaw': math.pi * 0.5,
            'velocity': (0.0, 0.0, 0.0),
            'half_length': 5.0, 'half_width': 1.5,
        }

        self.assertEqual((0.0, True), self.module.BotRuntime.
                         _traffic_throttle(source, {
                             'throttle': 1.0, 'target_yaw': 0.0,
                         }, [other]))

    def test_high_speed_headway_is_not_clipped_at_nine_metres(self):
        source = {
            'id': 23, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 13.163,
            'half_length': 3.5, 'half_width': 1.7,
        }
        # Twelve metres edge-to-edge is outside the former fixed 9 m scan,
        # but inside the established standstill gap + one-second headway.
        other = {
            'id': 30, 'team': 2,
            'position': (0.0, 0.0, 19.0), 'yaw': 0.0,
            'velocity': (0.0, 0.0, 0.0),
            'half_length': 3.5, 'half_width': 1.7,
        }

        self.assertEqual((0.0, True), self.module.BotRuntime.
                         _traffic_throttle(source, {
                             'throttle': 1.0, 'target_yaw': 0.0,
                         }, [other]))

    def test_malinovka_fast_follower_stops_before_rotated_teammate(self):
        vehicle_physics = self.module.vehicle_physics
        params = vehicle_physics.derive_params({})
        yaw = 0.641
        source = {
            'id': 23, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': yaw, 'speed': 13.163,
            'half_length': 3.5, 'half_width': 1.7,
            'last_drive_pitch': 0.0,
        }
        leader_forward = 21.5
        other = {
            'id': 30, 'team': 2,
            'position': (
                math.sin(yaw) * leader_forward, 0.0,
                math.cos(yaw) * leader_forward),
            'yaw': 2.582,
            'velocity': (-0.0010, 0.0, 0.0016),
            'half_length': 3.5, 'half_width': 1.7,
        }
        command = {'throttle': 1.0, 'target_yaw': yaw, 'turn': 0.0}
        minimum_clearance = leader_forward - 7.0

        # Twenty-four seconds is long enough to prove this converges to the
        # stopped queue instead of merely delaying the same collision.
        for unused_frame in range(600):
            throttle, unused_waiting = self.module.BotRuntime.\
                _traffic_throttle(source, command, [other], params)
            source['speed'] = vehicle_physics.longitudinal_step(
                params, source['speed'], throttle, False, 0.0, 0.04)
            source['x'] += math.sin(yaw) * source['speed'] * 0.04
            source['z'] += math.cos(yaw) * source['speed'] * 0.04
            travelled = (source['x'] * math.sin(yaw) +
                         source['z'] * math.cos(yaw))
            minimum_clearance = min(
                minimum_clearance, leader_forward - travelled - 7.0)

        self.assertGreater(minimum_clearance, 0.0)
        self.assertAlmostEqual(0.0, source['speed'], delta=0.05)

    def test_opening_queue_runtime_never_reaches_stopped_teammate(self):
        yaw = 0.641

        class TrafficAdapter(_Adapter):
            def decide(adapter_self, state, clear):
                adapter_self.calls.append((state, clear(state['yaw'])))
                moving = state['id'] == 23
                heading = yaw if moving else state['yaw']
                position = state['position']
                aim = (
                    position[0] + math.sin(heading) * 100.0,
                    position[1],
                    position[2] + math.cos(heading) * 100.0)
                return {
                    'target_yaw': heading,
                    'throttle': 1.0 if moving else 0.0,
                    'turn': 0.0, 'shell_index': 0,
                    'fire_allowed': False, 'target_id': None,
                    'fire_range': 0.0, 'combat_mode': 'route',
                    'aim_position': aim, 'face_position': aim,
                    'move_position': aim,
                    'recovery_mode': 'drive' if moving else 'arrived',
                    'movement_intent': moving,
                }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: TrafficAdapter(),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 23, 'team': 2, 'slot': 0, 'name': 'T-43'},
            {'id': 30, 'team': 2, 'slot': 1, 'name': 'Lorraine'},
        ]))
        leader_forward = 21.5
        runtime.states[23].update(
            x=0.0, y=0.0, z=0.0, yaw=yaw, speed=13.163,
            half_length=3.5, half_width=1.7)
        runtime.states[30].update(
            x=math.sin(yaw) * leader_forward, y=0.0,
            z=math.cos(yaw) * leader_forward,
            yaw=2.582, speed=-0.001887,
            half_length=3.5, half_width=1.7)
        minimum_clearance = leader_forward - 7.0

        for frame in range(300):
            runtime.update(0.04, 1.0 + frame * 0.04)
            follower = runtime.states[23]
            leader = runtime.states[30]
            dx = leader['x'] - follower['x']
            dz = leader['z'] - follower['z']
            clearance = dx * math.sin(yaw) + dz * math.cos(yaw) - 7.0
            minimum_clearance = min(minimum_clearance, clearance)

        self.assertGreater(minimum_clearance, 0.0)
        self.assertTrue(runtime.states[23]['alive'])
        self.assertTrue(runtime.states[30]['alive'])
        self.assertEqual([], runtime._pending_ram_reports)

    def test_traffic_yield_is_friendly_only_and_humans_have_priority(self):
        source = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 5.0,
            'half_length': 3.5, 'half_width': 1.7,
        }
        command = {'throttle': 1.0, 'target_yaw': 0.0}
        traffic = {
            'position': (0.0, 0.0, 8.0), 'yaw': 0.0,
            'velocity': (0.0, 0.0, 0.0),
            'half_length': 3.5, 'half_width': 1.7,
        }

        traffic_throttle = self.module.BotRuntime._traffic_throttle
        self.assertEqual((1.0, False), traffic_throttle(
            source, command, [dict(traffic, id=22, team=1)]))
        self.assertEqual((0.0, True), traffic_throttle(
            source, command, [dict(
                traffic, id=self.module.HUMAN_TARGET_ID_BASE + 1,
                team=2)]))

    def test_dense_spawn_following_has_no_full_coast_speed_pulse(self):
        vehicle_physics = self.module.vehicle_physics
        params = vehicle_physics.derive_params({})
        source = {
            'id': 21, 'team': 2,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 0.0,
            'half_length': 3.5, 'half_width': 1.7,
            'last_drive_pitch': 0.0,
        }
        # Karelia's closest same-lane spawn rows start with 4.31 m of
        # edge-to-edge clearance.  Give the leader a materially weaker drive
        # so this also exercises velocity matching, not just equal motion.
        leader_z = 11.31
        leader_speed = 0.0
        follower_z = 0.0
        follower_speed = 0.0
        throttle = 1.0
        throttle_samples = []
        speed_steps = []
        clearances = []
        command = {'throttle': 1.0, 'target_yaw': 0.0, 'turn': 0.0}
        frame_step = 0.04
        for frame in range(500):
            clearance = leader_z - follower_z - 7.0
            if frame % 3 == 0:
                source['z'] = follower_z
                source['speed'] = follower_speed
                throttle, unused_waiting = self.module.BotRuntime.\
                    _traffic_throttle(source, command, [{
                        'id': 22, 'team': 2,
                        'position': (0.0, 0.0, leader_z),
                        'yaw': 0.0,
                        'velocity': (0.0, 0.0, leader_speed),
                        'half_length': 3.5, 'half_width': 1.7,
                    }], params)
            previous_speed = follower_speed
            leader_speed = vehicle_physics.longitudinal_step(
                params, leader_speed, 0.7, False, 0.0, frame_step)
            follower_speed = vehicle_physics.longitudinal_step(
                params, follower_speed, throttle, False, 0.0, frame_step)
            leader_z += leader_speed * frame_step
            follower_z += follower_speed * frame_step
            throttle_samples.append(throttle)
            speed_steps.append(follower_speed - previous_speed)
            clearances.append(clearance)

        self.assertGreater(min(clearances), 4.0)
        self.assertGreater(min(throttle_samples), 0.1)
        self.assertGreater(min(speed_steps), -0.05)
        self.assertAlmostEqual(leader_speed, follower_speed, delta=0.05)

    def test_traffic_wait_does_not_enter_reverse_recovery(self):
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        driver = LocalDriver()
        driver_state = driver._state(11, (0.0, 0.0, 0.0))
        driver_state['stuck_time'] = 10.0
        driver_state['recovery_time'] = 0.5

        self.assertTrue(driver.wait_for_traffic(11))
        self.assertEqual((0.0, 0.0), (
            driver_state['stuck_time'], driver_state['recovery_time']))

    def test_authority_freezes_manifest_until_transport_enqueue(self):
        resolved = []
        resolver = self.runtime.descriptor_resolver
        self.runtime.descriptor_resolver = lambda vehicle: (
            resolved.append(vehicle) or resolver(vehicle))
        first = self.runtime.battle_start(self.start)
        self.assertEqual('bot_manifest', first[0]['type'])
        self.assertEqual(11, first[0]['bots'][0]['id'])
        self.assertFalse(self.runtime._manifest_sent)
        self.assertEqual(['ussr:R11_MS-1'], resolved)

        first[0]['bots'][0]['name'] = 'mutated caller copy'
        repeated = self.runtime.battle_start(dict(
            self.start, bots=[dict(
                self.start['bots'][0], name='rebuilt roster')]))

        self.assertEqual('Bot', repeated[0]['bots'][0]['name'])
        self.assertEqual(['ussr:R11_MS-1'], resolved)
        self.assertFalse(self.runtime.mark_manifest_enqueued({
            'type': 'bot_manifest', 'bots': []}))
        self.assertTrue(
            self.runtime.mark_manifest_enqueued(repeated[0]))
        self.assertTrue(self.runtime._manifest_sent)
        self.assertIsNone(self.runtime.pending_manifest())
        self.assertEqual([], self.runtime.battle_start(self.start))

    def test_pending_manifest_does_not_cross_authority_or_round(self):
        first = self.runtime.battle_start(self.start)[0]

        self.assertEqual([], self.runtime.battle_start(dict(
            self.start, bot_authority_id=2)))
        self.assertIsNone(self.runtime.pending_manifest())
        self.assertFalse(self.runtime.mark_manifest_enqueued(first))

        resumed = self.runtime.battle_start(self.start)[0]
        next_round = dict(
            self.start, round_id=6,
            bots=[{'id': 12, 'team': 1, 'slot': 0, 'name': 'Next'}])
        current = self.runtime.battle_start(next_round)[0]

        self.assertFalse(self.runtime.mark_manifest_enqueued(resumed))
        self.assertEqual([12], [row['id'] for row in current['bots']])
        self.assertEqual(current, self.runtime.pending_manifest())

    def test_manifest_descriptor_preflight_failure_is_atomic(self):
        for failure_kind in ('resolver', 'siege_pair'):
            good = _combat_descriptor()
            broken = _combat_descriptor()
            broken.hasSiegeMode = True
            resolved = []

            def resolver(vehicle_name):
                resolved.append(vehicle_name)
                if vehicle_name == 'broken:Vehicle':
                    if failure_kind == 'resolver':
                        raise ValueError('descriptor parse failed')
                    return broken
                return good

            runtime = self.module.BotRuntime(
                1, descriptor_resolver=resolver,
                adapter_factory=lambda *args, **kwargs: _Adapter(*args),
                baked_graph=_graph())
            start = dict(self.start, bots=[
                {'id': 11, 'team': 2, 'slot': 0, 'name': 'Good',
                 'vehicle': 'good:Vehicle'},
                {'id': 12, 'team': 2, 'slot': 1, 'name': 'Broken',
                 'vehicle': 'broken:Vehicle'},
            ])

            with self.assertRaisesRegex(
                    RuntimeError,
                    'bot 12 vehicle broken:Vehicle descriptor is '
                    'unavailable'):
                runtime.battle_start(start)

            self.assertEqual(['good:Vehicle', 'broken:Vehicle'], resolved)
            self.assertEqual({}, runtime.states)
            self.assertEqual({}, runtime._descriptor_pairs)
            self.assertEqual({}, runtime._descriptors)
            self.assertEqual({}, runtime._gun_states)
            self.assertEqual({}, runtime._ammo_states)
            self.assertEqual({}, runtime._physics_params)
            self.assertFalse(runtime._manifest_sent)
            self.assertIsNone(runtime.adapter)

    def test_worker_donates_sorted_client_effective_collision_profiles(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        self.runtime.descriptor_resolver = lambda unused: descriptor
        start = dict(self.start, human_ram_timeline=True, players=[
            {'id': 2, 'vehicle': 'ussr:R11_MS-1',
             'effective_params': _effective_params_snapshot(
                 mass=48000.0, ramming_bonus=0.10)},
            {'id': self.module.lan_client.WORKER_AUTHORITY_ID,
             'vehicle': 'germany:G54_E-50'},
            {'id': 1, 'vehicle': 'ussr:R11_MS-1',
             'effective_params': _effective_params_snapshot(
                 mass=51000.0, ramming_bonus=0.15)},
        ])

        outgoing = self.runtime.battle_start(start)

        profiles = outgoing[0]['player_collision_profiles']
        self.assertEqual([1, 2], [profile['id'] for profile in profiles])
        self.assertEqual(
            ['ussr:R11_MS-1', 'ussr:R11_MS-1'],
            [profile['vehicle'] for profile in profiles])
        self.assertEqual([51000.0, 48000.0],
                         [profile['mass'] for profile in profiles])
        self.assertEqual(
            [[1.5, 3.5, -0.8, 2.0], [1.5, 3.5, -0.8, 2.0]],
            [profile['shape'] for profile in profiles])
        self.assertEqual([0.15, 0.10], [
            profile['ram_profile']['ramming_bonus']
            for profile in profiles])
        self.assertTrue(self.runtime.mark_manifest_enqueued(outgoing[0]))
        self.assertEqual([], self.runtime.battle_start(start))

    def test_player_profiles_use_client_mass_ramming_and_camouflage(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 9000.0
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor)
        first = _admit_player({
            'id': 1, 'vehicle': 'ussr:R11_MS-1'},
            mass=51000.0, base_moving=0.31, base_still=0.42,
            shot_factor=0.17, ramming_bonus=0.15,
            spall_coefficient=1.4)
        second = _admit_player({
            'id': 2, 'vehicle': 'ussr:R11_MS-1'},
            mass=27000.0, base_moving=0.07, base_still=0.11,
            shot_factor=0.33, ramming_bonus=0.04,
            spall_coefficient=1.1)

        first_collision = runtime._player_collision_profile(first)
        second_collision = runtime._player_collision_profile(second)
        first_spotting = runtime._player_vehicle_profile(first)['spotting']
        second_spotting = runtime._player_vehicle_profile(second)['spotting']

        self.assertEqual(51000.0, first_collision['mass'])
        self.assertEqual(
            {'spall_coefficient': 1.4, 'ramming_bonus': 0.15},
            first_collision['ram_profile'])
        self.assertEqual(27000.0, second_collision['mass'])
        self.assertEqual(
            {'spall_coefficient': 1.1, 'ramming_bonus': 0.04},
            second_collision['ram_profile'])
        self.assertEqual(((0.31, 0.42), 0.17), first_spotting[:2])
        self.assertEqual(((0.07, 0.11), 0.33), second_spotting[:2])

    def test_player_commander_loss_uses_projected_native_vision_state(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(
            1, player_descriptor_resolver=lambda unused: descriptor)
        params = _effective_params_snapshot()
        params['crew']['dynamic_spotting']['states']['1:0']['vision'] = 0.5
        source = {
            'id': 1, 'network_id': 1, 'kind': 'human',
            'vehicle': 'ussr:R11_MS-1', 'speed': 1.0,
            'effective_params': params,
            'critical': _critical_payload(),
        }

        self.assertEqual(445.0, runtime._source_view_range(source, 1.0))
        source['critical'] = _critical_payload(crew_ko=['commander'])
        self.assertEqual(222.5, runtime._source_view_range(source, 1.0))

    def test_player_crew_state_selects_matching_camouflage_projection(self):
        runtime = self.module.BotRuntime(
            1, player_descriptor_resolver=lambda unused: _combat_descriptor())
        params = _effective_params_snapshot(
            base_moving=0.17, base_still=0.23)
        knocked_out = params['crew']['dynamic_spotting']['states']['1:0']
        knocked_out.update({
            'camouflage': 0.5,
            'base_moving': 0.41,
            'base_still': 0.52,
            'invisibility_moving': [0.2, 0.8],
            'invisibility_still': [0.3, 0.7],
        })
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'network_id': 1, 'kind': 'human',
            'vehicle': 'ussr:R11_MS-1', 'effective_params': params,
            'critical': _critical_payload(),
        }

        healthy = runtime._spotting_profile(target)
        target['critical'] = _critical_payload(crew_ko=['commander'])
        injured = runtime._spotting_profile(target)

        self.assertEqual((0.17, 0.23), healthy[0])
        self.assertEqual((0.41, 0.52), injured[0])
        self.assertEqual((0.2, 0.8),
                         injured[2]['invisibility_moving'])
        self.assertEqual((0.3, 0.7),
                         injured[2]['invisibility_still'])
        self.assertEqual(0.285, injured[2]['camouflage_factor'])

    def test_player_spotting_rejects_missing_effective_params(self):
        runtime = self.module.BotRuntime(1)
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'kind': 'human', 'network_id': 1,
            'vehicle': 'ussr:R11_MS-1',
            'position': (0.0, 0.0, 100.0),
            'speed': 0.0, 'fire_seq': 0,
        }

        with self.assertRaisesRegex(
                ValueError, 'effective parameters are missing or invalid'):
            runtime._visible(source, target, 1.0)

    def test_player_collision_manifest_rejects_missing_effective_params(self):
        start = dict(self.start, human_ram_timeline=True, players=[
            {'id': 1, 'vehicle': 'ussr:R11_MS-1'},
        ])

        with self.assertRaisesRegex(
                ValueError, 'effective parameters are missing or invalid'):
            self.runtime.battle_start(start)

    def test_player_collision_manifest_rejects_missing_descriptor(self):
        self.runtime.player_descriptor_resolver = lambda unused: None
        start = dict(self.start, human_ram_timeline=True, players=[
            _admit_player({'id': 1, 'vehicle': 'ussr:R11_MS-1'}),
        ])

        with self.assertRaisesRegex(
                ValueError, 'collision manifest descriptor is unavailable'):
            self.runtime.battle_start(start)

    def test_injected_baked_graph_replaces_runtime_grid_and_passes_routes(self):
        graph = _graph()
        seen = []
        def factory(*args, **kwargs):
            seen.append((args, kwargs))
            return _Adapter(*args)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=factory, baked_graph=graph,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0})
        runtime.battle_start(self.start)
        self.assertTrue(runtime.navigator.grid.prebaked)
        self.assertEqual(graph['routes'], seen[0][1]['baked_routes'])

    def test_supported_map_with_empty_baked_routes_fails_closed(self):
        graph = _graph()
        graph['routes'] = {'1': (), '2': ()}
        seen = []

        def factory(*args, **kwargs):
            seen.append((args, kwargs))
            return _Adapter(*args)

        runtime = self.module.BotRuntime(
            1, adapter_factory=factory, baked_graph=graph,
            ground_probe=lambda *unused: 0.0)
        with self.assertRaisesRegex(
                ValueError, 'navigation graph routes are missing'):
            runtime.battle_start(self.start)

        self.assertEqual([], seen)
        self.assertIsNone(runtime.baked_graph)

    def test_unknown_developer_map_is_rejected_without_runtime_navigation(self):
        seen = []

        def factory(*args, **kwargs):
            seen.append((args, kwargs))
            return _Adapter(*args)

        runtime = self.module.BotRuntime(
            1, adapter_factory=factory,
            ground_probe=lambda unused_x, unused_z, unused_hint: 0.0)
        start = dict(self.start, map='dev_test_map')

        with self.assertRaisesRegex(ValueError, 'map is not supported'):
            runtime.battle_start(start)

        self.assertEqual([], seen)
        self.assertIsNone(runtime.baked_graph)

    def test_supported_map_without_graph_fails_closed(self):
        original = self.module.prebaked_navigation.load_graph
        self.module.prebaked_navigation.load_graph = lambda unused: None
        try:
            runtime = self.module.BotRuntime(
                1, adapter_factory=lambda *args: _Adapter(*args))
            with self.assertRaisesRegex(
                    ValueError, 'required navigation graph is missing'):
                runtime.battle_start(self.start)
        finally:
            self.module.prebaked_navigation.load_graph = original

    def test_new_map_replaces_previous_navigation_graph(self):
        first_graph = _graph('01_karelia')
        second_graph = _graph('02_malinovka')
        loaded = []
        original = self.module.prebaked_navigation.load_graph
        self.module.prebaked_navigation.load_graph = lambda name: (
            loaded.append(name) or second_graph)
        try:
            runtime = self.module.BotRuntime(
                1, descriptor_resolver=lambda unused: _combat_descriptor(),
                adapter_factory=lambda *args, **unused: _Adapter(*args),
                baked_graph=first_graph,
                ground_probe=lambda *unused: 0.0,
                physics_ground_probe=lambda *unused: 0.0,
                spawn_resolver=_spawn_resolver)
            runtime.battle_start(self.start)
            runtime.battle_start(dict(
                self.start, round_id=6, map='02_malinovka'))
        finally:
            self.module.prebaked_navigation.load_graph = original

        self.assertEqual(['02_malinovka'], loaded)
        self.assertIs(second_graph, runtime.baked_graph)
        self.assertEqual('02_malinovka', runtime._navigation_map_name)

    def test_render_frames_bank_pose_until_the_30hz_authority_tick(self):
        self.runtime.descriptor_resolver = lambda unused: _combat_descriptor(
            reload_time=0.45, clip=(1,))
        self.runtime.battle_start(self.start)
        first = self.runtime.update(.02, 1.0)
        first_pose = self.runtime.presentation_states()[0]
        second = self.runtime.update(.02, 1.02, players=[{
            'id': 2, 'team': 1, 'alive': True,
            'x': 5, 'y': 0, 'z': 5,
            'effective_params': _effective_params_snapshot()}])
        second_pose = self.runtime.presentation_states()[0]
        self.assertEqual('bot_state', first[0]['type'])
        self.assertEqual([], second)
        self.assertEqual(first_pose['z'], second_pose['z'])
        self.assertEqual(0, second_pose['fire_seq'])
        player = [{'id': 2, 'team': 1, 'alive': True,
                   'x': 5, 'y': 0, 'z': 5}]
        player = [_admit_player(value) for value in player]
        self.runtime.update(.20, 1.22, players=player)
        self.runtime.update(.20, 1.42, players=player)
        result = self.runtime.update(.04, 1.46, players=player)
        bot = result[0]['bots'][0]
        self.assertEqual('bot_state', result[0]['type'])
        self.assertGreater(bot['z'], 0.0); self.assertEqual(1, bot['fire_seq'])
        self.assertEqual(0, bot['shell_index'])

    def test_publication_sample_clock_tracks_integrated_time_not_callback_time(self):
        self.runtime.battle_start(self.start)

        first = next(message for message in self.runtime.update(.04, 10.0)
                     if message.get('type') == 'bot_state')
        self.assertEqual(40000, first['sample_time_us'])

        # This render callback is banked until the next authority deadline.
        self.assertEqual([], self.runtime.update(.02, 10.02))
        second = next(message for message in self.runtime.update(.02, 10.04)
                      if message.get('type') == 'bot_state')
        self.assertEqual(80000, second['sample_time_us'])

        # A slow callback is divided into stable integration slices, but the
        # complete elapsed interval is consumed before update returns.
        stalled = [
            message for message in self.runtime.update(.25, 10.29)
            if message.get('type') == 'bot_state']
        self.assertEqual(
            [280000, 330000],
            [message['sample_time_us'] for message in stalled])
        self.assertEqual(
            [330000, 330000],
            [message['source_batch_horizon_us'] for message in stalled])
        self.assertEqual([], self.runtime.update(0.0, 10.33))

        self.runtime.battle_start(dict(self.start, round_id=6))
        reset = next(message for message in self.runtime.update(.03, 20.0)
                     if message.get('type') == 'bot_state')
        self.assertEqual(30000, reset['sample_time_us'])

    def test_one_second_callback_advances_all_time_and_preserves_events(self):
        self.runtime.battle_start(self.start)
        self.runtime._pending_ram_reports = [{
            'type': 'ram_damage', 'event': 'once'}]

        outgoing = self.runtime.update(1.0, 10.0)
        states = [message for message in outgoing
                  if message.get('type') == 'bot_state']
        events = [message for message in outgoing
                  if message.get('type') == 'ram_damage']

        self.assertEqual(
            [200000, 400000, 600000, 800000, 1000000],
            [message['sample_time_us'] for message in states])
        self.assertEqual(
            [1000000] * 5,
            [message['source_batch_horizon_us'] for message in states])
        self.assertEqual([{'type': 'ram_damage', 'event': 'once'}], events)
        self.assertEqual(0.0, self.runtime._accumulator)

    def test_worker_stall_refreshes_control_once_and_consumes_all_elapsed(self):
        adapter = _Adapter()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused: adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': .2},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.battle_start(self.start)
        runtime._pending_ram_reports = [{
            'type': 'ram_damage', 'event': 'once'}]
        runtime._world_receipt_waiting = [(11, True)]
        steps = []
        original_update_once = runtime._update_once

        def counted_update_once(*args):
            steps.append((
                args[0],
                getattr(runtime, '_refresh_control_this_step', True)))
            return original_update_once(*args)

        runtime._update_once = counted_update_once

        self.assertEqual([], runtime.update(0.01, 9.01))
        self.assertEqual([(11, True)], runtime._world_receipt_waiting)

        outgoing = runtime.update(0.99, 10.0)
        states = [message for message in outgoing
                  if message.get('type') == 'bot_state']
        events = [message for message in outgoing
                  if message.get('type') == 'ram_damage']

        self.assertEqual(1000000, states[-1]['sample_time_us'])
        self.assertEqual(
            [1000000] * len(states),
            [message['source_batch_horizon_us'] for message in states])
        self.assertEqual([{'type': 'ram_damage', 'event': 'once'}], events)
        self.assertAlmostEqual(1.0, sum(step for step, unused in steps))
        self.assertTrue(all(
            step <= self.module.MAX_CONTROL_ELAPSED_SECONDS + 1.0e-9
            for step, unused in steps))
        self.assertEqual(
            [True] + [False] * (len(steps) - 1),
            [refresh for unused, refresh in steps])
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(1000000, runtime._sample_time_us)
        self.assertAlmostEqual(0.0, runtime._accumulator)

    def test_worker_intermediate_ram_report_forces_state_barrier(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.battle_start(self.start)
        contact_slices = []

        def contact(unused_players, unused_now, step):
            contact_slices.append(step)
            if len(contact_slices) == 3:
                return [{
                    'type': 'bot_ram', 'bot_id': 11,
                    'target_kind': 'bot', 'target_id': 12,
                    'ram_seq': 1, 'damage_to_bot': 1,
                    'damage_to_target': 1,
                }]
            return []

        runtime._resolve_tank_contacts = contact
        outgoing = runtime.update(1.0, 1.0)
        event_index = next(
            index for index, message in enumerate(outgoing)
            if message.get('type') == 'bot_ram')

        self.assertEqual(5, len(contact_slices))
        self.assertEqual('bot_state', outgoing[event_index - 1]['type'])
        self.assertEqual(
            600000, outgoing[event_index - 1]['sample_time_us'])
        self.assertEqual([200000, 600000, 1000000], [
            message['sample_time_us'] for message in outgoing
            if message.get('type') == 'bot_state'])
        self.assertEqual(1000000, runtime._sample_time_us)
        self.assertAlmostEqual(0.0, runtime._accumulator)

    def test_worker_sustained_five_fps_consumes_elapsed_without_debt(self):
        runtime = self.module.BotRuntime(
            1, control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.authority_id = 1
        runtime.adapter = object()
        runtime._decision_cache[11] = ('last-command',)
        steps = []
        runtime._update_once = lambda step, *unused: steps.append(step) or []

        for frame in range(5):
            runtime.update(0.2, 1.0 + frame * 0.2)

        self.assertEqual([0.2] * 5, steps)
        self.assertAlmostEqual(0.0, runtime._accumulator)
        self.assertEqual(('last-command',), runtime._decision_cache[11])

    def test_worker_subthreshold_callbacks_keep_bounded_control_cadence(self):
        runtime = self.module.BotRuntime(
            1, control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.authority_id = 1
        runtime.adapter = object()
        steps = []
        runtime._update_once = lambda step, *unused: steps.append(step) or []

        runtime.update(0.06, 1.00)
        runtime.update(0.06, 1.06)
        runtime.update(0.08, 1.14)
        runtime.update(0.02, 1.16)

        self.assertEqual([0.12, 0.10], steps)
        self.assertAlmostEqual(0.0, runtime._accumulator)

    def test_worker_fixed_control_tracks_wall_time_from_five_to_one_fps(self):
        wall_seconds = 2
        for fps in (5, 4, 2, 1):
            with self.subTest(fps=fps):
                runtime = self.module.BotRuntime(
                    1, control_seconds=self.module.WORKER_CONTROL_SECONDS)
                runtime.authority_id = 1
                runtime.adapter = object()
                callback_calls = []
                current_callback = [None]

                def simulate(step, unused_now, unused_players,
                             unused_neighbours):
                    callback_calls[current_callback[0]].append((
                        step,
                        getattr(runtime, '_refresh_control_this_step', True)))
                    duration_us = max(
                        1, int(round(float(step) * 1000000.0)))
                    runtime._sample_time_us += duration_us
                    return []

                runtime._update_once = simulate
                frame_seconds = 1.0 / float(fps)
                frame_count = wall_seconds * fps
                accumulator_us = []
                for frame in range(frame_count):
                    current_callback[0] = frame
                    callback_calls.append([])
                    runtime.update(
                        frame_seconds, (frame + 1) * frame_seconds)
                    accumulator_us.append(int(round(
                        runtime._accumulator * 1000000.0)))

                callback_elapsed_us = [
                    int(round(sum(step for step, unused in calls) *
                              1000000.0))
                    for calls in callback_calls]
                refresh_counts = [sum(
                    1 for unused, refresh in calls if refresh)
                    for calls in callback_calls]
                max_step_us = max(
                    int(round(step * 1000000.0))
                    for calls in callback_calls for step, unused in calls)
                self.assertEqual({
                    'accumulator_us': [0] * frame_count,
                    'sample_time_us': wall_seconds * 1000000,
                    'callback_elapsed_us': [
                        int(round(frame_seconds * 1000000.0))
                    ] * frame_count,
                    'refresh_counts': [1] * frame_count,
                    'refresh_ordered': True,
                    'step_bound_held': True,
                }, {
                    'accumulator_us': accumulator_us,
                    'sample_time_us': runtime._sample_time_us,
                    'callback_elapsed_us': callback_elapsed_us,
                    'refresh_counts': refresh_counts,
                    'refresh_ordered': all(
                        calls and calls[0][1] and
                        not any(refresh for unused, refresh in calls[1:])
                        for calls in callback_calls),
                    'step_bound_held': max_step_us <= int(round(
                        self.module.MAX_CONTROL_ELAPSED_SECONDS *
                        1000000.0)),
                })

    def test_worker_low_fps_reuses_valid_drive_and_moves_continuously(self):
        command = self._stationary_command()
        command.update({
            'throttle': 1.0, 'combat_mode': 'route',
            'move_position': (0.0, 0.0, 100.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        })
        adapter = _FixedAdapter(command)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.battle_start(self.start)
        runtime.navigator = None
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)
        original_decision_seconds = self.module.DECISION_SECONDS
        original_pose_safe = self.module.prebaked_navigation.pose_is_safe
        self.module.DECISION_SECONDS = 2.0
        self.module.prebaked_navigation.pose_is_safe = (
            lambda *unused, **unused_kwargs: True)
        positions = []
        try:
            for frame in range(5):
                runtime.update(0.2, 1.0 + frame * 0.2)
                positions.append(state['z'])
                self.assertEqual(1, state['movement_dir'])
        finally:
            self.module.DECISION_SECONDS = original_decision_seconds
            self.module.prebaked_navigation.pose_is_safe = original_pose_safe

        self.assertEqual(sorted(positions), positions)
        self.assertTrue(all(second > first for first, second in
                            zip(positions, positions[1:])))
        self.assertEqual(1000000, runtime._sample_time_us)
        self.assertAlmostEqual(0.0, runtime._accumulator)
        self.assertEqual(1, len(adapter.calls))

    def test_worker_one_hz_holds_one_drive_plan_through_bounded_slices(self):
        command = self._stationary_command()
        command.update({
            'throttle': 1.0, 'combat_mode': 'route',
            'move_position': (0.0, 0.0, 100.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        })
        adapter = _FixedAdapter(command)
        direction_calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: direction_calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.battle_start(self.start)
        runtime.navigator = None
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)
        original_pose_safe = self.module.prebaked_navigation.pose_is_safe
        self.module.prebaked_navigation.pose_is_safe = (
            lambda *unused, **unused_kwargs: True)
        try:
            outgoing = runtime.update(1.0, 1.0)
        finally:
            self.module.prebaked_navigation.pose_is_safe = original_pose_safe

        states = [message for message in outgoing
                  if message.get('type') == 'bot_state']
        self.assertGreater(state['z'], 4.0)
        self.assertEqual(1, state['movement_dir'])
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(2, len(direction_calls))
        self.assertEqual(1000000, runtime._sample_time_us)
        self.assertAlmostEqual(0.0, runtime._accumulator)
        self.assertEqual([200000, 1000000], [
            message['sample_time_us'] for message in states])
        self.assertEqual([1000000, 1000000], [
            message['source_batch_horizon_us'] for message in states])

    def test_worker_one_hz_full_roster_refreshes_planning_once(self):
        command = self._stationary_command()
        command.update({
            'throttle': 1.0, 'combat_mode': 'route',
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        })
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Slow-worker-%02d' % index}
            for index in range(29)
        ]
        adapter = _FixedAdapter(command)
        direction_calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: direction_calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.battle_start(dict(self.start, bots=roster))
        runtime.navigator = None
        for index, state in enumerate(runtime._ordered_states()):
            state.update(
                x=float(index * 20), y=0.0, z=0.0, yaw=0.0,
                speed=4.0, grounded_once=True)
        original_pose_safe = self.module.prebaked_navigation.pose_is_safe
        self.module.prebaked_navigation.pose_is_safe = (
            lambda *unused, **unused_kwargs: True)
        try:
            outgoing = runtime.update(1.0, 1.0)
        finally:
            self.module.prebaked_navigation.pose_is_safe = original_pose_safe

        states = [message for message in outgoing
                  if message.get('type') == 'bot_state']
        self.assertEqual(29, len(adapter.calls))
        self.assertEqual(58, len(direction_calls))
        self.assertTrue(all(
            state['z'] > 4.0 and state['movement_dir'] == 1
            for state in runtime.states.values()))
        self.assertEqual([200000, 1000000], [
            message['sample_time_us'] for message in states])
        self.assertEqual(1000000, runtime._sample_time_us)
        self.assertAlmostEqual(0.0, runtime._accumulator)

    def test_worker_four_fps_keeps_turning_drive_active_between_plans(self):
        command = self._stationary_command()
        command.update({
            'target_yaw': math.pi / 2.0,
            'throttle': 1.0, 'turn': 1.0, 'combat_mode': 'route',
            'move_position': (100.0, 0.0, 100.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        })
        adapter = _FixedAdapter(command)
        direction_calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: direction_calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.battle_start(self.start)
        runtime.navigator = None
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=4.0,
                     grounded_once=True)
        original_pose_safe = self.module.prebaked_navigation.pose_is_safe
        self.module.prebaked_navigation.pose_is_safe = (
            lambda *unused, **unused_kwargs: True)
        try:
            samples = []
            for frame in range(4):
                runtime.update(0.25, (frame + 1) * 0.25)
                samples.append((
                    state['x'], state['z'], state['yaw'], state['speed'],
                    state['movement_dir']))
        finally:
            self.module.prebaked_navigation.pose_is_safe = original_pose_safe

        self.assertEqual(4, len(adapter.calls))
        self.assertTrue(all(
            speed > 0.0 and movement_dir == 1
            for unused_x, unused_z, unused_yaw, speed, movement_dir in samples))
        self.assertTrue(all(
            later[0] > earlier[0] and later[1] > earlier[1] and
            later[2] > earlier[2]
            for earlier, later in zip(samples, samples[1:])))
        self.assertEqual(12, len(direction_calls))
        self.assertEqual(1000000, runtime._sample_time_us)
        self.assertAlmostEqual(0.0, runtime._accumulator)

    def test_worker_catchup_reprobes_exhausted_corridor_without_replanning(self):
        command = self._stationary_command()
        command.update({
            'throttle': 1.0, 'combat_mode': 'route',
            'move_position': (0.0, 0.0, 100.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        })
        direction_calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: direction_calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        runtime.battle_start(self.start)
        runtime.navigator = None
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, speed=35.0,
                     grounded_once=True)
        original_pose_safe = self.module.prebaked_navigation.pose_is_safe
        self.module.prebaked_navigation.pose_is_safe = (
            lambda *unused, **unused_kwargs: True)
        try:
            runtime.update(2.0, 2.0)
        finally:
            self.module.prebaked_navigation.pose_is_safe = original_pose_safe

        self.assertEqual(3, len(direction_calls))
        self.assertGreater(state['z'], 20.0)
        self.assertEqual(1, state['movement_dir'])
        self.assertGreater(state['speed'], 0.0)
        self.assertEqual(2000000, runtime._sample_time_us)
        self.assertAlmostEqual(0.0, runtime._accumulator)
        self.assertEqual(
            state['half_length'],
            runtime._motion_probe_cache[11]['probe_leading'])

    def test_authority_publication_and_server_ack_remain_live_for_two_minutes(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(1,), max_ammo=300),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Shooter-%d' % index}
            for index in range(29)
        ]
        start = dict(self.start, bots=roster)
        manifest_message = runtime.battle_start(start)[0]
        for state in runtime.states.values():
            state.update(x=0.0, y=0.0, z=0.0,
                         yaw=0.0, aim_yaw=0.0)

        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        server.players[1] = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = list(roster)
        self.assertTrue(server.update_bot_manifest(
            1, {'round_id': server.round_id,
                'bots': manifest_message['bots']}))

        player = {
            'id': 1, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }
        player = _admit_player(player)
        published = 0
        accepted = 0
        fps = 24
        frame_count = fps * 120
        for frame in range(frame_count):
            now = 1.0 + frame / float(fps)
            outgoing = runtime.update(
                1.0 / float(fps), now, players=[player])
            bot_states = [message for message in outgoing
                          if message['type'] == 'bot_state']
            self.assertLessEqual(len(bot_states), 1)
            for bot_state in bot_states:
                self.assertEqual(29, len(bot_state['bots']))
                published += 1
                self.assertTrue(server.update_bot_states(1, {
                    'round_id': server.round_id,
                    'sample_time_us': bot_state['sample_time_us'],
                    'source_batch_horizon_us':
                        bot_state['source_batch_horizon_us'],
                    'bots': bot_state['bots'],
                }))
                accepted += 1
                for published_bot in bot_state['bots']:
                    server_bot = server.bot_states[published_bot['id']]
                    self.assertEqual(published_bot['combat_seq'],
                                     server_bot['combat_ack_seq'])
                runtime.apply_snapshot({
                    'server_tick': frame,
                    'bots': [dict(server.bot_states[bot_id])
                             for bot_id in sorted(server.bot_states)],
                })
                for bot_id, server_bot in server.bot_states.items():
                    self.assertEqual(
                        server_bot['combat_ack_seq'],
                        runtime.states[bot_id]['combat_ack_seq'])

        self.assertEqual((frame_count, frame_count), (published, accepted))
        enemy_ids = {entry['id'] for entry in roster if entry['team'] == 2}
        friendly_ids = {entry['id'] for entry in roster if entry['team'] == 1}
        self.assertTrue(all(runtime.states[bot_id]['fire_seq'] > 100
                            for bot_id in enemy_ids))
        self.assertTrue(all(runtime.states[bot_id]['fire_seq'] == 0
                            for bot_id in friendly_ids))
        self.assertEqual(
            {bot_id: state['fire_seq']
             for bot_id, state in runtime.states.items()},
            {bot_id: state['fire_seq']
             for bot_id, state in server.bot_states.items()})

    def test_rapid_clip_never_skips_server_fire_sequence_at_120_fps(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 0.0, 100.0),
            'face_position': (0.0, 0.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(30, 0.01)),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Autoloader-%d' % index}
            for index in range(29)
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        for state in runtime.states.values():
            state.update(
                x=0.0, y=0.0, z=0.0, yaw=0.0, aim_yaw=0.0)

        server = BattleState(map_name='04_himmelsdorf')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        server.players[1] = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = list(roster)
        self.assertTrue(server.update_bot_manifest(1, {
            'round_id': server.round_id, 'bots': manifest}))

        player = {
            'id': 1, 'team': 1, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }
        player = _admit_player(player)
        previous_fire = dict((entry['id'], 0) for entry in roster)
        publications = 0
        for frame in range(240):
            outgoing = runtime.update(
                1.0 / 120.0, 1.0 + frame / 120.0,
                players=[player])
            for message in outgoing:
                if message['type'] != 'bot_state':
                    continue
                publications += 1
                for bot in message['bots']:
                    current_fire = bot['fire_seq']
                    self.assertLessEqual(
                        current_fire - previous_fire[bot['id']], 1)
                    previous_fire[bot['id']] = current_fire
                self.assertTrue(server.update_bot_states(1, {
                    'round_id': server.round_id,
                    'sample_time_us': message['sample_time_us'],
                    'source_batch_horizon_us':
                        message['source_batch_horizon_us'],
                    'bots': message['bots'],
                }), server.last_bot_state_reject)
                runtime.apply_snapshot({
                    'server_tick': frame,
                    'bots': [dict(server.bot_states[bot_id])
                             for bot_id in sorted(server.bot_states)],
                })

        self.assertGreater(publications, 50)
        enemy_ids = {entry['id'] for entry in roster if entry['team'] == 2}
        friendly_ids = {entry['id'] for entry in roster if entry['team'] == 1}
        self.assertTrue(all(previous_fire[bot_id] > 20
                            for bot_id in enemy_ids))
        self.assertTrue(all(previous_fire[bot_id] == 0
                            for bot_id in friendly_ids))

    def test_render_rate_simulation_and_publication_share_bounded_cadence(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)

        for fps in (5, 20, 24, 30, 40, 60, 120):
            with self.subTest(fps=fps):
                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _critical_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        command),
                    direction_probe=lambda *unused: {
                        'clear': True, 'slope': 0.0},
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver,
                    baked_graph=_graph())
                roster = [dict(self.start['bots'][0])]
                start = dict(self.start, bots=roster)
                manifest = runtime.battle_start(start)[0]
                runtime.states[11]['critical'] = dict(burning)

                server = BattleState(map_name='04_himmelsdorf')
                server.client_build = CLIENT_BUILD_0922
                server.phase = 'battle'
                server.tick = 100000
                server.players[1] = Player(
                    1, object(), ('127.0.0.1', 1), team=1, slot=0)
                server.bot_authority_id = 1
                server.bot_roster = list(roster)
                self.assertTrue(server.update_bot_manifest(1, {
                    'round_id': server.round_id,
                    'bots': manifest['bots'],
                }))

                dt = 1.0 / float(fps)
                previous_pose = None
                changed_frames = 0
                publications = 0
                last_ack = 0
                for frame in range(fps * 2):
                    now = 10.0 + (frame + 1) * dt
                    outgoing = runtime.update(dt, now)
                    pose = runtime.presentation_states()[0]
                    current_pose = (pose['x'], pose['y'], pose['z'],
                                    pose['yaw'])
                    publications_now = [
                        message for message in outgoing
                        if message.get('type') == 'bot_state']
                    if previous_pose is not None:
                        if publications_now:
                            self.assertNotEqual(previous_pose, current_pose)
                            changed_frames += 1
                        else:
                            self.assertEqual(previous_pose, current_pose)
                    previous_pose = current_pose

                    self.assertLessEqual(len(publications_now), 1)
                    for publication in publications_now:
                        publications += 1
                        published = publication['bots'][0]
                        self.assertEqual(last_ack + 1,
                                         published['combat_seq'])
                        self.assertTrue(server.update_bot_states(1, {
                            'round_id': server.round_id,
                            'bots': publication['bots'],
                        }))
                        canonical = server.bot_states[11]
                        self.assertEqual(published['combat_seq'],
                                         canonical['combat_ack_seq'])
                        last_ack = canonical['combat_ack_seq']
                        runtime.apply_snapshot({
                            'server_tick': frame,
                            'bots': [dict(canonical)],
                        })

                expected_publications = min(fps, 30) * 2
                self.assertGreaterEqual(
                    publications, expected_publications - 1)
                self.assertLessEqual(
                    publications, expected_publications + 1)
                self.assertEqual(publications - 1, changed_frames)
                self.assertEqual(publications, last_ack)

    def test_29_bot_sensing_and_simulation_are_render_rate_independent(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Mover-%d' % index}
            for index in range(29)
        ]
        probe_totals = {}
        for fps in (40, 60, 120):
            with self.subTest(fps=fps):
                frame_number = [0]
                probe_frames = []

                def direction_probe(*unused):
                    probe_frames.append(frame_number[0])
                    return {'clear': True, 'slope': 0.0}

                runtime = self.module.BotRuntime(
                    1, descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        command),
                    direction_probe=direction_probe,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, baked_graph=_graph())
                runtime.battle_start(dict(self.start, bots=roster))
                # This case measures authority cadence, not planner fallback.
                runtime.adapter.decide = (
                    lambda unused_state, unused_clear: dict(command))
                previous = None
                changed = 0
                publications = []
                dt = 1.0 / float(fps)
                for frame in range(fps * 2):
                    frame_number[0] = frame
                    outgoing = runtime.update(
                        dt, 10.0 + (frame + 1) * dt)
                    poses = tuple(
                        (state['id'], state['x'], state['y'], state['z'],
                         state['yaw'])
                        for state in runtime.presentation_states())
                    publications_now = [
                        message for message in outgoing
                        if message.get('type') == 'bot_state']
                    if previous is not None:
                        if publications_now:
                            self.assertTrue(all(
                                poses[index] != previous[index]
                                for index in range(29)))
                            changed += 29
                        else:
                            self.assertEqual(previous, poses)
                    previous = poses
                    publications.extend(publications_now)

                self.assertGreaterEqual(len(publications), 59)
                self.assertLessEqual(len(publications), 61)
                self.assertEqual(29 * (len(publications) - 1), changed)
                self.assertTrue(all(
                    len(message['bots']) == 29
                    for message in publications))
                # The isolated selected-motion seam probes all 29 bots on the
                # first authority tick; later deadlines stay staggered.
                self.assertEqual(29, probe_frames.count(0))
                later_counts = [probe_frames.count(frame)
                                for frame in range(1, fps * 2)]
                self.assertLess(max(later_counts), 29)
                maximum_per_bot = (
                    4 + 2 * int(math.ceil(
                        2.0 / self.module.DECISION_SECONDS)))
                self.assertLessEqual(
                    len(probe_frames), 29 * maximum_per_bot)
                probe_totals[fps] = len(probe_frames)

        # Extra render callbacks consume neither physics nor native sensing.
        self.assertLessEqual(
            max(probe_totals.values()) - min(probe_totals.values()), 29 * 4)

    def test_full_roster_publication_projects_each_internal_state_once(self):
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 15 else 2,
             'slot': index if index < 15 else index - 15,
             'name': 'Projection-%02d' % index}
            for index in range(29)
        ]
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=roster))
        calls = []
        original = self.module.lan_client.project_bot_state

        def counted(state):
            calls.append(state['id'])
            return original(state)

        self.module.lan_client.project_bot_state = counted
        try:
            publication = runtime.update(.04, 1.0)[0]
        finally:
            self.module.lan_client.project_bot_state = original

        internal = runtime._ordered_states()
        self.assertEqual([state['id'] for state in internal], calls)
        self.assertEqual(
            [original(state) for state in internal], publication['bots'])
        self.assertNotIn('launches', publication)
        self.assertTrue(all(
            len(projected) < len(state)
            for projected, state in zip(publication['bots'], internal)))
        self.assertTrue(all(
            projected['pitch'] == state['pitch'] and
            projected['roll'] == state['roll'] and
            projected['speed'] == state['speed']
            for projected, state in zip(publication['bots'], internal)))
        self.assertTrue(all(
            'profile' not in state and
            state['reload_duration'] > 0.0 and
            0.0 <= state['reload_time'] <= state['reload_duration']
            for state in publication['bots']))

    def test_contact_resolution_uses_the_banked_global_simulation_step(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        contact_steps = []
        runtime._resolve_tank_contacts = (
            lambda unused_players, unused_now, step:
            contact_steps.append(step) or [])
        dt = 1.0 / 120.0

        for frame in range(5):
            runtime.update(dt, 1.0 + (frame + 1) * dt)

        self.assertEqual(2, len(contact_steps))
        self.assertAlmostEqual(dt, contact_steps[0])
        self.assertAlmostEqual(4.0 * dt, contact_steps[1])
        self.assertAlmostEqual(5.0 * dt, sum(contact_steps))

    def test_banked_ticks_preserve_motion_reload_and_vertical_time(self):
        command = dict(self._stationary_command())
        command.update({
            'throttle': 1.0, 'combat_mode': 'route',
            'move_position': (0.0, 100.0, 200.0),
            'movement_intent': True,
        })
        original_longitudinal = self.module.vehicle_physics.longitudinal_step
        original_traverse = self.module.vehicle_physics.traverse_step
        self.module.vehicle_physics.longitudinal_step = (
            lambda *unused, **unused_kwargs: 10.0)
        self.module.vehicle_physics.traverse_step = (
            lambda *unused, **unused_kwargs: 0.0)
        try:
            results = {}
            for fps in (20, 30, 60, 120):
                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        command),
                    direction_probe=lambda *unused: {
                        'clear': True, 'slope': 0.0},
                    ground_probe=lambda *unused: None,
                    physics_ground_probe=lambda *unused: None,
                    spawn_resolver=_spawn_resolver, baked_graph=_graph())
                runtime.battle_start(self.start)
                dt = 1.0 / float(fps)
                # Anchor the nominal publication clock, then measure an exact
                # one-second interval ending on the same 30 Hz boundary.
                runtime.update(dt, 10.0)
                state = runtime.states[11]
                state.update({
                    'x': 0.0, 'y': 100.0, 'z': 100.0, 'yaw': 0.0,
                    'speed': 10.0, 'grounded_once': True,
                    'airborne': True, 'vertical_speed': 0.0,
                    'push_x': 0.0, 'push_z': 0.0,
                })
                runtime._gun_states[11].elapsed = 0.0
                publications = 0
                for frame in range(fps):
                    outgoing = runtime.update(
                        dt, 10.0 + (frame + 1) * dt)
                    publications += len([
                        message for message in outgoing
                        if message.get('type') == 'bot_state'])
                results[fps] = (
                    state['z'] - 100.0,
                    runtime._gun_states[11].elapsed,
                    state['vertical_speed'], publications)
        finally:
            self.module.vehicle_physics.longitudinal_step = original_longitudinal
            self.module.vehicle_physics.traverse_step = original_traverse

        for fps, result in results.items():
            distance, reload_elapsed, vertical_speed, publications = result
            self.assertAlmostEqual(10.0, distance, places=8)
            self.assertAlmostEqual(1.0, reload_elapsed, places=8)
            self.assertAlmostEqual(
                -self.module.vehicle_physics.GRAVITY,
                vertical_speed, places=8)
            self.assertEqual(min(fps, 30), publications)

    def test_cover_jobs_are_phased_and_publish_complete_fair_batches(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 1,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 0.0),
            'face_position': (0.0, 1.0, 0.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        roster = [
            {'id': 11 + index, 'team': 2, 'slot': index,
             'name': 'Cover-%d' % index}
            for index in range(6)
        ]
        player = {
            'id': 1, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }
        player = _admit_player(player)
        for fps in (24, 40, 60, 120):
            with self.subTest(fps=fps):
                frame_number = [0]
                calls = []

                def cover_probe(source, target, unused_route, allies,
                                unused_segment_clear):
                    calls.append((frame_number[0], source['id'],
                                  frame_number[0] / float(fps)))
                    self.assertEqual(6, len(allies))
                    return ({'source_id': source['id'],
                             'target_id': target['network_id']},)

                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        command),
                    direction_probe=lambda *unused: {
                        'clear': True, 'slope': 0.0},
                    visibility_probe=lambda *unused: True,
                    firing_lane_probe=lambda *unused: True,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, cover_probe=cover_probe,
                    baked_graph=_graph())
                runtime.battle_start(dict(self.start, bots=roster))

                observations = []
                dt = 1.0 / float(fps)
                for frame in range(int(fps * 2.5) + 1):
                    frame_number[0] = frame
                    outgoing = runtime.update(
                        dt, 1.0 + frame * dt, players=[player])
                    published = [
                        message for message in outgoing
                        if message.get('type') == 'bot_observation']
                    observations.extend(published)

                self.assertGreaterEqual(len(observations), 6)
                # Visibility publishes independently at 0.40 seconds. Cover
                # results arrive as their one-hertz phased jobs complete.
                self.assertEqual([], observations[0]['affordances'])
                published_ids = [
                    value['candidates'][0]['source_id']
                    for observation in observations
                    for value in observation['affordances']]
                self.assertEqual([11, 12, 13, 14, 15, 16],
                                 published_ids[:6])
                self.assertEqual([11, 12, 13, 14, 15, 16],
                                 [value[1] for value in calls[:6]])
                self.assertEqual(11, calls[6][1])
                self.assertGreaterEqual(
                    calls[3][2] - calls[0][2],
                    self.module.COVER_REFRESH_SECONDS - dt - 1e-9)
                frame_counts = {}
                for frame, unused_bot_id, unused_offset in calls:
                    frame_counts[frame] = frame_counts.get(frame, 0) + 1
                self.assertEqual(1, max(frame_counts.values()))
                self.assertGreaterEqual(len(calls), 7)

                # Every cover batch remains phased through half of its own
                # one-second tactical window.
                first_offsets = [value[2] for value in calls[:3]]
                self.assertEqual(0.0, first_offsets[0])
                self.assertLess(first_offsets[-1],
                                self.module.COVER_JOB_WINDOW_SECONDS + 1e-9)

    def test_cached_motion_probe_has_explicit_corridor_safety_bounds(self):
        cached = {
            'result': {'clear': True, 'slope': 0.0},
            'position': (0.0, 0.0, 0.0), 'yaw': 0.0,
            'deadline': 1.0975,
        }
        reusable = self.module.BotRuntime._motion_probe_reusable

        self.assertTrue(reusable(
            cached, (0.0, 0.0, 3.4), 0.0, 35.0, 1.09))
        self.assertFalse(reusable(
            cached, (0.0, 0.0, 3.51), 0.0, 35.0, 1.09))
        self.assertFalse(reusable(
            cached, (1.01, 0.0, 0.0), 0.0, 0.0, 1.09))
        self.assertFalse(reusable(
            cached, (0.0, 0.0, 0.0),
            math.asin(1.01 / 15.0), 0.0, 1.09))
        self.assertFalse(reusable(
            cached, (0.0, 0.0, 0.0), 0.0, 0.0, 1.0975))

        covers = self.module.BotRuntime._motion_probe_covers_distance
        self.assertFalse(covers({'maximum_distance': 4.0}, 6.0))
        self.assertTrue(covers({'maximum_distance': 6.0}, 4.0))
        self.assertTrue(covers({}, 6.0))
        self.assertFalse(covers({'maximum_distance': 4.0}, None))

    def test_pending_generic_corridor_reserves_the_hull_leading_edge(self):
        runtime = self.module.BotRuntime(1)

        def install(yaw):
            runtime._motion_probe_cache[11] = {
                'result': {
                    'clear': True, 'collision': False, 'slope': 0.0,
                    '_world_receipt_pending': True,
                },
                'position': (0.0, 0.0, 0.0),
                'yaw': yaw,
                'probe_distance': 15.0,
                'probe_leading': 3.5,
                'deadline': 0.0,
            }

        install(0.0)
        self.assertTrue(runtime.motion_world_corridor_reusable(
            11, (0.0, 0.0, 11.0), 0.0, 4.0,
            now=10.0, dt=0.04))
        self.assertFalse(runtime.motion_world_corridor_reusable(
            11, (0.0, 0.0, 11.2), 0.0, 4.0,
            now=10.0, dt=0.04))

        install(math.pi)
        self.assertTrue(runtime.motion_world_corridor_reusable(
            11, (0.0, 0.0, -11.0), math.pi, -4.0,
            now=10.0, dt=0.04))
        self.assertFalse(runtime.motion_world_corridor_reusable(
            11, (0.0, 0.0, -11.2), math.pi, -4.0,
            now=10.0, dt=0.04))

        runtime._motion_probe_cache[11].pop('probe_leading')
        self.assertFalse(runtime.motion_world_corridor_reusable(
            11, (0.0, 0.0, -3.6), math.pi, -4.0,
            now=10.0, dt=0.04))

    def test_typed_world_receipt_contains_only_the_actual_motion_step(self):
        runtime = self.module.BotRuntime(1)
        runtime._motion_probe_cache[11] = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': {
                    'distance': 8.0,
                    'half_width': 1.6,
                    'leading': 3.5,
                    'origin': (0.0, 0.0, 0.0),
                    'yaw': 0.0,
                    'direction': 1,
                },
            },
            'position': (0.0, 0.0, 0.0),
            'yaw': 0.0,
            'deadline': 1.10,
        }

        self.assertTrue(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 3.4), 0.0, 35.0,
            now=1.09, dt=0.02))
        self.assertFalse(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 3.4), 0.0, 35.0,
            now=1.09, dt=0.04))
        self.assertFalse(runtime._world_receipt_contains({
            'distance': 4.0, 'half_width': 1.6, 'leading': 5.0,
            'origin': (0.0, 0.0, 0.0), 'yaw': 0.0, 'direction': 1,
        }, (0.0, 0.0, 0.0), 0.0, 0.0, 0.04))

    def test_typed_world_receipt_rejects_pose_heading_expiry_and_defer(self):
        runtime = self.module.BotRuntime(1)
        cached = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': {
                    'distance': 8.0,
                    'half_width': 1.6,
                    'leading': 3.5,
                    'origin': (0.0, 0.0, 0.0),
                    'yaw': 0.0,
                    'direction': 1,
                },
            },
            'position': (0.0, 0.0, 0.0),
            'yaw': 0.0,
            'deadline': 1.10,
        }
        runtime._motion_probe_cache[11] = cached

        reusable = runtime.motion_world_receipt_reusable
        self.assertTrue(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0002, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0002, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.04, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, -0.0002), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, -0.05), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.00002, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.10, dt=0.04))

        cached['result']['deferred'] = True
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))

    def test_expired_plan_refresh_carries_only_a_contained_world_receipt(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': None, 'face_position': None,
            'move_position': None,
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        direction_calls = []
        receipt_calls = []

        def receipt(position, yaw, speed, unused_descriptor):
            receipt_calls.append((tuple(position), yaw, speed))
            return {
                'distance': 15.0, 'half_width': 1.6, 'leading': 3.5,
                'origin': tuple(position), 'yaw': float(yaw),
                'direction': -1 if float(speed) < 0.0 else 1,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: direction_calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            world_receipt_probe=receipt,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.adapter.decide = lambda unused_state, unused_clear: dict(
            command)

        runtime.update(.04, 1.0)
        first_receipt = runtime._motion_probe_cache[11][
            'result']['world_receipt']
        self.assertEqual(1, len(direction_calls))
        self.assertEqual(1, len(receipt_calls))

        # The generic planning sample expires, but the exact hull is still at
        # the receipt origin. Refresh slope/steering without another 3x3 proof.
        runtime.update(.04, 1.20)
        self.assertEqual(2, len(direction_calls))
        self.assertEqual(1, len(receipt_calls))
        self.assertIs(
            first_receipt,
            runtime._motion_probe_cache[11]['result']['world_receipt'])
        self.assertEqual(1, runtime.states[11]['movement_dir'])

        # Even sub-millimetre lateral drift is outside the exact typed lanes;
        # the next expired planning sample must acquire a new native receipt.
        runtime.states[11]['x'] += 0.0002
        runtime.update(.04, 1.40)
        self.assertEqual(3, len(direction_calls))
        self.assertEqual(2, len(receipt_calls))
        self.assertIsNot(
            first_receipt,
            runtime._motion_probe_cache[11]['result']['world_receipt'])

    def test_planner_direction_sample_refreshes_the_selected_motion_ray(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 1.0, 200.0),
            'face_position': (0.0, 1.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: calls.append(1) or {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=(lambda graph: (
                graph['bake'].update({
                    'vehicle_half_width': 2.15,
                    'edge_clearance_radii': [3.0, 6.0],
                }) or graph))(_graph()))
        runtime.battle_start(self.start)

        class StaticGrid(object):
            prebaked = True

            def near_baked_navigation(self, unused_position, unused_radius):
                return True

            def segment_has_baked_hazard(
                    self, unused_start, unused_end, unused_mask):
                return False

            def segment_clear(self, unused_start, unused_end):
                return True

        runtime.navigator.grid = StaticGrid()

        for frame in range(40):
            runtime.update(0.05, 1.0 + frame * 0.05)

        decisions = runtime._decision_counts[11]
        self.assertGreaterEqual(len(calls), decisions)
        # Pure graph ranking contributes no native calls. Initial deadline
        # staggering can require two extra selected-direction safety samples.
        self.assertLessEqual(len(calls), decisions + 2)

    def test_typed_receipt_owns_origin_yaw_and_direction_over_cache_key(self):
        runtime = self.module.BotRuntime(1)
        receipt = {
            'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
            'origin': (0.0, 0.0, 0.0), 'yaw': 0.0, 'direction': 1,
        }
        runtime._motion_probe_cache[11] = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': receipt,
            },
            # Model two planner requests that collided under round(yaw, 4):
            # the cache metadata names the later request, while the receipt
            # was actually sampled at the exact origin and yaw above.
            'position': (0.0, 0.0, 0.00004),
            'yaw': 0.00004,
            'deadline': 1.10,
        }

        reusable = runtime.motion_world_receipt_reusable
        self.assertTrue(reusable(
            11, (0.0, 0.0, 0.0), 0.0, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.00004), 0.00004, 4.0,
            now=1.09, dt=0.04))
        self.assertFalse(reusable(
            11, (0.0, 0.0, 0.0), 0.0, -4.0,
            now=1.09, dt=0.04))

    def test_reverse_typed_receipt_reuses_exact_travel_yaw(self):
        runtime = self.module.BotRuntime(1)
        runtime._motion_probe_cache[11] = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                'world_receipt': {
                    'distance': 8.0, 'half_width': 1.6, 'leading': 3.5,
                    'origin': (0.0, 0.0, 0.0), 'yaw': math.pi,
                    'direction': -1,
                },
            },
            'position': (0.0, 0.0, 0.0),
            'yaw': math.pi,
            'deadline': 1.10,
        }

        self.assertTrue(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 0.0), math.pi, -4.0,
            now=1.09, dt=0.04))
        self.assertFalse(runtime.motion_world_receipt_reusable(
            11, (0.0, 0.0, 0.0), 0.0, -4.0,
            now=1.09, dt=0.04))

    def test_settled_motion_reuses_only_an_unchanged_pose_and_heading(self):
        reusable = self.module.BotRuntime._motion_probe_reusable
        cached = {
            'result': {'clear': True, 'slope': 0.2},
            'position': (10.0, 2.0, 20.0),
            'yaw': 0.5, 'deadline': 1.0,
        }

        self.assertTrue(reusable(
            cached, (10.0, 2.0, 20.0), 0.5, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.1, 2.0, 20.0), 0.5, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.0, 2.1, 20.0), 0.5, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.0, 2.0, 20.0), 0.51, 0.0, 10.0, True))
        self.assertFalse(reusable(
            cached, (10.0, 2.0, 20.0), 0.5, 0.0, 10.0, False))

    def test_settled_full_roster_keeps_slope_without_periodic_motion_probes(self):
        command = self._stationary_command()
        calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: calls.append(1) or {
                'clear': True, 'slope': 0.2},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Settled-%d' % index}
            for index in range(29)
        ]
        runtime.battle_start(dict(self.start, bots=roster))
        # Isolate the continuous-motion seam from LocalDriver's decision
        # probes; an intentional hold returns before direction_clear in the
        # production adapter.
        runtime.adapter.decide = lambda unused_state, unused_clear: dict(command)

        self.assertTrue(all(
            not state['grounded_once'] for state in runtime.states.values()))
        runtime.update(.04, 1.0)
        self.assertEqual(29, len(calls))
        self.assertTrue(all(
            state['grounded_once'] for state in runtime.states.values()))
        runtime.update(.20, 2.0)
        self.assertEqual(29, len(calls))
        self.assertTrue(all(
            abs(state['last_drive_pitch'] + math.atan(0.2)) < 1e-9
            for state in runtime.states.values()))

        command['throttle'] = 1.0
        command['movement_intent'] = True
        runtime.update(.20, 2.2)
        self.assertEqual(58, len(calls))

    def test_full_roster_visual_slope_targets_rotate_under_frame_budget(self):
        ground_calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *args: ground_calls.append(args) or 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 15 else 2,
             'slot': index if index < 15 else index - 15,
             'name': 'Slope-%d' % index}
            for index in range(29)
        ]
        runtime.battle_start(dict(self.start, bots=roster))

        runtime.update(0.04, 1.0)

        self.assertEqual(
            29 + 4 * self.module.MAX_SLOPE_POSE_SAMPLES_PER_FRAME,
            len(ground_calls))
        self.assertEqual(
            self.module.MAX_SLOPE_POSE_SAMPLES_PER_FRAME,
            sum('pose_sample' in state for state in runtime.states.values()))

        for frame in range(1, 6):
            before = len(ground_calls)
            runtime.update(0.04, 1.0 + frame * 0.04)
            remaining = max(
                0, 29 - frame * self.module.MAX_SLOPE_POSE_SAMPLES_PER_FRAME)
            sampled = min(
                self.module.MAX_SLOPE_POSE_SAMPLES_PER_FRAME, remaining)
            self.assertEqual(29 + 4 * sampled,
                             len(ground_calls) - before)
        self.assertTrue(all(
            'pose_sample' in state for state in runtime.states.values()))

    def test_reverse_recovery_uses_driver_turn_sign_not_target_bearing(self):
        command = {
            'target_yaw': 1.0, 'throttle': -0.72, 'turn': -1.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 20.0),
            'face_position': (0.0, 0.0, 20.0),
            'move_position': (0.0, 0.0, 20.0),
            'recovery_mode': 'reverse_turn', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11]['speed'] = -1.0

        state = runtime.update(.04, 1.0)[0]['bots'][0]

        self.assertEqual(-1, state['rotation_dir'])
        # Reverse motion flips track steering inside the copied physics law, so
        # a negative input produces the requested positive hull-yaw recovery.
        self.assertGreater(runtime._turn_speeds[11], 0.0)

    def test_driver_proportional_turn_is_not_collapsed_to_keyboard_sign(self):
        command = {
            'target_yaw': 0.2, 'throttle': 1.0, 'turn': 0.2,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 20.0),
            'face_position': (0.0, 0.0, 20.0),
            'move_position': (0.0, 0.0, 20.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)

        state = runtime.update(.04, 1.0)[0]['bots'][0]

        self.assertEqual(1, state['rotation_dir'])
        self.assertAlmostEqual(
            runtime._physics_params[11]['rotSpd'] * 0.2,
            runtime._turn_speeds[11])

    def test_limited_traverse_tank_turns_hull_before_advancing_or_firing(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (100.0, 0.5, 0.0),
            'face_position': (100.0, 0.5, 0.0),
            'move_position': (100.0, 0.0, 0.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                turret_yaw_limits=(-0.1, 0.1)),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11]['yaw'] = 0.0

        state = runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'alive': True,
             'x': 100.0, 'y': 0.5, 'z': 0.0,
             'effective_params': _effective_params_snapshot()}
        ])[0]['bots'][0]

        self.assertEqual(0, state['movement_dir'])
        self.assertEqual(1, state['rotation_dir'])
        self.assertTrue(runtime.states[11]['hull_aiming'])
        self.assertEqual(0, state['fire_seq'])

    def test_no_target_gun_keeps_safe_bearing_and_rests_horizontally(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(
            x=0.0, y=0.0, z=0.0, yaw=0.0,
            aim_yaw=0.65, turret_yaw=0.65,
            gun_pitch=-0.30, desired_gun_pitch=-0.30)
        command = {
            'aim_position': (0.0, -100.0, 1.0),
            '_ballistic_solution': None,
        }
        original_origin = runtime._exact_shot_origin
        runtime._exact_shot_origin = lambda *unused: (_ for _ in ()).throw(
            AssertionError('no-target rest requested a shot origin'))
        try:
            desired_yaw, horizontal = runtime._update_gun_aim(
                state, command, None, .20)
        finally:
            runtime._exact_shot_origin = original_origin

        self.assertAlmostEqual(.65, desired_yaw, places=9)
        self.assertEqual(0.0, horizontal)
        self.assertAlmostEqual(0.0, state['desired_gun_pitch'], places=9)
        self.assertGreater(state['gun_pitch'], -.30)
        self.assertFalse(state['gun_aligned'])

    def test_direct_aim_reuses_ballistic_muzzle_origin_within_tick(self):
        calls = []
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }

        def origin(source, *unused):
            calls.append(int(source['id']))
            return (source['x'], source['y'] + 1.0, source['z'])

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            direct_launch_origin_probe=origin,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        player = _admit_player({
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
        })

        runtime.update(.04, 1.0, players=[player])

        self.assertEqual([11], calls)

    def test_target_solution_is_10hz_while_gun_slew_stays_30hz(self):
        solves = []
        aims = []
        origins = []
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }

        def origin(source, *unused):
            origins.append(int(source['id']))
            return (source['x'], source['y'] + 1.0, source['z'])

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            direct_launch_origin_probe=origin,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        original_solve = runtime._ballistic_solution
        original_aim = runtime._update_gun_aim

        def counted_solve(*args, **kwargs):
            solves.append(args[-1])
            return original_solve(*args, **kwargs)

        def counted_aim(*args, **kwargs):
            aims.append(args[-1])
            return original_aim(*args, **kwargs)

        runtime._ballistic_solution = counted_solve
        runtime._update_gun_aim = counted_aim
        player = _admit_player({
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
        })

        for frame in range(60):
            runtime.update(
                1.0 / 60.0, 1.0 + (frame + 1) / 60.0,
                players=[player])

        self.assertEqual(30, len(aims))
        self.assertEqual(10, len(solves))
        # A failed trajectory may need one fallback muzzle read for neutral
        # aiming, but the 10 Hz solve still keeps native reads below 30 Hz.
        self.assertGreaterEqual(len(origins), len(solves))
        self.assertLess(len(origins), len(aims))
        self.assertEqual({11}, set(origins))

    def test_target_solution_cache_invalidates_and_burst_forces_freshness(self):
        runtime = self.module.BotRuntime(1)
        descriptor = _combat_descriptor()
        state = {'id': 11, 'fire_seq': 0,
                 'siege_state': self.module.siege_mechanics.DISABLED}
        target = {'id': 2, 'network_id': 2, 'kind': 'human', 'alive': True}
        calls = []

        def solve(*unused_args):
            calls.append(len(calls) + 1)
            return {'solution': calls[-1]}

        runtime._ballistic_solution = solve
        first, first_fresh = runtime._cadenced_ballistic_solution(
            state, target, descriptor, 0, 1.0)
        reused, reused_fresh = runtime._cadenced_ballistic_solution(
            state, target, descriptor, 0, 1.01)
        self.assertTrue(first_fresh)
        self.assertFalse(reused_fresh)
        self.assertIs(first, reused)
        self.assertEqual(1, len(calls))

        state['fire_seq'] = 1
        unused_changed, changed_fresh = \
            runtime._cadenced_ballistic_solution(
                state, target, descriptor, 0, 1.02)
        self.assertTrue(changed_fresh)
        target = dict(target, network_id=3)
        unused_target, target_fresh = \
            runtime._cadenced_ballistic_solution(
                state, target, descriptor, 0, 1.03)
        self.assertTrue(target_fresh)
        for now in (1.04, 1.07):
            unused_burst, burst_fresh = \
                runtime._cadenced_ballistic_solution(
                    state, target, descriptor, 0, now, force=True)
            self.assertTrue(burst_fresh)
        self.assertEqual(5, len(calls))

    def test_ordinary_fire_waits_for_a_fresh_local_action_solution(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.01, clip=(1,)),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            direct_launch_origin_probe=lambda source, *unused: (
                source['x'], source['y'] + 1.0, source['z']),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': 0.0, 'flight_time': 0.1,
            'arc': 'low', '_origin': (0.0, 1.0, 0.0),
        }
        freshness = [False, True]
        runtime._cadenced_ballistic_solution = lambda *unused, **kwargs: (
            solution, freshness.pop(0))
        player = _admit_player({
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
        })

        stale = runtime.update(0.2, 1.0, players=[player])[0]['bots'][0]
        self.assertEqual(0, stale['fire_seq'])
        fresh = runtime.update(0.2, 1.2, players=[player])[0]['bots'][0]
        self.assertEqual(1, fresh['fire_seq'])

    def test_bot_fire_uses_turret_pitch_los_reload_clip_and_barrel_scatter(self):
        lane_probes = []

        def firing_lane(source, target):
            lane_probes.append((source['id'], target['network_id']))
            # Deliberately block the first otherwise-ready shot.
            return len(lane_probes) != 1

        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 10.5, 100.0),
            'face_position': (0.0, 10.5, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                gun_speed=0.25),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=firing_lane,
            artillery_launch_probe=lambda *unused: (_ for _ in ()).throw(
                AssertionError('ordinary tank requested SPG proof')),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        # This test isolates the firing clock. Observation-lane publication is
        # covered separately and intentionally uses the same cached probe.
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state['x'], state['y'], state['z'], state['yaw'] = 0.0, 0.0, 0.0, 0.0
        player = {'id': 2, 'team': 1, 'alive': True,
                  'x': 0.0, 'y': 10.5, 'z': 100.0}
        player = _admit_player(player)

        # The gun first slews to the visible elevated target; it cannot fire
        # merely because the strategic order says fire_allowed.
        first = runtime.update(.20, 1.0, players=[player])[0]['bots'][0]
        self.assertEqual(0, first['fire_seq'])
        self.assertNotIn('shot_yaw', first)
        self.assertNotIn('shot_pitch', first)
        self.assertLess(first['gun_pitch'], 0.0)
        self.assertFalse(runtime.states[11]['gun_aligned'])

        # The second slew tick aligns, but the full reload is not complete.
        aligned = runtime.update(.20, 1.2, players=[player])[0]['bots'][0]
        self.assertEqual(0, aligned['fire_seq'])
        self.assertTrue(runtime.states[11]['gun_aligned'])
        self.assertEqual(0, len(lane_probes))

        # Once aligned and reloaded, a fresh static-lane probe still blocks.
        blocked = runtime.update(.11, 1.31, players=[player])[0]['bots'][0]
        self.assertEqual(0, blocked['fire_seq'])
        self.assertNotIn('shot_yaw', blocked)
        self.assertNotIn('shot_pitch', blocked)
        self.assertTrue(runtime.states[11]['gun_aligned'])
        self.assertEqual(1, len(lane_probes))

        # The next fresh lane is clear. The emitted shot angles are the actual
        # dispersed barrel ray and the clip selects the intra-clip delay.
        fired = runtime.update(.20, 1.52, players=[player])[0]['bots'][0]
        self.assertEqual(1, fired['fire_seq'])
        self.assertIn('shot_yaw', fired)
        self.assertIn('shot_pitch', fired)
        self.assertAlmostEqual(0.0, fired['aim_yaw'], places=6)
        self.assertGreater(fired['shot_pitch'], 0.0)
        self.assertEqual(1, runtime.states[11]['clip'])
        self.assertAlmostEqual(0.2, runtime.states[11]['reload_duration'])

        runtime.update(.20, 1.72, players=[player])
        second = runtime.update(.11, 1.83, players=[player])[0]['bots'][0]
        self.assertEqual(2, second['fire_seq'])
        self.assertEqual(0, runtime.states[11]['clip'])
        full_reload = runtime._gun_states[11].reload_full
        self.assertAlmostEqual(
            full_reload, runtime.states[11]['reload_duration'])

        now = 1.83
        for fraction in (0.4, 0.4):
            step = full_reload * fraction
            now += step
            runtime.update(step, now, players=[player])
        step = full_reload * 0.19
        now += step
        early = runtime.update(step, now, players=[player])[0]['bots'][0]
        self.assertEqual(2, early['fire_seq'])
        step = max(full_reload * 0.02, 0.04)
        now += step
        runtime.update(step, now, players=[player])
        self.assertEqual(3, runtime.states[11]['fire_seq'])

    def test_ballistic_aim_leads_a_moving_target_and_matches_barrel_pitch(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(1)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'mediumTank'},
        }
        target = {
            'position': (0.0, 0.0, 300.0),
            'yaw': math.pi * 0.5, 'speed': 20.0,
        }

        solution = runtime._local_ballistic_solution(
            state, target, descriptor, 0)

        self.assertIsNotNone(solution)
        self.assertGreater(solution['aim_position'][0], 1.0)
        self.assertGreater(solution['yaw'], 0.0)
        self.assertGreaterEqual(solution['pitch'], -0.35)
        self.assertLessEqual(solution['pitch'], 0.15)
        self.assertGreater(solution['flight_time'], 0.25)

    def test_spg_requires_a_client_proved_arc_and_accepts_low_root_fallback(self):
        descriptor = _combat_descriptor()
        target = {
            'position': (0.0, 0.0, 500.0),
            'yaw': 0.0, 'speed': 0.0,
        }
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'SPG'},
        }
        calls = []
        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=lambda *args: calls.append(args) or {
                'aim_position': (0.0, 1.0, 500.0),
                'yaw': 0.0, 'pitch': -0.10, 'flight_time': 0.5,
                'arc': 'low',
            })

        solution = runtime._ballistic_solution(
            state, target, descriptor, 0, 2.0)

        self.assertEqual('low', solution['arc'])
        self.assertEqual(-0.10, solution['pitch'])
        self.assertEqual(1, len(calls))
        blocked = self.module.BotRuntime(1)
        self.assertIsNone(blocked._ballistic_solution(
            state, target, descriptor, 0, 2.0))

    def test_invalid_spg_solution_cannot_be_clamped_into_a_fake_hit(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=lambda *unused: {
                'aim_position': (0.0, 1.0, 500.0),
                'yaw': 0.0, 'pitch': -1.2, 'flight_time': 3.0,
                'arc': 'high',
            })
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'SPG'},
        }

        self.assertIsNone(runtime._ballistic_solution(
            state, {'position': (0.0, 0.0, 500.0)}, descriptor, 0, 3.0))

    def test_spg_solution_beyond_projectile_lifetime_is_rejected(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=lambda *unused: {
                'aim_position': (0.0, 1.0, 500.0),
                'yaw': 0.0, 'pitch': -0.1, 'flight_time': 20.001,
                'arc': 'high',
            })
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'profile': {'class_tag': 'SPG'},
        }

        self.assertIsNone(runtime._ballistic_solution(
            state, {'position': (0.0, 0.0, 500.0)}, descriptor, 0, 3.0))

    def test_spg_final_proof_pending_does_not_consume_fire_sequence(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        descriptor.gun.shots = descriptor.gun.shots * 2
        descriptor.gun.maxAmmo = 60
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 1, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1000.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        calls = []
        proofs = []

        def final_proof(state, target, unused_descriptor, shell_index,
                        fire_seq, shot_yaw, shot_pitch, flight_time, now):
            calls.append((
                state, target, shell_index, fire_seq, shot_yaw,
                shot_pitch, flight_time, now))
            if len(calls) == 1:
                return None
            speed = 1000.0
            horizontal = math.cos(shot_pitch)
            velocity = (
                math.sin(shot_yaw) * horizontal * speed,
                math.sin(shot_pitch) * speed,
                math.cos(shot_yaw) * horizontal * speed,
            )
            muzzle = (
                target['position'][0] - velocity[0] * flight_time,
                target['position'][1] + 1.0 -
                velocity[1] * flight_time +
                0.5 * 10.0 * flight_time * flight_time,
                target['position'][2] - velocity[2] * flight_time,
            )
            receipt = {
                'proof_key': ('exact', fire_seq, muzzle,
                              shot_yaw, shot_pitch),
                'fire_seq': fire_seq, 'shell_index': shell_index,
                'origin': muzzle,
                'velocity': velocity,
                'shot_yaw': shot_yaw, 'shot_pitch': shot_pitch,
                'gravity': 10.0, 'max_distance': 5000.0,
                'max_time_ms': 20000, 'flight_time': flight_time,
            }
            proofs.append(receipt)
            return receipt

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ballistic_solution_probe=lambda *unused: {
                'aim_position': (0.0, 1.0, 100.0),
                'yaw': 0.0, 'pitch': -0.1, 'flight_time': 0.5,
                'arc': 'low',
            },
            artillery_launch_probe=final_proof,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'turret_yaw': 0.0,
            'gun_pitch': -0.1, 'desired_gun_pitch': -0.1,
            'profile': {'class_tag': 'SPG'},
        })
        runtime._gun_states[11].elapsed = 1.0
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }
        player = _admit_player(player)

        pending = runtime.update(
            0.04, 1.0, players=[player])[0]['bots'][0]
        self.assertEqual(0, pending['fire_seq'])
        self.assertEqual((0, 1), (
            pending['shell_index'], pending['next_shell_index']))
        self.assertNotIn('shot_origin', pending)
        self.assertEqual(1, calls[0][3])
        self.assertEqual(0, calls[0][2])

        fired_message = runtime.update(0.04, 1.04, players=[player])[0]
        fired = fired_message['bots'][0]
        launch = fired_message['launches'][0]
        self.assertEqual(1, fired['fire_seq'])
        self.assertEqual((0, 1), (
            fired['shell_index'], fired['next_shell_index']))
        self.assertEqual(1, calls[1][3])
        self.assertEqual(0, calls[1][2])
        self.assertNotEqual(0.0, fired['shot_yaw'])
        self.assertNotEqual(0.1, fired['shot_pitch'])
        self.assertEqual(proofs[0]['origin'], launch['shot_origin'])
        self.assertEqual(proofs[0]['velocity'], launch['shot_velocity'])
        self.assertEqual(20000, launch['shot_max_time_ms'])

    def test_spg_final_proof_waits_for_exact_nominal_alignment(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        calls = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *args: calls.append(args))
        runtime.round_id = 7
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'fire_seq': 0, 'aim_yaw': 0.000001,
            'gun_pitch': -0.1, 'gun_aligned': True,
            'critical': {},
        }
        solution = {
            'yaw': 0.0, 'pitch': -0.1, 'flight_time': 0.5,
            'aim_position': (0.0, 1.0, 100.0),
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        self.assertEqual([], calls)

        state['aim_yaw'] = 0.0
        state['gun_pitch'] = -0.100001
        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        self.assertEqual([], calls)

        state['gun_pitch'] = -0.1
        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        self.assertEqual(1, len(calls))

    def test_spg_malformed_final_proof_fails_closed(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        runtime = self.module.BotRuntime(1)
        receipt = {
            'proof_key': ('exact',),
            'fire_seq': 'not-an-integer', 'shell_index': 0,
            'origin': (0.0, 0.0, 0.0),
            'velocity': (0.0, 0.0, 1000.0),
            'shot_yaw': 0.0, 'shot_pitch': 0.0,
            'gravity': 10.0, 'max_distance': 5000.0,
            'max_time_ms': 20000, 'flight_time': 0.5,
        }

        self.assertIsNone(runtime._validated_artillery_receipt(
            receipt, descriptor, 0, 1, 0.0, 0.0, 0.5))

    def test_spg_legal_world_receipt_fires_its_natural_miss_once(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        calls = []

        def final_proof(state, target, unused_descriptor, shell_index,
                        fire_seq, shot_yaw, shot_pitch, flight_time, now):
            calls.append((
                fire_seq, shot_yaw, shot_pitch, flight_time,
                target['position'], now))
            speed = 1000.0
            horizontal = math.cos(shot_pitch)
            return {
                'proof_key': ('exact-world-path', fire_seq),
                'fire_seq': fire_seq, 'shell_index': shell_index,
                # This origin deliberately puts the proved parabola far to the
                # right of the target. The world proof is still legal: random
                # dispersion may miss and must not be compensated away.
                'origin': (25.0, 6.0, 0.0),
                'velocity': (
                    math.sin(shot_yaw) * horizontal * speed,
                    math.sin(shot_pitch) * speed,
                    math.cos(shot_yaw) * horizontal * speed),
                'shot_yaw': shot_yaw, 'shot_pitch': shot_pitch,
                'gravity': 10.0, 'max_distance': 5000.0,
                'max_time_ms': 20000, 'flight_time': flight_time,
            }

        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=final_proof)
        runtime.round_id = 7
        gun_state = self.module._BotGunState(descriptor)
        gun_state.elapsed = 10.0
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
            'shell_index': 0,
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
            'yaw': 0.0, 'speed': 0.0,
        }
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        receipt = runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0)

        self.assertIsNotNone(receipt)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, calls[0][0])
        self.assertEqual((receipt['shot_yaw'], receipt['shot_pitch']),
                         calls[0][1:3])
        terminal_x = (receipt['origin'][0] +
                      receipt['velocity'][0] * receipt['flight_time'])
        self.assertGreater(abs(terminal_x - target['position'][0]), 20.0)
        self.assertNotIn('compensation_offset', receipt)

        self.assertTrue(runtime._fire(
            state, gun_state, 1.0, descriptor, launch_receipt=receipt))
        self.assertEqual(1, state['fire_seq'])
        self.assertEqual(receipt['shot_yaw'], state['shot_yaw'])
        self.assertEqual(receipt['shot_pitch'], state['shot_pitch'])
        self.assertEqual(receipt['origin'], state['shot_origin'])
        self.assertEqual(receipt['velocity'], state['shot_velocity'])
        self.assertFalse(runtime._fire(
            state, gun_state, 1.0, descriptor, launch_receipt=receipt))
        self.assertEqual(1, state['fire_seq'])
        self.assertEqual(1, len(calls))

    def test_spg_pending_intent_freezes_moving_target_angles_and_sequence(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        launch_calls = []
        cancel_calls = []
        strategic_calls = []
        fresh = {
            'aim_position': (8.0, 1.0, 100.0),
            'yaw': 0.08, 'pitch': -0.12,
            'flight_time': 0.6, 'arc': 'low',
        }
        runtime = self.module.BotRuntime(
            1,
            ballistic_solution_probe=lambda *unused: (
                strategic_calls.append(True) or fresh),
            artillery_launch_probe=lambda *args: (
                launch_calls.append(args) or None),
            artillery_launch_cancel=lambda source: cancel_calls.append(source))
        runtime.round_id = 9
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
            'yaw': math.pi * 0.5, 'speed': 8.0,
        }
        proved = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, proved, 1.0))
        intent = runtime._artillery_intents[11]
        first_angles = (
            intent['shot_yaw'], intent['shot_pitch'],
            intent['solution']['flight_time'])
        moved = dict(target, position=(8.0, 0.0, 100.0))
        frozen = runtime._ballistic_solution(
            state, moved, descriptor, 0, 1.04)
        self.assertEqual(proved['yaw'], frozen['yaw'])
        self.assertEqual(proved['pitch'], frozen['pitch'])
        self.assertEqual(0, len(strategic_calls))

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, moved, descriptor, 0, gun_state, frozen, 1.04))
        second_angles = (
            launch_calls[1][5], launch_calls[1][6], launch_calls[1][7])
        self.assertEqual(first_angles, second_angles)
        self.assertEqual(1, launch_calls[0][4])
        self.assertEqual(1, launch_calls[1][4])

        changed_target = dict(moved, network_id=3)
        self.assertEqual(fresh, runtime._ballistic_solution(
            state, changed_target, descriptor, 0, 1.08))
        self.assertNotIn(11, runtime._artillery_intents)
        self.assertEqual([{'id': 11}], cancel_calls)

    def test_spg_pending_intent_invalidates_on_shell_seq_pose_and_deadline(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        cancelled = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *unused: None,
            artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 10
        gun_state = self.module._BotGunState(descriptor)
        base_state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }
        variants = (
            ('missing_target', lambda state, now: (
                state, 0, now, None)),
            ('dead_target', lambda state, now: (
                state, 0, now, dict(target, health=0))),
            ('shell', lambda state, now: (state, 1, now)),
            ('sequence', lambda state, now: (
                dict(state, fire_seq=1), 0, now)),
            ('pose', lambda state, now: (
                dict(state, x=0.051), 0, now)),
            ('deadline', lambda state, now: (
                state, 0, now + self.module.ARTILLERY_INTENT_SECONDS + 0.01)),
        )
        for name, mutate in variants:
            with self.subTest(name=name):
                state = dict(base_state)
                self.assertIsNone(runtime._artillery_launch_receipt(
                    state, target, descriptor, 0, gun_state,
                    solution, 1.0))
                values = mutate(state, 1.0)
                changed, shell_index, now = values[:3]
                current_target = values[3] if len(values) > 3 else target
                self.assertIsNone(runtime._active_artillery_intent(
                    changed, current_target, descriptor, shell_index, now))
                self.assertNotIn(11, runtime._artillery_intents)
                self.assertNotIn(11, runtime._artillery_reproofs)

        self.assertEqual(6, len(cancelled))

    def test_spg_pending_intent_clears_on_authority_change(self):
        cancelled = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 5
        runtime.authority_id = 1
        runtime._artillery_intents[11] = {'source': {'id': 11}}
        runtime._artillery_reproofs[11] = {'source': {'id': 11}}
        handoff = dict(self.start, bot_authority_id=2)

        self.assertEqual([], runtime.battle_start(handoff))
        self.assertEqual({}, runtime._artillery_intents)
        self.assertEqual({}, runtime._artillery_reproofs)
        self.assertEqual([{'id': 11}], cancelled)

    def test_spg_intent_timeout_clears_and_allows_a_fresh_restart(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        cancelled = []
        launches = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *args: launches.append(args),
            artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 12
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        expired_at = 1.0 + self.module.ARTILLERY_INTENT_SECONDS + 0.01
        self.assertIsNone(runtime._active_artillery_intent(
            state, target, descriptor, 0, expired_at))
        self.assertNotIn(11, runtime._artillery_reproofs)

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, expired_at))
        self.assertIn(11, runtime._artillery_intents)
        self.assertGreater(
            runtime._artillery_reproofs[11]['deadline'], expired_at)
        self.assertEqual(2, len(launches))
        self.assertEqual([{'id': 11}], cancelled)

    def test_spg_reproof_attempts_never_extend_absolute_lifetime(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        cancelled = []
        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=lambda *unused: None,
            artillery_launch_cancel=lambda source: cancelled.append(source))
        runtime.round_id = 12
        gun_state = self.module._BotGunState(descriptor)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (0.0, 0.0, 100.0),
        }
        solution = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, target, descriptor, 0, gun_state, solution, 1.0))
        reproof = runtime._artillery_reproofs[11]
        absolute = 1.0 + self.module.ARTILLERY_TOTAL_PROOF_SECONDS
        self.assertEqual(absolute, reproof['absolute_deadline'])

        reproof['attempts'] = 4
        for now in (30.0, 80.0, 120.0):
            runtime._artillery_intents.pop(11, None)
            self.assertIsNotNone(runtime._create_artillery_intent(
                state, target, descriptor, 0, gun_state, solution, now))
            self.assertLessEqual(reproof['deadline'], absolute)
        runtime._artillery_intents.pop(11, None)
        self.assertIsNone(runtime._active_artillery_reproof(
            state, target, descriptor, 0, absolute + 0.01))
        self.assertNotIn(11, runtime._artillery_reproofs)

    def test_spg_reproofs_stale_undispersed_aim_then_fires_same_sequence(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        calls = []

        def exact_world_proof(
                state, target, unused_descriptor, shell_index, fire_seq,
                shot_yaw, shot_pitch, flight_time, now):
            calls.append((
                fire_seq, shot_yaw, shot_pitch, flight_time,
                target['position'], now))
            speed = 1000.0
            horizontal = math.cos(shot_pitch)
            return {
                'proof_key': ('exact-world-path', len(calls), fire_seq),
                'fire_seq': fire_seq, 'shell_index': shell_index,
                # The origin preserves a large natural random miss. Aim
                # staleness is evaluated from the undispersed solution only.
                'origin': (25.0, 6.0, 0.0),
                'velocity': (
                    math.sin(shot_yaw) * horizontal * speed,
                    math.sin(shot_pitch) * speed,
                    math.cos(shot_yaw) * horizontal * speed),
                'shot_yaw': shot_yaw, 'shot_pitch': shot_pitch,
                'gravity': 10.0, 'max_distance': 5000.0,
                'max_time_ms': 20000, 'flight_time': flight_time,
            }

        runtime = self.module.BotRuntime(
            1, artillery_launch_probe=exact_world_proof)
        runtime.round_id = 17
        gun_state = self.module._BotGunState(descriptor)
        gun_state.elapsed = 10.0
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': -0.1,
            'gun_aligned': True, 'fire_seq': 0, 'speed': 0.0,
            'critical': {}, 'profile': {'class_tag': 'SPG'},
        }
        moved_target = {
            'kind': 'human', 'network_id': 2, 'alive': True,
            'position': (10.0, 0.0, 100.0),
            'yaw': math.pi * 0.5, 'speed': 8.0,
        }
        stale = {
            'aim_position': (0.0, 1.0, 100.0),
            'yaw': 0.0, 'pitch': -0.1,
            'flight_time': 0.5, 'arc': 'low',
        }

        self.assertIsNone(runtime._artillery_launch_receipt(
            state, moved_target, descriptor, 0, gun_state, stale, 1.0))
        self.assertEqual(0, state['fire_seq'])
        reproof = runtime._artillery_reproofs[11]
        self.assertEqual(1, reproof['attempts'])
        self.assertGreater(
            reproof['last_aim_staleness'],
            self.module.ARTILLERY_AIM_STALENESS_METRES)
        self.assertNotIn('compensation_offset', reproof)
        expected = self.module._dispersed_barrel_angles(
            state['id'], runtime.round_id, 1,
            stale['yaw'], stale['pitch'],
            self.module._effective_shot_dispersion(
                gun_state, state, descriptor))
        self.assertEqual((1,) + expected, calls[0][:3])

        refreshed = runtime._ballistic_solution(
            state, moved_target, descriptor, 0, 1.04)
        self.assertIsNotNone(refreshed)
        state['aim_yaw'] = refreshed['yaw']
        state['gun_pitch'] = refreshed['pitch']
        receipt = runtime._artillery_launch_receipt(
            state, moved_target, descriptor, 0, gun_state,
            refreshed, 1.04)

        self.assertIsNotNone(receipt)
        self.assertEqual([1, 1], [call[0] for call in calls])
        refreshed_expected = self.module._dispersed_barrel_angles(
            state['id'], runtime.round_id, 1,
            refreshed['yaw'], refreshed['pitch'],
            self.module._effective_shot_dispersion(
                gun_state, state, descriptor))
        self.assertEqual(refreshed_expected, calls[1][1:3])
        self.assertNotIn('compensation_offset', receipt)
        self.assertEqual(0, state['fire_seq'])
        self.assertTrue(runtime._fire(
            state, gun_state, 1.0, descriptor,
            launch_receipt=receipt))
        self.assertEqual(1, state['fire_seq'])
        self.assertEqual(receipt['shot_yaw'], state['shot_yaw'])
        self.assertEqual(receipt['shot_pitch'], state['shot_pitch'])

    def test_spg_motion_changes_reproof_only_the_undispersed_aim(self):
        descriptor = _combat_descriptor(dispersion=0.03)
        cases = (
            ('stopped', {
                'position': (0.0, 0.0, 100.0),
                'yaw': 0.0, 'speed': 0.0,
            }, (8.0, 1.0, 100.0), 8.0),
            ('reversed', {
                'position': (0.0, 0.0, 100.0),
                'yaw': -math.pi * 0.5, 'speed': 8.0,
            }, (8.0, 1.0, 100.0), 16.0),
            ('height_changed', {
                'position': (0.0, 5.0, 100.0),
                'yaw': 0.0, 'speed': 0.0,
            }, (0.0, 1.0, 100.0), 5.0),
        )
        for name, motion, intended_impact, expected_staleness in cases:
            with self.subTest(name=name):
                runtime = self.module.BotRuntime(1)
                state = {
                    'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                    'yaw': 0.0, 'fire_seq': 0,
                }
                target = dict(
                    motion, kind='human', network_id=2, alive=True)
                reproof = {
                    'source': {'id': 11},
                    'source_pose': (0.0, 0.0, 0.0, 0.0),
                    'target_identity': ('human', 2),
                    'shell_index': 0, 'fire_seq': 1,
                    'physical': self.module._shot_ballistics(descriptor, 0),
                    'proof_latency': 0.0, 'attempts': 0,
                    'created': 1.0, 'deadline': 61.0,
                    'absolute_deadline': 121.0,
                }
                intent = {
                    'source': {'id': 11}, 'created': 1.0,
                    'solution': {
                        'arc': 'low', 'aim_position': intended_impact,
                    },
                }
                runtime._artillery_reproofs[11] = reproof
                runtime._artillery_intents[11] = intent

                self.assertTrue(runtime._reject_stale_artillery_receipt(
                    state, target, descriptor, 0, intent,
                    {'flight_time': 1.0}, 2.0))

                self.assertNotIn(11, runtime._artillery_intents)
                self.assertEqual(1, reproof['attempts'])
                self.assertAlmostEqual(
                    expected_staleness, reproof['last_aim_staleness'])
                self.assertNotIn('compensation_offset', reproof)

    def test_moving_spg_reproofs_converge_at_20_and_24_fps(self):
        from gui.mods.offline_lan_0922.artillery_controller import (
            ArtilleryController)

        for fps in (20, 24):
            for count in (1, 2):
                with self.subTest(fps=fps, count=count):
                    controller = ArtilleryController(maximum_step=0.12)
                    descriptor = _combat_descriptor(dispersion=0.03)
                    descriptor.gun.shots = ({
                        'shell': {'effectsIndex': 0},
                        'speed': 425.0, 'gravity': 143.0,
                        'maxDistance': 10000.0,
                    },)
                    descriptor.gun.pitchLimits = {
                        'absolute': (-0.8, 0.15)}
                    gun_by_id = {}
                    proof_calls = []

                    def strategic(source, target, installed, shell, now):
                        return controller.solution(
                            source, target, installed, shell, now)

                    def exact_launch(
                            source, target, installed, shell, fire_seq,
                            shot_yaw, shot_pitch, flight_time, now):
                        source_id = int(source['id'])
                        proof_calls.append((
                            source_id, fire_seq, shot_yaw, shot_pitch))
                        expected = self.module._dispersed_barrel_angles(
                            source_id, 77, fire_seq,
                            source['aim_yaw'], source['gun_pitch'],
                            self.module._effective_shot_dispersion(
                                gun_by_id[source_id], source, installed))
                        self.assertEqual(
                            expected, (shot_yaw, shot_pitch))
                        origin = (
                            source['x'] + 0.3, source['y'] + 1.7,
                            source['z'] - 0.2)
                        unused_ready, receipt = controller.request_launch(
                            source, target, installed, shell, fire_seq,
                            origin, shot_yaw, shot_pitch, flight_time, now)
                        return receipt

                    runtime = self.module.BotRuntime(
                        1, ballistic_solution_probe=strategic,
                        artillery_launch_probe=exact_launch,
                        artillery_launch_cancel=controller.cancel_launch)
                    runtime.round_id = 77
                    states = []
                    guns = []
                    for index in range(count):
                        state = {
                            'id': 11 + index,
                            'x': float(index), 'y': 0.0, 'z': 0.0,
                            'yaw': 0.0, 'speed': 0.0, 'fire_seq': 0,
                            'aim_yaw': 0.0, 'gun_pitch': 0.0,
                            'gun_aligned': True, 'critical': {},
                            'profile': {'class_tag': 'SPG'},
                        }
                        states.append(state)
                        gun = self.module._BotGunState(descriptor)
                        gun.elapsed = 100.0
                        guns.append(gun)
                        gun_by_id[state['id']] = gun

                    fired = {}
                    reproofed = dict((state['id'], 0) for state in states)
                    for frame in range(1, fps * 5 + 1):
                        now = frame / float(fps)
                        probe_calls = [0]

                        def clear_probe(unused_start, unused_end):
                            probe_calls[0] += 1
                            return None

                        used = controller.advance(now, 4, clear_probe)
                        self.assertEqual(probe_calls[0], used)
                        self.assertLessEqual(used, 4)
                        for index, (state, gun) in enumerate(
                                zip(states, guns)):
                            if state['id'] in fired:
                                continue
                            target = {
                                'kind': 'human',
                                'network_id': 2 + index,
                                'alive': True,
                                'position': (8.0 * now, 0.0, 560.0),
                                'yaw': math.pi * 0.5, 'speed': 8.0,
                            }
                            solution = runtime._ballistic_solution(
                                state, target, descriptor, 0, now)
                            if solution is None:
                                continue
                            state['aim_yaw'] = solution['yaw']
                            state['gun_pitch'] = solution['pitch']
                            state['gun_aligned'] = True
                            before = runtime._artillery_reproofs.get(
                                state['id'], {}).get('attempts', 0)
                            receipt = runtime._artillery_launch_receipt(
                                state, target, descriptor, 0, gun,
                                solution, now)
                            after = runtime._artillery_reproofs.get(
                                state['id'], {}).get('attempts', 0)
                            if after > before:
                                reproofed[state['id']] += 1
                                self.assertGreater(
                                    runtime._artillery_reproofs[
                                        state['id']]['last_aim_staleness'],
                                    self.module.
                                    ARTILLERY_AIM_STALENESS_METRES)
                                self.assertNotIn(
                                    'compensation_offset',
                                    runtime._artillery_reproofs[state['id']])
                            self.assertEqual(0, state['fire_seq'])
                            if receipt is None:
                                continue
                            self.assertEqual(1, receipt['fire_seq'])
                            self.assertNotIn(
                                'compensation_offset', receipt)
                            self.assertTrue(runtime._fire(
                                state, gun, 1.0, descriptor,
                                launch_receipt=receipt))
                            self.assertEqual(1, state['fire_seq'])
                            self.assertEqual(
                                receipt['shot_yaw'], state['shot_yaw'])
                            self.assertEqual(
                                receipt['shot_pitch'], state['shot_pitch'])
                            self.assertEqual(
                                receipt['origin'], state['shot_origin'])
                            self.assertEqual(
                                receipt['velocity'], state['shot_velocity'])
                            fired[state['id']] = now
                            runtime._cancel_artillery_intent(state['id'])
                        if len(fired) == count:
                            break

                    self.assertEqual(count, len(fired))
                    self.assertLessEqual(now, 5.0)
                    if count > 1:
                        self.assertTrue(any(
                            reproofed[state['id']] >= 1
                            for state in states))
                    self.assertTrue(proof_calls)
                    self.assertTrue(all(
                        call[1] == 1 for call in proof_calls))

    def _run_catalog_max_spg_proof_case(self, fps, direction):
        from gui.mods.offline_lan_0922.artillery_controller import (
            ArtilleryController)

        controller = ArtilleryController(maximum_step=0.12)
        descriptor = _combat_descriptor(dispersion=0.0001)
        # The pinned #1513 non-secret SPG catalog has shell speeds 265..510,
        # gravity 125..190 and maxDistance 10000. This FV3805/FV206 5.5-inch
        # shell takes about 5.66 seconds on the flat high arc used here. The
        # moving contact uses the catalogue's 79 km/h maximum plus the copied
        # 1.05 downhill overspeed, about 23.04 m/s.
        descriptor.gun.shots = ({
            'shell': {'effectsIndex': 0},
            'speed': 440.0, 'gravity': 146.0,
            'maxDistance': 10000.0,
        },)
        descriptor.gun.pitchLimits = {
            'absolute': (-math.radians(70.0), math.radians(5.0))}

        strategic_calls = {}
        proof_calls = []
        gun_by_id = {}

        def strategic(source, target, installed, shell, now):
            source_id = int(source['id'])
            strategic_calls[source_id] = (
                strategic_calls.get(source_id, 0) + 1)
            return controller.solution(
                source, target, installed, shell, now)

        def exact_launch(
                source, target, installed, shell, fire_seq,
                shot_yaw, shot_pitch, flight_time, now):
            source_id = int(source['id'])
            proof_calls.append((
                source_id, fire_seq, shot_yaw, shot_pitch))
            expected = self.module._dispersed_barrel_angles(
                source_id, 5, fire_seq,
                source['aim_yaw'], source['gun_pitch'],
                self.module._effective_shot_dispersion(
                    gun_by_id[source_id], source, installed))
            self.assertEqual(expected, (shot_yaw, shot_pitch))
            unused_ready, receipt = controller.request_launch(
                source, target, installed, shell, fire_seq,
                (source['x'], source['y'] + 1.5, source['z']),
                shot_yaw, shot_pitch, flight_time, now)
            return receipt

        runtime = self.module.BotRuntime(
            1, ballistic_solution_probe=strategic,
            artillery_launch_probe=exact_launch,
            artillery_launch_cancel=controller.cancel_launch)
        runtime.round_id = 5
        states = []
        guns = []
        for index in range(8):
            state = {
                'id': 11 + index,
                'x': float(index), 'y': 0.0, 'z': 0.0,
                'yaw': 0.0, 'speed': 0.0, 'fire_seq': 0,
                'aim_yaw': 0.0, 'gun_pitch': 0.0,
                'gun_aligned': True, 'critical': {},
                'profile': {'class_tag': 'SPG'},
            }
            states.append(state)
            gun = self.module._BotGunState(descriptor)
            gun.elapsed = 100.0
            guns.append(gun)
            gun_by_id[state['id']] = gun

        fired = {}
        reproofed = dict((state['id'], 0) for state in states)
        strategic_calls_at_first_reproof = {}
        catalog_max_speed = direction * 79.0 / 3.6 * 1.05
        for frame in range(1, fps * 20 + 1):
            now = frame / float(fps)
            probe_calls = [0]

            def low_arc_wall(start, end):
                probe_calls[0] += 1
                wall_z = 400.0
                if ((start[2] - wall_z) * (end[2] - wall_z) <= 0.0 and
                        abs(end[2] - start[2]) > 1e-9):
                    fraction = ((wall_z - start[2]) /
                                (end[2] - start[2]))
                    height = (
                        start[1] + (end[1] - start[1]) * fraction)
                    if height < 300.0:
                        return (0.0, height, wall_z)
                return None

            used = controller.advance(now, 4, low_arc_wall)
            self.assertEqual(probe_calls[0], used)
            self.assertLessEqual(used, 4)
            for index, (state, gun) in enumerate(zip(states, guns)):
                if state['id'] in fired:
                    continue
                target = {
                    'kind': 'human', 'network_id': 2 + index,
                    'alive': True,
                    'position': (catalog_max_speed * now, 0.0, 853.0),
                    'yaw': direction * math.pi * 0.5,
                    'speed': abs(catalog_max_speed),
                }
                solution = runtime._ballistic_solution(
                    state, target, descriptor, 0, now)
                if solution is None:
                    continue
                self.assertEqual('high', solution['arc'])
                self.assertGreater(solution['flight_time'], 5.0)
                self.assertLess(solution['flight_time'], 6.0)
                state['aim_yaw'] = solution['yaw']
                state['gun_pitch'] = solution['pitch']
                state['gun_aligned'] = True
                before = runtime._artillery_reproofs.get(
                    state['id'], {}).get('attempts', 0)
                receipt = runtime._artillery_launch_receipt(
                    state, target, descriptor, 0, gun, solution, now)
                after = runtime._artillery_reproofs.get(
                    state['id'], {}).get('attempts', 0)
                if after > before:
                    reproofed[state['id']] += 1
                    if before == 0:
                        strategic_calls_at_first_reproof[state['id']] = (
                            strategic_calls[state['id']])
                    self.assertGreater(
                        runtime._artillery_reproofs[
                            state['id']]['last_aim_staleness'],
                        self.module.ARTILLERY_AIM_STALENESS_METRES)
                    self.assertNotIn(
                        'compensation_offset',
                        runtime._artillery_reproofs[state['id']])
                self.assertEqual(0, state['fire_seq'])
                if receipt is None:
                    continue
                self.assertEqual(1, receipt['fire_seq'])
                self.assertNotIn('compensation_offset', receipt)
                self.assertTrue(runtime._fire(
                    state, gun, 1.0, descriptor,
                    launch_receipt=receipt))
                self.assertEqual(1, state['fire_seq'])
                self.assertEqual(receipt['shot_yaw'], state['shot_yaw'])
                self.assertEqual(
                    receipt['shot_pitch'], state['shot_pitch'])
                self.assertEqual('exact_launch', receipt['arc'])
                fired[state['id']] = (now, after)
                runtime._cancel_artillery_intent(state['id'])
            if len(fired) == 8:
                break

        self.assertEqual(8, len(fired))
        self.assertLessEqual(now, 20.0)
        self.assertTrue(all(
            value >= 1 for value in reproofed.values()))
        self.assertEqual(
            strategic_calls_at_first_reproof, strategic_calls)
        self.assertTrue(proof_calls)
        self.assertTrue(all(call[1] == 1 for call in proof_calls))
        self.assertEqual({}, runtime._artillery_intents)
        self.assertEqual({}, runtime._artillery_reproofs)
        return fired, reproofed

    def test_eight_catalog_max_flight_reproofs_finish_with_shared_budget(self):
        fired, reproofed = self._run_catalog_max_spg_proof_case(24, 1.0)

        self.assertEqual(8, len(fired))
        self.assertTrue(all(
            value >= 1 for value in reproofed.values()))

    def test_catalog_max_spg_reproofs_converge_across_frame_rates(self):
        for fps in (20, 30, 60):
            for direction in (-1.0, 1.0):
                with self.subTest(fps=fps, direction=direction):
                    fired, reproofed = (
                        self._run_catalog_max_spg_proof_case(
                            fps, direction))
                    self.assertEqual(8, len(fired))
                    self.assertTrue(all(
                        value >= 1 for value in reproofed.values()))

    def test_bot_dispersion_expands_for_move_hull_and_turret_motion(self):
        descriptor = _combat_descriptor(dispersion=0.01)
        descriptor.chassis.shotDispersionFactors = (0.2, 0.4)
        descriptor.gun.shotDispersionFactors = {
            'afterShot': 3.0, 'afterShotInBurst': 1.5,
            'turretRotation': 0.5,
        }
        descriptor.gun.aimingTime = 2.0
        gun_state = self.module._BotGunState(descriptor)

        gun_state.tick_dispersion(
            0.04, move_speed=5.0, rotation_speed=0.25,
            turret_speed=0.4)

        self.assertAlmostEqual(
            gun_state.fully_aimed_dispersion *
            math.sqrt(1.0 + 1.0 ** 2 + 0.1 ** 2 + 0.2 ** 2),
            gun_state.dispersion)

    def test_bot_first_high_fps_tick_keeps_exact_turret_angular_speed(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1000.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state['turret_yaw'] = -0.001
        state['aim_yaw'] = -0.001
        gun_state = runtime._gun_states[11]
        recorded = []
        gun_state.tick_dispersion = lambda *args: recorded.append(args)
        step = 1.0 / 240.0

        runtime.update(step, 1.0, players=[{
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
            'effective_params': _effective_params_snapshot(),
        }])

        self.assertEqual(1, len(recorded))
        turret_delta = abs(self.module._angle_delta(
            state['turret_yaw'], -0.001))
        self.assertGreater(turret_delta, 0.0)
        self.assertAlmostEqual(
            turret_delta, recorded[0][3] * step)

    def test_bot_dispersion_converges_with_1513_exponential_aiming_law(self):
        descriptor = _combat_descriptor(dispersion=0.01)
        descriptor.chassis.shotDispersionFactors = (0.2, 0.4)
        descriptor.gun.shotDispersionFactors = {
            'afterShot': 3.0, 'afterShotInBurst': 1.5,
            'turretRotation': 0.5,
        }
        descriptor.gun.aimingTime = 2.0
        gun_state = self.module._BotGunState(descriptor)
        gun_state.current_dispersion_factor = 8.0
        gun_state.aiming_start_factor = 8.0
        gun_state.aiming_elapsed = 0.0
        gun_state.dispersion = gun_state.fully_aimed_dispersion * 8.0

        gun_state.tick_dispersion(
            0.5, move_speed=0.0, rotation_speed=0.0,
            turret_speed=0.0)

        self.assertAlmostEqual(
            max(gun_state.fully_aimed_dispersion,
                gun_state.fully_aimed_dispersion * 8.0 *
                math.exp(-0.5 / gun_state.aiming_time)),
            gun_state.dispersion)

    def test_bot_shot_bloom_expands_to_after_shot_ideal_without_stacking(self):
        descriptor = _combat_descriptor(dispersion=0.01)
        descriptor.gun.shotDispersionFactors = {
            'afterShot': 3.0, 'afterShotInBurst': 1.5,
            'turretRotation': 0.0,
        }
        descriptor.gun.aimingTime = 2.0
        descriptor.gun.burst = (1, 0.0)
        gun_state = self.module._BotGunState(descriptor)
        expected = (gun_state.fully_aimed_dispersion *
                    math.sqrt(1.0 + 3.0 ** 2))

        gun_state.commit_shot_bloom()
        self.assertAlmostEqual(expected, gun_state.dispersion)

        # #1513 clamps the current aiming factor to the shot ideal. A second
        # event before any convergence does not add another independent jump.
        gun_state.commit_shot_bloom()
        self.assertAlmostEqual(expected, gun_state.dispersion)

    def test_bot_burst_launches_and_debits_every_physical_round(self):
        descriptor = _combat_descriptor(
            reload_time=4.0, clip=(5, 2.0), dispersion=0.01,
            max_ammo=20)
        descriptor.gun.burst = (3, 0.1)
        descriptor.gun.shotDispersionFactors = {
            'afterShot': 4.0, 'afterShotInBurst': 1.0,
            'turretRotation': 0.0,
        }
        runtime = self.module.BotRuntime(
            1, friendly_lane_probe=lambda *unused: True)
        runtime.round_id = 5
        runtime._descriptors[11] = descriptor
        state = {
            'id': 11, 'alive': True, 'health': 1000, 'fire_seq': 0,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
            'aim_yaw': 0.0, 'turret_yaw': 0.0, 'gun_pitch': -0.01,
            'critical': {}, 'profile': {},
        }
        target = {
            'id': 2, 'network_id': 2, 'kind': 'human', 'alive': True,
            'position': (0.0, 1.0, 100.0),
        }
        solution = {'flight_time': 0.5}
        gun_state = self.module._BotGunState(descriptor)
        gun_state.elapsed = 10.0
        ammo_state = self.module._BotAmmoState(descriptor, {}, state)
        runtime._gun_states[11] = gun_state
        runtime._ammo_states[11] = ammo_state
        initial_ammo = ammo_state.remaining[ammo_state.loaded]
        preview = runtime._direct_launch_preview(
            state, descriptor, ammo_state.loaded, gun_state, solution)

        self.assertTrue(runtime._fire(
            state, gun_state, 1.0, descriptor,
            ammo_state=ammo_state, launch_preview=preview))
        self.assertEqual(0, runtime._advance_active_burst(
            state, gun_state, ammo_state, 1.0, descriptor,
            target, solution, 0.099, set()))
        self.assertEqual(1, runtime._advance_active_burst(
            state, gun_state, ammo_state, 1.0, descriptor,
            target, solution, 0.001, set()))
        self.assertEqual(1, runtime._advance_active_burst(
            state, gun_state, ammo_state, 1.0, descriptor,
            target, solution, 0.1, set()))

        self.assertEqual([1, 2, 3], [
            launch['fire_seq'] for launch in runtime._pending_launches])
        self.assertEqual([0, 1, 2], [
            launch['burst_index'] for launch in runtime._pending_launches])
        self.assertEqual(initial_ammo - 3,
                         ammo_state.remaining[ammo_state.loaded])
        self.assertEqual(2, gun_state.clip)
        self.assertFalse(runtime._burst_states[11].active)

    def test_stalled_burst_freezes_each_logical_time_pose_and_muzzle(self):
        for stall, count in ((0.2, 3), (1.0, 11)):
            with self.subTest(stall=stall):
                descriptor = _combat_descriptor(
                    reload_time=4.0, clip=(count + 1, 2.0),
                    dispersion=0.01, max_ammo=count + 2)
                descriptor.gun.burst = (count, 0.1)
                descriptor.gun.shotDispersionFactors = {
                    'afterShot': 4.0, 'afterShotInBurst': 1.0,
                    'turretRotation': 0.0,
                }
                runtime = self.module.BotRuntime(
                    1, friendly_lane_probe=lambda *unused: True,
                    direct_launch_origin_probe=lambda source, *unused: (
                        source['x'], source['y'] + 1.0, source['z']))
                runtime.round_id = 5
                runtime.authority_id = 1
                runtime.adapter = object()
                runtime._descriptors[11] = descriptor
                state = {
                    'id': 11, 'alive': True, 'health': 1000,
                    'fire_seq': 0, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                    'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
                    'aim_yaw': 0.0, 'turret_yaw': 0.0,
                    'gun_pitch': -0.01, 'critical': {}, 'profile': {},
                }
                target = {
                    'id': 2, 'network_id': 2, 'kind': 'human',
                    'alive': True, 'position': (0.0, 1.0, 100.0),
                }
                solution = {'flight_time': 0.5}
                gun_state = self.module._BotGunState(descriptor)
                gun_state.elapsed = 10.0
                ammo_state = self.module._BotAmmoState(
                    descriptor, {}, state)
                runtime._gun_states[11] = gun_state
                runtime._ammo_states[11] = ammo_state
                preview = runtime._direct_launch_preview(
                    state, descriptor, ammo_state.loaded, gun_state,
                    solution)
                self.assertTrue(runtime._fire(
                    state, gun_state, 1.0, descriptor,
                    ammo_state=ammo_state, launch_preview=preview,
                    launch_time_us=0))

                substeps = []

                def simulate(step, unused_now, unused_players,
                             unused_neighbours):
                    start_us = runtime._sample_time_us
                    duration_us = max(1, int(round(step * 1000000.0)))
                    end_us = start_us + duration_us
                    state['z'] += 10.0 * step
                    substeps.append(step)
                    runtime._advance_active_burst(
                        state, gun_state, ammo_state, 1.0, descriptor,
                        target, solution, step, set(), start_us, end_us)
                    runtime._sample_time_us = end_us
                    return []

                runtime._update_once = simulate
                runtime.update(stall, stall)

                launches = runtime._pending_launches
                self.assertEqual(count, len(launches))
                self.assertEqual(
                    [index * 100000 for index in range(count)],
                    [launch['launch_time_us'] for launch in launches])
                self.assertEqual(
                    [round(float(index), 6) for index in range(count)],
                    [round(launch['shot_origin'][2], 6)
                     for launch in launches])
                self.assertEqual(
                    [round(float(index), 6) for index in range(count)],
                    [round(launch['launch_pose'][2], 6)
                     for launch in launches])
                self.assertTrue(all(
                    step <= 0.100000001 for step in substeps))

    def test_worker_slow_fps_catchup_preserves_every_burst_edge(self):
        count = 11
        for fps in (5, 4, 2, 1):
            with self.subTest(fps=fps):
                descriptor = _combat_descriptor(
                    reload_time=4.0, clip=(count + 1, 2.0),
                    dispersion=0.01, max_ammo=count + 2)
                descriptor.gun.burst = (count, 0.1)
                descriptor.gun.shotDispersionFactors = {
                    'afterShot': 4.0, 'afterShotInBurst': 1.0,
                    'turretRotation': 0.0,
                }
                runtime = self.module.BotRuntime(
                    1, friendly_lane_probe=lambda *unused: True,
                    direct_launch_origin_probe=lambda source, *unused: (
                        source['x'], source['y'] + 1.0, source['z']),
                    control_seconds=self.module.WORKER_CONTROL_SECONDS)
                runtime.round_id = 5
                runtime.authority_id = 1
                runtime.adapter = object()
                runtime._descriptors[11] = descriptor
                state = {
                    'id': 11, 'alive': True, 'health': 1000,
                    'fire_seq': 0, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                    'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
                    'aim_yaw': 0.0, 'turret_yaw': 0.0,
                    'gun_pitch': -0.01, 'critical': {}, 'profile': {},
                }
                target = {
                    'id': 2, 'network_id': 2, 'kind': 'human',
                    'alive': True, 'position': (0.0, 1.0, 100.0),
                }
                solution = {'flight_time': 0.5}
                gun_state = self.module._BotGunState(descriptor)
                gun_state.elapsed = 10.0
                ammo_state = self.module._BotAmmoState(
                    descriptor, {}, state)
                runtime._gun_states[11] = gun_state
                runtime._ammo_states[11] = ammo_state
                preview = runtime._direct_launch_preview(
                    state, descriptor, ammo_state.loaded, gun_state,
                    solution)
                self.assertTrue(runtime._fire(
                    state, gun_state, 1.0, descriptor,
                    ammo_state=ammo_state, launch_preview=preview,
                    launch_time_us=0))

                callback_calls = []
                current_callback = [None]

                def simulate(step, unused_now, unused_players,
                             unused_neighbours):
                    start_us = runtime._sample_time_us
                    duration_us = max(
                        1, int(round(step * 1000000.0)))
                    end_us = start_us + duration_us
                    state['z'] += 10.0 * step
                    callback_calls[current_callback[0]].append((
                        step,
                        getattr(runtime, '_refresh_control_this_step', True)))
                    runtime._advance_active_burst(
                        state, gun_state, ammo_state, 1.0, descriptor,
                        target, solution, step, set(), start_us, end_us)
                    runtime._sample_time_us = end_us
                    return []

                runtime._update_once = simulate
                frame_seconds = 1.0 / float(fps)
                accumulator_us = []
                for frame in range(fps):
                    current_callback[0] = frame
                    callback_calls.append([])
                    runtime.update(
                        frame_seconds, (frame + 1) * frame_seconds)
                    accumulator_us.append(int(round(
                        runtime._accumulator * 1000000.0)))

                launches = runtime._pending_launches
                callback_elapsed_us = [
                    int(round(sum(step for step, unused in calls) *
                              1000000.0))
                    for calls in callback_calls]
                refresh_counts = [sum(
                    1 for unused, refresh in calls if refresh)
                    for calls in callback_calls]
                all_steps = [
                    step for calls in callback_calls
                    for step, unused in calls]
                self.assertEqual({
                    'accumulator_us': [0] * fps,
                    'sample_time_us': 1000000,
                    'callback_elapsed_us': [
                        int(round(frame_seconds * 1000000.0))
                    ] * fps,
                    'refresh_counts': [1] * fps,
                    'refresh_ordered': True,
                    'step_bound_held': True,
                    'burst_active': False,
                    'launch_time_us': [
                        index * 100000 for index in range(count)],
                    'shot_origin_z': [
                        float(index) for index in range(count)],
                    'launch_pose_z': [
                        float(index) for index in range(count)],
                }, {
                    'accumulator_us': accumulator_us,
                    'sample_time_us': runtime._sample_time_us,
                    'callback_elapsed_us': callback_elapsed_us,
                    'refresh_counts': refresh_counts,
                    'refresh_ordered': all(
                        calls and calls[0][1] and
                        not any(refresh for unused, refresh in calls[1:])
                        for calls in callback_calls),
                    'step_bound_held': bool(all_steps) and all(
                        step <= (self.module.MAX_CONTROL_ELAPSED_SECONDS +
                                 1.0e-9)
                        for step in all_steps),
                    'burst_active': runtime._burst_states[11].active,
                    'launch_time_us': [
                        launch['launch_time_us'] for launch in launches],
                    'shot_origin_z': [
                        round(launch['shot_origin'][2], 6)
                        for launch in launches],
                    'launch_pose_z': [
                        round(launch['launch_pose'][2], 6)
                        for launch in launches],
                })

    def test_siege_transition_edge_locks_pose_in_its_starting_tick(self):
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        state.update({
            'x': 3.0, 'y': 0.0, 'z': 4.0, 'yaw': 0.25,
            'speed': 8.0, 'grounded_once': True,
            'push_x': 2.0, 'push_z': -1.0,
        })
        self.runtime._turn_speeds[11] = 0.4
        self.runtime.adapter = _FixedAdapter({
            'target_yaw': 1.0, 'throttle': 1.0, 'turn': 1.0,
            'movement_intent': True, 'shell_index': 0,
            'fire_allowed': False, 'target_id': None,
            'fire_range': 500.0,
        })

        def begin_transition(bot, command, unused_target, unused_step):
            bot['siege_state'] = self.module.siege_mechanics.SWITCHING_ON
            bot['siege_time_left_ms'] = 2000
            bot['siege_transition_total_ms'] = 2000
            command['fire_allowed'] = False
            return True

        def external_contact(unused_players, unused_now, unused_step):
            state['x'] += 1.0
            state['z'] -= 1.0
            state['yaw'] += 0.5
            state['speed'] = 3.0
            return []

        self.runtime._update_bot_siege_intent = begin_transition
        self.runtime._resolve_tank_contacts = external_contact

        self.runtime.update(0.04, 1.0)

        self.assertEqual((3.0, 4.0, 0.25),
                         (state['x'], state['z'], state['yaw']))
        self.assertEqual(0.0, state['speed'])
        self.assertEqual(0, state['movement_dir'])
        self.assertEqual(0, state['rotation_dir'])
        self.assertEqual(0.0, self.runtime._turn_speeds[11])
        self.assertEqual(0.0, state['push_x'])
        self.assertEqual(0.0, state['push_z'])

    def test_siege_completion_keeps_its_previous_switching_tick_locked(self):
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        descriptor = self.runtime._descriptors[11]
        self.runtime._descriptor_pairs[11] = (descriptor, descriptor)
        state.update({
            'x': 3.0, 'y': 0.0, 'z': 4.0, 'yaw': 0.25,
            'speed': 8.0, 'grounded_once': True,
            'siege_state': self.module.siege_mechanics.SWITCHING_ON,
            'siege_time_left_ms': 10, '_siege_time_left': 0.01,
            'siege_transition_total_ms': 2000,
            '_siege_transition_total': 2.0,
            '_siege_intent': True,
        })
        self.runtime.adapter = _FixedAdapter({
            'target_yaw': 1.0, 'throttle': 1.0, 'turn': 1.0,
            'movement_intent': True, 'shell_index': 0,
            'fire_allowed': False, 'target_id': None,
            'fire_range': 500.0,
        })
        self.runtime._update_bot_siege_intent = (
            lambda *unused_args: False)

        self.runtime.update(0.04, 1.0)

        self.assertEqual(self.module.siege_mechanics.ENABLED,
                         state['siege_state'])
        self.assertEqual((3.0, 4.0, 0.25),
                         (state['x'], state['z'], state['yaw']))
        self.assertEqual(0.0, state['speed'])
        self.assertEqual(0, state['movement_dir'])
        self.assertEqual(0, state['rotation_dir'])
        self.assertEqual(0.0, self.runtime._turn_speeds[11])

    def test_bot_fire_scatters_with_the_current_dynamic_circle(self):
        descriptor = _combat_descriptor(dispersion=0.01)
        descriptor.gun.shotDispersionFactors = {
            'afterShot': 3.0, 'afterShotInBurst': 1.5,
            'turretRotation': 0.0,
        }
        descriptor.gun.aimingTime = 2.0
        gun_state = self.module._BotGunState(descriptor)
        gun_state.current_dispersion_factor = 4.0
        gun_state.aiming_start_factor = 4.0
        gun_state.dispersion = 0.04
        gun_state.elapsed = 10.0
        state = {
            'id': 11, 'fire_seq': 0, 'aim_yaw': 0.4,
            'gun_pitch': -0.1, 'critical': {},
        }
        sigmas = []

        class RecordingRandom(object):
            def __init__(self, unused_seed):
                pass

            def gauss(self, mean, sigma):
                sigmas.append((mean, sigma))
                return 0.0

            @staticmethod
            def uniform(minimum, unused_maximum):
                return minimum

        original_random = self.module.random.Random
        self.module.random.Random = RecordingRandom
        try:
            self.assertTrue(self.module.BotRuntime(1)._fire(
                state, gun_state, 1.0, descriptor))
        finally:
            self.module.random.Random = original_random

        self.assertEqual([(0.0, 0.02)], sigmas)
        self.assertEqual(1, state['fire_seq'])

    def test_installed_gun_dispersion_sets_bounded_two_sigma_cone(self):
        descriptor = _combat_descriptor(dispersion=0.012)
        gun_state = self.module._BotGunState(descriptor)
        critical = {
            'devices': [{
                'name': 'gunHealth', 'hp': 10.0, 'max_hp': 54.0,
                'state': 'critical',
            }],
            'destroyed': [], 'crew_ko': ['gunner1'],
        }
        state = {
            'id': 11, 'fire_seq': 0, 'aim_yaw': 0.4,
            'gun_pitch': -0.1, 'critical': critical,
        }

        # Default-crew base x a dead gunner x a damaged gun 2.0.
        from gui.mods.offline_lan_0922 import device_damage
        effective_base = gun_state.fully_aimed_dispersion
        self.assertAlmostEqual(
            effective_base * device_damage.CREW_KO_TIME_FACTOR * 2.0,
            self.module._effective_shot_dispersion(
                gun_state, state, descriptor))

        sigmas = []
        uniform_calls = []

        class RecordingRandom(object):
            def __init__(self, unused_seed):
                pass

            def gauss(self, mean, sigma):
                sigmas.append((mean, sigma))
                return 0.0

            def uniform(self, minimum, maximum):
                uniform_calls.append((minimum, maximum))
                return minimum

        original_random = self.module.random.Random
        self.module.random.Random = RecordingRandom
        gun_state.elapsed = 10.0
        runtime = self.module.BotRuntime(1)
        runtime.round_id = 5
        try:
            self.assertTrue(runtime._fire(
                state, gun_state, 1.0, descriptor))
        finally:
            self.module.random.Random = original_random

        self.assertEqual(1, len(sigmas))
        for mean, sigma in sigmas:
            self.assertEqual(0.0, mean)
            self.assertAlmostEqual(
                effective_base * device_damage.CREW_KO_TIME_FACTOR, sigma)
        self.assertEqual([(0.0, 2.0 * math.pi)], uniform_calls)
        self.assertAlmostEqual(0.4, state['shot_yaw'])
        self.assertAlmostEqual(0.1, state['shot_pitch'])

    def test_bot_clip_scatter_never_leaves_the_presented_aiming_circle(self):
        dispersion = 0.0046
        nominal = self.module.ai_driver.barrel_direction(0.2, -0.05)

        for fire_seq in range(1, 31):
            shot_yaw, shot_pitch = self.module._dispersed_barrel_angles(
                24, 7, fire_seq, 0.2, -0.05, dispersion)
            physical = (
                math.sin(shot_yaw) * math.cos(shot_pitch),
                math.sin(shot_pitch),
                math.cos(shot_yaw) * math.cos(shot_pitch))
            dot = sum(nominal[index] * physical[index]
                      for index in range(3))
            offset = math.acos(max(-1.0, min(1.0, dot)))

            self.assertLessEqual(offset, dispersion + 1.0e-12)

    def test_live_reload_penalty_preserves_completed_reload_fraction(self):
        gun_state = self.module._BotGunState(_combat_descriptor())
        gun_state.reload_duration = 4.0
        gun_state.elapsed = 2.0

        self.assertTrue(gun_state.rescale_reload(2.0))
        self.assertAlmostEqual(4.0, gun_state.elapsed)
        self.assertAlmostEqual(4.0, gun_state.remaining(2.0))

        self.assertTrue(gun_state.rescale_reload(1.0))
        self.assertAlmostEqual(2.0, gun_state.elapsed)
        self.assertAlmostEqual(2.0, gun_state.remaining(1.0))

        gun_state.elapsed = 5.0
        self.assertTrue(gun_state.rescale_reload(2.0))
        self.assertAlmostEqual(9.0, gun_state.elapsed)
        self.assertTrue(gun_state.ready(2.0))

    def test_bot_intra_clip_interval_ignores_live_reload_penalty(self):
        gun_state = self.module._BotGunState(
            _combat_descriptor(reload_time=4.0, clip=(3, 0.2)))
        gun_state.elapsed = 20.0

        self.assertTrue(gun_state.fire(2.0))
        self.assertEqual(2, gun_state.clip)
        self.assertEqual('intra', gun_state.reload_kind)
        self.assertEqual(0.2, gun_state.duration(2.0))

        gun_state.tick(0.1)
        self.assertTrue(gun_state.rescale_reload(3.0))
        self.assertEqual(0.1, gun_state.elapsed)
        self.assertAlmostEqual(0.1, gun_state.remaining(3.0))

    def test_bot_gun_applies_default_loadout_to_base_values_only(self):
        descriptor = _combat_descriptor(
            reload_time=4.0, clip=(3, 0.2), dispersion=0.03)
        descriptor.gun.aimingTime = 2.0
        factor_calls = []
        modifier_calls = []
        original_attribute_factors = self.module.loadout.attribute_factors
        original_modifiers = self.module.loadout.modifiers

        def attribute_factors(value):
            factor_calls.append(value)
            return {'source': '1513-default-crew'}

        def modifiers(value, factors=None):
            modifier_calls.append((value, factors))
            return {
                'dispersion_factor': 0.8,
                'aim_time_factor': 0.75,
                'reload_factor': 0.5,
            }

        self.module.loadout.attribute_factors = attribute_factors
        self.module.loadout.modifiers = modifiers
        try:
            gun_state = self.module._BotGunState(descriptor)
        finally:
            self.module.loadout.attribute_factors = original_attribute_factors
            self.module.loadout.modifiers = original_modifiers

        self.assertEqual([descriptor], factor_calls)
        self.assertEqual(
            [(descriptor, {'source': '1513-default-crew'})],
            modifier_calls)
        self.assertAlmostEqual(
            0.03 * 0.8, gun_state.fully_aimed_dispersion)
        self.assertAlmostEqual(2.0 * 0.75, gun_state.aiming_time)
        self.assertAlmostEqual(4.0 * 0.5, gun_state.reload_full)

        gun_state.elapsed = 10.0
        self.assertTrue(gun_state.fire())
        self.assertAlmostEqual(0.2, gun_state.reload_intra)
        self.assertAlmostEqual(0.2, gun_state.duration(7.0))

    def test_friendly_lane_is_checked_on_each_final_fire_attempt(self):
        probes = []

        def friendly_lane(source, target, unused_descriptor,
                          unused_shell_index, launch):
            probes.append((
                source['id'], target['network_id'], dict(launch)))
            return len(probes) > 1

        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            friendly_lane_probe=friendly_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': -0.01,
        })
        runtime._gun_states[11].elapsed = 10.0
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
        }
        player = _admit_player(player)

        blocked = runtime.update(.04, 1.0, players=[player])[0]['bots'][0]
        self.assertEqual(0, blocked['fire_seq'])
        self.assertEqual(1, probes[0][2]['fire_seq'])
        self.assertNotIn(11, runtime._friendly_repositions)
        fired = runtime.update(.15, 1.15, players=[player])[0]['bots'][0]
        self.assertEqual(1, fired['fire_seq'])
        self.assertEqual([1, 1], [item[2]['fire_seq'] for item in probes])
        self.assertEqual(
            probes[1][2]['shot_yaw'], fired['shot_yaw'])
        self.assertEqual(
            probes[1][2]['shot_pitch'], fired['shot_pitch'])
        self.assertEqual([(11, 2), (11, 2)], [
            item[:2] for item in probes])

    def test_blocked_friendly_lane_repositions_through_safe_driver(self):
        final_probes = []

        def direction(position, yaw, speed, unused_descriptor):
            final_probes.append((tuple(position), float(yaw), float(speed)))
            return {'clear': True, 'collision': False, 'slope': 0.0}

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            direction_probe=direction,
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            friendly_lane_probe=lambda *unused: {
                'clear': False, 'blocker_kind': 'bot', 'blocker_id': 12,
                'blocker_team': 2, 'blocker_position': (0.0, 0.0, 20.0),
                'blocker_radius': 1.7},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': -0.01, 'fire_seq': 1,
        })
        runtime._gun_states[11].elapsed = 10.0
        runtime._apply_orders({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'target_kind': 'human', 'target_id': 2,
                'aim_position': (0.0, 1.0, 100.0),
                'face_position': (0.0, 1.0, 100.0),
                'move_position': (0.0, 0.0, 0.0),
                'fire_allowed': True, 'fire_range': 500.0,
                'shell_index': 0, 'combat_mode': 'engage',
                'throttle_override': 0.0,
            }],
        })
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
        }
        player = _admit_player(player)

        runtime.update(.04, 1.0, players=[player])
        self.assertIn(11, runtime._friendly_repositions)
        self.assertEqual(1, state['fire_seq'])
        start_position = self.module._position(state)
        moved = False
        for index in range(1, 151):
            runtime.update(
                .04, 1.0 + .04 * index, players=[player])
            if self.module._distance(
                    start_position, self.module._position(state)) > 0.05:
                moved = True
                break

        self.assertTrue(moved)
        self.assertEqual(1, state['fire_seq'])
        self.assertTrue(final_probes)

    def test_blocked_reposition_expires_without_crossing_native_gate(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            direction_probe=lambda *unused: {
                'clear': False, 'collision': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            friendly_lane_probe=lambda *unused: {
                'clear': False, 'blocker_kind': 'bot', 'blocker_id': 12,
                'blocker_team': 2, 'blocker_position': (0.0, 0.0, 20.0),
                'blocker_radius': 1.7},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': -0.01,
        })
        runtime._gun_states[11].elapsed = 10.0
        runtime._apply_orders({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'target_kind': 'human', 'target_id': 2,
                'aim_position': command['aim_position'],
                'face_position': command['face_position'],
                'move_position': command['move_position'],
                'fire_allowed': True, 'fire_range': 500.0,
                'shell_index': 0, 'combat_mode': 'engage',
                'throttle_override': 0.0,
            }],
        })
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
        }
        player = _admit_player(player)

        runtime.update(.04, 1.0, players=[player])
        self.assertIn(11, runtime._friendly_repositions)
        initial_position = self.module._position(state)
        initial_ammo = list(state['ammo_remaining'])
        expired = False
        for index in range(1, 140):
            runtime.update(.04, 1.0 + .04 * index, players=[player])
            if 11 not in runtime._friendly_repositions:
                expired = True
                break

        self.assertTrue(expired)
        self.assertEqual(initial_position, self.module._position(state))
        self.assertEqual(0, state['fire_seq'])
        self.assertEqual(initial_ammo, state['ammo_remaining'])

    def test_friendly_lane_fail_closed_without_blocker_does_not_reposition(self):
        verdicts = [
            {'clear': False},
            {'clear': False, 'blocker_kind': 'bot', 'blocker_id': 12,
             'blocker_team': 1, 'blocker_position': (0.0, 0.0, 20.0),
             'blocker_radius': 1.7},
        ]
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            friendly_lane_probe=lambda *unused: verdicts.pop(0),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': -0.01,
        })
        runtime._gun_states[11].elapsed = 10.0
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
        }
        player = _admit_player(player)

        runtime.update(.04, 1.0, players=[player])
        self.assertNotIn(11, runtime._friendly_repositions)
        runtime.update(.04, 1.04, players=[player])
        self.assertNotIn(11, runtime._friendly_repositions)
        self.assertEqual(0, state['fire_seq'])

    def test_unavailable_native_launch_origin_stops_without_reposition(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        lane_calls = []

        def lane(*unused):
            lane_calls.append(True)
            return {'clear': True}
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            friendly_lane_probe=lane,
            direct_launch_origin_probe=lambda *unused: (_ for _ in ()).throw(
                RuntimeError('native muzzle unavailable')),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': -0.01,
        })
        runtime._gun_states[11].elapsed = 10.0

        runtime.update(.04, 1.0, players=[{
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 100.0,
            'effective_params': _effective_params_snapshot(),
        }])

        self.assertEqual(0, state['fire_seq'])
        self.assertNotIn(11, runtime._friendly_repositions)
        self.assertEqual([], lane_calls)

    def test_spg_exact_friendly_lane_is_checked_before_atomic_fire(self):
        descriptor = _combat_descriptor(dispersion=0.003)
        probes = []
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1000.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 1.0, 100.0),
            'face_position': (0.0, 1.0, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }

        def strategic(*unused):
            return {
                'aim_position': (0.0, 1.0, 100.0),
                'yaw': 0.0, 'pitch': -0.1,
                'flight_time': 0.5, 'arc': 'low',
            }

        def exact_launch(source, target, unused_descriptor, shell_index,
                         fire_seq, shot_yaw, shot_pitch, flight_time,
                         unused_now):
            speed = 1000.0
            gravity = 10.0
            horizontal = math.cos(shot_pitch)
            velocity = (
                math.sin(shot_yaw) * horizontal * speed,
                math.sin(shot_pitch) * speed,
                math.cos(shot_yaw) * horizontal * speed,
            )
            terminal = (
                target['position'][0], target['position'][1] + 1.0,
                target['position'][2])
            origin = (
                terminal[0] - velocity[0] * flight_time,
                terminal[1] - velocity[1] * flight_time +
                0.5 * gravity * flight_time * flight_time,
                terminal[2] - velocity[2] * flight_time,
            )
            return {
                'proof_key': ('exact', fire_seq),
                'fire_seq': fire_seq, 'shell_index': shell_index,
                'origin': origin, 'velocity': velocity,
                'shot_yaw': shot_yaw, 'shot_pitch': shot_pitch,
                'gravity': gravity, 'max_distance': 5000.0,
                'max_time_ms': 20000, 'flight_time': flight_time,
                'path': (origin, terminal),
            }

        def exact_friendly_lane(
                unused_source, unused_target, unused_descriptor,
                unused_shell, receipt):
            probes.append(receipt['path'])
            if len(probes) > 1:
                return {'clear': True}
            return {
                'clear': False, 'blocker_kind': 'bot', 'blocker_id': 12,
                'blocker_team': 2, 'blocker_position': (0.0, 0.0, 20.0),
                'blocker_radius': 1.7,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ballistic_solution_probe=strategic,
            artillery_launch_probe=exact_launch,
            artillery_friendly_lane_probe=exact_friendly_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        state = runtime.states[11]
        state.update({
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'turret_yaw': 0.0,
            'gun_pitch': -0.1, 'desired_gun_pitch': -0.1,
            'profile': {'class_tag': 'SPG'},
        })
        runtime._gun_states[11].elapsed = 10.0
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }
        player = _admit_player(player)

        blocked = runtime.update(.04, 1.0, players=[player])[0]['bots'][0]
        self.assertEqual(0, blocked['fire_seq'])
        self.assertNotIn(11, runtime._artillery_intents)
        self.assertNotIn(11, runtime._artillery_reproofs)
        destination = runtime._friendly_repositions[11]['destination']
        state['x'], state['y'], state['z'] = destination
        fired = runtime.update(.15, 1.15, players=[player])[0]['bots'][0]
        self.assertEqual(1, fired['fire_seq'])
        self.assertEqual(2, len(probes))

    def test_firing_lane_probe_failure_is_not_hidden_as_blocked_los(self):
        def broken_lane(unused_source, unused_target):
            raise RuntimeError('native lane probe failed')

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=broken_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        with self.assertRaisesRegex(RuntimeError, 'native lane probe failed'):
            runtime.update(.04, 1.0, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': 0.0, 'y': 0.0, 'z': 100.0,
                'effective_params': _effective_params_snapshot(),
            }])

    def test_missing_or_nonpositive_installed_gun_dispersion_is_rejected(self):
        missing = _combat_descriptor()
        del missing.gun.shotDispersionAngle
        with self.assertRaisesRegex(
                ValueError, 'shotDispersionAngle is unavailable'):
            self.module._BotGunState(missing)

        zero = _combat_descriptor(dispersion=0.0)
        with self.assertRaisesRegex(
                ValueError, 'shotDispersionAngle must be positive'):
            self.module._BotGunState(zero)

    def test_cached_server_order_tracks_current_visible_target_pose(self):
        stale = (-40.0, 1.0, 80.0)
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'advance_contact',
            'aim_position': stale, 'face_position': stale,
            'move_position': stale,
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        adapter = _FixedAdapter(command)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=0.0, aim_yaw=0.0)
        runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'target_kind': 'human', 'target_id': 2,
                'fire_allowed': True, 'shell_index': 0,
                'fire_range': 500.0, 'combat_mode': 'advance_contact',
                'aim_position': stale, 'face_position': stale,
                'move_position': stale,
            }],
            'bots': [],
        })

        seen_commands = []
        original_update_aim = runtime._update_gun_aim

        def record_update_aim(state, live_command, target, step):
            seen_commands.append(dict(live_command))
            return original_update_aim(state, live_command, target, step)

        runtime._update_gun_aim = record_update_aim
        first_pose = (0.0, 1.0, 100.0)
        moved_pose = (80.0, 2.0, 20.0)
        try:
            runtime.update(.04, 1.0, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': first_pose[0], 'y': first_pose[1], 'z': first_pose[2],
                'effective_params': _effective_params_snapshot(),
            }])
            runtime.update(.04, 1.04, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': moved_pose[0], 'y': moved_pose[1], 'z': moved_pose[2],
                'effective_params': _effective_params_snapshot(),
            }])
            runtime.update(.10, 1.14, players=[{
                'id': 2, 'team': 1, 'alive': True,
                'x': moved_pose[0], 'y': moved_pose[1], 'z': moved_pose[2],
                'effective_params': _effective_params_snapshot(),
            }])
        finally:
            runtime._update_gun_aim = original_update_aim

        # The second frame reuses the cached decision but not its stale pose.
        self.assertEqual(1, len(adapter.calls))
        self.assertEqual(first_pose, adapter.server_orders[0]['aim_position'])
        self.assertEqual(moved_pose, seen_commands[-1]['aim_position'])
        self.assertEqual(moved_pose, seen_commands[-1]['face_position'])
        self.assertEqual(moved_pose, seen_commands[-1]['move_position'])
        self.assertGreater(runtime.states[11]['aim_yaw'], 0.3)

        missing = self.module._overlay_live_target_pose(command, None)
        team_spotted = self.module._overlay_live_target_pose(
            command, {'alive': True, 'visible': False,
                      'position': moved_pose})
        self.assertFalse(missing['fire_allowed'])
        self.assertTrue(team_spotted['fire_allowed'])
        self.assertEqual(moved_pose, team_spotted['aim_position'])
        self.assertEqual(stale, missing['aim_position'])

        stable = self.module._overlay_live_target_pose(dict(
            command, combat_mode='support_hold', stable_hull_face=True,
            face_position=stale), {
                'alive': True, 'visible': True, 'position': moved_pose})
        self.assertEqual(moved_pose, stable['aim_position'])
        self.assertEqual(stale, stable['face_position'])
        self.assertNotEqual(
            self.module._server_order_signature(stable),
            self.module._server_order_signature(dict(
                stable, face_position=(10.0, 1.0, 10.0))))
        with self.assertRaisesRegex(
                ValueError, 'stable hull face flag is invalid'):
            self.module._overlay_live_target_pose(
                dict(command, stable_hull_face=1), {
                    'alive': True, 'visible': True,
                    'position': moved_pose})

        with self.assertRaisesRegex(
                ValueError, 'alive flag is invalid'):
            self.module._overlay_live_target_pose(command, {
                'visible': True, 'position': moved_pose})
        with self.assertRaisesRegex(
                ValueError, 'position is unavailable'):
            self.module._overlay_live_target_pose(command, {
                'alive': True, 'visible': True})
        with self.assertRaisesRegex(
                ValueError, 'position must be finite'):
            self.module._overlay_live_target_pose(command, {
                'alive': True, 'visible': True,
                'position': (0.0, float('nan'), 1.0)})

    def test_cached_selected_bot_refreshes_only_live_pose_and_death(self):
        stale = (0.0, 1.0, 100.0)
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True, 'target_id': 12,
            'fire_range': 500.0, 'combat_mode': 'advance_contact',
            'aim_position': stale, 'face_position': stale,
            'move_position': stale,
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        adapter = _FixedAdapter(command)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.01, clip=(1,)),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Shooter'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Target'},
        ]))
        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=0.0, aim_yaw=0.0)
        runtime.states[12].update(
            x=stale[0], y=stale[1], z=stale[2], yaw=0.0,
            health=900, max_health=900, alive=True)
        # Keep all three frames outside both observation and lane-refresh
        # windows so only the selected target may be copied.
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        full_refreshes = []
        original_refresh = runtime._refresh_target_poses

        def record_full_refresh(*args, **kwargs):
            full_refreshes.append(1)
            return original_refresh(*args, **kwargs)

        seen = []
        original_update_aim = runtime._update_gun_aim

        def record_update_aim(state, live_command, target, step):
            if state['id'] == 11:
                seen.append((dict(live_command), dict(target)))
            return original_update_aim(state, live_command, target, step)

        runtime._refresh_target_poses = record_full_refresh
        runtime._update_gun_aim = record_update_aim
        try:
            runtime.update(.04, 1.0)
            cached_target = runtime._decision_cache[11][5][12]
            cached_snapshot = dict(cached_target)

            moved = (60.0, 2.0, 80.0)
            runtime.states[12].update(
                x=moved[0], y=moved[1], z=moved[2], health=321)
            runtime.update(.04, 1.04)
            fire_before_death = runtime.states[11]['fire_seq']

            runtime.states[12].update(alive=False, health=0)
            runtime.update(.04, 1.08)
        finally:
            runtime._refresh_target_poses = original_refresh
            runtime._update_gun_aim = original_update_aim

        self.assertEqual([], full_refreshes)
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual(moved, seen[1][0]['aim_position'])
        self.assertEqual(moved, seen[1][0]['face_position'])
        self.assertEqual(moved, seen[1][0]['move_position'])
        self.assertEqual(321, seen[1][1]['health'])
        self.assertFalse(seen[2][1]['alive'])
        self.assertEqual(0, seen[2][1]['health'])
        self.assertFalse(seen[2][0]['fire_allowed'])
        self.assertEqual(fire_before_death, runtime.states[11]['fire_seq'])
        self.assertIs(cached_target, runtime._decision_cache[11][5][12])
        self.assertEqual(cached_snapshot, cached_target)

    def test_bot_critical_state_preserves_loader_reload_and_gun_gate(self):
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 0.5, 100.0),
            'face_position': (0.0, 0.5, 100.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(1,)),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0,
                     critical={'crew_ko': ['loader1'], 'destroyed': [],
                               'devices': []})
        player = {'id': 2, 'team': 1, 'alive': True,
                  'x': 0.0, 'y': 0.5, 'z': 100.0}
        player = _admit_player(player)

        from gui.mods.offline_lan_0922 import device_damage
        expected_reload = (
            runtime._gun_states[11].reload_full *
            device_damage.CREW_KO_TIME_FACTOR)
        last = None
        for index in range(4):
            last = runtime.update(.20, 1.0 + index * .20,
                                  players=[player])[0]['bots'][0]
        self.assertEqual(0, last['fire_seq'])
        self.assertAlmostEqual(
            expected_reload, runtime.states[11]['reload_duration'])
        fired = runtime.update(.20, 1.8, players=[player])[0]['bots'][0]
        self.assertEqual(1, fired['fire_seq'])

        runtime.states[11]['critical'] = {
            'crew_ko': [], 'devices': [], 'destroyed': ['gunHealth']}
        for index in range(5):
            blocked = runtime.update(
                .20, 2.4 + index * .20, players=[player])[0]['bots'][0]
        self.assertEqual(1, blocked['fire_seq'])

    def test_critical_parts_tick_cache_reuses_only_unchanged_payload(self):
        payload = _critical_payload(
            {'name': 'engineHealth', 'hp': 40.0, 'max_hp': 100.0,
             'state': 'critical'},
            crew_ko=('driver',))
        state = {'critical': payload}
        original_parse = self.module._parse_critical_parts
        expected = original_parse(payload)
        calls = []

        def counted_parse(value):
            calls.append(value)
            return original_parse(value)

        self.module._parse_critical_parts = counted_parse
        try:
            cached = self.module._cache_critical_parts_for_tick(state)
            self.assertEqual(expected, cached)
            self.assertIs(cached, self.module._critical_parts(state))
            self.assertIs(cached, self.module._critical_parts(state))
            self.assertEqual([payload], calls)

            replacement = _critical_payload(
                {'name': 'engineHealth', 'hp': 0.0, 'max_hp': 100.0,
                 'state': 'destroyed'},
                destroyed=('engineHealth',))
            state['critical'] = replacement
            refreshed = self.module._critical_parts(state)
            self.assertIn('engineHealth', refreshed[1])
            self.assertEqual([payload, replacement], calls)
        finally:
            self.module._parse_critical_parts = original_parse

    def test_critical_parts_tick_cache_stays_out_of_state_projections(self):
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        key = self.module._CRITICAL_PARTS_TICK_CACHE
        self.module._cache_critical_parts_for_tick(state)
        self.assertIn(key, state)

        projected = self.module.lan_client.project_bot_state(state)
        self.assertNotIn(key, projected)
        self.assertNotIn(key, self.runtime.presentation_states()[0])
        json.dumps(projected)

        # Repair/fire advancement is a mutation boundary even when this slice
        # happens not to alter the canonical payload.
        self.runtime._advance_bot_critical(state, 0.2, 0.2)
        self.assertNotIn(key, state)

        outgoing = self.runtime.update(0.2, 1.0)
        wire = next(message for message in outgoing
                    if message['type'] == 'bot_state')
        self.assertNotIn(key, state)
        self.assertTrue(all(key not in value for value in wire['bots']))

    def test_bot_consumables_use_independent_inventory_and_cooldowns(self):
        contracts = _bot_equipment_contracts(self.module)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _critical_descriptor(),
            adapter_factory=lambda *unused: _Adapter(),
            direction_probe=lambda *unused: {'clear': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            bot_equipment_resolver=lambda: contracts)
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 2, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 2, 'slot': 1, 'name': 'Second'},
        ]))
        state = runtime.states[11]
        state['critical'] = _critical_payload(
            {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
             'state': 'destroyed'},
            destroyed=('leftTrackHealth',), crew_ko=('commander',),
            fire=True)

        runtime._advance_equipment_clock(0.2)
        self.assertTrue(runtime._advance_bot_critical(
            state, 0.2, 0.2))
        self.assertFalse(state['critical']['fire'])
        self.assertEqual(['commander'], state['critical']['crew_ko'])
        self.assertIn(
            'leftTrackHealth', state['critical']['destroyed'])

        runtime._advance_equipment_clock(0.2)
        self.assertTrue(runtime._advance_bot_critical(
            state, 0.2, 0.4))
        self.assertEqual([], state['critical']['crew_ko'])
        self.assertEqual([], state['critical']['destroyed'])
        self.assertEqual(
            [1, 1, 1],
            [value.uses_left for value in runtime._equipment_states[11]])
        self.assertEqual(
            [2, 2, 2],
            [value.uses_left for value in runtime._equipment_states[12]])
        self.assertFalse(runtime._equipment_states[11][1].ready(0.4))
        self.assertFalse(runtime._equipment_states[11][2].ready(0.4))
        snapshots = state['equipment_states']
        self.assertAlmostEqual(90.0, snapshots[1]['cooldownTimeLeft'])
        self.assertAlmostEqual(90.0, snapshots[2]['cooldownTimeLeft'])
        self.assertAlmostEqual(
            0.9, runtime.bot_equipment_passives(11)[
                'fireStartingChanceFactor'])
        descriptor = runtime._descriptors[11]
        base_repair = self.module.loadout.modifiers(
            descriptor,
            factors=self.module.loadout.attribute_factors(descriptor))[
                'repair_factor']
        self.assertAlmostEqual(
            base_repair * 1.10,
            runtime._bot_repair_factor(11, descriptor))
        projected = self.module.lan_client.project_bot_state(state)
        self.assertEqual(3, len(projected['equipment_states']))
        malformed = dict(state)
        malformed['equipment_states'] = [{}]
        self.assertIsNone(
            self.module.lan_client.project_bot_state(malformed))

    def test_bot_medkit_clears_stun_only_after_a_later_simulation_frame(self):
        contracts = _bot_equipment_contracts(self.module)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _critical_descriptor(),
            adapter_factory=lambda *unused: _Adapter(),
            direction_probe=lambda *unused: {'clear': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            bot_equipment_resolver=lambda: contracts)
        runtime.battle_start(dict(self.start, server_time_ms=1000))
        runtime.apply_snapshot({
            'server_tick': 1, 'server_time_ms': 1000,
            'bots': [_snapshot_bot(
                critical={}, revision=1, base_revision=1,
                stun_end_server_time_ms=5000)]})
        state = runtime.states[11]

        runtime._advance_equipment_clock(0.2)
        self.assertFalse(runtime._advance_bot_critical(state, 0.2, 0.2))
        self.assertEqual(5000, state['stun_end_server_time_ms'])
        self.assertEqual(2, runtime._equipment_states[11][1].uses_left)

        runtime._advance_equipment_clock(0.2)
        self.assertTrue(runtime._advance_bot_critical(state, 0.2, 0.4))
        self.assertEqual(0, state['stun_end_server_time_ms'])
        self.assertEqual(1, runtime._equipment_states[11][1].uses_left)
        self.assertAlmostEqual(
            90.0, state['equipment_states'][1]['cooldownTimeLeft'])
        self.assertTrue(runtime._mark_combat_publication(state))
        self.assertEqual(1, state['combat_seq'])

    def test_bot_medkit_restores_crew_and_clears_same_stun_once(self):
        contracts = _bot_equipment_contracts(self.module)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _critical_descriptor(),
            adapter_factory=lambda *unused: _Adapter(),
            direction_probe=lambda *unused: {'clear': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            bot_equipment_resolver=lambda: contracts)
        runtime.battle_start(dict(self.start, server_time_ms=1000))
        runtime.apply_snapshot({
            'server_tick': 1, 'server_time_ms': 1000,
            'bots': [_snapshot_bot(
                critical=_critical_payload(crew_ko=('commander',)),
                revision=1, base_revision=1,
                stun_end_server_time_ms=5000)]})
        state = runtime.states[11]

        runtime._advance_equipment_clock(0.2)
        runtime._advance_bot_critical(state, 0.2, 0.2)
        runtime._advance_equipment_clock(0.2)
        runtime._advance_bot_critical(state, 0.2, 0.4)

        self.assertEqual([], state['critical']['crew_ko'])
        self.assertEqual(0, state['stun_end_server_time_ms'])
        self.assertEqual(1, runtime._equipment_states[11][1].uses_left)

    def test_bot_equipment_takeover_restores_once_without_clock_rewind(self):
        contracts = _bot_equipment_contracts(self.module)

        def new_runtime():
            return self.module.BotRuntime(
                1, descriptor_resolver=lambda unused: _critical_descriptor(),
                adapter_factory=lambda *unused: _Adapter(),
                direction_probe=lambda *unused: {'clear': True},
                ground_probe=lambda *unused: 0.0,
                physics_ground_probe=lambda *unused: 0.0,
                spawn_resolver=_spawn_resolver, baked_graph=_graph(),
                bot_equipment_resolver=lambda: contracts)

        first = new_runtime()
        first.battle_start(self.start)
        repair = first._equipment_states[11][2]
        critical = _critical_payload(
            {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
             'state': 'destroyed'}, destroyed=('leftTrackHealth',))
        self.assertIsNotNone(repair.activate(0.0, critical))
        first._advance_equipment_clock(12.0)
        manifest = first._manifest_entry(first.states[11])
        self.assertAlmostEqual(
            78.0,
            manifest['equipment_states'][2]['cooldownTimeLeft'])

        restored = new_runtime()
        resume = dict(self.start, bots=[], bot_manifest=[manifest])
        restored.battle_start(resume)
        restored_repair = restored._equipment_states[11][2]
        self.assertAlmostEqual(78.0, restored_repair.ready_at)
        restored._advance_equipment_clock(10.0)
        restored.battle_start(resume)
        self.assertAlmostEqual(78.0, restored_repair.ready_at)
        restored._publish_equipment_state(restored.states[11])
        self.assertAlmostEqual(
            68.0,
            restored.states[11]['equipment_states'][2][
                'cooldownTimeLeft'])

    def test_destroyed_bot_track_repairs_to_regen_cap(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        broken = _critical_payload({
            'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
            'state': 'destroyed'}, destroyed=['leftTrackHealth'])
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=broken, revision=1, base_revision=1)]})

        # A bot carries #1513's default crew, which has no repair skill, so
        # factors['repairSpeed'] is 0.57 and the track takes 10 / (2 * 0.57)
        # seconds rather than the five a fully trained crew would need.
        outgoing = None
        for index in range(25):
            outgoing = runtime.update(.20, 1.0 + index * .20)[0]['bots'][0]
        self.assertLess(outgoing['critical']['devices'][0]['hp'], 130.0)
        for index in range(25, 45):
            outgoing = runtime.update(.20, 1.0 + index * .20)[0]['bots'][0]

        device = outgoing['critical']['devices'][0]
        self.assertEqual('leftTrackHealth', device['name'])
        self.assertEqual(130.0, device['hp'])
        self.assertEqual('critical', device['state'])
        self.assertNotIn('leftTrackHealth', outgoing['critical']['destroyed'])

    def test_yellow_bot_tracks_keep_full_motion_but_destroyed_track_locks(self):
        descriptor = _critical_descriptor()
        command = dict(self._stationary_command())
        command.update({
            'throttle': 1.0, 'turn': 1.0,
            'combat_mode': 'route', 'movement_intent': True,
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        yellow = _critical_payload(
            {'name': 'leftTrackHealth', 'hp': 100.0, 'max_hp': 170.0,
             'state': 'critical'},
            {'name': 'rightTrackHealth', 'hp': 100.0, 'max_hp': 170.0,
             'state': 'critical'})
        state['critical'] = yellow
        self.assertEqual(
            1.0, self.module._critical_factor(state, descriptor, 'traverse'))

        drive_inputs = []
        turn_inputs = []
        original_longitudinal = self.module.vehicle_physics.longitudinal_step
        original_traverse = self.module.vehicle_physics.traverse_step

        def drive(unused_params, unused_speed, throttle, *unused, **kwargs):
            drive_inputs.append(throttle)
            return 0.0

        def traverse(unused_params, unused_speed, turn, *unused, **kwargs):
            turn_inputs.append(turn)
            return 0.0

        self.module.vehicle_physics.longitudinal_step = drive
        self.module.vehicle_physics.traverse_step = traverse
        try:
            runtime.update(.04, 1.0)
            state['critical'] = _critical_payload(
                {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
                 'state': 'destroyed'},
                destroyed=['leftTrackHealth'])
            runtime.update(.04, 1.04)
        finally:
            self.module.vehicle_physics.longitudinal_step = original_longitudinal
            self.module.vehicle_physics.traverse_step = original_traverse

        self.assertEqual([1.0, 0.0], drive_inputs)
        self.assertEqual([1.0, 0.0], turn_inputs)

    def test_explicit_yellow_state_applies_above_the_hp_threshold(self):
        descriptor = _critical_descriptor()
        descriptor.hull.ammoBayHealth = types.SimpleNamespace(
            maxHealth=100, maxRegenHealth=70)
        state = {
            'critical': _critical_payload({
                'name': 'ammoBayHealth', 'hp': 70.0, 'max_hp': 100.0,
                'state': 'critical',
            }),
        }

        self.assertEqual(
            2.0, self.module._critical_factor(
                state, descriptor, 'reload'))

    def test_bot_fire_burns_five_percent_per_second_and_ends_at_ten_seconds(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=burning, revision=1, base_revision=1)]})

        outgoing = None
        for index in range(49):
            outgoing = runtime.update(.20, index * .20)[0]['bots'][0]
        self.assertEqual(550, outgoing['health'])
        self.assertTrue(outgoing['critical']['fire'])

        outgoing = runtime.update(.20, 9.8)[0]['bots'][0]
        fuel = outgoing['critical']['devices'][0]
        self.assertEqual(500, outgoing['health'])
        self.assertFalse(outgoing['critical']['fire'])
        self.assertEqual(0.0, outgoing['combat_fire_elapsed'])
        self.assertEqual(0.0, outgoing['combat_fire_timer'])
        self.assertEqual(40.0, fuel['hp'])
        self.assertEqual('critical', fuel['state'])
        self.assertNotIn('fuelTankHealth', outgoing['critical']['destroyed'])

    def test_worker_projects_terminal_critical_and_fire_uses_same_wreck(self):
        descriptor = _critical_descriptor()
        descriptor.type = types.SimpleNamespace(crewRoles=(
            ('commander',), ('driver',), ('gunner',),
            ('loader',), ('radioman',)))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())

        manifest = runtime.battle_start(self.start)[0]['bots'][0]
        terminal = manifest['terminal_critical']
        expected_devices = set(
            self.module.critical_damage._OFFH_DEATH_DEVICES)
        self.assertEqual(expected_devices, set(terminal['destroyed']))
        self.assertEqual(
            set(terminal['crew_roster']), set(terminal['crew_ko']))
        self.assertFalse(terminal['fire'])
        self.assertEqual([], terminal['events'])

        state = runtime.states[11]
        state.update({
            'health': 20, 'display_health': 20,
            'critical': _critical_payload({
                'name': 'fuelTankHealth', 'hp': 0.0,
                'max_hp': 100.0, 'state': 'destroyed',
            }, destroyed=['fuelTankHealth'], fire=True),
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        })
        self.assertTrue(runtime._advance_bot_critical(
            state, 1.0, 1.0, record_step=False))
        self.assertEqual(0, state['health'])
        self.assertFalse(state['alive'])
        self.assertEqual(expected_devices,
                         set(state['critical']['destroyed']))
        self.assertEqual(
            set(terminal['crew_roster']),
            set(state['critical']['crew_ko']))
        self.assertFalse(state['critical']['fire'])
        self.assertEqual([], state['critical']['events'])
        self.assertEqual((0.0, 0.0), (
            state['combat_fire_elapsed'], state['combat_fire_timer']))

    def test_delayed_server_echo_cannot_rewind_bot_fire_or_repair_publication(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        baseline = _critical_payload(
            {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
             'state': 'destroyed'},
            {'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
             'state': 'destroyed'},
            destroyed=['leftTrackHealth', 'fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                critical=baseline, revision=1, base_revision=1)]})

        first = None
        for index in range(5):
            first = runtime.update(.20, index * .20)[0]['bots'][0]
        first_seq = runtime._combat_sync[11]['next_seq']
        first_track = dict((record['name'], record['hp'])
                           for record in first['critical']['devices'])[
                               'leftTrackHealth']
        self.assertEqual(950, first['health'])

        # The server has not consumed the publication yet and repeats its last
        # canonical state.  This is an echo, not a new critical hit.
        runtime.apply_snapshot({
            'server_tick': 11,
            'bots': [_snapshot_bot(
                critical=baseline, revision=1, base_revision=1)]})
        self.assertEqual(950, runtime.states[11]['health'])
        self.assertEqual(
            first_track, dict((record['name'], record['hp']) for record in
                              runtime.states[11]['critical']['devices'])[
                                  'leftTrackHealth'])

        second = None
        for index in range(5, 10):
            second = runtime.update(.20, index * .20)[0]['bots'][0]
        second_track = dict((record['name'], record['hp'])
                            for record in second['critical']['devices'])[
                                'leftTrackHealth']
        self.assertEqual(900, second['health'])
        self.assertGreater(second_track, first_track)

        # A later snapshot acknowledges the earlier exact publication.  It
        # advances the local revision boundary but cannot overwrite revision 2.
        runtime.apply_snapshot({
            'server_tick': 12,
            'bots': [_snapshot_bot(
                health=first['health'], critical=first['critical'],
                revision=1 + first_seq, base_revision=1,
                ack_seq=first_seq,
                fire_elapsed=first['combat_fire_elapsed'],
                fire_timer=first['combat_fire_timer'])]})
        self.assertEqual(900, runtime.states[11]['health'])
        self.assertEqual(
            second_track, dict((record['name'], record['hp']) for record in
                               runtime.states[11]['critical']['devices'])[
                                   'leftTrackHealth'])
        sync = runtime._combat_sync[11]
        self.assertEqual(first_seq, sync['acked_seq'])
        self.assertEqual(
            sync['next_seq'] - first_seq, len(sync['pending']))

    def test_external_hit_before_publication_ack_replays_unacked_fire_once(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        manifest_message = runtime.battle_start(self.start)[0]
        server = BattleState(map_name='01_karelia')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.tick = 100000
        server.round_id = self.start['round_id']
        server.players[1] = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = list(self.start['bots'])
        self.assertTrue(server.update_bot_manifest(1, {
            'round_id': server.round_id,
            'bots': manifest_message['bots'],
        }))
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                health=950, critical=burning,
                revision=1, base_revision=1)]})

        publication = None
        for index in range(5):
            publication = runtime.update(
                .20, .20 + index * .20)[0]['bots'][0]
        self.assertEqual((900, 5),
                         (publication['health'], publication['combat_seq']))
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=2, base_revision=2, ack_seq=0)]})
        # Mirror the external hit as the server's canonical combat base.  The
        # next full-state wire must be accepted directly against this ack=0
        # state; accepting only a locally inspected sequence number would not
        # protect the complete #1513 reconciliation contract.
        server.bot_states[11].update(
            health=800, display_health=800, alive=True,
            critical=dict(burning), combat_revision=2,
            combat_base_revision=2, combat_ack_seq=0,
            combat_fire_elapsed=0.0, combat_fire_timer=0.0)

        self.assertEqual(750, runtime.states[11]['health'])
        self.assertEqual((2, 0), (
            runtime.states[11]['combat_base_revision'],
            runtime.states[11]['combat_seq']))
        sync = runtime._combat_sync[11]
        self.assertEqual([], sync['pending'])
        self.assertEqual(5, len(sync['unpublished_steps']))

        next_publication = runtime.update(.20, 1.20)[0]['bots'][0]

        # The five replayed slices plus this render slice's fire-clock advance
        # are one full-state publication on the new base.  The current slice
        # does not cross another one-second damage boundary, so the replayed
        # 50 health loss remains exact.  No invisible replay proposal may
        # consume sequence 1 and make the wire jump directly to sequence 2.
        self.assertEqual(750, next_publication['health'])
        self.assertAlmostEqual(
            1.2, next_publication['combat_fire_elapsed'])
        self.assertAlmostEqual(.2, next_publication['combat_fire_timer'])
        self.assertEqual(1, next_publication['combat_seq'])
        self.assertEqual([1], [entry['seq'] for entry in sync['pending']])
        self.assertEqual([], sync['unpublished_steps'])

        self.assertTrue(server.update_bot_states(1, {
            'round_id': server.round_id,
            'bots': [next_publication],
        }))
        self.assertEqual(1, server.bot_states[11]['combat_ack_seq'])

        # Acknowledged snapshots and later fire-clock publications stay
        # contiguous too; the fix must not merely make the first rebase packet
        # acceptable and then reopen the same gap on the following tick.
        for index in range(4):
            runtime.apply_snapshot({
                'server_tick': 3 + index,
                'bots': [dict(server.bot_states[11])],
            })
            server_ack = server.bot_states[11]['combat_ack_seq']
            outgoing = runtime.update(.20, 1.40 + index * .20)
            publication_message = next(
                message for message in outgoing
                if message['type'] == 'bot_state')
            published = publication_message['bots'][0]
            self.assertEqual(server_ack + 1, published['combat_seq'])
            self.assertTrue(server.update_bot_states(1, {
                'round_id': server.round_id,
                'bots': publication_message['bots'],
            }))
            self.assertEqual(
                published['combat_seq'],
                server.bot_states[11]['combat_ack_seq'])
        self.assertEqual(700, server.bot_states[11]['health'])

    def test_external_base_replay_waits_for_wire_before_reserving_sequence(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                health=950, critical=burning,
                revision=1, base_revision=1)]})

        first = runtime.update(.20, .20)[0]['bots'][0]
        self.assertEqual(1, first['combat_seq'])
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=900, critical=burning,
                revision=2, base_revision=2, ack_seq=0)]})

        sync = runtime._combat_sync[11]
        self.assertEqual(0, sync['next_seq'])
        self.assertEqual([], sync['pending'])
        self.assertEqual(1, len(sync['unpublished_steps']))
        self.assertEqual(0, runtime.states[11]['combat_seq'])

        publication = runtime.update(.20, .40)[0]['bots'][0]

        self.assertEqual(1, publication['combat_seq'])
        self.assertEqual(1, sync['next_seq'])
        self.assertEqual([1], [entry['seq'] for entry in sync['pending']])
        self.assertEqual([], sync['unpublished_steps'])

    def test_second_external_base_replays_unpublished_lineage_without_gap(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                health=950, critical=burning,
                revision=1, base_revision=1)]})
        runtime.update(.20, .20)
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=900, critical=burning,
                revision=2, base_revision=2, ack_seq=0)]})
        first_replay = list(runtime._combat_sync[11]['unpublished_steps'])

        runtime.apply_snapshot({
            'server_tick': 3,
            'bots': [_snapshot_bot(
                health=850, critical=burning,
                revision=3, base_revision=3, ack_seq=0)]})

        sync = runtime._combat_sync[11]
        self.assertEqual(first_replay, sync['unpublished_steps'])
        self.assertEqual(0, sync['next_seq'])
        self.assertEqual([], sync['pending'])
        publication = runtime.update(.20, .40)[0]['bots'][0]
        self.assertEqual(1, publication['combat_seq'])

    def test_external_hit_after_publication_ack_does_not_double_apply_fire(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                health=950, critical=burning,
                revision=1, base_revision=1)]})

        publication = None
        for index in range(5):
            publication = runtime.update(
                .20, .20 + index * .20)[0]['bots'][0]
        self.assertEqual(900, publication['health'])
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=7, base_revision=7, ack_seq=5,
                fire_elapsed=1.0, fire_timer=0.0)]})

        self.assertEqual(800, runtime.states[11]['health'])
        self.assertEqual([], runtime._combat_sync[11]['pending'])

    def test_external_ignition_does_not_replay_pre_hit_time_as_fire(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        repairing = _critical_payload({
            'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
            'state': 'destroyed'}, destroyed=['leftTrackHealth'])
        runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=repairing, revision=1, base_revision=1)]})
        for index in range(5):
            runtime.update(.20, .20 + index * .20)

        ignited = _critical_payload(
            {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 170.0,
             'state': 'destroyed'},
            {'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
             'state': 'destroyed'},
            destroyed=['leftTrackHealth', 'fuelTankHealth'], fire=True)
        runtime.apply_snapshot({
            'server_tick': 2,
            'bots': [_snapshot_bot(
                health=900, critical=ignited,
                revision=2, base_revision=2, ack_seq=0)]})

        self.assertEqual(900, runtime.states[11]['health'])
        self.assertEqual(0.0, runtime.states[11]['combat_fire_elapsed'])
        self.assertEqual(0.0, runtime.states[11]['combat_fire_timer'])

    def test_authority_handoff_preserves_fire_duration_and_tick_phase(self):
        descriptor = _critical_descriptor()
        reload_duration = self.module._BotGunState(descriptor).reload_full
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=800, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, reload_time=reload_duration,
            reload_duration=reload_duration, critical=burning,
            combat_revision=22, combat_base_revision=1,
            combat_ack_seq=21, combat_fire_elapsed=4.4,
            combat_fire_timer=0.4)
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))

        for index in range(3):
            outgoing = runtime.update(
                .20, 100.2 + index * .20)[0]['bots'][0]
        self.assertEqual(750, outgoing['health'])
        self.assertEqual(5.0, outgoing['combat_fire_elapsed'])
        self.assertEqual(0.0, outgoing['combat_fire_timer'])

        for index in range(25):
            outgoing = runtime.update(
                .20, 100.8 + index * .20)[0]['bots'][0]
        self.assertEqual(500, outgoing['health'])
        self.assertFalse(outgoing['critical']['fire'])
        self.assertEqual(0.0, outgoing['combat_fire_elapsed'])
        self.assertEqual(0.0, outgoing['combat_fire_timer'])

    def test_authority_handoff_resets_to_same_base_server_ack_ahead(self):
        descriptor = _critical_descriptor()
        reload_duration = self.module._BotGunState(descriptor).reload_full
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, reload_time=reload_duration,
            reload_duration=reload_duration, critical=burning,
            combat_revision=4, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))

        local = runtime.update(.20, 100.2)[0]['bots'][0]
        self.assertEqual(4, local['combat_seq'])
        self.assertEqual(2.2, local['combat_fire_elapsed'])

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=6, base_revision=1, ack_seq=5,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual(800, state['health'])
        self.assertEqual(3.0, state['combat_fire_elapsed'])
        self.assertEqual((5, 5, 5), (
            state['combat_ack_seq'], state['combat_seq'], sync['next_seq']))
        self.assertEqual([], sync['pending'])
        self.assertEqual([], sync['unpublished_steps'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual(800, outgoing['health'])
        self.assertEqual(3.2, outgoing['combat_fire_elapsed'])
        self.assertEqual(6, outgoing['combat_seq'])

    def test_authority_handoff_resets_same_sequence_signature_collision(self):
        descriptor = _critical_descriptor()
        reload_duration = self.module._BotGunState(descriptor).reload_full
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, reload_time=reload_duration,
            reload_duration=reload_duration, critical=burning,
            combat_revision=4, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))

        local = runtime.update(.20, 100.2)[0]['bots'][0]
        self.assertEqual(4, local['combat_seq'])
        self.assertEqual(2.2, local['combat_fire_elapsed'])

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=800, critical=burning,
                revision=5, base_revision=1, ack_seq=4,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual(800, state['health'])
        self.assertEqual(3.0, state['combat_fire_elapsed'])
        self.assertEqual((4, 4, 4), (
            state['combat_ack_seq'], state['combat_seq'], sync['next_seq']))
        self.assertEqual([], sync['pending'])
        self.assertFalse(sync['authority_handoff_pending'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual(800, outgoing['health'])
        self.assertEqual(3.2, outgoing['combat_fire_elapsed'])
        self.assertEqual(5, outgoing['combat_seq'])

    def test_authority_handoff_new_base_ack_ahead_drops_old_lineage(self):
        descriptor = _critical_descriptor()
        reload_duration = self.module._BotGunState(descriptor).reload_full
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, reload_time=reload_duration,
            reload_duration=reload_duration, critical=burning,
            combat_revision=3, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))
        runtime.update(.20, 100.2)

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=700, critical=burning,
                revision=6, base_revision=6, ack_seq=5,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual((700, 3.0), (
            state['health'], state['combat_fire_elapsed']))
        self.assertEqual((6, 6, 5, 5), (
            state['combat_revision'], state['combat_base_revision'],
            state['combat_ack_seq'], state['combat_seq']))
        self.assertEqual([], sync['pending'])
        self.assertEqual([], sync['unpublished_steps'])
        self.assertFalse(sync['authority_handoff_pending'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual((700, 3.2, 6), (
            outgoing['health'], outgoing['combat_fire_elapsed'],
            outgoing['combat_seq']))

    def test_authority_handoff_new_base_same_sequence_does_not_replay(self):
        descriptor = _critical_descriptor()
        reload_duration = self.module._BotGunState(descriptor).reload_full
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        takeover_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=0.0, y=0.0, z=100.0, yaw=math.pi,
            fire_seq=0, shell_index=0, reload_time=reload_duration,
            reload_duration=reload_duration, critical=burning,
            combat_revision=3, combat_base_revision=1,
            combat_ack_seq=3, combat_fire_elapsed=2.0,
            combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bot_authority_id=2))
        runtime.battle_start(dict(
            self.start, bot_manifest=[takeover_bot]))
        local = runtime.update(.20, 100.2)[0]['bots'][0]
        self.assertEqual((4, 2.2), (
            local['combat_seq'], local['combat_fire_elapsed']))

        runtime.apply_snapshot({
            'server_tick': 10,
            'bots': [_snapshot_bot(
                health=750, critical=burning,
                revision=5, base_revision=5, ack_seq=4,
                fire_elapsed=3.0, fire_timer=0.0)]})

        state = runtime.states[11]
        sync = runtime._combat_sync[11]
        self.assertEqual((750, 3.0), (
            state['health'], state['combat_fire_elapsed']))
        self.assertEqual((5, 5, 4, 4), (
            state['combat_revision'], state['combat_base_revision'],
            state['combat_ack_seq'], state['combat_seq']))
        self.assertEqual([], sync['pending'])
        self.assertFalse(sync['authority_handoff_pending'])

        outgoing = runtime.update(.20, 100.4)[0]['bots'][0]
        self.assertEqual((750, 3.2, 5), (
            outgoing['health'], outgoing['combat_fire_elapsed'],
            outgoing['combat_seq']))

    def test_same_authority_same_sequence_signature_mismatch_raises(self):
        descriptor = _critical_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        burning = _critical_payload({
            'name': 'fuelTankHealth', 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed'}, destroyed=['fuelTankHealth'], fire=True)
        initial = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, critical=burning, combat_revision=4,
            combat_base_revision=1, combat_ack_seq=3,
            combat_fire_elapsed=2.0, combat_fire_timer=0.0)
        runtime.battle_start(dict(self.start, bots=[initial]))
        runtime.update(.20, 100.2)

        with self.assertRaisesRegex(
                ValueError, 'server bot combat ack is inconsistent'):
            runtime.apply_snapshot({
                'server_tick': 10,
                'bots': [_snapshot_bot(
                    health=800, critical=burning,
                    revision=5, base_revision=1, ack_seq=4,
                    fire_elapsed=3.0, fire_timer=0.0)]})

    def test_bot_snapshot_without_explicit_combat_contract_raises(self):
        self.runtime.battle_start(self.start)
        with self.assertRaises(ValueError):
            self.runtime.apply_snapshot({'bots': [
                {'id': 11, 'health': 1000, 'alive': True,
                 'critical': {}}]})

    def test_bot_snapshot_non_object_critical_raises_without_clearing_state(self):
        self.runtime.battle_start(self.start)
        before = dict(self.runtime.states[11])
        malformed = _snapshot_bot(critical={})
        malformed['critical'] = []

        with self.assertRaises(ValueError):
            self.runtime.apply_snapshot({'bots': [malformed]})

        self.assertEqual(before['critical'], self.runtime.states[11]['critical'])

    def test_static_critical_state_does_not_accumulate_replay_steps(self):
        self.runtime.battle_start(self.start)
        static = _critical_payload(crew_ko=['commander'])
        self.runtime.apply_snapshot({
            'server_tick': 1,
            'bots': [_snapshot_bot(
                critical=static, revision=1, base_revision=1)]})

        for index in range(1000):
            self.runtime.update(.04, 1.0 + index * .04)

        sync = self.runtime._combat_sync[11]
        self.assertEqual([], sync['unpublished_steps'])
        self.assertEqual([], sync['pending'])

    def test_1513_native_motion_emits_input_without_integrating_pose(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *args: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            native_motion=True, baked_graph=_graph())
        runtime.battle_start(self.start)
        before = dict(runtime.states[11])

        outgoing = runtime.update(.04, 1.0)
        state = outgoing[0]['bots'][0]

        self.assertEqual(1, state['movement_dir'])
        self.assertEqual(0, state['rotation_dir'])
        self.assertEqual((before['x'], before['y'], before['z'],
                          before['yaw']),
                         (state['x'], state['y'], state['z'], state['yaw']))
        self.assertTrue(runtime.apply_native_pose(
            11, (7.0, 2.0, 9.0), 0.75, 4.5))
        self.assertEqual((7.0, 2.0, 9.0, 0.75, 4.5), (
            runtime.states[11]['x'], runtime.states[11]['y'],
            runtime.states[11]['z'], runtime.states[11]['yaw'],
            runtime.states[11]['speed']))

    @staticmethod
    def _stationary_command():
        return {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'hold',
            'aim_position': (0.0, 0.0, 10.0),
            'face_position': (0.0, 0.0, 10.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }

    def test_reverse_probe_pitch_is_stored_in_hull_coordinates(self):
        command = self._stationary_command()
        command.update({
            'throttle': -1.0,
            'combat_mode': 'route',
            'move_position': (0.0, 0.0, -100.0),
            'recovery_mode': 'drive',
            'movement_intent': True,
        })
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(command),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.2},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)

        runtime.update(0.04, 1.0)

        state = runtime.states[11]
        self.assertLess(state['speed'], 0.0)
        self.assertAlmostEqual(math.atan(0.2), state['last_drive_pitch'])

    def test_reverse_coast_uses_signed_speed_for_travel_pitch(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False,
                'water': False, 'slope': 0.2},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: -20.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(
            speed=-12.0, y=0.0, vertical_speed=0.0,
            airborne=False, grounded_once=True,
            last_drive_pitch=math.atan(0.2))

        runtime.update(0.04, 1.0)

        self.assertLess(state['speed'], 0.0)
        self.assertAlmostEqual(math.atan(0.2),
                               state['last_drive_pitch'])
        self.assertTrue(state['airborne'])
        self.assertGreater(state['vertical_speed'], 0.0)
        self.assertGreater(state['y'], 0.0)

    def test_overlapping_bots_are_separated_without_spawn_deadlock(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        start = dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Second'},
        ])
        runtime.battle_start(start)
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(x=0.8, y=0.0, z=0.0, yaw=0.0)

        outgoing = runtime.update(.04, 1.0)

        self.assertEqual('bot_state', outgoing[0]['type'])
        self.assertLess(runtime.states[11]['x'], 0.0)
        self.assertGreater(runtime.states[12]['x'], 0.8)
        self.assertEqual(0, runtime.states[11]['movement_dir'])
        self.assertEqual(0, runtime.states[12]['movement_dir'])

    def test_exactly_coincident_bots_separate_in_opposite_directions(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        start = dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Second'},
        ])
        runtime.battle_start(start)
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(x=0.0, y=0.0, z=0.0, yaw=0.0)

        runtime.update(.04, 1.0)

        self.assertGreater(runtime.states[11]['x'], 0.0)
        self.assertLess(runtime.states[12]['x'], 0.0)
        self.assertGreater(
            abs(runtime.states[11]['x'] - runtime.states[12]['x']), 2.0)

    def test_bot_push_decay_is_equal_across_render_rates(self):
        def run_for_one_second(frame_rate):
            runtime = self.module.BotRuntime(1)
            runtime._clear = lambda *unused: True
            state = {
                'id': 11, 'team': 1, 'slot': 0, 'alive': True,
                'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
                'mass': 25000.0,
                'collision_shape': self.module.tank_collision.DEFAULT_SHAPE,
                'speed': 0.0, 'push_x': 10.0, 'push_z': -4.0,
            }
            runtime.states = {11: state}
            dt = 1.0 / float(frame_rate)
            first_push = None
            for frame in range(frame_rate):
                runtime._resolve_tank_contacts([], frame * dt, dt)
                if first_push is None:
                    first_push = state['push_x']
            return first_push, state['push_x']

        results = dict((frame_rate, run_for_one_second(frame_rate))
                       for frame_rate in (20, 30, 60))
        expected = 10.0 * 0.90 ** 60

        self.assertAlmostEqual(9.0, results[60][0], places=12)
        for unused_frame_rate, (unused_first, final_push) in results.items():
            self.assertAlmostEqual(expected, final_push, places=12)
        self.assertAlmostEqual(results[20][1], results[30][1], places=12)
        self.assertAlmostEqual(results[30][1], results[60][1], places=12)

    def test_current_human_contact_waits_for_receipt_impulse(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Pusher'},
        ]))
        runtime._clear = lambda *unused: True
        state = runtime.states[11]
        player = {
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            'x': 6.5, 'y': 0.0, 'z': 0.0,
            'yaw': math.pi / 2.0, 'speed': 0.0, 'alive': True,
        }
        player = _admit_player(player)

        state.update(x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
                     speed=10.0, push_x=0.0, push_z=0.0)
        runtime._resolve_tank_contacts([player], None, .04)
        friendly_speed = state['speed']

        state.update(x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
                     speed=10.0, push_x=0.0, push_z=0.0)
        player['team'] = 2
        runtime._resolve_tank_contacts([player], None, .04)

        self.assertEqual(10.0, friendly_speed)
        self.assertEqual(10.0, state['speed'])

        # A wreck has one established immovable-body response regardless of
        # team. Live human velocity and HP wait for a historical receipt.
        player['alive'] = False
        for team in (1, 2):
            state.update(x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
                         speed=10.0, push_x=0.0, push_z=0.0)
            player['team'] = team
            runtime._resolve_tank_contacts([player], None, .04)
            self.assertEqual(10.0, state['speed'])

    def test_tank_separation_does_not_push_bots_through_world_geometry(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        start = dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Second'},
        ])
        runtime.battle_start(start)
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(x=0.8, y=0.0, z=0.0, yaw=0.0)

        runtime.update(.04, 1.0)

        self.assertEqual((0.0, 0.8), (
            runtime.states[11]['x'], runtime.states[12]['x']))
        self.assertEqual((0.0, 0.0), (
            runtime.states[11]['push_x'], runtime.states[12]['push_x']))

    def test_bot_pair_uses_native_contact_armor_and_reports_one_transaction(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        probes = []

        def ram_contact_probe(first, second, contact):
            probes.append((first['id'], second['id'], contact))
            return 45.0, 80.0

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            ram_contact_probe=ram_contact_probe)
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Second'},
        ]))
        runtime._clear = lambda *unused: True
        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
            speed=10.0, push_x=0.0, push_z=0.0)
        runtime.states[12].update(
            x=6.5, y=0.0, z=0.0, yaw=math.pi / 2.0,
            speed=0.0, push_x=0.0, push_z=0.0)

        reports = runtime._resolve_tank_contacts([], 10.0, .04)

        self.assertEqual(1, len(probes))
        self.assertEqual(1, len(reports))
        self.assertEqual('bot_ram', reports[0]['type'])
        self.assertEqual((11, 'bot', 12), (
            reports[0]['bot_id'], reports[0]['target_kind'],
            reports[0]['target_id']))
        self.assertTrue(
            reports[0]['damage_to_bot'] or reports[0]['damage_to_target'])

        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
            speed=10.0, push_x=0.0, push_z=0.0)
        runtime.states[12].update(
            x=6.5, y=0.0, z=0.0, yaw=math.pi / 2.0,
            speed=0.0, push_x=0.0, push_z=0.0)
        repeated = runtime._resolve_tank_contacts([], 10.04, .04)

        self.assertEqual(1, len(probes))
        self.assertEqual([], repeated)

    def test_friendly_bot_pair_never_probes_or_reports_ram_damage(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        probes = []

        def ram_contact_probe(first, second, contact):
            probes.append((first['id'], second['id'], contact))
            return 45.0, 80.0

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            ram_contact_probe=ram_contact_probe)
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Second'},
        ]))
        runtime._clear = lambda *unused: True
        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
            speed=10.0, push_x=0.0, push_z=0.0)
        runtime.states[12].update(
            x=6.5, y=0.0, z=0.0, yaw=math.pi / 2.0,
            speed=0.0, push_x=0.0, push_z=0.0)

        reports = runtime._resolve_tank_contacts([], 10.0, .04)

        self.assertEqual([], reports)
        self.assertEqual([], probes)

    def test_bot_human_native_armor_probe_carries_player_record_identity(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        probes = []

        def ram_contact_probe(first, second, contact):
            probes.append((dict(first), dict(second), contact))
            return 45.0, 80.0

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph(),
            ram_contact_probe=ram_contact_probe)
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
        ]))
        runtime._clear = lambda *unused: True
        runtime.states[11].update(
            x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
            speed=10.0, push_x=0.0, push_z=0.0)
        player = _admit_player({
            'id': 2, 'team': 2, 'vehicle': 'ussr:R11_MS-1',
            'x': 6.5, 'y': 0.0, 'z': 0.0,
            'yaw': math.pi / 2.0, 'speed': 0.0, 'alive': True,
        })

        runtime._resolve_tank_contacts([player], 10.0, .04)

        self.assertEqual(1, len(probes))
        first, second, unused_contact = probes[0]
        self.assertEqual(('bot', 11), (
            first['kind'], first['network_id']))
        self.assertEqual(('player', 2), (
            second['kind'], second['network_id']))
        self.assertEqual(self.module.HUMAN_TARGET_ID_BASE + 2, second['id'])

    def test_current_human_contact_never_reports_damage_without_receipt(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
                     speed=10.0)
        player = {
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            # End-to-end OBB contact. A deeply interpenetrating parallel pair
            # correctly chooses the shorter sideways escape axis instead.
            'x': 6.5, 'y': 0.0, 'z': 0.0, 'yaw': math.pi / 2.0,
            'speed': 0.0,
            'alive': True,
        }
        player = _admit_player(player)

        outgoing = runtime.update(.04, 10.0, players=[player])

        self.assertEqual(['bot_state', 'bot_observation'],
                         [message['type'] for message in outgoing])

        state.update(x=0.0, z=0.0, yaw=math.pi / 2.0, speed=10.0,
                     push_x=0.0, push_z=0.0)
        repeated = runtime.update(.04, 10.2, players=[player])
        self.assertNotIn('bot_ram', [message['type'] for message in repeated])

        # Cooldown expiry alone must not replay one persistent OBB overlap.
        state.update(x=0.0, z=0.0, yaw=math.pi / 2.0, speed=10.0,
                     push_x=0.0, push_z=0.0)
        persistent = runtime.update(.04, 10.8, players=[player])
        self.assertNotIn(
            'bot_ram', [message['type'] for message in persistent])

        # A complete separated frame re-arms the pair for a genuinely new
        # impact episode.
        player['x'] = 30.0
        state.update(x=0.0, z=0.0, yaw=math.pi / 2.0, speed=10.0,
                     push_x=0.0, push_z=0.0)
        separated = runtime.update(.04, 10.9, players=[player])
        self.assertNotIn(
            'bot_ram', [message['type'] for message in separated])

        player['x'] = 6.5
        state.update(x=0.0, z=0.0, yaw=math.pi / 2.0, speed=10.0,
                     push_x=0.0, push_z=0.0)
        new_contact = runtime.update(.04, 11.0, players=[player])
        self.assertNotIn(
            'bot_ram', [message['type'] for message in new_contact])

    def test_human_ram_receipt_hit_validation_uses_frozen_pitch_and_roll(self):
        body = {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'pitch': 0.80, 'roll': -0.12,
            'shape': (1.5, 3.5, -0.8, 2.0),
        }
        axes = self.module.tank_collision.pose_axes(
            body['yaw'], body['pitch'], body['roll'])
        local = (0.0, 1.0, body['shape'][1])
        hit = tuple(sum(
            local[row] * axes[row][index] for row in range(3))
            for index in range(3))

        self.assertTrue(self.module.BotRuntime._native_ram_hit_supported(
            body, hit))
        self.assertFalse(self.module.BotRuntime._native_ram_hit_supported(
            dict(body, pitch=0.0, roll=0.0), hit))

    def test_human_ram_receipt_uses_historical_bot_slope_pose(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        current = runtime.states[11]
        current.update(x=0.0, y=0.0, z=0.0, yaw=0.0,
                       pitch=0.80, roll=-0.12, speed=0.0,
                       push_x=0.0, push_z=0.0)
        historical = dict(current)
        historical.update(ram_vx=0.0, ram_vz=0.0)
        axes = self.module.tank_collision.pose_axes(
            historical['yaw'], historical['pitch'], historical['roll'])
        local = (0.5, 0.0, historical['collision_shape'][1])
        hit = tuple(sum(
            local[row] * axes[row][index] for row in range(3))
            for index in range(3))
        player_z = hit[2] + 3.0
        player = _admit_player({
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            'x': hit[0], 'y': hit[1], 'z': player_z, 'yaw': 0.0,
            'speed': -16.0, 'alive': True,
            'ram_contact': {
                'seq': 11, 'bot_id': 11, 'bot_state_revision': 41,
                'presentation_time_us': 180000,
                'native_contact_time_us': 180000,
                'contact_x': hit[0], 'contact_y': hit[1],
                'contact_z': hit[2],
                'contact_normal_x': 0.0,
                'contact_normal_z': 1.0,
                'contact_armor_player': 20.0,
                'contact_armor_bot': 20.0,
                'contact_spall_player': 1.0,
                'contact_bonus_player': 0.0,
                'contact_screened_player': False,
                'contact_screened_bot': False,
                'x': hit[0], 'y': hit[1], 'z': player_z, 'yaw': 0.0,
                'pitch': 0.0, 'roll': 0.0,
                'vx': 0.0, 'vy': 0.0, 'vz': -16.0,
                'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
            },
            '_ram_contact_bot_state': historical,
        })

        reports = runtime._resolve_human_ram_receipts(
            [player], 10.0, step=0.04)

        self.assertEqual(1, len(reports))
        self.assertGreater(reports[0]['damage_to_bot'], 0)
        self.assertGreater(reports[0]['damage_to_target'], 0)

    def test_human_ram_receipt_allows_bounded_native_pose_skew_only(self):
        body = {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0,
            'shape': (1.5, 3.5, -0.8, 2.0),
        }

        self.assertTrue(self.module.BotRuntime._native_ram_hit_supported(
            body, (2.0, 0.0, 0.0)))
        self.assertFalse(self.module.BotRuntime._native_ram_hit_supported(
            body, (4.0, 0.0, 0.0)))

    def test_human_ram_receipt_replays_pre_correction_pose_once(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        current = runtime.states[11]
        current.update(x=0.0, y=0.0, z=6.5, yaw=math.pi,
                       speed=0.0, push_x=0.0, push_z=0.0)
        historical = dict(current)
        historical['ram_vx'] = 0.0
        historical['ram_vz'] = 0.0
        player = {
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            # The public pose is already corrected and no longer overlaps.
            'x': 0.0, 'y': 0.0, 'z': -2.0, 'yaw': 0.0,
            'speed': 0.0, 'alive': True,
            'ram_contact': {
                'seq': 7, 'bot_id': 11, 'bot_state_revision': 37,
                'presentation_time_us': 150000,
                'native_contact_time_us': 150000,
                'contact_x': 0.0, 'contact_y': 0.0,
                'contact_z': 3.25,
                'contact_normal_x': 0.0,
                'contact_normal_z': -1.0,
                'contact_armor_player': 20.0,
                'contact_armor_bot': 20.0,
                'contact_spall_player': 1.0,
                'contact_bonus_player': 0.0,
                'contact_screened_player': False,
                'contact_screened_bot': False,
                'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
                'vx': 0.0, 'vy': 0.0, 'vz': 16.0,
                'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
            },
            '_ram_contact_bot_state': historical,
        }
        player = _admit_player(player)

        first = runtime._resolve_human_ram_receipts(
            [player], 10.0, step=.04)
        push_after_first = (current['push_x'], current['push_z'])
        repeated = runtime._resolve_human_ram_receipts(
            [player], 10.1, step=.04)
        push_after_retry = (current['push_x'], current['push_z'])
        acknowledged = dict(player, ram_contact_resolved_seq=7)
        after_ack = runtime._resolve_human_ram_receipts(
            [acknowledged], 10.2)
        current['x'] = 50.0
        distant = dict(player)
        distant['ram_contact_resolved_seq'] = 7
        distant['ram_contact'] = dict(
            player['ram_contact'], seq=8,
            presentation_time_us=1000000)
        replayed_distant = runtime._resolve_human_ram_receipts(
            [distant], 11.0)
        distant['ram_contact_resolved_seq'] = 8
        after_second_ack = runtime._resolve_human_ram_receipts(
            [distant], 11.1)

        self.assertEqual(1, len(first))
        self.assertEqual((11, 'human', 2), (
            first[0]['bot_id'], first[0]['target_kind'],
            first[0]['target_id']))
        self.assertEqual((2, 7), (
            first[0]['ram_contact_player_id'],
            first[0]['ram_contact_seq']))
        self.assertGreater(first[0]['damage_to_bot'], 0)
        self.assertGreater(first[0]['damage_to_target'], 0)
        self.assertEqual(first, repeated)
        self.assertEqual(push_after_first, push_after_retry)
        self.assertEqual([], after_ack)
        self.assertEqual(1, len(replayed_distant))
        self.assertGreater(replayed_distant[0]['damage_to_target'], 0)
        self.assertEqual([], after_second_ack)
        self.assertEqual({2: 8}, runtime._human_ram_receipt_seq)
        self.assertEqual({}, runtime._human_ram_report_cache)

    def test_human_ram_receipt_and_current_detector_do_not_double_report(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        current = runtime.states[11]
        current.update(x=0.0, y=0.0, z=6.5, yaw=math.pi,
                       speed=0.0, push_x=0.0, push_z=0.0)
        historical = dict(current)
        historical.update(ram_vx=0.0, ram_vz=0.0)
        player = {
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 16.0, 'alive': True,
            'ram_contact': {
                'seq': 8, 'bot_id': 11, 'bot_state_revision': 38,
                'presentation_time_us': 160000,
                'native_contact_time_us': 160000,
                'contact_x': 0.0, 'contact_y': 0.0,
                'contact_z': 3.25,
                'contact_normal_x': 0.0,
                'contact_normal_z': -1.0,
                'contact_armor_player': 20.0,
                'contact_armor_bot': 20.0,
                'contact_spall_player': 1.0,
                'contact_bonus_player': 0.0,
                'contact_screened_player': False,
                'contact_screened_bot': False,
                'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
                'vx': 0.0, 'vy': 0.0, 'vz': 16.0,
                'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
            },
            '_ram_contact_bot_state': historical,
        }
        player = _admit_player(player)

        reports = runtime._resolve_tank_contacts([player], 10.0, 0.04)

        self.assertEqual(1, len([
            report for report in reports if report['type'] == 'bot_ram']))
        # One e=0 response gives the stationary 25t bot half of the 16m/s
        # normal velocity, followed by the existing time-based push damping.
        # The same-frame current detector must not apply that impulse twice.
        expected_push = 8.0 * (0.90 ** (0.04 * 60.0))
        self.assertAlmostEqual(expected_push, current['push_z'], places=5)

    def test_human_ram_receipt_uses_obb_face_normal_for_side_scrape(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        current = runtime.states[11]
        current.update(x=0.0, y=0.0, z=0.0, yaw=0.0,
                       speed=10.0, push_x=0.0, push_z=0.0)
        historical = dict(current)
        historical.update(ram_vx=0.2, ram_vz=10.0)
        player = _admit_player({
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            'x': 2.8, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 10.0, 'alive': True,
            'ram_contact': {
                'seq': 9, 'bot_id': 11, 'bot_state_revision': 39,
                'presentation_time_us': 170000,
                'native_contact_time_us': 170000,
                'contact_x': 1.4, 'contact_y': 0.0,
                'contact_z': 0.0,
                'contact_normal_x': 1.0,
                'contact_normal_z': 0.0,
                'contact_armor_player': 20.0,
                'contact_armor_bot': 20.0,
                'contact_spall_player': 1.0,
                'contact_bonus_player': 0.0,
                'contact_screened_player': False,
                'contact_screened_bot': False,
                'x': 2.8, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
                'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
                'bot_vx': 0.2, 'bot_vy': 0.0, 'bot_vz': 10.0,
            },
            '_ram_contact_bot_state': historical,
        })

        original = self.module.tank_collision.ram_damage
        calls = []

        def capture_ram_damage(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        self.module.tank_collision.ram_damage = capture_ram_damage
        try:
            reports = runtime._resolve_human_ram_receipts(
                [player], 10.0)
            flipped = dict(player)
            flipped['ram_contact'] = dict(
                player['ram_contact'], seq=10,
                contact_normal_x=-1.0)
            rejected = runtime._resolve_human_ram_receipts(
                [flipped], 10.1)
        finally:
            self.module.tank_collision.ram_damage = original

        self.assertEqual(1, len(reports))
        self.assertEqual((0, 0), (
            reports[0]['damage_to_bot'], reports[0]['damage_to_target']))
        self.assertEqual(1, len(calls))
        self.assertAlmostEqual(0.2, calls[0][0])
        self.assertEqual(1, len(rejected))
        self.assertEqual((0, 0), (
            rejected[0]['damage_to_bot'],
            rejected[0]['damage_to_target']))

    def test_human_ram_ledger_resolves_every_contact_and_terminal_noop(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        historical = dict(runtime.states[11])
        historical.update(x=0.0, y=0.0, z=6.5, yaw=math.pi,
                          ram_vx=0.0, ram_vz=0.0)

        def receipt(seq, presentation_time_us, player_z=0.0):
            return {
                'seq': seq, 'bot_id': 11, 'bot_state_revision': 40,
                'presentation_time_us': presentation_time_us,
                'native_contact_time_us': presentation_time_us,
                'contact_x': 0.0, 'contact_y': 0.0,
                'contact_z': 3.25,
                'contact_normal_x': 0.0,
                'contact_normal_z': -1.0,
                'contact_armor_player': 20.0,
                'contact_armor_bot': 20.0,
                'contact_spall_player': 1.0,
                'contact_bonus_player': 0.0,
                'x': 0.0, 'y': 0.0, 'z': player_z, 'yaw': 0.0,
                'contact_screened_player': False,
                'contact_screened_bot': False,
                'vx': 0.0, 'vy': 0.0, 'vz': 16.0,
                'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
                '_ram_contact_bot_state': historical,
            }

        player = {
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': -30.0, 'yaw': 0.0,
            'speed': 0.0, 'alive': True,
            'ram_contacts': [
                receipt(1, 1000000), receipt(2, 2000000),
                receipt(3, 3000000, player_z=-30.0)],
        }
        player = _admit_player(player)

        first = runtime._resolve_human_ram_receipts([player], 20.0)
        player['ram_contact_resolved_seq'] = 1
        second = runtime._resolve_human_ram_receipts([player], 20.1)
        player['ram_contact_resolved_seq'] = 2
        third = runtime._resolve_human_ram_receipts([player], 20.2)
        reports = first + second + third

        self.assertEqual([1, 2, 3], [
            report['ram_contact_seq'] for report in reports])
        self.assertTrue(all(
            report['damage_to_target'] > 0 for report in reports[:2]))
        self.assertEqual((0, 0), (
            reports[2]['damage_to_bot'], reports[2]['damage_to_target']))

    def test_human_ram_ledger_does_not_overtake_missing_history(self):
        descriptor = _combat_descriptor()
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(self.start)
        historical = dict(runtime.states[11])
        historical.update(id=11, ram_vx=0.0, ram_vz=0.0)

        def receipt(seq, include_history):
            result = {
                'seq': seq, 'bot_id': 11, 'bot_state_revision': 40,
                'presentation_time_us': seq * 1000000,
                'x': 30.0, 'y': 0.0, 'z': 30.0, 'yaw': 0.0,
                'vx': 0.0, 'vz': 0.0,
            }
            if include_history:
                result['_ram_contact_bot_state'] = historical
            return result

        player = {
            'id': 2, 'team': 1, 'vehicle': 'ussr:R11_MS-1',
            'alive': True,
            'ram_contacts': [receipt(1, False), receipt(2, True)],
        }
        player = _admit_player(player)

        self.assertEqual([], runtime._resolve_human_ram_receipts(
            [player], 20.0))
        player['ram_contacts'][0]['_ram_contact_bot_state'] = historical
        first = runtime._resolve_human_ram_receipts([player], 20.1)

        self.assertEqual([1], [
            report['ram_contact_seq'] for report in first])
        self.assertNotIn((2, 2), runtime._human_ram_report_cache)

    def test_ram_diagnostic_logs_once_per_admitted_event(self):
        descriptor = _combat_descriptor()
        descriptor.physics['weight'] = 25000.0
        descriptor.hull.hitTester = _HitTester1513(
            (-1.5, -1.0, -3.5), (1.5, 1.0, 3.5))
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 2, 'slot': 0, 'name': 'Rammer',
             'vehicle': 'ussr:T-34'},
        ]))
        state = runtime.states[11]
        player = {
            'id': 2, 'team': 1, 'vehicle': 'germany:Maus',
            'x': 6.5, 'y': 0.0, 'z': 0.0,
            'yaw': math.pi / 2.0, 'speed': 0.0, 'alive': True,
        }
        player = _admit_player(player)
        state.update(x=0.0, y=0.0, z=0.0, yaw=math.pi / 2.0,
                     speed=10.0, push_x=0.0, push_z=0.0)
        historical = dict(state)
        historical.update(ram_vx=10.0, ram_vz=0.0)
        player.update({
            'ram_contact': {
                'seq': 1, 'bot_id': 11, 'bot_state_revision': 1,
                'presentation_time_us': 10000000,
                'native_contact_time_us': 10000000,
                'contact_x': 3.25, 'contact_y': 0.0,
                'contact_z': 0.0,
                'contact_normal_x': 1.0,
                'contact_normal_z': 0.0,
                'contact_armor_player': 20.0,
                'contact_armor_bot': 20.0,
                'contact_spall_player': 1.0,
                'contact_bonus_player': 0.0,
                'contact_screened_player': False,
                'contact_screened_bot': False,
                'x': 6.5, 'y': 0.0, 'z': 0.0,
                'yaw': math.pi / 2.0, 'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
                'bot_vx': 10.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
            },
            '_ram_contact_bot_state': historical,
        })
        capture = io.StringIO()
        previous_stdout = sys.stdout
        try:
            sys.stdout = capture
            first = runtime._resolve_tank_contacts([player], 10.0, .04)
            first_push = state['push_x']
            state.update(x=0.0, z=0.0, yaw=math.pi / 2.0,
                         speed=10.0, push_x=first_push, push_z=0.0)
            repeated = runtime._resolve_tank_contacts(
                [player], 10.2, .04)
            player['ram_contact_resolved_seq'] = 1
            acknowledged = runtime._resolve_tank_contacts(
                [player], 10.3, .04)
        finally:
            sys.stdout = previous_stdout

        lines = [line for line in capture.getvalue().splitlines()
                 if 'RAM diagnostic' in line]
        self.assertEqual(1, len(first))
        self.assertEqual(first, repeated)
        self.assertEqual([], acknowledged)
        self.assertEqual(1, len(lines))
        self.assertIn('self_id=11 self_vehicle=ussr:T-34', lines[0])
        self.assertIn(
            'other_kind=human other_id=2 other_vehicle=germany:Maus',
            lines[0])
        self.assertIn('mass_self=25000.000 mass_other=25000.000', lines[0])
        self.assertIn('velocity_self_xz=(10.0000,0.0000)', lines[0])
        self.assertIn('normal_closing_speed=10.00000', lines[0])
        self.assertIn('damage_to_self=', lines[0])
        self.assertIn('damage_to_other=', lines[0])

    def test_enemy_bots_and_humans_have_distinct_target_ids(self):
        self.start['bots'].append(
            {'id': 2, 'team': 1, 'slot': 0, 'name': 'OtherBot'})
        self.runtime.battle_start(self.start)
        self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'alive': True,
             'x': 4, 'y': 0, 'z': 4,
             'effective_params': _effective_params_snapshot()}])
        contacts = self.adapters[0].calls[0][0]['contacts']
        by_kind = dict((item['kind'], item) for item in contacts)
        self.assertEqual(2, by_kind['bot']['id'])
        self.assertEqual(self.module.HUMAN_TARGET_ID_BASE + 2,
                         by_kind['human']['id'])

    def test_non_authority_does_not_emit_or_construct_manifest(self):
        self.start['bot_authority_id'] = 2
        self.assertEqual([], self.runtime.battle_start(self.start))
        self.assertEqual([], self.runtime.update(.1, 1.0))

    def test_server_snapshot_kills_local_bot_and_stops_future_fire(self):
        self.runtime.battle_start(self.start)
        self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5,
             'effective_params': _effective_params_snapshot()}])

        self.runtime.apply_snapshot({'bots': [
            _snapshot_bot(health=0, alive=False,
                          revision=1, base_revision=1)]})

        self.assertFalse(self.runtime.states[11]['alive'])
        self.assertEqual(0.0, self.runtime.states[11]['speed'])
        final = self.runtime.update(.1, 2.0, players=[
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5,
             'effective_params': _effective_params_snapshot()}])
        self.assertFalse(final[0]['bots'][0]['alive'])
        self.assertEqual(0, self.runtime.states[11]['fire_seq'])

    def test_terminal_snapshot_freezes_all_bot_updates(self):
        self.runtime.battle_start(self.start)
        self.runtime.apply_snapshot({
            'battle_result': {'winner': 2, 'reason': 'team_eliminated'},
            'bots': [_snapshot_bot()]})

        self.assertTrue(self.runtime.finished)
        self.assertEqual([], self.runtime.update(.1, 2.0))

    def test_visibility_probes_are_cached_and_staggered(self):
        calls = []
        self.runtime.visibility_probe = lambda source, target: (
            calls.append((source['id'], target['network_id'])) or True)
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        players = [{'id': 2, 'team': 1, 'alive': True,
                    'x': state['x'] + 100,
                    'y': state['y'], 'z': state['z']}]
        players = [_admit_player(value) for value in players]

        self.runtime.update(.04, 1.0, players=players)
        self.runtime.update(.04, 1.04, players=players)

        self.assertEqual(set(((2, 11), (11, 2))), set(calls))
        self.assertEqual(2, len(calls))

    def test_visibility_fixed_cadence_and_fire_edge_invalidation(self):
        calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            visibility_probe=lambda source, target, fired=False: (
                calls.append((source['id'], target['network_id'], fired)) or
                True))
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        target = _admit_player({
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'ussr:R11_MS-1',
            'position': (0.0, 0.0, 100.0),
            'speed': 0.0, 'fire_seq': 0,
        }, base_moving=0.0, base_still=0.0)

        self.assertTrue(runtime._visible(source, target, 1.0))
        self.assertTrue(runtime._visible(
            source, target,
            1.0 + self.module.VISIBILITY_SAMPLE_SECONDS - 0.000001))
        self.assertEqual(1, len(calls))
        self.assertTrue(runtime._visible(
            source, target,
            1.0 + self.module.VISIBILITY_SAMPLE_SECONDS + 0.000001))
        self.assertEqual(2, len(calls))

        target['fire_seq'] = 1
        self.assertTrue(runtime._visible(
            source, target,
            1.0 + self.module.VISIBILITY_SAMPLE_SECONDS + 0.01))
        self.assertEqual(3, len(calls))
        self.assertTrue(calls[-1][2])

    def test_visibility_tick_projection_matches_uncached_observer_order(self):
        calls = [[], []]

        def build(index):
            return self.module.BotRuntime(
                1, descriptor_resolver=lambda unused: _combat_descriptor(),
                visibility_probe=lambda source, target, fired=False: (
                    calls[index].append((
                        source['id'], target['network_id'], bool(fired))) or
                    {'line_of_sight': (source['id'] +
                                       target['network_id']) % 3 != 0,
                     'foliage_bonus': (0.05 if source['id'] % 2 else 0.0)}))

        cached_runtime = build(0)
        baseline_runtime = build(1)
        sources = [
            {'id': bot_id, 'x': float(bot_id - 11) * 3.0,
             'y': 0.0, 'z': 0.0, 'view_range': 445.0}
            for bot_id in range(11, 16)
        ]
        target = _admit_player({
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'ussr:R11_MS-1',
            'position': (0.0, 0.0, 300.0),
            'speed': 0.0, 'fire_seq': 0,
        }, base_moving=0.05, base_still=0.10)
        generator = random.Random(1513)
        events = (
            (1.00, 0.0, 0),
            (1.04, 0.0, 0),
            (1.08, 8.0, 0),
            (1.12, 0.0, 1),
            (1.32, 0.0, 1),
        )
        for now, speed, fire_seq in events:
            target['speed'] = speed
            target['fire_seq'] = fire_seq
            order = list(sources)
            generator.shuffle(order)
            order.append(order[0])
            tick_cache = {}
            cached_values = [cached_runtime._visible(
                source, target, now, tick_cache) for source in order]
            baseline_values = [baseline_runtime._visible(
                source, target, now) for source in order]
            self.assertEqual(baseline_values, cached_values)
            self.assertEqual(baseline_runtime._visibility_fire,
                             cached_runtime._visibility_fire)
            self.assertEqual(baseline_runtime._visibility_still,
                             cached_runtime._visibility_still)
            self.assertEqual(baseline_runtime._visibility_cache,
                             cached_runtime._visibility_cache)
        self.assertEqual(calls[1], calls[0])

    def test_contacts_compute_source_view_range_once_per_decision(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            visibility_probe=lambda *unused: True)
        source = {
            'id': 11, 'team': 1,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        players = [_admit_player({
            'id': player_id, 'team': 2, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': float(distance),
            'speed': 0.0, 'fire_seq': 0,
        }, base_moving=0.0, base_still=0.0)
                   for player_id, distance in ((2, 100), (3, 150), (4, 200))]
        calls = []
        original = runtime._source_view_range

        def source_view_range(*args):
            calls.append(args)
            return original(*args)

        runtime._source_view_range = source_view_range
        runtime._contacts_for(
            source, players, 1.0, visibility_tick={})
        self.assertEqual(1, len(calls))

    def test_visibility_upper_bound_skips_only_impossible_native_probes(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        probes = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            visibility_probe=lambda source, target, fired=False: (
                probes.append(target['network_id']) or True))
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }

        targets = [
            {'id': self.module.HUMAN_TARGET_ID_BASE + target_id,
             'kind': 'human', 'network_id': target_id,
             'vehicle': 'ussr:R11_MS-1',
             'position': (0.0, 0.0, distance),
             'speed': 0.0, 'fire_seq': 0,
             'effective_params': _effective_params_snapshot()}
            for target_id, distance in ((2, 300.0), (3, 400.0),
                                        (4, 325.0))
        ]

        visible = [runtime._visible(source, target, 1.0)
                   for target in targets]

        self.assertEqual([True, False, True], visible)
        # The baseline probe order is 2, 3, 4. The fast path removes only the
        # impossible middle ray and leaves the remaining order unchanged.
        self.assertEqual([2, 4], probes)
        self.assertEqual(2, runtime.probe_totals()[0])

    def test_visibility_upper_bound_retains_24_fps_cache_and_shot_refresh(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        probes = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            visibility_probe=lambda source, target, fired=False: (
                probes.append((target['network_id'], bool(fired))) or True))
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'ussr:R11_MS-1',
            'position': (0.0, 0.0, 400.0),
            'speed': 0.0, 'fire_seq': 0,
        }
        target = _admit_player(target)

        for frame in range(12):
            self.assertFalse(runtime._visible(
                source, target, 1.0 + frame / 24.0))
        self.assertEqual([], probes)
        target['fire_seq'] = 1
        self.assertTrue(runtime._visible(source, target, 1.5))
        self.assertTrue(runtime._visible(source, target, 1.5 + 1.0 / 24.0))
        self.assertEqual([(2, True)], probes)

    def test_visibility_upper_bound_preserves_descriptor_failure_fallback(self):
        descriptor_calls = []
        probes = []

        def descriptor_resolver(vehicle_name):
            descriptor_calls.append(vehicle_name)
            raise RuntimeError('descriptor unavailable')

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=descriptor_resolver,
            visibility_probe=lambda source, target, fired=False: (
                probes.append(target['network_id']) or True))
        source = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'missing:vehicle',
            'position': (0.0, 0.0, 400.0),
            'speed': 0.0, 'fire_seq': 0,
        }
        target = _admit_player(
            target, base_moving=0.0, base_still=0.0)

        self.assertTrue(runtime._visible(source, target, 1.0))
        self.assertEqual(['missing:vehicle'], descriptor_calls)
        self.assertEqual([2], probes)

    def test_bot_spotting_applies_target_camouflage_and_shot_penalty(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        camouflage_calls = []

        def base_invisibility(crew_factor, camouflage_id):
            camouflage_calls.append((crew_factor, camouflage_id))
            return (0.30 * crew_factor, 0.40 * crew_factor)

        descriptor.computeBaseInvisibility = base_invisibility
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        source = runtime.states[11]
        source.update(x=0.0, y=0.0, z=0.0)
        player = {
            'id': 2, 'team': 1, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'camouflage_id': 37,
            'x': 0.0, 'y': 0.0, 'z': 400.0,
            'speed': 0.0, 'fire_seq': 0,
        }
        player = _admit_player(player)

        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 1.0)
        self.assertFalse(contacts[0]['visible'])
        self.assertEqual([], camouflage_calls)

        player['fire_seq'] = 1
        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 1.01)
        self.assertTrue(contacts[0]['visible'])

        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 1.80)
        self.assertTrue(contacts[0]['visible'])
        self.assertFalse(contacts[0]['fresh_visible'])

        player.update(z=365.0, speed=10.0)
        contacts, unused_lookup = runtime._contacts_for(
            source, [player], 2.10)
        self.assertTrue(contacts[0]['visible'])

    def test_hidden_human_and_bot_targets_keep_last_visible_pose(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor())
        visible = [True]
        runtime._visible = lambda *unused: visible[0]
        source = {
            'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        player = _admit_player({
            'id': 2, 'team': 2, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'x': 10.0, 'y': 1.0, 'z': 100.0,
            'yaw': 0.2, 'speed': 4.0, 'health': 900,
            'max_health': 1000,
        })
        unused_contacts, lookup = runtime._contacts_for(
            source, [player], 1.0)
        planner_id = self.module.HUMAN_TARGET_ID_BASE + 2
        self.assertEqual((10.0, 1.0, 100.0),
                         lookup[planner_id]['position'])

        visible[0] = False
        player.update(x=80.0, y=2.0, z=240.0, yaw=1.2,
                      speed=20.0, health=700)
        contacts, lookup = runtime._contacts_for(source, [player], 1.1)
        hidden_human = lookup[planner_id]
        self.assertTrue(contacts[0]['visible'])
        self.assertFalse(contacts[0]['direct_visible'])
        self.assertFalse(contacts[0]['fresh_visible'])
        self.assertEqual((10.0, 1.0, 100.0),
                         hidden_human['position'])
        refreshed_human = runtime._refresh_target_pose(
            planner_id, hidden_human, runtime._index_live_players([player]))
        self.assertEqual((10.0, 1.0, 100.0),
                         refreshed_human['position'])
        self.assertEqual((10.0, 1.0, 100.0, 0.2, 4.0), (
            refreshed_human['x'], refreshed_human['y'],
            refreshed_human['z'], refreshed_human['yaw'],
            refreshed_human['speed']))
        self.assertEqual(700, refreshed_human['health'])

        bot = {
            'id': 12, 'team': 2, 'alive': True,
            'x': -20.0, 'y': 0.5, 'z': 90.0,
            'yaw': -0.4, 'speed': 3.0,
            'health': 800, 'max_health': 1000,
            'profile': {'class_tag': 'mediumTank', 'armor': 80.0},
        }
        runtime.states = {12: bot}
        visible[0] = True
        runtime._contacts_for(source, [], 1.2)
        visible[0] = False
        bot.update(x=-90.0, y=3.0, z=260.0, yaw=-1.0,
                   speed=18.0, health=600)
        contacts, lookup = runtime._contacts_for(source, [], 1.3)
        hidden_bot = lookup[12]
        refreshed_bot = runtime._refresh_target_pose(12, hidden_bot, {})
        self.assertTrue(contacts[0]['visible'])
        self.assertFalse(contacts[0]['fresh_visible'])
        self.assertEqual((-20.0, 0.5, 90.0),
                         refreshed_bot['position'])
        self.assertEqual((-20.0, 0.5, 90.0, -0.4, 3.0), (
            refreshed_bot['x'], refreshed_bot['y'], refreshed_bot['z'],
            refreshed_bot['yaw'], refreshed_bot['speed']))
        self.assertEqual(600, refreshed_bot['health'])

    def test_worker_owned_spot_memory_expires_without_pose_refresh(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor())
        visible = [True]
        runtime._visible = lambda *unused: visible[0]
        source = {
            'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        player = _admit_player({
            'id': 2, 'team': 2, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'x': 10.0, 'y': 1.0, 'z': 100.0,
            'health': 900, 'max_health': 1000,
        })

        runtime._contacts_for(source, [player], 1.0)
        visible[0] = False
        player.update(x=80.0, z=240.0)
        remembered, lookup = runtime._contacts_for(source, [player], 5.0)
        self.assertTrue(remembered[0]['visible'])
        self.assertEqual((10.0, 1.0, 100.0),
                         lookup[self.module.HUMAN_TARGET_ID_BASE + 2][
                             'position'])

        expired, lookup = runtime._contacts_for(source, [player], 11.01)
        self.assertFalse(expired[0]['visible'])
        self.assertNotIn(self.module.HUMAN_TARGET_ID_BASE + 2, lookup)

    def test_designated_target_uses_completed_carrier_and_five_degree_sector(self):
        snapshot = _effective_params_snapshot()
        snapshot['crew']['members'] = [{
            'instance': 'gunner', 'roles': ['gunner'],
            'skills': [{
                'name': 'gunner_rancorous',
                'active': True, 'level': 100.0,
            }],
        }]
        source = {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'aim_yaw': 0.0, 'critical': {'crew_ko': []},
        }
        target = {'position': (0.0, 0.0, 100.0)}

        self.assertEqual(
            12.0, self.module.BotRuntime._designated_spot_duration(
                source, target, snapshot))
        source['aim_yaw'] = math.radians(5.01)
        self.assertEqual(
            10.0, self.module.BotRuntime._designated_spot_duration(
                source, target, snapshot))
        source['aim_yaw'] = 0.0
        source['critical'] = {'crew_ko': ['gunner']}
        self.assertEqual(
            10.0, self.module.BotRuntime._designated_spot_duration(
                source, target, snapshot))

    def test_last_effort_reuses_only_the_alive_direct_target_set(self):
        params = _effective_params_snapshot()
        params['crew']['members'] = [{
            'instance': 'radioman', 'roles': ['radioman'],
            'skills': [{
                'name': 'radioman_lasteffort',
                'active': True, 'level': 100.0,
            }],
        }]
        params['crew']['dynamic_spotting']['crew'] = ['radioman']
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            visibility_probe=lambda *unused: self.fail(
                'a destroyed observer must not acquire new LOS'))
        runtime.states = {11: {
            'id': 11, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
            'health': 100, 'max_health': 100,
        }}
        alive = {
            'id': 1, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0,
            'critical': {'crew_ko': []},
            'effective_params': params,
        }
        runtime._track_human_observer_lifecycle([alive], 1.0)
        runtime._human_direct_targets[1] = set((('bot', 11),))
        dead = dict(alive, alive=False,
                    critical={'crew_ko': ['radioman']})
        runtime._track_human_observer_lifecycle([dead], 2.0)

        aggregate = {}
        runtime._append_human_observations(
            [dead], 2.0, aggregate, {})
        self.assertEqual(set((1,)), aggregate[(1, 'bot', 11)][3])

        runtime._track_human_observer_lifecycle([dead], 4.01)
        aggregate = {}
        runtime._append_human_observations(
            [dead], 4.01, aggregate, {})
        self.assertEqual(set(), aggregate[(1, 'bot', 11)][3])

    def test_first_hidden_target_has_no_live_pose_in_local_lookup(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor())
        runtime._visible = lambda *unused: False
        source = {
            'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        player = _admit_player({
            'id': 2, 'team': 2, 'alive': True,
            'vehicle': 'ussr:R11_MS-1',
            'x': 37.0, 'y': 2.0, 'z': 211.0,
            'health': 1000, 'max_health': 1000,
        })

        contacts, lookup = runtime._contacts_for(source, [player], 1.0)

        self.assertNotIn(self.module.HUMAN_TARGET_ID_BASE + 2, lookup)
        self.assertFalse(contacts[0]['visible'])
        self.assertEqual((0.0, 0.0, 0.0), contacts[0]['position'])

    def test_shot_camouflage_invalidates_every_observer_cache(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.30, 0.40))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        probes = []

        def visibility(source, target, fired_recently=False):
            probes.append((source['id'], target['network_id'],
                           bool(fired_recently)))
            return True

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            visibility_probe=visibility)
        sources = [
            {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
             'view_range': 445.0},
            {'id': 12, 'x': 0.0, 'y': 0.0, 'z': 0.0,
             'view_range': 445.0},
        ]
        target = {
            'id': self.module.HUMAN_TARGET_ID_BASE + 2,
            'kind': 'human', 'network_id': 2,
            'vehicle': 'ussr:R11_MS-1',
            'position': (0.0, 0.0, 400.0),
            'speed': 0.0, 'fire_seq': 0,
        }
        target = _admit_player(target)

        self.assertEqual(
            [False, False],
            [runtime._visible(source, target, 1.0) for source in sources])
        target['fire_seq'] = 1
        self.assertEqual(
            [True, True],
            [runtime._visible(source, target, 1.01) for source in sources])
        # The stationary target is mathematically undetectable before firing,
        # so those two native rays are skipped. The fire-sequence change still
        # invalidates both observer caches and admits the same two shot probes.
        self.assertEqual([(11, 2, True), (12, 2, True)], probes)

        self.assertEqual(
            [True, True],
            [runtime._visible(source, target, 1.02) for source in sources])
        self.assertEqual(2, len(probes))

    def test_bot_spotting_applies_foliage_without_breaking_proximity(self):
        descriptor = _combat_descriptor()
        descriptor.type = types.SimpleNamespace(
            invisibility=(0.0, 0.0))
        descriptor.miscAttrs = {'invisibilityFactor': 1.0}
        descriptor.gun.invisibilityFactorAtShot = 0.10
        calls = []

        def visibility(unused_source, target, fired_recently=False):
            target_id = target['network_id']
            calls.append((target_id, bool(fired_recently)))
            foliage_bonus = (
                0.60 if target_id == 3 and not fired_recently else 0.0)
            return {
                'line_of_sight': target_id != 4,
                'foliage_bonus': foliage_bonus,
            }

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=visibility,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(self.start)
        source = runtime.states[11]
        source.update(x=0.0, y=0.0, z=0.0)
        players = [
            {'id': 2, 'team': 1, 'alive': True,
             'vehicle': 'ussr:R11_MS-1',
             'x': 0.0, 'y': 0.0, 'z': 300.0,
             'speed': 0.0, 'fire_seq': 0},
            {'id': 3, 'team': 1, 'alive': True,
             'vehicle': 'ussr:R11_MS-1',
             'x': 0.0, 'y': 0.0, 'z': 300.0,
             'speed': 0.0, 'fire_seq': 0},
            {'id': 4, 'team': 1, 'alive': True,
             'vehicle': 'ussr:R11_MS-1',
             'x': 0.0, 'y': 0.0, 'z': 40.0,
             'speed': 0.0, 'fire_seq': 0},
        ]
        players = [
            _admit_player(value, base_moving=0.0, base_still=0.0)
            for value in players]

        contacts, unused_lookup = runtime._contacts_for(
            source, players, 1.0)
        visible = dict((contact['network_id'], contact['visible'])
                       for contact in contacts)
        self.assertEqual({2: True, 3: False, 4: True}, visible)
        self.assertFalse(any(target_id == 4
                             for target_id, unused_fired in calls))

        players[1]['fire_seq'] = 1
        contacts, unused_lookup = runtime._contacts_for(
            source, players, 1.01)
        visible = dict((contact['network_id'], contact['visible'])
                       for contact in contacts)
        self.assertTrue(visible[3])
        self.assertIn((3, True), calls)

    def test_driver_decisions_are_cached_and_staggered_but_physics_ticks(self):
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        start_z = state['z']

        self.runtime.update(.04, 1.00)
        self.runtime.update(.04, 1.04)
        self.runtime.update(.04, 1.08)

        self.assertEqual(1, len(self.adapters[0].calls))
        self.assertNotEqual(start_z, state['z'])

        # The 150 ms planner cadence plus the first-expiry phase keeps the
        # previous valid drive command live without rerunning perception.
        self.runtime.update(.04, 1.20)
        self.assertEqual(1, len(self.adapters[0].calls))

        self.runtime.update(.04, 1.30)
        self.assertEqual(2, len(self.adapters[0].calls))
        self.assertGreater(
            self.adapters[0].calls[-1][0]['dt'],
            self.adapters[0].calls[0][0]['dt'])

    def test_zero_bot_authority_still_publishes_authenticated_state(self):
        outgoing = self.runtime.battle_start(dict(self.start, bots=[]))

        self.assertEqual([{'type': 'bot_manifest', 'bots': []}], outgoing)
        publications = self.runtime.update(.04, 1.0, players=[])
        states = [message for message in publications
                  if message.get('type') == 'bot_state']
        self.assertEqual(1, len(states))
        self.assertEqual([], states[0]['bots'])

    def test_runtime_keeps_planner_throttle_without_traffic_feedback(self):
        self.runtime.battle_start(self.start)
        calls = []

        def traffic(*unused, **unused_kwargs):
            calls.append(True)
            return 0.0, True

        self.runtime._traffic_throttle = traffic
        state = self.runtime.states[11]
        start_z = state['z']
        self.runtime.update(.04, 1.00)
        self.runtime.update(.04, 1.04)
        self.runtime.update(.04, 1.08)

        self.assertEqual(1, len(self.adapters[0].calls))
        self.assertEqual([], calls)
        self.assertEqual(1, state['movement_dir'])
        self.assertGreater(abs(state['z'] - start_z), 0.0)

        self.runtime.update(.04, 1.30)
        self.assertEqual(2, len(self.adapters[0].calls))
        self.assertEqual([], calls)

    def test_unavailable_motion_probe_holds_last_drive_command(self):
        command = {
            'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': False, 'target_id': None,
            'fire_range': 0.0, 'combat_mode': 'route',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 200.0),
            'recovery_mode': 'drive', 'movement_intent': True,
        }
        unavailable = (
            None,
            {'clear': False, 'collision': False, 'slope': 0.0,
             'deferred': True},
        )
        for probe_result in unavailable:
            with self.subTest(probe_result=probe_result):
                adapter = _FixedAdapter(command)
                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: adapter,
                    direction_probe=lambda *unused, value=probe_result: value,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, baked_graph=_graph())
                runtime.battle_start(self.start)
                runtime.adapter.decide = (
                    lambda unused_state, unused_clear: dict(command))
                state = runtime.states[11]
                start_z = state['z']
                positions = [start_z]

                for index in range(4):
                    runtime.update(.04, 1.00 + index * .04)
                    self.assertEqual(1, state['movement_dir'])
                    positions.append(state['z'])

                self.assertGreater(abs(state['z'] - start_z), 0.0)
                self.assertTrue(all(
                    abs(current - previous) > 0.0
                    for previous, current in zip(
                        positions[:-1], positions[1:])))
                self.assertGreater(state['speed'], 0.0)
                self.assertIn(11, runtime._decision_cache)
                self.assertNotIn(11, runtime._motion_probe_cache)

    def test_new_server_order_revision_invalidates_decision_cache(self):
        self.runtime.battle_start(self.start)
        self.runtime.update(.04, 1.00)
        self.assertEqual(1, len(self.adapters[0].calls))

        self.runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'move_position': {'x': 8, 'y': 0, 'z': 8},
                'fire_allowed': False, 'shell_index': 0,
                'fire_range': 0}],
            'bots': []})
        self.runtime.update(.04, 1.04)

        self.assertEqual(2, len(self.adapters[0].calls))

    def test_unfireable_moving_target_order_keeps_worker_caches(self):
        def order(**changes):
            result = {
                'id': 11, 'target_kind': 'human', 'target_id': 2,
                'fire_allowed': False, 'shell_index': 0,
                'fire_range': 500.0, 'combat_mode': 'advance_contact',
                'aim_position': (0.0, 1.0, 80.0),
                'face_position': (0.0, 1.0, 80.0),
                'move_position': (0.0, 1.0, 80.0),
            }
            result.update(changes)
            return result

        runtime = self.module.BotRuntime(1)
        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': 1, 'bot_orders': [order()],
        }))
        decision = object()
        motion = object()
        runtime._decision_cache[11] = decision
        runtime._motion_probe_cache[11] = motion
        token = runtime._server_order_tokens[11]
        moved = (20.0, 1.0, 70.0)

        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': 2,
            'bot_orders': [order(
                aim_position=moved, face_position=moved,
                move_position=moved)],
        }))

        self.assertIs(decision, runtime._decision_cache[11])
        self.assertIs(motion, runtime._motion_probe_cache[11])
        self.assertEqual(token, runtime._server_order_tokens[11])

    def test_real_server_order_changes_still_invalidate_worker_caches(self):
        base = {
            'id': 11, 'target_kind': 'human', 'target_id': 2,
            'fire_allowed': False, 'shell_index': 0,
            'fire_range': 500.0, 'combat_mode': 'advance_contact',
            'aim_position': (20.0, 1.0, 70.0),
            'face_position': (20.0, 1.0, 70.0),
            'move_position': (20.0, 1.0, 70.0),
        }
        for field, value in (
                ('target_id', 3),
                ('fire_allowed', True),
                ('shell_index', 1),
                ('combat_mode', 'engage')):
            with self.subTest(field=field):
                runtime = self.module.BotRuntime(1)
                self.assertTrue(runtime._apply_orders({
                    'bot_order_revision': 1, 'bot_orders': [dict(base)],
                }))
                runtime._decision_cache[11] = object()
                runtime._motion_probe_cache[11] = object()
                token = runtime._server_order_tokens[11]
                changed = dict(base)
                changed[field] = value

                self.assertTrue(runtime._apply_orders({
                    'bot_order_revision': 2, 'bot_orders': [changed],
                }))

                self.assertNotIn(11, runtime._decision_cache)
                self.assertNotIn(11, runtime._motion_probe_cache)
                self.assertEqual(
                    token + 1, runtime._server_order_tokens[11])

    def test_server_order_revision_invalidates_only_semantically_changed_bot(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter({
                'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
                'shell_index': 0, 'fire_allowed': False,
                'target_id': None, 'fire_range': 500.0,
                'combat_mode': 'route',
                'aim_position': (0.0, 0.0, 100.0),
                'face_position': (0.0, 0.0, 100.0),
                'move_position': (0.0, 0.0, 100.0),
                'recovery_mode': 'drive', 'movement_intent': True,
            }),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Changed'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Moving target'},
        ]
        runtime.battle_start(dict(self.start, bots=roster))

        def order(bot_id, mode='advance_contact', aim=(0.0, 1.0, 80.0)):
            return {
                'id': bot_id, 'target_kind': 'human', 'target_id': 2,
                'fire_allowed': True, 'shell_index': 0,
                'fire_range': 500.0, 'combat_mode': mode,
                'aim_position': aim, 'face_position': aim,
                'move_position': aim,
            }

        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': 1,
            'bot_orders': [order(11), order(12)],
        }))
        player = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 1.0, 'z': 80.0,
        }
        player = _admit_player(player)
        runtime.update(.04, 1.0, players=[player])
        self.assertEqual({11, 12}, set(runtime._decision_cache))
        self.assertEqual({11, 12}, set(runtime._motion_probe_cache))
        token_11 = runtime._server_order_tokens[11]
        token_12 = runtime._server_order_tokens[12]

        moved = (20.0, 1.0, 70.0)
        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': 2,
            'bot_orders': [order(11, mode='engage'), order(12, aim=moved)],
        }))

        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertIn(12, runtime._decision_cache)
        self.assertIn(12, runtime._motion_probe_cache)
        self.assertEqual(token_11 + 1, runtime._server_order_tokens[11])
        self.assertEqual(token_12, runtime._server_order_tokens[12])
        self.assertEqual(moved, runtime._server_orders[12]['aim_position'])

    def test_distant_firing_lane_is_ready_without_native_probe_or_budget(self):
        calls = []
        runtime = self.module.BotRuntime(
            1, firing_lane_probe=lambda *unused: calls.append(1) or True)
        source = {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {
            'id': 12, 'network_id': 12, 'kind': 'bot',
            'x': 0.0, 'y': 0.0,
            'z': self.module.SHOT_LANE_QUERY_DISTANCE + 1.0,
        }
        budget = [7]

        self.assertFalse(runtime._shot_clear(
            source, target, 1.0, force=True, probe_budget=budget))
        self.assertEqual([], calls)
        self.assertEqual([7], budget)
        self.assertEqual(
            (1.0, False),
            runtime._shot_los_cache[(11, 'bot', 12)])

        target['z'] = self.module.SHOT_LANE_QUERY_DISTANCE
        self.assertTrue(runtime._shot_clear(
            source, target, 1.1, force=True, probe_budget=budget))
        self.assertEqual([1], calls)
        self.assertEqual([6], budget)

    def test_selected_target_keeps_the_independent_lane_freshness_gate(self):
        calls = []
        runtime = self.module.BotRuntime(
            1, firing_lane_probe=lambda *unused: calls.append(1) or True)
        source = {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {
            'id': 12, 'network_id': 12, 'kind': 'bot',
            'x': 0.0, 'y': 0.0, 'z': 100.0,
        }

        self.assertTrue(runtime._shot_clear(source, target, 1.0))
        self.assertTrue(runtime._shot_clear(source, target, 1.2))
        self.assertEqual(1, len(calls))
        self.assertTrue(runtime._shot_clear(source, target, 1.200001))
        self.assertEqual(2, len(calls))

    def test_render_frame_reuses_probe_geometry_by_target_pose_phase(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'B'},
            {'id': 13, 'team': 2, 'slot': 0, 'name': 'C'},
        ]))
        player = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 20.0, 'y': 0.0, 'z': 30.0,
            'health': 800, 'max_health': 900,
        }
        player = _admit_player(player)
        probe_geometries = []
        original_probe_pose = runtime._probe_target_pose

        def record_probe_pose(planner_id, cached, live_players,
                              probe_targets, processed_bot_ids):
            result = original_probe_pose(
                planner_id, cached, live_players,
                probe_targets, processed_bot_ids)
            probe_geometries.append((
                cached.get('kind'), cached.get('network_id'), id(result)))
            return result

        runtime._probe_target_pose = record_probe_pose
        try:
            runtime.update(.04, 1.0, players=[player])
        finally:
            runtime._probe_target_pose = original_probe_pose

        human_probe_ids = [value[2] for value in probe_geometries
                           if value[:2] == ('human', 2)]
        bot_probe_ids = [value[2] for value in probe_geometries
                         if value[:2] == ('bot', 13)]
        self.assertGreaterEqual(len(human_probe_ids), 2)
        self.assertEqual(1, len(set(human_probe_ids)))
        self.assertGreaterEqual(len(bot_probe_ids), 2)
        self.assertEqual(1, len(set(bot_probe_ids)))

    def test_due_observation_builds_one_lane_key_per_pair(self):
        lane_calls = []
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda source, target: lane_calls.append(
                (source['id'], target['network_id'])) or True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'B'},
        ]))
        key_calls = []
        original_key = runtime._shot_los_key

        def counted_key(source, target):
            key_calls.append((source['id'], target['network_id']))
            return original_key(source, target)

        runtime._shot_los_key = counted_key
        outgoing = runtime.update(.04, 1.0)

        self.assertEqual([(11, 12), (12, 11)], key_calls)
        self.assertEqual(key_calls, lane_calls)
        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        self.assertEqual(2, len(observation['contacts']))

    def test_due_observation_deduplicates_pair_serialisation(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'B'},
            {'id': 13, 'team': 2, 'slot': 0, 'name': 'C'},
        ]))
        calls = []

        class CountingDict(dict):
            def get(self, name, default=None):
                if name == 'profile':
                    calls.append(name)
                return dict.get(self, name, default)

        original_refresh = runtime._refresh_target_pose

        def counted_refresh(planner_id, cached, live_players):
            return CountingDict(original_refresh(
                planner_id, cached, live_players))

        runtime._refresh_target_pose = counted_refresh
        outgoing = runtime.update(.04, 1.0)

        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        self.assertEqual(3, len(observation['contacts']))
        # Three enemy pairs collapse to one payload record per team target:
        # team 1->bot 13 and team 2->bot 11/bot 12.
        self.assertEqual(3, len(calls))

    def test_fire_range_uses_one_target_distance_per_cached_frame(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(dict(
                self._stationary_command(), target_id=12,
                fire_allowed=False, fire_range=500.0)),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'B'},
        ]))
        runtime.states[11].update(x=0.0, y=0.0, z=0.0)
        runtime.states[12].update(x=0.0, y=0.0, z=100.0)
        runtime.update(.04, 1.0)
        runtime._next_observation = 100.0
        runtime._next_shot_lane_refresh = 100.0
        runtime._next_cover_refresh = 100.0
        distance_calls = []
        original_distance = self.module._distance

        def counted_distance(first, second):
            distance_calls.append((first, second))
            return original_distance(first, second)

        self.module._distance = counted_distance
        try:
            runtime.update(.04, 1.04)
        finally:
            self.module._distance = original_distance

        self.assertEqual(1, len(distance_calls))

    def test_gun_yaw_limits_are_derived_once_per_manifest_bot(self):
        calls = []
        original_limits = self.module.ai_driver.gun_yaw_limits

        def counted_limits(descriptor):
            calls.append(descriptor)
            return original_limits(descriptor)

        self.module.ai_driver.gun_yaw_limits = counted_limits
        try:
            runtime = self.module.BotRuntime(
                1, descriptor_resolver=lambda unused: _combat_descriptor(),
                adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                    self._stationary_command()),
                direction_probe=lambda *unused: {
                    'clear': True, 'slope': 0.0},
                visibility_probe=lambda *unused: True,
                firing_lane_probe=lambda *unused: True,
                ground_probe=lambda *unused: 0.0,
                physics_ground_probe=lambda *unused: 0.0,
                spawn_resolver=_spawn_resolver, native_motion=True,
                baked_graph=_graph())
            runtime.battle_start(dict(self.start, bots=[
                {'id': 11, 'team': 1, 'slot': 0, 'name': 'A'},
                {'id': 12, 'team': 2, 'slot': 0, 'name': 'B'},
            ]))
            self.assertEqual(2, len(calls))
            for frame in range(5):
                runtime.update(.04, 1.0 + frame * .04)
            self.assertEqual(2, len(calls))
        finally:
            self.module.ai_driver.gun_yaw_limits = original_limits

    def test_collision_broad_phase_skips_distant_all_pairs(self):
        self.runtime.battle_start(self.start)
        template = dict(self.runtime.states[11])
        for index in range(1, 29):
            state = dict(template)
            state.update(
                id=100 + index, slot=index, x=float(index * 100),
                z=float(index * 100))
            self.runtime.states[state['id']] = state

        candidate_counts = []
        original = self.module.tank_collision.resolve_tank

        def resolve(unused_own, others, now=None, ram_cooldowns=None,
                    active_ram_contacts=None):
            candidate_counts.append(len(list(others)))
            return {
                'correction': (0.0, 0.0),
                'delta_velocity': (0.0, 0.0),
                'ram_events': (),
                'cooldowns': dict(ram_cooldowns or {}),
                'contacts': frozenset(),
            }

        self.module.tank_collision.resolve_tank = resolve
        try:
            self.runtime._resolve_tank_contacts([], 1.0, .04)
        finally:
            self.module.tank_collision.resolve_tank = original

        self.assertEqual(29, len(candidate_counts))
        self.assertEqual({0}, set(candidate_counts))

    def test_authority_failover_resumes_server_fire_sequence(self):
        waiting = dict(self.start, bot_authority_id=2)
        self.assertEqual([], self.runtime.battle_start(waiting))
        snapshot_bot = dict(
            self.start['bots'][0], health=900, max_health=1000,
            alive=True, x=1, y=0, z=2, yaw=0.5,
            fire_seq=7, shell_index=0, next_shell_index=0,
            ammo_remaining=[38], ammo_reload_pending=False,
            reload_time=0.1, reload_duration=0.2,
            critical=_critical_payload({
                'name': 'gunHealth', 'hp': 10.0, 'max_hp': 54.0,
                'state': 'critical',
            }, crew_ko=['gunner1']),
            combat_revision=0, combat_base_revision=0,
            combat_ack_seq=0, combat_fire_elapsed=0.0,
            combat_fire_timer=0.0, stun_end_server_time_ms=0)
        takeover = dict(
            self.start, bot_authority_id=1,
            bot_manifest=[snapshot_bot])

        outgoing = self.runtime.battle_start(takeover)

        self.assertEqual(7, self.runtime.states[11]['fire_seq'])
        self.assertEqual(0, self.runtime.states[11]['shell_index'])
        self.assertEqual('bot_manifest', outgoing[0]['type'])
        descriptor = self.runtime._descriptors[11]
        expected_factor = self.module._critical_factor(
            self.runtime.states[11], descriptor, 'dispersion')
        gun_state = self.runtime._gun_states[11]
        expected_dispersion = (
            gun_state.fully_aimed_dispersion * expected_factor *
            math.sqrt(1.0 + 1.5 ** 2))
        self.assertAlmostEqual(
            expected_dispersion,
            gun_state.dispersion)
        self.assertAlmostEqual(0.1, gun_state.elapsed)
        self.assertAlmostEqual(
            0.1, gun_state.remaining(1.0))
        self.runtime.apply_snapshot({'bots': [dict(
            snapshot_bot, fire_seq=8, shell_index=0,
            ammo_remaining=[37], ammo_reload_pending=True,
            reload_time=gun_state.reload_full - 0.2,
            reload_duration=gun_state.reload_full)]})
        self.assertEqual(8, self.runtime.states[11]['fire_seq'])
        self.assertEqual(0, self.runtime.states[11]['shell_index'])
        self.assertEqual([37], self.runtime.states[11]['ammo_remaining'])
        self.assertTrue(
            self.runtime.states[11]['ammo_reload_pending'])
        self.assertAlmostEqual(
            expected_dispersion,
            self.runtime._gun_states[11].dispersion)

    def test_mid_reload_progress_survives_server_takeover_manifest(self):
        self.runtime.battle_start(self.start)
        publication = self.runtime.update(.20, 1.0)[0]
        expected_remaining = self.runtime._gun_states[11].reload_full - 0.20
        self.assertAlmostEqual(
            expected_remaining, publication['bots'][0]['reload_time'])

        server, unused_manifest, unused_socket = \
            ServerBotStateRevisionTests._server()
        for name in ('shell_index', 'next_shell_index', 'ammo_remaining',
                     'ammo_reload_pending', 'clip', 'clip_size'):
            server.bot_states[11][name] = publication['bots'][0][name]
        self.assertTrue(server.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': server.round_id,
                'bots': publication['bots'],
                'sample_time_us': publication['sample_time_us'],
                'source_batch_horizon_us':
                    publication['source_batch_horizon_us'],
            }), server.last_bot_state_reject)
        start = server.current_battle_message()
        start['bot_authority_id'] = 1
        takeover = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph('04_himmelsdorf'))

        takeover.battle_start(start)

        gun = takeover._gun_states[11]
        self.assertAlmostEqual(0.20, gun.elapsed)
        self.assertAlmostEqual(expected_remaining, gun.remaining(1.0))

    def test_new_round_discards_previous_bot_and_terminal_state(self):
        self.runtime.battle_start(self.start)
        self.runtime._human_ram_receipt_seq[2] = 17
        self.runtime.apply_snapshot({
            'battle_result': {'winner': 1},
            'bots': [_snapshot_bot(health=0, alive=False,
                                   revision=1, base_revision=1)]})
        next_round = dict(
            self.start, round_id=6, battle_result=None,
            bots=[{'id': 12, 'team': 1, 'slot': 0, 'name': 'Next'}])

        outgoing = self.runtime.battle_start(next_round)

        self.assertFalse(self.runtime.finished)
        self.assertEqual({12}, set(self.runtime.states))
        self.assertEqual({}, self.runtime._human_ram_receipt_seq)
        self.assertEqual('bot_manifest', outgoing[0]['type'])

    def test_authority_handback_resends_manifest_in_same_round(self):
        first = self.runtime.battle_start(self.start)
        self.assertEqual('bot_manifest', first[0]['type'])
        self.runtime._human_ram_receipt_seq[2] = 17
        self.assertEqual([], self.runtime.battle_start(dict(
            self.start, bot_authority_id=2)))

        resumed = self.runtime.battle_start(self.start)

        self.assertEqual('bot_manifest', resumed[0]['type'])
        self.assertEqual({2: 17}, self.runtime._human_ram_receipt_seq)

    def test_authority_handback_rebases_canonical_pose_aim_and_motion(self):
        self.runtime.battle_start(self.start)
        state = self.runtime.states[11]
        sync = self.runtime._combat_sync[11]
        state.update({
            'x': 50.0, 'y': 1.0, 'z': 60.0, 'yaw': 2.5,
            'aim_yaw': 2.7, 'turret_yaw': 0.2, 'gun_pitch': -0.1,
            'desired_gun_pitch': -0.1, 'gun_aligned': True,
            'hull_aiming': True, 'speed': 8.0,
            'movement_dir': 1, 'rotation_dir': -1,
            'push_x': 3.0, 'push_z': -2.0,
            'vertical_speed': -4.0, 'airborne': True,
            'grounded_once': True, 'last_drive_pitch': 0.3,
            'health': 777,
        })
        self.runtime._turn_speeds[11] = 0.8
        self.assertEqual([], self.runtime.battle_start(dict(
            self.start, bot_authority_id=2)))
        reload_duration = self.runtime._gun_states[11].reload_full
        takeover = dict(
            self.start['bots'][0], x=200.0, y=4.0, z=300.0,
            yaw=1.0, aim_yaw=1.4, gun_pitch=-0.25,
            movement_dir=-1, rotation_dir=1, health=900,
            max_health=1000, alive=True,
            reload_time=reload_duration, reload_duration=reload_duration)

        resumed = self.runtime.battle_start(dict(
            self.start, bot_manifest=[takeover]))

        state = self.runtime.states[11]
        self.assertEqual((200.0, 4.0, 300.0, 1.0), (
            state['x'], state['y'], state['z'], state['yaw']))
        self.assertAlmostEqual(1.4, state['aim_yaw'])
        self.assertAlmostEqual(0.4, state['turret_yaw'])
        self.assertEqual((-0.25, -0.25), (
            state['gun_pitch'], state['desired_gun_pitch']))
        self.assertEqual((0.0, -1, 1), (
            state['speed'], state['movement_dir'], state['rotation_dir']))
        self.assertEqual((0.0, 0.0, 0.0, False, False, 0.0), (
            state['push_x'], state['push_z'], state['vertical_speed'],
            state['airborne'], state['grounded_once'],
            state['last_drive_pitch']))
        self.assertFalse(state['gun_aligned'])
        self.assertFalse(state['hull_aiming'])
        self.assertEqual(0.0, self.runtime._turn_speeds[11])
        self.assertEqual(777, state['health'])
        self.assertIs(sync, self.runtime._combat_sync[11])
        self.assertTrue(sync['authority_handoff_pending'])
        self.assertEqual((200.0, 4.0, 300.0, 1.0), (
            resumed[0]['bots'][0]['x'], resumed[0]['bots'][0]['y'],
            resumed[0]['bots'][0]['z'], resumed[0]['bots'][0]['yaw']))

    def test_server_macro_order_drives_local_adapter_with_human_id_mapping(self):
        self.runtime.battle_start(self.start)
        self.runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'target_kind': 'human', 'target_id': 2,
                'move_position': {'x': 8, 'y': 0, 'z': 8},
                'fire_allowed': False, 'shell_index': 1,
                'fire_range': 400}],
            'bots': []})

        self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5,
             'effective_params': _effective_params_snapshot()}])

        order = self.adapters[0].server_orders[-1]
        self.assertEqual(self.module.HUMAN_TARGET_ID_BASE + 2,
                         order['target_id'])
        self.assertEqual((5.0, 0.0, 5.0), order['aim_position'])
        self.assertEqual((5.0, 0.0, 5.0), order['face_position'])
        self.assertEqual('human', self.runtime.states[11]['target_kind'])
        self.assertEqual(2, self.runtime.states[11]['target_id'])

    def test_spawn_route_join_paths_are_scoped_to_each_bot(self):
        calls = []

        class Navigator(object):
            def next_target(self, bot_id, position, goal, path_key, now,
                            anchor, avoid, lookahead_distance=None):
                calls.append((bot_id, path_key, anchor))
                return goal

        runtime = self.module.BotRuntime(1)
        runtime.navigator = Navigator()
        runtime.states = {
            11: {'team': 2},
            12: {'team': 2},
        }
        strategic = {
            'combat_mode': 'route', 'route_id': 'forest', 'route_index': 1,
            'route_join': True,
        }
        runtime._navigation_target(
            11, (-12.0, 0.0, 0.0), (0.0, 0.0, 80.0),
            dict(strategic, route_anchor=(-12.0, 0.0, 0.0)),
            {'now': 1.0, 'neighbours': ()})
        runtime._navigation_target(
            12, (12.0, 0.0, 0.0), (0.0, 0.0, 80.0),
            dict(strategic, route_anchor=(12.0, 0.0, 0.0)),
            {'now': 1.0, 'neighbours': ()})

        self.assertEqual(('route_join', 11, 2, 'forest', 1), calls[0][1])
        self.assertEqual(('route_join', 12, 2, 'forest', 1), calls[1][1])
        self.assertNotEqual(calls[0][1], calls[1][1])
        self.assertNotEqual(calls[0][2], calls[1][2])

        runtime._navigation_target(
            11, (0.0, 0.0, 40.0), (0.0, 0.0, 80.0),
            dict(strategic, route_join=False,
                 route_anchor=(0.0, 0.0, 20.0)),
            {'now': 2.0, 'neighbours': ()})
        self.assertEqual(('route', 2, 'forest', 1), calls[2][1])
        self.assertIsNone(calls[2][2])

    def test_shared_route_lanes_are_unique_stable_and_keep_their_side(self):
        class Grid(object):
            cell_size = 4.0

            @staticmethod
            def _ground(unused_x, unused_z, unused_hint):
                return 0.0

            @staticmethod
            def segment_has_baked_hazard(*unused):
                return False

            @staticmethod
            def point_has_baked_hazard(*unused):
                return False

            @staticmethod
            def dry_segment_clear(*unused):
                return True

        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(
            grid=Grid(), bot_states={})
        runtime.baked_graph = {'bounds': (-100.0, -100.0, 100.0, 100.0)}
        runtime.states = dict((bot_id, {
            'id': bot_id, 'team': 1, 'slot': slot,
            'half_length': 3.5, 'half_width': 1.7,
        }) for slot, bot_id in enumerate(range(11, 16)))
        position = (0.0, 0.0, 0.0)
        selected = (0.0, 0.0, 40.0)
        goal = (0.0, 0.0, 80.0)
        strategic = {
            'combat_mode': 'route', 'route_id': 'forest', 'route_index': 1,
            'route_anchor': (0.0, 0.0, 0.0),
        }

        first = [runtime._route_lane_target(
            bot_id, position, goal, selected, strategic, 1.0)
                 for bot_id in range(11, 16)]
        repeated = [runtime._route_lane_target(
            bot_id, position, goal, selected, strategic, 1.1)
                    for bot_id in range(11, 16)]
        desired = [runtime.states[bot_id]['_route_lane_desired']
                   for bot_id in range(11, 16)]

        self.assertEqual(list(self.module.ROUTE_LANE_OFFSETS), desired)
        self.assertEqual(5, len(set(point[0] for point in first)))
        self.assertEqual(first, repeated)
        self.assertEqual([0.0, -5.0, 5.0, -10.0, 10.0],
                         [point[0] for point in first])

        advanced = dict(strategic, route_index=2)
        second_segment = [runtime._route_lane_target(
            bot_id, position, goal, selected, advanced, 2.0)
                          for bot_id in range(11, 16)]
        self.assertEqual(first, second_segment)
        self.assertEqual(desired, [
            runtime.states[bot_id]['_route_lane_desired']
            for bot_id in range(11, 16)])

        # Moving one member to another route preserves every remaining lease;
        # a newly joining member receives the now-unoccupied centre lane.
        runtime._route_lane_target(
            11, position, goal, selected,
            dict(strategic, route_id='hill'), 3.0)
        retained = [runtime.states[bot_id]['_route_lane_desired']
                    for bot_id in range(12, 16)]
        runtime.states[16] = {
            'id': 16, 'team': 1, 'slot': 5,
            'half_length': 3.5, 'half_width': 1.7,
        }
        runtime._route_lane_target(
            16, position, goal, selected, strategic, 3.1)
        self.assertEqual([5.0, -5.0, 10.0, -10.0], retained)
        self.assertEqual(0.0, runtime.states[16]['_route_lane_desired'])
        self.assertEqual(retained, [
            runtime.states[bot_id]['_route_lane_desired']
            for bot_id in range(12, 16)])

    def test_route_lane_collision_and_hull_bounds_narrow_without_oscillation(self):
        class Grid(object):
            cell_size = 4.0

            def __init__(self):
                self.block_before_x = -8.0

            @staticmethod
            def _ground(unused_x, unused_z, unused_hint):
                return 0.0

            @staticmethod
            def segment_has_baked_hazard(*unused):
                return False

            @staticmethod
            def point_has_baked_hazard(*unused):
                return False

            def dry_segment_clear(self, unused_start, end, unused_now):
                return end[0] >= self.block_before_x

        grid = Grid()
        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(grid=grid, bot_states={})
        runtime.baked_graph = {
            'bounds': (-100.0, -100.0, 100.0, 100.0)}
        group = (1, 'west')
        runtime.states = {14: {
            'id': 14, 'team': 1, 'slot': 3,
            'half_length': 3.5, 'half_width': 1.7,
            '_route_lane_group': group,
            '_route_lane_desired': 10.0,
        }}
        strategic = {
            'combat_mode': 'route', 'route_id': 'west', 'route_index': 1,
            'route_anchor': (0.0, 0.0, 0.0),
        }
        position = (0.0, 0.0, 0.0)
        selected = (0.0, 0.0, 40.0)
        goal = (0.0, 0.0, 80.0)

        narrowed = runtime._route_lane_target(
            14, position, goal, selected, strategic, 1.0)
        self.assertEqual(-5.0, narrowed[0])
        self.assertEqual(5.0, runtime.states[14]['_route_lane_offset'])

        # Removing the obstacle cannot make this route leg jump back outward.
        grid.block_before_x = -100.0
        repeated = runtime._route_lane_target(
            14, position, goal, selected, strategic, 1.1)
        self.assertEqual(narrowed, repeated)

        # A new macro leg retries the desired lane, but the complete hull still
        # cannot cross the authored -8 m boundary and settles at +5 again.
        runtime.baked_graph = {
            'bounds': (-8.0, -100.0, 100.0, 100.0)}
        advanced = dict(strategic, route_index=2)
        bounded = runtime._route_lane_target(
            14, position, goal, selected, advanced, 2.0)
        self.assertEqual(-5.0, bounded[0])
        self.assertEqual(10.0, runtime.states[14]['_route_lane_desired'])

    def test_route_lane_rejects_backward_arrival_candidate(self):
        class Grid(object):
            @staticmethod
            def _ground(unused_x, unused_z, unused_hint):
                return 0.0

            @staticmethod
            def segment_has_baked_hazard(*unused):
                return False

            @staticmethod
            def point_has_baked_hazard(*unused):
                return False

            @staticmethod
            def dry_segment_clear(*unused):
                return True

        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(
            grid=Grid(), bot_states={})
        runtime.baked_graph = {
            'bounds': (-500.0, -500.0, 500.0, 500.0)}
        group = (1, 'central_ridges')
        runtime.states = {8: {
            'id': 8, 'team': 1, 'slot': 7,
            'half_length': 3.5, 'half_width': 1.7,
            '_route_lane_group': group,
            '_route_lane_desired': 5.0,
        }}
        position = (338.0, 0.0, -218.0)
        selected = (342.0, 0.0, -218.0)

        target = runtime._route_lane_target(
            8, position, (358.0, 0.0, -6.0), selected, {
                'route_id': 'central_ridges', 'route_index': 2,
                'route_anchor': position,
            }, 1.0)

        self.assertEqual(selected, target)
        self.assertEqual(0.0, runtime.states[8]['_route_lane_offset'])

    def test_route_lane_narrows_around_bot_local_contact_penalty(self):
        class Grid(object):
            @staticmethod
            def _ground(unused_x, unused_z, unused_hint):
                return 0.0

            @staticmethod
            def segment_has_baked_hazard(*unused):
                return False

            @staticmethod
            def point_has_baked_hazard(*unused):
                return False

            @staticmethod
            def dry_segment_clear(*unused):
                return True

        checked = []

        def bot_edges_penalized(bot_id, unused_start, end, unused_now):
            checked.append((bot_id, end))
            return end[0] < -8.0

        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(
            grid=Grid(), bot_states={},
            bot_segment_penalized=bot_edges_penalized)
        runtime.baked_graph = {
            'bounds': (-100.0, -100.0, 100.0, 100.0)}
        group = (1, 'west')
        runtime.states = {14: {
            'id': 14, 'team': 1, 'slot': 3,
            'half_length': 3.5, 'half_width': 1.7,
            '_route_lane_group': group,
            '_route_lane_desired': 10.0,
        }}

        target = runtime._route_lane_target(
            14, (0.0, 0.0, 0.0), (0.0, 0.0, 80.0),
            (0.0, 0.0, 40.0), {
                'route_id': 'west', 'route_index': 1,
                'route_anchor': (0.0, 0.0, 0.0),
            }, 1.0)

        self.assertEqual(-5.0, target[0])
        self.assertEqual(5.0, runtime.states[14]['_route_lane_offset'])
        self.assertEqual([14, 14], [value[0] for value in checked])

    def test_malinovka_route_lanes_reject_shallow_and_fatal_hazards(self):
        graph = json.loads(
            (PORT_ROOT / 'navgraphs' / '02_malinovka.json').read_text())
        runtime = self.module.BotRuntime(1)
        runtime.navigator = self.module.TerrainNavigator(
            lambda *unused: None, baked_graph=graph)
        runtime.baked_graph = graph
        state = {
            'id': 24, 'team': 2, 'slot': 4,
            'half_length': 3.5, 'half_width': 1.7,
        }
        runtime.states = {24: state}

        central_group = (2, 'central_field')
        state.update(_route_lane_group=central_group,
                     _route_lane_desired=-10.0)
        central_minus_ten = (
            -226.92893218813452, 0.0, 202.92893218813452)
        central_minus_five = (
            -230.46446609406726, 0.0, 206.46446609406726)
        self.assertTrue(runtime.navigator.grid.point_has_baked_hazard(
            central_minus_ten, self.module.BAKED_SHALLOW_WATER))
        self.assertTrue(runtime.navigator.grid.point_has_baked_hazard(
            central_minus_five, self.module.BAKED_SHALLOW_WATER))
        central = runtime._route_lane_target(
            24, (-270.0, 0.0, 174.0), (-234.0, 0.0, 210.0),
            (-234.0, 0.0, 210.0), {
                'route_id': 'central_field', 'route_index': 3,
                'route_anchor': (-270.0, 0.0, 174.0),
            }, 1.0)
        self.assertEqual((-234.0, 0.0, 210.0), central)
        self.assertEqual(0.0, state['_route_lane_offset'])

        west_group = (2, 'west_lake_road')
        state.update(_route_lane_group=west_group,
                     _route_lane_desired=-10.0)
        state.pop('_route_lane_segment', None)
        west_minus_ten = (
            -340.2469504755442, 0.0, -293.8086880944303)
        west_minus_five = (
            -337.12347523777214, 0.0, -289.90434404721515)
        self.assertTrue(runtime.navigator.grid.point_has_baked_hazard(
            west_minus_ten, self.module.BAKED_FATAL_HAZARDS))
        self.assertFalse(runtime.navigator.grid.point_has_baked_hazard(
            west_minus_five, self.module.BAKED_FATAL_HAZARDS |
            self.module.BAKED_SHALLOW_WATER))
        west = runtime._route_lane_target(
            24, (-354.0, 0.0, -270.0), (-334.0, 0.0, -286.0),
            (-334.0, 0.0, -286.0), {
                'route_id': 'west_lake_road', 'route_index': 2,
                'route_anchor': (-354.0, 0.0, -270.0),
            }, 2.0)
        self.assertEqual(-5.0, state['_route_lane_offset'])
        self.assertFalse(runtime.navigator.grid.point_has_baked_hazard(
            west, self.module.BAKED_FATAL_HAZARDS |
            self.module.BAKED_SHALLOW_WATER))
        self.assertTrue(runtime.navigator.grid.dry_segment_clear(
            (-354.0, 0.0, -270.0), west, 2.0))

    def test_route_lane_keeps_a_planner_selected_shallow_ford_centered(self):
        class Grid(object):
            @staticmethod
            def _ground(unused_x, unused_z, unused_hint):
                return 0.0

            @staticmethod
            def segment_has_baked_hazard(*unused):
                return False

            @staticmethod
            def point_has_baked_hazard(*unused):
                return False

            @staticmethod
            def dry_segment_clear(*unused):
                return True

        selected = (0.0, 0.0, 40.0)
        runtime = self.module.BotRuntime(1)
        runtime.navigator = types.SimpleNamespace(
            grid=Grid(), bot_states={11: {
                'controlled_shallow_target': selected,
            }})
        runtime.baked_graph = {'bounds': (-100.0, -100.0, 100.0, 100.0)}
        runtime.states = {11: {
            'id': 11, 'team': 1, 'slot': 3,
            'half_length': 3.5, 'half_width': 1.7,
        }}

        target = runtime._route_lane_target(
            11, (0.0, 0.0, 0.0), (0.0, 0.0, 80.0), selected, {
                'route_id': 'only-ford', 'route_index': 1,
                'route_anchor': (0.0, 0.0, 0.0),
            }, 1.0)

        self.assertEqual(selected, target)
        self.assertEqual(0.0, runtime.states[11]['_route_lane_offset'])

    def test_short_navigation_goal_uses_planner_when_direct_path_is_shallow(self):
        calls = []

        class Grid(object):
            allow_direct = False

            def dry_segment_clear(self, start, goal, now):
                calls.append(('direct', start, goal, now))
                return self.allow_direct

        class Navigator(object):
            def __init__(self):
                self.grid = Grid()
                self.penalized = False
                self.penalty_calls = []

            def next_target(self, bot_id, position, goal, path_key, now,
                            anchor, avoid, lookahead_distance=None):
                calls.append(('planned', bot_id, path_key))
                return (4.0, 0.0, 8.0)

            @staticmethod
            def target_is_terminal(unused_bot_id):
                return False

            def bot_segment_penalized(
                    self, bot_id, start, end, now):
                self.penalty_calls.append((bot_id, start, end, now))
                return self.penalized

        runtime = self.module.BotRuntime(1)
        runtime.navigator = Navigator()
        runtime.states = {11: {'team': 1}}
        strategic = {
            'combat_mode': 'route', 'route_id': 'shore', 'route_index': 2,
        }

        selected = runtime._navigation_target(
            11, (0.0, 0.0, 0.0), (8.0, 0.0, 0.0), strategic,
            {'now': 3.0})

        self.assertEqual((4.0, 0.0, 8.0), selected)
        self.assertEqual('planned', calls[-1][0])

        runtime.navigator.penalty_calls[:] = []
        runtime._navigation_target(
            11, (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), strategic,
            {'now': 7.0})

        self.assertEqual([], runtime.navigator.penalty_calls)

        runtime.navigator.grid.allow_direct = True
        calls[:] = []
        selected = runtime._navigation_target(
            11, (0.0, 0.0, 0.0), (8.0, 0.0, 0.0), strategic,
            {'now': 4.0})

        self.assertEqual((8.0, 0.0, 0.0), selected)
        self.assertEqual(['direct'], [call[0] for call in calls])

        hold_state = {'now': 5.0}
        selected = runtime._navigation_target(
            11, (0.0, 0.0, 0.0), (8.0, 0.0, 0.0), {
                'combat_mode': 'hold', 'route_id': 'shore',
                'route_index': 2,
            }, hold_state)

        self.assertEqual((8.0, 0.0, 0.0), selected)
        self.assertTrue(hold_state['navigation_stop_at_target'])

        runtime.navigator.penalized = True
        calls[:] = []
        selected = runtime._navigation_target(
            11, (0.0, 0.0, 0.0), (8.0, 0.0, 0.0), strategic,
            {'now': 6.0})

        self.assertEqual((4.0, 0.0, 8.0), selected)
        self.assertEqual('planned', calls[-1][0])

    def test_base_defense_navigation_key_ignores_combat_target_changes(self):
        calls = []

        class Navigator(object):
            def next_target(self, bot_id, position, goal, path_key, now,
                            anchor, avoid, lookahead_distance=None):
                calls.append(path_key)
                return goal

        runtime = self.module.BotRuntime(1)
        runtime.navigator = Navigator()
        runtime.states = {11: {'team': 1}}
        for target_id in (21, 22, None):
            runtime._navigation_target(
                11, (0.0, 0.0, 0.0), (100.0, 0.0, 0.0), {
                    'combat_mode': 'base_defense',
                    'defense_base_id': '1:0',
                    'target_id': target_id,
                }, {'now': 1.0, 'neighbours': ()})

        self.assertEqual([
            ('local', 11, 'base_defense', '1:0'),
            ('local', 11, 'base_defense', '1:0'),
            ('local', 11, 'base_defense', '1:0'),
        ], calls)

    def test_planner_selects_near_fast_stable_base_defenders(self):
        planner = BotPlanner()
        manifest = []
        states = []
        for bot_id, x, speed in (
                (11, 90.0, 10.0), (12, 180.0, 22.0),
                (13, 60.0, 5.0), (14, 400.0, 22.0)):
            manifest.append({
                'id': bot_id, 'team': 1, 'slot': bot_id - 11,
                'health': 1000,
                'profile': {
                    'speed': speed, 'class_tag': 'mediumTank',
                    'dominant_role': 'support', 'roles': {},
                },
                'route': {'id': 'lane-%s' % bot_id, 'waypoints': [
                    {'x': x, 'y': 0.0, 'z': 0.0},
                    {'x': x, 'y': 0.0, 'z': 300.0},
                ]},
            })
            states.append({
                'id': bot_id, 'team': 1, 'alive': True,
                'world_pose': True, 'x': x, 'y': 0.0, 'z': 0.0,
                'health': 1000, 'max_health': 1000,
            })
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 20, 'time_left': 40.0,
                'invaders': 2, 'stopped': False}},
            'contributors': {'1': []},
        }

        orders = planner.build_orders(
            manifest, states, [], 1.0, defense)['orders']
        defenders = [order for order in orders
                     if order['combat_mode'] == 'base_defense']

        self.assertEqual([11, 12], sorted(
            order['id'] for order in defenders))
        self.assertTrue(all(
            order['move_position'] == {'x': 0.0, 'y': 0.0, 'z': 0.0}
            for order in defenders))
        self.assertTrue(all(order['defense_base_id'] == '1:0'
                            for order in defenders))

        # Small ETA changes and fewer invaders do not churn an active group.
        states[2]['x'] = 5.0
        defense['states']['1']['invaders'] = 1
        again = planner.build_orders(
            manifest, states, [], 2.0, defense)['orders']
        self.assertEqual([11, 12], sorted(
            order['id'] for order in again
            if order['combat_mode'] == 'base_defense'))

    def test_base_defense_stays_through_stopped_and_clears_after_grace(self):
        planner = BotPlanner()
        manifest = [{
            'id': 11, 'team': 1, 'slot': 0, 'health': 1000,
            'profile': {'speed': 16.0, 'class_tag': 'lightTank'},
            'route': {'id': 'lane', 'waypoints': [
                {'x': 0.0, 'y': 0.0, 'z': 0.0},
                {'x': 0.0, 'y': 0.0, 'z': 300.0}]},
        }]
        states = [{
            'id': 11, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 100.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }]
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 30, 'time_left': 70.0,
                'invaders': 1, 'stopped': True}},
        }
        self.assertEqual('base_defense', planner.build_orders(
            manifest, states, [], 1.0, defense)['orders'][0]['combat_mode'])

        defense['states']['1']['invaders'] = 0
        self.assertEqual('base_defense', planner.build_orders(
            manifest, states, [], 2.0, defense)['orders'][0]['combat_mode'])
        self.assertEqual('base_defense', planner.build_orders(
            manifest, states, [], 4.9, defense)['orders'][0]['combat_mode'])
        self.assertEqual('route', planner.build_orders(
            manifest, states, [], 5.0, defense)['orders'][0]['combat_mode'])

    def test_base_defender_keeps_visible_target_and_moving_order(self):
        planner = BotPlanner()
        manifest = [{
            'id': 11, 'team': 1, 'slot': 0, 'health': 1000,
            'profile': {
                'speed': 16.0, 'class_tag': 'mediumTank',
                'dominant_role': 'support', 'desired_range': 180.0,
                'fire_range': 500.0, 'roles': {}},
            'route': {'id': 'lane', 'waypoints': [
                {'x': 100.0, 'y': 0.0, 'z': 0.0},
                {'x': 100.0, 'y': 0.0, 'z': 300.0}]},
        }, {
            'id': 12, 'team': 1, 'slot': 1, 'health': 1000,
            'profile': {'speed': 8.0, 'class_tag': 'heavyTank'},
            'route': {'id': 'other', 'waypoints': [
                {'x': 400.0, 'y': 0.0, 'z': 0.0},
                {'x': 400.0, 'y': 0.0, 'z': 300.0}]},
        }]
        states = [{
            'id': 11, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 100.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }, {
            'id': 12, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 400.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1, 'target_kind': 'human',
            'target_id': 2, 'target_team': 2, 'visible': True,
            'shootable_by_bot_ids': [11],
            'x': 120.0, 'y': 0.0, 'z': 0.0,
            'health': 1000, 'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 10, 'time_left': 90.0,
                'invaders': 1, 'stopped': False}},
            'contributors': {'1': [{'kind': 'human', 'id': 2}]},
        }

        order = next(order for order in planner.build_orders(
            manifest, states, [enemy], 1.0, defense)['orders']
                     if order['id'] == 11)

        self.assertEqual('base_defense', order['combat_mode'])
        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0},
                         order['move_position'])
        self.assertIsNone(order['throttle_override'])

    def test_base_defense_leaves_one_attacker_and_replaces_crippled_responder(self):
        planner = BotPlanner()
        manifest = []
        states = []
        for bot_id, x in ((11, 30.0), (12, 60.0),
                          (13, 90.0), (14, 120.0), (15, 150.0)):
            manifest.append({
                'id': bot_id, 'team': 1, 'slot': bot_id - 11,
                'health': 1000,
                'profile': {
                    'speed': 18.0, 'class_tag': 'mediumTank',
                    'dominant_role': 'support', 'roles': {},
                },
                'route': {'id': 'lane-%s' % bot_id, 'waypoints': [
                    {'x': x, 'y': 0.0, 'z': 0.0},
                    {'x': x, 'y': 0.0, 'z': 300.0},
                ]},
            })
            states.append({
                'id': bot_id, 'team': 1, 'alive': True,
                'world_pose': True, 'x': x, 'y': 0.0, 'z': 0.0,
                'health': 1000, 'max_health': 1000, 'critical': {},
            })
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 30, 'time_left': 25.0,
                'invaders': 3, 'stopped': False}},
            'contributors': {'1': []},
        }

        first = planner.build_orders(
            manifest, states, [], 1.0, defense)
        self.assertEqual([11, 12, 13], sorted(
            order['id'] for order in first['orders']
            if order['combat_mode'] == 'base_defense'))
        self.assertGreaterEqual(sum(
            order['combat_mode'] != 'base_defense'
            for order in first['orders']), 1)

        # An authority failover clears observations, not the server-owned
        # capture incident or its stable responder leases.
        planner.clear_observations()
        unchanged = planner.build_orders(
            manifest, states, [], 2.0, defense)
        self.assertEqual(first['revision'], unchanged['revision'])
        self.assertEqual([11, 12, 13], sorted(
            order['id'] for order in unchanged['orders']
            if order['combat_mode'] == 'base_defense'))

        # If the two bots left on attack are lost, one of the three existing
        # leases is released deterministically so the last mobile trio does
        # not all abandon the rest of the map.
        states[3]['alive'] = False
        states[4]['alive'] = False
        reduced = planner.build_orders(
            manifest, states, [], 2.5, defense)
        self.assertEqual([11, 12], sorted(
            order['id'] for order in reduced['orders']
            if order['combat_mode'] == 'base_defense'))

        # The same reserve invariant applies during the three-second clear
        # grace; debouncing the capture signal must not recall the last Bot.
        defense['states']['1']['invaders'] = 0
        grace = planner.build_orders(
            manifest, states, [], 2.6, defense)
        self.assertEqual([11, 12], sorted(
            order['id'] for order in grace['orders']
            if order['combat_mode'] == 'base_defense'))

        # The local driver cannot move with either track fully destroyed.
        # That responder must release its lease and be replaced without
        # recalling every bot deliberately left ahead.
        states[3]['alive'] = True
        states[4]['alive'] = True
        defense['states']['1']['invaders'] = 3
        states[0]['critical'] = {'destroyed': ['leftTrackHealth']}
        replaced = planner.build_orders(
            manifest, states, [], 3.0, defense)
        self.assertEqual([12, 13, 14], sorted(
            order['id'] for order in replaced['orders']
            if order['combat_mode'] == 'base_defense'))

    def test_base_defenders_spread_visible_capture_contributors(self):
        planner = BotPlanner()
        manifest = []
        states = []
        for bot_id, x in ((11, 30.0), (12, 40.0), (13, 300.0)):
            manifest.append({
                'id': bot_id, 'team': 1, 'slot': bot_id - 11,
                'health': 1000,
                'profile': {
                    'speed': 18.0, 'class_tag': 'mediumTank',
                    'dominant_role': 'support', 'desired_range': 180.0,
                    'fire_range': 500.0, 'roles': {},
                },
                'route': {'id': 'lane-%s' % bot_id, 'waypoints': [
                    {'x': x, 'y': 0.0, 'z': 0.0},
                    {'x': x, 'y': 0.0, 'z': 300.0},
                ]},
            })
            states.append({
                'id': bot_id, 'team': 1, 'alive': True,
                'world_pose': True, 'x': x, 'y': 0.0, 'z': 0.0,
                'health': 1000, 'max_health': 1000,
            })
        enemies = [
            {'id': 2, 'team': 2, 'alive': True},
            {'id': 3, 'team': 2, 'alive': True},
        ]
        known = planner.known_targets(states, enemies)
        for enemy_id, z in ((2, 15.0), (3, -15.0)):
            self.assertEqual(1, planner.report_contacts([{
                'observing_team': 1, 'target_kind': 'human',
                'target_id': enemy_id, 'target_team': 2,
                'visible': True, 'shootable_by_bot_ids': [11, 12],
                'x': 0.0, 'y': 0.0, 'z': z,
                'health': 1000, 'max_health': 1000,
            }], known, 1.0))
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0}]},
            'states': {'1': {
                'points': 30, 'time_left': 35.0,
                'invaders': 2, 'stopped': False}},
            'contributors': {'1': [
                {'kind': 'human', 'id': 2},
                {'kind': 'human', 'id': 3},
            ]},
        }

        orders = planner.build_orders(
            manifest, states, enemies, 1.0, defense)['orders']
        defenders = [order for order in orders
                     if order['combat_mode'] == 'base_defense']

        self.assertEqual([11, 12], sorted(
            order['id'] for order in defenders))
        self.assertEqual({2, 3}, set(
            order['target_id'] for order in defenders))
        self.assertTrue(all(order['fire_allowed'] for order in defenders))
        self.assertTrue(all(
            order['move_position'] == {'x': 0.0, 'y': 0.0, 'z': 0.0}
            for order in defenders))

    def test_json_route_anchor_is_normalized_before_terrain_navigation(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            ground_probe=lambda unused_x, unused_z, unused_hint: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            obstacle_probe=lambda *unused: False,
            spawn_resolver=_spawn_resolver,
            baked_graph=_graph(),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0})
        runtime.battle_start(self.start)
        runtime.apply_snapshot({
            'bot_order_revision': 1,
            'bot_orders': [{
                'id': 11, 'team': 2,
                'route_id': 'server_route', 'route_index': 1,
                'route_join': True,
                # Keep y first so the unfixed tuple(dict) path reproduces the
                # exact legacy-client error: could not convert string to float: y.
                'route_anchor': {'y': 0.0, 'x': 0.0, 'z': 0.0},
                'move_position': {'x': 8.0, 'y': 0.0, 'z': 0.0},
                'aim_position': {'x': 6.0, 'y': 1.0, 'z': 0.0},
                'face_position': {'x': 7.0, 'y': 0.0, 'z': 0.0},
                'target_id': None, 'target_kind': None,
                'combat_mode': 'route', 'fire_allowed': False,
                'shell_index': 0, 'fire_range': 400.0,
            }],
            'bots': [],
        })

        outgoing = runtime.update(0.04, 1.0)

        self.assertEqual('bot_state', outgoing[0]['type'])
        path = list(runtime.navigator.paths.values())[0]
        self.assertEqual((0.0, 0.0, 0.0),
                         path[0])
        order = runtime._server_orders[11]
        self.assertEqual((6.0, 1.0, 0.0), order['aim_position'])
        self.assertEqual((7.0, 0.0, 0.0), order['face_position'])
        self.assertEqual((8.0, 0.0, 0.0), order['move_position'])
        self.assertEqual((0.0, 0.0, 0.0), order['route_anchor'])

    def test_authority_publishes_deduplicated_visibility_observations(self):
        self.runtime.battle_start(self.start)

        outgoing = self.runtime.update(.04, 1.0, players=[
            {'id': 2, 'team': 1, 'alive': True,
             'x': 5, 'y': 0, 'z': 5,
             'health': 100, 'max_health': 100,
             'effective_params': _effective_params_snapshot()}])

        observation = [value for value in outgoing
                       if value['type'] == 'bot_observation'][0]
        self.assertEqual(2, len(observation['contacts']))
        identities = set((
            contact['observing_team'], contact['target_kind'],
            contact['target_id']) for contact in observation['contacts'])
        self.assertEqual(
            set(((1, 'bot', 11), (2, 'human', 2))), identities)
        human_direct = next(
            contact for contact in observation['contacts']
            if contact['target_kind'] == 'bot')
        self.assertTrue(human_direct['visible'])
        self.assertEqual([2], human_direct['visible_by_player_ids'])
        self.assertTrue(all(
            'visible_by_player_ids' in contact
            for contact in observation['contacts']))

    def test_human_vehicle_profiles_drive_server_shell_selection_once(self):
        descriptor_calls = []

        def target_descriptor(vehicle_name):
            descriptor_calls.append(vehicle_name)
            descriptor = _combat_descriptor()
            armor = 40.0 if vehicle_name == 'test:soft' else 240.0
            class_tag = ('lightTank' if vehicle_name == 'test:soft' else
                         'heavyTank')
            descriptor.hull.primaryArmor = (armor, armor, armor)
            descriptor.type = types.SimpleNamespace(
                tags=(class_tag,), name=vehicle_name)
            return descriptor

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=target_descriptor,
            visibility_probe=lambda *unused: True)
        source = {
            'id': 11, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'view_range': 445.0,
        }
        players = [
            {'id': 2, 'team': 2, 'alive': True,
             'vehicle': 'test:soft', 'x': 10.0, 'y': 0.0, 'z': 100.0,
             'health': 1000, 'max_health': 1000},
            {'id': 3, 'team': 2, 'alive': True,
             'vehicle': 'test:hard', 'x': -10.0, 'y': 0.0, 'z': 100.0,
             'health': 1000, 'max_health': 1000},
        ]
        players = [_admit_player(value) for value in players]
        contacts, unused_lookup = runtime._contacts_for(
            source, players, 1.0)
        runtime._contacts_for(source, players, 1.1)
        by_network_id = dict((contact['network_id'], contact)
                             for contact in contacts)

        self.assertEqual((40.0, 'lightTank'), (
            by_network_id[2]['armor'], by_network_id[2]['class_tag']))
        self.assertEqual((240.0, 'heavyTank'), (
            by_network_id[3]['armor'], by_network_id[3]['class_tag']))
        self.assertEqual(['test:soft', 'test:hard'], descriptor_calls)

        aggregate = {}
        for target_id, target in by_network_id.items():
            key = (1, 'human', target_id)
            aggregate[key] = (
                True, set((11,)), target, set(), set((11,)))
            runtime._renew_team_spot(key, 1.0)
            runtime._visible_target_poses[key] = {
                'position': target['position'],
                'x': target['x'], 'y': target['y'], 'z': target['z'],
                'yaw': target.get('yaw', 0.0),
                'speed': target.get('speed', 0.0),
            }
        observations = runtime._pack_observations(aggregate, 1.0)
        planner = BotPlanner()
        known = planner.known_targets([], players)
        self.assertEqual(2, planner.report_contacts(
            observations, known, 1.0))
        weapon_descriptor = _combat_descriptor()
        weapon_descriptor.gun.shots = (
            {'shell': {'kind': 'ARMOR_PIERCING',
                       'piercingPower': 180.0, 'damage': 300.0},
             'speed': 900.0},
            {'shell': {'kind': 'ARMOR_PIERCING_CR',
                       'piercingPower': 260.0, 'damage': 300.0},
             'speed': 1100.0},
            {'shell': {'kind': 'HIGH_EXPLOSIVE',
                       'piercingPower': 60.0, 'damage': 420.0},
             'speed': 700.0},
        )
        weapon_profile = self.module.ai_planner.build_vehicle_profile(
            weapon_descriptor)
        weapon_profile = BattleState._sanitize_bot_profile(weapon_profile)
        personality = {'aggression': 0.5}
        remaining = {'ammo_remaining': [30, 20, 10]}
        self.assertEqual(2, planner._shell_index(
            weapon_profile, planner._contacts[1][('human', 2)],
            personality, remaining))
        self.assertEqual(1, planner._shell_index(
            weapon_profile, planner._contacts[1][('human', 3)],
            personality, remaining))

    def test_cached_observation_uses_current_bot_pose_and_health(self):
        adapter = _FixedAdapter(self._stationary_command())
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        runtime.battle_start(dict(self.start, bots=[
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Observer'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Target'},
        ]))
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(
            x=0.0, y=1.0, z=100.0, yaw=0.0,
            health=900, max_health=900, alive=True)

        runtime.update(.04, 1.0)
        cached_target = runtime._decision_cache[11][5][12]
        cached_snapshot = dict(cached_target)
        moved = (45.0, 2.0, 70.0)
        runtime.states[12].update(
            x=moved[0], y=moved[1], z=moved[2],
            health=321, max_health=900)
        # Force the next publish frame to collect an observation while both
        # bots still reuse their first decision/perception cache.
        runtime._next_observation = 1.04
        indexed_players = []
        full_refreshes = []
        original_index = runtime._index_live_players
        original_refresh = runtime._refresh_target_poses

        def record_index(players):
            indexed_players.append(1)
            return original_index(players)

        def record_full_refresh(*args, **kwargs):
            full_refreshes.append(1)
            return original_refresh(*args, **kwargs)

        runtime._index_live_players = record_index
        runtime._refresh_target_poses = record_full_refresh
        try:
            outgoing = runtime.update(.04, 1.04)
        finally:
            runtime._index_live_players = original_index
            runtime._refresh_target_poses = original_refresh

        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        contact = next(
            value for value in observation['contacts']
            if value['observing_team'] == 1 and
            value['target_kind'] == 'bot' and value['target_id'] == 12)
        self.assertEqual(moved, (contact['x'], contact['y'], contact['z']))
        self.assertEqual(321, contact['health'])
        self.assertEqual(900, contact['max_health'])
        self.assertEqual(2, len(adapter.calls))
        self.assertEqual([1], indexed_players)
        self.assertEqual([1, 1], full_refreshes)
        self.assertIs(cached_target, runtime._decision_cache[11][5][12])
        self.assertEqual(cached_snapshot, cached_target)

    def test_team_spot_does_not_pull_blocked_bot_off_route(self):
        lane_calls = []

        def firing_lane(source, target):
            lane_calls.append((source['id'], target['network_id']))
            return source['id'] == 12

        def visibility(source, unused_target):
            return source['id'] == 11

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.05, clip=(1,)),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            # Bot 11 spots for the team, while only bot 12 owns the clear
            # barrel lane. Team visibility and per-bot shootability are
            # deliberately different facts.
            visibility_probe=visibility,
            firing_lane_probe=firing_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Clear'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Blocked'},
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        runtime.states[11].update(x=0.0, y=0.0, z=0.0, yaw=0.0)
        runtime.states[12].update(x=10.0, y=0.0, z=0.0, yaw=0.0)
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 100.0,
            'health': 1000, 'max_health': 1000,
        }
        enemy = _admit_player(enemy)

        outgoing = runtime.update(.04, 1.0, players=[enemy])
        bot_states = next(message['bots'] for message in outgoing
                          if message['type'] == 'bot_state')
        observation = next(message for message in outgoing
                           if message['type'] == 'bot_observation')
        self.assertEqual(3, len(observation['contacts']))
        contact = next(
            value for value in observation['contacts']
            if value['target_kind'] == 'human')
        self.assertTrue(contact['visible'])
        self.assertEqual([12], contact['shootable_by_bot_ids'])
        self.assertEqual([(11, 2), (12, 2)], lane_calls)

        planner = BotPlanner()
        known = planner.known_targets(bot_states, [enemy])
        self.assertEqual(1, planner.report_contacts(
            observation['contacts'], known, 1.0))
        orders = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, bot_states, [enemy], 1.0)['orders'])
        self.assertIsNone(orders[11]['target_id'])
        self.assertEqual('route', orders[11]['combat_mode'])
        self.assertFalse(orders[11]['fire_allowed'])
        self.assertEqual(2, orders[12]['target_id'])
        self.assertEqual('human', orders[12]['target_kind'])
        self.assertTrue(orders[12]['fire_allowed'])

        payload = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)
        self.assertTrue(runtime._apply_orders({
            'bot_order_revision': payload['revision'],
            'bot_orders': payload['orders'],
        }))
        # Team spotting does not pull bot 11 off its route. Bot 12 owns the
        # current firing lane and remains the only assigned shooter.
        for index in range(4):
            runtime.update(.06, 1.21 + index * .21, players=[enemy])
        self.assertEqual(0, runtime.states[11]['fire_seq'])
        self.assertGreater(runtime.states[12]['fire_seq'], 0)

    def test_close_support_withdraws_without_losing_limited_traverse_fire(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=8.0, clip=(1,),
                turret_yaw_limits=(-0.1, 0.1)),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        profile = {
            'dominant_role': 'support', 'desired_range': 200.0,
            'fire_range': 500.0, 'roles': {'support': 1.0},
        }
        manifest = runtime.battle_start(dict(self.start, bots=[{
            'id': 11, 'team': 1, 'slot': 0, 'name': 'Limited TD',
            'profile': profile,
        }]))[0]['bots']
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0,
                     aim_yaw=0.0, speed=0.0)
        manifest[0]['route'] = {
            'id': 'support_lane', 'waypoints': [
                {'x': 0.0, 'y': 0.0, 'z': -120.0},
                {'x': 0.0, 'y': 0.0, 'z': 300.0},
            ],
        }
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 20.0,
            'health': 1000, 'max_health': 1000,
        }
        enemy = _admit_player(enemy)
        planner = BotPlanner()
        bot_states = [dict(state)]
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1, 'target_kind': 'human',
            'target_id': 2, 'target_team': 2,
            'visible': True, 'shootable_by_bot_ids': [11],
            'x': 0.0, 'y': 0.0, 'z': 20.0,
            'health': 1000, 'max_health': 1000,
        }], planner.known_targets(bot_states, [enemy]), 1.0))
        payload = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)
        order = payload['orders'][0]

        self.assertEqual('withdraw', order['combat_mode'])
        self.assertIsNone(order['throttle_override'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0},
                         order['move_position'])
        runtime.apply_snapshot({
            'bot_order_revision': payload['revision'],
            'bot_orders': payload['orders'], 'bots': [],
        })

        yaws = []
        turns = []
        for frame in range(750):
            runtime.update(0.02, 1.0 + frame * 0.02,
                           players=[enemy])
            yaws.append(runtime.states[11]['yaw'])
            turns.append(runtime.states[11]['rotation_dir'])

        self.assertEqual({0}, set(turns))
        self.assertLess(max(abs(value) for value in yaws), 0.001)
        self.assertTrue(runtime.states[11]['gun_aligned'])
        self.assertGreaterEqual(runtime.states[11]['fire_seq'], 1)

    def test_non_close_support_target_still_uses_engage_hold(self):
        planner = BotPlanner()
        profile = {
            'dominant_role': 'support', 'desired_range': 200.0,
            'fire_range': 500.0, 'roles': {'support': 1.0},
        }
        manifest = [{
            'id': 11, 'team': 1, 'slot': 0, 'name': 'Support',
            'health': 1000, 'profile': profile,
            'route': {
                'id': 'support_lane', 'waypoints': [
                    {'x': 0.0, 'y': 0.0, 'z': -120.0},
                    {'x': 0.0, 'y': 0.0, 'z': 300.0},
                ],
            },
        }]
        bot_states = [{
            'id': 11, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
        }]
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 150.0,
        }
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1, 'target_kind': 'human',
            'target_id': 2, 'target_team': 2,
            'visible': True, 'shootable_by_bot_ids': [11],
            'x': 0.0, 'y': 0.0, 'z': 150.0,
            'health': 1000, 'max_health': 1000,
        }], planner.known_targets(bot_states, [enemy]), 1.0))

        order = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual('engage', order['combat_mode'])
        self.assertEqual(0.0, order['throttle_override'])

    def test_far_detail_observation_keeps_all_bots_and_explicit_lane_list(self):
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: False,
            firing_lane_probe=lambda *unused: False,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Blocked-A'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Blocked-B'},
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        runtime.states[11].update(x=0.0, y=0.0, z=0.0)
        runtime.states[12].update(x=5.0, y=0.0, z=0.0)
        runtime.set_camera_position((1000.0, 0.0, 1000.0))
        enemy = {
            'id': 2, 'team': 2, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 30.0,
            'health': 1000, 'max_health': 1000,
        }
        enemy = _admit_player(enemy)

        outgoing = runtime.update(.04, 1.0, players=[enemy])
        bot_states = next(message['bots'] for message in outgoing
                          if message['type'] == 'bot_state')
        contact = next(message for message in outgoing
                       if message['type'] == 'bot_observation')['contacts'][0]
        self.assertTrue(contact['visible'])
        self.assertEqual([], contact['shootable_by_bot_ids'])
        self.assertEqual([11, 12], [state['id'] for state in bot_states])

        planner = BotPlanner()
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(bot_states, [enemy]), 1.0))
        orders = planner.build_orders(
            manifest, bot_states, [enemy], 1.0)['orders']
        self.assertTrue(all(order['target_id'] is None for order in orders))
        self.assertTrue(all(order['combat_mode'] == 'route'
                            for order in orders))
        self.assertTrue(all(not order['fire_allowed'] for order in orders))

    def test_full_roster_firing_lane_refresh_is_staggered_and_complete(self):
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Observer-%d' % index}
            for index in range(29)
        ]
        for fps in (24, 40, 60, 120):
            with self.subTest(fps=fps):
                frame_time = [0.0]
                sample_times = {}

                def firing_lane(source, target):
                    key = (source['id'], target['kind'],
                           target['network_id'])
                    sample_times.setdefault(key, []).append(frame_time[0])
                    clear_id = 11 if source['team'] == 1 else 25
                    return source['id'] == clear_id

                runtime = self.module.BotRuntime(
                    1,
                    descriptor_resolver=lambda unused: _combat_descriptor(),
                    adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                        self._stationary_command()),
                    direction_probe=lambda *unused: {
                        'clear': True, 'slope': 0.0},
                    visibility_probe=lambda *unused: True,
                    firing_lane_probe=firing_lane,
                    ground_probe=lambda *unused: 0.0,
                    physics_ground_probe=lambda *unused: 0.0,
                    spawn_resolver=_spawn_resolver, native_motion=True,
                    baked_graph=_graph())
                runtime.battle_start(dict(self.start, bots=roster))
                player = {
                    'id': 1, 'team': 1, 'alive': True,
                    'x': 0.0, 'y': 0.0, 'z': 100.0}
                player = _admit_player(player)
                per_frame = []
                observations = []
                lane_complete_at = [None]
                for frame in range(int(fps * 0.6) + 1):
                    now = 1.0 + frame / float(fps)
                    frame_time[0] = now
                    before = sum(len(values)
                                 for values in sample_times.values())
                    outgoing = runtime.update(
                        1.0 / fps, now, players=[player])
                    after = sum(len(values)
                                for values in sample_times.values())
                    per_frame.append(after - before)
                    if (lane_complete_at[0] is None and
                            len(runtime._shot_los_cache) == 435):
                        lane_complete_at[0] = now
                    for message in outgoing:
                        if message['type'] != 'bot_observation':
                            continue
                        observations.append((now, message))

                # The one-second tactical scan obeys one global render-frame
                # budget, while the first visibility observation publishes
                # immediately with only the lane samples already available.
                self.assertLessEqual(
                    max(per_frame),
                    self.module.MAX_SHOT_LANE_PAIRS_PER_FRAME)
                self.assertEqual(435, len(sample_times))
                self.assertIsNotNone(lane_complete_at[0])
                self.assertTrue(observations)
                self.assertLessEqual(
                    observations[0][0], 1.0 + 1.0 / fps + 1e-9)
                self.assertTrue(any(
                    contact['shootable_by_bot_ids'] != (
                        [11] if contact['target_team'] == 2 else [25])
                    for contact in observations[0][1]['contacts']))
                self.assertLessEqual(
                    max(later[0] - earlier[0]
                        for earlier, later in zip(
                            observations, observations[1:])),
                    self.module.OBSERVATION_SECONDS + 1.0 / fps + 1e-9)
                complete_observations = [
                    observation for sample_time, observation in observations
                    if sample_time + 1e-9 >= lane_complete_at[0]]
                self.assertTrue(complete_observations)
                for observation in complete_observations:
                    self.assertEqual(30, len(observation['contacts']))
                    for contact in observation['contacts']:
                        expected = (
                            [11] if contact['target_team'] == 2 else [25])
                        self.assertEqual(
                            expected, contact['shootable_by_bot_ids'])

    def test_unspotted_team_targets_do_not_spend_static_lane_rays(self):
        lane_pairs = []
        runtime = self.module.BotRuntime(
            1,
            descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: False,
            firing_lane_probe=lambda source, target: lane_pairs.append((
                source['id'], target['network_id'])) or True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'One'},
            {'id': 12, 'team': 2, 'slot': 0, 'name': 'Two'},
        ]
        runtime.battle_start(dict(self.start, bots=roster))

        observations = []
        for frame in range(30):
            observations.extend(
                message for message in runtime.update(
                    1.0 / 60.0, 1.0 + frame / 60.0)
                if message['type'] == 'bot_observation')

        self.assertTrue(observations)
        self.assertEqual([], lane_pairs)
        self.assertTrue(all(
            not contact['visible'] and
            contact['shootable_by_bot_ids'] == []
            for observation in observations
            for contact in observation['contacts']))

    def test_team_spotting_proves_every_friendly_shooter_lane(self):
        lane_pairs = []
        visibility_pairs = []

        def visibility(source, target):
            visibility_pairs.append((source['id'], target['network_id']))
            return source['id'] == 11

        runtime = self.module.BotRuntime(
            1,
            descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=visibility,
            firing_lane_probe=lambda source, target: lane_pairs.append((
                source['id'], target['network_id'])) or True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'Spotter'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Shooter'},
            {'id': 13, 'team': 2, 'slot': 0, 'name': 'Target'},
        ]
        runtime.battle_start(dict(self.start, bots=roster))

        observations = []
        for frame in range(30):
            observations.extend(
                message for message in runtime.update(
                    1.0 / 60.0, 1.0 + frame / 60.0)
                if message['type'] == 'bot_observation')

        contact = next(
            contact for observation in observations
            for contact in observation['contacts']
            if contact['observing_team'] == 1 and
            contact['target_id'] == 13)
        self.assertTrue(contact['visible'])
        self.assertEqual([11, 12], contact['shootable_by_bot_ids'])
        self.assertIn((11, 13), lane_pairs)
        self.assertIn((12, 13), lane_pairs)
        self.assertIn((11, 13), visibility_pairs)
        self.assertIn((12, 13), visibility_pairs)
        self.assertEqual([11], contact['visible_by_bot_ids'])

    def test_negative_spots_remain_per_observer(self):
        visibility_pairs = []

        def visibility(source, target):
            visibility_pairs.append((source['id'], target['network_id']))
            return False

        runtime = self.module.BotRuntime(
            1,
            descriptor_resolver=lambda unused: _combat_descriptor(),
            adapter_factory=lambda *unused, **kwargs: _FixedAdapter(
                self._stationary_command()),
            direction_probe=lambda *unused: {
                'clear': True, 'slope': 0.0},
            visibility_probe=visibility,
            firing_lane_probe=lambda *unused: True,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'name': 'First'},
            {'id': 12, 'team': 1, 'slot': 1, 'name': 'Second'},
            {'id': 13, 'team': 2, 'slot': 0, 'name': 'Hidden'},
        ]
        runtime.battle_start(dict(self.start, bots=roster))

        runtime.update(1.0 / 60.0, 1.0)

        self.assertIn((11, 13), visibility_pairs)
        self.assertIn((12, 13), visibility_pairs)

    def test_full_roster_observation_and_server_planner_stay_live_for_two_minutes(self):
        lane_probes = [0]
        clear_observer = {1: 11, 2: 25}

        def firing_lane(source, unused_target):
            lane_probes[0] += 1
            return source['id'] == clear_observer[source['team']]

        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(
                reload_time=0.5, clip=(1,)),
            adapter_factory=lambda *args, **kwargs: _Adapter(*args),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=firing_lane,
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver, native_motion=True,
            baked_graph=_graph())
        refresh_lane_probes = [0]
        refresh_shot_clear = runtime._refresh_shot_clear

        def counted_refresh(*args, **kwargs):
            before = lane_probes[0]
            result = refresh_shot_clear(*args, **kwargs)
            refresh_lane_probes[0] += lane_probes[0] - before
            return result

        runtime._refresh_shot_clear = counted_refresh
        roster = [
            {'id': 11 + index,
             'team': 1 if index < 14 else 2,
             'slot': index if index < 14 else index - 14,
             'name': 'Observer-%d' % index}
            for index in range(29)
        ]
        manifest = runtime.battle_start(
            dict(self.start, bots=roster))[0]['bots']
        identities = dict((state['id'], state) for state in manifest)
        planner = BotPlanner()
        observation_batches = 0
        observation_times = []
        per_frame_lane_probes = []

        # Drive the runtime at 50 FPS for 120 seconds. Periodic firing-lane
        # refreshes and current-fire safety checks share one frame budget.
        for frame in range(6000):
            now = 1.0 + frame * 0.02
            before_lane_probes = lane_probes[0]
            outgoing = runtime.update(.02, now)
            per_frame_lane_probes.append(
                lane_probes[0] - before_lane_probes)
            if not any(message['type'] == 'bot_observation'
                       for message in outgoing):
                continue
            wire_states = next(message['bots'] for message in outgoing
                               if message['type'] == 'bot_state')
            # The wire deliberately omits manifest-owned identity/profile
            # fields. Rebuild exactly what the server sanitizer gives its
            # planner rather than depending on the old full internal copy.
            bot_states = [BattleState._sanitize_bot_state(
                state, identities[state['id']], None)
                for state in wire_states]
            observation = next(
                message for message in outgoing
                if message['type'] == 'bot_observation')
            observation_batches += 1
            observation_times.append(now)
            contacts = observation['contacts']
            self.assertEqual(29, len(contacts))
            self.assertTrue(all(
                'shootable_by_bot_ids' in contact for contact in contacts))
            known = planner.known_targets(bot_states, [])
            self.assertEqual(29, planner.report_contacts(
                contacts, known, now))
            orders = planner.build_orders(
                manifest, bot_states, [], now)['orders']
            contact_by_target = dict(
                ((contact['target_kind'], contact['target_id']), contact)
                for contact in contacts)
            targeted = 0
            firing = 0
            for order in orders:
                if order['target_id'] is None:
                    self.assertFalse(order['fire_allowed'])
                    self.assertEqual('route', order['combat_mode'])
                    continue
                targeted += 1
                contact = contact_by_target[
                    (order['target_kind'], order['target_id'])]
                if order['fire_allowed']:
                    firing += 1
                    self.assertIn(order['id'],
                                  contact['shootable_by_bot_ids'])
            if len(runtime._shot_los_cache) == 420:
                self.assertEqual(2, targeted)
                self.assertEqual(2, firing)
            else:
                self.assertLessEqual(targeted, 2)
                self.assertLessEqual(firing, targeted)

        self.assertTrue(observation_times)
        self.assertLessEqual(
            observation_times[0],
            1.0 + self.module.PUBLICATION_SECONDS + 0.02 + 1e-6)
        # Visibility remains no more than one observation window behind even
        # while the independent tactical lane refresh is still in flight.
        self.assertGreaterEqual(
            observation_times[-1],
            now - self.module.OBSERVATION_SECONDS -
            self.module.PUBLICATION_SECONDS - 1e-6)
        self.assertLessEqual(max(
            later - earlier for earlier, later in zip(
                observation_times, observation_times[1:])),
            self.module.OBSERVATION_SECONDS + 0.02 +
            self.module.PUBLICATION_SECONDS + 1e-6)
        self.assertLessEqual(
            observation_batches,
            int(math.ceil(120.0 / self.module.OBSERVATION_SECONDS)) + 1)
        # 14x15 plus 15x14 enemy pairs refresh once per one-second tactical
        # cycle. Live-fire safety checks are measured separately.
        self.assertLessEqual(
            max(per_frame_lane_probes),
            self.module.MAX_SHOT_LANE_PAIRS_PER_FRAME)
        self.assertLessEqual(
            refresh_lane_probes[0],
            420 * (int(math.ceil(
                120.0 / self.module.SHOT_LANE_REFRESH_SECONDS)) + 1))
        self.assertLessEqual(sum(
            1 for deadline in runtime._shot_los_deadlines.values()
            if deadline == runtime._next_shot_lane_refresh), 420)
        live_fire_lane_probes = lane_probes[0] - refresh_lane_probes[0]
        # Tactical roster scans are deliberately slower than the selected
        # target's final-fire gate. Each Bot may still refresh its own
        # 0.20-second lane once per live-fire cycle.
        maximum_live_fire_cycles = int(math.ceil(
            120.0 / self.module.SHOT_LANE_SECONDS)) + 1
        self.assertLessEqual(
            live_fire_lane_probes,
            len(roster) * maximum_live_fire_cycles)

    def test_malformed_new_server_order_batch_does_not_replace_last_good(self):
        self.runtime.battle_start(self.start)
        self.assertTrue(self.runtime._apply_orders({
            'bot_order_revision': 1,
            'bot_orders': [{'id': 11, 'move_position': {'x': 1}}]}))

        self.assertFalse(self.runtime._apply_orders({
            'bot_order_revision': 2, 'bot_orders': {'id': 11}}))
        self.assertEqual(1, self.runtime._order_revision)
        self.assertEqual({11}, set(self.runtime._server_orders))

    def test_manifest_rejects_route_above_protocol_limit(self):
        self.runtime.battle_start(self.start)
        state = dict(self.runtime.states[11])
        state['route'] = {
            'id': 'too-long',
            'waypoints': tuple((float(value), 0.0, False)
                               for value in range(17)),
        }

        with self.assertRaisesRegex(ValueError, '16-waypoint'):
            self.runtime._manifest_entry(state)

    def test_route_strategy_metadata_survives_the_server_manifest_boundary(self):
        self.runtime.battle_start(self.start)
        state = dict(self.runtime.states[11])
        state['route'] = {
            'id': 'middle_road', 'capacity': 4, 'risk': 0.56,
            'role_weights': {'scout': 1.0, 'brawler': 0.02},
            'class_weights': {'lightTank': 1.0, 'heavyTank': 0.02},
            'waypoints': ((0.0, 0.0, False), (8.0, 0.0, True)),
        }

        manifest = self.runtime._manifest_entry(state)

        self.assertEqual({
            'id': 'middle_road', 'capacity': 4, 'risk': 0.56,
            'role_weights': {'scout': 1.0, 'brawler': 0.02},
            'class_weights': {'lightTank': 1.0, 'heavyTank': 0.02},
            'waypoints': [
                {'x': 0.0, 'y': 0.0, 'z': 0.0, 'hold': False},
                {'x': 8.0, 'y': 0.0, 'z': 0.0, 'hold': True},
            ],
        }, manifest['route'])

        server = BattleState(map_name='01_karelia')
        server.client_build = CLIENT_BUILD_0922
        server.phase = 'battle'
        server.players[1] = Player(
            1, _CaptureSocket(), ('127.0.0.1', 1), team=1, slot=0)
        server.bot_authority_id = 1
        server.bot_roster = [
            {'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot'}]

        self.assertTrue(server.update_bot_manifest(1, {
            'round_id': server.round_id, 'bots': [manifest],
        }))
        self.assertEqual(manifest['route'],
                         server.bot_manifest[0]['route'])

    def test_server_route_metadata_is_bounded_and_legacy_compatible(self):
        legacy = BattleState._sanitize_bot_route({
            'id': 'legacy', 'waypoints': [],
        })
        self.assertEqual({'id': 'legacy', 'waypoints': []}, legacy)

        sanitized = BattleState._sanitize_bot_route({
            'id': 'strategy', 'capacity': 99, 'risk': -2.0,
            'role_weights': {'scout': 4.0, 'brawler': -1.0},
            'class_weights': {'lightTank': float('nan')},
            'waypoints': [],
        })
        self.assertEqual(15, sanitized['capacity'])
        self.assertEqual(0.0, sanitized['risk'])
        self.assertEqual(
            {'scout': 1.0, 'brawler': 0.0},
            sanitized['role_weights'])
        self.assertEqual({'lightTank': 0.0},
                         sanitized['class_weights'])

    def test_probe_rejects_water_collision_and_steep_slope(self):
        for probe in ({'clear': True, 'water': True}, {'clear': True, 'collision': True}, {'clear': True, 'slope': .7}):
            runtime = self.module.BotRuntime(1, direction_probe=lambda *unused, value=probe: value)
            self.assertFalse(runtime._clear((0, 0, 0), 0.0))

    def test_bot_leaving_support_enters_ballistic_fall(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda x, unused_z, unused_y: (
                0.0 if x < 1.0 else -20.0))
        state = {
            'id': 11, 'x': 2.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'speed': 8.0, 'half_length': 1.5,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(state, 0.1)

        self.assertTrue(state['airborne'])
        self.assertLess(state['vertical_speed'], 0.0)
        self.assertLess(state['y'], 0.0)

    def test_grounded_bot_uses_real_gap_for_remote_edge_support(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda x, unused_z, unused_y: (
                None if abs(x) < 0.1 else -20.0))
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': math.pi * 0.5, 'speed': 15.0, 'half_length': 1.5,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(state, 0.04)

        self.assertTrue(state['airborne'])
        self.assertLess(state['vertical_speed'], 0.0)
        self.assertGreater(state['y'], -1.0)

    def test_grounded_bot_keeps_near_support_across_narrow_gap(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda x, unused_z, unused_y: (
                None if abs(x) < 0.1 else 0.0))
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': math.pi * 0.5, 'speed': 0.0, 'half_length': 1.5,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(state, 0.04)

        self.assertFalse(state['airborne'])
        self.assertEqual(0.0, state['y'])

    def test_high_speed_bot_does_not_snap_down_a_flat_ledge(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: -2.0)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 15.0, 'half_length': 1.5,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True, 'last_drive_pitch': 0.0,
        }

        runtime._update_vertical_motion(state, 0.1)

        self.assertTrue(state['airborne'])
        self.assertLess(state['vertical_speed'], 0.0)
        self.assertGreater(state['y'], -2.0)

    def test_reverse_uphill_bot_keeps_hull_axis_launch_velocity(self):
        runtime = self.module.BotRuntime(
            1, physics_ground_probe=lambda *unused: -20.0)
        state = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': -12.0, 'half_length': 1.5,
            'vertical_speed': 0.0, 'airborne': False,
            'grounded_once': True,
            'last_drive_pitch': math.radians(20.0),
        }

        runtime._update_vertical_motion(state, 0.04)

        self.assertTrue(state['airborne'])
        self.assertGreater(state['vertical_speed'], 0.0)
        self.assertGreater(state['y'], 0.0)

    def test_realised_hazard_guard_never_rewinds_an_already_fallen_bot(self):
        graph = _graph()
        graph['hazards'] = (0, 2, 0)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: _Adapter(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=4.0, y=-6.0, z=0.0, speed=3.0,
                     vertical_speed=-8.0, airborne=True)

        guarded = runtime._guard_realised_pose(
            state, (0.0, 0.0, 0.0), False, 0.0)

        self.assertFalse(guarded)
        self.assertEqual((4.0, -6.0, 0.0),
                         (state['x'], state['y'], state['z']))
        self.assertEqual(-8.0, state['vertical_speed'])

    def test_realised_hazard_guard_cancels_only_current_safe_tick(self):
        graph = _graph()
        graph['hazards'] = (0, 2, 0)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: _Adapter(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        failures = []
        runtime.adapter.driver = types.SimpleNamespace(
            remember_failure=lambda *args: failures.append(args))
        runtime._decision_cache[11] = object()
        runtime._motion_probe_cache[11] = object()
        state = runtime.states[11]
        state.update(x=4.0, y=-1.0, z=0.0, speed=3.0,
                     vertical_speed=-2.0, airborne=True)

        guarded = runtime._guard_realised_pose(
            state, (0.0, 0.0, 0.0), True, 0.25)

        self.assertTrue(guarded)
        self.assertEqual((0.0, 0.0, 0.0),
                         (state['x'], state['y'], state['z']))
        self.assertEqual(0.0, state['vertical_speed'])
        self.assertNotIn(11, runtime._decision_cache)
        self.assertNotIn(11, runtime._motion_probe_cache)
        self.assertEqual([(11, 0.25, 5.0)], failures)

    def test_realised_guard_rejects_only_outward_map_boundary_progress(self):
        graph = _graph()
        graph['bounds'] = (0.0, -20.0, 8.0, 20.0)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: _Adapter(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(half_length=3.5, half_width=1.5, yaw=0.0)

        # Nearest-cell lookup still regards this half-cell apron as baked.
        # The authored bounds constrain the complete vehicle footprint.
        self.assertTrue(self.module.prebaked_navigation.pose_is_safe(
            graph, (7.0, 0.0, 0.0), shoulder_cells=0))

        state.update(x=7.0, y=0.0, z=0.0, speed=3.0)
        self.assertTrue(runtime._guard_realised_pose(
            state, (6.5, 0.0, 0.0), True, math.pi * 0.5))
        self.assertEqual((6.5, 0.0), (state['x'], state['z']))

        state.update(x=7.5, y=0.0, z=0.0, speed=3.0)
        self.assertTrue(runtime._guard_realised_pose(
            state, (7.0, 0.0, 0.0), False, math.pi * 0.5))
        self.assertEqual((7.0, 0.0), (state['x'], state['z']))

        state.update(x=6.8, y=0.0, z=0.0, speed=3.0)
        self.assertFalse(runtime._guard_realised_pose(
            state, (7.0, 0.0, 0.0), False, -math.pi * 0.5))
        self.assertEqual((6.8, 0.0), (state['x'], state['z']))

    def test_realised_guard_rejects_diagonal_axis_tradeoff(self):
        graph = _graph()
        graph['bounds'] = (0.0, -20.0, 8.0, 20.0)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: _Adapter(),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=7.5, y=0.0, z=16.6, yaw=0.0, speed=3.0,
                     half_length=3.5, half_width=1.5)

        # This diagonal step reduces the large north-edge overflow but makes
        # the east-edge overflow worse. A scalar distance would admit it.
        guarded = runtime._guard_realised_pose(
            state, (7.0, 0.0, 18.5), False, 0.0)

        self.assertTrue(guarded)
        self.assertEqual((7.0, 18.5), (state['x'], state['z']))

    def test_final_pose_guard_runs_after_tank_contact_displacement(self):
        adapter = _FixedAdapter(self._stationary_command())
        graph = _graph()
        graph['bounds'] = (0.0, -20.0, 8.0, 20.0)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=6.5, y=0.0, z=0.0, yaw=0.0, speed=0.0,
                     half_length=3.5, half_width=1.5, grounded_once=True)

        def push_outside(unused_players, unused_now, unused_step):
            state['x'] = 7.0
            return []

        runtime._resolve_tank_contacts = push_outside

        runtime.update(.04, 1.0)

        self.assertEqual((6.5, 0.0), (state['x'], state['z']))
        self.assertEqual(0.0, state['speed'])

    def test_hull_rotation_cannot_swing_chassis_past_map_boundary(self):
        command = self._stationary_command()
        command.update(turn=1.0, target_yaw=math.pi * 0.5)
        adapter = _FixedAdapter(command)
        graph = _graph()
        graph['bounds'] = (0.0, -20.0, 8.0, 20.0)
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: _combat_descriptor(),
            baked_graph=graph,
            adapter_factory=lambda *unused, **kwargs: adapter,
            direction_probe=lambda *unused: {
                'clear': True, 'collision': False, 'slope': 0.0},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=_spawn_resolver)
        runtime.battle_start(self.start)
        state = runtime.states[11]
        state.update(x=6.5, y=0.0, z=0.0, yaw=0.0, speed=0.0,
                     half_length=3.5, half_width=1.5, grounded_once=True)

        runtime.update(.04, 1.0)

        self.assertEqual(0.0, state['yaw'])
        self.assertEqual(0, state['rotation_dir'])

    def test_driver_receives_native_collision_dimensions_and_velocity(self):
        descriptor = _combat_descriptor()
        descriptor.chassis.hitTester = _HitTester1513(
            (-2.1, -1.0, -4.2), (2.3, 1.0, 3.8))
        self.runtime.descriptor_resolver = lambda unused: descriptor
        self.start['bots'].append(
            {'id': 12, 'team': 2, 'slot': 1, 'name': 'Wingman'})
        self.runtime.battle_start(self.start)
        self.runtime.states[11]['speed'] = 6.0

        self.runtime.update(.04, 1.0)

        decision = self.adapters[0].calls[0][0]
        self.assertEqual(4.2, decision['half_length'])
        self.assertEqual(2.3, decision['half_width'])
        expected_velocity = (
            math.sin(self.runtime.states[11]['yaw']) * 6.0, 0.0,
            math.cos(self.runtime.states[11]['yaw']) * 6.0)
        self.assertEqual(expected_velocity, decision['velocity'])
        neighbour = decision['neighbours'][0]
        self.assertEqual(4.2, neighbour['half_length'])
        self.assertEqual(2.3, neighbour['half_width'])

    def test_the_load_report_names_the_busiest_planners_once(self):
        runtime = self.runtime
        runtime.battle_start(self.start)
        runtime.update(.04, 1.0)

        busiest = runtime.load_report()['busiest']
        self.assertTrue(busiest)
        self.assertEqual(11, busiest[0][0])
        self.assertGreaterEqual(busiest[0][1], 1)
        # Reading the report clears the window's counters.
        self.assertEqual((), runtime.load_report()['busiest'])


class BotOwnStationaryVisionTests(unittest.TestCase):
    """A bot's own stereoscope was evaluated once at registration with zero
    stationary seconds, so a bot never earned the bonus its target did."""

    def setUp(self):
        self.module = _load()
        self.runtime = self.module.BotRuntime.__new__(self.module.BotRuntime)
        self.runtime._vision_ranges = {7: (400.0, 500.0, 3.0)}
        self.runtime._source_still = {}

    def _state(self, speed):
        return {'id': 7, 'kind': 'bot', 'speed': speed, 'view_range': 300.0}

    def test_a_moving_bot_keeps_its_moving_range(self):
        state = self._state(5.0)
        self.runtime._note_source_stillness(state, 100.0)

        self.assertEqual(400.0, self.runtime._source_view_range(state, 100.0))

    def test_a_bot_earns_its_stereoscope_after_the_device_delay(self):
        state = self._state(0.0)
        self.runtime._note_source_stillness(state, 100.0)

        self.assertEqual(400.0, self.runtime._source_view_range(state, 100.0))
        self.assertEqual(400.0, self.runtime._source_view_range(state, 102.9))
        self.assertEqual(500.0, self.runtime._source_view_range(state, 103.0))

    def test_the_stamp_survives_ticks_without_an_observation(self):
        state = self._state(0.0)
        # The tick loop keeps stamping while nothing observes this bot.
        for tick in range(40):
            self.runtime._note_source_stillness(state, 100.0 + tick * 0.1)

        self.assertEqual(500.0, self.runtime._source_view_range(state, 104.0))

    def test_moving_again_disarms_the_device(self):
        still = self._state(0.0)
        self.runtime._note_source_stillness(still, 100.0)
        self.assertEqual(500.0, self.runtime._source_view_range(still, 104.0))

        moving = self._state(9.0)
        self.runtime._note_source_stillness(moving, 105.0)
        self.assertEqual(400.0, self.runtime._source_view_range(moving, 105.0))

        self.runtime._note_source_stillness(still, 105.1)
        self.assertEqual(400.0, self.runtime._source_view_range(still, 107.0))
        self.assertEqual(500.0, self.runtime._source_view_range(still, 108.1))

    def test_a_bot_without_the_device_keeps_one_range(self):
        self.runtime._vision_ranges[7] = (400.0, 400.0, None)
        state = self._state(0.0)
        self.runtime._note_source_stillness(state, 100.0)

        self.assertEqual(400.0, self.runtime._source_view_range(state, 110.0))

    def test_an_unregistered_source_falls_back_to_its_published_range(self):
        self.runtime._vision_ranges = {}

        self.assertEqual(
            300.0, self.runtime._source_view_range(self._state(0.0), 100.0))
