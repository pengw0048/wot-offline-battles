"""CPython 2.7 conformance for persistent native perception and control laws.

This checks the actual Python reference laws, state histories and synchronous
query order. It does not establish BigWorld behavior or Windows frame pacing.
"""
from __future__ import print_function
import argparse
import copy
import imp
import json
import math
import os
import random
import sys
import types
sys.dont_write_bytecode = True


def load_sources(path):
    module_dir = os.path.join(path, 'gui', 'mods', 'offline_lan_0922')
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        package = types.ModuleType(name)
        package.__path__ = [module_dir] if name.endswith('0922') else []
        sys.modules[name] = package
    from gui.mods.offline_lan_0922 import native_control_core
    from gui.mods.offline_lan_0922.ai import driver, traffic
    return native_control_core, driver, traffic


def _effective_params_snapshot(mass=25000.0, base_moving=0.171,
                               base_still=0.228, shot_factor=0.10,
                               ramming_bonus=0.0,
                               spall_coefficient=1.0):
    crew_members = [{
        'instance': 'commander', 'roles': ['commander'], 'skills': [],
    }]
    controlled_impact_level = max(
        0.0, float(ramming_bonus) / 0.0015)
    if controlled_impact_level > 0.0:
        crew_members.append({
            'instance': 'driver', 'roles': ['driver'],
            'skills': [{
                'name': 'driver_rammingmaster',
                'level': controlled_impact_level,
                'active': True, 'enabled': True,
            }],
        })
    crew_names = [member['instance'] for member in crew_members]
    return {
        'version': 1,
        'loadout': {
            'crew_level': 100.0, 'commander_level': 100.0,
            'effective_crew_level': 100.0, 'crew_multiplier': 1.0,
            'crew_factor': 1.0, 'gun_rotation_factor': 1.0,
            'reload_factor': 1.0, 'aim_time_factor': 1.0,
            'dispersion_factor': 1.0, 'repair_factor': 1.0,
            'vehicle_rotation_factor': 1.0, 'radio_factor': 1.0,
            'bloom_move_factor': 1.0, 'bloom_rotation_factor': 1.0,
            'bloom_turret_factor': 1.0,
            'terrain_resistance_factors': [1.0, 1.0, 1.0],
            'has_big_kit': False, 'from_client_factors': True,
            'has_rammer': False, 'has_aim_drives': False,
            'has_ventilation': False, 'has_stabiliser': False,
            'has_rations': False, 'has_brotherhood': False,
            'has_snap_shot': False, 'has_smooth_ride': False,
            'has_sixth_sense': False,
        },
        'physics': {
            'mass': float(mass), 'powerW': 500000.0,
            'speedFwd': 14.0, 'speedBwd': 7.0, 'rotSpd': 0.75,
            'terrainResist': [1.0, 1.0, 1.0],
            'specificFriction': 1.0, 'brakeDecel': 4.0,
            'trackCenter': 2.0, 'minPlaneNormalY': 0.2,
            'nativePowerRatio': 1.0, 'rotationIsAroundCenter': True,
        },
        'spotting': {
            'commander_level': 100.0, 'recon_level': 0.0,
            'situational_level': 0.0, 'camouflage_level': 0.0,
            'binocular_factor': 1.0, 'binocular_delay': 3.0,
            'camouflage_net_bonus': 0.0, 'camouflage_net_delay': 3.0,
            'has_binoculars': False, 'has_camouflage_net': False,
            'vision_factor': 1.0, 'camouflage_factor': 0.57,
            'invisibility_moving': [0.0, 1.0],
            'invisibility_still': [0.0, 1.0],
            'from_client_factors': True,
        },
        'ramming': {
            'spall_coefficient': float(spall_coefficient),
            'ramming_bonus': float(ramming_bonus),
        },
        'ammo': [[1, 20]],
        'camouflage': {
            'camouflage_id': None,
            'base_moving': float(base_moving),
            'base_still': float(base_still),
            'shot_factor': float(shot_factor),
        },
        'skills': {
            'sixth_sense': False, 'expert': False,
            'deadeye': False, 'intuition_chances': 0,
            'controlled_impact': controlled_impact_level >= 100.0,
            'designated_target': False, 'last_effort': False,
        },
        'crew': {
            'members': crew_members,
            'dynamic_spotting': {
                'crew': crew_names,
                'states': dict(
                    ('%d:%d' % (mask, fire), {
                        'vision': 1.0, 'signal': 1.0,
                        'camouflage': 1.0,
                        'base_moving': float(base_moving),
                        'base_still': float(base_still),
                        'invisibility_moving': [0.0, 1.0],
                        'invisibility_still': [0.0, 1.0],
                    })
                    for mask in range(1 << len(crew_members))
                    for fire in (0, 1)),
            },
        },
        'gun': {
            'clip_size': 1,
            'shots': [{
                'compact_descr': 1,
                'source_shot': {
                    'speed': 800.0, 'gravity': 9.81,
                    'maxDistance': 500.0,
                    'piercingPower': [100.0, 80.0],
                    'deadeye': False,
                    'shell': {
                        'kind': 'ARMOR_PIERCING', 'caliber': 37.0,
                        'damage': [40.0, 20.0],
                        'explosionRadius': 0.0,
                    },
                },
            }],
        },
    }

class Object(object):
    pass


