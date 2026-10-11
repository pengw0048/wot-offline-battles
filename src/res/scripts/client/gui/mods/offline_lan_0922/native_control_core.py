"""Typed main-thread frontier for one persistent native control lifetime."""
from __future__ import print_function

import copy

from gui.mods.offline_lan_0922.ai.driver import LocalDriver
from gui.mods.offline_lan_0922.worker_diagnostics import (
    observed, count as combat_count)

_POSE_NAMES = ('x', 'y', 'z', 'yaw', 'pitch', 'roll', 'aim_yaw',
               'turret_yaw', 'gun_pitch', 'speed', 'velocity')
_RECOVERY = ('drive', 'arrived', 'avoid', 'blocked', 'reverse_turn',
             'pivot_recovery')
_TRAFFIC = (None, 'yield', 'head_on', 'head_on_blocked')
_STATE_NUMBERS = ('recovery_count', 'stuck_time', 'recovery_time',
    'recovery_side', 'steering_yaw', 'steering_age', 'plan_age', 'clock',
    'escape_side', 'escape_side_until', 'last_desired_yaw',
    'heading_progress_yaw', 'best_heading_error', 'traffic_wait_time',
    'last_step', 'last_clear_yaw')


def _key(source):
    return (0 if source.get('kind') == 'human' else 1,
            int(source.get('network_id', source.get('id', 0))))


def _wire_key(key):
    return ('human' if key[0] == 0 else 'bot', int(key[1]))


def _position(source):
    return tuple(source.get('position') or
                 (source.get('x', 0.0), source.get('y', 0.0),
                  source.get('z', 0.0)))


def _pose(source):
    mask = sum(1 << index for index, name in enumerate(_POSE_NAMES)
               if name in source)
    # These fields are mandatory in the original remembered-pose projection.
    mask |= 1 | 2 | 4 | 8 | (1 << 9)
    values = [float(source.get(name, 0.0) or 0.0)
              for name in _POSE_NAMES[:-1]]
    values.extend(float(v) for v in source.get('velocity', (0.0, 0.0, 0.0)))
    return (_position(source), tuple(values), mask)


def _pose_dict(row):
    position, values, mask = row
    result = dict((name, values[index])
                  for index, name in enumerate(_POSE_NAMES[:-1])
                  if mask & (1 << index))
    if mask & (1 << 10):
        result['velocity'] = tuple(values[10:13])
    result['position'] = tuple(position)
    return result


def _body(raw, half_length=3.5, half_width=1.7):
    if not isinstance(raw, dict):
        raw = {'position': raw}
    position = raw.get('position') or raw.get('pos')
    if position is None:
        return None
    shape = raw.get('shape')
    return (int(raw.get('id', 0) or 0), tuple(position),
            tuple(raw.get('velocity', (0.0, 0.0, 0.0))),
            (int(raw.get('team', 0)), bool(raw.get('alive', True)),
             float(raw.get('yaw', 0.0) or 0.0),
             float(shape[1] if shape is not None else
                   raw.get('half_length', half_length) or half_length),
             float(shape[0] if shape is not None else
                   raw.get('half_width', half_width) or half_width),
             shape is not None, float(shape[2]) if shape is not None else 0.0,
             float(shape[3]) if shape is not None else 0.0))


def _bodies(rows, half_length=3.5, half_width=1.7):
    result = []
    for raw in rows or ():
        body = _body(raw, half_length, half_width)
        if body is not None:
            result.append(body)
    return tuple(result)


def _command(row, original=None):
    result = dict(original or ())
    result.update(throttle=row[0], turn=row[1], target_yaw=row[2],
                  recovery_mode=_RECOVERY[row[3]])
    if row[4]:
        result['traffic_mode'] = _TRAFFIC[row[4]]
    if row[5]:
        result['reverse_blocked_by'] = row[6] if row[6] else True
    return result


def _command_row(command):
    return (float(command.get('throttle', 0.0)),
            float(command.get('turn', 0.0)),
            float(command.get('target_yaw', 0.0)),
            _RECOVERY.index(command.get('recovery_mode', 'drive')),
            _TRAFFIC.index(command.get('traffic_mode')),
            'reverse_blocked_by' in command,
            int(command.get('reverse_blocked_by', 0)))


