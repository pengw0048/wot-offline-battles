"""Persistent native weapon owner and detached Python observation mirrors.

The shared NativeSimulation owns context lifetime. Descriptor readers execute
only at installation or mode changes. Production uses prepare/after_motion and
atomic commit; the individual facade methods support external control and
inspection, not a second Python simulation path.
"""
from __future__ import print_function

import math
import random
import sys


CONFIG_FIELDS = (
    'fully_aimed_dispersion', 'after_shot', 'after_shot_in_burst',
    'turret_dispersion_factor', 'aiming_time', 'movement_dispersion_factor',
    'rotation_dispersion_factor', 'reload_full', 'reload_intra',
    'clip_size', 'shell_count', 'burst_count', 'burst_interval',
)
GUN_FIELDS = (
    'clip', 'elapsed', 'reload_duration', 'reload_kind', 'reload_factor',
    '_burst_remaining', 'current_dispersion_factor', 'aiming_start_factor',
    'aiming_elapsed', 'dispersion', 'motion_dispersion_squared',
)
BURST_FIELDS = (
    'active', 'group_seq', 'count', 'next_index', 'interval',
    'time_left', 'shell_index',
)
TARGET_KINDS = ('', 'bot', 'human')
CATEGORIES = ('standard', 'premium', 'he')


def _gun_row(gun):
    values = [getattr(gun, name) for name in GUN_FIELDS]
    values[3] = int(values[3] == 'intra')
    return tuple(values)


def _ammo_row(ammo):
    return (tuple(CATEGORIES.index(ammo.categories[index])
                  for index in range(ammo.shell_count)),
            tuple(ammo.remaining), int(ammo.loaded), int(ammo.next),
            int(ammo.reload_pending), int(ammo.plan_pending))


def _edge(row):
    return dict(shot_seq=int(row[0]), burst_group_seq=int(row[1]),
                burst_index=int(row[2]), burst_count=int(row[3]),
                shell_index=int(row[4]), final=bool(row[5]),
                due_offset=float(row[6]))


def _edge_row(edge):
    return (int(edge['shot_seq']), int(edge['burst_group_seq']),
            int(edge['burst_index']), int(edge['burst_count']),
            int(edge['shell_index']), int(bool(edge['final'])),
            float(edge.get('due_offset', 0.0)))


def _proof_row(proof):
    if proof is None:
        return ()
    if len(proof) != 13 or proof[0] != 'launch' or len(proof[6]) != 3:
        raise ValueError('artillery launch proof has an invalid shape')
    return (int(proof[1]), TARGET_KINDS.index(proof[2]), int(proof[3]),
            int(proof[4]), int(proof[5])) + tuple(proof[6]) + tuple(proof[7:])


def _proof_value(row):
    return ('launch', int(row[0]), TARGET_KINDS[int(row[1])], int(row[2]),
            int(row[3]), int(row[4]), tuple(row[5:8])) + tuple(row[8:])


def _text_row(value):
    return tuple(bytearray(value.encode('utf-8')))


def _text_value(row):
    value = ''.join(chr(int(byte)) for byte in row)
    return value if sys.version_info[0] < 3 else bytes(bytearray(row)).decode('utf-8')


def _launch_row(record, edge=None):
    if edge is None:
        index = int(record.get('burst_index', 0))
        count = int(record.get('burst_count', 1))
        edge = dict(shot_seq=int(record['fire_seq']),
                    burst_group_seq=int(record.get('burst_group_seq',
                                                  record['fire_seq'])),
                    burst_index=index, burst_count=count,
                    shell_index=int(record['shell_index']),
                    final=index + 1 >= count, due_offset=0.0)
    return (_edge_row(edge), float(record['launch_time_us']),
            tuple(record['launch_pose']), float(record['shot_yaw']),
            float(record['shot_pitch']), _text_row(record['class_tag']),
            record.get('shot_origin'), record.get('shot_velocity'),
            (float(record.get('shot_gravity', 0.0)),
             float(record.get('shot_max_distance', 0.0)),
             int(record.get('shot_max_time_ms', 0))),
            int('shells_before_shot' in record),
            int(record.get('shells_before_shot', 0)),
            _proof_row(record.get('shot_proof_key')))