class Checker(object):
    def __init__(self, backend, facade, driver, traffic):
        self.backend, self.facade = backend, facade
        self.driver, self.traffic = driver, traffic
        self.comparisons = self.driver_ticks = self.traffic_ticks = 0
        self.contacts = self.visibility_queries = self.lifecycle_checks = 0
        self.maximum_difference = 0.0
        self.human_frames = 0
        self.arrival_runs = self.arrival_ticks = self.arrival_handoffs = 0
        self.radio_summaries = 0
        self.stopping_distance_ticks = self.driver_input_rejections = 0

    def equal(self, actual, expected, path):
        if isinstance(expected, dict):
            assert isinstance(actual, dict) and set(actual) == set(expected), (path, actual, expected)
            for key in expected:
                self.equal(actual[key], expected[key], path + '.' + str(key))
        elif isinstance(expected, (tuple, list)):
            assert isinstance(actual, (tuple, list)) and len(actual) == len(expected), (path, actual, expected)
            for index, pair in enumerate(zip(actual, expected)):
                self.equal(pair[0], pair[1], path + '[%d]' % index)
        elif isinstance(expected, float):
            error = abs(float(actual) - expected)
            self.maximum_difference = max(self.maximum_difference, error)
            assert error <= 1e-11 * max(1.0, abs(expected)), (path, actual, expected)
        else:
            assert actual == expected, (path, actual, expected)
        self.comparisons += 1

    def owner(self):
        runtime = Object()
        runtime.adapter = Object()
        runtime.adapter.driver = self.driver.LocalDriver()
        runtime._traffic_coordinator = self.traffic.TrafficCoordinator()
        from gui.mods.offline_lan_0922.radio import RadioNetwork
        runtime._radio_network = RadioNetwork()
        handle = self.backend.sim_open(13, 17)
        owner = self.facade.NativeControl(runtime, self.backend, handle)
        return owner, runtime

    def driver_cases(self):
        owner, runtime = self.owner()
        original = self.driver.LocalDriver()
        rng = random.Random(1729)
        for actor in range(1, 16):
            position = [0.0, 0.0, 0.0]
            for step in range(140):
                mode = (step // 20) % 7
                yaw = (rng.uniform(-math.pi, math.pi) if mode == 6 else
                       0.12 * math.sin(step * 0.2))
                if mode in (0, 5):
                    position[2] += 0.12
                target = (0.3 * math.sin(step * 0.03),
                          10.0 if mode == 5 else 0.0, 30.0)
                if mode == 1:
                    target = (position[0], position[1], position[2])
                peers = []
                if mode in (3, 4):
                    peers.append(dict(id=100, position=(0.0, 0.0, position[2]-4.0),
                        yaw=0.1, alive=mode == 3, half_length=3.5, half_width=1.7))
                    peers.append(dict(id=101, position=(0.0, 0.0, position[2]+4.0),
                        yaw=-0.2, alive=False, half_length=3.5, half_width=1.7))
                traces = [[], []]
                def probes(index):
                    def clear(yaw, distance=None):
                        traces[index].append(('direction', yaw, distance))
                        if mode == 2:
                            return abs(self.driver._angle_delta(yaw, 0.0)) > 0.5
                        if mode in (3, 4):
                            return distance is not None and mode == 3
                        return True
                    def pose(yaw):
                        traces[index].append(('pose', yaw))
                        return mode != 4 and math.sin(yaw) >= -0.8
                    return clear, pose
                a_clear, a_pose = probes(0)
                b_clear, b_pose = probes(1)
                dt = 0.63 if step % 19 == 0 else 0.1
                speed = rng.uniform(-2.0, 9.0)
                args = (actor, actor-1, tuple(position), yaw, speed, dt,
                        target, peers)
                kwargs = dict(half_length=3.5, half_width=1.7,
                    movement_intent=step % 31 != 0, stopping_distance=3.5,
                    stop_at_target=step % 17 != 0, decision_horizon=0.2)
                expected = original.drive(*(args + (a_clear,)), pose_clear=a_pose, **kwargs)
                actual = owner.driver.drive(*(args + (b_clear,)), pose_clear=b_pose, **kwargs)
                path = 'driver.%d.%d' % (actor, step)
                self.equal(actual, expected, path)
                self.equal(traces[1], traces[0], path + '.queries')
                self.equal(owner.driver.states[actor], original.states[actor], path + '.state')
                if step % 13 == 0:
                    failed_yaw = yaw + (math.pi if step % 2 else -math.pi)
                    original.remember_failure(actor, failed_yaw, 1.7)
                    owner.driver.remember_failure(actor, failed_yaw, 1.7)
                    self.equal(owner.driver.states[actor], original.states[actor], path + '.failure')
                if step % 17 == 0:
                    elapsed = None if step % 2 else 1.6
                    self.equal(owner.driver.wait_for_traffic(actor, elapsed),
                               original.wait_for_traffic(actor, elapsed), path + '.wait')
                    self.equal(owner.driver.states[actor], original.states[actor], path + '.wait_state')
                self.driver_ticks += 1
        owner.traffic.forget(1)
        self.equal(owner.driver.states, original.states, 'descriptor.traffic_forget_preserves_driver')
        owner.driver.forget(1)
        original.forget(1)
        owner.detach()
        self.equal(runtime.adapter.driver.states, original.states, 'driver.detach')
        self.backend.sim_close(owner.handle)

    def arrival_cases(self):
        from gui.mods.offline_lan_0922 import vehicle_physics
        from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
        scenarios = []
        for rate in (20.,26.,38.,60.):
            for distance in (2.,3.,5.,15.):
                for side in (-1.,1.):
                    scenarios.append(((side*distance,0.,0.),(0.,0.,0.),
                                      0.,0.,rate,True,.2))
        scenarios.extend([
            ((-318.,-.18,-162.),(-315.9337480894199,-.18,-164.84366244795356),
             .9814697507451752,1.7495827038679135,26.,True,.1),
            ((-318.,-.18,-162.),(-315.42137096440297,-.1800001859664917,-162.87952283341613),
             .4442300992425158,2.900211921504135,26.,True,.1),
            ((-3.,0.,0.),(0.,0.,0.),0.,0.,26.,False,.1),
            ((3.,0.,0.),(0.,0.,0.),0.,0.,26.,False,.1),
            ((0.,0.,40.),(0.,0.,0.),0.,0.,26.,True,.1),
        ])
        for scene, data in enumerate(scenarios):
            target,position,yaw,speed,rate,center,control_step = data
            params = dict(vehicle_physics._DEFAULTS,mass=49045.,powerW=514850.,
                terrainResist=(1.3422818563357068,1.4381591562799616,2.2051773272447597),
                rotSpd=math.radians(rate),rotationIsAroundCenter=center)
            owner,runtime = self.owner()
            expected_adapter = BotAdapter('airfield',11,
                navigation_target=lambda *unused: target)
            actual_adapter = BotAdapter('airfield',11,
                navigation_target=lambda *unused: target)
            actual_adapter.driver = owner.driver
            omega,elapsed,tick = 0.,0.,0
            transferred = False
            order = dict(combat_mode='route',move_position=(0.,0.,200.))
            while elapsed < 12.:
                distance = math.hypot(target[0]-position[0],target[2]-position[2])
                if distance <= self.driver.WAYPOINT_ARRIVAL_RADIUS:
                    break
                sample = dict(id=17,team=2,slot=1,position=position,yaw=yaw,
                    speed=speed,dt=control_step,half_length=3.409619092941284,
                    half_width=1.6861989498138428,turn_speed_limit=params['rotSpd'])
                traces = [[],[]]
                def probe(index):
                    def clear(*args):
                        traces[index].append(args)
                        return True
                    return clear
                expected = expected_adapter.decide_with_order(dict(sample),order,probe(0))
                actual = actual_adapter.decide_with_order(dict(sample),order,probe(1))
                path = 'arrival.%d.%d' % (scene,tick)
                self.equal(actual,expected,path+'.command')
                self.equal(traces[1],traces[0],path+'.queries')
                self.equal(owner.driver.states,expected_adapter.driver.states,path+'.state')
                if scene == len(scenarios)-1:
                    assert actual['throttle'] == 1., (path,actual)
                if not transferred and owner.driver.states[17]['coast_target'] is not None:
                    owner.detach()
                    self.equal(runtime.adapter.driver.states,expected_adapter.driver.states,
                               path+'.detached')
                    self.backend.sim_close(owner.handle)
                    handle = self.backend.sim_open(13,17)
                    owner = self.facade.NativeControl(runtime,self.backend,handle)
                    actual_adapter.driver = owner.driver
                    self.equal(owner.driver.states,expected_adapter.driver.states,
                               path+'.restored')
                    transferred = True
                    self.arrival_handoffs += 1
                remaining = control_step
                while remaining > .000001:
                    step = min(remaining,.032)
                    omega = vehicle_physics.traverse_step(params,omega,actual['turn'],
                        speed,step,drive_intent=actual['throttle'])
                    old_yaw = yaw
                    offset = vehicle_physics.track_pivot_offset(params,speed,omega,
                        drive_intent=actual['throttle'])
                    yaw = (yaw+omega*step+math.pi) % (2.*math.pi)-math.pi
                    position = vehicle_physics.track_pivot_position(position,old_yaw,yaw,offset)
                    speed = vehicle_physics.longitudinal_step(params,speed,actual['throttle'],
                        actual['turn'],0.,step)
                    position = (position[0]+math.sin(yaw)*speed*step,position[1],
                                position[2]+math.cos(yaw)*speed*step)
                    remaining -= step
                    elapsed += step
                tick += 1
                self.arrival_ticks += 1
            assert distance <= self.driver.WAYPOINT_ARRIVAL_RADIUS, (scene,elapsed,distance)
            assert expected_adapter.driver.states[17]['recovery_count'] == 0, scene
            owner.detach()
            self.backend.sim_close(owner.handle)
            self.arrival_runs += 1
        assert self.arrival_handoffs > 0

    def driver_failure_cases(self):
        owner, unused_runtime = self.owner()
        original = self.driver.LocalDriver()
        original._state(1, 0, (0.,0.,0.))['stuck_time'] = 10.
        owner._call('sim_control_driver_restore', 1,
                    self.facade._state_row(original.states[1]))
        traces = [[], []]
        def probe(index):
            def failed(yaw, distance=None):
                traces[index].append((yaw, distance))
                raise TypeError('native query failed after observable work')
            return failed
        args = (1,0,(0.,0.,0.),0.,0.,.1,(0.,0.,20.),[])
        expected = original.drive(*(args + (probe(0),)))
        actual = owner.driver.drive(*(args + (probe(1),)))
        self.equal(actual,expected,'native_driver.failed_probe.command')
        self.equal(owner.driver.states,original.states,'native_driver.failed_probe.state')
        self.equal(traces[1],traces[0],'native_driver.failed_probe.calls')
        assert traces[1] == [(math.pi,3.5*1.6)], traces
        assert actual['throttle'] == 0. and actual['recovery_mode'] == 'pivot_recovery'
        owner.detach()
        self.backend.sim_close(owner.handle)
        self.lifecycle_checks += 1

    def stopping_distance_cases(self):
        from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
        params = _effective_params_snapshot()['physics']
        finite = BotRuntime._traffic_stopping_distance(8., params, 0.)
        unbounded = BotRuntime._traffic_stopping_distance(8., params, .2)
        assert 0. < finite < 100.
        assert math.isinf(unbounded) and unbounded > 0.
        owner, unused_runtime = self.owner()
        original = self.driver.LocalDriver()
        # An unbounded coast distance is a legal producer result on a slope.
        # The latch persists when the grade clears, until the hull slows down.
        scenes = (
            (finite, 8., 100., True, True, False, 1., 'drive', None),
            (unbounded, 8., 100., True, True, False, 0., 'drive', (0., 100.)),
            (finite, 8., 100., True, True, False, 0., 'drive', (0., 100.)),
            (finite, .2, 100., True, True, False, 1., 'drive', None),
            (unbounded, 8., 120., True, True, False, 0., 'drive', (0., 120.)),
            (unbounded, 8., 120., False, True, False, 1., 'drive', None),
            (unbounded, 8., 120., True, False, False, 0., 'arrived', None),
            (unbounded, 8., 0., True, True, False, 0., 'arrived', None),
            (unbounded, 8., 120., True, True, True, 1., 'avoid', None),
        )
        for index, scene in enumerate(scenes):
            distance, speed, target_z, stop, moving, avoid, throttle, mode, brake = scene
            traces = [[], []]
            def probe(side):
                def clear(yaw, maximum_distance=None):
                    traces[side].append((yaw, maximum_distance))
                    return not avoid or abs(yaw) > .2
                return clear
            args = (1, 0, (0., 0., 0.), 0., speed, .1,
                    (0., 0., target_z), [])
            kwargs = dict(stopping_distance=distance, stop_at_target=stop,
                          movement_intent=moving, decision_horizon=.2)
            expected = original.drive(*(args + (probe(0),)), **kwargs)
            actual = owner.driver.drive(*(args + (probe(1),)), **kwargs)
            path = 'driver.stopping_distance.%d' % index
            self.equal(actual, expected, path + '.command')
            self.equal(owner.driver.states, original.states, path + '.state')
            self.equal(traces[1], traces[0], path + '.queries')
            assert actual['throttle'] == throttle, (path, actual)
            assert actual['recovery_mode'] == mode, (path, actual)
            assert owner.driver.states[1]['braking_target'] == brake, path
            self.stopping_distance_ticks += 1
        owner.detach()
        self.backend.sim_close(owner.handle)

    def driver_input_rejection_cases(self):
        # Only the stopping-distance slot admits positive infinity. Exercise
        # every other numeric ingress slot, including unused flags and peers.
        base = [1, [0., 0., 0.], [0., 0., 100.],
                [0, 0., 8., .1, 3.5, 1.7, 1, 1, 1, 15., .2, 0., 100., 0.]]
        peer = [2, [30., 0., 30.], [0., 0., 0.],
                [1, 1, 0., 3.5, 1.7, 1, -.8, 2.]]
        malformed = []
        nonfinite = (('nan', float('nan')), ('positive_inf', float('inf')),
                     ('negative_inf', -float('inf')))
        for label, value in nonfinite:
            for field in range(14):
                if field == 9 and value > 0.:
                    continue
                row = copy.deepcopy(base)
                row[3][field] = value
                malformed.append(('value.%d.%s' % (field, label), row, []))
            for vector in (1, 2):
                for axis in range(3):
                    row = copy.deepcopy(base)
                    row[vector][axis] = value
                    malformed.append(('point.%d.%d.%s' % (vector, axis, label), row, []))
            for field, width in ((1, 3), (2, 3), (3, 8)):
                for index in range(width):
                    body = copy.deepcopy(peer)
                    body[field][index] = value
                    malformed.append(('peer.%d.%d.%s' % (field, index, label),
                                      copy.deepcopy(base), [body]))
        for label, row, peers in malformed:
            owner, unused_runtime = self.owner()
            original = self.driver.LocalDriver()
            traces = [[], []]
            def probe(side):
                def clear(yaw, maximum_distance=None):
                    traces[side].append((yaw, maximum_distance))
                    return True
                return clear
            args = (1, 0, (0., 0., 0.), 0., 8., .1, (0., 0., 100.), [])
            kwargs = dict(stopping_distance=15., decision_horizon=.2)
            original.drive(*(args + (probe(0),)), **kwargs)
            owner.driver.drive(*(args + (probe(1),)), **kwargs)
            before = copy.deepcopy(owner.driver.states)
            callbacks = []
            def dispatch(*values):
                callbacks.append(values)
                return 1
            assert self.backend.sim_control_drive(
                owner.handle, row, peers, dispatch) is None, label
            assert callbacks == [], label
            if label == 'value.9.negative_inf':
                rejected = dict(kwargs, stopping_distance=-float('inf'))
                try:
                    owner.driver.drive(*(args + (dispatch,)), **rejected)
                except RuntimeError as error:
                    message = str(error)
                    values = list(row[3])
                    for flag in (6, 7, 8):
                        values[flag] = bool(values[flag])
                    inputs = (1, tuple(row[1]), tuple(row[2]), tuple(values))
                    assert 'Native control operation failed: sim_control_drive' in message
                    assert 'inputs=%r' % (inputs,) in message, message
                    assert 'neighbours=0' in message, message
                else:
                    raise AssertionError('invalid facade input was accepted')
                assert callbacks == [], label
                self.lifecycle_checks += 1
            self.equal(owner.driver.states, before, label + '.mirror_unchanged')
            # The next accepted command exposes the native state as well as
            # the Python mirror, so a rejected parse cannot advance its clock.
            traces[0][:] = []
            traces[1][:] = []
            expected = original.drive(*(args + (probe(0),)), **kwargs)
            actual = owner.driver.drive(*(args + (probe(1),)), **kwargs)
            self.equal(actual, expected, label + '.continued_command')
            self.equal(owner.driver.states, original.states, label + '.continued_state')
            self.equal(traces[1], traces[0], label + '.continued_queries')
            owner.detach()
            self.backend.sim_close(owner.handle)
            self.driver_input_rejections += 1

    def traffic_cases(self):
        owner, runtime = self.owner()
        original = self.traffic.TrafficCoordinator()
        rng = random.Random(9447)
        for scene in range(8):
            for step in range(65):
                now = (scene * 65 + step) * 0.1
                bodies = []
                for actor in range(1, 7):
                    yaw = (math.pi if actor % 2 else 0.0) if scene < 4 else actor * math.pi / 2.0
                    x = ((actor-1)//2)*3.0 if scene < 4 else -8.0 + actor*2.0
                    z = (5.0 if actor % 2 else -5.0) - math.cos(yaw)*step*.08
                    speed = 0.0 if step > 35 else (2.0 if scene != 7 else -2.0)
                    bodies.append(dict(id=actor, team=1 if scene != 6 else actor % 2,
                        alive=not (scene == 5 and actor == 3), position=(x,0.0,z),
                        velocity=(math.sin(yaw)*speed,0.0,math.cos(yaw)*speed),
                        yaw=yaw, half_width=1.7, half_length=3.5,
                        shape=(1.7,3.5,-0.8,2.0)))
                for body in bodies:
                    command = dict(throttle=1.0, turn=.15, target_yaw=body['yaw'],
                                   recovery_mode='drive', combat_mode='route', opaque='kept')
                    traces = [[], []]
                    def probe(index):
                        def clear(yaw):
                            traces[index].append(yaw)
                            return scene % 2 == 0 or step >= 40
                        return clear
                    expected = original.adjust(body['id'], body, command, bodies, now, probe(0))
                    actual = owner.traffic.adjust(body['id'], body, command, bodies, now, probe(1))
                    self.equal(actual, expected, 'traffic.command')
                    self.equal(traces[1], traces[0], 'traffic.query')
                    self.traffic_ticks += 1
                owner.traffic.detach()
                self.equal(runtime._traffic_coordinator.original._pairs,
                           original._pairs, 'traffic.pairs')
                self.equal(runtime._traffic_coordinator.original._held,
                           original._held, 'traffic.held')
        owner.detach()
        self.backend.sim_close(owner.handle)

    def perception_cases(self):
        from gui.mods.offline_lan_0922 import bot_runtime as laws
        from gui.mods.offline_lan_0922 import spotting
        for asynchronous in (False, True):
            owner, unused_runtime = self.owner()
            reference = laws.BotRuntime(1)
            config, profiles = {}, {}
            rng = random.Random(8531)
            for actor in range(1, 31):
                team = 1 if actor <= 15 else 2
                position = (rng.uniform(-170., 170.), (actor % 3)*8.,
                            (-90. if team == 1 else 90.) + rng.uniform(-40.,40.))
                reference.states[actor] = dict(id=actor, kind='bot', network_id=actor,
                    team=team, slot=(actor-1)%15, alive=True, health=600, max_health=600,
                    x=position[0], y=position[1], z=position[2], yaw=.03*actor,
                    pitch=.01, roll=-.01, speed=0., fire_seq=0,
                    velocity=(0.,0.,0.), profile={'class_tag':'mediumTank'}, armor=80.)
                moving, still = .04+actor*.001, .15+actor*.001
                profile = dict(invisibility_moving=(.01,1.05),
                    invisibility_still=(.06,1.08), has_camouflage_net=actor%4==0,
                    camouflage_net_delay=1.0)
                profiles[actor] = ((moving, still), .4, profile)
                view = 270. + actor*2.
                delay = .8 if actor%3==0 else None
                reference._vision_ranges[actor] = (view, view*1.2 if delay is not None else view, delay)
                radio = 0. if actor%11==0 else 80. + actor*3.
                config[actor] = (team,(actor-1)%15,3.5,1.7,view,
                    view*1.2 if delay is not None else view,
                    -1. if delay is None else delay,still,moving,.01,.05,1.05,1.08,.4,
                    1. if profile['has_camouflage_net'] else -1.,radio,0.,0.)
                owner.configure((1,actor),config[actor])
            reference._spotting_profile = lambda target, tick=None: profiles[target['id']]
            reference._source_radio_range = lambda source, tick=None: config[source['id']][15]
            traces = [[], []]
            pending = [{}, {}]
            frame = [0]
            def response(index, source, target, fired, now, detection=None):
                pair = (source,target)
                traces[index].append((source,target,fired))
                clear = (source*7+target*11+frame[0]//5)%9 not in (0,1)
                foliage = .2 if (source+target)%5==0 else 0.
                if asynchronous:
                    if pair not in pending[index]:
                        camo = spotting.effective_camouflage(detection[2],
                            moving=detection[3], fired_recently=detection[4],
                            additive=detection[5], multiplier=detection[6],
                            shot_factor=detection[7], foliage_bonus=foliage)
                        pending[index][pair] = dict(line_of_sight=clear,
                            foliage_bonus=foliage, sampled_at=now,
                            detected=spotting.is_detected(detection[0],detection[1],camo,clear))
                        return None
                    return pending[index].pop(pair)
                return dict(line_of_sight=clear,foliage_bonus=foliage)
            if asynchronous:
                reference.visibility_async_probe = lambda source,target,fired,now,fire,detection: response(0,source['id'],target['id'],fired,now,detection)
            else:
                reference.visibility_probe = lambda source,target,fired=False: response(0,source['id'],target['id'],fired,frame[0]*.1)
            def native_probe(source,target,now,fire,detection,unused):
                d=(detection[0],detection[1],tuple(detection[2:4]),bool(detection[4]),
                   bool(detection[5]),detection[6],detection[7],detection[8])
                value=response(1,source[1],target[1],bool(detection[5]),now,d)
                if value is None:
                    return 2,0,0,0.,now
                return (0 if value['line_of_sight'] else 1,
                    int('detected' in value),int(value.get('detected',False)),
                    value['foliage_bonus'],value.get('sampled_at',now))
            for index in range(95):
                frame[0]=index
                now=index*.1
                if index in (18,45,64):
                    for actor in (1,4,17,23,29):
                        reference.states[actor]['fire_seq'] += 1
                for actor,state in reference.states.items():
                    state['alive'] = not (25<=index<38 and actor%5==0)
                    state['speed'] = 2. if (index+actor)%13<4 else 0.
                if index == 65:
                    for actor,state in reference.states.items():
                        state['z'] += 500. if actor > 15 else 0.
                if index == 80:
                    for actor,state in reference.states.items():
                        state['z'] -= 500. if actor > 15 else 0.
                order=[(1,actor) for actor in reference.states]
                samples=[(key,self.facade._pose(reference.states[key[1]]),
                    reference.states[key[1]]['fire_seq'],
                    8|int(reference.states[key[1]]['alive']),None) for key in order]
                owner.update_samples(samples)
                owner.begin_samples(order,now,48,asynchronous,False)
                if index == 0 or index % 4 != 1:
                    reference._begin_visibility_frame()
                reference._prepare_visibility_frame([],now,False)
                reference._configure_radio([],now,{})
                tick={}
                processed=set()
                traces[0][:]=[];traces[1][:]=[]
                for source in reference._ordered_states():
                    if not source['alive']:
                        continue
                    actor=source['id']
                    reference._note_source_stillness(source,now)
                    expected,unused_lookup=reference._contacts_for(source,[],now,{},tick,processed)
                    actual=owner.contacts((1,actor),native_probe)
                    self.equal([row[0][1] for row in actual],
                               [row['id'] for row in expected],'perception.order')
                    for row,expected_target in zip(actual,expected):
                        key,flags,remaining,sampled,pose=row
                        self.equal((bool(flags&1),bool(flags&2),bool(flags&4)),
                            (expected_target['visible'],expected_target['direct_visible'],
                             expected_target['fresh_visible']),'perception.flags.%d.%d.%d'%(index,actor,key[1]))
                        expected_pose=dict((name,expected_target[name])
                            for name in ('position',)+self.facade._POSE_NAMES if name in expected_target)
                        actual_pose=self.facade._pose_dict(pose) if pose is not None else dict(
                            position=(0.,0.,0.),x=0.,y=0.,z=0.,yaw=0.,speed=0.)
                        self.equal(actual_pose,expected_pose,'perception.pose.%d.%d.%d'%(index,actor,key[1]))
                        self.contacts += 1
                    # Preserve the old per-actor pre/post motion observation order.
                    source['z'] += .015*actor
                    source['yaw'] += .0001
                    owner.update_samples((((1,actor),self.facade._pose(source),
                        source['fire_seq'],8|int(source['alive']),None),))
                    processed.add(actor)
                self.equal(traces[1],traces[0],'perception.query_order.%d'%index)
                self.visibility_queries += len(traces[1])
                actual_team=dict(((row[0],'bot',row[1][1]),(row[2],self.facade._pose_dict(row[3])))
                    for row in owner.team_contacts())
                expected_team=dict((key,(min(12.,deadline-now),reference._visible_target_poses[key]))
                    for key,deadline in reference._spot_until.items() if deadline>now)
                self.equal(actual_team,expected_team,'perception.team')
                if index != 94 and index % 4 == 0:
                    continue
                reference._finish_visibility_frame()
                owner.finish()
                # Snapshot includes empty observer dictionaries and dict tombstones.
                snap=owner._call('sim_control_snapshot',None)
                native_radio=dict((self.facade._wire_key(observer),dict(
                    (self.facade._wire_key(row[0]),(row[1],row[2],self.facade._pose_dict(row[3]),bool(row[4])))
                    for row in targets)) for observer,targets in snap[1])
                self.equal(native_radio,reference._radio_network.observations,'perception.radio')
                native_order=[self.facade._wire_key(row[1]) for row in snap[2] if row[0]==1]
                self.equal(native_order,list(reference._radio_network.observations),'perception.observer_order')
                native_wait=[self.facade._wire_key(row[0])+self.facade._wire_key(row[1]) for row in snap[5]]
                self.equal(native_wait,reference._visibility_waiting,'perception.waiting')
                if index==49:
                    owner._call('sim_control_snapshot',snap)
                    self.equal(owner._call('sim_control_snapshot',None),snap,'perception.roundtrip')
                    # Restoring an authority deliberately requeues old async preparations.
                    if asynchronous:
                        reference._visibility_inflight.clear()
            owner.detach()
            self.backend.sim_close(owner.handle)

    def projection_cache_cases(self):
        from gui.mods.offline_lan_0922 import bot_runtime as laws
        runtime = laws.BotRuntime(1)
        runtime.adapter = Object()
        runtime.adapter.driver = self.driver.LocalDriver()
        state = dict(id=1, kind='bot', team=1, slot=0, alive=True,
                     x=0., y=0., z=0., yaw=0., speed=0., fire_seq=0,
                     health=600, max_health=600, view_range=350.)
        runtime.states = {1: state}
        runtime._descriptors[1] = dict(radio=dict(distance=180.))
        runtime._vision_ranges[1] = (350., 450., .5)
        profile = ((.1, .15), .4, dict(invisibility_moving=(0., 1.),
            invisibility_still=(.05, 1.), has_camouflage_net=True,
            camouflage_net_delay=.4))
        runtime._spotting_profiles[('bot', 1)] = profile
        runtime._spotting_profile = lambda source, tick=None: (
            runtime._spotting_profiles[('bot', source['id'])])
        runtime._source_radio_range = lambda source, tick=None: (
            runtime._descriptors[source['id']]['radio']['distance'] *
            laws._critical_factor(source, runtime._descriptors[source['id']],
                                  'signal'))
        owner = self.facade.NativeControl(
            runtime, self.backend, self.backend.sim_open(1, 81))
        project = owner._project_actor_config
        calls = []
        def counted(source, tick):
            calls.append(1)
            return project(source, tick)
        owner._project_actor_config = counted
        def check(expected_builds):
            actual = owner._actor_config(state, {})
            self.equal(actual, project(state, {}), 'projection.cached_mechanics')
            assert len(calls) == expected_builds
        check(1)
        # Moving, firing, damage to hull HP and terminal pose do not change
        # the immutable view/radio mechanics; pose samples still update.
        state.update(x=50., speed=7., fire_seq=3, health=400, alive=False)
        check(1)
        state['critical'] = {'crew_ko': ['commander', 'radioman']}
        check(2)
        # A reference-only cache would incorrectly reuse the damaged factor.
        state['critical']['crew_ko'][:] = []
        check(3)
        state['critical'] = copy.deepcopy(state['critical'])
        check(3)
        state['critical']['devices'] = [dict(
            name='radioHealth', hp=1., state='critical')]
        check(4)
        state['critical']['devices'][0]['state'] = 'normal'
        state['critical']['devices'][0]['hp'] = 100.
        check(5)
        runtime._vision_ranges[1] = (370., 470., .7)
        check(6)
        runtime._descriptors[1] = dict(radio=dict(distance=240.))
        check(7)
        runtime._spotting_profiles[('bot', 1)] = (
            (.2, .3), .5, dict(profile[2]))
        check(8)
        runtime._bot_behavior[1] = {'crew_level': 75}
        check(9)
        state['team'], state['slot'] = 2, 5
        check(10)
        state['view_range'] = 390.
        check(11)
        owner.detach()
        assert not owner._config_projections
        self.backend.sim_close(owner.handle)
        self.lifecycle_checks += 1

    def projection_cases(self):
        from gui.mods.offline_lan_0922 import bot_runtime as laws
        runtime = laws.BotRuntime(1)
        runtime.adapter = Object()
        runtime.adapter.driver = self.driver.LocalDriver()
        runtime.states = dict((actor,dict(id=actor,team=actor,slot=actor-1,
            alive=True,x=0.,y=0.,z=actor*60.,yaw=0.,speed=0.,fire_seq=0,
            health=600,max_health=600,view_range=350.)) for actor in (1,2))
        runtime._descriptors = dict((actor,dict(radio=dict(distance=180.)))
                                    for actor in (1,2))
        runtime._vision_ranges = {1:(350.,450.,.5),2:(350.,450.,.5)}
        runtime._spotting_profile = lambda source,tick=None: (
            (.1,.15),.4,dict(invisibility_moving=(0.,1.),
            invisibility_still=(.05,1.),has_camouflage_net=True,
            camouflage_net_delay=.4))
        runtime._source_radio_range = lambda source,tick=None: (
            runtime._descriptors[source['id']]['radio']['distance'] *
            laws._critical_factor(source,runtime._descriptors[source['id']],'signal'))
        schedule_calls, selected_calls = [],[]
        def due(source,now):
            schedule_calls.append(source['id'])
            return source.get('test_due',True)
        def selected(source):
            selected_calls.append(source['id'])
            return source.get('test_selected')
        runtime._visibility_decision_due = due
        runtime._selected_visibility_target = selected
        owner = self.facade.NativeControl(runtime,self.backend,self.backend.sim_open(1,80))
        owner.begin([],1.,False,{})
        source = runtime.states[1]
        first_template = owner._templates[(1,1)]
        first_projection = owner._pose_free[(1,1)]
        before_config = owner._config[(1,1)]
        source.update(x=7.,health=550,fire_seq=1,test_due=False,
                      test_selected=('bot',2),critical={'crew_ko':['commander','radioman']})
        owner.update_actor(source,processed=False)
        assert schedule_calls == selected_calls == [1,2]
        assert owner._templates[(1,1)] is first_template
        assert owner._pose_free[(1,1)] is first_projection
        assert owner._config[(1,1)] != before_config
        factor = laws.device_damage.clamp_vision_factor(
            laws._critical_factor(source,runtime._descriptors[1],'vision'))
        self.equal(owner._config[(1,1)][4],350.*factor,'projection.critical_view')
        # A descriptor/siege change remains visible within this same slice.
        runtime._descriptors[1] = dict(radio=dict(distance=240.))
        runtime._vision_ranges[1] = (370.,470.,.7)
        owner.update_actor(source,processed=True)
        assert owner._templates[(1,1)] is not first_template
        assert owner._pose_free[(1,1)] is not first_projection
        assert owner._pose_free[(1,1)]['health'] == 550
        self.equal(owner._config[(1,1)][4],370.*factor,'projection.changed_descriptor_view')
        self.equal(owner._config[(1,1)][15],runtime._source_radio_range(source),
                   'projection.changed_descriptor_radio')
        rows = owner.contacts((1,2),lambda *unused:(0,1,1,0.,1.))
        assert rows[0][0] == (1,1) and rows[0][4][0] == (7.,0.,60.)
        owner.finish()
        owner.begin([],1.1,False,{})
        assert schedule_calls == selected_calls == [1,2,1,2]
        assert owner._scheduling[(1,1)] == (False,(1,2))
        source['alive'] = False
        owner.update_actor(source)
        assert (1,1) not in owner._templates and (1,1) not in owner._pose_free
        assert owner.contacts((1,2),lambda *unused:(0,1,1,0.,1.1)) == ()
        owner.detach()
        self.backend.sim_close(owner.handle)
        self.lifecycle_checks += 1

    def human_cases(self):
        from gui.mods.offline_lan_0922 import bot_runtime as laws
        params = _effective_params_snapshot()
        params['crew']['members'] = [
            dict(instance='gunner', roles=['gunner'], skills=[dict(
                name='gunner_rancorous', level=100., active=True, enabled=True)]),
            dict(instance='radioman', roles=['radioman'], skills=[dict(
                name=name, level=100., active=True, enabled=True) for name in
                ('radioman_lasteffort', 'radioman_retransmitter')])]
        params['skills'].update(designated_target=True, last_effort=True)
        dynamic = params['crew']['dynamic_spotting']
        row = dynamic['states']['0:0']
        dynamic['crew'] = ['gunner', 'radioman']
        dynamic['states'] = dict(('%d:%d' % (mask, fire), dict(row,
            vision=.5 if mask & 1 else 1., signal=.5 if mask & 2 else 1.))
            for mask in range(4) for fire in (0, 1))
        params['spotting'].update(has_binoculars=True, binocular_factor=1.25,
            binocular_delay=.3, has_camouflage_net=True, camouflage_net_delay=.4)
        descriptor = dict(turret=dict(circularVisionRadius=400.),
                          radio=dict(distance=180.), miscAttrs={})
        scenes, traces = [], [[], []]
        for side in (0, 1):
            runtime = laws.BotRuntime(1)
            runtime.adapter = Object()
            runtime.adapter.driver = self.driver.LocalDriver()
            runtime._player_vehicle_profile = lambda raw, tick=None: dict(
                descriptor=descriptor, class_tag='mediumTank', armor=80.)
            original_profile = runtime._spotting_profile
            original_radio = runtime._source_radio_range
            def profile(source, tick=None, original=original_profile):
                if source.get('kind') != 'bot':
                    return original(source, tick)
                return ((.10, .15), .4, dict(invisibility_moving=(0., 1.),
                    invisibility_still=(.05, 1.), has_camouflage_net=True,
                    camouflage_net_delay=.4))
            def radio(source, tick=None, original=original_radio):
                return (180. if source.get('kind') != 'human' else original(source, tick))
            def probe(source, target, fired=False, side=side):
                traces[side].append((self.facade._key(source), self.facade._key(target),
                                     fired, tuple(sorted(source))))
                return dict(line_of_sight=True, foliage_bonus=.1)
            runtime._spotting_profile = profile
            runtime._source_radio_range = radio
            runtime.visibility_probe = probe
            runtime.states = dict((actor, dict(id=actor,
                team=1 if actor < 13 else 2, slot=actor-11, alive=True,
                x=0., y=0., z=80.+(actor-11)*25., yaw=0., speed=0., fire_seq=0,
                health=600, max_health=600, view_range=350.,
                profile={'class_tag':'mediumTank'}, armor=80.)) for actor in range(11,15))
            scenes.append(runtime)
        owner = self.facade.NativeControl(scenes[1], self.backend, self.backend.sim_open(1, 40))
        for frame in range(65):
            now = frame * .1
            results = []
            for side, runtime in enumerate(scenes):
                traces[side][:] = []
                players = [dict(id=actor, team=1 if actor < 3 else 2,
                    alive=not (actor == 1 and 10 <= frame < 35),
                    vehicle='test:projected', x=0. if actor == 1 else actor*70.,
                    y=0., z=(900. if actor == 3 and 15 <= frame < 33 else 0.),
                    yaw=0., aim_yaw=0. if frame < 5 else .10,
                    speed=2. if 42 <= frame < 46 else 0., fire_seq=int(frame >= 14),
                    health=600, max_health=600,
                    critical={'crew_ko': ['radioman'] if actor == 1 and 10 <= frame < 35 else
                        (['gunner'] if actor == 2 and 40 <= frame < 44 else [])},
                    effective_params=copy.deepcopy(params)) for actor in (1,2,3)
                    if not (actor == 2 and 20 <= frame < 30)]
                tick, aggregate, team, processed = {}, {}, {}, set()
                if side == 0:
                    runtime._begin_visibility_frame()
                    runtime._track_human_observer_lifecycle(players, now, tick)
                    runtime._configure_radio(players, now, tick)
                    runtime._prepare_visibility_frame(players, now, True)
                    runtime._append_human_observations(players, now, aggregate, team, tick)
                else:
                    owner.begin_frame()
                    owner.begin(players, now, True, tick)
                    owner.append_human_observations(players, now, aggregate, team, tick)
                contacts = []
                for source in runtime._ordered_states():
                    runtime._note_source_stillness(source, now)
                    if side:
                        owner.note_source_stillness(source, now)
                    rows, lookup = (runtime._contacts_for(source, players, now, team, tick, processed)
                        if side == 0 else owner.contacts_for(source, players, now, team, tick, processed))
                    contacts.append(rows)
                    source['z'] += .01*source['id']
                    if side:
                        owner.update_actor(source)
                    processed.add(source['id'])
                actors = runtime._radio_network.actors
                radio = dict((actor, dict((('bot', target), runtime._radio_network.contact(
                    actor, ('bot', target), now)) for target in runtime.states)) for actor in actors)
                if side:
                    pairs = tuple((actor,target) for actor in sorted(actors)
                        for target in ([('bot',i) for i in runtime.states] +
                                       [('human',1),('human',2),('human',3)]))
                    pairs += (pairs[-1],pairs[0],(('bot',99),('human',98)))
                    expected = tuple(runtime._radio_network.contact(a,b,now)[:2]
                                     for a,b in pairs)
                    def pose_unread(unused):
                        raise AssertionError('radio summary crossed an unread pose')
                    project_pose = self.facade._pose_dict
                    self.facade._pose_dict = pose_unread
                    try:
                        actual = runtime._radio_network.summaries(pairs,now)
                        assert runtime._radio_network.summaries((),now) == ()
                    finally:
                        self.facade._pose_dict = project_pose
                    self.equal(actual,expected,'humans.radio_summary.%d'%frame)
                    self.radio_summaries += len(pairs)
                links = dict((actor, tuple(other for other in sorted(actors)
                    if runtime._radio_network.connected(actor, other))) for actor in actors)
                results.append(dict(contacts=contacts, aggregate=aggregate, team=team,
                    radio=radio, links=links, actors=actors, queries=list(traces[side]),
                    vision=tick.get('player_vision_ranges'),
                    leases=dict(runtime._spot_until), poses=copy.deepcopy(runtime._visible_target_poses)))
                if side:
                    owner.finish()
                else:
                    runtime._finish_visibility_frame()
            self.equal(results[1], results[0], 'humans.frame.%d' % frame)
            if frame == 0:
                assert scenes[0]._spot_until[(1, 'bot', 13)] == 12.
            if 10 <= frame < 30:
                assert scenes[0]._human_vengeance_until[1] == 3.
                assert 1 in results[0]['aggregate'][(1, 'bot', 13)][3]
            if 10 <= frame < 35:
                assert not any(row[0] == (0,1) for row in traces[0])
            if frame == 30:
                assert 1 not in results[0]['aggregate'][(1, 'bot', 13)][3]
            # Restore inside the active Last Effort lease, then restore alive state.
            if frame in (19,39):
                owner.detach()
                self.backend.sim_close(owner.handle)
                owner = self.facade.NativeControl(scenes[1], self.backend,
                                                   self.backend.sim_open(1,41+frame))
            self.human_frames += 1
        owner.detach()
        self.backend.sim_close(owner.handle)

    def lifecycle(self):
        owner, runtime = self.owner()
        original_driver, original_traffic = owner._old_driver, owner._old_traffic
        calls = []
        def close_during_query(unused_yaw, unused_distance=None):
            calls.append(1)
            self.backend.sim_close(owner.handle)
            return True
        try:
            owner.driver.drive(1, 0, (0.,0.,0.), 0., 0., .1,
                               (0.,0.,20.), [], close_during_query)
        except RuntimeError:
            pass
        else:
            raise AssertionError('closed callback committed a command')
        assert calls == [1]
        assert owner.driver.states == {}
        assert self.backend.sim_control_finish(owner.handle) is None
        # A closed context cannot be read by a newly opened lifetime.
        second = self.backend.sim_open(13, 18)
        assert second != owner.handle
        assert self.backend.sim_control_driver_event(second, 1, 1, .1, 0.) is None
        self.backend.sim_close(second)
        self.lifecycle_checks += 4
        class FailingBackend(object):
            def __getattr__(self, name):
                if name == 'sim_control_traffic_state':
                    return lambda *unused: None
                return getattr(self_backend, name)
        self_backend = self.backend
        runtime.adapter.driver = original_driver
        runtime._traffic_coordinator = original_traffic
        handle = self.backend.sim_open(13, 19)
        try:
            self.facade.NativeControl(runtime, FailingBackend(), handle)
        except RuntimeError:
            pass
        else:
            raise AssertionError('partial construction unexpectedly succeeded')
        assert runtime.adapter.driver is original_driver
        assert runtime._traffic_coordinator is original_traffic
        self.backend.sim_close(handle)
        self.lifecycle_checks += 1
        owner, runtime = self.owner()
        owner.driver.drive(1, 0, (0.,0.,0.), 0., 0., .1, (0.,0.,20.), [],
                           lambda *unused: True)
        committed = copy.deepcopy(owner.driver.states)
        for actor in (1,2):
            owner.configure((1,actor), (actor,0,3.5,1.7,400.,400.,-1.,
                0.,0.,0.,0.,1.,1.,1.,-1.,300.,0.,0.))
        owner.update_samples(tuple(((1,actor), self.facade._pose(dict(
            x=0.,y=0.,z=100.*(actor-1),yaw=0.,speed=0.)),0,9,None)
            for actor in (1,2)))
        owner.begin_samples(((1,1),(1,2)),0.,48)
        sight_calls = []
        def close_during_sight(*unused):
            sight_calls.append(1)
            self.backend.sim_close(owner.handle)
            return 0,0,0,0.,0.
        try:
            owner.contacts((1,1),close_during_sight)
        except RuntimeError:
            pass
        else:
            raise AssertionError('closed sight callback published contacts')
        assert sight_calls == [1]
        try:
            owner.detach()
        except RuntimeError:
            pass
        else:
            raise AssertionError('closed context unexpectedly exported native state')
        assert runtime.adapter.driver is owner._old_driver
        assert runtime._traffic_coordinator is owner._old_traffic
        self.equal(runtime.adapter.driver.states, committed, 'closed.detach.driver')
        owner.detach()
        self.lifecycle_checks += 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension', required=True)
    parser.add_argument('--client-scripts', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'src', 'res', 'scripts', 'client'))
    args = parser.parse_args()
    assert sys.version_info[:2] == (2, 7), 'Run with the pinned CPython 2.7 interpreter'
    facade, driver, traffic = load_sources(args.client_scripts)
    backend = imp.load_dynamic('offline_math_batch_native', args.extension)
    checker = Checker(backend, facade, driver, traffic)
    checker.driver_cases()
    checker.arrival_cases()
    checker.driver_failure_cases()
    checker.driver_input_rejection_cases()
    checker.stopping_distance_cases()
    checker.traffic_cases()
    checker.perception_cases()
    checker.projection_cases()
    checker.projection_cache_cases()
    checker.human_cases()
    checker.lifecycle()
    print(json.dumps(dict(ok=True, comparisons=checker.comparisons,
        driver_ticks=checker.driver_ticks, traffic_ticks=checker.traffic_ticks,
        contacts=checker.contacts, visibility_queries=checker.visibility_queries,
        human_frames=checker.human_frames,
        arrival_runs=checker.arrival_runs, arrival_ticks=checker.arrival_ticks,
        arrival_handoffs=checker.arrival_handoffs,
        radio_summaries=checker.radio_summaries,
        stopping_distance_ticks=checker.stopping_distance_ticks,
        driver_input_rejections=checker.driver_input_rejections,
        lifecycle_checks=checker.lifecycle_checks,
        maximum_difference=checker.maximum_difference), sort_keys=True))


if __name__ == '__main__':
    main()
