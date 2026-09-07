#!/usr/bin/env python
"""Differential state/query tests against the CPython 2.7 client contract.

Use CPython 2.7 for the reference: Python 3.12 changed float sum arithmetic
in shot_geometry's direction normalization. Its random aiming cases can
differ by one ULP even though the target Python 2 calculation is unchanged.
Do not mask that interpreter difference with approximate comparisons.
"""
from __future__ import print_function
import argparse
import copy
import math
import random

from combat_adapter import CombatBackend
from driver_adapter import NativeDriver
from navigation_adapter import Backend
from portable_workload import load_fixture, Namespace


def equal(actual, expected, label):
    if actual != expected:
        raise AssertionError('%s\nactual=%r\nexpected=%r' % (label, actual, expected))


def check_boundaries(backend, rt):
    original = rt.ai_driver.LocalDriver()
    native = NativeDriver(backend, rt.ai_driver.LocalDriver())
    neighbour = dict(position=(0.0, 0.0, -3.0), yaw=0.0,
                     half_length=3.5, half_width=1.7, alive=True)
    def clear(unused_yaw, maximum_distance=None):
        return True
    def blocked_pose(unused_yaw):
        return False
    args = (1, 0, (0.0, 0.0, 0.0), 0.0, 0.0, 2.0,
            (0.0, 0.0, 30.0), [neighbour], clear)
    expected = original.drive(*args, pose_clear=blocked_pose)
    actual = native.drive(*args, pose_clear=blocked_pose)
    equal(actual, expected, 'anonymous reverse blocker')
    assert actual['reverse_blocked_by'] is True
    assert expected['reverse_blocked_by'] is True
    before = native.dump_state(1)
    for packet in ([float('nan')], [float('inf')], [1e100], [200.5], [300.5], [0, 0]):
        try:
            backend.call(packet)
        except RuntimeError:
            pass
        else:
            raise AssertionError('invalid command was admitted: %r' % packet)
        equal(native.dump_state(1), before, 'rejected command preserves owner')
    native.forget(1)


def check_intercepts(backend, rt, rng, cases):
    valid = 0
    for index in range(cases):
        shooter = [rng.uniform(-600, 600), rng.uniform(-30, 100), rng.uniform(-600, 600)]
        target = [rng.uniform(-600, 600), rng.uniform(-30, 100), rng.uniform(-600, 600)]
        velocity = [rng.uniform(-20, 20), rng.uniform(-2, 2), rng.uniform(-20, 20)]
        speed, gravity = rng.uniform(0, 1300), rng.uniform(-30, 30)
        minimum, maximum = rng.uniform(-math.pi / 2, 0), rng.uniform(0, math.pi / 2)
        high, max_time = bool(index % 2), rng.choice([0, 1.0, 10.0, 20.0])
        if index % 37 == 0:
            target = list(shooter)
        original = rt.ballistics.ballistic_intercept(shooter, target, velocity, speed,
                                                     gravity, minimum, maximum, high, max_time)
        packet = backend.call([103] + shooter + target + velocity +
                              [speed, gravity, minimum, maximum, int(high), max_time, 0] + [0] * 7)[-7:]
        native = (tuple(packet[1:4]), packet[4], packet[5]) if packet[0] else None
        equal(native, original, 'intercept %d' % index)
        valid += original is not None
    return valid


