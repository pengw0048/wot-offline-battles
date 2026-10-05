"""Exercise persistent motion with source-derived drive and real owner frontiers.

Usage (CPython 2.7): check_native_simulation_motion.py EXTENSION [--scene HOST_SCENE]
The analytic engine and descriptors are explicit host fixtures. This checks the
complete physical stage, not planning/combat throughput or Windows acceptance.
"""
from __future__ import print_function
import argparse
import copy
import imp
import json
import math
import os
import re
import textwrap


def near(a, b, label):
    if isinstance(b, dict):
        assert set(a) == set(b), (label, a, b)
        for key in b:
            near(a[key], b[key], label + '/' + str(key))
    elif isinstance(b, (tuple, list)):
        assert len(a) == len(b), (label, a, b)
        for i, pair in enumerate(zip(a, b)):
            near(pair[0], pair[1], label + '[%d]' % i)
    elif isinstance(b, bool):
        assert a == b, (label, a, b)
    elif b is None or isinstance(b, (str, bytes)):
        assert a == b, (label, a, b)
    else:
        assert abs(float(a) - float(b)) <= 2.e-7 * max(1., abs(float(b))), (label, a, b)


def reference_drive(module):
    # Use the maintained mature drive block verbatim. This is not another
    # hand-written acceleration or collision oracle.
    source = open(module.__file__.replace('.pyc', '.py')).read()
    match = re.search(r"(?m)^([ \t]+)params = self\._physics_params_for\(state\['id'\]\)\n\1# Direction probes", source)
    if match is None:
        raise RuntimeError('Maintained physical reference block is missing')
    start = match.start()
    tail = re.search(r"(?m)^[ \t]+if diagnostic is not None:\n[ \t]+diagnostic.phase\('bot.aim_fire'\)", source[start:])
    if tail is None:
        raise RuntimeError('Physical reference boundary is missing')
    end = start + tail.start()
    body = textwrap.dedent(source[start:end])
    prefix = '''def drive(self,state,step,now,command,motion_probe,throttle,turn,path_clear,pose_frozen,travel_sign,neighbours,descriptor,siege_motion_locked):
    position = _position(state)
    travel_yaw = state['yaw'] + (math.pi if travel_sign < 0. else 0.)
    steer_dir = 1 if turn > .01 else -1 if turn < -.01 else 0
    state['movement_dir'] = 1 if throttle > .01 else -1 if throttle < -.01 else 0
    state['rotation_dir'] = steer_dir
    state.pop('_contact_motor_turn', None)
    baked_shallow_escape = False
    controlled_shallow = controlled_shallow_commit = None
    exact_motion_owns_collision = callable(self.motion_resolver)
    passive_forwards = {}
    attempted_yaws = {}
    navigation_grid = getattr(self.navigator,'grid',None)
'''
    namespace = dict(module.__dict__)
    code = prefix + ''.join('    ' + line + '\n' for line in body.splitlines())
    code += '    return passive_forwards,attempted_yaws\n'
    exec(compile(code, '<maintained physical drive>', 'exec'), namespace)
    return namespace['drive']


