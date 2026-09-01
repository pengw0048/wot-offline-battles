from __future__ import print_function

"""Dedicated #1513 client lifecycle for native bot simulation authority.

The worker is not a player.  It keeps one private, off-map Avatar only because
the exact client exposes terrain, model nodes and hit testers through a loaded
native battle space.  That synthetic identity is projected into this process'
BattleRuntime messages and is never sent to the LAN server.
"""

import math
import os
import sys
import time

from gui.mods.offline_lan_0922 import burst_mechanics
from gui.mods.offline_lan_0922 import siege_mechanics
from gui.mods.offline_lan_0922 import config as port_config
from gui.mods.offline_lan_0922.lan_client import (
    CLIENT_BUILD, CLIENT_CAPABILITIES, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY, MAX_MOTION_TIME_US,
    MAX_PROJECTILE_ID, PROTOCOL_VERSION, PROJECTILE_LEDGER_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    PLAYER_ENVIRONMENT_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY, RICOCHET_CONTINUATION_CAPABILITY,
    SIMULATION_WORKER_CAPABILITY,
    WORKER_AUTHORITY_ID, LANClient,
    _BOT_STATE_WIRE_FIELDS,
    _canonical_effective_params, _canonical_vehicle_compact_descr,
    _canonical_wire_outfits,
    _exact_int, _project_human_ram_armors, _projectile_int_range, _safe_text,
    _strict_capabilities, _strict_mapping_list)


WORKER_ROLE = 'simulation_worker'
WORKER_STATUS_PATH = os.path.join(
    os.path.dirname(port_config.CONFIG_PATH), 'authority_worker_status.json')
WORKER_MONITOR_SECONDS = 0.10
WORKER_RETRY_SECONDS = 1.0
WORKER_BUSY_RETRY_SECONDS = 5.0
WORKER_RETRY_MAX_SECONDS = 15.0
WORKER_PROGRESS_SECONDS = 1.0
WORKER_STATUS_SECONDS = 2.0
WORKER_DUMMY_Y = -500.0
_PROJECTED_BOT_STATE_FIELDS = frozenset(
    _BOT_STATE_WIRE_FIELDS + ('shot_yaw', 'shot_pitch'))
_COALESCIBLE_BOT_STATE_FIELDS = frozenset((
    'x', 'y', 'z', 'yaw', 'pitch', 'roll', 'aim_yaw', 'gun_pitch',
    'speed', 'movement_dir', 'rotation_dir', 'reload_time',
    'burst_time_left', 'siege_time_left_ms',
    'combat_fire_elapsed', 'combat_fire_timer'))
_COALESCIBLE_EQUIPMENT_FIELDS = frozenset((
    'cooldownTimeLeft', 'autoPendingElapsed', 'aiPendingElapsed'))
_VISIBILITY_DIAGNOSTIC_COUNTERS = (
    'visibility_admitted', 'visibility_completed', 'visibility_deferred',
    'visibility_selected_services', 'visibility_fire_services',
    'visibility_new_services', 'visibility_ordinary_services')
_SHOT_LANE_DIAGNOSTIC_COUNTERS = (
    'shot_lane_completed_pairs',
    'shot_lane_budget_deferred_attempts')


def _windows_process_counters():
    """Read cheap process counters on the exact Windows worker, fail-soft."""
    if not sys.platform.startswith('win'):
        return None
    try:
        import ctypes

        class _FileTime(ctypes.Structure):
            _fields_ = [
                ('low', ctypes.c_ulong),
                ('high', ctypes.c_ulong),
            ]

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ('cb', ctypes.c_ulong),
                ('page_fault_count', ctypes.c_ulong),
                ('peak_working_set_size', ctypes.c_size_t),
                ('working_set_size', ctypes.c_size_t),
                ('quota_peak_paged_pool_usage', ctypes.c_size_t),
                ('quota_paged_pool_usage', ctypes.c_size_t),
                ('quota_peak_non_paged_pool_usage', ctypes.c_size_t),
                ('quota_non_paged_pool_usage', ctypes.c_size_t),
                ('pagefile_usage', ctypes.c_size_t),
                ('peak_pagefile_usage', ctypes.c_size_t),
            ]

        kernel = ctypes.windll.kernel32
        process = kernel.GetCurrentProcess()
        creation = _FileTime()
        exit_time = _FileTime()
        kernel_time = _FileTime()
        user_time = _FileTime()
        if not kernel.GetProcessTimes(
                process, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        memory = _ProcessMemoryCounters()
        memory.cb = ctypes.sizeof(memory)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
                process, ctypes.byref(memory), memory.cb):
            return None

        def filetime_seconds(value):
            ticks = (int(value.high) << 32) | int(value.low)
            return ticks / 10000000.0

        processors = None
        try:
            processors = max(
                1, int(os.environ.get('NUMBER_OF_PROCESSORS')))
        except (TypeError, ValueError, OverflowError):
            processors = None
        return {
            'cpu_seconds': (
                filetime_seconds(kernel_time) +
                filetime_seconds(user_time)),
            'working_set_bytes': int(memory.working_set_size),
            'logical_processors': processors,
        }
    except Exception:
        # Performance evidence can be absent; it cannot alter worker life.
        return None