def _launch_value(row):
    edge = _edge(row[2])
    record = dict(id=int(row[1]), fire_seq=edge['shot_seq'],
                  shell_index=edge['shell_index'], shot_yaw=row[5],
                  shot_pitch=row[6], class_tag=_text_value(row[7]),
                  burst_group_seq=edge['burst_group_seq'],
                  burst_index=edge['burst_index'], burst_count=edge['burst_count'],
                  launch_time_us=int(row[3]), launch_pose=tuple(row[4]))
    if row[8] is not None:
        record['shot_origin'] = tuple(row[8])
    if row[9] is not None:
        record.update(shot_velocity=tuple(row[9]), shot_gravity=row[10][0],
                      shot_max_distance=row[10][1],
                      shot_max_time_ms=int(row[10][2]),
                      shot_proof_key=_proof_value(row[13]))
    if row[11]:
        record['shells_before_shot'] = int(row[12])
    return record


class NativeWeapons(object):
    """One weapon store inside a caller-owned shared simulation context."""

    def __init__(self, runtime, backend, context_handle):
        self.runtime = runtime
        self.backend = backend
        self.context_handle = context_handle
        self.actors = {}
        self.detached = False

    def _call(self, method, *args):
        if self.detached:
            raise RuntimeError('native weapon owner is detached')
        result = getattr(self.backend, method)(self.context_handle, *args)
        if result is None:
            raise RuntimeError('%s rejected native weapon input' % method)
        return result

    def install(self, actor_id, gun, ammo, burst, state=None, publish=True):
        actor_id = int(actor_id)
        if actor_id in self.actors:
            return self.actors[actor_id]
        if state is None:
            state = self.runtime.states[actor_id]
        config = tuple(getattr(gun, name) for name in CONFIG_FIELDS)
        row = self._call('sim_weapon_install', 1, actor_id, config,
                         _ammo_row(ammo), int(state.get('fire_seq', 0)), 1.0)
        actor = _Actor(self, actor_id, gun, ammo, burst)
        self.actors[actor_id] = actor
        actor.sync(row)
        # Migrate the exact existing clocks, including a partially aimed gun.
        actor.command(27, _gun_row(gun))
        actor.command(18, (tuple(getattr(burst, name) for name in BURST_FIELDS),
                           int(state.get('fire_seq', 0))))
        actor.command(23, (float(state.get('turret_yaw', 0.0)),
                           float(state.get('gun_pitch', 0.0)),
                           float(state.get('desired_gun_pitch', 0.0)),
                           int(bool(state.get('gun_aligned', False)))))
        descriptor = getattr(self.runtime, '_descriptors', {}).get(actor_id)
        if descriptor is not None:
            self.configure_aim_from_descriptor(actor_id, descriptor)
        if publish:
            self._publish_actor(actor)
        return actor

    def _publish_actor(self, actor):
        self.runtime._gun_states[actor.actor_id] = actor.gun
        self.runtime._ammo_states[actor.actor_id] = actor.ammo
        self.runtime._burst_states[actor.actor_id] = actor.burst

    def install_all(self):
        if self.actors:
            raise RuntimeError('native weapon inventory is already installed')
        try:
            for actor_id in sorted(self.runtime._gun_states):
                if actor_id not in self.runtime._ammo_states:
                    continue
                if actor_id not in self.runtime._burst_states:
                    continue
                self.install(actor_id, self.runtime._gun_states[actor_id],
                             self.runtime._ammo_states[actor_id],
                             self.runtime._burst_states[actor_id], publish=False)
            # Preserve the admitted outbox before publishing any facade alias.
            for launch in getattr(self.runtime, '_pending_launches', ()):
                self.enqueue(launch)
        except Exception:
            # The enclosing shared owner closes this failed context. No
            # Python runtime reference has observed the partial inventory.
            self.detached = True
            self.actors.clear()
            raise
        for actor in self.actors.values():
            self._publish_actor(actor)
        return self

    def prepare(self, actor_id, dt, reload_factor, requested_shell, state=None):
        actor = self.actors[int(actor_id)]
        actor.command(19, (dt, reload_factor, int(requested_shell)))
        if state is not None:
            actor.ammo.publish(state)
        return actor.gun, actor.ammo, actor.burst

    def install_aim(self, actor_id, minimum_curve, maximum_curve,
                    turret_speed, gun_speed, static_pitch, fixed_limits=None):
        self.actors[int(actor_id)].command(24, (
            (tuple(tuple(value) for value in (minimum_curve or ())),
             tuple(tuple(value) for value in (maximum_curve or ())),
             turret_speed, gun_speed, int(static_pitch is not None),
             0.0 if static_pitch is None else static_pitch,
             None if fixed_limits is None else tuple(fixed_limits)),))

    def configure_aim_from_descriptor(self, actor_id, descriptor, force=False):
        """Install the existing reader's exact envelope at a mode boundary."""
        from . import bot_runtime
        actor = self.actors[int(actor_id)]
        if actor.aim_descriptor is descriptor and not force:
            return actor.valid_pitch
        gun = bot_runtime._value(descriptor, 'gun', {}) or {}
        turret = bot_runtime._value(descriptor, 'turret', {}) or {}
        limits = bot_runtime._value(gun, 'pitchLimits')
        # The source reader is the authority for missing/malformed envelopes.
        sampled = bot_runtime._gun_pitch_limits(descriptor, 0.0)
        has_curves = bool(isinstance(limits, dict) and
                          'minPitch' in limits and 'maxPitch' in limits)
        minimum = limits['minPitch'] if sampled is not None and has_curves else ()
        maximum = limits['maxPitch'] if sampled is not None and has_curves else ()
        fixed = sampled if sampled is not None and not has_curves else None
        self.install_aim(actor_id, minimum, maximum,
                         bot_runtime._rotation_speed(turret, 0.5),
                         bot_runtime._rotation_speed(gun, 0.35),
                         bot_runtime.BotRuntime._static_gun_value(descriptor, 'staticPitch'),
                         fixed_limits=fixed)
        actor.aim_descriptor = descriptor
        actor.valid_pitch = sampled is not None
        return actor.valid_pitch

    @staticmethod
    def aim_input(raw_yaw, raw_pitch, dt, target, limited_yaw,
                  valid_pitch, minimum_yaw, maximum_yaw, crew_factor,
                  turret_factor, gun_factor, override_pitch=None):
        return (raw_yaw, raw_pitch, dt, int(bool(target)), int(bool(limited_yaw)),
                int(bool(valid_pitch)), minimum_yaw, maximum_yaw, crew_factor,
                turret_factor, gun_factor, int(override_pitch is not None),
                0.0 if override_pitch is None else override_pitch[0],
                0.0 if override_pitch is None else override_pitch[1])

    def after_motion(self, actor_id, dt, move_speed, rotation_speed,
                     turret_speed, dispersion_factor, aim_time_factor,
                     aim_input=None, state=None):
        actor = self.actors[int(actor_id)]
        apply_aim = aim_input is not None
        if aim_input is None:
            aim_input = (0.0, 0.0, dt, 0, 0, 0, 0.0, 0.0,
                         0.0, 0.0, 0.0, 0, 0.0, 0.0)
        elif float(aim_input[2]) != float(dt):
            raise ValueError('weapon aim and dispersion elapsed time differ')
        rows = actor.command(25, (tuple(aim_input), move_speed,
                                  rotation_speed, turret_speed,
                                  dispersion_factor, aim_time_factor,
                                  int(apply_aim)))
        if state is not None and apply_aim:
            actor.publish_aim(state)
        return tuple(_edge(row) for row in rows)

    def aim(self, actor_id, inputs, state=None):
        actor = self.actors[int(actor_id)]
        actor.sync(self._call('sim_weapon_aim', 1, actor.actor_id, tuple(inputs)))
        if state is not None:
            actor.publish_aim(state)
        return actor.aim

    def pitch_limits(self, actor_id, yaw):
        return tuple(self.actors[int(actor_id)].command(26, (yaw,)))

    def last_pitch_limits(self, actor_id):
        aim = self.actors[int(actor_id)].aim
        return tuple(aim[4:6]) if aim[6] else None

    def begin(self, actor_id, count, interval, reload_factor):
        actor = self.actors[int(actor_id)]
        if not actor.command(20, (int(count), interval, reload_factor)):
            return ()
        return actor.burst.advance(0.0)

    def cancel(self, actor_id):
        return bool(self.actors[int(actor_id)].command(21, ()))

    def commit(self, actor_id, launch, edge, dispersion_factor, state=None):
        actor = self.actors[int(actor_id)]
        result, row = self._call('sim_weapon_commit', 1, actor.actor_id,
                                 _launch_row(launch, edge), dispersion_factor, 1)
        actor.sync(row)
        if result and state is not None:
            state['fire_seq'] = actor.fire_seq
            state['clip'] = actor.gun.clip
            actor.ammo.publish(state)
            actor.burst.publish(state)
            state['shells_before_shot'] = sum(actor.ammo.remaining) + 1
        return bool(result)

    def enqueue(self, launch):
        actor = self.actors[int(launch['id'])]
        result, row = self._call('sim_weapon_commit', 1, actor.actor_id,
                                 _launch_row(launch), 1.0, 0)
        actor.sync(row)
        return bool(result)

    def pending(self):
        return tuple(_launch_value(row)
                     for row in self._call('sim_weapon_pending'))

    def ack(self, actor_id, fire_seq):
        return bool(self._call('sim_weapon_ack', 1, int(actor_id), int(fire_seq)))

    def remove(self, actor_id):
        actor_id = int(actor_id)
        result = bool(self._call('sim_weapon_remove', 1, actor_id))
        if result:
            self.actors.pop(actor_id, None)
        return result

    def ballistic_intercept(self, start, target, velocity, speed, gravity,
                            minimum=-math.pi * 0.5, maximum=math.pi * 0.5,
                            prefer_high=False, max_lead_time=20.0):
        row = self._call('sim_weapon_ballistic', 0, (
            tuple(start), tuple(target), tuple(velocity), speed, gravity,
            minimum, maximum, int(bool(prefer_high)), max_lead_time))
        return (tuple(row[0]), row[1], row[2]) if row else None

    def scattered_angles(self, actor_id, round_id, fire_seq, yaw, pitch,
                          dispersion_angle, burst_index=0,
                          burst_group_seq=None, base_direction=None):
        dispersion_angle = float(dispersion_angle)
        if math.isnan(dispersion_angle) or math.isinf(dispersion_angle):
            raise ValueError('bot shot dispersion must be finite')
        if dispersion_angle <= 0.0:
            raise ValueError('bot shot dispersion must be positive')
        group = fire_seq if burst_group_seq is None else burst_group_seq
        seed = (((int(round_id) & 0xffff) * 1000003 +
                 (int(actor_id) & 0xffff) * 9176 +
                 (int(group) & 0x7fffffff) * 6113 +
                 (int(burst_index) & 0xffff) * 3571) & 0x7fffffff)
        generator = random.Random(seed)
        radius = abs(generator.gauss(0.0, dispersion_angle / 2.0))
        if radius > dispersion_angle:
            radius = dispersion_angle * generator.uniform(0.0, 1.0)
        azimuth = generator.uniform(0.0, 2.0 * math.pi)
        if base_direction is None:
            horizontal = math.cos(pitch)
            direction = (math.sin(yaw) * horizontal, -math.sin(pitch),
                         math.cos(yaw) * horizontal)
        else:
            direction = tuple(base_direction)
        return tuple(self._call('sim_weapon_ballistic', 1,
                                (direction, radius, azimuth,
                                 int(base_direction is not None))))

    def detach(self):
        """Restore current plain objects before the shared context closes."""
        if self.detached:
            return
        launches = list(self.pending())
        for actor_id, actor in self.actors.items():
            actor.sync(self._call('sim_weapon_snapshot', 1, actor_id))
            actor.detach()
            for name, facade, original in (
                    ('_gun_states', actor.gun, actor.original_gun),
                    ('_ammo_states', actor.ammo, actor.original_ammo),
                    ('_burst_states', actor.burst, actor.original_burst)):
                mapping = getattr(self.runtime, name)
                if mapping.get(actor_id) is facade:
                    mapping[actor_id] = original
        # Hand the frozen records back to the existing same-round outbox.
        self.runtime._pending_launches = launches
        self.runtime._pending_launch_keys = dict(
            ((int(row['id']), int(row['fire_seq'])), row) for row in launches)
        by_bot = {}
        for row in launches:
            key = (int(row['id']), int(row['fire_seq']))
            by_bot.setdefault(key[0], []).append(key)
        self.runtime._pending_launch_by_bot = by_bot
        self.detached = True