def run_reference(scene, module, commands, step, now, drive):
    runtime = scene.runtime
    players = [scene.human]
    runtime._contact_players, runtime._contact_now = players, now
    runtime._consume_human_contact_pushes(players, now)
    ticks, safe, attempted, passive, locked, integrated = {}, {}, {}, {}, {}, set()
    for state in runtime.states.values():
        if not state.get('alive'):
            continue
        runtime._advance_bot_drowning(state, step)
        runtime._advance_bot_overturn(state, step)
        if not state.get('alive'):
            continue
        siege_locked = runtime._advance_bot_siege(state, step)
        actor = state['id']
        if getattr(scene, 'new_siege', False) and scene.frame_index == 0 and actor == 16:
            runtime._set_bot_siege_state(state, 1, 2., 2.)
            siege_locked = True
        ticks[actor] = module._position(state)
        safe[actor] = module.prebaked_navigation.pose_is_safe(
            runtime.baked_graph, ticks[actor], shoulder_cells=0,
            hazard_mask=module.prebaked_navigation.MOTION_FATAL_HAZARDS)
        if siege_locked:
            locked[actor] = (state['x'], state['z'], state['yaw'])
        throttle, turn, slope = commands[actor]
        if siege_locked:
            throttle = turn = 0.
            state['speed'] = state['push_x'] = state['push_z'] = 0.
            state['movement_dir'] = state['rotation_dir'] = 0
            runtime._turn_speeds[actor] = 0.
        probe = {'clear': True, 'collision': False, 'slope': slope}
        state['speed'] = state.get('speed', 0.0)
        f, a = drive(runtime, state, step, now,
            {'move_position': (state['x'], state['y'], state['z']+30.)}, probe,
            throttle, turn, True, False, 1., (), runtime._descriptors[actor], siege_locked)
        passive.update(f)
        attempted.update(a)
        integrated.add(actor)
    for actor, pose in locked.items():
        state = runtime.states[actor]
        state['x'], state['z'], state['yaw'] = pose
        state['speed'] = state['push_x'] = state['push_z'] = 0.
        state['movement_dir'] = state['rotation_dir'] = 0
        runtime._turn_speeds[actor] = 0.
    ordered = runtime._ordered_states()
    settled, ballistic, blocked, pose_rollback = {}, {}, {}, {}
    for state in ordered:
        actor = state['id']
        if state.get('alive') and actor in integrated:
            previous_air = bool(state.get('airborne'))
            driven = module._position(state)
            blocked[actor] = runtime._update_vertical_motion(state, step, ticks[actor], attempted[actor])
            ballistic[actor] = previous_air or bool(state.get('airborne'))
            if not blocked[actor] and not ballistic[actor]:
                pose_rollback[actor] = bool(runtime._guard_realised_pose(state, ticks[actor], safe[actor], attempted[actor], navigation_pose=driven))
            settled[actor] = module._position(state)
    runtime._guard_tank_translations(players, ticks)
    for actor, forced in passive.items():
        state = runtime.states[actor]
        if state.get('alive') and actor not in locked:
            yaw = state['yaw']
            runtime._apply_tank_contact_response(state,
                {'delta_velocity': (math.sin(yaw)*forced, math.cos(yaw)*forced), 'correction': (0.,0.)},
                step, advance_push=False, advance_forward=True)
    reports = runtime._resolve_tank_contacts(players, now, step)
    for actor, pose in locked.items():
        state = runtime.states[actor]
        state['x'], state['z'], state['yaw'] = pose
        state['speed'] = state['push_x'] = state['push_z'] = 0.
        state['movement_dir'] = state['rotation_dir'] = 0
        runtime._turn_speeds[actor] = 0.
    scene.motion_receipts = {}
    for state in ordered:
        actor = state['id']
        if actor not in settled:
            continue
        after_contacts = module._position(state)
        if abs(state['x']-settled[actor][0]) > 1.e-6 or abs(state['z']-settled[actor][2]) > 1.e-6:
            rollback = (state['x'], settled[actor][1], state['z'])
            blocked[actor] = blocked[actor] or runtime._update_vertical_motion(state, 0., rollback, attempted[actor])
        if not blocked[actor] and not ballistic[actor] and not state.get('airborne'):
            pose_rollback[actor] = bool(runtime._guard_realised_pose(state, settled[actor], False, attempted[actor], navigation_hazards=False)) or pose_rollback.get(actor, False)
        trace = state.get('_motion_stall_pending')
        if trace is not None:
            trace['after_contacts'] = after_contacts
        scene.motion_receipts[actor] = dict(vertical_cohort=True,
            support_blocked=bool(blocked[actor]), pose_rollback=pose_rollback.get(actor, False),
            settled_pose=settled[actor], after_contacts=after_contacts,
            ballistic=bool(ballistic[actor]), siege_locked=actor in locked)
    return reports