def _state_row(state):
    flags = ((state.get('steering_yaw') is not None) |
        ((state.get('steering_reason', 'route') != 'route') << 1) |
        ((state.get('last_desired_yaw') is not None) << 2) |
        ((state.get('heading_progress_yaw') is not None) << 3) |
        ((state.get('last_clear_yaw') is not None) << 4) |
        (bool(state.get('traffic_waiting', False)) << 5) |
        ((state.get('braking_target') is not None) << 6) |
        ((state.get('best_heading_error') is not None) << 7) |
        (('traffic_waiting' in state) << 8) |
        ((state.get('coast_target') is not None) << 9))
    position = state['last_position']
    return (int(state['team_slot']), (position[0], 0.0, position[1]),
            tuple(float(state.get(name, 0.0) or 0.0)
                  for name in _STATE_NUMBERS), flags,
            tuple(state.get('failed_yaws', {}).items()),
            state.get('braking_target') or (0.0, 0.0),
            state.get('coast_target') or (0.0, 0.0))


def _state_dict(row, previous=None):
    slot, position, values, flags, failed, brake, coast = row
    result = dict(zip(_STATE_NUMBERS, values))
    result.update(team_slot=slot, last_position=(position[0], position[2]),
                  recovery_count=int(values[0]),
                  recovery_timing_phase=((slot * 7) % 15 + 0.5) / 15.0,
                  failed_yaws=dict(failed),
                  steering_reason='obstacle' if flags & 2 else 'route',
                  braking_target=tuple(brake) if flags & 64 else None,
                  coast_target=tuple(coast) if flags & 512 else None)
    if not flags & 1:
        result['steering_yaw'] = None
    if not flags & 4:
        result['last_desired_yaw'] = None
    if not flags & 8:
        result['heading_progress_yaw'] = None
    if not flags & 16:
        result.pop('last_clear_yaw', None)
    if flags & 256:
        result['traffic_waiting'] = bool(flags & 32)
    # None is used only before the first heading measurement in the old law.
    if not flags & 128:
        result['best_heading_error'] = None
    return result


class _NativeDriver(LocalDriver):
    def __init__(self, owner, original):
        self.owner = owner
        self.original = original
        self.states = copy.deepcopy(original.states)
        self.stuck_seconds = original.stuck_seconds
        self.recovery_seconds = original.recovery_seconds
        self.failure_ttl = original.failure_ttl
        if (self.stuck_seconds, self.recovery_seconds, self.failure_ttl) != (
                1.8, 0.85, 2.0):
            raise ValueError('Native control requires the reviewed driver law')
        for actor, state in self.states.items():
            owner._call('sim_control_driver_restore', int(actor),
                        _state_row(state))

    def _dispatcher(self, direction_clear, pose_clear=None):
        def dispatch(opcode, yaw, maximum_distance):
            if opcode:
                return int(self._pose_fits(pose_clear, yaw))
            return int(self._clear(direction_clear, yaw,
                       None if maximum_distance < 0.0 else maximum_distance))
        return dispatch

    def drive(self, bot_id, team_slot, position, yaw, speed, dt, target,
              neighbours, direction_clear, velocity=None,
              half_length=3.5, half_width=1.7, movement_intent=True,
              stopping_distance=None, stop_at_target=True,
              decision_horizon=0.0, pose_clear=None, turn_speed_limit=None):
        values = (int(team_slot), float(yaw), float(speed), float(dt),
            float(half_length), float(half_width), bool(movement_intent),
            bool(stop_at_target), stopping_distance is not None,
            float(stopping_distance or 0.0), float(decision_horizon),
            round(float(target[0]), 2), round(float(target[2]), 2),
            float(turn_speed_limit or 0.0))
        inputs = (int(bot_id), tuple(position), tuple(target), values)
        bodies = _bodies(neighbours, half_length, half_width)
        output = self.owner._optional('sim_control_drive', inputs, bodies,
            self._dispatcher(direction_clear, pose_clear))
        if output is None:
            raise RuntimeError(
                'Native control operation failed: sim_control_drive; '
                'inputs=%r; neighbours=%d' % (inputs, len(bodies)))
        result, state = output
        self.states[bot_id] = _state_dict(state, self.states.get(bot_id))
        return _command(result)

    def remember_failure(self, bot_id, yaw, ttl=None):
        state = self.owner._optional('sim_control_driver_event', int(bot_id),
            0, float(yaw), self.failure_ttl if ttl is None else float(ttl))
        if state is not None:
            self.states[bot_id] = _state_dict(state, self.states.get(bot_id))

    def wait_for_traffic(self, bot_id, elapsed=None):
        state = self.owner._optional('sim_control_driver_event', int(bot_id),
            1, -1.0 if elapsed is None else float(elapsed), 0.0)
        if state is None:
            return False
        self.states[bot_id] = _state_dict(state, self.states.get(bot_id))
        return True

    def forget(self, bot_id):
        self.owner._call('sim_control_driver_event', int(bot_id), 2, 0.0, 0.0)
        self.states.pop(bot_id, None)

    def detach(self):
        self.original.states = copy.deepcopy(self.states)


