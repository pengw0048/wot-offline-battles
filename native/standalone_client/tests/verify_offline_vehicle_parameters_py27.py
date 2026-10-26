"""Run the actual mod producers through the actual CPython 2.7 parser extension."""
from __future__ import print_function

import copy
import imp
import os
import sys
import types

ROOT, LIBRARY = sys.argv[1:3]
sys.path.insert(0, LIBRARY)
import _offline_vehicle_parameters_probe as probe

CLIENT = os.path.join(ROOT, 'src/res/scripts/client')
PACKAGE = os.path.join(CLIENT, 'gui/mods/offline_lan_0922')
for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
    module = types.ModuleType(name)
    module.__path__ = [PACKAGE]
    sys.modules[name] = module
imp.load_source('gui.mods.offline_lan_0922.worker_diagnostics',
                os.path.join(PACKAGE, 'worker_diagnostics.py'))
physics = imp.load_source('gui.mods.offline_lan_0922.vehicle_physics',
                          os.path.join(PACKAGE, 'vehicle_physics.py'))


class Object(object):
    def __init__(self, **values):
        self.__dict__.update(values)


def descriptor(mass, power, width, length, detailed):
    name = 'selected-chassis'
    cfg = {'detailed': {'chassis': {name: {}}}}
    if detailed:
        cfg['detailed']['chassis'][name] = {
            'roadWheelPositions': (-2, -1, 0, 1, 2),
            'stiffnessFactors': (.8, 1.1, 1.2, 1, .9),
            'stiffness0': 1, 'stiffness1': .9, 'damping': .2,
            'bodyHeight': .95, 'hullInertiaFactors': (1, 1, 1.8), 'wheelRadius': .35}
    return Object(physics={'weight': mass, 'enginePower': power, 'trackCenterOffset': 1.2,
                          'speedLimits': (15, 5), 'terrainResistance': (1, 1.4, 2),
                          'rollingFrictionFactors': (.95, 1.05, 1)},
        type=Object(xphysics=cfg),
        chassis=Object(name=name, rotationSpeed=.75, hullPosition=(0, 1, 0),
            hitTester=Object(bbox=((-width/2, -.5, -length/2), (width/2, .7, length/2)))),
        hull=Object(hitTester=Object(bbox=((-1.2, -.65, -2.2), (1.2, .65, 2.2)))))


def vehicle_expected(p):
    return tuple(p[k] for k in ('mass', 'powerW', 'speedFwd', 'speedBwd', 'rotSpd')) + tuple(
        p['terrainResist']) + tuple(p[k] for k in ('specificFriction', 'brakeDecel',
                                                'trackCenter', 'nativePowerRatio'))


def suspension_expected(p):
    sides = {None: 0, 'left': 1, 'right': 2}
    values = tuple(p[k] for k in ('mass', 'width', 'length', 'clearance', 'rest_length',
        'static_compression', 'hard_ratio', 'contact_memory_distance', 'pitch_inertia',
        'roll_inertia', 'fixed_step', 'constraint_iterations'))
    springs = [(s['x'], s.get('y', 0), s['z'], sides[s['side']], s['static_compression'],
                s['max_compression'], s['stiffness'], s['damping'], s['max_force']) for s in p['springs']]
    contacts = [(s['x'], s.get('y', 0), s['z'], sides[s.get('side')],
                 s.get('penetration', .01)) for s in p['pseudo_contacts']]
    return values + (springs, contacts)


TUNING = ('GRAVITY_FACTOR', 'COHESION', 'DRIVE_TRACTION', 'SLIP_DRAG', 'SLIP_THRESHOLD_TAN',
    'POWER_FACTOR', 'BKWD_POWER_FRACTION', 'ANG_ACCELERATION_TIME', 'SPEED_AFFECT_ROT_DECREASE',
    'COAST_BRAKE_SHARE', 'STEER_RESIST_MULT', 'SLIDE_HOLD_TAN', 'SLIDE_KINETIC',
    'OVERSPEED_MAX_FACTOR', 'OVERSPEED_DAMP', 'OVERSPEED_BUILD', 'SLOPE_COH_DECAY',
    'SLOPE_COH_DECAY_Y', 'COH_DECAY_BOUND', 'SLIDE_MAX', 'SLIDE_DRAG', 'FALL_SAFE_SPEED', 'FALL_DMG_PER_MS')


def rejects(function, value, error):
    try:
        function(value)
    except error:
        return
    raise AssertionError('Malformed producer result was accepted')


for tuning in ({}, {'gravity_factor': 1.05, 'cohesion': 1.1, 'slide_max': 4.3,
                    'fall_safe_speed': 8.4, 'fall_dmg_per_ms': .04, 'coast_brake_share': .5}):
    physics.apply_tuning(tuning)
    expected_tuning = tuple(getattr(physics, key) for key in TUNING)
    assert probe.tuning(physics) == expected_tuning
    assert probe.tuning(physics.__dict__) == expected_tuning
    for mass, power, width, length, detailed in (
            (4759, 40452.5, 1.77, 3.47, False),
            (27940, 353040, 2.95, 5.79, False),
            (21000, 316265, 2.8, 5.2, True)):
        desc = descriptor(mass, power, width, length, detailed)
        p = physics.derive_params(desc, {'engine/power': 1.07,
            'vehicle/rotationSpeed': .95, 'chassis/terrainResistance': (1.01, 1.02, 1.03)})
        s = physics.derive_suspension_params(desc)
        assert probe.vehicle(p) == vehicle_expected(p)
        assert probe.suspension(s) == suspension_expected(s)

p_bad = copy.deepcopy(p)
del p_bad['terrainResist']
rejects(probe.vehicle, p_bad, KeyError)
p_bad = copy.deepcopy(p)
p_bad['terrainResist'] = (1, 2)
rejects(probe.vehicle, p_bad, ValueError)
p_bad['terrainResist'] = (1, 2, float('nan'))
rejects(probe.vehicle, p_bad, ValueError)
p_bad = copy.deepcopy(p)
p_bad['powerW'] = '353040'
rejects(probe.vehicle, p_bad, TypeError)
s_bad = copy.deepcopy(s)
del s_bad['springs'][-1]['max_force']
rejects(probe.suspension, s_bad, KeyError)
s_bad = copy.deepcopy(s)
s_bad['pseudo_contacts'][-1]['side'] = 'wrong'
rejects(probe.suspension, s_bad, ValueError)
s_bad = copy.deepcopy(s)
s_bad['constraint_iterations'] = -1
rejects(probe.suspension, s_bad, OverflowError)
t_bad = dict(physics.__dict__)
del t_bad['FALL_DMG_PER_MS']
rejects(probe.tuning, t_bad, KeyError)
print('CPython 2.7 actual producer/parser integration passed: 6 descriptor/tuning combinations and atomic failures')