def physical_snapshot(runtime):
    names = ('x','y','z','yaw','pitch','roll','terrain_pitch','speed','push_x','push_z',
             'push_yaw','vertical_speed','movement_dir','rotation_dir','alive','health','display_health','grounded_once',
             'airborne','service_brake','_contact_forward_speed','last_drive_pitch',
             '_drown_check','_drown_time','_water_depth','_overturn_check','_overturn_time',
             '_overturn_level','_overturned','_drowning','siege_state','_siege_time_left',
             '_siege_transition_total','death_reason')
    result = {}
    for actor, state in runtime.states.items():
        result[actor] = dict((name, state.get(name, -1. if name == '_water_depth' else 0.)) for name in names)
        result[actor]['turn_speed'] = runtime._turn_speeds.get(actor, 0.)
        result[actor]['grind_ticks'] = runtime._hard_contact_grinds.get(actor, 0)
    return result


def setup(scene, case, bot):
    state = scene.runtime.states[16]
    if case == 'motion_trace':
        state['_motion_stall_pending'] = {}
    elif case == 'new_siege':
        scene.new_siege = True
        normal = scene.runtime._descriptors[16]
        scene.runtime._descriptor_pairs[16] = (normal, normal)
        state.update(speed=4., push_x=.3, push_z=.5)
    elif case == 'missing_catalog':
        scene.owner._destructibles = None
        state.update(x=39., z=-11., yaw=0., speed=12.)
    elif case == 'wall':
        state.update(x=0., z=-11., yaw=0., speed=14.)
    elif case == 'wreck':
        state.update(x=48., z=8., alive=False, health=0., speed=0.,
                     push_x=2., push_z=6., push_yaw=.35,
                     _contact_dynamics=True)
    elif case == 'landing':
        state.update(x=40., z=-40., y=2., airborne=True,
                     vertical_speed=-18., health=1000.)
    elif case == 'fatal_landing':
        state.update(x=40., z=-40., y=1., airborne=True,
                     vertical_speed=-35., health=1.)
    elif case == 'airborne_wall':
        state.update(x=0., z=-10., y=1., yaw=0., speed=40.,
                     airborne=True, vertical_speed=-2., health=1.)
    elif case == 'drowning':
        scene.runtime._water_depth_probe = lambda point: 5.
        state.update(_drown_time=bot.BOT_DROWNING_SECONDS,
                     _drown_check=bot.BOT_DROWNING_PROBE_SECONDS,
                     push_x=.25, push_z=.5)
    elif case == 'overturn':
        state.update(pitch=math.pi/2., roll=math.pi/2.,
                     _overturn_check=bot.BOT_OVERTURN_IGNORE_SECONDS,
                     _overturn_level=2,
                     _overturn_time=bot.BOT_OVERTURN_DEATH_SECONDS,
                     push_x=.25, push_z=.5)


def native_frame(owner, scene, commands, step, now):
    owner.begin_slice(step, now, players=[scene.human])
    for actor in scene.runtime.states:
        owner.prepare_actor(actor)
        new_lock = bool(getattr(scene, 'new_siege', False) and scene.frame_index == 0 and actor == 16)
        if new_lock:
            scene.runtime._set_bot_siege_state(scene.runtime.states[actor], 1, 2., 2.)
            owner.patch_external(actor, ('siege',))
        owner.advance_actor(actor, *commands[actor], **{'move_position': (scene.runtime.states[actor]['x'], scene.runtime.states[actor]['y'], scene.runtime.states[actor]['z']+30.), 'siege_locked': new_lock})
        owner.after_weapon(actor)
    return owner.settle_roster()


def engine_queries(scene):
    # Keep exact ordered native frontiers. Ignore trace-only simulation labels.
    return [(row['name'], row['args']) for row in scene.frontier_log
            if row['edge'] == 'start']


