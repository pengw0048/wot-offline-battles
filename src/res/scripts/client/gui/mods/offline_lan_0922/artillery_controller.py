# -*- coding: utf-8 -*-
"""Deterministic SPG aiming backed by the bounded native arc queue."""

import math

from gui.mods.offline_lan_0922.artillery_arc_queue import ArcProbeQueue
from gui.mods.offline_lan_0922 import ballistics
from gui.mods.offline_lan_0922 import gun_pitch_limits
from gui.mods.offline_lan_0922 import shot_geometry


STRATEGIC_MAXIMUM_STEP = 0.20


def _value(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _number(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    if math.isnan(value) or math.isinf(value):
        return float(default)
    return value


def _position(value):
    raw = value.get('position') if isinstance(value, dict) else None
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return tuple(_number(raw[index]) for index in range(3))
    value = value if isinstance(value, dict) else {}
    return (_number(value.get('x')), _number(value.get('y')),
            _number(value.get('z')))


def _target_velocity(target):
    raw = target.get('velocity') if isinstance(target, dict) else None
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        return tuple(_number(raw[index]) for index in range(3))
    target = target if isinstance(target, dict) else {}
    yaw = _number(target.get('yaw'))
    speed = _number(target.get('speed'))
    return (math.sin(yaw) * speed, 0.0, math.cos(yaw) * speed)


def _pitch_limits(descriptor, turret_yaw):
    gun = _value(descriptor, 'gun', {}) or {}
    limits = _value(gun, 'pitchLimits')
    if isinstance(limits, dict) and all(
            name in limits for name in ('minPitch', 'maxPitch')):
        try:
            return gun_pitch_limits.calc_pitch_limits(turret_yaw, limits)
        except ValueError:
            return None
    if isinstance(limits, dict):
        limits = limits.get('absolute')
    try:
        return float(limits[0]), float(limits[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _shot(descriptor, shell_index):
    gun = _value(descriptor, 'gun', {}) or {}
    shots = _value(gun, 'shots', ()) or ()
    try:
        value = shots[max(0, int(shell_index))]
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    speed = _number(_value(value, 'speed'), -1.0)
    gravity = abs(_number(_value(value, 'gravity'), -1.0))
    maximum = _number(_value(value, 'maxDistance'), -1.0)
    if speed <= 1.0 or gravity <= 0.01 or maximum <= 1.0:
        return None
    return speed, gravity, maximum


def _quantized(point, scale=0.25):
    return tuple(int(round(_number(value) * scale)) for value in point)


class ArtilleryController(object):
    """Create low/high candidates and publish only fully checked solutions."""

    def __init__(self, queue=None, maximum_step=0.12,
                 origin_resolver=None):
        # A strategic job proves only the low/high arc family. Freeze it to the
        # source pose, target identity and shell while the bounded queue works;
        # moving-target pose buckets would restart long shared jobs forever.
        # ``solution`` re-leads the proved family from the current target pose,
        # and the separate exact queue checks that final physical trajectory.
        self.queue = queue or ArcProbeQueue(
            success_ttl=0.35, failure_ttl=0.25, max_job_age=60.0)
        # Final launch paths are immutable and may contain 167 chords at the
        # 20-second protocol ceiling.  They therefore cannot share a short,
        # moving-target planning lifetime.  Completed receipts are pinned by
        # their exact key until that key changes or the controller resets.
        self.launch_queue = ArcProbeQueue(
            max_jobs=8, success_ttl=0.35, failure_ttl=0.25,
            max_job_age=40.0)
        self.maximum_step = max(0.04, min(0.20, float(maximum_step)))
        self.origin_resolver = (
            origin_resolver if callable(origin_resolver) else None)
        self._planning_keys = {}
        self._launch_keys = {}
        self._launch_receipts = {}

    def reset(self):
        self.queue.reset()
        self.launch_queue.reset()
        self._planning_keys = {}
        self._launch_keys = {}
        self._launch_receipts = {}

    @staticmethod
    def _key(source, target, shell_index):
        target_id = target.get('network_id', target.get('id', 0))
        return (
            int(source.get('id', 0)), str(target.get('kind') or ''),
            int(target_id or 0), int(shell_index),
            tuple(float(value) for value in _position(source)),
            tuple(_number(source.get(name)) for name in (
                'yaw', 'pitch', 'roll', 'turret_yaw', 'gun_pitch')),
        )

    @staticmethod
    def _planning_slot(source, target, shell_index):
        target_id = target.get('network_id', target.get('id', 0))
        return (
            int(source.get('id', 0)), str(target.get('kind') or ''),
            int(target_id or 0), int(shell_index),
        )

    def _replace_planning_key(self, slot, key):
        previous = self._planning_keys.get(slot)
        if previous == key:
            return
        if previous is not None:
            self.queue._discard_job(previous)
            self.queue._discard_waiting(previous)
            self.queue.results.pop(previous, None)
        if key is None:
            self._planning_keys.pop(slot, None)
        else:
            self._planning_keys[slot] = key

    def _candidates(self, source, target, descriptor, shell_index):
        physical = _shot(descriptor, shell_index)
        if physical is None:
            return ()
        speed, gravity, maximum = physical
        source_position = _position(source)
        if self.origin_resolver is None:
            start = (source_position[0], source_position[1] + 1.5,
                     source_position[2])
        else:
            try:
                start = tuple(float(value) for value in
                              self.origin_resolver(source, descriptor))
            except (TypeError, ValueError, OverflowError):
                return ()
            if (len(start) != 3 or
                    any(math.isnan(value) or math.isinf(value)
                        for value in start)):
                return ()
        target_position = _position(target)
        target_position = (
            target_position[0], target_position[1] + 1.0,
            target_position[2])
        velocity = _target_velocity(target)
        candidates = []
        for name, prefer_high in (('low', False), ('high', True)):
            solution = ballistics.ballistic_intercept(
                start, target_position, velocity, speed, gravity,
                -math.pi * 0.5, math.pi * 0.5,
                prefer_high=prefer_high)
            if solution is None:
                continue
            aim_position, pitch, flight_time = solution
            if (flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                    speed * flight_time > maximum + 1e-6):
                continue
            if candidates and abs(pitch - candidates[-1]['pitch']) < 1e-5:
                continue
            yaw = math.atan2(
                aim_position[0] - start[0], aim_position[2] - start[2])
            try:
                local_turret_yaw, local_pitch = \
                    shot_geometry.world_direction_to_local_gun_angles(
                        (math.sin(yaw) * math.cos(pitch),
                         -math.sin(pitch),
                         math.cos(yaw) * math.cos(pitch)),
                        _number(source.get('yaw')),
                        _number(source.get('pitch')),
                        _number(source.get('roll')))
            except (TypeError, ValueError, OverflowError):
                continue
            pitch_limits = _pitch_limits(descriptor, local_turret_yaw)
            if pitch_limits is None:
                continue
            minimum_pitch, maximum_pitch = pitch_limits
            if (local_pitch < minimum_pitch - 0.0001 or
                    local_pitch > maximum_pitch + 0.0001):
                continue
            candidates.append({
                'aim_position': aim_position,
                'yaw': yaw,
                'pitch': pitch,
                'flight_time': flight_time,
                'arc': name,
                'speed': speed,
                'gravity': gravity,
                'max_distance': maximum,
                'path': ballistics.ballistic_path(
                    start, yaw, pitch, speed, gravity, flight_time,
                    STRATEGIC_MAXIMUM_STEP),
            })
        return tuple(candidates)

    def request(self, source, target, descriptor, shell_index, now):
        if not isinstance(source, dict):
            return False, None
        if not isinstance(target, dict):
            return False, None
        try:
            slot = self._planning_slot(source, target, shell_index)
        except (TypeError, ValueError, OverflowError):
            return True, None
        try:
            key = self._key(source, target, shell_index)
        except (TypeError, ValueError, OverflowError):
            self._replace_planning_key(slot, None)
            return True, None
        self._replace_planning_key(slot, key)
        candidates = self._candidates(
            source, target, descriptor, shell_index)
        return self.queue.request(
            key, candidates, _position(target), float(now))

    def result(self, source, target, shell_index, now):
        if not isinstance(source, dict) or not isinstance(target, dict):
            return False, None
        try:
            key = self._key(source, target, shell_index)
        except (TypeError, ValueError, OverflowError):
            return False, None
        try:
            slot = self._planning_slot(source, target, shell_index)
        except (TypeError, ValueError, OverflowError):
            return False, None
        if self._planning_keys.get(slot) != key:
            return False, None
        return self.queue.result(key, float(now))

    def solution(self, source, target, descriptor, shell_index, now):
        ready, proved = self.request(
            source, target, descriptor, shell_index, now)
        if not ready or not isinstance(proved, dict):
            return None
        # The queue proves which arc family is clear. A moving contact may
        # advance inside its bounded planning bucket while those chords are
        # checked, so return a freshly led solution of the same proved family.
        # The final launch queue still checks every chord of that exact path.
        proved_arc = proved.get('arc')
        for candidate in self._candidates(
                source, target, descriptor, shell_index):
            if candidate.get('arc') == proved_arc:
                return candidate
        return None

    @staticmethod
    def _launch_slot(source):
        return int(source.get('id', 0))

    def _launch_key(self, source, target, shell_index, fire_seq, origin,
                    yaw, pitch, speed, gravity, maximum, flight_time):
        """Bind a final receipt to every value that changes its trajectory."""
        target_id = target.get('network_id', target.get('id', 0))
        return (
            'launch', int(source.get('id', 0)),
            str(target.get('kind') or ''), int(target_id or 0),
            int(shell_index), int(fire_seq),
            tuple(float(value) for value in origin),
            float(yaw), float(pitch), float(speed), float(gravity),
            float(maximum), float(flight_time),
        )

    def _replace_launch_key(self, slot, key):
        previous = self._launch_keys.get(slot)
        if previous == key:
            return
        if previous is not None:
            self.launch_queue._discard_job(previous)
            self.launch_queue._discard_waiting(previous)
            self.launch_queue.results.pop(previous, None)
            self._launch_receipts.pop(previous, None)
        if key is None:
            self._launch_keys.pop(slot, None)
        else:
            self._launch_keys[slot] = key

    def cancel_launch(self, source):
        """Discard one Bot's pending or completed exact launch proof."""
        if not isinstance(source, dict):
            return False
        try:
            slot = self._launch_slot(source)
        except (TypeError, ValueError, OverflowError):
            return False
        existed = slot in self._launch_keys
        self._replace_launch_key(slot, None)
        return existed

    def request_launch(self, source, target, descriptor, shell_index,
                       fire_seq, origin, yaw, pitch, flight_time, now):
        """Queue the exact dispersed path that will be put on the wire.

        Unlike the strategic low/high request, this takes the native muzzle
        origin and the already-seeded physical shot angles.  The returned
        receipt freezes the same origin and velocity for launch; changing any
        bound trajectory value discards that proof before it can be reused.
        """
        if not isinstance(source, dict):
            return False, None
        slot = self._launch_slot(source)
        if not isinstance(target, dict):
            self._replace_launch_key(slot, None)
            return False, None
        physical = _shot(descriptor, shell_index)
        if physical is None:
            self._replace_launch_key(slot, None)
            return True, None
        speed, gravity, maximum = physical
        try:
            origin = tuple(float(value) for value in origin)
            yaw = float(yaw)
            pitch = float(pitch)
            flight_time = float(flight_time)
            if len(origin) != 3:
                raise ValueError('invalid muzzle origin')
        except (TypeError, ValueError, OverflowError):
            self._replace_launch_key(slot, None)
            return True, None
        values = origin + (yaw, pitch, flight_time)
        if (any(math.isnan(value) or math.isinf(value) for value in values) or
                flight_time <= 0.0 or
                flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                speed * flight_time > maximum + 1e-6):
            self._replace_launch_key(slot, None)
            return True, None
        try:
            key = self._launch_key(
                source, target, shell_index, fire_seq, origin, yaw, pitch,
                speed, gravity, maximum, flight_time)
        except (TypeError, ValueError, OverflowError):
            self._replace_launch_key(slot, None)
            return True, None
        self._replace_launch_key(slot, key)
        cached = self._launch_receipts.get(key)
        if cached is not None:
            return True, cached
        horizontal = math.cos(pitch)
        velocity = (
            math.sin(yaw) * horizontal * speed,
            math.sin(pitch) * speed,
            math.cos(yaw) * horizontal * speed,
        )
        path = ballistics.ballistic_path(
            # ``pitch`` is the physical protocol elevation (positive up),
            # while the pure ballistic helper follows BigWorld's rendered
            # negative-is-up gun convention.
            origin, yaw, -pitch, speed, gravity, flight_time,
            self.maximum_step)
        receipt = {
            'proof_key': key,
            'fire_seq': int(fire_seq),
            'shell_index': int(shell_index),
            'origin': origin,
            'velocity': velocity,
            'shot_yaw': yaw,
            'shot_pitch': pitch,
            'gravity': gravity,
            'max_distance': maximum,
            'max_time_ms': int(round(
                ballistics.PROJECTILE_MAX_FLIGHT_SECONDS * 1000.0)),
            'flight_time': flight_time,
            'arc': 'exact_launch',
            'path': path,
        }
        terminal = path[-1] if path else origin
        ready, result = self.launch_queue.request(
            key, (receipt,), terminal, float(now))
        if ready and result is not None:
            self._launch_receipts[key] = result
            self.launch_queue.results.pop(key, None)
        return ready, result

    def _pin_launch_results(self):
        """Retain exact receipts until their fully bound key is invalidated."""
        for key, value in list(self.launch_queue.results.items()):
            solution = value[1]
            if solution is None:
                continue
            try:
                slot = int(key[1])
            except (TypeError, ValueError, IndexError):
                slot = None
            if slot is not None and self._launch_keys.get(slot) == key:
                self._launch_receipts[key] = solution
            self.launch_queue.results.pop(key, None)

    def _advance_launch(self, now, budget, probe):
        used = self.launch_queue.advance(now, budget, probe)
        self._pin_launch_results()
        return used

    def advance(self, now, ray_budget, probe):
        """Share one native-ray budget without starving either proof stage."""
        budget = max(0, int(ray_budget))
        now = float(now)
        if budget <= 0:
            return 0
        launch_pending = self.launch_queue.diagnostics()['pending'] > 0
        planning_pending = self.queue.diagnostics()['pending'] > 0
        if not launch_pending:
            return self.queue.advance(now, budget, probe)
        if not planning_pending:
            return self._advance_launch(now, budget, probe)

        # Production has four rays per frame.  Reserve half for each stage;
        # unused quota is immediately borrowed so the total remains bounded
        # and short jobs do not waste a render-frame slot.
        launch_quota = max(1, budget // 2)
        planning_quota = max(0, budget - launch_quota)
        launch_used = self._advance_launch(
            now, launch_quota, probe)
        planning_used = self.queue.advance(
            now, planning_quota, probe)
        remaining = budget - launch_used - planning_used
        if remaining > 0:
            launch_used += self._advance_launch(
                now, remaining, probe)
            remaining = budget - launch_used - planning_used
        if remaining > 0:
            planning_used += self.queue.advance(
                now, remaining, probe)
        return launch_used + planning_used

    def diagnostics(self):
        result = self.queue.diagnostics()
        launch = self.launch_queue.diagnostics()
        result.update({
            'launch_pending': launch['pending'],
            'launch_results': (
                launch['results'] + len(self._launch_receipts)),
        })
        return result
