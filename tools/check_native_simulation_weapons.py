"""Strict CPython 2.7 parity for persistent native weapon state and outboxes.

Usage: python2.7 tools/check_native_simulation_weapons.py EXTENSION [CLIENT_SCRIPTS]
Synthetic descriptors exercise existing readers and state machines. This is
host conformance, not BigWorld/Windows physics or frame-pacing acceptance.
"""
from __future__ import print_function
import sys
sys.dont_write_bytecode = True
import copy
import imp
import json
import math
import os
import random
import types


def load_sources(path):
    module_dir = os.path.join(path, 'gui', 'mods', 'offline_lan_0922')
    if not os.path.isfile(os.path.join(module_dir, 'bot_runtime.py')):
        module_dir = path
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        package = types.ModuleType(name)
        package.__path__ = [module_dir]
        sys.modules[name] = package
    name = 'gui.mods.offline_lan_0922.ai'
    package = types.ModuleType(name)
    package.__path__ = [os.path.join(module_dir, 'ai')]
    sys.modules[name] = package
    from gui.mods.offline_lan_0922 import bot_runtime, native_weapons
    return bot_runtime, native_weapons


def equal(left, right, label):
    if left != right:
        raise AssertionError('%s\nreference=%r\nnative=%r' % (label, left, right))


def descriptor(clip, burst):
    return dict(gun=dict(shotDispersionAngle=0.035, aimingTime=2.2,
                        reloadTime=0.6, clip=(clip, 0.17), burst=burst,
                        shotDispersionFactors=dict(afterShot=2.1,
                                                  afterShotInBurst=0.6,
                                                  turretRotation=0.08),
                        shots=({}, {}, {}), maxAmmo=90),
                chassis=dict(shotDispersionFactors=(0.12, 0.15)))


def inventory(gun, ammo, burst, fields):
    result = dict((name, getattr(gun, name))
                  for name in fields.CONFIG_FIELDS + fields.GUN_FIELDS)
    result['ammo'] = (list(ammo.remaining), ammo.loaded, ammo.next,
                      ammo.reload_pending, ammo.plan_pending)
    result['burst'] = tuple(getattr(burst, name) for name in fields.BURST_FIELDS)
    return result


def launch(actor_id, seq, edge, artillery=False):
    result = dict(id=actor_id, fire_seq=seq, shell_index=edge['shell_index'],
                  shot_yaw=0.15, shot_pitch=0.02,
                  class_tag='SPG' if artillery else 'mediumTank',
                  burst_group_seq=edge['burst_group_seq'],
                  burst_index=edge['burst_index'], burst_count=edge['burst_count'],
                  launch_time_us=seq * 123456,
                  launch_pose=(1.0, 2.0, 3.0, 0.1, -0.03, 0.02),
                  shot_origin=(1.1, 2.2, 3.3))
    if artillery:
        result.update(shot_velocity=(10.0, 200.0, 30.0), shot_gravity=10.0,
                      shot_max_distance=1000.0, shot_max_time_ms=1400,
                      shot_proof_key=('launch', actor_id, 'human', 101,
                                      edge['shell_index'], seq,
                                      result['shot_origin'], 0.15, 0.02,
                                      1000.0, 10.0, 1000.0, 1.4))
    return result