def check_case(scene_module, bot, native_math, backend, NativeMotion, drive, case):
    native_math._backend, native_math._attempted = None, True
    baseline = scene_module.HostScene(case + '-reference', native_library='')
    setup(baseline, case, bot)
    commands = dict((actor, (1., (actor % 3 - 1)*.15, 0.))
                    for actor in baseline.runtime.states)
    baseline.frontier_log[:] = []
    expected = []
    for frame in range(4):
        baseline.now = .2*(frame+1)
        baseline.frame_index = frame
        reports = run_reference(baseline, bot, commands, .2, baseline.now, drive)
        expected.append((physical_snapshot(baseline.runtime), copy.deepcopy(reports),
                         dict((actor, frozenset(state)) for actor, state in baseline.runtime.states.items()),
                         copy.deepcopy(baseline.runtime.states),
                         copy.deepcopy(baseline.motion_receipts)))
    candidate = scene_module.HostScene(case + '-native', native_library='')
    setup(candidate, case, bot)
    candidate.frontier_log[:] = []
    native_math._backend, native_math._attempted = backend, True
    handle = backend.sim_open(5, 1)
    owner = NativeMotion(candidate.runtime, backend, handle)
    owner.install_all()
    comparisons = 0
    try:
        for frame in range(4):
            candidate.now = .2*(frame+1)
            candidate.frame_index = frame
            reports = native_frame(owner, candidate, commands, .2, candidate.now)
            assert not owner.failures, owner.failures
            for actor, receipt in expected[frame][4].items():
                near(owner.settlement[actor], receipt, case+'/settlement/'+str(actor))
            actual = physical_snapshot(candidate.runtime)
            for actor, state in expected[frame][0].items():
                for name, value in state.items():
                    near(actual[actor][name], value,
                         '%s/frame%d/actor%d/%s' % (case, frame, actor, name))
                    comparisons += 1
            from gui.mods.offline_lan_0922.native_motion_core import STATE_NAMES, FLAG_NAMES
            fields = set(STATE_NAMES + FLAG_NAMES) - set(('_turn_speed','grind_ticks'))
            for actor, names in expected[frame][2].items():
                wanted = fields & set(names)
                present = fields & set(candidate.runtime.states[actor])
                assert present == wanted, (case, frame, actor, 'presence', present-wanted, wanted-present)
            for actor, state in expected[frame][3].items():
                # The native stage does not print the reference path's CONTACT /
                # WRECK stdout messages. These two deadlines only gate that log.
                ignored = ('_contact_motion_log_time', '_wreck_motion_log_time')
                near(dict((k,v) for k,v in candidate.runtime.states[actor].items() if k not in ignored),
                     dict((k,v) for k,v in state.items() if k not in ignored),
                     case+'/full state/'+str(actor))
            assert reports == expected[frame][1], (case, frame, reports, expected[frame][1])
        near(engine_queries(candidate), engine_queries(baseline), case+'/ordered queries')
        near(candidate.effect_log, baseline.effect_log, case+'/ordered effects')
    finally:
        backend.sim_close(handle)
    return dict(case=case, comparisons=comparisons,
                ordered_queries=len(engine_queries(candidate)))


def check_missing_catalog(scene_module, bot, native_math, backend, NativeMotion, drive):
    from gui.mods.offline_lan_0922 import world_collision
    original = world_collision._destroy_and_recast
    candidates = [0]
    def kinetic_candidate(*args, **kwargs):
        # Explicit engine candidate seam: no catalog has confirmed destruction.
        candidates[0] += 1
        return 'kinetic'
    world_collision._destroy_and_recast = kinetic_candidate
    try:
        result = check_case(scene_module, bot, native_math, backend, NativeMotion,
                            drive, 'missing_catalog')
        assert candidates[0], 'missing-catalog kinetic branch was not exercised'
        return result
    finally:
        world_collision._destroy_and_recast = original