class _Actor(object):
    def __init__(self, owner, actor_id, gun, ammo, burst):
        self.owner = owner
        self.actor_id = actor_id
        self.original_gun, self.original_ammo, self.original_burst = gun, ammo, burst
        self.gun = NativeGun(self, gun)
        self.ammo = NativeAmmo(self, ammo)
        self.burst = NativeBurst(self)
        self.fire_seq = 0
        self.aim = (0.0, 0.0, 0.0, False)
        self.aim_descriptor = None
        self.valid_pitch = False

    def command(self, opcode, values):
        result, row = self.owner._call('sim_weapon_command', 1, self.actor_id,
                                       opcode, tuple(values))
        self.sync(row)
        return result

    def sync(self, row):
        self.gun._sync(row[0])
        self.ammo._sync(row[1])
        self.burst._sync(row[2])
        self.aim = tuple(row[3])
        self.fire_seq = int(row[4])

    def publish_aim(self, state):
        state['turret_yaw'], state['gun_pitch'], state['desired_gun_pitch'] = self.aim[:3]
        state['gun_aligned'] = bool(self.aim[3])

    def detach(self):
        for name in CONFIG_FIELDS + GUN_FIELDS:
            setattr(self.original_gun, name, getattr(self.gun, name))
        self.original_gun.loadout = dict(self.gun.loadout)
        for name in ('remaining', 'loaded', 'next', 'reload_pending', 'plan_pending'):
            value = getattr(self.ammo, name)
            setattr(self.original_ammo, name, list(value) if name == 'remaining' else value)
        for name in BURST_FIELDS:
            setattr(self.original_burst, name, getattr(self.burst, name))