def check_long_session_timestamps(source, native, backend):
    """Exercise the real Win32-safe launch payload and immutable outbox."""
    runtime = types.ModuleType('long_session_weapon_fixture')
    actor_id = 41
    params = descriptor(1, (1, 0.0))
    runtime.states = {actor_id: dict(fire_seq=0)}
    runtime._gun_states = {actor_id: source._BotGunState(params)}
    runtime._ammo_states = {actor_id: source._BotAmmoState(params, dict(shells=(
        dict(index=0, kind='armor_piercing', penetration=100),)))}
    runtime._burst_states = {actor_id: source.burst_mechanics.BurstClock()}
    runtime._pending_launches = []
    handle = backend.sim_open(2, 1)
    owner = native.NativeWeapons(runtime, backend, handle).install_all()
    checks = [0]

    def check(expected, actual, label):
        equal(expected, actual, label)
        checks[0] += 1

    timestamps = (2 ** 31, 2 ** 32, 14400 * 1000000,
                  86400 * 1000000 + 123456, 2 ** 53 - 1)
    rejected = (0.5, float('nan'), float('inf'), -float('inf'),
                float(2 ** 53), -float(2 ** 53))
    try:
        for index, timestamp in enumerate(timestamps):
            owner.prepare(actor_id, 2.0, 1.0, 0)
            edges = owner.begin(actor_id, 1, 0.0, 1.0)
            check(1, len(edges), 'long session trigger')
            edge = edges[0]
            record = launch(actor_id, edge['shot_seq'], edge)
            record['launch_time_us'] = timestamp
            row = native._launch_row(record, edge)
            check(float, type(row[1]), 'timestamp input uses a float payload')
            check(timestamp, int(row[1]), 'timestamp input retains every integer bit')
            if index == 0:
                before_state = backend.sim_weapon_snapshot(handle, 1, actor_id)
                before_pending = backend.sim_weapon_pending(handle)
                for invalid in rejected:
                    invalid_record = dict(record, launch_time_us=invalid)
                    invalid_row = native._launch_row(invalid_record, edge)
                    check(float, type(invalid_row[1]), 'invalid timestamp also uses the real serializer')
                    check(None, backend.sim_weapon_commit(
                        handle, 1, actor_id, invalid_row, 1.0, 1),
                        'invalid timestamp rejected at the native bridge')
                    check(before_state, backend.sim_weapon_snapshot(handle, 1, actor_id),
                          'rejected timestamp preserves ammo, reload and burst')
                    check(before_pending, backend.sim_weapon_pending(handle),
                          'rejected timestamp preserves the launch ledger')
            check(True, owner.commit(actor_id, record, edge, 1.0,
                                     runtime.states[actor_id]), 'long session commit')
            raw_pending = backend.sim_weapon_pending(handle)
            check(1, len(raw_pending), 'one long session launch is pending')
            check(float, type(raw_pending[0][3]), 'native timestamp output uses a float payload')
            check(timestamp, int(raw_pending[0][3]), 'native timestamp output is exact')
            pending = owner.pending()
            check(timestamp, pending[0]['launch_time_us'], 'facade restores the exact timestamp')
            check(type(int(timestamp)), type(pending[0]['launch_time_us']),
                  'facade restores the timestamp as an integer')
            check(True, owner.ack(actor_id, edge['shot_seq']), 'long session launch ack')
            check((), owner.pending(), 'ack removes the long session launch')
            check(False, owner.ack(actor_id, edge['shot_seq']), 'duplicate long session ack is harmless')
    finally:
        owner.detach()
        backend.sim_close(handle)
    return checks[0], len(timestamps), len(rejected)