def check_win32_widths(scene_module, backend, NativeMotion):
    from gui.mods.offline_lan_0922.native_motion_core import STATE_NAMES, FLAG_NAMES
    scene = scene_module.HostScene('win32-widths', native_library='')
    state = scene.runtime.states[16]
    state.update(_drowning=False, _overturned=False)
    scene.runtime.states = {16: state}
    expected = sum(1 << index for index, name in enumerate(STATE_NAMES + FLAG_NAMES)
                   if name in state and name not in ('_turn_speed', 'grind_ticks'))
    assert expected & (1 << 31) and expected & (1 << 32)
    assert expected > 2147483647
    installs, tokens = [], []
    class ProducerGuard(object):
        def __getattr__(self, name):
            method = getattr(backend, name)
            if name not in ('sim_motion_install', 'sim_motion_begin'):
                return method
            def checked(*args):
                if name == 'sim_motion_install':
                    mask = args[2][3]
                    assert type(mask) is float, 'Presence producer must not depend on host PyInt width'
                    assert int(mask) == expected and mask == float(expected)
                    installs.append(mask)
                else:
                    stamp = args[1][3]
                    assert type(stamp) is float, 'Time producer must not depend on host PyInt width'
                    assert stamp == float(int(stamp)) and stamp <= 9007199254740991.
                    tokens.append(stamp)
                return method(*args)
            return checked
    handle = backend.sim_open(5, 21)
    owner = NativeMotion(scene.runtime, ProducerGuard(), handle)
    try:
        owner.install_all()
        assert len(installs) == 1
        receipt = backend.sim_motion_snapshot(handle, (1, 16))
        assert receipt is not None and type(receipt[3]) is float
        assert receipt[3] == float(expected) and int(receipt[3]) == expected
        owner._mirror(16, receipt)
        assert '_drowning' in state and '_overturned' in state
        assert not state['_drowning'] and not state['_overturned']
        # This must exercise the actual adapter and C++ begin boundary. LP64
        # acceptance of a large Python int is explicitly insufficient proof.
        for now in (2147.483648, 86400., 86400.123456):
            scene.now = now
            native_frame(owner, scene, {16: (0., 0., 0.)}, .1, now)
            assert not owner.failures, owner.failures
            assert tokens[-1] == float(round(now * 1000000.))
            mirrored = backend.sim_motion_snapshot(handle, (1, 16))
            assert type(mirrored[3]) is float
            mask = int(mirrored[3])
            assert float(mask) == mirrored[3]
            assert mask & (1 << 31) and mask & (1 << 32)
        assert len(tokens) == 3
    finally:
        backend.sim_close(handle)
    return 'Win32 first-install presence bits31/32 / explicit float payloads / 2147s and 86400s clocks'


def check_query_rows(scene_module, backend, NativeMotion):
    scene = scene_module.HostScene('narrow-query-inputs', native_library='')
    handle = backend.sim_open(5, 20)
    owner = NativeMotion(scene.runtime, backend, handle)
    owner.install_all()
    state = scene.runtime.states[16]
    key, pose = (1, 16), (0., 0., 0., 0., 0., 0.)
    def forbidden_mirror(actor):
        raise AssertionError('Pure query performed a full actor readback')
    owner.mirror = forbidden_mirror
    # The live Python mirror deliberately differs: pure query decisions must
    # use their explicit native inputs, rather than stale physics dictionaries.
    state['_water_depth'] = 99.
    observed = []
    scene.runtime._planner_corridor_clear = lambda *args, **kwargs: observed.append(kwargs) or True
    owner._dispatch(13, (key, pose, 0., 1., 0, -1.))
    assert not observed[-1]['wet_escape'], observed
    scene.runtime._turret_motion_probe = lambda a, b, descriptor: observed.append((a, b)) or True
    owner._dispatch(12, (key, pose, pose, 0, (.37, .11, .22, 1)))
    near((observed[-1][0]['pitch'], observed[-1][0]['roll'],
          observed[-1][0]['chassis']['pitch']), (.37, .11, .22), 'narrow turret orientation')
    owner._dispatch(11, (key, pose, pose, 0))
    owner._dispatch(24, (key, 0, 0., 0., 0, pose, 0., 0))
    reuse = []
    scene.runtime.motion_world_corridor_reusable = lambda *args: reuse.append(args) or True
    scene.owner._destructibles._catalog_hull_contact = lambda *args, **kwargs: False
    scene.runtime._turn_speeds[16] = 0.
    state['rotation_dir'] = 0
    assert owner._dispatch(20, (key, pose, 1., .2, None, 10, 0., 0, 1, 0.)) == 0
    assert not reuse, 'stale rotation_dir admitted a world receipt'
    owner._dispatch(21, ())
    scene.runtime._turn_speeds[16] = 99.
    assert owner._dispatch(20, (key, pose, 1., .2, None, 10, 0., 0, 0, 0.)) == 1
    assert len(reuse) == 1, 'stale turn speed rejected a valid receipt'
    owner._dispatch(21, ())
    backend.sim_close(handle)
    return 'pure query rows / no snapshot / current water and orientation / live receipt inputs'