class NativeGun(object):
    def __init__(self, actor, original):
        self._actor = actor
        self.loadout = dict(original.loadout)
        self.crew_level = original.crew_level
        for name in CONFIG_FIELDS:
            self.__dict__[name] = getattr(original, name)

    def _sync(self, row):
        for name, value in zip(GUN_FIELDS, row):
            self.__dict__[name] = value
        self.__dict__['clip'] = int(row[0])
        self.__dict__['reload_kind'] = 'intra' if row[3] else 'full'
        self.__dict__['_burst_remaining'] = int(row[5])

    def __setattr__(self, name, value):
        if name in GUN_FIELDS and '_actor' in self.__dict__:
            row = list(_gun_row(self))
            row[GUN_FIELDS.index(name)] = (int(value == 'intra')
                                           if name == 'reload_kind' else value)
            self._actor.command(27, row)
        else:
            self.__dict__[name] = value

    def adopt_descriptor(self, descriptor):
        from .bot_runtime import _BotGunState
        candidate = _BotGunState(descriptor, crew_level=self.crew_level)
        changed = self.loadout != candidate.loadout
        changed = bool(self._actor.command(11, (
            tuple(getattr(candidate, name) for name in CONFIG_FIELDS),))) or changed
        for name in CONFIG_FIELDS:
            self.__dict__[name] = getattr(candidate, name)
        self.loadout = dict(candidate.loadout)
        self._actor.owner.configure_aim_from_descriptor(
            self._actor.actor_id, descriptor, force=True)
        return changed

    def restore_fire_seq(self, fire_seq, dispersion_factor=1.0,
                         reload_time=None, reload_duration=None,
                         reload_factor=1.0, clip=None, clip_size=None):
        if (reload_time is None) != (reload_duration is None):
            raise ValueError('bot reload progress must be an atomic pair')
        if (clip is None) != (clip_size is None):
            raise ValueError('bot clip snapshot must be an atomic pair')
        return self._actor.command(10, (
            max(0, int(fire_seq)), dispersion_factor, int(reload_time is not None),
            0.0 if reload_time is None else reload_time,
            0.0 if reload_duration is None else reload_duration,
            reload_factor, int(clip is not None),
            0 if clip is None else clip, 0 if clip_size is None else clip_size))

    def tick(self, dt):
        self._actor.command(1, (dt,))

    def tick_dispersion(self, dt, move_speed, rotation_speed, turret_speed,
                        dispersion_factor=1.0, aim_time_factor=1.0):
        self._actor.command(2, (dt, move_speed, rotation_speed, turret_speed,
                                dispersion_factor, aim_time_factor))

    def commit_shot_bloom(self, dispersion_factor=1.0, final_round=True):
        self._actor.command(3, (dispersion_factor, int(bool(final_round))))

    def rescale_reload(self, reload_factor):
        return bool(self._actor.command(4, (reload_factor,)))

    def duration(self, reload_factor=1.0):
        return self.reload_duration * (1.0 if self.reload_kind == 'intra'
                                       else max(0.0, float(reload_factor)))

    def ready(self, reload_factor=1.0):
        return self.elapsed > self.duration(reload_factor)

    def remaining(self, reload_factor=1.0):
        return max(0.0, self.duration(reload_factor) - self.elapsed)

    def complete_reload(self, reload_factor=1.0, available_rounds=None):
        value = int(self._actor.command(5, (
            reload_factor, -1 if available_rounds is None else max(0, int(available_rounds)))))
        return None if value < 0 else 'intra' if value else 'full'

    def require_full_reload(self):
        self._actor.command(6, ())

    def shell_index(self, requested):
        try:
            requested = int(float(requested))
        except (TypeError, ValueError, OverflowError):
            requested = 0
        return max(0, min(requested, self.shell_count - 1))

    def fire(self, reload_factor=1.0):
        return (self.begin_burst(1, reload_factor) and
                self.fire_burst_round(True, reload_factor))

    def begin_burst(self, count, reload_factor=1.0):
        try:
            count = int(count)
        except (TypeError, ValueError, OverflowError):
            return False
        return bool(self._actor.command(7, (count, reload_factor)))

    def fire_burst_round(self, final_round, reload_factor=1.0):
        return bool(self._actor.command(8, (int(bool(final_round)),)))

    def cancel_burst(self):
        return bool(self._actor.command(9, ()))


