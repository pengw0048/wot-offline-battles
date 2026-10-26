"""Compare the native suspension kernel with the current mod's Python laws."""

import ctypes
import importlib.util
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / 'native/standalone_client'
REFERENCE = ROOT / 'tests/test_port_0922_vehicle_physics.py'
STATE = ('height', 'vertical_velocity', 'pitch', 'pitch_velocity', 'roll', 'roll_velocity')
OUTPUT = STATE + ('contact_count', 'airborne', 'left_flying', 'right_flying',
                  'contacted_this_step', 'touched_contact_count')


def doubles(values):
    return (ctypes.c_double * len(values))(*values)


def native_step(library, params, state, ground, pseudo, elapsed, support):
    sides = {None: 0, 'left': 1, 'right': 2}
    springs = []
    for spring in params['springs']:
        springs.extend((spring['x'], spring.get('y', 0), spring['z'], sides[spring['side']],
                        spring['static_compression'], spring['max_compression'],
                        spring['stiffness'], spring['damping'], spring['max_force']))
    contacts = []
    for contact in params.get('pseudo_contacts', ()):
        contacts.extend((contact['x'], contact.get('y', 0), contact['z'],
                         sides[contact.get('side')], contact.get('penetration', .01)))
    valid = lambda values: (ctypes.c_ubyte * len(values))(*(v is not None for v in values))
    values = lambda values: doubles([0 if v is None else v for v in values])
    result = (ctypes.c_double * 16)()
    status = library.suspension_fixture(
        doubles([params[k] for k in ('mass', 'pitch_inertia', 'roll_inertia',
                                    'fixed_step', 'constraint_iterations')]),
        doubles(springs), len(params['springs']), doubles(contacts), len(pseudo),
        values(ground), valid(ground), values(pseudo), valid(pseudo),
        doubles([state.get(k, 0) for k in STATE]), elapsed, support, result)
    assert status == 0, 'Native suspension rejected a valid Python fixture'
    output = dict(zip(OUTPUT, result))
    output['impact_speed'] = result[13] if result[12] else None
    output['max_compression'], output['max_limit_excess'] = result[14], result[15]
    return output


def compare(library, reference, params, state, ground, pseudo, elapsed, support):
    expected = reference.damper_suspension_step(params, state, ground, elapsed, pseudo, support)
    actual = native_step(library, params, state, ground, pseudo, elapsed, support)
    for key, value in actual.items():
        if value is None or expected[key] is None:
            assert value is expected[key], (key, value, expected[key])
        else:
            assert math.isclose(value, expected[key], rel_tol=2e-11, abs_tol=2e-10), (
                key, value, expected[key], state, elapsed, support)
    return actual


