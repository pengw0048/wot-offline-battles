import json
import math
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, ClientHandler,
    MAX_LINE_BYTES, MAX_MOTION_TIME_US,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY, PREBATTLE_SECONDS,
    HUMAN_RAM_TIMELINE_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    PLAYER_ENVIRONMENT_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    MODERN_VISIBLE_MESSAGE_TYPES,
    PROJECTILE_CAPABILITY, PROJECTILE_MAX_ACTIVE,
    PROJECTILE_MAX_ID,
    RICOCHET_CONTINUATION_CAPABILITY, SERVER_CAPABILITIES,
    Player, SimulationWorker, SIMULATION_WORKER_AUTHORITY_ID,
    SIMULATION_WORKER_ADVANCEMENT_TYPES,
    SIEGE_DISABLED, SIEGE_ENABLED, SIEGE_SWITCHING_OFF,
    SIEGE_SWITCHING_ON, SIEGE_VEHICLE_PARAMS, TICK_HZ,
    _critical_damage_delta, _critical_payload, _projectile_source_shot,
)
from gui.mods.offline_lan_0922 import vehicle_physics
from effective_params_fixture import effective_params


class _Socket(object):
    def sendall(self, unused_payload):
        pass


def _player(player_id, team=1, x=0.0):
    return Player(
        player_id, _Socket(), ('127.0.0.1', player_id), team=team,
        slot=(player_id - 1) % 15, x=x, client_position=True,
        capabilities=(
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY,
            RICOCHET_CONTINUATION_CAPABILITY),
        effective_params=effective_params())


def _state(players=2):
    state = BattleState(map_name='04_himmelsdorf')
    state.client_build = CLIENT_BUILD_0922
    state.phase = 'battle'
    state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
    for player_id in range(1, players + 1):
        state.players[player_id] = _player(
            player_id, 1 if player_id % 2 else 2,
            float(player_id - 1) * 10.0)
    _attach_worker_authority(state)
    state.authority_epoch = 1
    return state


def _gun_checkpoint(reload_time=0.0, clip=1, clip_size=1,
                    dispersion=0.02, reload_duration=5.0):
    return {
        'reload_time': float(reload_time),
        'reload_duration': float(reload_duration),
        'clip': int(clip), 'clip_size': int(clip_size),
        'dispersion': float(dispersion),
    }


def _attach_worker_authority(state):
    state.simulation_worker = SimulationWorker(
        _Socket(), ('127.0.0.1', 28782), capabilities=(
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY,
            RICOCHET_CONTINUATION_CAPABILITY))
    state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
    state.simulation_worker.offer_reliable = lambda unused_message: True
    return state.simulation_worker


def _update_player_input(state, player_id, **changes):
    player = state.players[player_id]
    message = {
        'type': 'input', 'round_id': state.round_id,
        'input_seq': player.input_processed_seq + 1,
        'pose_time_us': state._logical_motion_time_us(),
        'forward': 0.0, 'turn': 0.0, 'speed': 0.0,
        'aim_yaw': player.aim_yaw, 'gun_pitch': player.gun_pitch,
        'x': player.x, 'y': player.y, 'z': player.z,
        'yaw': player.yaw, 'pitch': player.pitch, 'roll': player.roll,
        'fire_seq': player.fire_seq, 'shell_index': player.shell_index,
        'next_shell_index': player.next_shell_index,
        'shell_change_pending': player.shell_change_pending,
        'gun_checkpoint': _gun_checkpoint(),
    }
    message.update(changes)
    return state.update_input(player_id, message)


def _fire_intent(state, player_id=1, **changes):
    player = state.players[player_id]
    message = {
        'type': 'fire_intent', 'round_id': state.round_id,
        'intent_seq': player.fire_intent_seq + 1,
        'input_seq': player.input_seq, 'shell_index': player.shell_index,
        'shot_origin': [player.x, player.y + 1.0, player.z],
        'shot_direction': [0.0, 0.0, 1.0],
        'dispersion_angle': 0.01,
        'presentation_ledger': [],
        'trigger_server_time_ms': state._server_time_ms(),
    }
    message.update(changes)
    return message


def _source_shot(speed, gravity, maximum, is_he=False, radius=0.0,
                 damage=(390.0, 150.0), deadeye=False):
    return {
        'speed': speed,
        'gravity': gravity,
        'maxDistance': maximum,
        'piercingPower': [220.0, 200.0],
        'deadeye': bool(deadeye),
        'shell': {
            'kind': 'HIGH_EXPLOSIVE' if is_he else 'ARMOR_PIERCING',
            'caliber': 105.0,
            'damage': list(damage),
            'explosionRadius': radius,
        },
    }


def _launch(shooter_id=1, shot_seq=1, shooter_kind='player', **changes):
    message = {
        'type': 'projectile_launch', 'round_id': 1,
        'shooter_kind': shooter_kind, 'shooter_id': shooter_id,
        'shot_seq': shot_seq, 'shell_index': 0,
        'origin': [0.0, 1.0, 0.0],
        'velocity': [100.0, 0.0, 0.0], 'gravity': 9.81,
        'max_distance': 1000.0, 'max_time_ms': 10000,
        'is_he': False, 'splash_radius': 0.0,
        'penetration_factor': 1.0,
    }
    message.update(changes)
    if shooter_kind == 'bot':
        message.setdefault('launch_time_us', int(shot_seq) * 100000)
        message.setdefault('launch_pose', [
            float(message['origin'][0]), float(message['origin'][1]) - 1.0,
            float(message['origin'][2]), 0.0, 0.0, 0.0])
    if 'source_shot' not in message:
        message['source_shot'] = _source_shot(
            math.sqrt(sum(component * component
                          for component in message['velocity'])),
            message['gravity'], message['max_distance'],
            message['is_he'], message['splash_radius'])
    if shooter_kind == 'bot':
        message['authority_epoch'] = 1
    return message


def _launch_authority(state, message, before_launch=None):
    """Admit a player trigger, then let only worker -1 launch it."""
    if message.get('shooter_kind') == 'bot':
        return state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, message)
    if 'fire_intent_seq' not in message:
        player_id = int(message['shooter_id'])
        player = state.players[player_id]
        input_seq = player.input_processed_seq + 1
        self_time = state._logical_motion_time_us()
        if not state.update_input(player_id, {
                'type': 'input', 'round_id': state.round_id,
                'input_seq': input_seq, 'pose_time_us': self_time,
                'forward': 0.0, 'turn': 0.0, 'speed': 0.0,
                'aim_yaw': player.aim_yaw, 'gun_pitch': player.gun_pitch,
                'x': player.x, 'y': player.y, 'z': player.z,
                'yaw': player.yaw, 'pitch': player.pitch,
                'roll': player.roll, 'fire_seq': player.fire_seq,
                'shell_index': message['shell_index'],
                'next_shell_index': message['shell_index'],
                'shell_change_pending': False,
                'gun_checkpoint': _gun_checkpoint()}):
            return False
        intent_seq = player.fire_intent_seq + 1
        launch_speed = math.sqrt(sum(
            component * component for component in message['velocity']))
        if not state.submit_fire_intent(player_id, _fire_intent(
                state, player_id, intent_seq=intent_seq,
                input_seq=input_seq,
                shell_index=message['shell_index'],
                shot_origin=list(message['origin']),
                shot_direction=[
                    component / launch_speed
                    for component in message['velocity']],
                dispersion_angle=0.0)):
            return False
        relay = player.pending_fire_intents[intent_seq]
        message.update({
            'authority_epoch': state.authority_epoch,
            'shot_seq': relay['shot_seq'],
            'fire_intent_seq': intent_seq,
            'fire_input_seq': input_seq,
        })
    if callable(before_launch):
        before_launch()
    return state.launch_projectile(
        SIMULATION_WORKER_AUTHORITY_ID, message)


def _effect(target_id=2, target_kind='player', damage=100, x=10.0,
            target_pose=None, **changes):
    value = {
        'target_kind': target_kind, 'target_id': target_id,
        'damage': damage, 'shot_result': 2,
        'x': x, 'y': 1.0, 'z': 0.0,
    }
    if target_pose is not None:
        value.update({
            'target_x': target_pose[0], 'target_y': target_pose[1],
            'target_z': target_pose[2],
        })
    value.update(changes)
    return value


def _resolve(projectile_id, epoch=1, **changes):
    message = {
        'type': 'projectile_resolve', 'round_id': 1,
        'authority_epoch': epoch, 'projectile_id': projectile_id,
        'base_checked_ms': 0, 'outcome': 'impact',
        'resolved_time_ms': 0, 'checked_distance': 10.0,
        'piercing_loss': 0.0, 'penetration_factor': 1.0,
        'impact': [10.0, 1.0, 0.0],
        'direct': _effect(), 'splash': [], 'destructibles': [],
    }
    message.update(changes)
    return message


def _ricochet(projectile_id, epoch=1, **changes):
    message = {
        'type': 'projectile_ricochet', 'round_id': 1,
        'authority_epoch': epoch, 'projectile_id': projectile_id,
        'base_checked_ms': 0, 'resolved_time_ms': 100,
        'checked_distance': 10.0, 'piercing_loss': 0.0,
        'penetration_factor': 1.0,
        'impact': [10.0, 1.0, 0.0],
        'segment_origin': [10.0, 1.0, 0.0],
        'segment_velocity': [-100.0, 0.0, 0.0],
        'base_penetration_multiplier': 0.75,
        'direct': _effect(damage=0, shot_result=0),
        'destructibles': [],
    }
    message.update(changes)
    return message


def _progress(projectile_id, checked_through_ms, checked_distance,
              base_checked_ms=0, **changes):
    cursor = {
        'projectile_id': projectile_id,
        'base_checked_ms': base_checked_ms,
        'checked_through_ms': checked_through_ms,
        'checked_distance': checked_distance,
        'piercing_loss': 0.0, 'penetration_factor': 1.0,
        'destructibles': [],
    }
    cursor.update(changes)
    return {
        'type': 'projectile_progress', 'round_id': 1,
        'authority_epoch': 1, 'cursors': [cursor],
    }


def _terminal_critical():
    devices = [
        'engineHealth', 'ammoBayHealth', 'fuelTankHealth', 'radioHealth',
        'leftTrackHealth', 'rightTrackHealth', 'gunHealth',
        'turretRotatorHealth', 'surveyingDeviceHealth',
    ]
    roster = ['commander', 'driver', 'gunner1', 'loader1']
    return {
        'devices': [{
            'name': name, 'hp': 0.0, 'max_hp': 100.0,
            'state': 'destroyed',
        } for name in devices],
        'destroyed': list(devices), 'crew_ko': list(roster),
        'crew_roster': list(roster), 'fire': False,
        'ammo_rack_death': False, 'events': [],
    }


def _destructible(chunk_id=7, item_index=3, **changes):
    event = {
        'destructible_kind': 'fragile', 'chunk_id': chunk_id,
        'item_index': item_index, 'x': 5.0, 'y': 0.5, 'z': 0.0,
        'fall_yaw': 0.2, 'speed': 12.0, 'is_shot': True,
    }
    event.update(changes)
    return event


def _player_ram_contact(seq=1, **changes):
    contact = {
        'seq': seq, 'bot_id': 30, 'bot_state_revision': 999,
        'presentation_time_us': 500000,
        'native_contact_time_us': 500000,
        'contact_x': 0.0, 'contact_y': 0.0, 'contact_z': 3.0,
        'contact_normal_x': 0.0, 'contact_normal_z': -1.0,
        'contact_armor_player': 80.0, 'contact_armor_bot': 100.0,
        'contact_screened_player': False,
        'contact_screened_bot': False,
        'contact_spall_player': 1.0, 'contact_bonus_player': 0.0,
        'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
        'pitch': 0.0, 'roll': 0.0,
        'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
        'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
    }
    contact.update(changes)
    return contact


def _player_destructible_contact(seq=1, **changes):
    contact = {
        'seq': seq, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
        'speed': 5.0, 'dt': 0.03,
        'end_x': 0.0, 'end_y': 0.0, 'end_z': 0.15, 'end_yaw': 0.0,
        'token': [[7, 3, None]],
    }
    contact.update(changes)
    return contact


