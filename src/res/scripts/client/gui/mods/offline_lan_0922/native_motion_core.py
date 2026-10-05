"""Persistent physical values; synchronous Python engine/effect frontiers.

This adapter does not select commands or own the context lifetime. Installation
and explicit external events write native state; ordinary ticks only read its
committed mirrors. Actor drive results are mirrored before the weapon boundary,
and the complete roster is mirrored after contact/support settlement.
"""
from __future__ import print_function

import math

from . import vehicle_physics as physics
from . import world_collision as world
from . import tank_collision
from . import prebaked_navigation
from .worker_diagnostics import observed

STATE_NAMES = (
    'terrain_pitch', 'speed', '_turn_speed', 'vertical_speed', 'push_x', 'push_z',
    'push_yaw', '_contact_forward_speed', 'last_drive_pitch', '_direction_command',
    '_drown_check', '_drown_time', '_water_depth', '_overturn_check',
    '_overturn_time', '_siege_time_left', '_siege_transition_total', 'movement_dir',
    'rotation_dir', '_contact_motor_turn', 'grind_ticks', 'siege_state',
    '_overturn_level', 'death_reason', 'team', 'destructible_contact_speed')
FLAG_NAMES = ('alive', 'airborne', 'grounded_once', 'service_brake',
              '_contact_dynamics', '_drowning', '_overturned',
              '_rotation_contact_blocked')
INTEGER_NAMES = frozenset(STATE_NAMES[17:25])
TUNING_NAMES = (
    'GRAVITY', 'GRAVITY_FACTOR', 'COHESION', 'POWER_FACTOR',
    'BKWD_POWER_FRACTION', 'ENGINE_MIN_V', 'DRIVE_TRACTION',
    'SLOPE_GRIP_LNG_FULL_Y', 'SLOPE_GRIP_LNG_MIN_Y', 'SLOPE_GRIP_LNG_FULL',
    'SLOPE_GRIP_LNG_MIN', 'SLOPE_GRIP_SDW_FULL_Y', 'SLOPE_GRIP_SDW_MIN_Y',
    'SLOPE_GRIP_SDW_FULL', 'SLOPE_GRIP_SDW_MIN', 'COH_DECAY_Y',
    'COH_DECAY_FACTOR', 'COH_DECAY_POW', 'SLOPE_COH_DECAY_Y', 'SLOPE_COH_DECAY',
    'COH_DECAY_BOUND', 'STEER_RESIST_MULT', 'COAST_BRAKE_SHARE', 'SLIDE_HOLD_TAN',
    'SLIDE_KINETIC', 'SLIP_THRESHOLD_TAN', 'SLIP_DRAG', 'OVERSPEED_MAX_FACTOR',
    'ANG_ACCELERATION_TIME', 'SPEED_AFFECT_ROT_DECREASE', 'GROUND_FOLLOW_BASE',
    'GROUND_FOLLOW_MIN', 'GROUND_FOLLOW_MAX', 'GROUND_PITCH_LIMIT',
    'HARD_CONTACT_BRAKE_DECAY', 'HARD_CONTACT_STOP_SPEED',
    'HARD_CONTACT_ENTRY_FACTOR', 'HARD_CONTACT_SLIDE_DECAY',
    'HARD_CONTACT_GRIND_TICKS', 'SERVER_PHYSICS_CONSTRAINT_ITERATIONS')


def _pose(state):
    return tuple(float(state.get(name, 0.0))
                 for name in ('x', 'y', 'z', 'yaw', 'pitch', 'roll'))


def _bound_owner(method):
    return getattr(method, '__self__', getattr(method, 'im_self', None))