class NativeAmmo(object):
    def __init__(self, actor, original):
        self._actor = actor
        self.shell_count = original.shell_count
        self.categories = dict(original.categories)

    def _sync(self, row):
        self.remaining = list(row[1])
        self.loaded, self.next = int(row[2]), int(row[3])
        self.reload_pending, self.plan_pending = bool(row[4]), bool(row[5])

    def _standard_fallback(self):
        candidates = [index for index in range(self.shell_count)
                      if self.remaining[index] > 0]
        return next((index for index in candidates
                     if self.categories.get(index) == 'standard'),
                    candidates[0] if candidates else 0)

    def _available(self, requested):
        try:
            requested = int(requested)
        except (TypeError, ValueError, OverflowError):
            requested = -1
        if 0 <= requested < self.shell_count and self.remaining[requested] > 0:
            return requested
        return self._standard_fallback()

    def restore(self, raw):
        from .bot_runtime import _BotAmmoState
        candidate = object.__new__(_BotAmmoState)
        candidate.shell_count = self.shell_count
        candidate.categories = self.categories
        if not candidate.restore(raw):
            return False
        self._actor.command(14, (_ammo_row(candidate),))
        return True

    def stage(self, requested, ready, full_reload=True):
        try:
            requested = int(requested)
        except (TypeError, ValueError, OverflowError):
            requested = -1
        return bool(self._actor.command(12, (requested, int(bool(ready)), int(bool(full_reload)))))

    def can_fire(self, continuing_burst=False):
        return (0 <= self.loaded < self.shell_count and
                self.remaining[self.loaded] > 0 and
                (bool(continuing_burst) or not self.reload_pending))

    def consume_loaded(self, continuing_burst=False):
        return bool(self._actor.command(13, (int(bool(continuing_burst)),)))

    def planned_rounds(self):
        return self.remaining[self.next] if 0 <= self.next < len(self.remaining) else 0

    def loaded_shell_requires_full_reload(self):
        return (0 <= self.loaded < len(self.remaining) and
                self.remaining[self.loaded] <= 0 and sum(self.remaining) > 0)

    def publish(self, state):
        state.update(shell_index=int(self.loaded), next_shell_index=int(self.next),
                     ammo_remaining=list(self.remaining),
                     ammo_reload_pending=bool(self.reload_pending))