def check_events(scene_module, backend, NativeMotion):
    scene = scene_module.HostScene('event-boundaries', native_library='')
    handle = backend.sim_open(5, 11)
    owner = NativeMotion(scene.runtime, backend, handle)
    original = copy.deepcopy(scene.runtime.states)
    assert scene.runtime.states == original, 'constructor altered the mirror'
    owner.install_all()
    # Use the actual root producer seam, not a parallel ad-hoc rebase owner.
    scene.runtime._native_simulation = scene_module.Namespace(motion=owner)
    commands = dict((actor, (1., 0., 0.)) for actor in scene.runtime.states)
    state = scene.runtime.states[16]
    state.update(x=39., pitch=.02, roll=.03, terrain_pitch=.04,
                 push_x=.7, push_z=.9)
    owner.patch_external(16, ('pose','velocity','hydraulic'))
    committed = owner.mirror(16)
    near((committed['x'], committed['pitch'], committed['roll'],
          committed['terrain_pitch'], committed['push_x'], committed['push_z']),
         (39., .02, .03, .04, .7, .9), 'explicit event')
    owner.begin_slice(.1, .1, players=[scene.human])
    first = list(scene.runtime.states)[0]
    owner.prepare_actor(first)
    state = scene.runtime.states[first]
    state['pitch'], state['terrain_pitch'] = .05, .06
    owner.after_weapon(first)
    owner.advance_actor(first, *commands[first])
    # This is the supported same-Entry descriptor callback boundary. No actor
    # replacement and no clock/history reset may occur while a native call lives.
    actual = owner.dispatch
    patched = [False]
    def dispatch(opcode, rows):
        if opcode == 12 and not patched[0]:
            owner.descriptor_patch(first, revision=2)
            patched[0] = True
        return actual(opcode, rows)
    owner.dispatch = dispatch
    for actor in list(scene.runtime.states)[1:]:
        owner.prepare_actor(actor)
        owner.advance_actor(actor, *commands[actor])
        owner.after_weapon(actor)
    owner.settle_roster()
    assert patched[0] and not owner.failures, owner.failures
    state = scene.runtime.states[16]
    state.update(alive=False, death_reason=3, push_x=.7, push_z=.9)
    owner.patch_external(16, ('velocity',))
    owner.patch_external(16, ('terminal',))
    owner.after_weapon(16)
    assert owner.mirror(16)['death_reason'] == 3, 'weapon terminal presence lost'
    assert owner.mirror(16)['push_x'] == .7, 'terminal patch erased passive input'
    owner.detach()
    assert backend.sim_close(handle) is not None
    assert backend.sim_close(handle) is not None
    return 'external patches / descriptor reentry / hydraulic / double close'