class NativeMotion(object):
    def __init__(self, runtime, backend, context_handle):
        self.runtime = runtime
        self.backend = backend
        self.handle = int(context_handle)
        self.sequence = 0
        self.now = self.step = 0.0
        self.players = ()
        self.installed = set()
        self._world = None
        self._world_actor = None
        self._world_hard = False
        self.failures = []
        self.failure_count = 0
        self._reports = []
        self._commands = {}
        self._by_id = {}
        self._current_contacts = set()
        self._frame_armors = {}
        self.settlement = {}
        self.siege_locked = {}
        # Constructor only installs tuning in the unstarted native Store.
        # It never changes BotRuntime.states or the established live path.
        from . import bot_runtime as bot
        tail = (bot.BOT_DROWNING_PROBE_SECONDS, bot.BOT_DROWNING_SECONDS,
                bot.BOT_OVERTURN_IGNORE_SECONDS, bot.BOT_OVERTURN_DEATH_SECONDS,
                bot.BOT_OVERTURN_WARNING_COSINE, bot.BOT_OVERTURN_DANGER_COSINE)
        self._require('sim_motion_configure',
                      (tuple(float(getattr(physics, name))
                             for name in TUNING_NAMES) + tail,
                       tuple(physics.HARD_CONTACT_YAW_DELTAS)))

    def _require(self, method, *args):
        if self.runtime is None:
            raise RuntimeError('Native motion is detached')
        result = getattr(self.backend, method)(self.handle, *args)
        if result is None:
            # Entry may already have committed an effect. Never replay a full
            # physical stage in Python after an unsupported/error receipt.
            raise RuntimeError('%s failed; physical stage was not replayed' % method)
        return result

    def detach(self):
        self.runtime = None
        self._world = None
        self._by_id.clear()
        self.players = ()

    @observed('frontier.motion_pack')
    def _state(self, state):
        row = []
        for name in STATE_NAMES:
            if name == '_turn_speed':
                value = self.runtime._turn_speeds.get(int(state['id']), 0.0)
            elif name == 'grind_ticks':
                value = self.runtime._hard_contact_grinds.get(int(state['id']), 0)
            elif name == 'terrain_pitch':
                value = state.get(name, state.get('pitch', 0.0))
            elif name == '_water_depth':
                value = state.get(name, -1.0)
            else:
                value = state.get(name, 0.0)
            row.append(float(value))
        flags = sum(1 << index for index, name in enumerate(FLAG_NAMES)
                    if state.get(name, name == 'alive'))
        flags |= 512 if 'destructible_contact_speed' in state else 0
        presence = sum(1 << index for index, name in enumerate(STATE_NAMES + FLAG_NAMES)
                       if name in state and name not in ('_turn_speed', 'grind_ticks'))
        return (_pose(state), tuple(row), flags, float(presence))

    @observed('frontier.motion_mirror')
    def _mirror(self, bot_id, receipt):
        state = self.runtime.states[int(bot_id)]
        pose, values, flags, presence = receipt
        presence = int(presence)
        for name, value in zip(('x', 'y', 'z', 'yaw', 'pitch', 'roll'), pose):
            state[name] = value
        for index, (name, value) in enumerate(zip(STATE_NAMES, values)):
            if name == '_turn_speed':
                self.runtime._turn_speeds[int(bot_id)] = value
            elif name == 'destructible_contact_speed' and not flags & 512:
                state.pop(name, None)
            elif name == '_contact_motor_turn' and not value:
                state.pop(name, None)
            elif name == 'grind_ticks':
                if value or int(bot_id) in self.runtime._hard_contact_grinds:
                    self.runtime._hard_contact_grinds[int(bot_id)] = int(value)
            elif presence & (1 << index):
                state[name] = int(value) if name in INTEGER_NAMES else value
            else:
                state.pop(name, None)
        if flags & 256:
            state['_native_motion_failed'] = True
        else:
            state.pop('_native_motion_failed', None)
        for index, name in enumerate(FLAG_NAMES):
            if not presence & (1 << (26+index)):
                state.pop(name, None)
            else:
                state[name] = bool(flags & (1 << index))
        state['siege_time_left_ms'] = int(math.ceil(max(0.0, state.get('_siege_time_left', 0.0)) * 1000.0 - 1.0e-9)) if state.get('_siege_time_left', 0.0) > 0 else 0
        state['siege_transition_total_ms'] = int(math.ceil(max(0.0, state.get('_siege_transition_total', 0.0)) * 1000.0 - 1.0e-9)) if state.get('_siege_transition_total', 0.0) > 0 else 0
        return state

    def mirror(self, bot_id):
        return self._mirror(bot_id, self._require(
            'sim_motion_snapshot', (1, int(bot_id))))

    @observed('frontier.motion_descriptor')
    def _descriptor(self, bot_id, descriptor=None, revision=0):
        state = self.runtime.states[int(bot_id)]
        descriptor = descriptor or self.runtime._descriptors[int(bot_id)]
        from . import bot_runtime as bot
        params = (self.runtime._physics_params_for(int(bot_id)) if descriptor is self.runtime._descriptors[int(bot_id)] else bot._bot_physics_params(descriptor, self.runtime.bot_crew_level(int(bot_id))))
        bounds = world._vehicle_motion_bounds(descriptor)
        heights = world._vehicle_motion_heights(descriptor)
        if bounds is None:
            raise RuntimeError('Installed descriptor has no motion bounds')
        from . import bot_runtime as bot
        sensor = bot._water_sensor_geometry(descriptor)[0]
        sensor = sensor['position'] if isinstance(sensor, dict) else sensor
        if not isinstance(sensor, (tuple, list)):
            sensor = (sensor.x, sensor.y, sensor.z)
        from . import siege_mechanics
        limit = siege_mechanics.enabled_speed_limit(descriptor) or 0.0
        numbers = (params['mass'], params['powerW'], params['speedFwd'],
                   params['speedBwd'], params['rotSpd']) + tuple(params['terrainResist']) + (
                   params['specificFriction'], params['brakeDecel'],
                   params['trackCenter'], params.get('nativePowerRatio', 1.0),
                   limit, params['speedFwd'])
        detailed = self.runtime._suspension_params_for(int(bot_id)) is not None
        flags = int(bool(params.get('rotationIsAroundCenter', True))) | (2 if detailed else 0)
        return (int(revision), tuple(float(v) for v in numbers),
                tuple(state['collision_shape']), tuple(bounds) + tuple(heights),
                tuple(sensor), flags)

    def install(self, bot_id, revision=0, siege_descriptor=None):
        """Install at spawn/descriptor revision, never on every normal tick."""
        bot_id = int(bot_id)
        alternate = None
        pair = self.runtime._descriptor_pairs.get(bot_id)
        normal = pair[0] if pair else self.runtime._descriptors[bot_id]
        siege_descriptor = siege_descriptor or (pair[1] if pair else None)
        if siege_descriptor is not None:
            alternate = self._descriptor(bot_id, siege_descriptor, revision)
        self._require('sim_motion_install', (1, bot_id),
                      self._state(self.runtime.states[bot_id]),
                      self._descriptor(bot_id, normal, revision), alternate)
        self.installed.add(bot_id)

    def install_all(self):
        for bot_id in sorted(self.runtime.states):
            self.install(bot_id)

    def patch(self, bot_id):
        """Apply one explicit authority/impulse/critical event before begin."""
        bot_id = int(bot_id)
        self._require('sim_motion_patch', (1, bot_id),
                      self._state(self.runtime.states[bot_id]))

    def patch_external(self, bot_id, field_groups):
        """Apply explicit events, preserving every unrelated native history."""
        masks = {'pose': 1, 'velocity': 2, 'terminal': 4, 'hydraulic': 8, 'siege': 16}
        mask = 0
        for name in field_groups:
            mask |= masks[name]
        self._require('sim_motion_patch_fields', (1, int(bot_id)), mask,
                      self._state(self.runtime.states[int(bot_id)]))

    def after_weapon(self, bot_id):
        state = self.runtime.states[int(bot_id)]
        self._require('sim_motion_after_weapon', (1, int(bot_id)),
            (float(state.get('pitch', 0.0)),
             float(state.get('terrain_pitch', state.get('pitch', 0.0))),
             int(bool(state.get('alive', True))), int(state.get('death_reason', 0)),
             int('death_reason' in state)))

    def descriptor_patch(self, bot_id, revision=0):
        bot_id = int(bot_id)
        state = self.runtime.states[bot_id]
        pair = self.runtime._descriptor_pairs.get(bot_id)
        normal = pair[0] if pair else self.runtime._descriptors[bot_id]
        alternate = self._descriptor(bot_id, pair[1], revision) if pair and pair[1] is not None else None
        self._require('sim_motion_descriptor', (1, bot_id),
            self._descriptor(bot_id, normal, revision), alternate,
            (int(state.get('siege_state', 0)), float(state.get('_siege_time_left', 0.0)),
             float(state.get('_siege_transition_total', 0.0))))

    def begin_slice(self, step, now, actor_order=None, players=()):
        if not self.installed:
            self.install_all()
        self.step, self.now, self.players = float(step), float(now), players or ()
        self.sequence += 1
        lifetime = self.backend.sim_lifetime(self.handle)
        token = (int(lifetime[0]), int(lifetime[1]), self.sequence,
                 float(round(self.now * 1000000.0)))
        order = actor_order if actor_order is not None else list(self.runtime.states)
        human_rows = []
        from .bot_runtime import HUMAN_TARGET_ID_BASE
        for raw in self.players:
            profile = self.runtime._player_collision_profile(raw)
            alive = bool(raw.get('alive', True))
            speed = raw.get('speed', 0.0) if alive else 0.0
            yaw = raw.get('yaw', 0.0)
            grip = physics.contact_push_decel(
                profile['physics'], bool(speed or raw.get('forward')),
                normal_y=math.cos(raw.get('pitch', 0.0))*math.cos(raw.get('roll', 0.0)))
            flags = 16 | 32 | 64 | (1 if alive else 2) | (4 if 'tank_pushes' not in raw else 8)
            human_rows.append((HUMAN_TARGET_ID_BASE + int(raw['id']),
                int(raw.get('team', 0)), flags, _pose(raw),
                (profile['mass'], math.sin(yaw)*speed, 0.0, math.cos(yaw)*speed,
                 raw.get('push_yaw', 0.0), grip[0], grip[1], 0.0, 0.0),
                tuple(profile['shape'])))
        self.runtime._contact_players = self.players
        self.runtime._contact_now = self.now
        self._reports = []
        settle_order = [int(state['id']) for state in self.runtime._ordered_states()]
        by_id = dict((actor, None) for actor in settle_order)
        for row in human_rows:
            by_id[row[0]] = None
        self._require('sim_motion_begin', token, self.step, self.now,
                      tuple((1, int(actor)) for actor in order), tuple(human_rows),
                      tuple(by_id), tuple((1, actor) for actor in settle_order))

    def prepare_actor(self, bot_id):
        state = self.runtime.states[int(bot_id)]
        self.siege_locked[int(bot_id)] = bool(state.get('alive', True) and
                                              int(state.get('siege_state', 0)) in (1, 3))
        return self._mirror(bot_id, self._require(
            'sim_motion_prepare', (1, int(bot_id)), self.dispatch))

    def advance_actor(self, bot_id, throttle, turn, slope_pitch, travel_sign=1.0,
                      path_clear=True, pose_frozen=False, generic_collision=False,
                      exact_collision=True, allow_shallow=False, wet_escape=False,
                      move_position=None, siege_locked=False):
        self._commands[int(bot_id)] = move_position
        trace = self.runtime.states[int(bot_id)].get('_motion_stall_pending')
        flags = (int(path_clear) | (int(pose_frozen) << 1) |
                 (int(generic_collision) << 2) | (int(exact_collision) << 3) |
                 (int(allow_shallow) << 4) | (int(wet_escape) << 5) |
                 (int(siege_locked) << 6) | (int(trace is not None) << 7))
        command = ((float(throttle), float(turn), float(slope_pitch), float(travel_sign)), flags)
        receipt = self._require('sim_motion_advance', (1, int(bot_id)), command, self.dispatch)
        if trace is not None:
            receipt, values = receipt
            names = ('drive_speed', 'drive_pitch', 'throttle', 'baked_veto',
                     'path_clear', 'frozen', 'world_status', 'hard_contact',
                     'integrated', 'world_speed')
            trace.update(zip(names, values))
            trace['world_status'] = ('clear', 'hard', 'soft', 'crushed')[int(values[6])]
            for name in ('baked_veto', 'path_clear', 'frozen', 'hard_contact'):
                trace[name] = bool(trace[name])
            params = self.runtime._physics_params_for(bot_id)
            trace.update(dt=self.step, mass=params['mass'], powerW=params['powerW'],
                         nativePowerRatio=params.get('nativePowerRatio', 1.0),
                         terrainResist=params['terrainResist'],
                         specificFriction=params['specificFriction'])
        self.siege_locked[int(bot_id)] |= bool(siege_locked)
        return self._mirror(bot_id, receipt)

    def settle_roster(self):
        self.settlement = {}
        for key, receipt, metadata in self._require('sim_motion_settle', self.dispatch):
            if key[0] == 1:
                self._mirror(key[1], receipt)
                flags, settled, after_contacts, support = metadata
                self.settlement[key[1]] = dict(vertical_cohort=bool(flags & 1),
                    support_blocked=bool(flags & 2), pose_rollback=bool(flags & 4),
                    ballistic=bool(flags & 8), siege_locked=bool(flags & 16),
                    settled_pose=settled, after_contacts=after_contacts)
                trace = self.runtime.states[key[1]].get('_motion_stall_pending')
                if trace is not None and flags & 1:
                    trace['after_contacts'] = after_contacts
                    if support is not None:
                        trace.update(suspension=False, support_centre=support[0],
                                     support_highest=support[1], grounded_before=bool(support[2]))
                        if support[3] is not None:
                            trace.update(support_limit=support[3],
                                         support_rise_obstacle=bool(support[4]),
                                         support_rise_continuous=bool(support[5]))
        self.runtime._ram_contacts = frozenset(self._current_contacts)
        return list(self._reports)

    def _owner(self):
        owner = _bound_owner(self.runtime.motion_resolver)
        if owner is None or getattr(owner, '_runtime', None) is None:
            raise RuntimeError('Native motion requires the real main-thread world owner')
        return owner

    def dispatch(self, op, row):
        try:
            return self._dispatch(op, row)
        except Exception as error:
            # Return an explicit failed operation, never a fabricated miss or
            # hit. The native actor operation contains it without replaying
            # already committed engine/destruction effects.
            self.failure_count += 1
            self.failures.append((self.sequence, int(op), str(error)))
            del self.failures[:-32]
            return (-999,)

    def _dispatch(self, op, row):
        if op <= 6:
            if self._world is None:
                raise RuntimeError('World query outside its ordered frontier')
            return self._world(op, row)
        if op == 21:
            if self._world_actor is not None and not self._world_hard:
                self.runtime.states[self._world_actor].pop('_world_contact_trace', None)
            self._world = None
            return None
        if op == 23:
            self._begin_contacts(row)
            return None
        bot_id = int(row[0][1])
        state = self.runtime.states[bot_id]
        # Pure query rows carry their actual consumer inputs. Only effects
        # and commit frontiers need a fresh, writable full actor mirror.
        if op in (14, 17, 18, 19, 22):
            state = self.mirror(bot_id)
        if op == 10:
            return tuple(self.runtime._ground_probe_at(point[0], point[2], point[1])
                         for point in row[1])
        if op == 11:
            a, b = row[1:3]
            clear = self.runtime._baked_pose_progress_clear(state, a[:3], a[3], b[:3], b[3])
            if clear and row[3] and prebaked_navigation.pose_is_safe(
                    self.runtime.baked_graph, a[:3], shoulder_cells=0,
                    hazard_mask=prebaked_navigation.MOTION_FATAL_HAZARDS):
                clear = prebaked_navigation.pose_is_safe(
                    self.runtime.baked_graph, b[:3], shoulder_cells=0,
                    hazard_mask=prebaked_navigation.MOTION_FATAL_HAZARDS)
            return int(clear)
        if op == 12:
            a, b = row[1:3]
            pitch, roll, terrain_pitch, has_terrain_pitch = row[4]
            orientation = dict(id=bot_id, pitch=pitch, roll=roll)
            if has_terrain_pitch:
                orientation['terrain_pitch'] = terrain_pitch
            elif 'suspension_pitch' in state:
                orientation['suspension_pitch'] = state['suspension_pitch']
            return int(self.runtime._turret_pose_is_clear(
                orientation, a[:3], a[3], b[:3], b[3]))
        if op == 13:
            from .bot_runtime import BOT_WATER_AVOID_DEPTH, BAKED_SHALLOW_WATER
            flags = row[4]
            position, yaw = row[1][:3], row[2]
            grid = getattr(self.runtime.navigator, 'grid', None)
            hazard = getattr(grid, 'point_has_baked_hazard', None)
            escape = bool(flags & 1 or row[5] >
                          BOT_WATER_AVOID_DEPTH or callable(hazard) and
                          hazard(position, BAKED_SHALLOW_WATER))
            shallow = bool(flags & 2)
            for name in ('controlled_shallow_step', 'controlled_shallow_committed'):
                admitted = getattr(self.runtime.navigator, name, None)
                if not shallow and callable(admitted):
                    shallow = bool(admitted(bot_id, position, yaw))
            return int(self.runtime._planner_corridor_clear(
                position, yaw, row[3], wet_escape=escape,
                allow_shallow=shallow, hazard_only=True) is not False)
        if op == 14:
            a, b = row[1:3]
            travel = math.hypot(b[0]-a[0], b[2]-a[2])
            return int(self.runtime._support_rise_follows_tick_path(state, row[3], a[:3], travel))
        if op == 15:
            probe = self.runtime._water_depth_probe
            return float(probe(row[1][:3])) if callable(probe) else -1.0
        if op == 16:
            return int(prebaked_navigation.pose_is_safe(
                self.runtime.baked_graph, row[1][:3], shoulder_cells=0,
                hazard_mask=prebaked_navigation.MOTION_FATAL_HAZARDS))
        if op == 17:
            kind, value = row[1:3]
            if kind == 1:
                self.runtime._apply_bot_landing_impact(state, value)
            elif kind == 2:
                self.runtime._apply_world_contact_impact(state, value, self.now)
            elif kind in (3, 4):
                from . import bot_runtime as bot
                critical = bot._terminal_critical(state, self.runtime._descriptors[bot_id], 'drowning' if kind == 3 else 'overturn')
                if critical is not None:
                    state['critical'] = critical
                state['display_health'] = max(0, int(state.get('health', 0))) if kind == 3 else 0
                state['health'] = 0
                state['alive'] = False
                state['speed'] = 0.0
                state['movement_dir'] = state['rotation_dir'] = 0
                if kind == 3:
                    state['_drowned'] = True
                state['target_kind'] = state['target_id'] = None
                self.runtime._friendly_repositions.pop(bot_id, None)
            elif kind == 5:
                self.runtime._invalidate_realised_motion(bot_id, value)
            elif kind == 6:
                self.runtime._install_bot_descriptor(bot_id, state, int(value))
                state['_siege_intent'] = int(value) == 2
                state['_siege_intent_elapsed'] = 0.0
            return self._state(state)
        if op == 24:
            status, initial, final, hard, original, contact_yaw, resolved = row[1:]
            target = self._commands.get(bot_id)
            if hard and target is not None:
                grid = getattr(self.runtime.navigator, 'grid', None)
                cell = float(getattr(grid, 'cell_size', 0.0))
                if cell > 0.:
                    target = (original[0]+math.sin(contact_yaw)*cell,
                              original[1], original[2]+math.cos(contact_yaw)*cell)
                report = getattr(self.runtime.navigator, 'report_blocked_step', None)
                if callable(report):
                    review = getattr(self.runtime.navigator, 'report_blocked_plan', None)
                    if callable(review):
                        review(original[:3], target)
                    report(bot_id, original[:3], target, self.now)
            if resolved and callable(self.runtime.motion_report):
                self.runtime.motion_report(bot_id, ('clear','hard','soft','crushed')[status],
                                           initial, final)
            return None
        if op == 18:
            return self._catalog(bot_id, state, row)
        if op == 19:
            a, b, dt, movement = row[1:]
            probe = self.runtime._wreck_rotation_probe
            return int(callable(probe) and probe(
                bot_id, a[:3], a[3], b[3], self.runtime._descriptors[bot_id],
                dt, self.now, 0.0, translation=movement))
        if op == 20:
            owner = self._owner()
            pose, speed, dt, motion_yaw, flags = row[1:6]
            state.pop('_world_contact_trace', None)
            self._world_actor, self._world_hard = bot_id, False
            travel_yaw = motion_yaw if motion_yaw is not None else pose[3] if speed >= 0 else pose[3]+math.pi
            reusable = getattr(self.runtime, 'motion_world_corridor_reusable', None)
            if not callable(reusable):
                reusable = getattr(self.runtime, 'motion_world_receipt_reusable', None)
            if (flags & 8 and flags & 2 and owner._destructibles is not None and
                    not flags & 4 and row[8] == 0 and
                    abs(row[9]) <= .01 and
                    callable(reusable) and reusable(bot_id, pose[:3], travel_yaw, speed, self.now, dt) and
                    not owner._destructibles._catalog_hull_contact(
                        owner._vector(pose[:3]), pose[3], speed,
                        self.runtime._descriptors[bot_id], dt,
                        pitch=pose[4], roll=pose[5],
                        **({'motion_yaw': motion_yaw} if motion_yaw is not None else {}))):
                return 1
            self._world = self._world_dispatch(bot_id, state, row)
            return 0
        if op == 22:
            return self._finalize_contact(state, row[1])
        raise RuntimeError('Unknown persistent motion frontier %s' % op)

    def _begin_contacts(self, rows):
        runtime = self.runtime
        self._by_id = {}
        self._current_contacts = set()
        self._frame_armors = {}
        self._reports = runtime._resolve_human_ram_receipts(self.players, self.now)
        from .bot_runtime import HUMAN_TARGET_ID_BASE
        raw_players = dict((HUMAN_TARGET_ID_BASE + int(p['id']), p) for p in self.players)
        for row in rows:
            actor, team, flags, pose, values, shape, unused = row
            source = runtime.states.get(actor) or raw_players.get(actor, {})
            profile = (source.get('ram_profile') if actor in runtime.states else
                       runtime._player_collision_profile(source)['ram_profile'])
            self._by_id[actor] = dict(id=actor, team=team, alive=bool(flags & 1),
                kind='player' if flags & 16 else 'bot',
                network_id=int(source['id']), vehicle=str(source.get('vehicle') or ''),
                x=pose[0], y=pose[1], z=pose[2], yaw=pose[3], pitch=pose[4], roll=pose[5],
                mass=values[0], vx=values[1], vy=values[2], vz=values[3],
                push_yaw=values[4], contact_decel=values[5:7],
                traverse_speed=values[7], traverse_torque=values[8], shape=shape,
                ram_profile=profile, impulse=bool(flags & 4),
                position_fixed=bool(flags & 8), immovable=bool(flags & 2))

    def _finalize_contact(self, state, row):
        runtime = self.runtime
        own = self._by_id[int(state['id'])]
        if not row[4]:
            return ()
        def armor(first, second, contact):
            first_id, second_id = int(first['id']), int(second['id'])
            pair = (min(first_id, second_id), max(first_id, second_id))
            if pair not in self._frame_armors:
                value = runtime.ram_contact_probe(first, second, contact)
                if value is None:
                    self._frame_armors[pair] = None
                elif isinstance(value, (tuple, list)) and len(value) == 2:
                    self._frame_armors[pair] = tuple(value) if first_id <= second_id else tuple(reversed(value))
                else:
                    return value
            value = self._frame_armors[pair]
            return value if value is None or first_id <= second_id else tuple(reversed(value))
        kwargs = {'now': None}
        if state.get('alive', True):
            kwargs.update(now=self.now, ram_cooldowns=runtime._ram_cooldowns,
                active_ram_contacts=frozenset(set(runtime._ram_contacts) | self._current_contacts))
            if runtime.ram_contact_probe is not None:
                kwargs['contact_armor_probe'] = armor
        result = tank_collision.finalize_contact_row(own, self._by_id, row, **kwargs)
        if state.get('alive', True):
            runtime._ram_cooldowns = result['cooldowns']
            self._current_contacts.update(result['contacts'])
            self._reports.extend(runtime._ram_reports(state, result['ram_events']))
            if (state.pop('_rotation_contact_blocked', False) or
                    any(abs(value) > .0001 for value in row[1]) or
                    any(abs(value) > .0001 for value in row[2])):
                runtime._record_traffic_wait_contact(int(state['id']), self.step)
        return tuple(result['contacts']) if state.get('alive', True) else ()

    def _catalog(self, bot_id, state, row):
        owner = self._owner()
        sensor = owner._destructibles
        if sensor is None:
            # No catalog may confirm a native kinetic candidate.
            return (-1, 0)
        if state.get('airborne', False):
            return (0, 0)
        pose, speed, motion_yaw, dt, passive, commit = row[1:]
        descriptor = self.runtime._descriptors[bot_id]
        kinetic = self._world_kinetic
        detail = sensor._catalog_motion_blocked(
            owner._avatar.spaceID, owner._vector(pose[:3]), pose[3], speed,
            descriptor, self.now, dt=dt, kinetic_speed=kinetic,
            return_detail=True, kinetic_commit=bool(not passive and commit and
                not state.get('airborne') and state.get('movement_dir', 0)*speed > 0),
            commit_enabled=bool(commit), pitch=state.get('terrain_pitch', pose[4]),
            roll=pose[5], **({'motion_yaw': motion_yaw} if passive else {}))
        if isinstance(detail, bool):
            detail = {'status': 'hard' if detail else 'clear'}
        elif isinstance(detail, str):
            detail = {'status': detail}
        if not isinstance(detail, dict):
            raise RuntimeError('Catalog motion detail is unavailable')
        status = detail.get('status')
        statuses = {'clear': 0, 'hard': 1, 'soft': 2, 'crushed': 3, 'approach': 0}
        if status not in statuses:
            raise RuntimeError('Invalid catalog motion status')
        accepted = bool(detail.get('accepted_now'))
        if detail.get('used_kinetic_speed') and not (accepted and status == 'crushed' and detail.get('token')):
            raise RuntimeError('Inconsistent cap-crush receipt')
        if accepted and status in ('clear', 'approach', 'soft'):
            raise RuntimeError('Inconsistent contact receipt')
        owner._bot_motion_kinds[bot_id] = str(detail.get('kinds', '-'))
        return (statuses[status], int(accepted))

    def _world_dispatch(self, bot_id, state, row):
        owner = self._owner()
        V = owner._runtime.math.Vector3
        engine = owner._runtime.bigworld
        space = owner._avatar.spaceID
        descriptor = self.runtime._descriptors[bot_id]
        pose, speed, dt, motion_yaw, flags, unused_cap, unused_revision = row[1:8]
        pos, yaw = V(*pose[:3]), pose[3]
        pitch, roll = pose[4:]
        commit, active = bool(flags & 1), bool(flags & 2)
        bounds = world._vehicle_motion_bounds(descriptor)
        heights = world._vehicle_motion_heights(descriptor)
        trace = {}
        state['_world_contact_trace'] = trace
        trace.update(position=tuple(pose[:3]), yaw=yaw, speed=speed, dt=dt,
                     motion_yaw=motion_yaw, pitch=pitch, roll=roll,
                     airborne=bool(flags & 4),
                     extents=(max(abs(bounds[0]), abs(bounds[1])), bounds[2], bounds[3]),
                     lateral_bounds=bounds[:2])
        self._world_kinetic = owner._destructible_drive_speed_cap(
            descriptor, physics.derive_params(descriptor), speed,
            owner._bot_destructible_travel_descriptor(bot_id)) if active else None
        hits, collision, crush = {}, [world._UNPREPARED_COLLISION_FILTER, None], [False]
        def xyz(value):
            return (value.x, value.y, value.z)
        def dispatch(op, rows):
            if op == 1:
                a, b = rows[0]
                collision[0] = world._trace_collision_filter(
                    world.prepare_horizontal_collision_filter(V(*a), V(*b)), trace)
                return None
            if op == 2:
                v = rows[0]
                collision[1] = world._translation_departing_contact(
                    pos, yaw, bounds, v[3:6], v[0], v[1], heights, dy=v[2],
                    pose_axes=(v[6:9], v[9:12], v[12:15]) if motion_yaw is not None else None)
                return None
            if op == 5:
                a, b, handle = rows[0]
                result = world._destroy_and_recast(space, V(*a), V(*b), hits[handle],
                    yaw, speed, descriptor, crush, active, self._world_kinetic, commit, collision[0])
                return (2 if result == 'kinetic' else 1 if result is True else 0,)
            if op == 6:
                a, b, handle, reason, ground_ahead, profile = rows[0]
                self._world_hard = True
                world._record_hard_contact(trace, ('', 'raised_wall', 'ground_profile',
                    'solid_lane', 'upper_lane')[reason], V(*a), V(*b), hits[handle], ground_ahead, profile)
                return None
            result, stopped = [], False
            for a, b, query_flags in rows:
                if stopped or query_flags & 4:
                    result.append((None, None, 0, a, b))
                    stopped = stopped or (op == 4 and bool(query_flags & 1))
                    continue
                start, end = V(*a), V(*b)
                if op == 3:
                    hit = world._collide_horizontal(space, start, end, collision[0],
                        collision[1] if query_flags & 1 else None)
                else:
                    query_filter = collision[0]
                    if query_filter is world._UNPREPARED_COLLISION_FILTER:
                        query_filter = world.ground_collision_filter(a[0], a[2])
                    try:
                        hit = world.collide_motion_segment(space, start, end, query_filter,
                            engine.wg_collideSegment, 'native.motion.ground')
                    except (AttributeError, IndexError, TypeError, ValueError):
                        hit = None
                if hit is None:
                    result.append((None, None, 0, xyz(start), xyz(end)))
                    stopped = stopped or (op == 4 and bool(query_flags & 1))
                else:
                    handle = len(hits) + 1
                    hits[handle] = hit
                    normal = xyz(hit[1]) if len(hit) > 1 else (0.0, 0.0, 0.0)
                    result.append((xyz(hit[0]), normal, handle, xyz(start), xyz(end)))
            return tuple(result)
        return dispatch