class _NativeTraffic(object):
    def __init__(self, owner, original):
        self.owner, self.original = owner, original
        rows = []
        for pair, lease in original._pairs.items():
            head_on = lease['mode'] == 'head_on'
            targets = lease.get('targets', {})
            rows.append((int(pair[0]), int(pair[1]),
                (head_on, lease['axis'][0], lease['axis'][1],
                 targets.get(pair[0], 0.0), targets.get(pair[1], 0.0),
                 lease.get('winner', 0), lease.get('clear_after', 0.0),
                 lease.get('until', 0.0), lease.get('blocked_until', 0.0),
                 lease.get('blocked_until') is not None)))
        owner._call('sim_control_traffic_state',
                    (tuple(rows), tuple(original._held.items())))

    def adjust(self, bot_id, body, command, neighbours, now, direction_clear):
        if (command.get('recovery_mode', 'drive') not in _RECOVERY or
                command.get('traffic_mode') not in _TRAFFIC):
            return dict(command)
        def dispatch(unused_opcode, yaw, unused_distance):
            try:
                return int(bool(direction_clear(yaw)))
            except Exception:
                return 0
        result = self.owner._call('sim_control_traffic', _body(body),
            _command_row(command), _bodies(neighbours), float(now),
            command.get('combat_mode', 'route') in ('route', 'advance'),
            dispatch)
        return _command(result, command)

    def forget(self, bot_id):
        self.owner._call('sim_control_driver_event', int(bot_id), 3, 0.0, 0.0)

    def detach(self):
        rows, held = self.owner._call('sim_control_traffic_state', None)
        pairs = {}
        for first, second, n in rows:
            lease = {'mode': 'head_on' if n[0] else 'yield',
                     'axis': tuple(n[1:3])}
            if n[0]:
                lease['targets'] = {first: n[3], second: n[4]}
                if n[9]:
                    lease['blocked_until'] = n[8]
            else:
                lease.update(winner=int(n[5]), clear_after=n[6], until=n[7])
            pairs[(first, second)] = lease
        self.original._pairs = pairs
        self.original._held = dict(held)

class _NativeRadio(object):
    def __init__(self, owner):
        self.owner = owner
        self.rows = ()
        self.actors = {}

    def refresh(self):
        self.rows = self.owner._call('sim_control_radio_actors')
        self.actors = dict((_wire_key(row[0]),
            (row[1], tuple(row[2]), row[3]) + ((row[4],) if row[0][0] == 0 else ()))
            for row in self.rows)
        for key in tuple(self.owner._old_radio.observations):
            if key not in self.actors:
                del self.owner._old_radio.observations[key]

    def connected(self, first, second):
        first = (0 if first[0] == 'human' else 1, int(first[1]))
        second = (0 if second[0] == 'human' else 1, int(second[1]))
        return bool(self.owner._call('sim_control_radio', 1, first, second,
                                    self.owner._now))

    def contact(self, recipient, target, now):
        recipient = (0 if recipient[0] == 'human' else 1, int(recipient[1]))
        target = (0 if target[0] == 'human' else 1, int(target[1]))
        remaining, fresh, pose = self.owner._call(
            'sim_control_radio', 0, recipient, target, float(now))
        return remaining, bool(fresh), _pose_dict(pose) if pose is not None else None

    def summaries(self, pairs, now):
        """Read ordered recipient flags without crossing remembered poses."""
        native_keys, rows = {}, []
        for recipient, target in pairs:
            for key in (recipient, target):
                if key not in native_keys:
                    native_keys[key] = (0 if key[0] == 'human' else 1, int(key[1]))
            rows.append((native_keys[recipient], native_keys[target]))
        result = self.owner._call('sim_control_radio_summaries',
                                  tuple(rows), float(now))
        return tuple((remaining, bool(fresh)) for remaining, fresh in result)