def check_aim(backend, rt, rng, cases):
    owner = CombatBackend(backend, rt)
    original = rt.BotRuntime._update_gun_aim
    fields = ('terrain_pitch', 'suspension_pitch', 'pitch', 'turret_yaw', 'gun_pitch',
              'desired_gun_pitch', 'aim_yaw', 'gun_aligned')
    # Fresh profiles exercise missing, absolute, yaw-dependent and static guns,
    # damaged modules, switching siege mode, and active hydraulic suspension.
    profiles = []
    for index in range(12):
        gun = dict(rotationSpeed=0.35,
                   shots=[dict(speed=200.0 + index * 40, gravity=9.81, maxDistance=700.0)])
        if index % 4 == 0:
            gun['pitchLimits'] = None
        elif index % 4 == 1:
            gun['pitchLimits'] = (-0.2, 0.35)
        else:
            gun['pitchLimits'] = dict(
                minPitch=[(0, -0.18), (1.8, -0.10), (3.8, -0.12), (2 * math.pi, -0.18)],
                maxPitch=[(0, 0.30), (2.5, 0.20), (2 * math.pi, 0.30)])
        if index % 3 == 0:
            gun.update(staticPitch=-0.02, staticTurretYaw=0.01)
        if index % 2:
            gun['turretYawLimits'] = (-0.15, 0.15)
        descriptor = dict(gun=gun, turret=dict(rotationSpeed=0.5))
        if index >= 6:
            descriptor['hullAimingParams'] = dict(pitch=dict(
                isAvailable=True, isEnabled=True,
                wheelsCorrectionAngles=dict(pitchMin=-0.25, pitchMax=0.20),
                wheelsCorrectionSpeed=0.05))
        profiles.append(descriptor)
    for index in range(cases):
        descriptor = profiles[index % len(profiles)]
        state = dict(id=1, yaw=rng.uniform(-math.pi, math.pi),
                     pitch=rng.uniform(-0.4, 0.4), roll=rng.uniform(-0.3, 0.3),
                     suspension_pitch=rng.uniform(-0.25, 0.20),
                     turret_yaw=rng.uniform(-math.pi, math.pi),
                     gun_pitch=rng.uniform(-0.25, 0.35), aim_yaw=rng.uniform(-math.pi, math.pi),
                     movement_dir=rng.choice([-1, 0, 1]), siege_state=index % 4,
                     _overturned=bool(index % 17 == 0),
                     critical={'destroyed': rng.choice([[], ['engineHealth'], ['leftTrackHealth']])})
        if index % 5 == 0:
            state['pitch'] = state['roll'] = 0.0
        if index % 2:
            state['terrain_pitch'] = rng.uniform(-0.2, 0.2)
        target = dict(position=(100.0, 10.0, 120.0)) if index % 7 else None
        solution = dict(aim_position=(100.0, 10.0, 120.0), yaw=rng.uniform(-math.pi, math.pi),
                        pitch=rng.uniform(-0.5, 0.5), _origin=(0.0, 0.0, 0.0))
        command = {'_ballistic_solution': solution} if index % 9 else {}
        runtime = rt.BotRuntime.__new__(rt.BotRuntime)
        runtime._descriptors = {1: descriptor}
        runtime._gun_states = {1: Namespace(loadout={'crew_factor': 0.83, 'gun_rotation_factor': 1.1})}
        runtime._gun_yaw_limits = {}
        runtime._exact_shot_origin = lambda *unused: ((0.0, 0.0, 0.0) if index % 23 else None)
        first, second = copy.deepcopy(state), copy.deepcopy(state)
        step = rng.choice([0, 1.0/60, 1.0/15, 0.35])
        expected = original(runtime, first, command, target, step)
        actual = owner.aim(runtime, second, command, target, step)
        equal(actual, expected, 'aim return %d' % index)
        equal(dict((k, second[k]) for k in fields if k in second),
              dict((k, first[k]) for k in fields if k in first), 'aim state %d' % index)
        yaw, pitch = rng.uniform(-math.pi, math.pi), rng.uniform(-0.7, 0.7)
        distance = 50.0 + index % 800
        moving_target = dict(position=(math.sin(yaw) * distance, pitch * 100,
                                       math.cos(yaw) * distance),
                             velocity=(3.0, 0.0, -2.0))
        runtime.direct_aim_point_probe = None
        if index % 3 == 0:
            runtime.direct_aim_point_probe = lambda *unused: dict(
                aim_position=moving_target['position'], aim_token=('hull', index))
        elif index % 11 == 0:
            runtime.direct_aim_point_probe = lambda *unused: None
        expected = rt.BotRuntime._local_ballistic_solution(
            runtime, first, moving_target, descriptor, 0)
        actual = owner.ballistic(runtime, second, moving_target, descriptor, 0)
        equal(actual, expected, 'full low arc and physical gun reach %d' % index)
    owner.close()