class NativeBurst(object):
    WIRE_FIELDS = ('burst_active', 'burst_group_seq', 'burst_count',
                   'burst_next_index', 'burst_interval', 'burst_time_left',
                   'burst_shell_index')

    def __init__(self, actor):
        self._actor = actor

    def _sync(self, row):
        for name, value in zip(BURST_FIELDS, row):
            setattr(self, name, value)
        self.active = bool(row[0])
        self.group_seq, self.count, self.next_index = map(int, row[1:4])
        self.shell_index = int(row[6])

    def start(self, first_shot_seq, count, interval, shell_index):
        try:
            values = (int(first_shot_seq), int(count), float(interval), int(shell_index))
        except (TypeError, ValueError, OverflowError):
            return False
        if math.isnan(values[2]) or math.isinf(values[2]):
            return False
        return bool(self._actor.command(15, values))

    def advance(self, dt):
        try:
            dt = float(dt)
        except (TypeError, ValueError, OverflowError):
            return ()
        if math.isnan(dt) or math.isinf(dt):
            return ()
        return tuple(_edge(row) for row in self._actor.command(16, (dt,)))

    def cancel(self, launched_count=None):
        try:
            launched_count = self.next_index if launched_count is None else int(launched_count)
        except (TypeError, ValueError, OverflowError):
            return False
        if launched_count < 0:
            return False
        return bool(self._actor.command(17, (launched_count,)))

    def publish(self, state):
        state.update(burst_active=bool(self.active), burst_group_seq=int(self.group_seq),
                     burst_count=int(self.count), burst_next_index=int(self.next_index),
                     burst_interval=round(float(self.interval), 6),
                     burst_time_left=round(max(0.0, float(self.time_left)), 6),
                     burst_shell_index=int(self.shell_index))

    def restore(self, raw, fire_seq):
        from .burst_mechanics import BurstClock
        candidate = BurstClock()
        if not candidate.restore(raw, fire_seq):
            return False
        self._actor.command(18, (tuple(getattr(candidate, name) for name in BURST_FIELDS), int(fire_seq)))
        return True
