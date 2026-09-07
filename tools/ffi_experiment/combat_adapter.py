"""Opt-in complete numerical aiming experiment; Python owns engine seams.

Profiles are transferred per descriptor identity. Numeric state is sampled at
the original synchronous call site, after the exact muzzle/part queries. No
query, firing intent, projectile event, or actor-order barrier is coalesced.
"""
from __future__ import print_function

import math


class CombatBackend(object):
    def __init__(self, backend, runtime_module):
        self.backend = backend
        self.runtime = runtime_module
        self.profiles = {}
        self.originals = []
        self.closed = False

    def install(self):
        cls = self.runtime.BotRuntime
        owner = self

        def ballistic(runtime, state, target, descriptor, shell_index):
            return owner.ballistic(runtime, state, target, descriptor, shell_index)

        def aim(runtime, state, command, target, step):
            return owner.aim(runtime, state, command, target, step)

        for name, replacement in (('_local_ballistic_solution', ballistic),
                                  ('_update_gun_aim', aim)):
            self.originals.append((cls, name, cls.__dict__[name], replacement))
            setattr(cls, name, replacement)
        return self

    def close(self):
        if self.closed:
            return
        for cls, name, original, replacement in reversed(self.originals):
            if cls.__dict__.get(name) is replacement:
                setattr(cls, name, original)
        self.profiles.clear()
        self.closed = True

    def profile(self, descriptor):
        cached = self.profiles.get(id(descriptor))
        if cached is not None and cached[0] is descriptor:
            return cached[1]
        rt = self.runtime
        gun = rt._value(descriptor, 'gun', {}) or {}
        limits = rt._value(gun, 'pitchLimits')
        kind, low, high = 0, 0.0, 0.0
        curves = []
        if isinstance(limits, dict) and all(name in limits for name in ('minPitch', 'maxPitch')):
            try:
                for name in ('minPitch', 'maxPitch'):
                    points = rt.gun_pitch_limits._curve_points(limits[name])
                    curves.append(len(points))
                    for point in points:
                        curves.extend(point)
                kind = 2
            except ValueError:
                curves = []
        else:
            pair = rt._gun_pitch_limits(descriptor)
            if pair is not None:
                kind, low, high = 1, pair[0], pair[1]
        yaw = rt.ai_driver.gun_yaw_limits(descriptor)
        static_pitch = rt.BotRuntime._static_gun_value(descriptor, 'staticPitch')
        static_yaw = rt.BotRuntime._static_gun_value(descriptor, 'staticTurretYaw')
        try:
            hydraulic = rt.hull_aiming.pitch_params(descriptor)
        except ValueError:
            hydraulic = None
        hp = hydraulic or {}
        packet = [100, kind, low, high, yaw[0], yaw[1], int(yaw[2]),
                  int(static_pitch is not None), static_pitch or 0.0,
                  int(static_yaw is not None), static_yaw or 0.0,
                  int(hydraulic is not None), int(bool(hp.get('isAvailable'))),
                  int(bool(hp.get('isEnabled'))), hp.get('minimum', 0.0),
                  hp.get('maximum', 0.0), hp.get('speed', 0.0)] + curves
        handle = int(self.backend.call(packet)[0])
        self.profiles[id(descriptor)] = (descriptor, handle)
        return handle

    def pose(self, state):
        rt = self.runtime
        unused_devices, destroyed, unused_crew, unused_yellow = rt._critical_parts(state)
        return [state.get('yaw', 0.0), state.get('pitch', 0.0),
                state.get('roll', 0.0), rt.BotRuntime._terrain_pitch(state),
                rt._number(state.get('suspension_pitch')),
                state.get('turret_yaw', 0.0), state.get('gun_pitch', 0.0),
                state.get('aim_yaw', state.get('yaw', 0.0)),
                int(int(state.get('movement_dir', 0)) != 0),
                int('engineHealth' in destroyed),
                int(bool(destroyed.intersection(('leftTrackHealth', 'rightTrackHealth')))),
                int(bool(state.get('_overturned', False))),
                int(state.get('siege_state', rt.siege_mechanics.DISABLED))]

    def ballistic(self, runtime, state, target, descriptor, shell_index):
        rt = self.runtime
        physical = rt._shot_ballistics(descriptor, shell_index)
        if target is None or physical is None:
            return None
        speed, gravity, maximum = physical
        start = runtime._exact_shot_origin(state, descriptor, shell_index)
        if start is None:
            return None
        aim_token = None
        if callable(runtime.direct_aim_point_probe):
            selected = runtime.direct_aim_point_probe(state, target)
            if not isinstance(selected, dict):
                return None
            try:
                position = rt._water_sensor_vector(selected['aim_position'], 3, 'bot aim point')
                aim_token = selected['aim_token']
            except (KeyError, TypeError, ValueError):
                return None
        else:
            raw = rt._point(target.get('position'), rt._position(target))
            position = (raw[0], raw[1] + 1.0, raw[2])
        packet = self.backend.call(
            [101, self.profile(descriptor)] + self.pose(state) + list(start) +
            list(position) + list(runtime._target_velocity(target)) +
            [speed, gravity, -math.pi * 0.5, math.pi * 0.5, 0, 20.0, maximum] + [0] * 7)
        result = packet[-7:]
        if not result[0]:
            return None
        return dict(aim_position=tuple(result[1:4]), yaw=result[6], pitch=result[4],
                    flight_time=result[5], arc='low', _origin=start, _aim_token=aim_token)

    def aim(self, runtime, state, command, target, step):
        rt = self.runtime
        descriptor = runtime._descriptors.get(state['id'], {})
        solution = command.get('_ballistic_solution')
        if target is None and not isinstance(solution, dict):
            desired_yaw = state.get('aim_yaw', state.get('yaw', 0.0))
            world_pitch, horizontal = 0.0, 0.0
        else:
            fallback = target.get('position') if target is not None else rt._position(state)
            position = rt._point(solution.get('aim_position') if isinstance(solution, dict)
                                 else command.get('aim_position'), fallback)
            origin = solution.get('_origin') if isinstance(solution, dict) else None
            if not (isinstance(origin, (list, tuple)) and len(origin) == 3):
                origin = runtime._exact_shot_origin(state, descriptor, state.get('shell_index', 0))
            if origin is None:
                state['gun_aligned'] = False
                return state.get('aim_yaw', 0.0), 0.0
            dx, dz = position[0] - origin[0], position[2] - origin[2]
            horizontal = math.sqrt(dx * dx + dz * dz)
            desired_yaw = (solution.get('yaw', 0.0) if isinstance(solution, dict)
                           else math.atan2(dx, dz) if horizontal > 0.1 else state.get('yaw', 0.0))
            world_pitch = (solution.get('pitch', 0.0) if isinstance(solution, dict)
                           else -math.atan2((position[1] + 1.0) - origin[1], max(0.5, horizontal)))
        gun_state = runtime._gun_states.get(state['id'])
        modifiers = gun_state.loadout if gun_state is not None else {}
        turret = rt._value(descriptor, 'turret', {}) or {}
        gun = rt._value(descriptor, 'gun', {}) or {}
        turret_speed = (rt._rotation_speed(turret, 0.5) *
                        max(0.0, modifiers.get('crew_factor', 1.0)) *
                        rt._critical_factor(state, descriptor, 'turret_speed'))
        gun_speed = rt._rotation_speed(gun, 0.35) * max(0.0, modifiers.get('gun_rotation_factor', 1.0))
        result = self.backend.call(
            [102, self.profile(descriptor)] + self.pose(state) +
            [desired_yaw, world_pitch, step, int(target is not None), turret_speed, gun_speed] + [0] * 8)[-8:]
        for index, key in enumerate(('terrain_pitch', 'suspension_pitch', 'pitch',
                                     'turret_yaw', 'gun_pitch', 'desired_gun_pitch', 'aim_yaw')):
            state[key] = result[index]
        state['gun_aligned'] = bool(result[7])
        return desired_yaw, horizontal