def check_driver(backend, rt, rng, ticks):
    original = rt.ai_driver.LocalDriver()
    native = NativeDriver(backend, rt.ai_driver.LocalDriver())
    modes = set()
    for index in range(ticks):
        bot = index % 15
        phase = (index // 15) % 12
        pos = (float(bot * 20), 0.0, 0.0)
        # Long stationary intervals force timed recovery. Later translation
        # and changing targets exercise progress anchors and braking release.
        if phase >= 8:
            pos = (pos[0], 0.0, float(index % 9))
        target = (pos[0] + (0.0 if phase == 9 else 20.0),
                  8.0 if phase == 10 else 0.0, pos[2] + (0.0 if phase == 9 else 15.0))
        yaw = rng.choice([0.0, math.pi, -math.pi, rng.uniform(-math.pi, math.pi)])
        speed, dt = rng.uniform(-4, 15), rng.choice([1.0/60, 1.0/15, 0.5, 1.25])
        neighbours = []
        if phase in (1, 2, 3, 4, 5):
            neighbours = [dict(id=100+bot, position=(pos[0], 0.0, pos[2]-5.0),
                               yaw=0.0, half_length=3.5, half_width=1.7, alive=phase != 5)]
        first_queries, second_queries = [], []
        def probes(log):
            def clear(yaw, maximum_distance=None):
                log.append(('direction', yaw, maximum_distance))
                if phase in (0, 3):
                    return False
                return phase >= 6 or math.sin(yaw * 3.0 + index) > -0.2
            def pose(yaw):
                log.append(('pose', yaw))
                return phase not in (0, 1) and math.cos(yaw) > -0.5
            return clear, pose
        clear_a, pose_a = probes(first_queries)
        clear_b, pose_b = probes(second_queries)
        kwargs = dict(half_length=3.5, half_width=1.7, movement_intent=phase != 11,
                      stopping_distance=3.0, stop_at_target=bool(index % 3), decision_horizon=0.1)
        expected = original.drive(bot, bot, pos, yaw, speed, dt, target, neighbours,
                                  clear_a, pose_clear=pose_a, **kwargs)
        actual = native.drive(bot, bot, pos, yaw, speed, dt, target, neighbours,
                              clear_b, pose_clear=pose_b, **kwargs)
        equal(actual, expected, 'driver output %d' % index)
        equal(second_queries, first_queries, 'driver queries %d' % index)
        equal(native.dump_state(bot), original.states[bot], 'driver state %d' % index)
        modes.add(actual['recovery_mode'])
        if index % 7 == 0:
            elapsed = None if index % 2 else 0.25
            equal(native.wait_for_traffic(bot, elapsed), original.wait_for_traffic(bot, elapsed), 'traffic result')
        if index % 11 == 0:
            ttl = None if index % 2 else 5.0
            native.remember_failure(bot, yaw, ttl)
            original.remember_failure(bot, yaw, ttl)
        equal(native.dump_state(bot), original.states[bot], 'driver events %d' % index)
        if index % 47 == 0:
            native.forget(bot)
            original.forget(bot)
            equal(native.dump_state(bot), None, 'driver forget')
    equal(modes, set(('arrived', 'blocked', 'pivot_recovery', 'reverse_turn', 'avoid', 'drive')), 'driver branch coverage')
    return native.queries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--cases', type=int, default=1200)
    args = parser.parse_args()
    fixtures = load_fixture(args.fixture)
    rt = fixtures['fixtures']._load()
    backend = Backend(args.module)
    try:
        check_boundaries(backend, rt)
        rng = random.Random(7812)
        valid = check_intercepts(backend, rt, rng, args.cases)
        check_aim(backend, rt, rng, args.cases)
        queries = check_driver(backend, rt, rng, args.cases * 3)
        print('Exact parity: %d intercepts (%d solutions), %d full gun updates plus low-arc/reach solves, %d driver steps / %d queries; all six drive modes and invalid-command/anonymous-blocker boundaries.' %
              (args.cases, valid, args.cases, args.cases * 3, queries))
    finally:
        backend.close()


if __name__ == '__main__':
    main()