def check_failure(scene_module, backend, NativeMotion, after_effect=False):
    scene = scene_module.HostScene('contained-failure', native_library='')
    handle = backend.sim_open(5, 12 if after_effect else 13)
    owner = NativeMotion(scene.runtime, backend, handle)
    owner.install_all()
    commands = dict((actor, (1., 0., 0.)) for actor in scene.runtime.states)
    original = owner._dispatch
    injected = [False]
    def failing(opcode, rows):
        if not after_effect and opcode == 10 and rows[0][1] == 16 and not injected[0]:
            injected[0] = True
            raise RuntimeError('analytic support query failure')
        value = original(opcode, rows)
        if after_effect and scene.destructibles.manager.orders and not injected[0]:
            injected[0] = True
            raise RuntimeError('analytic failure after accepted destruction')
        return value
    owner._dispatch = failing
    if after_effect:
        scene.runtime.states[16].update(z=-11., speed=12.)
        owner.patch_external(16, ('pose','velocity'))
    native_frame(owner, scene, commands, .2, .2)
    assert injected[0] and owner.failures, owner.failures
    assert scene.runtime.states[16].get('_native_motion_failed')
    assert any(scene.runtime.states[actor]['speed'] > 0.
               for actor in scene.runtime.states if actor != 16), 'one error froze roster'
    if after_effect:
        assert len(scene.destructibles.manager.orders) == 1, 'committed effect replayed'
    # A fresh operation may proceed; a local query error is not round closure.
    owner._dispatch = original
    native_frame(owner, scene, commands, .2, .4)
    assert not scene.runtime.states[16].get('_native_motion_failed')
    if after_effect:
        assert len(scene.destructibles.manager.orders) == 1
    backend.sim_close(handle)
    return 'after-effect no replay' if after_effect else 'one actor failed / roster continued'


def check_reentrant_close(scene_module, backend, NativeMotion):
    scene = scene_module.HostScene('reentrant-close', native_library='')
    handle = backend.sim_open(5, 14)
    owner = NativeMotion(scene.runtime, backend, handle)
    owner.install_all()
    owner.begin_slice(.1, .1, players=[scene.human])
    original = owner.dispatch
    def closing(opcode, rows):
        backend.sim_close(handle)
        return original(opcode, rows)
    owner.dispatch = closing
    try:
        owner.prepare_actor(list(scene.runtime.states)[0])
        raise AssertionError('closed round committed its actor')
    except RuntimeError:
        pass
    assert backend.sim_lifetime(handle) is None
    return 'close during frontier rejects old commit'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('extension')
    parser.add_argument('--scene', default=os.path.join(os.path.dirname(__file__),
                                                  'native_simulation_motion_fixture.py'))
    parser.add_argument('--case', action='append')
    parser.add_argument('--output')
    args = parser.parse_args()
    scene_module = imp.load_source('simulation_motion_scene', args.scene)
    from gui.mods.offline_lan_0922 import bot_runtime as bot
    from gui.mods.offline_lan_0922 import native_math
    from gui.mods.offline_lan_0922.native_motion_core import NativeMotion
    drive = reference_drive(bot)
    backend = imp.load_dynamic('offline_math_batch_native', args.extension)
    cases = args.case or ['movement','wall','wreck','landing','fatal_landing',
                         'airborne_wall','drowning','overturn','new_siege','motion_trace']
    results = [check_case(scene_module, bot, native_math, backend, NativeMotion,
                         drive, case) for case in cases]
    results.append(check_missing_catalog(scene_module, bot, native_math, backend,
                                         NativeMotion, drive))
    events = [check_win32_widths(scene_module, backend, NativeMotion),
              check_query_rows(scene_module, backend, NativeMotion),
              check_events(scene_module, backend, NativeMotion),
              check_failure(scene_module, backend, NativeMotion),
              check_failure(scene_module, backend, NativeMotion, True),
              check_reentrant_close(scene_module, backend, NativeMotion)]
    result = dict(event_checks=events, comparisons=sum(row['comparisons'] for row in results),
                  actors=29, slices_per_case=4, cases=results,
                  physical_state_parity=True, ram_report_parity=True,
                  state_presence_parity=True, full_state_parity=True,
                  excluded_reference_stdout_deadlines=['_contact_motion_log_time',
                                                      '_wreck_motion_log_time'],
                  ordered_engine_query_parity=True,
                  scope='Source-derived physical stage; explicit analytic engine/descriptors. Not AI/Windows acceptance.')
    if args.output:
        with open(args.output, 'w') as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