class NativeControl(object):
    def __init__(self, runtime, backend, context_handle):
        self.runtime, self.backend, self.handle = runtime, backend, context_handle
        self._config = {}
        self._config_projections = {}
        self._templates = {}
        self._pose_free = {}
        self._sources = {}
        self._present = ()
        self._sequence = 0
        self._open = False
        self._detached = False
        self._now = 0.0
        self._visibility_tick = None
        self._scheduling = {}
        self._sight_binding = None
        self._old_driver = runtime.adapter.driver
        self._old_traffic = runtime._traffic_coordinator
        self._old_radio = runtime._radio_network
        self.radio = _NativeRadio(self)
        # Build and rebase both stages before changing either runtime alias.
        self.driver = _NativeDriver(self, self._old_driver)
        self.traffic = _NativeTraffic(self, self._old_traffic)
        checkpoint = getattr(runtime, '_native_control_checkpoint', None)
        if checkpoint is not None and checkpoint[0] == getattr(runtime, 'round_id', None):
            for key, values in checkpoint[1].items():
                self.configure(key, values)
            self._call('sim_control_snapshot', checkpoint[2])
            self._present = tuple(tuple(row[0]) for row in checkpoint[2][0])
        runtime.adapter.driver = self.driver
        runtime._traffic_coordinator = self.traffic

    def _optional(self, name, *args):
        return getattr(self.backend, name)(self.handle, *args)

    def _call(self, name, *args):
        result = self._optional(name, *args)
        if result is None:
            raise RuntimeError('Native control operation failed: ' + name)
        return result

    def detach(self):
        if self._detached:
            return
        self._detached = True
        try:
            self.driver.detach()
            if self._open:
                self.finish()
            if self._present:
                snapshot = self._call('sim_control_snapshot', None)
                self.runtime._native_control_checkpoint = (
                    getattr(self.runtime, 'round_id', None), dict(self._config), snapshot)
                self._export_perception(snapshot)
            self.traffic.detach()
        finally:
            try:
                from .battle_visibility import close_visibility
                close_visibility(self)
            finally:
                self._config_projections.clear()
                self._sight_binding = None
                if self.runtime.adapter.driver is self.driver:
                    self.runtime.adapter.driver = self._old_driver
                if self.runtime._traffic_coordinator is self.traffic:
                    self.runtime._traffic_coordinator = self._old_traffic
                if self.runtime._radio_network is self.radio:
                    self.runtime._radio_network = self._old_radio

    def configure(self, key, values):
        key = tuple(key)
        values = tuple(values)
        if self._config.get(key) == values:
            combat_count('frontier_control_config_unchanged')
            return
        combat_count('frontier_control_config_changed')
        self._call('sim_control_configure', ((key, hash(_wire_key(key)), values),))
        self._config[key] = values

    def update_samples(self, rows):
        self._call('sim_control_update', tuple(rows))

    def begin_samples(self, order, now, budget, asynchronous=False,
                      include_humans=True):
        self._sequence += 1
        self._now = float(now)
        self._call('sim_control_begin', self._sequence, float(now),
                   tuple(order), int(budget), bool(asynchronous), bool(include_humans))
        self._open = True

    def contacts(self, key, probe):
        return self._call('sim_control_contacts', tuple(key), probe)

    def observe_humans(self, probe):
        self._call('sim_control_humans', probe)

    def observations(self):
        return self._call('sim_control_observations')

    def team_contacts(self):
        return self._call('sim_control_team_contacts')

    def finish(self):
        if self._open:
            self._call('sim_control_finish')
            self._open = False
            from .battle_visibility import flush_visibility
            requests = flush_visibility(self)
            if requests:
                self.runtime._probe_totals[0] += requests

    @observed('frontier.control_config')
    def _actor_config(self, source, tick):
        """Keep cold Bot mechanics out of repeated pose/visibility updates.

        A Bot descriptor and its spotting projection are immutable until the
        existing descriptor/crew owner replaces them. Critical payloads may
        be edited in place by a consumer, so retain a detached value snapshot
        instead of treating dictionary identity as an invalidation token.
        Human mechanics keep their existing per-slice client snapshot reader.
        """
        key = _key(source)
        cacheable = key[0] == 1
        runtime = self.runtime
        if cacheable:
            descriptor = runtime._descriptors.get(key[1])
            profile = runtime._spotting_profiles.get(('bot', key[1]))
            inputs = (source.get('team', 0), source.get('slot', 0),
                      source.get('view_range', 330.0),
                      runtime._vision_ranges.get(key[1]),
                      runtime.bot_crew_level(key[1]))
            critical = source.get('critical')
            cached = self._config_projections.get(key)
            if (cached is not None and descriptor is cached[0] and
                    profile is not None and profile is cached[1] and
                    inputs == cached[2] and critical == cached[3]):
                combat_count('frontier_control_projection_reused')
                return cached[4]
        result = self._project_actor_config(source, tick)
        if cacheable:
            self._config_projections[key] = (
                descriptor, runtime._spotting_profiles.get(('bot', key[1])),
                inputs, copy.deepcopy(critical), result)
        combat_count('frontier_control_projection_built')
        return result

    def _project_actor_config(self, source, tick):
        from gui.mods.offline_lan_0922 import bot_runtime as laws
        runtime = self.runtime
        key = _key(source)
        base, shot_factor, profile = runtime._spotting_profile(source, tick)
        moving_aspect = profile['invisibility_moving']
        still_aspect = profile['invisibility_still']
        relay = 0.0
        last_effort = designated = False
        if key[0] == 0:
            snapshot = laws._player_effective_params(source, tick)
            spotting_profile = snapshot['spotting']
            descriptor = runtime._player_vehicle_profile(source, tick)['descriptor']
            turret = laws._value(descriptor, 'turret', {}) or {}
            misc = laws._value(descriptor, 'miscAttrs', {}) or {}
            unused_key, dynamic = laws._player_dynamic_spotting(snapshot, source)
            devices, destroyed, unused_crew, yellow = laws._critical_parts(source)
            module_factor = laws.device_damage.module_stat_factor(
                devices, destroyed, descriptor, 'vision', yellow)
            damage = laws.device_damage.clamp_vision_factor(
                dynamic.get('vision', 1.0) * module_factor)
            kwargs = dict(misc_factor=(laws._value(
                misc, 'circularVisionRadiusFactor', 1.0) * damage),
                crew_factor=spotting_profile['vision_factor'],
                binocular_factor=spotting_profile['binocular_factor'])
            view_moving = laws.spotting.effective_view_range(
                laws._value(turret, 'circularVisionRadius', 330.0),
                binocular_active=False, **kwargs)
            view_still = laws.spotting.effective_view_range(
                laws._value(turret, 'circularVisionRadius', 330.0),
                binocular_active=spotting_profile['has_binoculars'], **kwargs)
            delay = (spotting_profile['binocular_delay']
                     if spotting_profile['has_binoculars'] else None)
            relay = laws.effective_params.living_skill_level(snapshot,
                'radioman_retransmitter', source.get('critical') or {}) * 0.001
            last_effort = laws._player_spotting_perk(
                snapshot, source, 'radioman_lasteffort')
            designated = laws._player_spotting_perk(
                snapshot, source, 'gunner_rancorous')
        else:
            view_moving, view_still, delay = runtime._vision_ranges.get(
                key[1], (source.get('view_range', 330.0),
                         source.get('view_range', 330.0), None))
            descriptor = runtime._descriptors.get(key[1])
            if descriptor is not None:
                factor = laws.device_damage.clamp_vision_factor(
                    laws._critical_factor(source, descriptor, 'vision'))
                view_moving *= factor
                view_still *= factor
        values = (int(source.get('team', 0)), int(source.get('slot', 0)),
            3.5, 1.7, float(view_moving), float(view_still),
            -1.0 if delay is None else float(delay), float(base[1]), float(base[0]),
            float(moving_aspect[0]), float(still_aspect[0] - moving_aspect[0]),
            float(moving_aspect[1]), float(still_aspect[1]), float(shot_factor),
            float(profile['camouflage_net_delay'])
            if profile['has_camouflage_net'] else -1.0,
            runtime._source_radio_range(source, tick), float(relay), 0.0)
        return values, bool(last_effort), bool(designated)

    @observed('frontier.control_sample')
    def _sample(self, source, key, last_effort=False, designated=False,
                refresh_schedule=True):
        runtime = self.runtime
        fire = runtime._visibility_fire_sequence(source)
        if refresh_schedule or key not in self._scheduling:
            due = (True if key[0] == 0 else
                   runtime._visibility_decision_due(source, self._now))
            selected = None if key[0] == 0 else runtime._selected_visibility_target(source)
            if selected is not None:
                selected = (0 if selected[0] == 'human' else 1, int(selected[1]))
            self._scheduling[key] = (due, selected)
        else:
            # Only begin() uses these fields to choose the render-frame cohort.
            # Actor deltas still update current pose, fire and terminal state.
            due, selected = self._scheduling[key]
        flags = (bool(source.get('alive', True)) | (last_effort << 1) |
                 (designated << 2) | (bool(due) << 3))
        return key, _pose(source), -1 if fire is None else int(fire), flags, selected

    @observed('frontier.control_template')
    def _install_template(self, source, key, processed=False):
        runtime = self.runtime
        if key[0] == 0:
            target = runtime._human_observation_target(
                source, self._visibility_tick, key[1])
        else:
            target = runtime._bot_observation_target(
                key[1], source, self._visibility_tick,
                set((key[1],)) if processed else None)
        if self._templates.get(key) is target:
            # Observation events only overlay pose fields on this template.
            # The cached metadata projection belongs to this exact phase.
            return
        self._templates[key] = target
        static = dict(target)
        for name in _POSE_NAMES:
            static.pop(name, None)
        static.update(position=(0.0, 0.0, 0.0), x=0.0, y=0.0,
                      z=0.0, yaw=0.0, speed=0.0)
        self._pose_free[key] = static

    def begin_frame(self):
        if self._open:
            raise RuntimeError('Previous native control frame was not finished')

    def begin(self, players, now, include_humans=True, visibility_tick=None):
        from gui.mods.offline_lan_0922 import bot_runtime as laws
        self._now = float(now)
        self._visibility_tick = (visibility_tick if isinstance(visibility_tick, dict)
                                 else {})
        self._templates = {}
        self._pose_free = {}
        self._sources = {}
        self._scheduling = {}
        rows, order = [], []
        for actor, raw in self.runtime.states.items():
            source = dict(raw, kind='bot', network_id=int(actor))
            key = (1, int(actor))
            self._sources[key] = source
            values, last_effort, designated = self._actor_config(source, self._visibility_tick)
            self.configure(key, values)
            rows.append(self._sample(source, key, last_effort, designated))
            order.append(key)
            if source.get('alive', True):
                self._install_template(source, key)
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            source = dict(raw, kind='human', network_id=int(raw['id']))
            key = _key(source)
            self._sources[key] = source
            values, last_effort, designated = self._actor_config(source, self._visibility_tick)
            self.configure(key, values)
            rows.append(self._sample(source, key, last_effort, designated))
            order.append(key)
            if source.get('alive', True):
                self._install_template(source, key)
            if source.get('alive', True):
                self.runtime._human_last_alive_critical[key[1]] = dict(
                    source.get('critical') or {})
        removed = set(self._present) - set(order)
        for key in removed:
            self._call('sim_control_forget', key)
            self._config.pop(key, None)
            self._config_projections.pop(key, None)
        self._present = tuple(order)
        self.update_samples(rows)
        self.begin_samples(order, now, laws.MAX_VISIBILITY_PROBES_PER_FRAME,
            callable(self.runtime.visibility_async_probe), include_humans)
        self.radio.refresh()
        if self.runtime._radio_network is self._old_radio:
            self.runtime._radio_network = self.radio
        self._include_humans = bool(include_humans)
        from .battle_visibility import bind_visibility
        self._sight_binding = bind_visibility(self)
        return True

    def update_actor(self, state, kind='bot', processed=True):
        source = dict(state, kind=kind)
        key = _key(source)
        source['network_id'] = key[1]
        values, last_effort, designated = self._actor_config(source, self._visibility_tick)
        self.configure(key, values)
        self._sources[key] = source
        self.update_samples((self._sample(
            source, key, last_effort, designated, refresh_schedule=False),))
        if source.get('alive', True):
            self._install_template(source, key, processed)
        else:
            self._templates.pop(key, None)
            self._pose_free.pop(key, None)
        from .battle_visibility import update_visibility
        update_visibility(self, key)

    def _sight(self, source_key, target_key, now, fire, detection, unused):
        runtime = self.runtime
        # Keep the exact legacy engine callback record. The native roster's
        # tagged copy is internal and must not widen a Bot source's schema.
        source = (runtime.states[int(source_key[1])] if source_key[0] == 1
                  else self._sources[tuple(source_key)])
        target = self._templates[tuple(target_key)]
        fired = bool(detection[5])
        started = runtime._probe_started()
        runtime._probe_totals[0] += 1
        try:
            if callable(runtime.visibility_async_probe):
                value = runtime.visibility_async_probe(source, target, fired, now,
                    None if fire < 0 else fire,
                    (detection[0], detection[1], tuple(detection[2:4]),
                     bool(detection[4]), fired, detection[6], detection[7], detection[8]))
                if value is None:
                    return (2, 0, 0, 0.0, now)
            else:
                try:
                    value = runtime.visibility_probe(source, target, fired)
                except TypeError:
                    value = runtime.visibility_probe(source, target)
            if isinstance(value, dict):
                return (0 if value.get('line_of_sight', False) else 1,
                        int('detected' in value), int(bool(value.get('detected', False))),
                        float(value.get('foliage_bonus', 0.0) or 0.0),
                        float(value.get('sampled_at', now)))
            return (0 if value else 1, 0, 0, 0.0, now)
        except Exception:
            return (3, 0, 0, 0.0, now)
        finally:
            runtime._probe_finished(0, started)

    @observed('frontier.control_events')
    def _sync_events(self, aggregate=None, team_visibility=None):
        runtime = self.runtime
        for source, target, sampled, duration, pose_row in self.observations():
            source, target = tuple(source), tuple(target)
            source_team = int(self._sources[source].get('team', 0))
            key = (source_team,) + _wire_key(target)
            pose = _pose_dict(pose_row)
            runtime._visible_target_poses[key] = pose
            runtime._spot_until[key] = max(runtime._spot_until.get(key, 0.0),
                                          sampled + min(12.0, max(0.0, duration)))
            # Keep only the Python dictionary's insertion history for handoff.
            # Its target payloads are exported once when detaching this owner.
            self._old_radio.observations.setdefault(_wire_key(source), {})
            if team_visibility is not None:
                team_visibility[key] = True
            if aggregate is not None:
                target_record = self._templates[target]
                entry = aggregate.setdefault(key, [False, set(), target_record, set(), set()])
                entry[0] = True
                entry[2] = target_record
                entry[2].update(pose)
                entry[3 if source[0] == 0 else 4].add(source[1])

    def append_human_observations(self, players, now, aggregate, team_visibility,
                                  visibility_tick=None):
        # One target record per human-observed team is sufficient for the
        # publication aggregate; per-human direct state stays in native memory.
        teams = set(int(source.get('team', 0)) for key, source in self._sources.items()
                    if key[0] == 0)
        for team in teams:
            for key, target in self._templates.items():
                if int(target.get('team', 0)) != team:
                    aggregate.setdefault((team,) + _wire_key(key),
                                         [False, set(), target, set(), set()])
        self.observe_humans(self._sight_binding or self._sight)
        self._sync_events(aggregate, team_visibility)
        if isinstance(visibility_tick, dict):
            vision_rows = [{'id': row[0][1], 'radius': row[5]}
                for row in self.radio.rows if row[0][0] == 0 and
                self._sources[tuple(row[0])].get('alive', True)]
            if vision_rows:
                visibility_tick.setdefault('player_vision_ranges', []).extend(vision_rows)
        return True

    @observed('frontier.control_contacts')
    def contacts_for(self, source, players, now, team_spotted=None,
                     visibility_tick=None, processed_bot_ids=None):
        # Current actor input may have changed during its preparation phase.
        # Earlier actors are updated separately after their committed motion.
        self.update_actor(source, source.get('kind', 'bot'), processed=False)
        rows = self.contacts(_key(source), self._sight_binding or self._sight)
        combat_count('frontier_contact_rows', len(rows))
        contacts, lookup = [], {}
        for key, flags, unused_remaining, unused_sampled, pose in rows:
            target = dict(self._pose_free[tuple(key)])
            if pose is not None:
                target.update(_pose_dict(pose))
            target.update(visible=bool(flags & 1), direct_visible=bool(flags & 2),
                          fresh_visible=bool(flags & 4))
            contacts.append(target)
            if target['visible']:
                lookup[target['id']] = target
        self._sync_events(team_visibility=team_spotted)
        return contacts, lookup

    @observed('frontier.control_export')
    def _export_perception(self, snapshot):
        runtime = self.runtime
        actor_rows, observations, unused_slots, visibility, teams, waiting, radio_rows, now = snapshot
        owners = set(_wire_key(row[0]) for row in observations)
        for key in tuple(self._old_radio.observations):
            if key not in owners:
                del self._old_radio.observations[key]
        for owner, targets in observations:
            self._old_radio.observations[_wire_key(owner)] = dict(
                (_wire_key(row[0]), (row[1], row[2], _pose_dict(row[3]), bool(row[4])))
                for row in targets)
        # Export unboosted actors so configure applies the relay bonus once.
        raw_actors = dict((_wire_key(row[0]),
            (row[1], tuple(row[2]), self._config[tuple(row[0])][15]) +
            ((row[4],) if row[0][0] == 0 else ())) for row in radio_rows)
        self._old_radio.configure(raw_actors, now)
        runtime._visibility_cache = dict(((_wire_key(row[0]) + _wire_key(row[1])),
            (row[2], bool(row[3]), None if row[4] < 0 else row[4])) for row in visibility)
        runtime._visibility_waiting = [_wire_key(row[0]) + _wire_key(row[1]) for row in waiting]
        runtime._visibility_inflight = set()
        runtime._visibility_frame = None
        runtime._spot_until = dict(((row[0],) + _wire_key(row[1]), row[2]) for row in teams)
        runtime._visible_target_poses = dict(((row[0],) + _wire_key(row[1]), _pose_dict(row[4])) for row in teams)
        for key, flags, times, fire, direct in actor_rows:
            identity = _wire_key(key)
            if flags & 2:
                runtime._visibility_still[identity] = times[0]
            else:
                runtime._visibility_still.pop(identity, None)
            if flags & 64:
                runtime._source_still[key[1] if key[0] else identity] = times[3]
            else:
                runtime._source_still.pop(key[1] if key[0] else identity, None)
            if flags & 4:
                runtime._visibility_fire[identity] = (fire, times[1])
            if key[0] == 0:
                runtime._human_observer_alive[key[1]] = bool(flags & 16)
                runtime._human_direct_targets[key[1]] = set(_wire_key(row) for row in direct)
                if times[2] > now:
                    runtime._human_vengeance_until[key[1]] = times[2]
                else:
                    runtime._human_vengeance_until.pop(key[1], None)

    def note_source_stillness(self, state, now):
        self._call('sim_control_source_still', _key(state), float(now),
                   float(state.get('speed', 0.0)))
        return True