class ServerProjectileLedgerTests(unittest.TestCase):
    def _assert_rejected_terminal_retired(self, state, projectile_id,
                                          record=None):
        """Assert a refused live terminal retired its shot silently.

        Nothing else retires a worker-owned projectile while the worker is
        connected, so a shot the server refuses to resolve must not stay in
        the ledger: it would never reach the shooter as feedback and would
        eventually exhaust the gun's projectile capacity.
        """
        self.assertNotIn(projectile_id, state.projectiles)
        impacts = [event for event in state.pending_events
                   if event.get('kind') == 'projectile_impact' and
                   event.get('projectile_id') == projectile_id]
        self.assertEqual(1, len(impacts))
        self.assertEqual('expired', impacts[0]['outcome'])
        self.assertFalse(impacts[0]['hit_vehicle'])
        tombstone = state.projectile_tombstones.get(projectile_id)
        self.assertIsNotNone(tombstone)
        self.assertEqual('expired', tombstone['outcome'])
        if record is not None:
            self.assertEqual(record['checked_through_ms'],
                             impacts[0]['resolved_time_ms'])
            self.assertEqual(record['launch_fingerprint'],
                             tombstone['launch_fingerprint'])
        return impacts[0]

    @staticmethod
    def _critical_record():
        return {
            'shooter_kind': 'player', 'shooter_id': 1, 'team': 1,
            'projectile_id': '1:p:1:1', 'shot_seq': 1,
            'shell_index': 0,
        }

    def test_stale_destroyed_snapshot_damages_repaired_canonical_module(self):
        state = _state()
        target = state.players[2]
        target.critical = {
            'devices': [{
                'name': 'engineHealth', 'hp': 100.0,
                'max_hp': 100.0, 'state': 'normal',
            }],
            'destroyed': [], 'crew_ko': [],
            'crew_roster': ['commander'], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        target.critical_report_base_revision = 4
        target.critical_ack_seq = 2
        critical = {
            'devices': [{
                'name': 'engineHealth', 'hp': 0.0,
                'max_hp': 100.0, 'state': 'destroyed',
            }],
            'destroyed': ['engineHealth'], 'crew_ko': [],
            'crew_roster': ['commander'], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        raw = _effect(
            damage=0, critical=critical,
            critical_target_base_revision=3,
            critical_target_ack_seq=1, hull_damage=0,
            critical_delta={
                'devices': [{
                    'name': 'engineHealth', 'hp_loss': 60.0,
                }],
                'crew_ko': [], 'ignite': False,
            })
        record = self._critical_record()

        proposal = state._normalize_projectile_effect(
            raw, record, (10.0, 1.0, 0.0), False)
        self.assertFalse(proposal['critical_accepted'])
        state._apply_projectile_effect(record, proposal)

        engine = next(row for row in target.critical['devices']
                      if row['name'] == 'engineHealth')
        self.assertEqual((40.0, 'critical'),
                         (engine['hp'], engine['state']))
        self.assertTrue(state.pending_events[-1]['critical_accepted'])

    def test_stale_crew_snapshot_reknocks_only_newly_hit_member(self):
        state = _state()
        target = state.players[2]
        target.effective_params['critical']['crew_roster'] = [
            'commander', 'driver']
        target.critical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'crew_roster': ['commander', 'driver'], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        target.critical_report_base_revision = 7
        target.critical_ack_seq = 3
        critical = {
            'devices': [], 'destroyed': [], 'crew_ko': ['commander'],
            'crew_roster': ['commander', 'driver'], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        raw = _effect(
            damage=0, critical=critical,
            critical_target_base_revision=6,
            critical_target_ack_seq=2, hull_damage=0,
            critical_delta={
                'devices': [], 'crew_ko': ['commander'],
                'ignite': False,
            })
        record = self._critical_record()

        proposal = state._normalize_projectile_effect(
            raw, record, (10.0, 1.0, 0.0), False)
        self.assertFalse(proposal['critical_accepted'])
        state._apply_projectile_effect(record, proposal)

        self.assertEqual(['commander'], target.critical['crew_ko'])
        self.assertTrue(target.alive)
        self.assertTrue(state.pending_events[-1]['critical_accepted'])

    def test_modern_input_admits_bounded_world_up_atomically(self):
        state = _state()
        player = state.players[1]

        self.assertTrue(_update_player_input(
            state, 1, up_cosine=0.1256789))
        self.assertEqual(0.125679, player.up_cosine)
        before = (
            player.input_seq, dict(player.input_fingerprints),
            player.up_cosine)
        for invalid in (True, '0.5', float('nan'), float('inf'), -1.01, 1.01):
            with self.subTest(up_cosine=invalid):
                self.assertFalse(_update_player_input(
                    state, 1, up_cosine=invalid))
                self.assertEqual(before, (
                    player.input_seq, dict(player.input_fingerprints),
                    player.up_cosine))

    def test_landing_observation_applies_one_sequenced_server_delta(self):
        state = _state()
        player = state.players[1]
        results = []
        player.offer_reliable = lambda message: results.append(message) or True
        self.assertTrue(_update_player_input(state, 1, up_cosine=1.0))
        message = {
            'type': 'landing_observation', 'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'observation_seq': 1, 'input_seq': player.input_seq,
            'impact_speed': 20.0,
        }

        self.assertTrue(state.submit_landing_observation(1, message))
        expected = vehicle_physics.fall_damage(player.max_health, 20.0)
        self.assertEqual(player.max_health - expected, player.health)
        self.assertEqual(1, player.landing_observation_seq)
        self.assertEqual(expected, state.pending_events[-1]['damage'])
        self.assertEqual('environment', state.pending_events[-1]['source'])
        event_count = len(state.pending_events)
        self.assertTrue(state.submit_landing_observation(1, message))
        self.assertEqual(event_count, len(state.pending_events))
        self.assertFalse(state.submit_landing_observation(
            1, dict(message, impact_speed=21.0)))
        self.assertFalse(state.submit_landing_observation(
            1, dict(message, observation_seq=2, damage=999)))
        self.assertTrue(results[-1]['accepted'] is False)

    def test_server_overturn_owns_control_lock_and_terminal_death(self):
        state = _state()
        player = state.players[1]
        results = []
        player.offer_reliable = lambda message: (
            results.append(dict(message)) or True)
        self.assertTrue(_update_player_input(
            state, 1, up_cosine=0.0, forward=1.0, turn=1.0,
            speed=20.0))

        self.assertEqual(0, state._tick_player_overturn(0.1))
        self.assertTrue(state._player_overturn_danger(1))
        self.assertEqual((0.0, 0.0, 0.0),
                         (player.forward, player.turn, player.speed))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertEqual([{
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'intent_seq': 1, 'accepted': False,
            'reason': 'player_overturned',
        }], results)
        self.assertEqual(1, player.fire_intent_seq)
        self.assertEqual((False, 'player_overturned'),
                         player.fire_intent_results[1])
        self.assertFalse(player.pending_fire_intents)
        self.assertEqual(1, state._tick_player_overturn(29.9))
        self.assertFalse(player.alive)
        self.assertEqual(0, player.health)
        self.assertEqual(7, player.death_reason)
        self.assertEqual('environment', state.pending_events[-1]['source'])
        self.assertEqual(7, state.pending_events[-1]['attack_reason'])

    def test_modern_input_whitelist_rejects_before_state_advances(self):
        state = _state(players=1)
        player = state.players[1]


        def frame(**changes):
            message = {
                'type': 'input', 'round_id': state.round_id,
                'input_seq': player.input_processed_seq + 1,
                'pose_time_us': state._logical_motion_time_us(),
                'forward': 0.75, 'turn': -0.25, 'speed': 0.0,
                'aim_yaw': 0.2, 'gun_pitch': 0.05,
                'x': player.x, 'y': player.y, 'z': player.z,
                'yaw': player.yaw, 'pitch': player.pitch,
                'roll': player.roll, 'fire_seq': 0,
                'shell_index': 0, 'next_shell_index': 0,
                'shell_change_pending': False,
                'gun_checkpoint': _gun_checkpoint(),
            }
            message.update(changes)
            return json.loads(json.dumps(message))

        before = (
            player.input_seq, dict(player.input_fingerprints),
            player.gun_checkpoint_seq, dict(player.gun_checkpoint),
            player.forward, player.turn, player.health, player.alive,
        )
        forbidden = (
            ('health', 0), ('critical', {'events': []}),
            ('dead', True), ('damage', 1000), ('death_reason', 2),
            ('reported_health', 0),
            ('automatic_equipment_response', {'equipment_id': 21}),
            ('destructible_verdict', {'destroyed': True}),
            ('type', 'equipment_intent'),
        )
        for index, (field, value) in enumerate(forbidden):
            with self.subTest(field=field):
                rejected_seq = player.input_processed_seq + 1
                self.assertFalse(
                    state.update_input(1, frame(**{field: value})))
                # No applied state, and no gun checkpoint, from a frame that
                # never passed the whitelist.
                self.assertEqual(before, (
                    player.input_seq, dict(player.input_fingerprints),
                    player.gun_checkpoint_seq, dict(player.gun_checkpoint),
                    player.forward, player.turn, player.health, player.alive,
                ))
                # The terminal frontier still advances so the next legal frame
                # is not stuck behind this rejected sequence forever.
                self.assertEqual(index + 1, player.input_processed_seq)
                self.assertEqual(
                    'rejected',
                    player.input_decisions[rejected_seq]['outcome'])
                self.assertNotIn(rejected_seq, player.gun_checkpoints)

        recovered_seq = player.input_processed_seq + 1
        self.assertTrue(state.update_input(1, frame()))
        self.assertEqual((recovered_seq, recovered_seq, 0.75, -0.25), (
            player.input_seq, player.gun_checkpoint_seq,
            player.forward, player.turn))
        self.assertEqual(recovered_seq, player.input_processed_seq)

    def test_modern_input_folds_inactive_player_frame_as_noop(self):
        for condition in ('waiting', 'loading', 'finished',
                          'nonparticipating', 'dead'):
            with self.subTest(condition=condition):
                state = _state(players=1)
                player = state.players[1]
                if condition in ('waiting', 'loading'):
                    state.phase = condition
                elif condition == 'finished':
                    state.battle_result = {'winner': 1}
                elif condition == 'nonparticipating':
                    player.participating = False
                else:
                    player.alive = False
                    player.health = 0
                state.human_collision_profiles[player.player_id] = {
                    'shape': (1.5, 3.5, -0.8, 2.0),
                    'ram_profile': {
                        'spall_coefficient': 1.0,
                        'ramming_bonus': 0.0,
                    },
                }
                before = (
                    player.input_seq, dict(player.input_fingerprints),
                    player.gun_checkpoint_seq, dict(player.gun_checkpoint),
                    dict(player.gun_checkpoints),
                    player.forward, player.turn, player.speed,
                    player.aim_yaw, player.gun_pitch,
                    player.x, player.y, player.z, player.yaw,
                    player.pitch, player.roll, player.up_cosine,
                    player.pose_time_us, tuple(player.pose_history),
                    player.fire_seq, player.shell_index,
                    player.next_shell_index, player.shell_change_pending,
                    player.siege_state, player.siege_transition_ticks,
                    player.ram_contact_seq, dict(player.ram_contacts),
                    player.destructible_contact_seq,
                    dict(player.destructible_contacts),
                )
                self.assertTrue(_update_player_input(
                    state, 1, forward=1.0, turn=1.0, speed=25.0,
                    aim_yaw=0.5, gun_pitch=0.1,
                    x=2.0, y=3.0, z=4.0, yaw=0.2,
                    pitch=0.1, roll=-0.1, up_cosine=0.75,
                    fire_seq=7, shell_index=1, next_shell_index=1,
                    shell_change_pending=False,
                    siege_enabled=True,
                    ram_contacts=[_player_ram_contact()],
                    destructible_contacts=[
                        _player_destructible_contact()],
                ))
                self.assertEqual(before, (
                    player.input_seq, dict(player.input_fingerprints),
                    player.gun_checkpoint_seq, dict(player.gun_checkpoint),
                    dict(player.gun_checkpoints),
                    player.forward, player.turn, player.speed,
                    player.aim_yaw, player.gun_pitch,
                    player.x, player.y, player.z, player.yaw,
                    player.pitch, player.roll, player.up_cosine,
                    player.pose_time_us, tuple(player.pose_history),
                    player.fire_seq, player.shell_index,
                    player.next_shell_index, player.shell_change_pending,
                    player.siege_state, player.siege_transition_ticks,
                    player.ram_contact_seq, dict(player.ram_contacts),
                    player.destructible_contact_seq,
                    dict(player.destructible_contacts),
                ))

                malformed = (
                    ('round_id', float(state.round_id)),
                    ('input_seq', True),
                    ('forward', True), ('forward', 1e300),
                    ('speed', 1e300), ('fire_seq', True),
                    ('pose_time_us', 1.5), ('ram_contacts', [{}]),
                    ('destructible_contacts', [{}]),
                    ('ram_contacts', [dict(
                        _player_ram_contact(), unexpected=True)]),
                    ('ram_contacts', [
                        _player_ram_contact(contact_x=True)]),
                    ('destructible_contacts', [
                        _player_destructible_contact(x=True)]),
                    ('destructible_contacts', [
                        _player_destructible_contact(x=2001.0)]),
                )
                for field, value in malformed:
                    with self.subTest(condition=condition, field=field):
                        self.assertFalse(_update_player_input(
                            state, 1, **{field: value}))
                        self.assertEqual(0, player.input_seq)
                        self.assertEqual({}, player.input_fingerprints)
                        self.assertEqual((0.0, 0.0), (
                            player.forward, player.turn))

    def test_active_input_contains_bad_contact_rows_without_sequence_gap(self):
        state = _state(players=1)
        player = state.players[1]
        bad_ram = _player_ram_contact(contact_x=True)
        bad_destructible = _player_destructible_contact(x=True)

        self.assertTrue(_update_player_input(
            state, 1, ram_contacts=[bad_ram],
            destructible_contacts=[bad_destructible]))
        self.assertEqual(1, player.input_seq)
        self.assertEqual((1, 1), (
            player.ram_contact_seq, player.ram_contact_resolved_seq))
        self.assertIn(1, player.ram_contact_rejections)
        self.assertEqual((1, 1), (
            player.destructible_contact_seq,
            player.destructible_contact_resolved_seq))
        self.assertIn(1, player.destructible_contact_rejections)

        self.assertTrue(_update_player_input(
            state, 1, forward=1.0, ram_contacts=[bad_ram],
            destructible_contacts=[bad_destructible]))
        self.assertEqual(2, player.input_seq)
        self.assertEqual(1.0, player.forward)

    def test_pure_pivot_destructible_contact_binds_and_relays_swept_pose(self):
        state = _state(players=1)
        player = state.players[1]
        relayed = []
        state.simulation_worker.offer_reliable = lambda message: (
            relayed.append(dict(message)) or True)
        contact = _player_destructible_contact(
            speed=0.0, end_z=0.0, end_yaw=0.2)

        self.assertTrue(_update_player_input(
            state, 1, forward=0.0, turn=1.0, speed=0.0,
            destructible_contacts=[contact]))

        self.assertEqual([1], list(player.destructible_contacts))
        admitted = player.destructible_contacts[1]
        self.assertEqual((0.0, 0.2), (
            admitted['speed'], admitted['end_yaw']))
        self.assertEqual(0.0, admitted['forward'])
        self.assertEqual('player_destructible_contact', relayed[0]['type'])
        self.assertEqual(0.2, relayed[0]['player']
                         ['destructible_contacts'][0]['end_yaw'])

    def test_lateral_destructible_contact_does_not_require_forward_speed(self):
        state = _state(players=1)
        player = state.players[1]
        relayed = []
        state.simulation_worker.offer_reliable = lambda message: (
            relayed.append(dict(message)) or True)
        contact = _player_destructible_contact(
            speed=5.0, end_x=0.15, end_z=0.0)

        self.assertTrue(_update_player_input(
            state, 1, forward=1.0, speed=20.0,
            destructible_contacts=[contact]))

        self.assertEqual([1], list(player.destructible_contacts))
        self.assertEqual('player_destructible_contact', relayed[0]['type'])
        admitted = relayed[0]['player']['destructible_contacts'][0]
        self.assertEqual((0.15, 0.0, 5.0), (
            admitted['end_x'], admitted['end_z'], admitted['speed']))

    def test_destructible_contact_batch_admits_bounded_window(self):
        state = _state(players=1)
        player = state.players[1]
        relayed = []
        state.simulation_worker.offer_reliable = lambda message: (
            relayed.append(dict(message)) or True)
        contacts = [
            _player_destructible_contact(
                seq=seq, token=[[7, seq + 10, None]])
            for seq in range(1, 17)]

        self.assertTrue(_update_player_input(
            state, 1, destructible_contacts=contacts))

        self.assertEqual(16, player.destructible_contact_seq)
        self.assertEqual(list(range(1, 17)), list(
            player.destructible_contacts))
        self.assertEqual(16, len(relayed))
        self.assertEqual(
            list(range(1, 17)),
            [message['player']['destructible_contacts'][0]['seq']
             for message in relayed])

    def test_destructible_results_advance_independently_out_of_order(self):
        state = _state(players=1)
        player = state.players[1]
        contacts = [
            _player_destructible_contact(
                seq=seq, token=[[7, seq + 10, None]])
            for seq in range(1, 4)]
        self.assertTrue(_update_player_input(
            state, 1, destructible_contacts=contacts))
        for seq in range(1, 4):
            state.destructibles[('tree', 7, seq + 10, None)] = {
                'destructible_kind': 'tree', 'chunk_id': 7,
                'item_index': seq + 10, 'mat_kind': None,
            }

        def accept(seq):
            return state.report_player_destructible_contact_result(
                SIMULATION_WORKER_AUTHORITY_ID, {
                    'type': 'player_destructible_contact_result',
                    'round_id': state.round_id,
                    'player_id': 1, 'contact_seq': seq,
                    'accepted': True, 'token': [[7, seq + 10, None]],
                })

        self.assertTrue(accept(2))
        self.assertEqual([1, 3], list(player.destructible_contacts))
        self.assertEqual(0, player.destructible_contact_resolved_seq)
        self.assertEqual([2], list(
            player.destructible_contact_resolutions))
        public = state._public_player(player)
        self.assertEqual([2], public[
            'destructible_contact_resolved_seqs'])

        self.assertTrue(_update_player_input(
            state, 1, destructible_contacts=[
                _player_destructible_contact(
                    seq=4, token=[[7, 14, None]])]))
        self.assertEqual([1, 3, 4], list(player.destructible_contacts))

        self.assertTrue(accept(1))
        self.assertEqual([3, 4], list(player.destructible_contacts))
        self.assertEqual(2, player.destructible_contact_resolved_seq)
        self.assertFalse(player.destructible_contact_resolutions)
        self.assertNotIn(
            'destructible_contact_resolved_seqs',
            state._public_player(player))

    def test_destructible_admission_stays_inside_selective_ack_window(self):
        state = _state(players=1)
        player = state.players[1]
        player.destructible_contact_seq = 64
        player.destructible_contacts[1] = _player_destructible_contact(seq=1)
        for seq in range(2, 65):
            player.destructible_contact_resolutions[seq] = True

        self.assertTrue(_update_player_input(
            state, 1, destructible_contacts=[
                _player_destructible_contact(
                    seq=65, token=[[7, 65, None]])]))

        self.assertEqual(64, player.destructible_contact_seq)
        self.assertNotIn(65, player.destructible_contacts)
        public = state._public_player(player)
        self.assertEqual(
            list(range(2, 65)),
            public['destructible_contact_resolved_seqs'])

    def test_destructible_swept_pose_rejects_impossible_motion_locally(self):
        cases = (
            {'speed': 0.0, 'end_z': 1.0, 'end_yaw': 0.0},
            {'speed': 0.0, 'end_z': 0.0, 'end_yaw': 0.5},
            {'speed': 0.0, 'end_z': 0.0, 'end_yaw': 0.0},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                state = _state(players=1)
                player = state.players[1]
                self.assertTrue(_update_player_input(
                    state, 1, destructible_contacts=[
                        _player_destructible_contact(**changes)]))
                self.assertEqual((1, 1), (
                    player.destructible_contact_seq,
                    player.destructible_contact_resolved_seq))
                self.assertIn(1, player.destructible_contact_rejections)
                self.assertFalse(player.destructible_contacts)

    def test_destructible_contact_token_accepts_64_identities_only(self):
        token = [[7, item_index, None] for item_index in range(64)]
        accepted = BattleState._validated_player_destructible_contact(
            _player_destructible_contact(token=token))

        self.assertIsNotNone(accepted)
        self.assertEqual(64, len(accepted['token']))
        self.assertEqual(
            64, len(BattleState._destructible_contact_result_token(token)))
        self.assertIsNone(
            BattleState._validated_player_destructible_contact(
                _player_destructible_contact(
                    token=token + [[7, 64, None]])))
        self.assertIsNone(BattleState._destructible_contact_result_token(
            token + [[7, 64, None]]))

    def test_worker_result_accepts_canonical_tree_contact(self):
        state = _state(players=1)
        player = state.players[1]
        player.destructible_contact_seq = 1
        player.destructible_contacts[1] = _player_destructible_contact()
        state.destructibles[('tree', 7, 3, None)] = {
            'destructible_kind': 'tree', 'chunk_id': 7,
            'item_index': 3, 'mat_kind': None,
        }

        self.assertTrue(state.report_player_destructible_contact_result(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'player_destructible_contact_result',
                'round_id': state.round_id,
                'player_id': 1, 'contact_seq': 1,
                'accepted': True, 'token': [[7, 3, None]],
            }))

        self.assertEqual(1, player.destructible_contact_resolved_seq)
        self.assertFalse(player.destructible_contacts)

    def test_player_environment_skips_overtaken_rows_and_keeps_live_rows(self):
        state = _state(players=2)
        dead = state.players[1]
        live = state.players[2]
        dead.alive = False
        dead.health = 0

        self.assertTrue(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'player_environment',
                'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'sample_seq': 1,
                'observations': [
                    {'player_id': dead.player_id, 'input_seq': 0,
                     'level': 2},
                    {'player_id': live.player_id, 'input_seq': 0,
                     'level': 1},
                ],
            }))

        self.assertNotIn(dead.player_id, state.player_environment)
        self.assertEqual(
            1, state.player_environment[live.player_id]['level'])
        self.assertEqual(1, state.player_environment_seq)

        # Exact retries and a queued current-round sample overtaken by the
        # terminal result are both successful no-ops.
        snapshot = dict(state.player_environment)
        self.assertTrue(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'player_environment',
                'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'sample_seq': 1,
                'observations': [],
            }))
        state.battle_result = {'winner': 2}
        self.assertTrue(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'player_environment',
                'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'sample_seq': 2,
                'observations': [],
            }))
        self.assertEqual(snapshot, state.player_environment)

    def test_player_environment_skips_departed_row_after_full_validation(self):
        state = _state(players=2)
        departed_id = 1
        live = state.players[2]
        state.remove_player(departed_id)
        message = {
            'type': 'player_environment',
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'sample_seq': 1,
            'observations': [
                {'player_id': departed_id, 'input_seq': 0, 'level': 2},
                {'player_id': live.player_id, 'input_seq': 0, 'level': 1},
            ],
        }

        malformed = dict(message, observations=[
            {'player_id': departed_id, 'input_seq': 0, 'level': 2,
             'drowning_critical': True},
            message['observations'][1],
        ])
        self.assertFalse(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID, malformed))
        duplicate = dict(message, observations=[
            message['observations'][0], message['observations'][0],
            message['observations'][1],
        ])
        self.assertFalse(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID, duplicate))

        self.assertTrue(state.update_player_environment(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertNotIn(departed_id, state.player_environment)
        self.assertEqual(
            1, state.player_environment[live.player_id]['level'])
        self.assertEqual(1, state.player_environment_seq)

    def test_leave_filters_participant_and_dead_leave_has_no_death_event(self):
        state = _state(players=2)
        departed = state.players[2]
        departed.alive = False
        departed.health = 0

        self.assertTrue(state.leave_battle(
            departed.player_id, {'round_id': state.round_id}))

        self.assertFalse(departed.participating)
        self.assertFalse(any(
            event.get('source') == 'player_left'
            for event in state.pending_events))
        self.assertEqual([1], [
            player['id']
            for player in state.current_battle_message()['players']])
        self.assertFalse(BattleState._public_player(departed)[
            'participating'])

    def test_fatal_bot_projectile_commits_worker_terminal_critical_once(self):
        state = _state()
        bot = {
            'id': 16, 'team': 2, 'vehicle': 'ussr:R11_MS-1',
            'health': 100, 'max_health': 1000, 'alive': True,
            'display_health': 100, 'x': 10.0, 'y': 0.0, 'z': 0.0,
            'critical': {}, 'combat_revision': 0,
            'combat_base_revision': 0, 'combat_ack_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        }
        state.bot_states[16] = bot
        state.bot_terminal_criticals[16] = _terminal_critical()
        admitted = {
            'devices': [{
                'name': 'leftTrackHealth', 'hp': 0.0,
                'max_hp': 100.0, 'state': 'destroyed',
            }],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False,
            'events': [{
                'kind': 'device', 'name': 'leftTrackHealth',
                'old_state': 'normal', 'state': 'destroyed',
                'cause': 'shot',
            }],
        }
        proposal = {
            'target_kind': 'bot', 'target_id': 16, 'target': bot,
            'target_team': 2, 'target_alive': True,
            'retired_target': False, 'damage': 100,
            'potential_damage': 100, 'shot_result': 2,
            'pose': (10.0, 1.0, 0.0), 'critical': admitted,
            'critical_delta': None, 'critical_accepted': True,
            'hull_damage': 100, 'splash': False,
            'stun_end_server_time_ms': 0,
        }
        record = {
            'shooter_kind': 'player', 'shooter_id': 1, 'team': 1,
            'projectile_id': '1:p:1:1', 'shot_seq': 1,
            'shell_index': 0,
        }

        state._apply_projectile_effect(record, proposal)

        self.assertFalse(bot['alive'])
        self.assertEqual(0, bot['health'])
        self.assertEqual(
            set(_terminal_critical()['destroyed']),
            set(bot['critical']['destroyed']))
        self.assertEqual(1, bot['combat_revision'])
        hit = [event for event in state.pending_events
               if event.get('target_bot') == 16][-1]
        self.assertEqual(bot['combat_revision'], hit['combat_revision'])
        self.assertEqual(
            admitted['events'], hit['critical']['events'])

    def test_fatal_bot_ammo_rack_preserves_cause_and_drains_hull(self):
        state = _state()
        bot = {
            'id': 16, 'team': 2, 'vehicle': 'ussr:R11_MS-1',
            'health': 900, 'max_health': 900, 'alive': True,
            'display_health': 900, 'x': 10.0, 'y': 0.0, 'z': 0.0,
            'critical': {}, 'combat_revision': 0,
            'combat_base_revision': 0, 'combat_ack_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        }
        state.bot_states[16] = bot
        state.bot_terminal_criticals[16] = _terminal_critical()
        ammo_rack_event = {
            'kind': 'ammo_rack', 'state': 'destroyed', 'cause': 'shot'}
        admitted = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': True,
            'events': [ammo_rack_event],
        }
        proposal = {
            'target_kind': 'bot', 'target_id': 16, 'target': bot,
            'target_team': 2, 'target_alive': True,
            'retired_target': False, 'damage': 100,
            'potential_damage': 100, 'shot_result': 2,
            'pose': (10.0, 1.0, 0.0), 'critical': admitted,
            'critical_delta': None, 'critical_accepted': True,
            'hull_damage': 100, 'splash': False,
            'stun_end_server_time_ms': 0,
        }
        record = {
            'shooter_kind': 'player', 'shooter_id': 1, 'team': 1,
            'projectile_id': '1:p:1:1', 'shot_seq': 1,
            'shell_index': 0,
        }

        state._apply_projectile_effect(record, proposal)

        self.assertFalse(bot['alive'])
        self.assertEqual(0, bot['health'])
        self.assertTrue(bot['critical']['ammo_rack_death'])
        hit = [event for event in state.pending_events
               if event.get('target_bot') == 16][-1]
        self.assertEqual(900, hit['damage'])
        self.assertTrue(hit['critical']['ammo_rack_death'])
        self.assertEqual([ammo_rack_event], hit['critical']['events'])

    def test_bot_ram_commits_terminal_critical_for_both_wrecks(self):
        state = _state()
        state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        for bot_id in (16, 17):
            state.bot_states[bot_id] = {
                'id': bot_id, 'team': 1 if bot_id == 16 else 2,
                'vehicle': 'ussr:R11_MS-1', 'health': 50,
                'max_health': 1000, 'alive': True,
                'display_health': 50, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                'critical': {}, 'combat_revision': 0,
                'combat_base_revision': 0, 'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
            }
            state.bot_terminal_criticals[bot_id] = _terminal_critical()

        self.assertTrue(state.report_bot_ram(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': state.round_id, 'bot_id': 16,
                'target_kind': 'bot', 'target_id': 17, 'ram_seq': 1,
                'damage_to_bot': 80, 'damage_to_target': 80,
            }))

        for bot_id in (16, 17):
            bot = state.bot_states[bot_id]
            self.assertFalse(bot['alive'])
            self.assertEqual(0, bot['health'])
            self.assertEqual(1, bot['combat_revision'])
            self.assertEqual(
                set(_terminal_critical()['destroyed']),
                set(bot['critical']['destroyed']))
        events = [event for event in state.pending_events
                  if event.get('source') == 'ram']
        self.assertEqual(2, len(events))
        self.assertTrue(all('critical' in event for event in events))

    def test_friendly_bot_ram_report_is_a_terminal_noop(self):
        state = _state()
        state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        for bot_id in (16, 17):
            state.bot_states[bot_id] = {
                'id': bot_id, 'team': 1,
                'vehicle': 'ussr:R11_MS-1', 'health': 500,
                'max_health': 500, 'alive': True,
                'display_health': 500, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                'critical': {}, 'combat_revision': 0,
                'combat_base_revision': 0, 'combat_ack_seq': 0,
                'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
            }

        message = {
            'round_id': state.round_id, 'bot_id': 16,
            'target_kind': 'bot', 'target_id': 17, 'ram_seq': 1,
            'damage_to_bot': 200, 'damage_to_target': 300,
        }
        self.assertTrue(state.report_bot_ram(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertTrue(state.report_bot_ram(
            SIMULATION_WORKER_AUTHORITY_ID, message))

        self.assertEqual((500, 500), (
            state.bot_states[16]['health'], state.bot_states[17]['health']))
        self.assertEqual([], [
            event for event in state.pending_events
            if event.get('source') == 'ram'])

    def test_1513_siege_transition_is_server_owned_and_caps_speed(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S21_UDES_03'
        _update_player_input(
            state, 1, siege_enabled=True, forward=1.0, turn=1.0,
            speed=99.0, x=10.0, y=4.0, z=11.0, yaw=0.5)

        self.assertEqual(SIEGE_SWITCHING_ON, player.siege_state)
        self.assertEqual(60, player.siege_transition_ticks)
        self.assertEqual((0.0, 0.0, 0.0), (
            player.forward, player.turn, player.speed))
        self.assertEqual((10.0, 4.0, 11.0, 0.5), (
            player.x, player.y, player.z, player.yaw))
        _update_player_input(
            state, 1, forward=-1.0, turn=-1.0, speed=-99.0,
            x=12.0, y=3.5, z=13.0, yaw=0.6)
        self.assertEqual((0.0, 0.0, 0.0), (
            player.forward, player.turn, player.speed))
        self.assertEqual((12.0, 3.5, 13.0, 0.6), (
            player.x, player.y, player.z, player.yaw))
        player.forward = 1.0
        player.turn = 1.0
        player.speed = 99.0
        state._apply_movement(player, 1.0)
        self.assertEqual((0.0, 0.0, 0.0), (
            player.forward, player.turn, player.speed))
        self.assertEqual((12.0, 3.5, 13.0, 0.6), (
            player.x, player.y, player.z, player.yaw))
        self.assertEqual(2000, state._public_player(
            player)['siege_time_left_ms'])
        for unused_tick in range(59):
            state._advance_siege_states()
        self.assertEqual(SIEGE_SWITCHING_ON, player.siege_state)
        state._advance_siege_states()
        self.assertEqual(SIEGE_ENABLED, player.siege_state)

        _update_player_input(state, 1, forward=1.0, turn=1.0, speed=99.0)
        self.assertEqual((1.0, 1.0), (player.forward, player.turn))
        self.assertAlmostEqual(5.0 / 3.6, player.speed)
        _update_player_input(state, 1, siege_enabled=False)
        self.assertEqual(SIEGE_SWITCHING_OFF, player.siege_state)
        self.assertEqual((0.0, 0.0, 0.0), (
            player.forward, player.turn, player.speed))
        for unused_tick in range(60):
            state._advance_siege_states()
        self.assertEqual(SIEGE_DISABLED, player.siege_state)
        self.assertEqual(0, state._public_player(
            player)['siege_time_left_ms'])

    def test_1513_siege_vehicle_table_matches_pinned_xml(self):
        self.assertEqual(
            (2.0, 1.3, 10.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S10_Strv_103_0_Series'])
        self.assertEqual(
            (2.0, 1.3, 10.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S11_Strv_103B'])
        self.assertEqual(
            (2.0, 2.0, 5.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S21_UDES_03'])
        self.assertEqual(
            (2.0, 1.3, 8.0 / 3.6, 2.0),
            SIEGE_VEHICLE_PARAMS['sweden:S22_Strv_S1'])

    def test_server_trusts_worker_siege_motion_after_wire_validation(self):
        for offset, (vehicle, params) in enumerate(sorted(
                SIEGE_VEHICLE_PARAMS.items())):
            identity = {
                'id': 16 + offset, 'team': 2, 'slot': offset,
                'name': 'Bot%d' % offset, 'vehicle': vehicle,
                'max_health': 1000,
            }
            base = {
                'id': 16 + offset, 'health': 1000, 'alive': True,
                'x': 10.0, 'y': 1.0, 'z': 20.0, 'yaw': 0.25,
                'pitch': 0.0, 'roll': 0.0,
                'movement_dir': 1, 'rotation_dir': 0,
                'siege_state': SIEGE_ENABLED,
                'siege_time_left_ms': 0,
                'siege_transition_total_ms': 0,
            }
            wire_limit = round(float(params[2]), 4)
            for speed in (wire_limit, -wire_limit, wire_limit + 0.0001):
                sanitized = BattleState._sanitize_bot_state(
                    dict(base, speed=speed), identity, None)
                self.assertAlmostEqual(speed, sanitized['speed'], places=4)

    def test_server_preserves_worker_siege_transition_pose(self):
        identity = {
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'sweden:S11_Strv_103B', 'max_health': 1000,
        }
        base = {
            'id': 16, 'health': 1000, 'alive': True,
            'x': 10.0, 'y': 1.0, 'z': 20.0, 'yaw': 0.25,
            'pitch': 0.0, 'roll': 0.0,
            'speed': 8.0, 'movement_dir': 1, 'rotation_dir': 1,
            'siege_state': SIEGE_DISABLED,
            'siege_time_left_ms': 0,
            'siege_transition_total_ms': 0,
        }
        previous = BattleState._sanitize_bot_state(base, identity, None)
        transition = dict(
            base, speed=0.0, movement_dir=0, rotation_dir=0,
            siege_state=SIEGE_SWITCHING_ON,
            siege_time_left_ms=2000,
            siege_transition_total_ms=2000,
            y=0.8, pitch=-0.1, roll=0.05)

        switching = BattleState._sanitize_bot_state(
            transition, identity, previous)
        self.assertEqual((0.8, -0.1, 0.05), (
            switching['y'], switching['pitch'], switching['roll']))

        advancing = BattleState._sanitize_bot_state(
            dict(transition, speed=0.00001, movement_dir=0.005,
                 rotation_dir=-0.005, x=10.1, z=20.1, yaw=0.3),
            identity, previous)
        self.assertEqual((10.1, 20.1, 0.3), (
            advancing['x'], advancing['z'], advancing['yaw']))

        enabled = dict(
            transition, siege_state=SIEGE_ENABLED,
            siege_time_left_ms=0, siege_transition_total_ms=0,
            y=0.5, pitch=-0.2, roll=0.1)
        settled = BattleState._sanitize_bot_state(
            enabled, identity, switching)
        self.assertEqual((10.0, 20.0, 0.25), (
            settled['x'], settled['z'], settled['yaw']))
        moved = BattleState._sanitize_bot_state(
            dict(enabled, x=10.1), identity, switching)
        self.assertEqual(10.1, moved['x'])

    def test_siege_request_rejects_non_bool_and_destroyed_engine(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S11_Strv_103B'

        _update_player_input(state, 1, siege_enabled=1)
        self.assertEqual(SIEGE_DISABLED, player.siege_state)
        player.critical = {
            'destroyed': ['engineHealth'], 'devices': []}
        _update_player_input(state, 1, siege_enabled=True)
        self.assertEqual(SIEGE_DISABLED, player.siege_state)

    def test_damaged_engine_uses_pinned_siege_transition_coefficient(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S11_Strv_103B'
        player.critical = {
            'destroyed': [],
            'devices': [{
                'name': 'engineHealth', 'state': 'critical',
                'hp': 20.0, 'max_hp': 100.0,
            }],
        }

        _update_player_input(state, 1, siege_enabled=True)

        self.assertEqual(SIEGE_SWITCHING_ON, player.siege_state)
        self.assertEqual(120, player.siege_transition_ticks)

    def test_server_trusts_worker_projectile_during_siege_transition(self):
        state = _state()
        player = state.players[1]
        player.vehicle = 'sweden:S22_Strv_S1'
        player.siege_state = SIEGE_SWITCHING_ON

        self.assertTrue(_launch_authority(state, _launch()))
        enabled = _state()
        enabled.players[1].vehicle = 'sweden:S22_Strv_S1'
        enabled.players[1].siege_state = SIEGE_ENABLED
        self.assertTrue(_launch_authority(enabled, _launch()))

    def test_modern_player_launch_is_atomic_and_idempotent(self):
        state = _state()
        message = _launch()
        mapped = []

        self.assertTrue(_launch_authority(
            state, message,
            before_launch=lambda: mapped.append(
                state.players[1].pending_fire_intents[1][
                    'trigger_launch_time_ms'])))
        self.assertEqual(1, state.players[1].fire_seq)
        self.assertEqual(['1:p:1:1'], sorted(state.projectiles))
        shot = state.pending_events[-1]
        self.assertEqual('ussr:R11_MS-1', shot['source_vehicle'])
        self.assertEqual(message['source_shot'], shot['source_shot'])
        self.assertEqual(
            'ussr:R11_MS-1',
            state._projectile_snapshot()[0]['source_vehicle'])
        self.assertEqual(
            message['source_shot'],
            state._projectile_snapshot()[0]['source_shot'])
        self.assertEqual('shot', shot['kind'])
        self.assertEqual([0.0, 1.0, 0.0], shot['origin'])
        self.assertEqual([100.0, 0.0, 0.0], shot['velocity'])
        self.assertEqual([0.0, 0.0, 0.0], shot['range_origin'])
        record = state.projectiles['1:p:1:1']
        self.assertEqual([0.0, 0.0, 0.0], record['range_origin'])
        self.assertEqual(message['origin'], record['segment_origin'])
        self.assertEqual(message['velocity'], record['segment_velocity'])
        self.assertEqual(0, record['segment_start_time_ms'])
        self.assertEqual(0, record['ricochet_count'])
        self.assertEqual(1.0, record['base_penetration_multiplier'])
        for key, value in state._projectile_snapshot()[0].items():
            self.assertEqual(value, shot[key])
        self.assertEqual(1.570796, shot['shot_yaw'])
        # The launch instant is the mapped visible trigger frozen at intent
        # admission, not the tick the canonical launch reached the server.
        self.assertEqual(mapped, [shot['launch_server_time_ms']])

        event_count = len(state.pending_events)
        self.assertTrue(_launch_authority(state, dict(message)))
        self.assertEqual(event_count, len(state.pending_events))
        self.assertFalse(state.launch_projectile(
            1, dict(message, velocity=[101.0, 0.0, 0.0])))
        self.assertFalse(state.launch_projectile(
            1, _launch(shot_seq=3)))
        self.assertEqual(1, state.players[1].fire_seq)

    def test_launch_freezes_authoritative_player_range_origin(self):
        state = _state()
        state.players[1].x = 12.5
        state.players[1].y = 2.25
        state.players[1].z = -7.75
        message = _launch(origin=[20.0, 3.0, 9.0])

        self.assertTrue(_launch_authority(
            state, message,
            before_launch=lambda: setattr(state.players[1], 'x', 99.0)))

        record = state.projectiles['1:p:1:1']
        snapshot = state._projectile_snapshot()[0]
        self.assertEqual([12.5, 2.25, -7.75], record['range_origin'])
        self.assertEqual(record['range_origin'], snapshot['range_origin'])
        self.assertEqual([20.0, 3.0, 9.0], record['origin'])
        self.assertEqual([12.5, 2.25, -7.75],
                         state.pending_events[-1]['range_origin'])

        rejected = _state()
        self.assertFalse(_launch_authority(
            rejected, dict(_launch(), range_origin=[500.0, 0.0, 0.0])))
        self.assertFalse(rejected.projectiles)

    def test_player_fire_intent_freezes_order_but_trusts_worker_ballistics(self):
        state = _state()
        player = state.players[1]
        relayed = []
        state.simulation_worker.offer_reliable = lambda message: (
            relayed.append(dict(message)) or True)
        self.assertTrue(_update_player_input(
            state, 1, x=3.25, y=1.5, z=-4.75, yaw=0.25,
            aim_yaw=-0.5, gun_pitch=0.125))
        message = _fire_intent(
            state, 1, shot_origin=[3.25, 2.5, -4.75])

        self.assertTrue(state.submit_fire_intent(1, message))
        self.assertEqual(1, len(relayed))
        relay = relayed[0]
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID,
                         state.bot_authority_id)
        self.assertEqual(1, relay['intent_seq'])
        self.assertEqual(1, relay['shot_seq'])
        self.assertEqual(player.input_seq, relay['input_seq'])
        self.assertEqual(player.pose_time_us, relay['pose_time_us'])
        self.assertEqual((3.25, 1.5, -4.75),
                         (relay['x'], relay['y'], relay['z']))
        self.assertEqual((-0.5, 0.125),
                         (relay['aim_yaw'], relay['gun_pitch']))
        self.assertEqual([3.25, 2.5, -4.75], relay['shot_origin'])
        self.assertEqual([0.0, 0.0, 1.0], relay['shot_direction'])
        self.assertEqual(0.01, relay['dispersion_angle'])
        self.assertEqual(0, relay['next_shell_index'])
        self.assertFalse(relay['shell_change_pending'])
        self.assertEqual(player.input_seq, relay['gun_checkpoint_seq'])
        self.assertEqual(_gun_checkpoint(), relay['gun_checkpoint'])
        self.assertNotIn('deadline_server_time_ms', relay)

        self.assertTrue(state.submit_fire_intent(1, dict(message)))
        self.assertEqual(1, len(relayed))
        self.assertFalse(state.submit_fire_intent(
            1, dict(message, shell_index=1)))
        self.assertFalse(state.submit_fire_intent(
            1, dict(message, intent_seq=3)))
        self.assertFalse(state.submit_fire_intent(
            1, dict(message, shot_direction=[1.0, 0.0, 0.0])))
        self.assertEqual([1], list(player.pending_fire_intents))
        launch = _launch(
            origin=list(relay['shot_origin']), velocity=[100.0, 0.0, 0.0],
            authority_epoch=state.authority_epoch,
            fire_intent_seq=relay['intent_seq'],
            fire_input_seq=relay['input_seq'])
        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, launch))

    def test_player_fire_intent_survives_worker_stall_beyond_five_seconds(self):
        state = _state(players=1)
        now_ms = [1000]
        state._server_time_ms = lambda: now_ms[0]
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        player = state.players[1]
        relay = player.pending_fire_intents[1]

        now_ms[0] += 6001
        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _launch(
                origin=list(relay['shot_origin']),
                velocity=[0.0, 0.0, 100.0],
                authority_epoch=state.authority_epoch,
                fire_intent_seq=relay['intent_seq'],
                fire_input_seq=relay['input_seq'])))

        projectile_id = state._projectile_id(1, 'player', 1, 1)
        self.assertNotIn(1, player.pending_fire_intents)
        self.assertEqual(
            (True, projectile_id), player.fire_intent_results[1])
        self.assertIn(projectile_id, state.projectiles)

    def test_late_next_gun_checkpoint_is_kept_and_old_retry_cannot_replace_it(self):
        state = _state(players=1)
        player = state.players[1]
        first = _gun_checkpoint(reload_time=1.0, clip=0)
        second = _gun_checkpoint(reload_time=0.0, clip=1)

        self.assertTrue(_update_player_input(
            state, 1, gun_checkpoint=first))
        first_message = json.loads(player.input_fingerprints[1])
        # The fingerprint is JSON, so retry the original semantic input
        # through the helper's frozen server record instead of reusing it.
        self.assertTrue(_update_player_input(
            state, 1, gun_checkpoint=second))
        self.assertEqual(2, player.gun_checkpoint_seq)
        self.assertEqual(second, player.gun_checkpoint)
        self.assertEqual(first, player.gun_checkpoints[1])
        self.assertEqual(second, player.gun_checkpoints[2])

        # An exact retry of input 1 remains idempotent but cannot roll the
        # public checkpoint back from the already-admitted input 2.
        self.assertTrue(state.update_input(1, first_message))
        self.assertEqual(2, player.gun_checkpoint_seq)
        self.assertEqual(second, player.gun_checkpoint)
        self.assertFalse(state.update_input(1, {
            'type': 'input', 'round_id': state.round_id,
            'input_seq': 3, 'pose_time_us': state._logical_motion_time_us(),
            'shell_index': 0, 'next_shell_index': 0,
            'shell_change_pending': False,
        }))
        self.assertEqual(2, player.input_seq)

    def test_player_queued_shell_is_promoted_by_the_canonical_shot(self):
        state = _state()
        player = state.players[1]
        self.assertTrue(_update_player_input(
            state, 1, shell_index=0, next_shell_index=1,
            shell_change_pending=True))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, shot_direction=[1.0, 0.0, 0.0])))
        relay = player.pending_fire_intents[1]
        self.assertEqual(1, relay['next_shell_index'])
        self.assertTrue(relay['shell_change_pending'])

        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, _launch(
                authority_epoch=state.authority_epoch,
                fire_intent_seq=relay['intent_seq'],
                fire_input_seq=relay['input_seq'])))

        self.assertEqual(1, player.shell_index)
        self.assertEqual(1, player.next_shell_index)
        self.assertFalse(player.shell_change_pending)
        public = state._public_player(player)
        self.assertEqual(1, public['shell_index'])
        self.assertEqual(1, public['next_shell_index'])
        self.assertFalse(public['shell_change_pending'])

    def test_unidentifiable_fire_intents_do_not_advance_the_frontier(self):
        state = _state()
        player = state.players[1]
        results = []
        player.offer_reliable = lambda message: (
            results.append(dict(message)) or True)
        self.assertTrue(_update_player_input(state, 1))
        unidentifiable = (
            _fire_intent(state, round_id=state.round_id + 1),
            _fire_intent(state, round_id=float(state.round_id)),
            _fire_intent(state, type='equipment_intent'),
            _fire_intent(state, intent_seq=2),
            _fire_intent(state, intent_seq='1'),
        )
        missing_round = _fire_intent(state)
        missing_round.pop('round_id')
        missing_sequence = _fire_intent(state)
        missing_sequence.pop('intent_seq')

        for message in unidentifiable + (missing_round, missing_sequence):
            with self.subTest(message=message):
                self.assertFalse(state.submit_fire_intent(1, message))
                self.assertEqual(0, player.fire_intent_seq)
                self.assertFalse(player.fire_intent_fingerprints)
                self.assertFalse(player.fire_intent_results)
                self.assertFalse(results)
        self.assertFalse(state.submit_fire_intent(
            99, _fire_intent(state)))
        self.assertEqual(0, player.fire_intent_seq)
        self.assertFalse(results)

    def test_identified_malformed_fire_intents_receive_one_terminal(self):
        cases = (
            ('input_type', {'input_seq': '1'}, None,
             'fire_intent_field_invalid'),
            ('origin_type', {'shot_origin': '0,1,0'}, None,
             'fire_intent_field_invalid'),
            ('origin_bounds', {'shot_origin': [5001.0, 1.0, 0.0]}, None,
             'fire_intent_field_invalid'),
            ('direction_nan', {
                'shot_direction': [float('nan'), 0.0, 1.0]}, None,
             'fire_intent_field_invalid'),
            ('dispersion_inf', {'dispersion_angle': float('inf')}, None,
             'fire_intent_field_invalid'),
            ('dispersion_bounds', {'dispersion_angle': 0.5001}, None,
             'fire_intent_field_invalid'),
            ('missing_origin', {}, 'shot_origin',
             'fire_intent_wire_shape'),
            ('extra_field', {'unexpected': True}, None,
             'fire_intent_wire_shape'),
        )
        for label, changes, missing, reason in cases:
            with self.subTest(label=label):
                state = _state()
                player = state.players[1]
                results = []
                relayed = []
                player.offer_reliable = lambda result: (
                    results.append(dict(result)) or True)
                state.simulation_worker.offer_reliable = lambda relay: (
                    relayed.append(dict(relay)) or True)
                self.assertTrue(_update_player_input(state, 1))
                message = _fire_intent(state, **changes)
                if missing is not None:
                    message.pop(missing)

                self.assertTrue(state.submit_fire_intent(1, message))
                self.assertEqual(1, player.fire_intent_seq)
                self.assertEqual((False, reason),
                                 player.fire_intent_results[1])
                self.assertEqual(reason, results[0]['reason'])
                self.assertFalse(player.pending_fire_intents)
                self.assertFalse(relayed)

                # One exact malformed retry folds into the recorded terminal;
                # a changed retry conflicts without replacing or redelivering
                # it, and the next legal trigger still reaches authority.
                self.assertTrue(state.submit_fire_intent(1, dict(message)))
                self.assertFalse(state.submit_fire_intent(
                    1, dict(message, shell_index=1)))
                self.assertEqual(1, len(results))
                self.assertEqual((False, reason),
                                 player.fire_intent_results[1])
                self.assertTrue(_update_player_input(state, 1))
                self.assertTrue(state.submit_fire_intent(
                    1, _fire_intent(state)))
                self.assertIn(2, player.pending_fire_intents)
                self.assertEqual(2, relayed[0]['intent_seq'])

    def test_finite_untrusted_fire_rays_receive_terminal_results(self):
        cases = (
            ({'shot_origin': [100.0, 1.0, 0.0]},
             'shot_origin_untrusted'),
            ({'shot_direction': [0.0, 0.0, 0.0]},
             'shot_direction_untrusted'),
            ({'shot_direction': [0.0, 0.0, 0.5]},
             'shot_direction_untrusted'),
        )
        for changes, reason in cases:
            with self.subTest(changes=changes):
                state = _state()
                player = state.players[1]
                results = []
                player.offer_reliable = lambda message: (
                    results.append(dict(message)) or True)
                self.assertTrue(_update_player_input(state, 1))

                self.assertTrue(state.submit_fire_intent(
                    1, _fire_intent(state, **changes)))

                self.assertEqual(1, player.fire_intent_seq)
                self.assertEqual((False, reason),
                                 player.fire_intent_results[1])
                self.assertEqual(reason, results[0]['reason'])
                self.assertFalse(player.pending_fire_intents)
                self.assertEqual(0, player.fire_seq)

    def test_operational_fire_rejections_advance_exactly_once(self):
        cases = (
            ('battle_finished',
             lambda state, unused_player: setattr(
                 state, 'battle_result', {'winner': 0}), {}),
            ('combat_not_accepting',
             lambda state, unused_player: setattr(state, 'tick', 0), {}),
            ('player_not_participating',
             lambda unused_state, player: setattr(
                 player, 'participating', False), {}),
            ('player_dead',
             lambda unused_state, player: setattr(player, 'alive', False),
             {}),
            ('input_checkpoint_stale',
             lambda unused_state, player: setattr(player, 'input_seq', 2),
             {'input_seq': 1}),
            ('shell_mismatch', lambda unused_state, unused_player: None,
             {'shell_index': 1}),
            ('pose_unavailable',
             lambda unused_state, player: setattr(
                 player, 'pose_time_us', None), {}),
            ('fire_sequence_exhausted',
             lambda unused_state, player: setattr(
                 player, 'fire_seq', PROJECTILE_MAX_ID), {}),
        )
        for reason, mutate, changes in cases:
            with self.subTest(reason=reason):
                state = _state(players=1)
                player = state.players[1]
                results = []
                player.offer_reliable = lambda message: (
                    results.append(dict(message)) or True)
                self.assertTrue(_update_player_input(state, 1))
                mutate(state, player)

                self.assertTrue(state.submit_fire_intent(
                    1, _fire_intent(state, **changes)))

                self.assertEqual(1, player.fire_intent_seq)
                self.assertEqual((False, reason),
                                 player.fire_intent_results[1])
                self.assertEqual(reason, results[0]['reason'])
                self.assertFalse(player.pending_fire_intents)

    def test_no_worker_rejection_is_idempotent_and_next_intent_recovers(self):
        state = _state(players=1)
        player = state.players[1]
        results = []
        player.offer_reliable = lambda message: (
            results.append(dict(message)) or True)
        self.assertTrue(_update_player_input(state, 1))
        message = _fire_intent(state)
        state.simulation_worker = None
        state.bot_authority_id = None

        self.assertTrue(state.submit_fire_intent(1, message))
        self.assertEqual([{
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'intent_seq': 1, 'accepted': False,
            'reason': 'worker_unavailable',
        }], results)
        self.assertEqual((False, 'worker_unavailable'),
                         player.fire_intent_results[1])
        self.assertEqual(1, player.fire_intent_seq)
        self.assertFalse(player.pending_fire_intents)
        self.assertEqual((0, 0), (player.fire_seq, player.shell_index))

        self.assertTrue(state.submit_fire_intent(1, dict(message)))
        self.assertEqual(1, len(results))
        self.assertFalse(state.submit_fire_intent(
            1, dict(message, dispersion_angle=0.010000001)))
        self.assertEqual(1, len(results))

        relayed = []
        worker = _attach_worker_authority(state)
        worker.offer_reliable = lambda relay: (
            relayed.append(dict(relay)) or True)
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertEqual(2, player.fire_intent_seq)
        self.assertEqual([2], list(player.pending_fire_intents))
        self.assertEqual(2, relayed[0]['intent_seq'])

    def test_stale_gun_checkpoint_terminal_allows_the_next_intent(self):
        state = _state(players=1)
        player = state.players[1]
        results = []
        relayed = []
        player.offer_reliable = lambda message: (
            results.append(dict(message)) or True)
        state.simulation_worker.offer_reliable = lambda message: (
            relayed.append(dict(message)) or True)
        self.assertTrue(_update_player_input(state, 1))
        player.gun_checkpoint_seq = 0

        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertEqual('gun_checkpoint_unavailable', results[0]['reason'])
        self.assertEqual(1, player.fire_intent_seq)
        self.assertFalse(player.pending_fire_intents)

        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertEqual(2, player.fire_intent_seq)
        self.assertEqual([2], list(player.pending_fire_intents))
        self.assertEqual(2, relayed[0]['intent_seq'])

    def test_pending_capacity_rejection_consumes_only_the_new_intent(self):
        state = _state(players=1)
        player = state.players[1]
        worker = state.simulation_worker
        results = []
        relayed = []
        player.offer_reliable = lambda message: (
            results.append(dict(message)) or True)
        worker.offer_reliable = lambda message: (
            relayed.append(dict(message)) or True)
        self.assertTrue(_update_player_input(state, 1))

        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertEqual([1], list(player.pending_fire_intents))

        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertEqual('fire_intent_pending', results[0]['reason'])
        self.assertEqual([1], list(player.pending_fire_intents))
        self.assertEqual(2, player.fire_intent_seq)

        self.assertTrue(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'fire_intent_result',
                'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'player_id': 1, 'intent_seq': 1,
                'accepted': False, 'reason': 'gun_not_ready',
            }))
        self.assertFalse(player.pending_fire_intents)
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertEqual([3], list(player.pending_fire_intents))
        self.assertEqual([1, 3], [relay['intent_seq'] for relay in relayed])

    def test_worker_offer_stall_publishes_terminal_before_round_failure(self):
        state = _state(players=1)
        player = state.players[1]
        worker = state.simulation_worker
        results = []
        player.offer_reliable = lambda message: (
            results.append(dict(message)) or True)
        worker.offer_reliable = lambda unused_message: False
        self.assertTrue(_update_player_input(state, 1))

        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))

        self.assertEqual('fire_intent_result', results[0]['type'])
        self.assertEqual('worker_send_stalled', results[0]['reason'])
        self.assertEqual(1, player.fire_intent_seq)
        self.assertEqual((False, 'worker_send_stalled'),
                         player.fire_intent_results[1])
        self.assertFalse(player.pending_fire_intents)
        self.assertEqual(0, player.fire_seq)
        self.assertIsNone(state.simulation_worker)
        self.assertIsNotNone(state.battle_result)

    def _presented_bot(self, state, bot_id=16, team=2):
        """Publish one canonical Bot the shooter can legally have displayed."""
        state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_states[bot_id] = {
            'id': bot_id, 'team': team,
            'vehicle': 'ussr:R11_MS-1', 'health': 500,
            'max_health': 500, 'alive': True,
            'display_health': 500, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'critical': {}, 'combat_revision': 0,
            'combat_base_revision': 0, 'combat_ack_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        }
        # A live round is many seconds and many revisions old by the time a
        # player fires; the clock and revision windows below are meaningless
        # against a freshly zeroed fixture.
        state.motion_time_offset_us = 10000000
        state.bot_state_revision = 300
        state.bot_state_time_us = state._logical_motion_time_us()
        return bot_id

    def _ledger_entry(self, state, bot_id=16, lag_us=90000, revision=None):
        return {
            'bot_id': bot_id,
            'bot_state_revision': (
                state.bot_state_revision if revision is None else revision),
            'presentation_time_us': max(
                0, int(state.players[1].pose_time_us) - int(lag_us)),
        }

    def test_player_launch_uses_the_frozen_visible_trigger_clock(self):
        state = _state()
        player = state.players[1]
        state.tick = 450
        self.assertTrue(_update_player_input(state, 1))
        trigger_time_ms = state._server_time_ms()
        # Admission is 133 ms later. A simultaneous catch-up correction moves
        # the independent motion clock by another 300 ms, but must not change
        # the trigger already frozen in the round projectile clock.
        state.tick += 4
        state.motion_time_offset_us += 300000
        receipt_tick_ms = state._server_time_ms()
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, trigger_server_time_ms=trigger_time_ms)))
        relay = player.pending_fire_intents[1]
        mapped = int(relay['trigger_launch_time_ms'])

        self.assertEqual(trigger_time_ms, mapped)
        self.assertEqual(receipt_tick_ms - 133, mapped)

        state.tick += 6
        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, _launch(
                fire_intent_seq=1, fire_input_seq=relay['input_seq'],
                shot_seq=relay['shot_seq'],
                authority_epoch=state.authority_epoch)))
        record = state.projectiles['1:p:1:1']
        self.assertEqual(mapped, record['launch_server_time_ms'])
        self.assertEqual(
            state._server_time_ms() - 333,
            record['launch_server_time_ms'])

    def test_trigger_clock_bounds_have_typed_terminal_results(self):
        state = _state()
        state.tick += 30
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state,
            trigger_server_time_ms=state._server_time_ms() - 500)))
        self.assertEqual(
            state._server_time_ms() - 500,
            state.players[1].pending_fire_intents[1][
                'trigger_launch_time_ms'])

        state = _state()
        player = state.players[1]
        state.tick += 30
        self.assertTrue(_update_player_input(state, 1))
        delivered = []
        player.offer_reliable = lambda message: (
            delivered.append(dict(message)) or True)

        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state,
            trigger_server_time_ms=state._server_time_ms() - 501)))
        self.assertEqual(
            (False, 'trigger_clock_stale'), player.fire_intent_results[1])
        self.assertEqual('trigger_clock_stale', delivered[-1]['reason'])
        self.assertNotIn(1, player.pending_fire_intents)

        # A small ahead estimate is legal clock quantization, but canonical
        # projectile time is clamped once to server receipt.
        state = _state()
        player = state.players[1]
        state.tick += 30
        self.assertTrue(_update_player_input(state, 1))
        receipt_time_ms = state._server_time_ms()
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, trigger_server_time_ms=receipt_time_ms + 250)))
        self.assertEqual(
            receipt_time_ms,
            int(player.pending_fire_intents[1]['trigger_launch_time_ms']))

        state = _state()
        player = state.players[1]
        state.tick += 30
        self.assertTrue(_update_player_input(state, 1))
        delivered = []
        player.offer_reliable = lambda message: (
            delivered.append(dict(message)) or True)
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state,
            trigger_server_time_ms=state._server_time_ms() + 251)))
        self.assertEqual(
            (False, 'trigger_clock_future'), player.fire_intent_results[1])
        self.assertEqual('trigger_clock_future', delivered[-1]['reason'])
        self.assertNotIn(1, player.pending_fire_intents)

    def test_invalid_trigger_clock_is_a_typed_local_failure(self):
        missing = object()
        for raw_time in (missing, None, True, 1.0, -1,
                         MAX_MOTION_TIME_US // 1000 + 1):
            state = _state()
            player = state.players[1]
            self.assertTrue(_update_player_input(state, 1))
            delivered = []
            player.offer_reliable = lambda message: (
                delivered.append(dict(message)) or True)
            intent = _fire_intent(state, trigger_server_time_ms=raw_time)
            if raw_time is missing:
                intent.pop('trigger_server_time_ms')

            self.assertTrue(state.submit_fire_intent(1, intent))
            self.assertEqual(
                (False, 'trigger_clock_invalid'),
                player.fire_intent_results[1])
            self.assertEqual('trigger_clock_invalid', delivered[-1]['reason'])
            self.assertNotIn(1, player.pending_fire_intents)

    def test_mapped_launch_time_is_invariant_to_further_delay(self):
        times = []
        for extra_ticks in (0, 3, 30):
            state = _state()
            player = state.players[1]
            self.assertTrue(_update_player_input(state, 1))
            self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
            relay = player.pending_fire_intents[1]
            state.tick += extra_ticks
            self.assertTrue(state.launch_projectile(
                SIMULATION_WORKER_AUTHORITY_ID, _launch(
                    fire_intent_seq=1, fire_input_seq=relay['input_seq'],
                    shot_seq=relay['shot_seq'],
                    authority_epoch=state.authority_epoch)))
            record = state.projectiles['1:p:1:1']
            times.append(record['launch_server_time_ms'])
            # An exact retry after even more delay reuses the same instant.
            state.tick += 10
            self.assertTrue(state.launch_projectile(
                SIMULATION_WORKER_AUTHORITY_ID, _launch(
                    fire_intent_seq=1, fire_input_seq=relay['input_seq'],
                    shot_seq=relay['shot_seq'],
                    authority_epoch=state.authority_epoch)))
            self.assertEqual(
                times[-1], state.projectiles['1:p:1:1'][
                    'launch_server_time_ms'])
        self.assertEqual(1, len(set(times)))

    def test_bot_launch_timing_is_unchanged_by_the_player_mapping(self):
        state = _state()
        state.bot_states[16] = {
            'id': 16, 'team': 2, 'vehicle': 'ussr:R11_MS-1',
            'health': 500, 'max_health': 500, 'alive': True,
            'display_health': 500, 'fire_seq': 0,
        }
        state.bot_pending_projectile_launches.add((16, 1))
        state.bot_launch_clock_offset_us = (
            state._server_time_ms() * 1000 - 100000)

        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _launch(shooter_id=16, shooter_kind='bot')))
        record = state.projectiles['1:b:16:1']
        self.assertEqual(
            int(round((100000 + state.bot_launch_clock_offset_us) / 1000.0)),
            record['launch_server_time_ms'])

    def test_fire_intent_relays_the_validated_presentation_ledger(self):
        state = _state()
        player = state.players[1]
        bot_id = self._presented_bot(state)
        self.assertTrue(_update_player_input(state, 1))
        entry = self._ledger_entry(state, bot_id)
        delivered = []
        state.simulation_worker.offer_reliable = lambda message: (
            delivered.append(dict(message)) or True)

        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, presentation_ledger=[entry])))
        self.assertEqual([entry], delivered[-1]['presentation_ledger'])
        self.assertEqual(
            [entry], player.pending_fire_intents[1]['presentation_ledger'])
        # An exact retransmission is idempotent; a conflicting one is not.
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, intent_seq=1, presentation_ledger=[entry])))
        self.assertEqual(1, len(delivered))
        self.assertFalse(state.submit_fire_intent(1, _fire_intent(
            state, intent_seq=1, presentation_ledger=[])))
        self.assertEqual(1, len(delivered))

    def test_empty_presentation_ledger_is_a_legal_statement(self):
        state = _state()
        self._presented_bot(state)
        self.assertTrue(_update_player_input(state, 1))

        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, presentation_ledger=[])))
        self.assertEqual(
            [], state.players[1].pending_fire_intents[1][
                'presentation_ledger'])

    def test_missing_presentation_ledger_is_a_typed_local_failure(self):
        state = _state()
        player = state.players[1]
        self.assertTrue(_update_player_input(state, 1))
        message = _fire_intent(state)
        message.pop('presentation_ledger')
        delivered = []
        player.offer_reliable = lambda result: (
            delivered.append(dict(result)) or True)

        self.assertTrue(state.submit_fire_intent(1, message))
        self.assertEqual(1, player.fire_intent_seq)
        self.assertEqual(
            (False, 'presentation_ledger_invalid'),
            player.fire_intent_results[1])
        self.assertEqual('presentation_ledger_invalid', delivered[-1]['reason'])
        self.assertEqual({}, dict(player.pending_fire_intents))
        # The exact malformed retry is idempotent and does not republish its
        # terminal; the next sequence can still be admitted normally.
        self.assertTrue(state.submit_fire_intent(1, dict(message)))
        self.assertEqual(1, len(delivered))
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(state)))
        self.assertIn(2, player.pending_fire_intents)

    def test_invalid_presentation_evidence_is_a_typed_local_failure(self):
        bot_id = 16
        for label, ledger in (
                ('unknown_bot', None),
                ('duplicate_bot', None),
                ('future_revision', None),
                ('retired_revision', None),
                ('future_presentation', None),
                ('stale_presentation', None),
                ('malformed_entry', [{'bot_id': bot_id}]),
                ('extra_field', None),
                ('oversized', None)):
            state = _state()
            player = state.players[1]
            self._presented_bot(state, bot_id)
            self.assertTrue(_update_player_input(state, 1))
            entry = self._ledger_entry(state, bot_id)
            if label == 'unknown_bot':
                ledger = [dict(entry, bot_id=17)]
            elif label == 'duplicate_bot':
                ledger = [dict(entry), dict(entry)]
            elif label == 'future_revision':
                ledger = [dict(entry,
                               bot_state_revision=state.bot_state_revision + 1)]
            elif label == 'retired_revision':
                ledger = [dict(
                    entry,
                    bot_state_revision=state.bot_state_revision - 256)]
            elif label == 'future_presentation':
                ledger = [dict(
                    entry,
                    presentation_time_us=int(player.pose_time_us) + 1)]
            elif label == 'stale_presentation':
                ledger = [dict(entry, presentation_time_us=max(
                    0, int(player.pose_time_us) - 2000000))]
            elif label == 'extra_field':
                ledger = [dict(entry, presented_pose=[0.0, 0.0, 0.0])]
            elif label == 'oversized':
                ledger = [dict(entry, bot_id=index)
                          for index in range(1, 32)]
            delivered = []
            player.offer_reliable = lambda message: (
                delivered.append(dict(message)) or True)

            self.assertTrue(
                state.submit_fire_intent(1, _fire_intent(
                    state, presentation_ledger=ledger)), label)
            # One bad shot settles as an observable local terminal; it never
            # reaches the authority and never ends the round.
            self.assertEqual(
                (False, 'presentation_ledger_invalid'),
                player.fire_intent_results[1], label)
            self.assertEqual(
                'presentation_ledger_invalid',
                delivered[-1]['reason'], label)
            self.assertNotIn(1, player.pending_fire_intents)
            self.assertIsNone(state.battle_result, label)
            # The next legal trigger still works.
            self.assertTrue(_update_player_input(state, 1))
            self.assertTrue(state.submit_fire_intent(1, _fire_intent(
                state, intent_seq=2,
                presentation_ledger=[self._ledger_entry(state, bot_id)])),
                label)

    def test_bot_revision_window_matches_the_ram_evidence_contract(self):
        state = _state()
        bot_id = self._presented_bot(state)
        self.assertTrue(_update_player_input(state, 1))
        entry = self._ledger_entry(state, bot_id, revision=45)

        self.assertTrue(state.submit_fire_intent(
            1, _fire_intent(state, presentation_ledger=[entry])))
        self.assertEqual(
            [entry],
            state.players[1].pending_fire_intents[1]['presentation_ledger'])
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(1, _fire_intent(
            state, intent_seq=2,
            presentation_ledger=[
                self._ledger_entry(state, bot_id, revision=44)])))
        self.assertEqual(
            (False, 'presentation_ledger_invalid'),
            state.players[1].fire_intent_results[2])

    def test_worker_fire_rejection_is_committed_after_visible_delivery(self):
        state = _state()
        player = state.players[1]
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(
            1, _fire_intent(state)))
        delivered = []
        player.offer_reliable = lambda message: (
            delivered.append(dict(message)) or True)
        rejection = {
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'authority_epoch': state.authority_epoch, 'player_id': 1,
            'intent_seq': 1, 'accepted': False, 'reason': 'gun_not_ready',
        }

        self.assertTrue(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID, rejection))
        self.assertEqual([{
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'intent_seq': 1, 'accepted': False, 'reason': 'gun_not_ready',
        }], delivered)
        self.assertNotIn(1, player.pending_fire_intents)
        self.assertEqual((False, 'gun_not_ready'),
                         player.fire_intent_results[1])
        self.assertTrue(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID, dict(rejection)))
        self.assertEqual(1, len(delivered))
        self.assertFalse(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(rejection, reason='different')))

    def test_failed_fire_rejection_delivery_disconnects_without_commit(self):
        state = _state()
        player = state.players[1]
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(
            1, _fire_intent(state)))
        player.offer_reliable = lambda unused_message: False

        self.assertFalse(state.resolve_fire_intent(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'fire_intent_result',
                'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'player_id': 1, 'intent_seq': 1,
                'accepted': False, 'reason': 'gun_not_ready',
            }))
        self.assertNotIn(1, state.players)
        self.assertEqual({}, player.fire_intent_results)

    def test_order_rejected_player_launch_resolves_intent_without_worker_loss(self):
        state = _state()
        player = state.players[1]
        worker = state.simulation_worker
        player_messages = []
        worker_messages = []
        player.offer_reliable = lambda message: (
            player_messages.append(dict(message)) or True)
        worker.offer_reliable = lambda message: (
            worker_messages.append(dict(message)) or True)
        self.assertTrue(_update_player_input(state, 1))
        self.assertTrue(state.submit_fire_intent(
            1, _fire_intent(state)))
        relay = worker_messages.pop()
        launch = _launch()
        launch.update({
            'authority_epoch': state.authority_epoch,
            'shot_seq': relay['shot_seq'],
            'fire_intent_seq': relay['intent_seq'],
            'fire_input_seq': relay['input_seq'],
        })
        player.alive = False
        handler = object.__new__(ClientHandler)

        self.assertFalse(handler._dispatch_simulation_worker_message(
            types.SimpleNamespace(state=state), worker, launch))

        terminal = {
            'type': 'fire_intent_result', 'round_id': state.round_id,
            'player_id': 1, 'intent_seq': 1, 'accepted': False,
            'reason': 'projectile_launch_rejected',
        }
        self.assertEqual([terminal], player_messages)
        self.assertEqual([terminal], worker_messages)
        self.assertNotIn(1, player.pending_fire_intents)
        self.assertEqual(
            (False, 'projectile_launch_rejected'),
            player.fire_intent_results[1])
        self.assertFalse(state.projectiles)
        self.assertIs(state.simulation_worker, worker)
        self.assertTrue(worker.connected)

    def test_rejected_worker_ricochet_preserves_the_worker_round(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']
        worker = state.simulation_worker
        handler = object.__new__(ClientHandler)
        malformed = _ricochet('1:p:1:1')
        malformed['direct']['damage'] = 1

        self.assertFalse(handler._dispatch_simulation_worker_message(
            types.SimpleNamespace(state=state), worker, malformed))
        self.assertIs(state.simulation_worker, worker)
        self.assertTrue(worker.connected)
        self.assertEqual(0, record['ricochet_count'])
        self.assertEqual(1000, state.players[2].health)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_rejected_worker_terminal_preserves_the_worker_round(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']
        worker = state.simulation_worker
        handler = object.__new__(ClientHandler)
        malformed = _resolve(
            '1:p:1:1', direct=_effect(target_id=1))

        self.assertFalse(handler._dispatch_simulation_worker_message(
            types.SimpleNamespace(state=state), worker, malformed))

        self.assertIs(state.simulation_worker, worker)
        self.assertTrue(worker.connected)
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(
            'direct_self_hit', state.last_projectile_resolve_reject_code)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_projectile_commands_after_battle_result_are_late_noops(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        state.battle_result = {'winner': 1, 'reason': 'team_eliminated'}

        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'projectile_progress', 'round_id': 1,
                'authority_epoch': 1, 'cursors': [{
                    'projectile_id': '1:p:1:1',
                    'base_checked_ms': 0,
                    'checked_through_ms': 0,
                    'checked_distance': 0.0,
                    'piercing_loss': 0.0,
                    'penetration_factor': 1.0,
                    'destructibles': [],
                }]}))
        self.assertTrue(state.ricochet_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': 1, 'authority_epoch': 1}))
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': 1, 'authority_epoch': 1}))
        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': 1, 'authority_epoch': 0}))
        self.assertEqual(
            'authority', state.last_projectile_resolve_reject_code)
        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'round_id': 1, 'authority_epoch': 1,
                'impact': float('nan')}))
        self.assertEqual('finite', state.last_projectile_resolve_reject_code)

    def test_launch_rejects_malformed_but_trusts_finite_source_shot(self):
        valid = _launch()
        invalid = []
        for mutate in (
                lambda shot: shot.update(gravity='9.81'),
                lambda shot: shot.update(extra=1),
                lambda shot: shot['shell'].update(damage=[390.0]),
                lambda shot: shot['shell'].update(caliber=True)):
            candidate = json.loads(json.dumps(valid))
            mutate(candidate['source_shot'])
            invalid.append(candidate)
        missing = json.loads(json.dumps(valid))
        missing.pop('source_shot')
        invalid.append(missing)

        for message in invalid:
            with self.subTest(message=message):
                state = _state()
                self.assertFalse(_launch_authority(state, message))
                self.assertEqual(0, state.players[1].fire_seq)
                self.assertFalse(state.projectiles)

        for mutate in (
                lambda shot: shot.update(speed=101.0),
                lambda shot: shot['shell'].update(kind='HIGH_EXPLOSIVE'),
                lambda shot: shot['shell'].update(explosionRadius=4.0)):
            state = _state()
            message = json.loads(json.dumps(valid))
            mutate(message['source_shot'])
            self.assertTrue(_launch_authority(state, message))

    def test_large_finite_module_values_survive_server_validation(self):
        amount = 500000000.0
        shot = _source_shot(
            100.0, 9.81, 1000.0, damage=(390.0, amount))
        critical = {
            'devices': [{
                'name': 'ammoBayHealth', 'hp': amount,
                'max_hp': amount, 'state': 'normal',
            }],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        delta = {
            'devices': [{
                'name': 'ammoBayHealth', 'hp_loss': amount,
            }],
            'crew_ko': [], 'ignite': False,
        }

        self.assertEqual(
            amount,
            _projectile_source_shot(shot)['shell']['damage'][1])
        self.assertEqual(amount, _critical_payload(
            critical)['devices'][0]['max_hp'])
        self.assertEqual(
            amount, _critical_damage_delta(delta)['devices'][0]['hp_loss'])

    def test_he_factors_survive_server_ledger_and_snapshot(self):
        state = _state()
        message = _launch(is_he=True, splash_radius=4.5)
        message['source_shot']['shell'].update({
            'explosionDamageFactor': 0.55,
            'explosionDamageAbsorptionFactor': 1.4,
            'explosionEdgeDamageFactor': 0.2,
        })

        self.assertTrue(_launch_authority(state, message))

        shell = state._projectile_snapshot()[0]['source_shot']['shell']
        self.assertEqual(0.55, shell['explosionDamageFactor'])
        self.assertEqual(1.4, shell['explosionDamageAbsorptionFactor'])
        self.assertEqual(0.2, shell['explosionEdgeDamageFactor'])

    def test_retail_spg_gravity_is_admitted_and_snapshotted(self):
        state = _state()

        self.assertTrue(_launch_authority(
            state, _launch(gravity=143.0)))
        self.assertEqual(143.0,
                         state.projectiles['1:p:1:1']['gravity'])
        self.assertEqual(143.0,
                         state._projectile_snapshot()[0]['gravity'])

    def test_bot_state_edge_waits_for_authorized_launch(self):
        state = _state()
        state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_roster = [{
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Bot',
        }]
        manifest = [{
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'health': 1000,
            'max_health': 1000, 'x': 20.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'world_pose': True, 'profile': {},
            'reload_time': 0.0, 'reload_duration': 1.5,
            'route': {'id': 'test', 'waypoints': []},
        }]
        self.assertTrue(state.update_bot_manifest(
            SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': 1, 'bots': manifest,
            'player_collision_profiles': [
                {
                    'id': player.player_id, 'vehicle': player.vehicle,
                    'mass': player.effective_params['physics']['mass'],
                    'shape': [3.0, 6.0, -1.0, 2.0],
                    'ram_profile': {
                        'spall_coefficient': player.effective_params[
                            'ramming']['spall_coefficient'],
                        'ramming_bonus': player.effective_params[
                            'ramming']['ramming_bonus'],
                    },
                }
                for player in state.players.values()
            ]}))
        state.pending_events[:] = []
        publication = {
            'id': 16, 'x': 20.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'health': 1000, 'alive': True, 'fire_seq': 1,
            'reload_time': 0.0, 'reload_duration': 1.5,
            'critical': {}, 'combat_base_revision': 0, 'combat_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
            'stun_end_server_time_ms': 0,
        }
        first = dict(publication, fire_seq=0)
        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': 1, 'sample_time_us': 100000,
            'source_batch_horizon_us': 200000,
            'bots': [first]}),
            state.last_bot_state_reject)
        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': 1, 'sample_time_us': 200000,
            'source_batch_horizon_us': 200000,
            'bots': [publication]}),
            state.last_bot_state_reject)
        self.assertEqual(
            state._server_time_ms() * 1000 - 200000,
            state.bot_launch_clock_offset_us)
        self.assertFalse(any(event.get('kind') == 'bot_shot'
                             for event in state.pending_events))

        launch = _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=1,
            origin=[20.0, 1.0, 0.0], launch_time_us=200000)
        self.assertFalse(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(launch, authority_epoch=0)))
        self.assertTrue(_launch_authority(state, launch))
        self.assertEqual('bot_shot', state.pending_events[-1]['kind'])
        self.assertEqual('1:b:16:1',
                         state.pending_events[-1]['projectile_id'])
        self.assertEqual(
            [20.0, 0.0, 0.0],
            state.projectiles['1:b:16:1']['range_origin'])
        self.assertEqual(
            [20.0, 0.0, 0.0],
            state._projectile_snapshot()[0]['range_origin'])
        self.assertTrue(_launch_authority(state, dict(launch)))
        self.assertFalse(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(launch, gravity=9.9)))

        next_publication = dict(publication, fire_seq=2)
        self.assertTrue(state.update_bot_states(
            SIMULATION_WORKER_AUTHORITY_ID, {
            'round_id': 1, 'sample_time_us': 300000,
            'source_batch_horizon_us': 300000,
            'bots': [next_publication]}),
            state.last_bot_state_reject)
        future = _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=2,
            origin=[20.0, 1.0, 0.0], launch_time_us=300000)
        self.assertTrue(_launch_authority(state, future))
        self.assertNotIn((16, 2), state.bot_pending_projectile_launches)

    def test_bot_burst_transition_yields_each_physical_projectile_edge(self):
        previous = {
            'fire_seq': 0, 'shell_index': 0,
            'burst_active': False, 'burst_group_seq': 0,
            'burst_count': 0, 'burst_next_index': 0,
            'burst_interval': 0.0, 'burst_time_left': 0.0,
            'burst_shell_index': 0,
        }
        current = dict(previous, fire_seq=3, burst_active=False,
                       burst_group_seq=1, burst_count=3,
                       burst_next_index=3)

        edges = BattleState._bot_burst_transition(previous, current)

        self.assertEqual([0, 1, 2], [
            edge['burst_index'] for edge in edges])
        self.assertEqual([3, 3, 3], [
            edge['burst_count'] for edge in edges])
        with self.assertRaises(ValueError):
            BattleState._bot_burst_transition(
                previous, dict(current, burst_group_seq=2))

    def test_idle_bot_may_normalize_completed_burst_history(self):
        previous = {
            'fire_seq': 3, 'shell_index': 0,
            'burst_active': False, 'burst_group_seq': 1,
            'burst_count': 3, 'burst_next_index': 3,
            'burst_interval': 0.1, 'burst_time_left': 0.0,
            'burst_shell_index': 0,
        }
        current = dict(
            previous, shell_index=1, burst_group_seq=3,
            burst_count=1, burst_next_index=1,
            burst_interval=0.0, burst_shell_index=1)

        self.assertEqual(
            (), BattleState._bot_burst_transition(previous, current))

        with self.assertRaisesRegex(ValueError, 'idle burst state changed'):
            BattleState._bot_burst_transition(
                previous, dict(current, burst_active=True))

    def test_server_keeps_bot_launch_order_and_trusts_worker_metadata(self):
        state = _state()
        state.bot_states[16] = {
            'id': 16, 'team': 2, 'alive': True, 'fire_seq': 3,
            'shell_index': 0, 'health': 1000, 'max_health': 1000,
            'vehicle': 'ussr:R11_MS-1',
            'x': 20.0, 'y': 0.0, 'z': 0.0,
            'burst_active': False, 'burst_group_seq': 1,
            'burst_count': 3, 'burst_next_index': 3,
            'burst_interval': 0.1, 'burst_time_left': 0.0,
            'burst_shell_index': 0,
        }
        for index in range(3):
            key = (16, index + 1)
            edge = {
                'burst_group_seq': 1, 'burst_index': index,
                'burst_count': 3, 'shell_index': 0,
                'sample_start_us': 0, 'sample_end_us': 300000,
                'launch_clock_offset_us':
                    state._server_time_ms() * 1000 - 300000,
            }
            state.bot_pending_projectile_launches.add(key)
            state.bot_pending_projectile_metadata[key] = edge
        self.assertFalse(_launch_authority(state, _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=2,
            origin=[20.0, 1.0, 0.0], burst_group_seq=1,
            burst_index=1, burst_count=3)))
        self.assertTrue(_launch_authority(state, _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=1,
            launch_time_us=300001, origin=[20.0, 1.0, 0.0],
            burst_group_seq=1, burst_index=0, burst_count=3)))
        for index in range(1, 3):
            self.assertTrue(_launch_authority(state, _launch(
                shooter_id=16, shooter_kind='bot', shot_seq=index + 1,
                launch_time_us=(100000 if index == 1 else 300000),
                origin=[20.0, 1.0, 0.0], burst_group_seq=index + 1,
                burst_index=0, burst_count=1)))
        self.assertEqual([0, 1, 2], [
            event['shot_seq'] - 1 for event in state.pending_events
            if event.get('kind') == 'bot_shot'])
        self.assertEqual(
            [state._server_time_ms(),
             state._server_time_ms() - 200,
             state._server_time_ms()],
            [state.projectiles['1:b:16:%s' % shot_seq][
                'launch_server_time_ms'] for shot_seq in (1, 2, 3)])

    def test_bot_launch_without_edge_converges_only_when_already_stale(self):
        state = _state()
        _attach_worker_authority(state)
        state.bot_states[16] = {
            'id': 16, 'team': 2, 'alive': True, 'fire_seq': 3,
            'shell_index': 0, 'health': 1000, 'max_health': 1000,
            'vehicle': 'ussr:R11_MS-1',
            'x': 20.0, 'y': 0.0, 'z': 0.0,
        }

        self.assertTrue(_launch_authority(state, _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=3,
            origin=[20.0, 1.0, 0.0], launch_time_us=300000)))
        self.assertNotIn('1:b:16:3', state.projectiles)

        self.assertFalse(_launch_authority(state, _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=4,
            origin=[20.0, 1.0, 0.0], launch_time_us=400000)))
        self.assertEqual('launch_edge_pending',
                         state.last_projectile_launch_reject_code)

        state.bot_states[16]['alive'] = False
        state.bot_states[16]['health'] = 0
        self.assertTrue(_launch_authority(state, _launch(
            shooter_id=16, shooter_kind='bot', shot_seq=4,
            origin=[20.0, 1.0, 0.0], launch_time_us=400000)))

    def test_progress_uses_batch_cas_epoch_and_exact_retry(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        projectile_id = '1:p:1:1'
        cursor = {
            'projectile_id': projectile_id, 'base_checked_ms': 0,
            'checked_through_ms': 200, 'checked_distance': 20.0,
            'piercing_loss': 3.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [cursor],
        }
        self.assertFalse(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(message, authority_epoch=0)))
        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        record = state.projectiles[projectile_id]
        self.assertEqual(200, record['checked_through_ms'])
        self.assertEqual(20.0, record['checked_distance'])
        self.assertEqual(3.0, record['piercing_loss'])
        self.assertEqual(1.0, record['penetration_factor'])
        stale = dict(
            cursor, checked_through_ms=201,
            checked_distance=19.0, piercing_loss=2.0)
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(message, cursors=[stale])))
        self.assertEqual(201, record['checked_through_ms'])
        self.assertEqual(20.0, record['checked_distance'])
        self.assertEqual(3.0, record['piercing_loss'])
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, dict(message, cursors=[dict(
                cursor, base_checked_ms=202, checked_through_ms=202)])))
        self.assertEqual(202, record['checked_through_ms'])
        self.assertEqual(20.0, record['checked_distance'])
        self.assertEqual(3.0, record['piercing_loss'])
        self.assertFalse(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, dict(message, cursors=[dict(
                cursor, base_checked_ms=202, checked_through_ms=202,
                penetration_factor=0.999999)])))

    def test_stale_progress_converges_without_poisoning_active_batch(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(_launch_authority(
            state, _launch(shooter_id=2, shot_seq=1)))
        first = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 200, 'checked_distance': 20.0,
            'piercing_loss': 3.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'projectile_progress', 'round_id': 1,
                'authority_epoch': 1, 'cursors': [first]}))
        stale_with_receipt = dict(
            first, checked_through_ms=150, checked_distance=15.0,
            piercing_loss=1.0, destructibles=[_destructible()])
        active = {
            'projectile_id': '1:p:2:1', 'base_checked_ms': 0,
            'checked_through_ms': 600, 'checked_distance': 60.0,
            'piercing_loss': 4.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }

        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'projectile_progress', 'round_id': 1,
                'authority_epoch': 1,
                'cursors': [stale_with_receipt, active]}))

        first_record = state.projectiles['1:p:1:1']
        self.assertEqual(200, first_record['checked_through_ms'])
        self.assertEqual(20.0, first_record['checked_distance'])
        self.assertEqual(3.0, first_record['piercing_loss'])
        active_record = state.projectiles['1:p:2:1']
        self.assertEqual(600, active_record['checked_through_ms'])
        self.assertEqual(60.0, active_record['checked_distance'])
        self.assertEqual(4.0, active_record['piercing_loss'])
        self.assertEqual(1, state.destructible_revision)

    def test_terminal_overtaking_exact_progress_retry_does_not_poison_batch(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(_launch_authority(
            state, _launch(shooter_id=2, shot_seq=1)))
        retired = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 100, 'checked_distance': 10.0,
            'piercing_loss': 1.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'projectile_progress', 'round_id': 1,
                'authority_epoch': 1, 'cursors': [retired]}))
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, _resolve(
                '1:p:1:1', base_checked_ms=100,
                resolved_time_ms=100, checked_distance=10.0,
                piercing_loss=1.0)))
        active = {
            'projectile_id': '1:p:2:1', 'base_checked_ms': 0,
            'checked_through_ms': 120, 'checked_distance': 12.0,
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'destructibles': [],
        }
        late = dict(
            retired, checked_through_ms=999999,
            checked_distance=999999.0, piercing_loss=-1.0,
            penetration_factor=999.0)

        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'projectile_progress', 'round_id': 1,
                'authority_epoch': 1, 'cursors': [late, active]}))

        self.assertEqual(
            120, state.projectiles['1:p:2:1']['checked_through_ms'])

    def test_progress_destructibles_are_atomic_and_idempotent(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        cursor = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 100, 'checked_distance': 10.0,
            'piercing_loss': 1.0, 'penetration_factor': 1.0,
            'destructibles': [_destructible()],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [cursor],
        }
        invalid = dict(message, cursors=[dict(
            cursor, destructibles=[_destructible(is_shot=False)])])
        before_revision = state.projectile_revision
        self.assertFalse(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, invalid))
        self.assertEqual(0,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(before_revision, state.projectile_revision)
        self.assertEqual(0, state.destructible_revision)
        self.assertFalse(state.destructibles)

        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(100,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1, len(state.destructibles))
        events = len(state.pending_events)
        self.assertTrue(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(events, len(state.pending_events))

    def test_first_ricochet_advances_segment_and_is_idempotent(self):
        state = _state()
        message = _launch()
        self.assertTrue(_launch_authority(state, message))
        record = state.projectiles['1:p:1:1']
        original_origin = list(record['origin'])
        original_velocity = list(record['velocity'])
        original_launch_time = record['launch_server_time_ms']
        request = _ricochet(
            '1:p:1:1', destructibles=[_destructible()])
        request['direct']['damage_sticker'] = 12345678901234567890
        revision = state.projectile_revision

        self.assertTrue(state.ricochet_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, request))

        self.assertIn('1:p:1:1', state.projectiles)
        self.assertEqual(original_origin, record['origin'])
        self.assertEqual(original_velocity, record['velocity'])
        self.assertEqual(original_launch_time, record['launch_server_time_ms'])
        self.assertEqual(100, record['checked_through_ms'])
        self.assertEqual(10.0, record['checked_distance'])
        self.assertEqual([10.0, 1.0, 0.0], record['segment_origin'])
        self.assertEqual([-100.0, 0.0, 0.0], record['segment_velocity'])
        self.assertEqual(100, record['segment_start_time_ms'])
        self.assertEqual(1, record['ricochet_count'])
        self.assertEqual(0.75, record['base_penetration_multiplier'])
        self.assertEqual(revision + 1, state.projectile_revision)
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1000, state.players[2].health)
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_ricochet', 'hit'],
                         [event['kind'] for event in events])
        self.assertEqual(0, events[-1]['damage'])
        self.assertEqual(0, events[-1]['shot_result'])
        self.assertEqual(
            request['direct']['damage_sticker'],
            events[-1]['damage_sticker'])
        snapshot = state._projectile_snapshot()[0]
        for key, value in snapshot.items():
            self.assertEqual(value, events[1][key])
        self.assertEqual(request['impact'], events[1]['impact'])
        self.assertEqual(request['direct'], events[1]['direct'])
        self.assertEqual(request['resolved_time_ms'],
                         events[1]['resolved_time_ms'])
        self.assertEqual(record['segment_origin'], snapshot['segment_origin'])
        self.assertEqual(record['segment_velocity'],
                         snapshot['segment_velocity'])
        self.assertEqual(100, snapshot['segment_start_time_ms'])
        self.assertEqual(1, snapshot['ricochet_count'])
        self.assertEqual(0.75,
                         snapshot['base_penetration_multiplier'])
        self.assertEqual(message['max_distance'], snapshot['max_distance'])
        self.assertEqual(message['max_time_ms'], snapshot['max_time_ms'])

        event_count = len(state.pending_events)
        self.assertTrue(state.ricochet_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, dict(request)))
        self.assertEqual(event_count, len(state.pending_events))
        self.assertEqual(revision + 1, state.projectile_revision)
        self.assertEqual(1, state.destructible_revision)
        self.assertFalse(state.ricochet_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(request, checked_distance=10.000001)))

        second = dict(
            request, base_checked_ms=100, resolved_time_ms=101,
            checked_distance=11.0, impact=[11.0, 1.0, 0.0],
            segment_origin=[11.0, 1.0, 0.0])
        self.assertFalse(state.ricochet_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, second))
        self.assertEqual(1, record['ricochet_count'])

    def test_worker_roll_becomes_the_victim_blocked_damage_ledger(self):
        for shot_result, damage, blocked in (
                (1, 0, 390), (1, 200, 190), (2, 300, 0)):
            with self.subTest(shot_result=shot_result, damage=damage):
                state = _state()
                self.assertTrue(_launch_authority(state, _launch()))
                victim = state.players[2]
                health = victim.health

                self.assertTrue(state.resolve_projectile(
                    SIMULATION_WORKER_AUTHORITY_ID,
                    _resolve('1:p:1:1', direct=_effect(
                        damage=damage, shot_result=shot_result,
                        potential_damage=390))))

                hit = [event for event in state.pending_events
                       if event.get('kind') == 'hit'][-1]
                self.assertEqual(blocked, hit['blocked_damage'])
                self.assertEqual(damage, hit['damage'])
                self.assertEqual(health - damage, victim.health)
                self.assertEqual(
                    blocked,
                    state._statistics_row('player', 2)['damage_blocked'])
                # The shooter never blocks his own shot.
                self.assertEqual(
                    0, state._statistics_row('player', 1)['damage_blocked'])
                self.assertEqual(
                    blocked, state.vehicle_interactions[
                        ('player', 2)]['player:1']['damage_blocked'])

    def test_first_ricochet_credits_one_bounce_and_no_penetration(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        victim = state.players[2]

        self.assertTrue(state.ricochet_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _ricochet('1:p:1:1', direct=_effect(
                damage=0, shot_result=0, potential_damage=420))))

        # A bounce is the archetypal blocked hit: the whole roll counts for
        # the vehicle whose armour stopped it, and the shell keeps flying.
        bounce = [event for event in state.pending_events
                  if event.get('kind') == 'hit'][-1]
        self.assertEqual(0, bounce['shot_result'])
        self.assertEqual(420, bounce['blocked_damage'])
        self.assertEqual(0, bounce['damage'])
        self.assertEqual(1000, victim.health)
        self.assertEqual(
            420, state._statistics_row('player', 2)['damage_blocked'])
        self.assertEqual(
            1, state.vehicle_interactions[
                ('player', 2)]['player:1']['ricochets_received'])

        # The continued shell that finally penetrates adds no second credit.
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', base_checked_ms=100,
                     resolved_time_ms=150, checked_distance=20.0,
                     impact=[20.0, 1.0, 0.0],
                     direct=_effect(damage=100, shot_result=2, x=20.0,
                                    potential_damage=390))))

        penetration = [event for event in state.pending_events
                       if event.get('kind') == 'hit'][-1]
        self.assertEqual(2, penetration['shot_result'])
        self.assertEqual(0, penetration['blocked_damage'])
        self.assertEqual(900, victim.health)
        self.assertEqual(
            420, state._statistics_row('player', 2)['damage_blocked'])

    def test_splash_never_credits_blocked_damage(self):
        # Player 4 shares team 2 with the direct victim, so only the splash
        # exclusion can keep its blocked-damage row at zero.
        state = _state(players=4)
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=20.0)))

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1',
                     direct=_effect(damage=0, shot_result=1,
                                    potential_damage=390),
                     splash=[_effect(
                         target_id=4, damage=0, shot_result=1,
                         target_pose=(30.0, 1.0, 0.0))])))

        self.assertEqual(
            390, state._statistics_row('player', 2)['damage_blocked'])
        self.assertEqual(2, state.players[4].team)
        self.assertEqual(
            0, state._statistics_row('player', 4)['damage_blocked'])
        splash_hit = [event for event in state.pending_events
                      if event.get('kind') == 'hit' and event.get('splash')]
        self.assertEqual([0], [
            event['blocked_damage'] for event in splash_hit])

    def test_splash_rejects_a_direct_contact_damage_roll(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=20.0)))
        record = state.projectiles['1:p:1:1']

        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=None, splash=[_effect(
                target_id=3, damage=0, shot_result=1,
                potential_damage=390,
                target_pose=(30.0, 1.0, 0.0))])))

        self.assertEqual(1000, state.players[3].health)
        self.assertEqual(
            0, state._statistics_row('player', 3)['damage_blocked'])
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_ricochet_keeps_wire_and_order_boundaries(self):
        mutations = (
            {'direct': _effect(damage=1, shot_result=0)},
            {'direct': _effect(damage=0, shot_result=1)},
            {'direct': dict(
                _effect(damage=0, shot_result=0), potential_damage=5001)},
            {'direct': dict(
                _effect(damage=0, shot_result=0), potential_damage=-1)},
            {'direct': dict(
                _effect(damage=0, shot_result=0), potential_damage=True)},
            {'direct': dict(
                _effect(damage=0, shot_result=0), potential_damage=100.0)},
            {'direct': _effect(
                damage=0, shot_result=0, damage_sticker=True)},
            {'direct': _effect(
                damage=0, shot_result=0, damage_sticker=-1)},
            {'direct': _effect(
                damage=0, shot_result=0,
                damage_sticker=(1 << 64))},
            {'direct': _effect(
                damage=0, shot_result=0, damage_sticker=1.0)},
            {'segment_velocity': [0.0, 0.0, 0.0]},
            {'segment_velocity': [3000.0, 0.0, 1.0]},
            {'penetration_factor': 0.999999},
            {'base_checked_ms': 1},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                state = _state()
                self.assertTrue(_launch_authority(state, _launch()))
                record = state.projectiles['1:p:1:1']
                revision = state.projectile_revision
                self.assertFalse(state.ricochet_projectile(
                    SIMULATION_WORKER_AUTHORITY_ID,
                    _ricochet('1:p:1:1', **changes)))
                # The malformed ricochet still commits nothing: no segment,
                # no cursor motion and no damage. The shot it named cannot
                # stay live either, so it retires as an expired terminal.
                self.assertEqual(0, record['ricochet_count'])
                self.assertEqual(0, record['checked_through_ms'])
                self.assertEqual(1000, state.players[2].health)
                self.assertEqual(revision + 1, state.projectile_revision)
                self._assert_rejected_terminal_retired(
                    state, '1:p:1:1', record)

        for changes in (
                {'base_penetration_multiplier': 0.750001},
                {'segment_origin': [10.100001, 1.0, 0.0]},
                {'segment_velocity': [-90.0, 0.0, 0.0]},
                {'resolved_time_ms': 10000},
                {'checked_distance': 1000.0}):
            with self.subTest(changes=changes):
                state = _state()
                self.assertTrue(_launch_authority(state, _launch()))
                self.assertTrue(state.ricochet_projectile(
                    SIMULATION_WORKER_AUTHORITY_ID,
                    _ricochet('1:p:1:1', **changes)))

    def test_ricochet_accepts_a_base_behind_the_canonical_cursor(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID,
            _progress('1:p:1:1', 50, 5.0)))
        record = state.projectiles['1:p:1:1']
        self.assertEqual(50, record['checked_through_ms'])

        # The worker built this ricochet from an older echo of its own
        # progress cursor. Its cursors converge by monotonic frontier, so
        # the older base is a delayed message, not a conflicting claim.
        self.assertTrue(state.ricochet_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _ricochet('1:p:1:1', base_checked_ms=0)))

        self.assertEqual(1, record['ricochet_count'])
        self.assertEqual(100, record['checked_through_ms'])
        self.assertEqual(100, record['segment_start_time_ms'])
        self.assertEqual(0.75, record['base_penetration_multiplier'])
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(
            ['shot', 'projectile_ricochet', 'hit'],
            [event['kind'] for event in state.pending_events
             if event.get('projectile_id') == '1:p:1:1'])

    def test_server_trusts_worker_ricochet_multiplier_by_shell_kind(self):
        for shell_kind, multiplier in (
                ('ARMOR_PIERCING', 0.75),
                ('ARMOR_PIERCING_CR', 0.75),
                ('HOLLOW_CHARGE', 1.0),
                ('HIGH_EXPLOSIVE', 0.75),
                ('ARMOR_PIERCING_HE', 0.75)):
            with self.subTest(shell_kind=shell_kind):
                state = _state()
                launch = _launch(
                    is_he=shell_kind == 'HIGH_EXPLOSIVE',
                    splash_radius=(1.0 if shell_kind ==
                                   'HIGH_EXPLOSIVE' else 0.0))
                launch['source_shot']['shell']['kind'] = shell_kind
                self.assertTrue(_launch_authority(state, launch))
                self.assertTrue(state.ricochet_projectile(
                    SIMULATION_WORKER_AUTHORITY_ID, _ricochet(
                        '1:p:1:1',
                        base_penetration_multiplier=multiplier)))
                self.assertEqual(1, state.projectiles[
                    '1:p:1:1']['ricochet_count'])

    def test_progress_destructible_total_batch_cap_is_sixty_four(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(_launch_authority(
            state, _launch(shooter_id=2, shot_seq=1)))
        first = {
            'projectile_id': '1:p:1:1', 'base_checked_ms': 0,
            'checked_through_ms': 1, 'checked_distance': 1.0,
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'destructibles': [_destructible(index, 1)
                              for index in range(33)],
        }
        second = {
            'projectile_id': '1:p:2:1', 'base_checked_ms': 0,
            'checked_through_ms': 1, 'checked_distance': 1.0,
            'piercing_loss': 0.0, 'penetration_factor': 1.0,
            'destructibles': [_destructible(index + 100, 1)
                              for index in range(32)],
        }
        message = {
            'type': 'projectile_progress', 'round_id': 1,
            'authority_epoch': 1, 'cursors': [first, second],
        }
        self.assertFalse(state.progress_projectiles(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(0, state.destructible_revision)
        self.assertEqual(0,
                         state.projectiles['1:p:1:1']['checked_through_ms'])
        self.assertEqual(0,
                         state.projectiles['1:p:2:1']['checked_through_ms'])

    def test_terminal_round_folds_queued_destructible_as_noop(self):
        state = _state()
        message = {
            'type': 'destructible', 'round_id': state.round_id,
        }
        message.update(_destructible())
        state.battle_result = {'winner': 2}

        self.assertTrue(state.report_destructible(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(0, state.destructible_revision)
        self.assertEqual({}, state.destructibles)

        invalid = dict(message, speed=float('nan'))
        self.assertFalse(state.report_destructible(
            SIMULATION_WORKER_AUTHORITY_ID, invalid))

    def test_terminal_round_progress_validates_envelope_before_noop(self):
        state = _state()
        state.battle_result = {'winner': 2}
        cursor = {
            'projectile_id': '1:p:1:1',
            # Terminal state makes these finite cursor values obsolete.
            'base_checked_ms': -100,
            'checked_through_ms': -50,
            'checked_distance': -20.0,
            'piercing_loss': -3.0,
            'penetration_factor': 99.0,
            'destructibles': [],
        }
        message = {
            'type': 'projectile_progress',
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'cursors': [cursor],
        }

        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        invalid_messages = (
            dict(message, round_id=state.round_id + 1),
            dict(message, authority_epoch=state.authority_epoch + 1),
            dict(message, type='not_projectile_progress'),
            dict(message, unexpected=True),
            dict((key, value) for key, value in message.items()
                 if key != 'cursors'),
            dict(message, cursors='not-a-list'),
            dict(message, cursors=[]),
            dict(message, cursors=[dict(cursor, projectile_id='')]),
            dict(message, cursors=[dict(
                (key, value) for key, value in cursor.items()
                if key != 'checked_distance')]),
            dict(message, cursors=[cursor, dict(cursor)]),
            dict(message, cursors=[dict(cursor, destructibles='not-a-list')]),
        )
        for invalid in invalid_messages:
            with self.subTest(invalid=invalid):
                self.assertFalse(state.progress_projectiles(
                    SIMULATION_WORKER_AUTHORITY_ID, invalid))

    def test_prebattle_destructible_is_not_folded_as_terminal_noop(self):
        for condition in ('loading', 'countdown'):
            with self.subTest(condition=condition):
                state = _state()
                if condition == 'loading':
                    state.phase = 'loading'
                else:
                    state.tick = int(round(
                        PREBATTLE_SECONDS * TICK_HZ)) - 1
                message = {
                    'type': 'destructible', 'round_id': state.round_id,
                }
                message.update(_destructible())

                self.assertFalse(state.report_destructible(
                    SIMULATION_WORKER_AUTHORITY_ID, message))
                self.assertEqual(0, state.destructible_revision)
                self.assertEqual({}, state.destructibles)

    def test_resolve_destructibles_validate_before_any_terminal_change(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']
        message = _resolve(
            '1:p:1:1', destructibles=[_destructible()])
        invalid = dict(message, destructibles=[_destructible(
            destructible_kind='unknown')])
        before_revision = state.projectile_revision
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, invalid))
        # The invalid destructible batch commits neither damage nor world
        # state; only the unresolvable shot itself is retired.
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(0, state.destructible_revision)
        self.assertFalse(state.destructibles)
        self.assertEqual(before_revision + 1, state.projectile_revision)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(900, state.players[2].health)
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(1, len(state.destructibles))
        events = len(state.pending_events)
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        self.assertEqual(1, state.destructible_revision)
        self.assertEqual(events, len(state.pending_events))

    def test_terminal_base_behind_the_cursor_is_a_delayed_worker_echo(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID,
            _progress('1:p:1:1', 200, 20.0)))
        self.assertEqual(
            200, state.projectiles['1:p:1:1']['checked_through_ms'])

        # Refusing the older base stranded the shot: nothing else retires a
        # worker-owned projectile, so the tracer died with no feedback.
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', base_checked_ms=0, resolved_time_ms=250,
                     checked_distance=25.0)))

        self.assertEqual(900, state.players[2].health)
        self.assertNotIn('1:p:1:1', state.projectiles)
        impact = next(
            event for event in state.pending_events
            if event.get('kind') == 'projectile_impact')
        self.assertEqual('impact', impact['outcome'])
        self.assertTrue(impact['hit_vehicle'])
        self.assertEqual(250, impact['resolved_time_ms'])
        self.assertEqual(
            'impact', state.projectile_tombstones['1:p:1:1']['outcome'])

    def test_terminal_base_ahead_of_the_cursor_retires_the_shot(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']

        # A base ahead of the canonical cursor describes progress the
        # server never accepted, so the payload stays refused.
        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', base_checked_ms=1, resolved_time_ms=1)))

        self.assertEqual(
            'cursor_compare_and_swap_failed',
            state.last_projectile_resolve_reject_code)
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(0, record['checked_through_ms'])
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_terminal_cannot_rewind_the_canonical_cursor(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        self.assertTrue(state.progress_projectiles(
            SIMULATION_WORKER_AUTHORITY_ID,
            _progress('1:p:1:1', 200, 20.0)))
        record = state.projectiles['1:p:1:1']

        # Tolerating an older base must not lower the resolution floor: the
        # canonical cursor, not the echoed base, bounds the resolved time.
        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', base_checked_ms=0, resolved_time_ms=100,
                     checked_distance=25.0)))

        self.assertEqual(
            'integer_below_lower_bound',
            state.last_projectile_resolve_reject_code)
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(200, record['checked_through_ms'])
        self.assertEqual(20.0, record['checked_distance'])
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_player_disconnect_keeps_bot_projectile_under_worker_epoch(self):
        state = _state()
        _attach_worker_authority(state)
        state.bot_states[16] = {
            'id': 16, 'team': 2, 'alive': True, 'fire_seq': 1,
            'shell_index': 0, 'health': 1000, 'max_health': 1000,
            'vehicle': 'ussr:R11_MS-1',
            'x': 20.0, 'y': 0.0, 'z': 0.0,
        }
        state.bot_pending_projectile_launches.add((16, 1))
        state.bot_pending_projectile_metadata[(16, 1)] = {
            'burst_group_seq': 1, 'burst_index': 0,
            'burst_count': 1, 'shell_index': 0,
            'sample_start_us': 0, 'sample_end_us': 100000,
            'launch_clock_offset_us':
                state._server_time_ms() * 1000 - 100000,
        }
        self.assertTrue(state.launch_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _launch(shooter_id=16, shooter_kind='bot', shot_seq=1)))

        state.remove_player(1)
        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, state.bot_authority_id)
        self.assertEqual(1, state.authority_epoch)
        self.assertIn('1:b:16:1', state.projectiles)
        snapshot = state._projectile_snapshot()
        self.assertEqual(1, snapshot[0]['authority_epoch'])
        self.assertIn('launch_server_time_ms', snapshot[0])
        self.assertIn('checked_distance', snapshot[0])
        self.assertIn('piercing_loss', snapshot[0])

        miss = _resolve(
            '1:b:16:1', epoch=1, outcome='miss', impact=None,
            direct=None, splash=[], checked_distance=5.0)
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, miss))

    def test_resolve_is_atomic_idempotent_and_preserves_hit_contract(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        sticker = (1 << 64) - 1
        message = _resolve(
            '1:p:1:1', direct=_effect(damage_sticker=sticker))

        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(900, state.players[2].health)
        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual('impact',
                         state.projectile_tombstones['1:p:1:1']['outcome'])
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact', 'hit'],
                         [event['kind'] for event in events])
        self.assertTrue(events[1]['hit_vehicle'])
        self.assertEqual('shot', events[-1]['source'])
        self.assertEqual(sticker, events[-1]['damage_sticker'])
        outgoing = state.vehicle_interactions[
            ('player', 1)]['player:2']
        incoming = state.vehicle_interactions[
            ('player', 2)]['player:1']
        self.assertEqual(1, outgoing['direct_hits'])
        self.assertEqual(1, outgoing['piercings'])
        self.assertEqual(100, outgoing['damage'])
        self.assertEqual(100, incoming['damage_received'])

        event_count = len(state.pending_events)
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, dict(message)))
        self.assertEqual(900, state.players[2].health)
        self.assertEqual(event_count, len(state.pending_events))
        self.assertEqual(100, outgoing['damage'])
        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            dict(message, checked_distance=11.0)))

        for invalid in (True, 1.0, -1, 1 << 64):
            with self.subTest(damage_sticker=invalid):
                other = _state()
                self.assertTrue(_launch_authority(other, _launch()))
                self.assertFalse(other.resolve_projectile(
                    SIMULATION_WORKER_AUTHORITY_ID,
                    _resolve('1:p:1:1', direct=_effect(
                        damage_sticker=invalid))))

        splash_state = _state()
        self.assertTrue(_launch_authority(splash_state, _launch()))
        self.assertFalse(splash_state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=None, splash=[_effect(
                target_kind='bot', target_id=16,
                target_pose=(10.0, 1.0, 0.0), damage_sticker=1)])))

    def test_internal_projectile_stun_is_durable_expires_and_assists(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch()))
        now = state._server_time_ms()
        stun_end = now + 1500
        message = _resolve(
            '1:p:1:1', direct=_effect(
                stun_end_server_time_ms=stun_end))

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, message))

        target = state.players[2]
        self.assertEqual(stun_end, target.stun_end_server_time_ms)
        self.assertEqual(('player', 1), (
            target.stun_attacker_kind, target.stun_attacker_id))
        public = state._public_player(target)
        self.assertEqual(stun_end, public['stun_end_server_time_ms'])
        self.assertEqual('player', public['stun_attacker_kind'])
        stun_events = [event for event in state.pending_events
                       if event.get('kind') == 'stun']
        self.assertEqual([True], [event['active'] for event in stun_events])

        state._record_damage(
            ('player', 3), ('player', 2), 240, target.critical)
        self.assertEqual(
            240, state._statistics_row('player', 1)[
                'damage_assisted_stun'])
        self.assertEqual(
            240, state.vehicle_interactions[
                ('player', 1)]['player:2']['assist_stun'])
        assist = [event for event in state.pending_events
                  if event.get('kind') == 'assist'][-1]
        self.assertEqual('stun', assist['category'])

        state.tick += int(round(1.5 * TICK_HZ))
        self.assertEqual(1, state._expire_stuns())
        self.assertEqual(0, target.stun_end_server_time_ms)
        self.assertEqual('', target.stun_attacker_kind)
        self.assertEqual(0, target.stun_attacker_id)
        self.assertFalse([
            event for event in state.pending_events
            if event.get('kind') == 'stun'][-1]['active'])

    def test_stun_batch_uses_one_frozen_resolution_clock(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=20.0)))
        message = _resolve(
            '1:p:1:1',
            direct=_effect(stun_end_server_time_ms=101),
            splash=[_effect(
                target_id=3, damage=50, x=20.0,
                target_pose=(20.0, 1.0, 0.0),
                stun_end_server_time_ms=101)])

        with mock.patch.object(
                state, '_server_time_ms', side_effect=[100]) as clock:
            self.assertTrue(state.resolve_projectile(
                SIMULATION_WORKER_AUTHORITY_ID, message))

        self.assertEqual(1, clock.call_count)
        self.assertEqual(101, state.players[2].stun_end_server_time_ms)
        self.assertEqual(101, state.players[3].stun_end_server_time_ms)

    def test_visible_projectile_authority_cannot_supply_stun_state(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']
        state.bot_authority_id = 1
        message = _resolve(
            '1:p:1:1', direct=_effect(
                stun_end_server_time_ms=state._server_time_ms() + 1000))

        self.assertFalse(state.resolve_projectile(1, message))
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(0, state.players[2].stun_end_server_time_ms)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_resolve_cannot_lower_the_launch_penetration_roll(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']

        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', penetration_factor=0.75)))

        self.assertEqual(1.0, record['penetration_factor'])
        self.assertEqual(1000, state.players[2].health)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_wreck_terminal_can_have_no_damage_but_still_hit_a_vehicle(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        message = _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True)

        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))

        event = next(
            value for value in state.pending_events
            if value.get('kind') == 'projectile_impact')
        self.assertTrue(event['hit_vehicle'])
        self.assertEqual(1000, state.players[2].health)

    def test_wreck_impact_relays_identity_without_combat_statistics(self):
        state = _state()
        state.players[2].health = 0
        state.players[2].alive = False
        self.assertTrue(_launch_authority(state, _launch()))
        message = _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit={'target_kind': 'player', 'target_id': 2})

        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))

        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact'],
                         [event['kind'] for event in events])
        self.assertEqual(
            {'target_kind': 'player', 'target_id': 2},
            events[-1]['wreck_hit'])
        self.assertEqual({}, state.vehicle_interactions)

    def test_wreck_impact_contract_rejects_live_or_damage_targets(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        wreck_hit = {'target_kind': 'player', 'target_id': 2}

        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit=wreck_hit)))
        state.players[2].health = 0
        state.players[2].alive = False
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', hit_vehicle=True, wreck_hit=wreck_hit)))
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True, wreck_hit=None)))
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit={'target_kind': 'player', 'target_id': 99})))
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, _resolve(
            '1:p:1:1', direct=None, hit_vehicle=True,
            wreck_hit=wreck_hit)))

    def test_hit_event_reports_only_damage_the_target_had_left(self):
        state = _state()
        state.players[2].health = 200
        self.assertTrue(_launch_authority(state, _launch()))

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=_effect(damage=400))))

        event = next(
            value for value in state.pending_events
            if value.get('kind') == 'hit')
        self.assertEqual(200, event['damage'])
        self.assertEqual(0, event['health'])
        interaction = state.vehicle_interactions[
            ('player', 1)]['player:2']
        self.assertEqual(200, interaction['damage'])
        self.assertEqual(1, interaction['target_kills'])
        self.assertEqual(0, interaction['death_reason'])

    def test_he_direct_target_cannot_repeat_in_splash(self):
        state = _state(players=3)
        launch = _launch(
            is_he=True, splash_radius=15.0,
            penetration_factor=0.0)
        self.assertTrue(_launch_authority(state, launch))
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(
                target_id=2, damage=40, x=10.0,
                target_pose=(10.0, 1.0, 0.0))])
        record = state.projectiles['1:p:1:1']
        before = [state.players[index].health for index in (2, 3)]
        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(before,
                         [state.players[index].health for index in (2, 3)])
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=15.0, penetration_factor=0.0)))
        message['splash'] = [_effect(
            target_id=3, damage=40, x=10.0,
            target_pose=(20.0, 1.0, 0.0))]
        self.assertTrue(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual([950, 960],
                         [state.players[index].health for index in (2, 3)])

    def test_invalid_nth_effect_is_atomic_even_when_targets_are_distinct(self):
        state = _state(players=3)
        state.players[3].x = 12.0
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=15.0, penetration_factor=0.0)))
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(
                        target_id=3, damage=40, x=10.0,
                        target_pose=(12.0, 1.0, 0.0)),
                    _effect(
                        target_id=999, damage=30, x=10.0,
                        target_pose=(12.0, 1.0, 0.0))])
        record = state.projectiles['1:p:1:1']

        self.assertFalse(state.resolve_projectile(SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(1000, state.players[2].health)
        self.assertEqual(1000, state.players[3].health)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_splash_uses_the_workers_collision_time_target_pose(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=15.0, penetration_factor=0.0)))
        state.players[3].x = 100.0
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(
                target_id=3, damage=40, x=10.0,
                target_pose=(20.0, 1.0, 0.0))])

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual([950, 960],
                         [state.players[index].health for index in (2, 3)])

    def test_server_trusts_worker_splash_collision_pose(self):
        state = _state(players=3)
        self.assertTrue(_launch_authority(state, _launch(
            is_he=True, splash_radius=15.0, penetration_factor=0.0)))
        message = _resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=_effect(target_id=2, damage=50),
            splash=[_effect(
                target_id=3, damage=40, x=10.0,
                target_pose=(30.1, 1.0, 0.0))])

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual([950, 960],
                         [state.players[index].health for index in (2, 3)])
        self.assertNotIn('1:p:1:1', state.projectiles)

    def test_direct_effect_rejects_a_splash_target_pose(self):
        state = _state(players=2)
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']
        message = _resolve(
            '1:p:1:1', direct=_effect(
                target_id=2, target_pose=(10.0, 1.0, 0.0)))

        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID, message))
        self.assertEqual(1000, state.players[2].health)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_expiration_result_and_reset_lifecycle(self):
        state = _state()
        self.assertTrue(_launch_authority(
            state, _launch(max_time_ms=100)))
        state.tick += 3

        self.assertEqual(0, state._expire_projectiles())
        self.assertIn('1:p:1:1', state.projectiles)

        state.simulation_worker.connected = False
        state.simulation_worker = None
        state.bot_authority_id = None
        self.assertEqual(1, state._expire_projectiles())
        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual('expired',
                         state.projectile_tombstones['1:p:1:1']['outcome'])

        _attach_worker_authority(state)
        self.assertTrue(_launch_authority(state, _launch(
            shooter_id=2, shot_seq=1, max_time_ms=10000)))
        self.assertTrue(state._finish_battle(1, 'test'))
        self.assertFalse(state.projectiles)
        self.assertEqual('battle_finished',
                         state.projectile_tombstones['1:p:2:1']['outcome'])
        state._reset_round()
        self.assertFalse(state.projectiles)
        self.assertFalse(state.projectile_tombstones)
        self.assertEqual(0, state.projectile_revision)

    def test_player_disconnect_and_leave_do_not_cancel_fired_projectile(self):
        disconnected = _state()
        self.assertTrue(_launch_authority(disconnected, _launch(
            shooter_id=2, shot_seq=1)))
        disconnected.remove_player(2)
        self.assertIn('1:p:2:1', disconnected.projectiles)
        self.assertTrue(disconnected.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:2:1', direct=None, outcome='miss',
                        impact=None, checked_distance=1.0)))

        left = _state()
        self.assertTrue(_launch_authority(left, _launch(
            shooter_id=2, shot_seq=1)))
        self.assertTrue(left.leave_battle(2, {
            'round_id': left.round_id}))
        self.assertIn('1:p:2:1', left.projectiles)
        self.assertTrue(left.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:2:1', direct=None, outcome='miss',
                        impact=None, checked_distance=1.0)))

    def test_disconnected_shooter_projectile_still_applies_damage(self):
        state = _state()
        _attach_worker_authority(state)
        self.assertTrue(_launch_authority(state, _launch()))

        state.remove_player(1)

        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, state.bot_authority_id)
        self.assertEqual(1, state.authority_epoch)
        self.assertIn('1:p:1:1', state.projectiles)
        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', epoch=1)))
        self.assertEqual(900, state.players[2].health)
        events = [event for event in state.pending_events
                  if event.get('projectile_id') == '1:p:1:1']
        self.assertEqual(['shot', 'projectile_impact', 'hit'],
                         [event['kind'] for event in events])

    def test_terminal_noops_a_frozen_target_who_disconnected(self):
        state = _state()
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))
        state.remove_player(2)
        before_events = len(state.pending_events)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=_effect(target_id=2))))

        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual(
            1000, state._frozen_player_participant(2)['health'])
        self.assertFalse(any(
            event.get('projectile_id') == '1:p:1:1' and
            event.get('kind') == 'hit'
            for event in state.pending_events[before_events:]))
        self.assertEqual({}, state.vehicle_interactions)

    def test_terminal_still_rejects_an_unknown_missing_target(self):
        state = _state()
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))
        record = state.projectiles['1:p:1:1']

        self.assertFalse(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=_effect(target_id=99))))

        # An unknown target is still refused, but the shot that named it
        # retires instead of leaking into the ledger forever.
        self.assertEqual(1000, state.players[2].health)
        self._assert_rejected_terminal_retired(state, '1:p:1:1', record)

    def test_terminal_noops_a_frozen_wreck_who_disconnected(self):
        state = _state()
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state.players[2].health = 0
        state.players[2].alive = False
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))
        state.remove_player(2)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve(
                '1:p:1:1', direct=None, hit_vehicle=True,
                wreck_hit={'target_kind': 'player', 'target_id': 2})))

        self.assertNotIn('1:p:1:1', state.projectiles)
        impact = next(
            event for event in state.pending_events
            if event.get('kind') == 'projectile_impact')
        self.assertNotIn('wreck_hit', impact)

    def test_disconnected_shooter_projectile_keeps_stun_attribution(self):
        state = _state()
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))
        stun_end = state._server_time_ms() + 1500

        state.remove_player(1)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve(
                '1:p:1:1', direct=_effect(
                    stun_end_server_time_ms=stun_end))))
        target = state.players[2]
        self.assertEqual(stun_end, target.stun_end_server_time_ms)
        self.assertEqual(
            ('player', 1),
            (target.stun_attacker_kind, target.stun_attacker_id))

    def test_disconnected_shooter_enemy_frag_uses_frozen_launch_identity(self):
        state = _state()
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))

        state.remove_player(1)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve('1:p:1:1', direct=_effect(damage=1000))))
        attacker = state.vehicle_statistics[('player', 1)]
        target = state.vehicle_statistics[('player', 2)]
        self.assertEqual(1, attacker['team'])
        self.assertEqual(1000, attacker['damage_dealt'])
        self.assertEqual(1, attacker['kills'])
        self.assertEqual(1000, target['damage_received'])
        self.assertEqual(1, state.round_participants['player-1']['frags'])
        self.assertFalse(
            state.round_participants['player-1']['team_killer'])
        statistics = [event for event in state.pending_events
                      if event.get('kind') == 'vehicle_statistics']
        self.assertEqual(1, statistics[-1]['frags'])

    def test_disconnected_shooter_friendly_frag_keeps_enemy_stats_zero(self):
        state = _state(players=3)
        for player in state.players.values():
            player.account_key = 'player-%d' % player.player_id
        state._freeze_round_participants(list(state.players.values()))
        self.assertTrue(_launch_authority(state, _launch()))

        state.remove_player(1)

        self.assertTrue(state.resolve_projectile(
            SIMULATION_WORKER_AUTHORITY_ID,
            _resolve(
                '1:p:1:1', impact=[20.0, 1.0, 0.0],
                checked_distance=20.0,
                direct=_effect(target_id=3, damage=1000, x=20.0))))
        attacker = state.vehicle_statistics[('player', 1)]
        target = state.vehicle_statistics[('player', 3)]
        self.assertEqual(1, attacker['team'])
        self.assertEqual(0, attacker['damage_dealt'])
        self.assertEqual(0, attacker['kills'])
        self.assertEqual(1000, target['damage_received'])
        self.assertEqual(-1, state.round_participants['player-1']['frags'])
        self.assertTrue(state.round_participants['player-1']['team_killer'])
        statistics = [event for event in state.pending_events
                      if event.get('kind') == 'vehicle_statistics']
        self.assertEqual(-1, statistics[-1]['frags'])
        self.assertTrue(statistics[-1]['team_killer'])

    def test_retired_combat_report_handlers_are_removed(self):
        retired_types = (
            'hit_report', 'bot_hit_report', 'bot_human_hit',
            'spotted_report')
        for message_type in retired_types:
            self.assertNotIn(message_type, MODERN_VISIBLE_MESSAGE_TYPES)
            self.assertNotIn(
                message_type, SIMULATION_WORKER_ADVANCEMENT_TYPES)
        for method_name in (
                'report_hit', 'report_bot_hit', 'report_bot_human_hit',
                'update_spotted_targets', '_apply_reported_health'):
            self.assertFalse(hasattr(BattleState, method_name))

    def test_capability_and_active_snapshot_wire_bound(self):
        self.assertEqual('projectile_ledger_v2', PROJECTILE_CAPABILITY)
        self.assertEqual('ricochet_continuation_v1',
                         RICOCHET_CONTINUATION_CAPABILITY)
        self.assertIn(RICOCHET_CONTINUATION_CAPABILITY, SERVER_CAPABILITIES)
        modern = BattleState(map_name='04_himmelsdorf')
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P'})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [
                'projectile_ledger_v1',
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY]})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [PROJECTILE_CAPABILITY]})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY],
            'max_health': 1000,
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': effective_params()})
        self.assertIsNone(player)
        self.assertEqual('unsupported_capabilities', error)
        player, error = modern.add_player(_Socket(), ('127.0.0.1', 1), {
            'client_build': CLIENT_BUILD_0922, 'name': 'P',
            'capabilities': [
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY,
                RICOCHET_CONTINUATION_CAPABILITY],
            'max_health': 1000,
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': effective_params()})
        self.assertIsNotNone(player)
        self.assertIsNone(error)

        wrong_build = BattleState(map_name='04_himmelsdorf')
        player, error = wrong_build.add_player(
            _Socket(), ('127.0.0.1', 1), {
                'client_build': 'wot-0.9.22.0.1-cn-1514', 'name': 'P'})
        self.assertIsNone(player)
        self.assertEqual('unsupported_client_build', error)

        shooter_count = PROJECTILE_MAX_ACTIVE // 32
        state = _state(players=shooter_count)
        for shooter_id in range(1, shooter_count + 1):
            for shot_seq in range(1, 33):
                self.assertTrue(_launch_authority(
                    state, _launch(
                        shooter_id=shooter_id, shot_seq=shot_seq,
                        origin=[state.players[shooter_id].x, 1.0, 0.0])))
        self.assertEqual(PROJECTILE_MAX_ACTIVE, len(state.projectiles))
        snapshot = {
            'type': 'snapshot', 'protocol': 5, 'server_tick': state.tick,
            'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'server_time_ms': state._server_time_ms(),
            'projectile_revision': state.projectile_revision,
            'projectiles': state._projectile_snapshot(),
        }
        payload = (json.dumps(snapshot, separators=(',', ':')) + '\n').encode()
        self.assertLessEqual(len(payload), MAX_LINE_BYTES)

    def test_current_battle_message_includes_worker_projectile_ledger(self):
        state = _state(players=1)
        state.phase = 'battle'
        self.assertTrue(_launch_authority(state, _launch()))

        message = state.current_battle_message()

        self.assertEqual(state.authority_epoch, message['authority_epoch'])
        self.assertEqual(state.projectile_revision,
                         message['projectile_revision'])
        self.assertEqual(state._projectile_snapshot(), message['projectiles'])
        self.assertEqual('connected', message['worker_status'])
        self.assertNotIn('authority_status', message)
        self.assertNotIn('authority_fallback_reason', message)

    def test_launch_event_pitch_uses_physical_positive_up_convention(self):
        state = _state(players=1)

        self.assertTrue(_launch_authority(
            state, _launch(
                velocity=[0.0, 100.0, 425.0], gravity=143.0)))
        event = state.pending_events[-1]
        self.assertGreater(event['shot_pitch'], 0.0)
        self.assertAlmostEqual(
            math.atan2(100.0, 425.0), event['shot_pitch'], places=6)

    def test_modern_events_and_snapshot_share_current_tick_time_and_epoch(self):
        state = _state(players=1)
        self.assertTrue(_launch_authority(state, _launch()))
        broadcasts = []
        snapshots = []

        def offer_reliable(message):
            target = (snapshots if message.get('type') == 'snapshot'
                      else broadcasts)
            target.append(message)
            return True

        state.players[1].offer_reliable = offer_reliable
        state.players[1].offer_snapshot = (
            lambda message: snapshots.append(message) or True)
        state._server_time_ms = lambda: 40017

        state.tick_once(1.0 / TICK_HZ)

        events = [message for message in broadcasts
                  if message.get('type') == 'events']
        self.assertEqual(1, len(events))
        self.assertEqual(40017, events[0]['server_time_ms'])
        self.assertEqual(state.authority_epoch,
                         events[0]['authority_epoch'])
        self.assertEqual(40017, snapshots[-1]['server_time_ms'])
        self.assertEqual(events[0]['server_time_ms'],
                         snapshots[-1]['server_time_ms'])
        self.assertEqual(events[0]['authority_epoch'],
                         snapshots[-1]['authority_epoch'])

    def test_event_extraction_and_leave_are_one_ordered_state_transaction(self):
        state = _state()
        self.assertTrue(_launch_authority(state, _launch()))
        entered_delivery = threading.Event()
        release_delivery = threading.Event()
        mutation_started = threading.Event()
        mutation_done = threading.Event()
        delivered = []

        def offer_reliable(message):
            if message.get('type') == 'events':
                delivered.append(message)
                if not entered_delivery.is_set():
                    entered_delivery.set()
                    self.assertTrue(release_delivery.wait(2.0))
            return True

        state.players[1].offer_reliable = offer_reliable
        state.players[1].offer_snapshot = lambda unused_message: True
        tick_thread = threading.Thread(
            target=state.tick_once, args=(1.0 / TICK_HZ,))
        tick_thread.start()
        self.assertTrue(entered_delivery.wait(2.0))

        def leave_player():
            mutation_started.set()
            state.leave_battle(2, {'round_id': state.round_id})
            mutation_done.set()

        leave_thread = threading.Thread(target=leave_player)
        leave_thread.start()
        self.assertTrue(mutation_started.wait(2.0))
        self.assertFalse(mutation_done.wait(0.05))
        release_delivery.set()
        tick_thread.join(2.0)
        leave_thread.join(2.0)
        self.assertFalse(tick_thread.is_alive())
        self.assertFalse(leave_thread.is_alive())
        self.assertTrue(mutation_done.is_set())

        state.tick_once(1.0 / TICK_HZ)

        events = [event for message in delivered
                  for event in message['events']]
        event_ids = [event['event_id'] for event in events]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertEqual(1, sum(event.get('kind') == 'shot'
                                for event in events))
        self.assertEqual(1, sum(event.get('source') == 'player_left'
                                for event in events))

if __name__ == '__main__':
    unittest.main()