def main():
    spec = importlib.util.spec_from_file_location('suspension_reference_tests', REFERENCE)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    reference = fixture.vehicle_physics
    params = reference.derive_suspension_params(
        fixture.VehiclePhysicsSuspensionTrialTests._descriptor())
    with tempfile.TemporaryDirectory(prefix='wot-suspension-') as directory:
        output = str(Path(directory) / 'suspension.so')
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-O2',
            '-shared', '-fPIC', '-fno-fast-math', '-ffp-contract=off',
            '-Wall', '-Wextra', '-Werror', '-I' + str(NATIVE / 'include'),
            str(NATIVE / 'src/vehicle_suspension.cpp'),
            str(NATIVE / 'src/vehicle_control.cpp'),
            str(NATIVE / 'tests/vehicle_suspension_fixture.cpp'), '-o', output], check=True)
        library = ctypes.CDLL(output)
        ptr = ctypes.POINTER(ctypes.c_double)
        byte = ctypes.POINTER(ctypes.c_ubyte)
        library.suspension_fixture.argtypes = [ptr, ptr, ctypes.c_int, ptr, ctypes.c_int,
                                              ptr, byte, ptr, byte, ptr, ctypes.c_double,
                                              ctypes.c_double, ptr]
        library.suspension_fixture.restype = ctypes.c_int
        rng = random.Random(1513)
        cases = 0
        for index in range(400):
            gx, gz = rng.uniform(-.6, .6), rng.uniform(-.6, .6)
            origin = rng.uniform(-.4, .4)
            samples = lambda points: tuple(None if rng.random() < .18 else
                origin + gx * p['x'] + gz * p['z'] for p in points)
            ground, pseudo = samples(params['springs']), samples(params['pseudo_contacts'])
            state = dict(zip(STATE, (rng.uniform(-.6, 1), rng.uniform(-20, 5),
                rng.uniform(-.5, .5), rng.uniform(-2, 2), rng.uniform(-.5, .5), rng.uniform(-2, 2))))
            elapsed = rng.choice((-.1, 0, 1/120, 1/60, 1/30, .1, .25))
            compare(library, reference, params, state, ground, pseudo, elapsed, rng.uniform(-2, 2))
            cases += 1
        for kind in ('level', 'slope', 'cliff', 'landing'):
            state = dict.fromkeys(STATE, 0.0)
            if kind == 'landing':
                state.update(height=5, vertical_velocity=-3)
            for tick in range(180):
                ground = tuple(None if kind == 'cliff' and tick > 40 else
                    (.2 * point['x'] - .1 * point['z'] if kind == 'slope' else 0)
                    for point in params['springs'])
                pseudo = tuple(None if kind == 'cliff' and tick > 40 else
                    (.2 * point['x'] - .1 * point['z'] if kind == 'slope' else 0)
                    for point in params['pseudo_contacts'])
                state = compare(library, reference, params, state, ground, pseudo, 1/30, 0)
                cases += 1
        library.suspension_helpers.argtypes = [ptr, ptr, ptr]
        params_drive = {'mass': 27940.0, 'powerW': 353040.0, 'speedFwd': 15.0,
                        'speedBwd': 5.0, 'rotSpd': .6, 'terrainResist': (1.0, 1.5, 2.0),
                        'specificFriction': .7, 'brakeDecel': 15.9, 'trackCenter': 1.2,
                        'nativePowerRatio': 1.0}
        native_parameters = doubles([27940, 353040, 15, 5, .6, 1, 1.5, 2, .7, 15.9, 1.2, 1])
        for index in range(1000):
            values = [rng.uniform(-20, 20), rng.choice((-.1, 0, 1/60, 1/30, .1)),
                rng.randrange(2), rng.randrange(2), rng.uniform(-4, 4), rng.uniform(-1, 3),
                rng.uniform(-1.5, 1.5), rng.uniform(-.5, 1), rng.uniform(-8, 8),
                rng.uniform(-8, 8), rng.randrange(2), rng.randrange(3), rng.randrange(100, 3000),
                rng.uniform(-40, 40), rng.uniform(-1.5, 1.5), rng.uniform(0, 7),
                rng.uniform(-15, 15), rng.uniform(-15, 15), rng.uniform(-15, 15),
                rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1), rng.randrange(2)]
            actual = (ctypes.c_double * 16)()
            library.suspension_helpers(doubles(values), native_parameters, actual)
            hard = reference.hard_contact_step(values[0], values[1], bool(values[2]),
                                               values[4] if values[3] else None)
            push = reference.contact_push_step(params_drive, values[8], values[9], values[4],
                values[1], bool(values[10]), int(values[11]), values[7])
            decel = reference.contact_push_decel(params_drive, bool(values[10]),
                                                int(values[11]), values[7])
            expected = hard + (reference.slope_slide_speed(values[15], values[5], values[1]),
                reference.suspension_slope_slide_speed(values[15], values[5], values[1]),
                reference.ground_follow_gap(values[0], values[6], values[1]),
                reference.launch_vertical_speed(values[0], values[6]),
                reference.landing_impact_speed(values[16:19], values[19:22] if values[22] else None),
                reference.fall_damage(int(values[12]), values[13]),
                reference.overturn_level_from_up_cosine(values[14])) + push + (push == (0, 0),) + decel + (
                reference.suspension_vertical_sweep_drop(values[17], values[1]),)
            for left, right in zip(actual, expected):
                assert math.isclose(left, right, rel_tol=2e-12, abs_tol=1e-11), (left, right, values)
        library.suspension_plane_fixture.argtypes = [ptr, ctypes.c_int, ptr, byte, ptr,
            ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double, ptr]
        library.suspension_plane_fixture.restype = ctypes.c_int
        points = doubles([v for point in params['springs'] for v in
                          (point['x'], point.get('y', 0), point['z'])])
        for index in range(400):
            position = [rng.uniform(-1e7, 1e7), 0, rng.uniform(-1e7, 1e7)]
            yaw, pitch, roll = rng.uniform(-4, 4), rng.uniform(-1, 1), rng.uniform(-1, 1)
            gx, gz = rng.uniform(-.5, .5), rng.uniform(-.5, .5)
            posed = reference.suspension_world_points(params, position, yaw, pitch, roll)
            ground = tuple(None if rng.random() < .2 else
                5 + gx * (point[0] - position[0]) + gz * (point[1] - position[2]) +
                (1 if index % 5 == 0 and i == 0 else 0) for i, point in enumerate(posed))
            expected = reference.suspension_world_ground_plane(
                params, ground, position, yaw, .01, pitch, roll)
            output = (ctypes.c_double * 13)()
            present = library.suspension_plane_fixture(points, len(params['springs']),
                doubles([0 if value is None else value for value in ground]),
                (ctypes.c_ubyte * len(ground))(*(value is not None for value in ground)),
                doubles(position), yaw, pitch, roll, .01, output)
            assert bool(present) == (expected is not None)
            if expected is not None:
                values = [expected[k] for k in ('center_x', 'center_z', 'center_y', 'gradient_x', 'gradient_z')]
                values += list(expected['normal'])
                values += [expected['max_residual'], expected['contact_count'],
                    reference.suspension_drive_pitch(expected, yaw),
                    reference.suspension_support_vertical_velocity(expected, 3, -2),
                    reference.suspension_plane_height(expected, position[0]+1, position[2]-2)]
                for left, right in zip(output, values):
                    assert math.isclose(left, right, rel_tol=2e-11, abs_tol=2e-10), (left, right)
        print('%d suspension steps, 1000 contact/slide/fall cases and 400 world-plane fits matched Python' % cases)


if __name__ == '__main__':
    main()