def main():
    extension = os.path.abspath(sys.argv[1])
    source_path = (sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'src', 'res', 'scripts', 'client'))
    source, native = load_sources(source_path)
    backend = imp.load_dynamic('offline_math_batch_native', extension)
    source._bot_default_crew_factors = lambda *args, **kwargs: {}
    source.loadout.modifiers = lambda *args, **kwargs: dict(
        dispersion_factor=1.05, aim_time_factor=1.1, reload_factor=0.95,
        crew_factor=1.0, gun_rotation_factor=1.0)
    runtime = types.ModuleType('weapon_fixture')
    runtime.states = {}
    runtime._gun_states = {}
    runtime._ammo_states = {}
    runtime._burst_states = {}
    runtime._pending_launches = []
    reference = {}
    for actor_id, clip, burst in ((1, 1, (1, 0.0)), (2, 6, (3, 0.08)),
                                  (3, 4, (4, 0.12))):
        gun = source._BotGunState(descriptor(clip, burst))
        ammo = source._BotAmmoState(descriptor(clip, burst), dict(shells=(
            dict(index=0, kind='armor_piercing', penetration=100),
            dict(index=1, kind='armor_piercing', penetration=150),
            dict(index=2, kind='high_explosive', penetration=20))))
        clock = source.burst_mechanics.BurstClock()
        gun.tick(0.123)
        gun.tick_dispersion(0.1, 2.0, 0.3, 0.1)
        reference[actor_id] = (copy.deepcopy(gun), copy.deepcopy(ammo), copy.deepcopy(clock))
        runtime._gun_states[actor_id] = gun
        runtime._ammo_states[actor_id] = ammo
        runtime._burst_states[actor_id] = clock
        runtime.states[actor_id] = dict(fire_seq=0)
    handle = backend.sim_open(1, 1)
    owner = native.NativeWeapons(runtime, backend, handle).install_all()
    checks = [0]

    def compare(actor_id, label):
        expected = inventory(*(reference[actor_id] + (native,)))
        actual = inventory(runtime._gun_states[actor_id], runtime._ammo_states[actor_id],
                           runtime._burst_states[actor_id], native)
        equal(expected, actual, label)
        checks[0] += 1

    for actor_id in reference:
        compare(actor_id, 'exact install %d' % actor_id)
    rng = random.Random(982341)
    fire_seq = dict((key, 0) for key in reference)
    pending = []
    count_launches = 0
    for frame in range(180):
        dt = (0.013, 0.099, 0.2, 0.351)[frame % 4]
        factor = (1.0, 1.3, 0.8)[(frame // 11) % 3]
        for actor_id, (gun, ammo, burst) in sorted(reference.items()):
            requested = (frame // 7) % 3
            if not burst.active:
                gun.rescale_reload(factor)
                gun.tick(dt)
                boundary = gun.complete_reload(factor, ammo.planned_rounds())
                ammo.stage(gun.shell_index(requested), boundary is not None, boundary == 'full')
            owner.prepare(actor_id, dt, factor, requested)
            compare(actor_id, 'prepare %d/%d' % (frame, actor_id))
            values = (abs(math.sin(frame) * 8.0), abs(math.cos(frame) * 0.4),
                      rng.uniform(0.0, 0.6), 1.0 + 0.1 * (frame % 3),
                      1.0 + 0.2 * (frame % 2))
            gun.tick_dispersion(dt, *values)
            edges = burst.advance(dt)
            actual_edges = owner.after_motion(actor_id, dt, *values)
            equal(edges, actual_edges, 'all crossed burst edges')
            compare(actor_id, 'after motion %d/%d' % (frame, actor_id))
            if not edges and not burst.active and gun.ready(factor) and ammo.can_fire():
                amount = min(gun.burst_count, ammo.remaining[ammo.loaded], gun.clip)
                if amount > 0:
                    equal(True, burst.start(fire_seq[actor_id] + 1, amount,
                                            gun.burst_interval, ammo.loaded), 'reference start')
                    equal(True, gun.begin_burst(amount, factor), 'reference gun arm')
                    edges = burst.advance(0.0)
                    equal(edges, owner.begin(actor_id, amount, gun.burst_interval, factor), 'native trigger')
            for edge in edges:
                if frame % 19 == 8 and edge['burst_index'] > 0:
                    launched = min(max(0, fire_seq[actor_id] - burst.group_seq + 1), burst.next_index)
                    burst.cancel(launched)
                    gun.cancel_burst()
                    owner.cancel(actor_id)
                    break
                equal(True, gun.fire_burst_round(edge['final'], factor), 'reference clip debit')
                equal(True, ammo.consume_loaded(edge['burst_index'] > 0), 'reference ammo debit')
                if edge['final'] and ammo.loaded_shell_requires_full_reload():
                    gun.require_full_reload()
                fire_seq[actor_id] = edge['shot_seq']
                gun.commit_shot_bloom(values[3], edge['final'])
                record = launch(actor_id, edge['shot_seq'], edge, actor_id == 1)
                if actor_id == 2 and frame % 5 == 0:
                    record['class_tag'] = 'unknown'
                equal(True, owner.commit(actor_id, record, edge, values[3],
                                         runtime.states[actor_id]), 'native atomic commit')
                record['shells_before_shot'] = sum(ammo.remaining) + 1
                pending.append(record)
                count_launches += 1
                equal(False, owner.enqueue(record), 'same immutable identity is idempotent')
                compare(actor_id, 'commit %d/%d' % (frame, actor_id))
            compare(actor_id, 'frame end %d/%d' % (frame, actor_id))
        equal(tuple(pending), owner.pending(), 'complete frozen outbox')
        if pending and frame % 3 == 0:
            head = pending[0]
            later = next((row for row in pending if row['id'] == head['id'] and
                          row['fire_seq'] != head['fire_seq']), None)
            if later:
                equal(False, owner.ack(later['id'], later['fire_seq']), 'out-of-order actor ack')
            equal(True, owner.ack(head['id'], head['fire_seq']), 'actor head ack')
            pending.pop(0)

    # Aim curves, static crossing, yaw limits and dispersion use one phase.
    curves = dict(minPitch=((0.0, -0.25), (1.2, -0.15), (3.1, -0.18), (6.2831854, -0.25)),
                  maxPitch=((0.0, 0.12), (1.2, 0.08), (3.1, 0.11), (6.2831854, 0.12)))
    owner.install_aim(1, curves['minPitch'], curves['maxPitch'], 0.6, 0.3, -0.01)
    aim = dict(turret_yaw=0.0, gun_pitch=0.0)
    for index in range(80):
        raw_yaw, raw_pitch, dt = rng.uniform(-math.pi, math.pi), rng.uniform(-0.4, 0.25), 0.13
        limited = index % 3 == 0
        target = index % 4 != 0
        valid = index % 11 != 0
        override = (-0.01, -0.01) if index % 9 == 0 else None
        previous = aim['turret_yaw']
        desired = max(-0.8, min(0.8, raw_yaw)) if limited else raw_yaw
        speed = 0.6 * 0.93 * 0.7
        delta = source._angle_delta(desired, previous)
        current = source._wrapped(previous + max(-speed * dt, min(speed * dt, delta)))
        if limited:
            current = max(-0.8, min(0.8, current))
        rotation_time = abs(current - raw_yaw) / speed
        wanted = aim['gun_pitch']
        if valid:
            limits = override or source.gun_pitch_limits.calc_pitch_limits(current, curves)
            wanted = max(limits[0], min(limits[1], raw_pitch))
            aim['gun_pitch'] = source.hull_aiming.gun_pitch_step(
                aim['gun_pitch'], raw_pitch, -0.01, 0.3 * 0.81, dt, rotation_time, limits)
        aim.update(turret_yaw=current, desired_gun_pitch=wanted,
                   gun_aligned=bool(valid and target and abs(source._angle_delta(raw_yaw, current)) <= 0.06 and
                                    abs(raw_pitch - aim['gun_pitch']) <= 0.04))
        inputs = owner.aim_input(raw_yaw, raw_pitch, dt, target, limited, valid,
                                 -0.8, 0.8, 0.93, 0.7, 0.81, override)
        output = {}
        owner.after_motion(1, dt, 2.0, 0.1, -999.0, 1.0, 1.0, inputs, output)
        equal(aim, output, 'fused aim %d' % index)
        checks[0] += 1
    for index in range(150):
        yaw = rng.uniform(-math.pi, math.pi)
        equal(source.gun_pitch_limits.calc_pitch_limits(yaw, curves), owner.pitch_limits(1, yaw), 'float32 curves')
        start = (1.0, 3.0, -2.0)
        target = (rng.uniform(-200.0, 200.0), rng.uniform(-15.0, 30.0), rng.uniform(20.0, 400.0))
        velocity = (rng.uniform(-12.0, 12.0), 0.0, rng.uniform(-12.0, 12.0))
        arguments = (start, target, velocity, 750.0, 9.81, -1.4, 1.4, False, 20.0)
        equal(source.ballistics.ballistic_intercept(*arguments), owner.ballistic_intercept(*arguments), 'intercept')
        for base in (None, (0.31, 0.08, 0.94)):
            arguments = (1, 27, index + 1, yaw, -0.15, 0.038, index % 3, index // 3 + 1, base)
            equal(source._dispersed_barrel_angles(*arguments), owner.scattered_angles(*arguments), 'frozen entropy scatter')
        checks[0] += 4
    # Exact restore pairs, strict-ready boundary and mode descriptor adoption.
    gun, ammo, burst = reference[3]
    native_gun = runtime._gun_states[3]
    burst.cancel(0)
    runtime._burst_states[3].cancel(0)
    restored_duration = gun.reload_full * 0.8
    args = (7, 1.2, restored_duration * 0.4, restored_duration,
            0.8, 0, gun.clip_size)
    gun.restore_fire_seq(*args)
    native_gun.restore_fire_seq(*args)
    runtime.states[3]['fire_seq'] = 7
    compare(3, 'restored full reload clock')
    changed = descriptor(gun.clip_size, (4, 0.12))
    changed['gun'].update(reloadTime=1.7, aimingTime=3.4, shotDispersionAngle=0.041)
    previous_duration = gun.reload_duration
    equal(gun.adopt_descriptor(changed), native_gun.adopt_descriptor(changed), 'mode adopt changed')
    equal(previous_duration, native_gun.reload_duration, 'mode adopt keeps active reload')
    compare(3, 'complete adopted state')
    gun.elapsed = gun.duration(0.8)
    native_gun.elapsed = native_gun.duration(0.8)
    equal(False, native_gun.ready(0.8), 'exact duration is not strictly ready')
    equal(gun.complete_reload(0.8, 3), native_gun.complete_reload(0.8, 3), 'strict completion')
    gun.tick(1e-9)
    native_gun.tick(1e-9)
    equal(gun.complete_reload(0.8, 3), native_gun.complete_reload(0.8, 3), 'strict completion overrun')
    compare(3, 'ready boundary after descriptor switch')
    # An absolute pair retains double precision, unlike float32 yaw curves.
    fixed = (-0.30000000001, 0.10000000003)
    owner.install_aim(3, (), (), 0.4, 0.3, None, fixed)
    equal(fixed, owner.pitch_limits(3, -0.273), 'absolute pair is not quantized')
    checks[0] += 5
    # A reused identity with changed content must leave the ledger unchanged.
    before = owner.pending()
    if before:
        changed = dict(before[0], shot_yaw=before[0]['shot_yaw'] + 0.01)
        try:
            owner.enqueue(changed)
        except RuntimeError:
            pass
        else:
            raise AssertionError('changed frozen identity was accepted')
        equal(before, owner.pending(), 'ledger unchanged on duplicate conflict')
        equal(False, owner.remove(before[0]['id']), 'pending owner cannot disappear')
    snapshots = dict((key, inventory(runtime._gun_states[key], runtime._ammo_states[key],
                                     runtime._burst_states[key], native)) for key in reference)
    owner.detach()
    owner.detach()
    for key in reference:
        equal(snapshots[key], inventory(runtime._gun_states[key], runtime._ammo_states[key],
                                        runtime._burst_states[key], native), 'same-round detach state')
    equal(list(before), runtime._pending_launches, 'detach retains admitted outbox')
    backend.sim_close(handle)
    equal(None, backend.sim_weapon_snapshot(handle, 1, 1), 'closed lifetime rejected')
    # Install the detached state into a new generation without a clock reset.
    handle2 = backend.sim_open(1, 2)
    restored = native.NativeWeapons(runtime, backend, handle2).install_all()
    equal(before, restored.pending(), 'same-round outbox migration')
    for key in reference:
        equal(snapshots[key], inventory(runtime._gun_states[key], runtime._ammo_states[key],
                                        runtime._burst_states[key], native), 'same-round state migration')
    restored.detach()
    backend.sim_close(handle2)
    # Failed multi-actor installation must expose no partial facade aliases.
    original_refs = dict(runtime._gun_states)
    original_outbox = list(runtime._pending_launches)
    runtime._gun_states[2].fully_aimed_dispersion = -1.0
    handle3 = backend.sim_open(1, 3)
    failed = native.NativeWeapons(runtime, backend, handle3)
    try:
        failed.install_all()
    except RuntimeError:
        pass
    else:
        raise AssertionError('invalid installation did not fail')
    equal(True, all(runtime._gun_states[key] is value for key, value in original_refs.items()),
          'failed install publishes no aliases')
    failed.detach()
    equal(original_outbox, runtime._pending_launches, 'failed install preserves original outbox')
    backend.sim_close(handle3)
    checks[0] += 2
    timestamp_checks, timestamp_launches, timestamp_rejections = check_long_session_timestamps(
        source, native, backend)
    checks[0] += timestamp_checks
    print(json.dumps(dict(result='pass', strict_checks=checks[0], physical_launches=count_launches,
                          remaining_pending=len(before), python=sys.version.split()[0],
                          long_session_launches=timestamp_launches,
                          rejected_timestamp_payloads=timestamp_rejections,
                          coverage=['gun', 'ammo', 'burst', 'fused_aim', 'float32_curves',
                                    'ballistics', 'scatter', 'immutable_launch_fifo',
                                    'descriptor_adoption', 'strict_ready', 'unquantized_absolute_limits',
                                    'detach', 'same_round_migration', 'closed_lifetime',
                                    'atomic_install_failure', 'unknown_class_tag',
                                    'long_session_launch_timestamps', 'exact_double_timestamp_boundary',
                                    'invalid_timestamp_bridge_rejection'])))


if __name__ == '__main__':
    main()