def _immutable_outbound_key(value):
    """Freeze one already validated wire fragment for equality only."""
    if isinstance(value, dict):
        return tuple(sorted(
            (key, _immutable_outbound_key(item))
            for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_outbound_key(item) for item in value)
    return value


def _equipment_edge_key(snapshots):
    if not isinstance(snapshots, (list, tuple)):
        return _immutable_outbound_key(snapshots)
    return tuple(_immutable_outbound_key(dict(
        (name, value) for name, value in snapshot.items()
        if name not in _COALESCIBLE_EQUIPMENT_FIELDS))
        if isinstance(snapshot, dict) else _immutable_outbound_key(snapshot)
        for snapshot in snapshots)


def _bot_state_coalesce_key(message):
    """Identify checkpoints that differ only in supersedable continuous data."""
    bots = message.get('bots') or ()
    bot_keys = []
    for state in bots:
        fields = []
        for name, value in state.items():
            if name in _COALESCIBLE_BOT_STATE_FIELDS:
                continue
            if name == 'equipment_states':
                value = _equipment_edge_key(value)
            else:
                value = _immutable_outbound_key(value)
            fields.append((name, value))
        bot_keys.append(tuple(sorted(fields)))
    return (
        int(message.get('round_id') or 0),
        _immutable_outbound_key(message.get('human_ram_armors')),
        tuple(bot_keys))


def _trusted_projected_bot_states(bots):
    """Verify the shallow shape produced by BotRuntime's wire projector."""
    if not isinstance(bots, (list, tuple)) or len(bots) > 30:
        return False
    ammo_fields = frozenset((
        'shell_index', 'next_shell_index', 'ammo_remaining',
        'ammo_reload_pending'))
    for state in bots:
        if not isinstance(state, dict):
            return False
        fields = set(state)
        if not fields.issubset(_PROJECTED_BOT_STATE_FIELDS):
            return False
        if (('shot_yaw' in fields) != ('shot_pitch' in fields)):
            return False
        present_ammo = fields.intersection(ammo_fields)
        if present_ammo and present_ammo != ammo_fields:
            return False
        if (present_ammo and
                not isinstance(state.get('ammo_reload_pending'), bool)):
            return False
        siege_fields = frozenset((
            'siege_state', 'siege_time_left_ms',
            'siege_transition_total_ms'))
        present_siege = fields.intersection(siege_fields)
        if present_siege and present_siege != siege_fields:
            return False
        if (present_siege and
                not siege_mechanics.valid_wire_state(
                    state.get('siege_state'),
                    state.get('siege_time_left_ms'),
                    transition_total_ms=state.get(
                        'siege_transition_total_ms'))):
            return False
        try:
            burst_mechanics.BurstClock().restore(
                state, state.get('fire_seq', 0))
        except ValueError:
            return False
        try:
            reload_time = float(state['reload_time'])
            reload_duration = float(state['reload_duration'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if (isinstance(state.get('reload_time'), bool) or
                isinstance(state.get('reload_duration'), bool) or
                math.isnan(reload_time) or math.isinf(reload_time) or
                math.isnan(reload_duration) or
                math.isinf(reload_duration) or
                reload_duration <= 0.0 or reload_time < 0.0 or
                reload_time > reload_duration):
            return False
    return True


def _authority_id(value):
    parsed = _exact_int(value)
    if parsed in (None, WORKER_AUTHORITY_ID):
        return parsed
    return _projectile_int_range(parsed, 1, MAX_PROJECTILE_ID)


def _load_battle_runtime():
    from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
    return BattleRuntime


class AuthorityWorkerLANClient(LANClient):
    """LAN v5 transport whose identity never enters ``players``."""

    def __init__(self, host, port, on_event=None, bigworld=None):
        LANClient.__init__(
            self, host, port, 'SimulationWorker', 'ussr:R11_MS-1',
            max_health=1, on_event=on_event, bigworld=bigworld,
            account_key='simulation-worker', outfits={})
        self.player_id = WORKER_AUTHORITY_ID
        self.worker_id = WORKER_AUTHORITY_ID
        self.role = WORKER_ROLE
        self.team = 1
        self.slot = 0
        self.spawn = {
            'x': 0.0, 'y': WORKER_DUMMY_Y, 'z': 0.0, 'yaw': 0.0}
        self._worker_avatar = None

    def is_bot_authority(self):
        """Only the dedicated worker identity may own bot simulation."""
        return bool(
            self.ready and self.phase in ('loading', 'battle') and
            self.player_id == WORKER_AUTHORITY_ID and
            self.bot_authority_id == WORKER_AUTHORITY_ID)

    def _runtime_recovery_enabled(self):
        """Keep the sole simulation authority strict on state corruption."""
        return False

    def _outbound_discrete_headroom_enabled(self, message):
        """Reserve bounded backlog space for worker combat/protocol edges."""
        kind = message.get('type') if isinstance(message, dict) else None
        return kind not in (
            'bot_state', 'bot_observation', 'projectile_progress',
            'simulation_progress', 'player_environment')

    def _hello_payload(self):
        """Advertise only a worker role; no dummy player data crosses wire."""
        return {
            'type': 'hello',
            'protocol': PROTOCOL_VERSION,
            'client_build': CLIENT_BUILD,
            'capabilities': list(CLIENT_CAPABILITIES) + [
                SIMULATION_WORKER_CAPABILITY],
            'role': WORKER_ROLE,
        }

    def send_projected_bot_state(self, bots, sample_time_us=None,
                                 source_batch_horizon_us=None,
                                 human_ram_armors=None):
        """Queue BotRuntime's canonical publication as one frozen wire blob."""
        if (not self.is_bot_authority() or
                not _trusted_projected_bot_states(bots)):
            return False
        message = {
            'type': 'bot_state', 'round_id': self.round_id, 'bots': bots}
        if sample_time_us is not None:
            sample_time_us = _exact_int(sample_time_us)
            if (sample_time_us is None or
                    not 0 <= sample_time_us <= MAX_MOTION_TIME_US):
                return False
            message['sample_time_us'] = sample_time_us
            source_batch_horizon_us = _exact_int(
                source_batch_horizon_us)
            if (source_batch_horizon_us is None or
                    not sample_time_us <= source_batch_horizon_us <=
                    MAX_MOTION_TIME_US):
                return False
            message['source_batch_horizon_us'] = source_batch_horizon_us
        elif source_batch_horizon_us is not None:
            return False
        if human_ram_armors is not None:
            human_ram_armors = _project_human_ram_armors(human_ram_armors)
            if human_ram_armors is None:
                return False
            message['human_ram_armors'] = human_ram_armors
        try:
            coalesce_key = _bot_state_coalesce_key(message)
        except Exception:
            return False
        return self._send_preencoded_trusted(
            message, coalesce_key=coalesce_key)

    def send_simulation_progress(self, frame_seq):
        """Prove that the native BattleRuntime frame callback is advancing."""
        sequence = _projectile_int_range(frame_seq, 1, MAX_PROJECTILE_ID)
        if (sequence is None or not self.is_bot_authority() or
                self.round_id is None or self.authority_epoch is None):
            return False
        return self._send({
            'type': 'simulation_progress',
            'round_id': int(self.round_id),
            'authority_epoch': int(self.authority_epoch),
            'frame_seq': int(sequence),
        })

    def _invalid_worker_message(self, reason):
        self.last_error = reason
        self.stop()
        return False

    def _handle_worker_welcome(self, message):
        capabilities = _strict_capabilities(message.get('capabilities'))
        server_capabilities = _strict_capabilities(
            message.get('server_capabilities', []))
        state_revision = _exact_int(message.get('state_revision'))
        round_id = _exact_int(message.get('round_id'))
        host_player_id = _exact_int(message.get('host_player_id'))
        authority_epoch = _projectile_int_range(
            message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
        server_time_ms = None
        if 'server_time_ms' in message:
            server_time_ms = _projectile_int_range(
                message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
        team_size = _exact_int(message.get('team_size'))
        authority_id = _authority_id(message.get('bot_authority_id'))
        phase = _safe_text(message.get('phase'), '', 16)
        map_name = _safe_text(message.get('map'), '', 80)
        protocol = _exact_int(message.get('protocol'))
        if (
                protocol is None or protocol <= 0 or
                message.get('role') != WORKER_ROLE or
                _exact_int(message.get('worker_id')) != WORKER_AUTHORITY_ID or
                capabilities is None or
                server_capabilities is None or
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY not in
                server_capabilities or
                PROJECTILE_LEDGER_CAPABILITY not in capabilities or
                SIMULATION_WORKER_CAPABILITY not in capabilities or
                RAM_CONTACT_LEDGER_CAPABILITY not in capabilities or
                HUMAN_RAM_TIMELINE_CAPABILITY not in capabilities or
                PLAYER_FIRE_INTENT_CAPABILITY not in capabilities or
                PLAYER_ENVIRONMENT_CAPABILITY not in capabilities or
                EFFECTIVE_PARAMS_CAPABILITY not in capabilities or
                RICOCHET_CONTINUATION_CAPABILITY not in capabilities or
                RAM_CONTACT_LEDGER_CAPABILITY not in server_capabilities or
                HUMAN_RAM_TIMELINE_CAPABILITY not in server_capabilities or
                PLAYER_FIRE_INTENT_CAPABILITY not in
                server_capabilities or
                PLAYER_ENVIRONMENT_CAPABILITY not in
                server_capabilities or
                EFFECTIVE_PARAMS_CAPABILITY not in server_capabilities or
                RICOCHET_CONTINUATION_CAPABILITY not in
                server_capabilities or
                state_revision is None or state_revision < 0 or
                round_id is None or round_id < 0 or
                (host_player_id is not None and host_player_id <= 0) or
                authority_epoch is None or server_time_ms is None or
                team_size is None or not 1 <= team_size <= 15 or
                authority_id != WORKER_AUTHORITY_ID or
                phase not in ('waiting', 'loading', 'battle') or
                not map_name):
            return self._invalid_worker_message('invalid worker welcome')
        self.ready = True
        self.worker_id = WORKER_AUTHORITY_ID
        self.player_id = WORKER_AUTHORITY_ID
        self.phase = phase
        self.map_name = map_name
        self.map_pool = self._map_names(message.get('map_pool'))
        self.round_id = round_id
        self.state_revision = state_revision
        self.host_player_id = host_player_id
        self.bot_authority_id = authority_id
        self.authority_epoch = authority_epoch
        self.server_time_ms = server_time_ms
        self.capabilities = capabilities
        self.server_capabilities = server_capabilities
        self._schema_negotiated = True
        self._notify('welcome', message)
        return True

    def _handle_worker_roster(self, message):
        protocol = _exact_int(message.get('protocol'))
        round_id = _exact_int(message.get('round_id'))
        state_revision = _exact_int(message.get('state_revision'))
        phase = _safe_text(message.get('phase'), '', 16)
        map_name = _safe_text(message.get('map'), '', 80)
        players = _strict_mapping_list(message.get('players'), 64)
        host_player_id = _exact_int(message.get('host_player_id'))
        authority_id = _authority_id(message.get('bot_authority_id'))
        authority_epoch = _projectile_int_range(
            message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
        server_time_ms = _projectile_int_range(
            message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
        player_ids = set(
            _exact_int(value.get('id')) for value in players or ())
        outfits_valid = all(
            _canonical_wire_outfits(value.get('outfits')) is not None
            for value in players or ())
        compacts_valid = all(
            _canonical_vehicle_compact_descr(
                value.get('vehicle_compact_descr')) is not None
            for value in players or ())
        effective_params_valid = all(
            _canonical_effective_params(
                value.get('effective_params')) is not None
            for value in players or ())
        if (
                protocol is None or protocol <= 0 or
                round_id is None or round_id < 0 or
                state_revision is None or state_revision < 0 or
                phase not in ('waiting', 'loading', 'battle') or
                not map_name or players is None or not outfits_valid or
                not compacts_valid or not effective_params_valid or
                ((players and host_player_id not in player_ids) or
                 (not players and host_player_id is not None)) or
                authority_id != WORKER_AUTHORITY_ID or
                authority_epoch is None or
                ('server_time_ms' in message and server_time_ms is None)):
            return self._invalid_worker_message('invalid worker roster')
        if self.round_id is not None and round_id < self.round_id:
            return False
        if (round_id == self.round_id and self.state_revision is not None and
                state_revision < self.state_revision):
            return False
        # A same-round waiting roster can be sent by an earlier server thread
        # after the loading barrier. Never demote or tear down an active worker
        # because those two TCP writes overtook each other.
        if (round_id == self.round_id and
                self.phase in ('loading', 'battle') and
                phase == 'waiting'):
            return False
        if (self.authority_epoch is not None and
                authority_epoch < self.authority_epoch):
            return self._invalid_worker_message(
                'worker authority epoch regressed')
        same_round = round_id == self.round_id
        if not same_round:
            self.last_snapshot = None
            self._battle_start_round_id = None
            self._battle_live_round_id = None
            self._worker_avatar = None
            self.server_time_ms = None
        self.round_id = round_id
        self.state_revision = state_revision
        self.phase = phase
        self.map_name = map_name
        maps = self._map_names(message.get('map_pool'))
        if maps:
            self.map_pool = maps
        self.roster = self._remember_player_outfits(players)
        self.host_player_id = host_player_id
        self.bot_authority_id = authority_id
        self.authority_epoch = authority_epoch
        if server_time_ms is not None:
            effective_server_time = int(server_time_ms)
            if same_round and self.server_time_ms is not None:
                effective_server_time = max(
                    effective_server_time, int(self.server_time_ms))
            if effective_server_time != server_time_ms:
                message = dict(message)
                message['server_time_ms'] = effective_server_time
            self.server_time_ms = effective_server_time
        self._notify('roster', message)
        return True

    def _dummy_player(self, players):
        if self._worker_avatar is not None:
            return dict(self._worker_avatar)
        source = None
        for value in players or ():
            if not isinstance(value, dict):
                continue
            player_id = _exact_int(value.get('id'))
            if player_id is not None and player_id > 0 and value.get('vehicle'):
                source = value
                break
        if source is None:
            raise ValueError('worker round has no real player descriptor')
        dummy = {
            'id': WORKER_AUTHORITY_ID,
            'name': 'SimulationWorker',
            'vehicle': source['vehicle'],
            'vehicle_compact_descr': source['vehicle_compact_descr'],
            'team': int(source.get('team', 1) or 1),
            'slot': 0,
            'x': 0.0, 'y': WORKER_DUMMY_Y, 'z': 0.0,
            'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': 0.0,
            'speed': 0.0, 'world_pose': True,
            'health': 1, 'max_health': 1, 'alive': True,
            'critical': {}, 'critical_revision': 0,
            'critical_base_revision': 0, 'critical_ack_seq': 0,
            'input_seq': 0,
            'up_cosine': 1.0,
            'landing_observation_seq': 0,
            # LANClient validates the equipment ledger for every projected
            # player, including this worker-local carrier.  Keep an isolated
            # copy of the real descriptor's canonical ledger; the carrier is
            # never published and never activates equipment itself.
            'equipment_states': [
                dict(value)
                for value in source.get('equipment_states') or ()],
            'equipment_revision': source.get('equipment_revision'),
            'equipment_intent_seq': source.get('equipment_intent_seq'),
            'equipment_intent_result': dict(
                source.get('equipment_intent_result') or {}),
            'outfits': {},
            'effective_params': _canonical_effective_params(
                source['effective_params']),
        }
        self._worker_avatar = dummy
        self.vehicle = dummy['vehicle']
        self.max_health = 1
        self.team = dummy['team']
        self.spawn = {
            'x': dummy['x'], 'y': dummy['y'], 'z': dummy['z'],
            'yaw': dummy['yaw']}
        return dict(dummy)

    def _project_runtime_message(self, message):
        projected = dict(message)
        players = _strict_mapping_list(message.get('players'), 63)
        if players is None:
            return None
        try:
            dummy = self._dummy_player(players)
        except (TypeError, ValueError, OverflowError):
            return None
        projected['players'] = list(players) + [dummy]
        if projected.get('type') == 'battle_start':
            projected['spawn'] = dict(self.spawn)
            projected['vehicle'] = self.vehicle
        return projected

    def _refresh_stale_battle_start(self, message):
        """Preserve the local dummy when a newer roster overtook start."""
        round_id = _exact_int(message.get('round_id'))
        state_revision = _exact_int(message.get('state_revision'))
        stale = (
            round_id == self.round_id and
            state_revision is not None and
            self.state_revision is not None and
            state_revision < self.state_revision)
        if not stale:
            return message
        if self._battle_start_round_id == round_id:
            return None
        refreshed = dict(message)
        refreshed['state_revision'] = self.state_revision
        if self.map_name:
            refreshed['map'] = self.map_name
        if self.roster:
            refreshed['players'] = list(self.roster)
        if self.host_player_id is not None:
            refreshed['host_player_id'] = self.host_player_id
        if self.bot_authority_id is not None:
            refreshed['bot_authority_id'] = self.bot_authority_id
        if self.authority_epoch is not None:
            refreshed['authority_epoch'] = self.authority_epoch
        if self.server_time_ms is not None:
            refreshed['server_time_ms'] = self.server_time_ms
        return refreshed

    def _inherit_contact_effective_params(self, message):
        """Attach cached static player inputs to one lean contact relay."""
        if not isinstance(message, dict):
            return None
        player = message.get('player')
        if not isinstance(player, dict):
            return None
        player_id = _exact_int(player.get('id'))
        params = _canonical_effective_params(
            self._published_player_effective_params.get(player_id))
        if player_id is None or player_id <= 0 or params is None:
            return None
        projected = dict(message)
        projected_player = dict(player)
        projected_player['effective_params'] = params
        projected['player'] = projected_player
        return projected

    def _handle_message(self, message):
        if not isinstance(message, dict):
            return
        kind = message.get('type')
        if kind == 'welcome':
            self._handle_worker_welcome(message)
            return
        if kind == 'roster':
            self._handle_worker_roster(message)
            return
        if (kind in ('battle_start', 'snapshot') and
                _authority_id(message.get('bot_authority_id')) !=
                WORKER_AUTHORITY_ID):
            self._invalid_worker_message(
                'worker authority identity changed')
            return
        if kind in ('battle_start', 'snapshot'):
            real_players = _strict_mapping_list(
                message.get('players'), 63)
            effective_params_valid = all(
                (_canonical_effective_params(
                    value.get('effective_params')) is not None
                 if 'effective_params' in value else
                 _exact_int(value.get('id')) in
                 self._published_player_effective_params)
                for value in real_players or ())
            if (real_players is None or not effective_params_valid or any(
                    _canonical_vehicle_compact_descr(
                        value.get('vehicle_compact_descr')) is None
                    for value in real_players)):
                self._invalid_worker_message(
                    'worker player descriptor is unavailable')
                return
        if kind == 'battle_start':
            message = self._refresh_stale_battle_start(message)
            if message is None:
                return
            # Start a fresh local carrier from this round's real descriptor.
            self._worker_avatar = None
        if kind == 'player_destructible_contact':
            message = self._inherit_contact_effective_params(message)
            if message is None:
                self._invalid_worker_message(
                    'worker player effective parameters are unavailable')
                return
        if kind in ('battle_start', 'snapshot'):
            projected = self._project_runtime_message(message)
            if projected is None:
                self._invalid_worker_message(
                    'worker runtime projection is unavailable')
                return
            message = projected
        LANClient._handle_message(self, message)


class _WorldDrawLease(object):
    """Reversible ownership of BigWorld.worldDrawEnabled(False)."""

    def __init__(self, bigworld):
        self._bigworld = bigworld
        self._original = None
        self.active = False

    def read(self):
        boundary = getattr(self._bigworld, 'worldDrawEnabled', None)
        if not callable(boundary):
            raise RuntimeError('BigWorld.worldDrawEnabled is unavailable')
        value = boundary()
        if value not in (False, True, 0, 1):
            raise RuntimeError('world draw getter returned an invalid state')
        return bool(value)

    def acquire(self):
        if self.active:
            return True
        boundary = getattr(self._bigworld, 'worldDrawEnabled', None)
        if not callable(boundary):
            raise RuntimeError('BigWorld.worldDrawEnabled is unavailable')
        self._original = self.read()
        boundary(False)
        if self.read() is not False:
            try:
                boundary(self._original)
            finally:
                self._original = None
            raise RuntimeError('world draw disable readback mismatch')
        self.active = True
        return True

    def restore(self):
        if not self.active:
            return False
        boundary = getattr(self._bigworld, 'worldDrawEnabled', None)
        original = self._original
        if not callable(boundary):
            raise RuntimeError('BigWorld.worldDrawEnabled is unavailable')
        boundary(original)
        if self.read() is not original:
            raise RuntimeError('world draw restore readback mismatch')
        self.active = False
        self._original = None
        return True


class WorkerSession(object):
    """Auto-connected worker lifecycle with no player lobby/UI ownership."""

    def __init__(self, config, client_factory=None, battle_factory=None,
                 lobby_ready=None, callback=None, cancel_callback=None,
                 bigworld=None, status_path=WORKER_STATUS_PATH):
        self._config = dict(config or {})
        self._client_factory = client_factory or AuthorityWorkerLANClient
        self._battle_factory = battle_factory or _load_battle_runtime()
        self._lobby_ready = lobby_ready or (lambda: True)
        self._bigworld = bigworld
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._status_path = status_path
        self.client = None
        self.runtime = None
        self.state = 'idle'
        self._stopped = False
        self._generation = 0
        self._active_round_id = None
        self._pending_start = None
        self._pending_start_deadline = None
        self._retired_rounds = set()
        self._monitor_callback_id = None
        self._retry_callback_id = None
        self._retry_delay = WORKER_RETRY_SECONDS
        self._next_status_time = 0.0
        self._next_progress_time = 0.0
        self._last_progress_frame = 0
        self._handling_failure = False
        self._draw = None
        self._probe_runtime = None
        self._probe_round_id = None
        self._probe_time = None
        self._probe_sample = None
        self._process_sample_time = None
        self._process_cpu_seconds = None

    def _ensure_runtime_boundaries(self):
        if self._bigworld is None:
            import BigWorld
            self._bigworld = BigWorld
        if self._callback is None:
            self._callback = self._bigworld.callback
        if self._cancel_callback is None:
            self._cancel_callback = self._bigworld.cancelCallback
        if self._draw is None:
            self._draw = _WorldDrawLease(self._bigworld)

    def start(self):
        if self._stopped or self.client is not None:
            return False
        self._ensure_runtime_boundaries()
        return self._connect()

    def _connect(self):
        if self._stopped:
            return False
        self._generation += 1
        generation = self._generation

        def on_event(kind, message):
            if not self._stopped and generation == self._generation:
                try:
                    self._on_event(kind, message)
                except Exception as error:
                    # Native event adapters are private #1513 boundaries. One
                    # failure must fence authority and clean up, not escape the
                    # LAN poll callback and leave a connected zombie worker.
                    self._worker_failure(error)

        self.client = self._client_factory(
            self._config.get('host', '127.0.0.1'),
            self._config.get('port', 28782), on_event=on_event,
            bigworld=self._bigworld)
        self.state = 'connecting'
        try:
            if not self.client.start():
                raise RuntimeError('worker LAN client could not start')
        except Exception as error:
            self.client = None
            sys.stdout.write(
                '[Offline LAN 0.9.22] worker connect failed: %s\n' % error)
            self._schedule_retry()
            return False
        self._schedule_monitor()
        self._write_status(force=True)
        return True

    def _schedule_monitor(self):
        if self._stopped or self._monitor_callback_id is not None:
            return False

        def monitor():
            self._monitor_callback_id = None
            self._monitor()

        self._monitor_callback_id = self._callback(
            WORKER_MONITOR_SECONDS, monitor)
        return True

    def _schedule_retry(self, delay=None, grow=True):
        if self._stopped or self._retry_callback_id is not None:
            return False

        if delay is None:
            delay = self._retry_delay
        if grow:
            self._retry_delay = min(
                WORKER_RETRY_MAX_SECONDS,
                max(WORKER_RETRY_SECONDS, delay * 2.0))

        def retry():
            self._retry_callback_id = None
            if self._stopped:
                return
            if not self._lobby_ready():
                # Native teardown can briefly precede the worker's garage.
                # Poll that local condition quickly without inflating the
                # network retry delay and missing the next user-started round.
                self._schedule_retry(
                    delay=WORKER_RETRY_SECONDS, grow=False)
                return
            self._connect()

        self._retry_callback_id = self._callback(delay, retry)
        return True

    def _publish_simulation_progress(self, runtime):
        now = time.time()
        if now < self._next_progress_time:
            return False
        sampler = getattr(runtime, '_authority_worker_probe_sample', None)
        sender = getattr(self.client, 'send_simulation_progress', None)
        if not callable(sampler) or not callable(sender):
            raise RuntimeError(
                'worker simulation progress boundary is unavailable')
        sample = sampler() or {}
        if sample.get('round_finished') is True:
            return False
        frame_seq = _projectile_int_range(
            sample.get('frame_callbacks'), 1, MAX_PROJECTILE_ID)
        if frame_seq is None or frame_seq <= self._last_progress_frame:
            return False
        if not sender(frame_seq):
            return False
        self._last_progress_frame = frame_seq
        self._next_progress_time = now + WORKER_PROGRESS_SECONDS
        return True

    def _monitor(self):
        if self._stopped:
            return
        if self.runtime is None and self._pending_start is not None:
            if (self.client is None or
                    not self.client.is_bot_authority()):
                self._pending_start = None
                self._pending_start_deadline = None
                self.state = 'standby'
            elif (self._pending_start_deadline is not None and
                  time.time() >= self._pending_start_deadline):
                self._worker_failure(RuntimeError(
                    'worker lobby restoration timed out'))
                return
        if self.runtime is None and self._pending_start is not None:
            try:
                lobby_ready = bool(self._lobby_ready())
            except Exception as error:
                self._worker_failure(error)
                return
            if lobby_ready:
                pending = self._pending_start
                self._pending_start = None
                self._pending_start_deadline = None
                if not self._start_round(pending):
                    return
        runtime = self.runtime
        if runtime is not None:
            if (self.client is None or not self.client.is_bot_authority() or
                    self._active_round_id in self._retired_rounds):
                if not self._retire_or_fail('authority_lost'):
                    return
            elif runtime.state == 'running':
                try:
                    self._publish_simulation_progress(runtime)
                except Exception as error:
                    self._worker_failure(error)
                    return
                ready_for_draw = getattr(
                    runtime, 'authority_worker_ready_for_draw_off', None)
                if not callable(ready_for_draw):
                    self._worker_failure(RuntimeError(
                        'worker draw-off readiness boundary is unavailable'))
                    return
                if not ready_for_draw():
                    self.state = 'loading_models'
                    self._write_status()
                    self._schedule_monitor()
                    return
                try:
                    self._draw.acquire()
                except Exception as error:
                    self._worker_failure(error)
                    return
                self.state = 'battle'
            elif runtime.state == 'failed':
                self._worker_failure(
                    RuntimeError(runtime.error or 'worker runtime failed'))
                return
        self._write_status()
        self._schedule_monitor()

    def _start_round(self, message):
        round_id = _exact_int(message.get('round_id'))
        if (round_id is None or round_id in self._retired_rounds or
                self.client is None or not self.client.is_bot_authority()):
            return False
        if self.runtime is not None and round_id == self._active_round_id:
            return False
        if self.runtime is not None and round_id != self._active_round_id:
            if (self._active_round_id is not None and
                    round_id < self._active_round_id):
                return False
            # The new start barrier may overtake the previous round's waiting
            # roster on another server handler. Retire the old native space
            # first, then wait for its asynchronous lobby restoration.
            try:
                self._retire_runtime('round_complete')
            except Exception as error:
                self._worker_failure(error)
                return False
        try:
            lobby_ready = bool(self._lobby_ready())
        except Exception as error:
            self._worker_failure(error)
            return False
        if not lobby_ready:
            if (self._pending_start is not None and
                    _exact_int(self._pending_start.get('round_id')) !=
                    round_id):
                self._worker_failure(RuntimeError(
                    'worker received overlapping pending rounds'))
                return False
            self._pending_start = dict(message)
            self._pending_start_deadline = (
                time.time() + float(
                    self._config.get('startupTimeoutSeconds', 30.0)))
            self.state = 'waiting_lobby'
            self._write_status(force=True)
            return True
        self._pending_start = None
        self._pending_start_deadline = None
        self._last_progress_frame = 0
        self._next_progress_time = 0.0
        if self.runtime is not None:
            if round_id == self._active_round_id:
                return False
            self._worker_failure(RuntimeError(
                'worker received overlapping round lifecycles'))
            return False
        players = message.get('players') or ()
        dummy = next((dict(value) for value in players
                      if isinstance(value, dict) and
                      _exact_int(value.get('id')) == WORKER_AUTHORITY_ID), None)
        if dummy is None:
            self._worker_failure(RuntimeError(
                'worker dummy Avatar projection is missing'))
            return False
        config = dict(self._config)
        config.update({
            'worker_mode': True,
            'native_remote_vehicles': False,
            'bot_track_animation': False,
            'map': message.get('map'),
            'spawn': {
                'x': dummy['x'], 'y': dummy['y'], 'z': dummy['z'],
                'yaw': dummy.get('yaw', 0.0)},
            'vehicle': dummy['vehicle'],
            'name': dummy['name'],
        })
        runtime = self._battle_factory()
        self.runtime = runtime
        self._active_round_id = round_id
        self.state = 'loading'
        try:
            accepted = runtime.start(
                config, message=message, lan_client=self.client,
                on_local_leave=None)
        except Exception as error:
            if self.runtime is runtime:
                self._worker_failure(error)
            return False
        if not accepted:
            # A synchronous BattleRuntime._fail notification may already have
            # retired this exact object through client.on_event.
            if self.runtime is runtime:
                self._worker_failure(RuntimeError(
                    runtime.error or
                    'worker battle runtime rejected start'))
            return False
        self._write_status(force=True)
        return True

    def _retire_runtime(self, reason):
        runtime = self.runtime
        round_id = self._active_round_id
        if reason != 'round_complete' and self.client is not None:
            # Fence every authority publisher before touching native objects.
            self.client.bot_authority_id = None
        errors = []
        try:
            if runtime is not None:
                runtime.stop(show_login=False, restore_account=True)
        except Exception as error:
            errors.append(error)
        finally:
            # Keep the arena hidden until every native model is destroyed.
            try:
                if self._draw is not None:
                    self._draw.restore()
            except Exception as error:
                errors.append(error)
        self._reset_probe_window()
        if errors:
            raise errors[0]
        # Keep the exact BattleRuntime owner and round id while native stop or
        # draw restoration is unresolved.  Both operations are idempotent and
        # a later stop can therefore retry instead of abandoning a live map.
        if self.runtime is runtime:
            self.runtime = None
        self._active_round_id = None
        self._last_progress_frame = 0
        self._next_progress_time = 0.0
        if round_id is not None and reason not in ('round_complete',):
            self._retired_rounds.add(round_id)
        self.state = 'waiting' if reason == 'round_complete' else 'standby'
        self._write_status(force=True)
        return runtime is not None

    def _retire_or_fail(self, reason):
        try:
            return self._retire_runtime(reason)
        except Exception as error:
            self._worker_failure(error)
            return False

    def _worker_failure(self, error):
        if self._handling_failure or self._stopped:
            return False
        self._handling_failure = True
        server_busy = 'battle already in progress' in str(error).lower()
        if server_busy:
            self._retry_delay = WORKER_BUSY_RETRY_SECONDS
        sys.stdout.write(
            '[Offline LAN 0.9.22] simulation worker failed: %s\n' % error)
        if self._active_round_id is not None:
            self._retired_rounds.add(self._active_round_id)
        self._pending_start = None
        self._pending_start_deadline = None
        # Fence every authority send before native teardown starts.
        if self.client is not None:
            self.client.bot_authority_id = None
        cleanup_failed = False
        try:
            self._retire_runtime('worker_failed')
        except Exception as cleanup_error:
            cleanup_failed = True
            sys.stdout.write(
                '[Offline LAN 0.9.22] worker cleanup failed: %s\n' %
                cleanup_error)
        client = self.client
        self._generation += 1
        if client is not None:
            try:
                client.on_event = None
                client.stop()
            except Exception as cleanup_error:
                cleanup_failed = True
                sys.stdout.write(
                    '[Offline LAN 0.9.22] worker transport cleanup failed: '
                    '%s\n' % cleanup_error)
            else:
                if self.client is client:
                    self.client = None
        self.state = 'failed' if cleanup_failed else 'retrying'
        if cleanup_failed:
            self._stopped = True
        self._write_status(force=True)
        self._handling_failure = False
        if cleanup_failed:
            # A process with uncertain native cleanup or draw state must not
            # re-enter a new arena. Keep it fenced for manual restart.
            return False
        try:
            # A busy server is expected until the current battle ends. Five
            # seconds avoids a restart storm while bounding how late this
            # worker can rejoin after the room returns to waiting.
            self._schedule_retry(grow=not server_busy)
        except Exception as retry_error:
            self.state = 'failed'
            self._stopped = True
            self._write_status(force=True)
            sys.stdout.write(
                '[Offline LAN 0.9.22] worker retry failed: %s\n' %
                retry_error)
            return False
        return True

    def _on_event(self, kind, message):
        message = message if isinstance(message, dict) else {}
        if kind in ('welcome', 'roster'):
            if kind == 'welcome':
                self._retry_delay = WORKER_RETRY_SECONDS
            if (self.client is not None and
                    not self.client.is_bot_authority()):
                self._pending_start = None
                self._pending_start_deadline = None
            if (self.runtime is not None and
                    not self.client.is_bot_authority()):
                if not self._retire_or_fail('authority_lost'):
                    return
            if message.get('phase') == 'waiting':
                self._pending_start = None
                self._pending_start_deadline = None
                if self.runtime is not None:
                    if not self._retire_or_fail('round_complete'):
                        return
                self.state = 'waiting'
            elif self.runtime is None:
                self.state = 'standby'
        elif kind == 'battle_start':
            if self.client.is_bot_authority():
                self._start_round(message)
            else:
                self.state = 'standby'
        elif kind in ('snapshot', 'events', 'battle_live',
                      'bot_observation', 'fire_intent',
                      'player_destructible_contact',
                      'fire_intent_result'):
            if (self.runtime is not None and
                    not self.client.is_bot_authority()):
                self._retire_or_fail('authority_lost')
                return
            if self.runtime is None:
                return
            round_id = _exact_int(message.get('round_id'))
            if round_id != self._active_round_id:
                return
            if kind == 'snapshot':
                self.runtime.on_snapshot(message)
            elif kind == 'events':
                self.runtime.on_events(message)
            elif kind == 'battle_live':
                self.runtime.on_battle_live(message)
            elif kind == 'fire_intent':
                self.runtime.on_fire_intent(message)
            elif kind == 'player_destructible_contact':
                self.runtime.on_player_destructible_contact(message)
            elif kind == 'fire_intent_result':
                self.runtime.on_fire_intent_result(message)
            else:
                self.runtime.on_bot_observation(message)
        elif kind == 'battle_failed':
            self._worker_failure(RuntimeError(
                message.get('message') or 'worker battle failed'))
        elif kind in ('error', 'connection_lost', 'disconnected'):
            self._worker_failure(RuntimeError(
                message.get('message') or 'worker transport lost'))
        self._write_status()

    def _write_status(self, force=False):
        now = time.time()
        if not force and now < self._next_status_time:
            return False
        self._next_status_time = now + WORKER_STATUS_SECONDS
        draw_enabled = None
        try:
            if self._draw is not None:
                draw_enabled = self._draw.read()
        except Exception:
            draw_enabled = None
        value = {
            'schema': 1,
            'role': WORKER_ROLE,
            'process_id': os.getpid(),
            'heartbeat_epoch': now,
            'state': self.state,
            'connected': bool(
                self.client is not None and
                getattr(self.client, 'connected', False)),
            'phase': (
                None if self.client is None else
                getattr(self.client, 'phase', None)),
            'round_id': self._active_round_id,
            'bot_authority_id': (
                None if self.client is None else
                self.client.bot_authority_id),
            'world_draw_enabled': draw_enabled,
            'process_performance': self._process_performance(now),
            'runtime': self._runtime_status(now),
        }
        try:
            # This is live diagnostics, not user state. Avoid an fsync and
            # write-through rename on the native simulation callback thread.
            port_config.write_json(
                self._status_path, value, durable=False)
        except Exception:
            return False
        return True

    def _process_performance(self, now):
        """Sample Windows CPU/working set only at the two-second status rate."""
        counters = _windows_process_counters()
        if not isinstance(counters, dict):
            return {
                'available': False,
                'source': 'windows_process_api',
                'cpu_core_percent': None,
                'cpu_machine_percent': None,
                'working_set_bytes': None,
                'logical_processors': None,
                'gpu_measured': False,
            }
        try:
            cpu_seconds = float(counters['cpu_seconds'])
            working_set = max(0, int(counters['working_set_bytes']))
            processors = counters.get('logical_processors')
            processors = (None if processors is None else
                          max(1, int(processors)))
        except (KeyError, TypeError, ValueError, OverflowError):
            return {
                'available': False,
                'source': 'windows_process_api',
                'cpu_core_percent': None,
                'cpu_machine_percent': None,
                'working_set_bytes': None,
                'logical_processors': None,
                'gpu_measured': False,
            }
        core_percent = None
        if (self._process_sample_time is not None and
                self._process_cpu_seconds is not None and
                float(now) > self._process_sample_time and
                cpu_seconds >= self._process_cpu_seconds):
            core_percent = (100.0 *
                            (cpu_seconds - self._process_cpu_seconds) /
                            (float(now) - self._process_sample_time))
        self._process_sample_time = float(now)
        self._process_cpu_seconds = cpu_seconds
        return {
            'available': True,
            'source': 'windows_process_api',
            # This may exceed 100% when several worker threads are busy.
            'cpu_core_percent': core_percent,
            'cpu_machine_percent': (
                None if core_percent is None or processors is None else
                core_percent / processors),
            'working_set_bytes': working_set,
            'logical_processors': processors,
            # #1513 exposes no reliable per-process GPU counter to Python.
            'gpu_measured': False,
        }

    @staticmethod
    def _counter_delta(current, previous, name):
        try:
            return max(0, int(current.get(name)) - int(previous.get(name)))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _mapping_delta(current, previous, name, integer=False):
        try:
            current_values = current.get(name) or {}
            previous_values = previous.get(name) or {}
        except AttributeError:
            return None
        if (not isinstance(current_values, dict) or
                not isinstance(previous_values, dict)):
            return None
        result = {}
        for key in set(current_values).union(previous_values):
            try:
                value = (float(current_values.get(key, 0.0)) -
                         float(previous_values.get(key, 0.0)))
            except (TypeError, ValueError, OverflowError):
                continue
            if value < 0.0 or math.isnan(value) or math.isinf(value):
                continue
            result[str(key)] = int(value) if integer else value
        return result

    @staticmethod
    def _mapping_rates(values, seconds, scale=1.0):
        if not isinstance(values, dict) or not seconds:
            return None
        return dict((name, float(value) * float(scale) / float(seconds))
                    for name, value in values.items())

    def _reset_probe_window(self):
        self._probe_runtime = None
        self._probe_round_id = None
        self._probe_time = None
        self._probe_sample = None

    def _runtime_status(self, now):
        runtime = self.runtime
        sampler = getattr(runtime, '_authority_worker_probe_sample', None)
        if runtime is None or not callable(sampler):
            self._reset_probe_window()
            return {}
        try:
            sample = dict(sampler() or {})
        except Exception:
            return {}
        previous = self._probe_sample
        previous_time = self._probe_time
        same_window = (
            runtime is self._probe_runtime and
            self._active_round_id == self._probe_round_id and
            previous is not None and previous_time is not None and
            now > previous_time)
        window_seconds = None
        callback_delta = render_delta = publication_delta = None
        revision_delta = send_failed_delta = None
        probe_deltas = probe_rates = None
        timed_probe_seconds = timed_probe_ms = None
        presentation_delta = presentation_rates = None
        catchup_delta = debt_callback_delta = None
        astar_exhausted_delta = astar_completed_delta = None
        astar_failed_delta = None
        visibility_delta = visibility_hz = None
        shot_lane_delta = shot_lane_hz = None
        if same_window:
            window_seconds = float(now - previous_time)
            callback_delta = self._counter_delta(
                sample, previous, 'authority_callbacks')
            render_delta = self._counter_delta(
                sample, previous, 'frame_callbacks')
            publication_delta = self._counter_delta(
                sample, previous, 'bot_state_enqueued')
            revision_delta = self._counter_delta(
                sample, previous, 'bot_state_revision')
            send_failed_delta = self._counter_delta(
                sample, previous, 'bot_state_send_failed')
            probe_deltas = self._mapping_delta(
                sample, previous, 'bot_probes', integer=True)
            probe_rates = self._mapping_rates(
                probe_deltas, window_seconds)
            timed_probe_seconds = self._mapping_delta(
                sample, previous, 'bot_probe_seconds')
            timed_probe_ms = self._mapping_rates(
                timed_probe_seconds, 1.0, scale=1000.0)
            presentation_delta = self._mapping_delta(
                sample, previous, 'presentation', integer=True)
            presentation_rates = self._mapping_rates(
                presentation_delta, window_seconds)
            current_control = sample.get('control') or {}
            previous_control = previous.get('control') or {}
            catchup_delta = self._counter_delta(
                current_control, previous_control, 'catchup_callbacks')
            debt_callback_delta = self._counter_delta(
                current_control, previous_control, 'debt_callbacks')
            astar_exhausted_delta = self._counter_delta(
                current_control, previous_control,
                'astar_budget_exhausted_callbacks')
            astar_completed_delta = self._counter_delta(
                current_control, previous_control, 'astar_completed')
            astar_failed_delta = self._counter_delta(
                current_control, previous_control, 'astar_failed')
            current_diagnostics = sample.get('bot_diagnostics') or {}
            previous_diagnostics = previous.get('bot_diagnostics') or {}
            visibility_delta = {}
            for name in _VISIBILITY_DIAGNOSTIC_COUNTERS:
                delta = self._counter_delta(
                    current_diagnostics, previous_diagnostics, name)
                if delta is not None:
                    visibility_delta[name] = delta
            visibility_hz = self._mapping_rates(
                visibility_delta, window_seconds)
            shot_lane_delta = {}
            for name in _SHOT_LANE_DIAGNOSTIC_COUNTERS:
                delta = self._counter_delta(
                    current_diagnostics, previous_diagnostics, name)
                if delta is not None:
                    shot_lane_delta[name] = delta
            shot_lane_hz = self._mapping_rates(
                shot_lane_delta, window_seconds)
        sample.update({
            'window_seconds': window_seconds,
            'render_callback_hz': (
                None if render_delta is None else
                render_delta / window_seconds),
            'callback_hz': (
                None if callback_delta is None else
                callback_delta / window_seconds),
            'bot_publication_hz': (
                None if publication_delta is None else
                publication_delta / window_seconds),
            'revision_delta': revision_delta,
            'send_failed_delta': send_failed_delta,
            'logical_probe_delta': probe_deltas,
            'logical_probe_hz': probe_rates,
            'timed_probe_ms_delta': timed_probe_ms,
            'presentation_delta': presentation_delta,
            'presentation_hz': presentation_rates,
            'catchup_delta': catchup_delta,
            'debt_callback_delta': debt_callback_delta,
            'astar_budget_exhausted_delta': astar_exhausted_delta,
            'astar_completed_delta': astar_completed_delta,
            'astar_failed_delta': astar_failed_delta,
            'visibility_counter_delta': visibility_delta,
            'visibility_counter_hz': visibility_hz,
            'shot_lane_counter_delta': shot_lane_delta,
            'shot_lane_counter_hz': shot_lane_hz,
        })
        self._probe_runtime = runtime
        self._probe_round_id = self._active_round_id
        self._probe_time = now
        self._probe_sample = dict(sample)
        return sample

    def stop(self, show_login=False, restore_account=False,
             release_join=False):
        del show_login, release_join
        if (self._stopped and self.runtime is None and
                self.client is None and
                (self._draw is None or not self._draw.active)):
            return
        self._stopped = True
        self._generation += 1
        for name in ('_monitor_callback_id', '_retry_callback_id'):
            callback_id = getattr(self, name)
            setattr(self, name, None)
            if callback_id is not None and self._cancel_callback is not None:
                try:
                    self._cancel_callback(callback_id)
                except Exception:
                    pass
        client = self.client
        if client is not None:
            client.bot_authority_id = None
        cleanup_error = None
        try:
            if self.runtime is not None:
                runtime = self.runtime
                try:
                    runtime.stop(
                        show_login=False,
                        restore_account=bool(restore_account))
                finally:
                    # Do not reveal still-live models during native teardown.
                    if self._draw is not None:
                        self._draw.restore()
                if self.runtime is runtime:
                    self.runtime = None
            elif self._draw is not None:
                self._draw.restore()
        except Exception as error:
            cleanup_error = error
        finally:
            if client is not None:
                try:
                    client.on_event = None
                    client.stop()
                except Exception as error:
                    if cleanup_error is None:
                        cleanup_error = error
                else:
                    if self.client is client:
                        self.client = None
        self._active_round_id = None
        self._pending_start = None
        self._pending_start_deadline = None
        self._last_progress_frame = 0
        self._next_progress_time = 0.0
        self._reset_probe_window()
        self.state = 'stopped'
        self._write_status(force=True)
        if cleanup_error is not None:
            raise cleanup_error
