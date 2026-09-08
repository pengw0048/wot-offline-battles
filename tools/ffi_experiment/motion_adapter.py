"""One copied-motion owner with descriptor and tuning inputs frozen at install."""
from __future__ import print_function

CONSTANTS = (
    'GRAVITY GRAVITY_FACTOR COHESION DRIVE_TRACTION '
    'SLOPE_GRIP_LNG_FULL_Y SLOPE_GRIP_LNG_FULL SLOPE_GRIP_LNG_MIN_Y SLOPE_GRIP_LNG_MIN '
    'POWER_FACTOR BKWD_POWER_FRACTION ENGINE_MIN_V STEER_RESIST_MULT '
    'COH_DECAY_Y COH_DECAY_FACTOR COH_DECAY_POW SLOPE_COH_DECAY_Y SLOPE_COH_DECAY COH_DECAY_BOUND '
    'COAST_BRAKE_SHARE SLIDE_KINETIC SLIDE_HOLD_TAN SLIP_THRESHOLD_TAN SLIP_DRAG '
    'OVERSPEED_MAX_FACTOR OVERSPEED_BUILD OVERSPEED_DAMP '
    'SPEED_AFFECT_ROT_DECREASE ANG_ACCELERATION_TIME '
    'HARD_CONTACT_ENTRY_FACTOR HARD_CONTACT_SLIDE_DECAY HARD_CONTACT_BRAKE_DECAY HARD_CONTACT_STOP_SPEED '
    'GROUND_PITCH_LIMIT GROUND_FOLLOW_BASE GROUND_FOLLOW_MIN GROUND_FOLLOW_MAX '
    'SLIDE_DRAG SLIDE_MAX FALL_SAFE_SPEED FALL_DMG_PER_MS'
).split()


class Physics(object):
    def __init__(self, backend, module):
        self.backend = backend
        self.handle = int(backend.call([600] + [getattr(module, name) for name in CONSTANTS])[0])
        self.profiles = {}

    def profile(self, params):
        key = (params['mass'], params['powerW'], params.get('nativePowerRatio', 1.0),
               params['specificFriction'], params['brakeDecel'], params['speedFwd'],
               params['speedBwd'], params['rotSpd']) + tuple(params['terrainResist'])
        handle = self.profiles.get(key)
        if handle is None:
            handle = int(self.backend.call([601, self.handle] + list(key))[0])
            self.profiles[key] = handle
        return handle

    def batch(self, params, kind, rows):
        result = self.backend.call([602, self.profile(params), kind, len(rows)] +
                                   [value for row in rows for value in row])
        width = 3 if kind == 2 else 1
        return [tuple(result[1 + i * width:1 + (i + 1) * width]) if width > 1 else result[1 + i]
                for i in range(int(result[0]))]


def _optional(value):
    return [int(value is not None), value if value is not None else 0.0]


class MotionFlowBackend(object):
    """Replace the complete horizontal preparation/integration source block.

    Only engine queries, opaque receipts, diagnostics and publication cross the
    callback boundary. The copied motion, cache decisions and navigation guards
    execute on one C++ stack, with per-Bot ordering identical to the source.
    """
    def __init__(self, backend, runtime, module):
        import inspect
        import textwrap
        import types
        from array import array
        self.backend, self.runtime, self.module = backend, runtime, module
        if runtime.native_motion:
            raise ValueError('motion-flow is the copied-physics experiment')
        if not hasattr(runtime.navigator, 'handle') or not hasattr(runtime.adapter.driver, 'handle'):
            raise ValueError('motion-flow requires navigation-flow and driver-flow')
        self.physics = Physics(backend, module.vehicle_physics)
        self.handle = int(backend.call([610, module.MOTION_PROBE_SECONDS])[0])
        self.packet = array('d', [0.0] * 128)
        self.tokens, self.objects, self.cache_ids = {}, {}, {}
        self.next_object = 1
        self.patches = []
        self.vertical_counts = {'native': 0, 'suspension_source': 0}
        self.bake_admitted = runtime.adapter.driver.bake_admitted
        self.original_present = '_update_once' in runtime.__dict__
        self.original_update = runtime._update_once
        source = open(inspect.getsourcefile(module.BotRuntime), 'rb').read().decode('utf-8')
        start_method = source.index("    @timed('bot.slice')\n    def _update_once(")
        end_method = source.find('\n    def ', start_method + 30)
        source = textwrap.dedent(source[start_method:end_method if end_method != -1 else len(source)])
        start = source.index("        throttle = max(-1.0, min(1.0, command['throttle']))")
        end = source.index("        if diagnostic is not None:\n            diagnostic.phase('bot.aim_fire')", start)
        replacement = (
            '        destroyed_devices = self._experimental_motion_step(\n'
            '            state, command, target, descriptor, position, step, now,\n'
            '            decision_due, refresh_control, siege_motion_locked,\n'
            '            tick_siege_yaw, siege_locked_poses, attempted_yaws,\n'
            '            baked_shallow_escape, diagnostic)\n')
        scope = dict(module.__dict__)
        eval(compile(source[:start] + replacement + source[end:],
                     '<experimental-motion-flow>', 'exec'), scope)
        runtime._experimental_motion_step = self.step
        for name, replacement in (('_update_vertical_motion', self.vertical),
                                  ('_update_slope_pose', self.slope),
                                  ('_cached_traffic_stopping_distance', self.stopping),
                                  ('_guard_realised_pose', self.guard),
                                  ('_apply_tank_contact_response', self.contact_response)):
            self.patches.append((name, name in runtime.__dict__, getattr(runtime, name)))
            setattr(runtime, name, replacement)
        self.source_vertical = self.patches[0][2]
        self.source_guard = self.patches[3][2]
        navigation_source = open(inspect.getsourcefile(module.BotRuntime), 'rb').read().decode('utf-8')
        nav_start = navigation_source.index('    def _navigation_target(')
        nav_end = navigation_source.index('\n    def ', nav_start + 10)
        navigation_source = textwrap.dedent(navigation_source[nav_start:nav_end])
        begin = navigation_source.index('    direct = getattr(grid,')
        finish = navigation_source.index("    if mode in ('route', 'advance') and anchor is None:", begin)
        selection = '''    throttle_override = strategic.get('throttle_override')
    driver_state = getattr(self.adapter.driver, 'states', {}).get(int(bot_id), {})
    movement_intent = bool(
        (throttle_override is None or float(throttle_override) > 0.0)
        and not driver_state.get('traffic_waiting', False)
        and int(bot_id) not in self._artillery_intents
        and int(bot_id) not in self._artillery_reproofs)
    target, state['navigation_stop_at_target'] = self.navigator.motion_target(
        bot_id, position, goal, path_key, now, anchor, lookahead_distance,
        movement_intent, stop_at_goal)
'''
        navigation_scope = dict(module.__dict__)
        eval(compile(navigation_source[:begin] + selection + navigation_source[finish:],
                     '<experimental-motion-target>', 'exec'), navigation_scope)
        navigation_function = navigation_scope['_navigation_target']
        try:
            navigation_function = types.MethodType(navigation_function, runtime)
        except TypeError:
            navigation_function = types.MethodType(navigation_function, runtime, type(runtime))
        self.original_navigation_target = runtime.adapter.navigation_target
        runtime.adapter.navigation_target = navigation_function
        function = scope['_update_once']
        try:
            runtime._update_once = types.MethodType(function, runtime)
        except TypeError:
            runtime._update_once = types.MethodType(function, runtime, type(runtime))

    def _save(self, value):
        if value is None:
            return 0
        key = self.next_object
        self.next_object += 1
        self.active[key] = value
        return key

    def _receipt(self, value):
        rt = self.module
        kind = (0 if value is None else 1 if value is False else
                2 if isinstance(value, dict) else 3 if value == 'deferred' else 4)
        row = value if kind == 2 else {}
        origin = row.get('origin')
        valid = isinstance(origin, (tuple, list)) and len(origin) == 3
        values = [kind, self._save(value), int(valid)]
        values.extend(rt._number(item) for item in (origin if valid else (0.0, 0.0, 0.0)))
        return values + [
                    rt._number(row.get('yaw')), int(rt._number(row.get('direction'))),
                    rt._number(row.get('leading')), rt._number(row.get('distance'))]

    def _probe(self, value):
        rt = self.module
        row = value if isinstance(value, dict) else {}
        return [0 if value is None else 2 if isinstance(value, dict) else 1,
                self._save(value), int(bool(value)), int(bool(row.get('clear', True))),
                int(bool(row.get('collision', False))), int(bool(row.get('water', False))),
                rt._number(row.get('slope')), int(bool(row.get('deferred', False))),
                int(bool(row.get('_world_receipt_pending', False)))] + self._receipt(row.get('world_receipt')) + [0]

    def _cache(self, value):
        rt = self.module
        if value is None:
            return [0.0] * 33
        position = value.get('position')
        valid = isinstance(position, (tuple, list)) and len(position) == 3
        return ([1, int(valid)] + [rt._number(item) for item in (position if valid else (0, 0, 0))] +
                [rt._number(value.get('yaw'))] + _optional(value.get('maximum_distance')) +
                _optional(value.get('probe_distance')) + _optional(value.get('probe_leading')) +
                [rt._number(value.get('deadline'))] + self._probe(value.get('result')))

    def _read_probe(self, values, offset=0):
        key = int(values[offset + 1])
        probe = self.active.get(key) if key else ({} if values[offset] == 2 else None)
        if values[offset + 19]:
            probe = dict(probe)
            for index, name, default in ((3, 'clear', True), (4, 'collision', False),
                                          (8, '_world_receipt_pending', False)):
                changed = bool(values[offset + index])
                if changed != bool(probe.get(name, default)):
                    probe[name] = changed
            receipt_key = int(values[offset + 10])
            receipt = self.active.get(receipt_key) if receipt_key else None
            if receipt is not probe.get('world_receipt'):
                probe['world_receipt'] = receipt
            self.active[key] = probe
        return probe

    def _read_cache(self, values, offset=0):
        if not values[offset]:
            return None
        return dict(position=tuple(values[offset + 2:offset + 5]), yaw=values[offset + 5],
                    maximum_distance=values[offset + 7] if values[offset + 6] else None,
                    probe_distance=values[offset + 9] if values[offset + 8] else None,
                    probe_leading=values[offset + 11] if values[offset + 10] else None,
                    deadline=values[offset + 12], result=self._read_probe(values, offset + 13))

    def step(self, state, command, target, descriptor, position, step, now,
             decision_due, refresh_control, siege_motion_locked, tick_siege_yaw,
             siege_locked_poses, attempted_yaws, baked_shallow_escape, diagnostic):
        from array import array
        rt, runtime, bot_id = self.module, self.runtime, state['id']
        self.active = self.objects.setdefault(bot_id, {0: {}})
        raw_cache = runtime._motion_probe_cache.get(bot_id)
        seed = bot_id not in self.tokens or raw_cache is not self.tokens[bot_id]
        cache_packet = self._cache(raw_cache) if seed else []
        active_ids = ([int(cache_packet[14]), int(cache_packet[23])]
                      if seed else list(self.cache_ids.get(bot_id, ())))
        aim = rt._point(command.get('aim_position'),
                        target.get('position') if target is not None else rt._position(state))
        limits = runtime._gun_yaw_limits.get(bot_id)
        if limits is None:
            limits = rt.ai_driver.gun_yaw_limits(descriptor)
            runtime._gun_yaw_limits[bot_id] = limits
        unused, destroyed, unused_crew, unused_yellow = rt._critical_parts(state)
        blocked = state.get('_overturned', False) or bool(destroyed.intersection(
            ('engineHealth', 'leftTrackHealth', 'rightTrackHealth')))
        mobility = rt._critical_factor(state, descriptor, 'mobility') if not blocked and abs(command['throttle']) > 0.01 else 1.0
        params = runtime._physics_params_for(bot_id)
        profile = self.physics.profile(params)
        siege_limit = (rt.siege_mechanics.enabled_speed_limit(state.get('vehicle', ''))
                       if state.get('siege_state') == rt.siege_mechanics.ENABLED else None)
        move = command.get('move_position')
        recovery = {'drive': 0, 'avoid': 1, 'blocked': 2, 'reverse_turn': 3, 'pivot_recovery': 4}.get(command.get('recovery_mode', 'drive'), 5)
        values = ([611, self.handle, profile, bot_id, runtime.navigator.handle,
                   runtime.adapter.driver.handle] + list(position) + list(aim) +
                  [int(move is not None)] + list(rt._point(move, position)) +
                  [state['yaw'], state['speed'], runtime._turn_speeds.get(bot_id, 0.0),
                   state.get('half_length', 3.5), state.get('half_width', 1.7),
                   command['throttle'], command.get('turn', 0.0), limits[0], limits[1],
                   mobility, step, now, state.get('_water_depth', -1.0), tick_siege_yaw,
                   recovery, runtime._hard_contact_grinds.get(bot_id, 0)] +
                  [int(bool(value)) for value in (
                      target is not None and command.get('combat_mode') != 'base_defense',
                      command.get('movement_intent', True), blocked, state.get('airborne', False),
                      state.get('grounded_once', False), siege_motion_locked, self.bake_admitted,
                      baked_shallow_escape, decision_due, refresh_control,
                      callable(runtime.motion_resolver), callable(runtime.motion_report),
                      bot_id in runtime._hard_contact_grinds)] +
                  _optional(siege_limit) + _optional(state.get('destructible_contact_speed')) +
                  [int(seed)] + cache_packet)
        statuses = ('clear', 'crushed', 'soft', 'cap_crushed', 'hard')
        packet = self.packet

        def destructible(present, speed):
            if present:
                state['destructible_contact_speed'] = speed
            else:
                state.pop('destructible_contact_speed', None)

        def query():
            kind = int(packet[0])
            if kind == 504:
                # A blocked motion step can cancel a pending route search on
                # this same native stack. Its retired key belongs to navigation.
                runtime.navigator._publish_event(packet)
                return
            if int(packet[1]) != bot_id:
                raise RuntimeError('motion callback owner changed')
            if kind == 610:
                arguments = (tuple(packet[2:5]), packet[5], packet[6], descriptor)
                if packet[9]:
                    arguments += (packet[8] if packet[7] else None,)
                answer = runtime._probe_direction(*arguments)
                packet[:20] = array('d', self._probe(answer))
            elif kind == 611:
                answer = runtime._probe_world_receipt(bot_id, tuple(packet[2:5]), packet[5],
                    packet[6], descriptor, bool(packet[7]), packet[9] if packet[8] else None)
                packet[:10] = array('d', self._receipt(answer))
            elif kind == 612:
                cache = self._read_cache(packet, 2)
                active_ids[:] = [int(packet[16]), int(packet[25])] if cache is not None else []
                if cache is None:
                    runtime._motion_probe_cache.pop(bot_id, None)
                else:
                    runtime._motion_probe_cache[bot_id] = cache
                packet[0] = int(bool(cache and cache['result']))
            elif kind == 613:
                phase = int(packet[2])
                if phase == 0:
                    state['hull_aiming'] = bool(packet[3])
                    attempted_yaws[bot_id] = packet[5]
                    if packet[4]:
                        command.update(fire_allowed=False, throttle=0.0, turn=0.0, movement_intent=False)
                        state.update(speed=0.0, movement_dir=0, rotation_dir=0, push_x=0.0, push_z=0.0)
                        runtime._turn_speeds[bot_id] = 0.0
                        siege_locked_poses[bot_id] = (position[0], position[2], tick_siege_yaw)
                elif phase == 1:
                    state['movement_dir'], state['rotation_dir'] = int(packet[3]), int(packet[4])
                    probe = self._read_probe(packet, 10)
                    attempted_yaws[bot_id] = packet[9]
                    runtime._log_direction_flip(state, bool(packet[7]), probe, now)
                    runtime._log_motion_stall(state, command, packet[5], packet[6], bool(packet[7]), probe, now, bool(packet[8]))
                    if diagnostic is not None:
                        diagnostic.phase('bot.integrate')
                elif phase == 2:
                    state['yaw'], runtime._turn_speeds[bot_id] = packet[3], packet[4]
                    state['rotation_dir'], state['movement_dir'] = int(packet[5]), int(packet[6])
                    state['last_drive_pitch'] = packet[7]
                    attempted_yaws[bot_id] = packet[13]
                    trace = state.get('_motion_stall_pending')
                    if trace is not None:
                        trace.update(dt=step, drive_speed=packet[8], drive_pitch=packet[7], throttle=packet[9],
                            baked_veto=bool(packet[10]), path_clear=bool(packet[11]), frozen=bool(packet[12]),
                            mass=params['mass'], powerW=params['powerW'], nativePowerRatio=params.get('nativePowerRatio', 1.0),
                            terrainResist=params['terrainResist'], specificFriction=params['specificFriction'])
                    destructible(packet[14], packet[15])
                else:
                    raise RuntimeError('motion publication phase')
            elif kind == 614:
                runtime._decision_cache.pop(bot_id, None)
                runtime._motion_probe_cache.pop(bot_id, None)
                state.pop('destructible_contact_speed', None)
                active_ids[:] = []
            elif kind == 615:
                if packet[9]:
                    answer = runtime._passive_motion_status(state, tuple(packet[2:5]), packet[5], packet[6],
                        descriptor, packet[7], packet[8], commit_enabled=bool(packet[10]))
                else:
                    started = runtime._probe_started() if runtime._probe_timing_enabled() else None
                    try:
                        answer = rt.timed_call(runtime._combat_diagnostics, 'bot.physics', runtime.motion_resolver,
                            bot_id, tuple(packet[2:5]), packet[5], packet[6], descriptor, packet[7], packet[8])
                    finally:
                        if started is not None:
                            runtime._probe_finished(4, started)
                if answer not in statuses:
                    raise RuntimeError('bot motion resolver returned an invalid status')
                packet[0] = statuses.index(answer)
            elif kind == 616:
                destructible(packet[5], packet[6])
                if packet[7]:
                    runtime._hard_contact_grinds[bot_id] = int(packet[8])
                runtime.motion_report(bot_id, statuses[int(packet[2])], packet[3], packet[4])
            elif kind == 617:
                trace = state.get('_motion_stall_pending')
                if trace is not None:
                    trace.update(world_status=statuses[int(packet[2])], hard_contact=bool(packet[3]),
                                 integrated=tuple(packet[4:7]), world_speed=packet[7])
            else:
                raise RuntimeError('unknown motion event: %d' % kind)

        complete = False
        try:
            output = self.backend.call_sync(values, packet, query)
            state['x'], state['y'], state['z'] = output[:3]
            state['yaw'], state['speed'] = output[3:5]
            runtime._turn_speeds[bot_id] = output[5]
            state['last_drive_pitch'], attempted_yaws[bot_id] = output[6:8]
            state['movement_dir'], state['rotation_dir'] = int(output[8]), int(output[9])
            if output[11]:
                runtime._hard_contact_grinds[bot_id] = int(output[10])
            destructible(output[13], output[14])
            runtime.navigator._publish(bot_id, output, 15)
            runtime.navigator._progress = None
            complete = True
        finally:
            cache = runtime._motion_probe_cache.get(bot_id)
            if complete:
                self.tokens[bot_id] = cache
            else:
                # Re-import the actual Python cache after an interrupted
                # callback, regardless of how far native admission progressed.
                self.tokens.pop(bot_id, None)
            # Retain numeric identities, not all objects equal/identical to a
            # singleton False/None. Repeated failed probes stay bounded too.
            self.cache_ids[bot_id] = active_ids if cache is not None else []
            keep = set(self.cache_ids[bot_id])
            keep.add(0)
            self.objects[bot_id] = dict((key, value) for key, value in self.active.items() if key in keep)
        return destroyed

    def close(self):
        runtime = self.runtime
        for name, present, original in reversed(self.patches):
            if present:
                setattr(runtime, name, original)
            else:
                runtime.__dict__.pop(name, None)
        self.patches = []
        runtime.adapter.navigation_target = self.original_navigation_target
        if self.original_present:
            runtime._update_once = self.original_update
        else:
            runtime.__dict__.pop('_update_once', None)
        runtime.__dict__.pop('_experimental_motion_step', None)
        if self.handle is not None and not self.backend.closed:
            self.backend.call([612, self.handle])
        self.handle = None
        self.objects.clear()
        self.tokens.clear()
        self.cache_ids.clear()

    def stopping(self, source, command, params):
        rt, runtime = self.module, self.runtime
        bot_id = int(rt._number(source.get('id')))
        speed = abs(rt._number(source.get('speed')))
        pitch = rt._number(source.get('last_drive_pitch'))
        steer = abs(rt._number(command.get('turn'))) > 0.01
        key = (id(params), speed, pitch, steer)
        cached = runtime._traffic_stopping_cache.get(bot_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        distance = self.backend.call([623, self.physics.profile(params), speed, pitch, int(steer),
                                      rt.PUBLICATION_SECONDS, rt.TRAFFIC_DIRECTION_SPEED_EPSILON])[0]
        runtime._traffic_stopping_cache[bot_id] = (key, distance)
        return distance

    def _ground_query(self, bot_id, trace):
        rt, packet = self.runtime, self.packet
        if int(packet[1]) != bot_id:
            raise RuntimeError('vertical callback owner changed')
        if packet[0] == 630:
            rt._probe_totals[3] += 1
            started = rt._probe_started()
            try:
                value = rt._physics_ground_probe(packet[2], packet[3], packet[4])
            finally:
                rt._probe_finished(3, started)
            packet[0] = int(value is not None)
            packet[1] = float(value) if value is not None else 0.0
        elif packet[0] == 631 and trace is not None:
            if packet[2] == 0:
                trace.update(support_highest=packet[4] if packet[3] else None,
                             support_centre=packet[6] if packet[5] else None,
                             grounded_before=bool(packet[7]))
            else:
                trace.update(support_limit=packet[3], support_rise_obstacle=bool(packet[4]),
                             support_rise_continuous=bool(packet[5]))
        else:
            raise RuntimeError('unknown vertical query')

    def vertical(self, state, step, tick_pose=None, attempted_yaw=None,
                 suspension_motion_pose=None):
        rt, module, bot_id = self.runtime, self.module, state['id']
        # The optional ten-spring trial has its own solver and admission rules.
        # Keep that explicitly counted source path; it is outside the captured
        # legacy-motion workload and is never included in native coverage.
        if rt._suspension_params_for(bot_id) is not None:
            self.vertical_counts['suspension_source'] += 1
            return self.source_vertical(state, step, tick_pose, attempted_yaw, suspension_motion_pose)
        self.vertical_counts['native'] += 1
        trace = state.get('_motion_stall_pending')
        if trace is not None:
            trace['suspension'] = False
        values = ([620, self.handle, self.physics.handle, bot_id] + list(module._position(state)) +
                  [module._number(state.get('yaw')), max(1.5, module._number(state.get('half_length'), 3.5)),
                   state['speed'], step, state.get('vertical_speed', 0.0), state.get('last_drive_pitch', 0.0),
                   int(bool(state.get('airborne', False))), int(bool(state.get('grounded_once', False))),
                   int(tick_pose is not None)] + list(tick_pose or (0.0, 0.0, 0.0)) + [int(trace is not None)])
        def query():
            self._ground_query(bot_id, trace)
        output = self.backend.call_sync(values, self.packet, query)
        state['x'], state['y'], state['z'] = output[:3]
        state['speed'], state['vertical_speed'] = output[3:5]
        state['airborne'], state['grounded_once'] = bool(output[5]), bool(output[6])
        if output[7]:
            state.update(movement_dir=0, rotation_dir=0, push_x=0.0, push_z=0.0)
            state.pop('destructible_contact_speed', None)
            rt._turn_speeds[bot_id] = 0.0
            rt._invalidate_realised_motion(bot_id, state['yaw'] if attempted_yaw is None else attempted_yaw)
            return True
        if output[8]:
            rt._apply_bot_landing_impact(state, output[9])
        return False

    def slope(self, state, allow_ungrounded=False):
        rt, module, bot_id = self.runtime, self.module, int(state.get('id', -1))
        if (getattr(rt, '_suspension_params', {}).get(bot_id) is not None or state.get('airborne', False) or
                (not allow_ungrounded and not state.get('grounded_once', False))):
            return False
        yaw, x, z = state['yaw'], state['x'], state['z']
        tier = rt._detail_tier(state)
        marker = state.get('pose_sample')
        if (isinstance(marker, (list, tuple)) and len(marker) == 3 and
                abs(x - marker[0]) < module.SLOPE_SAMPLE_METRES[tier] and
                abs(z - marker[1]) < module.SLOPE_SAMPLE_METRES[tier] and
                abs(yaw - marker[2]) < module.SLOPE_SAMPLE_RADIANS[tier]):
            return False
        suspension = state.get('suspension_pitch', 0.0)
        pitch = state.get('terrain_pitch', state.get('pitch', 0.0) - suspension)
        values = [622, self.handle, self.physics.handle, bot_id, x, state['y'], z, yaw,
                  state.get('half_length', 3.5), state.get('half_width', 1.7), pitch, state.get('roll', 0.0)]
        def query():
            self._ground_query(bot_id, None)
        output = self.backend.call_sync(values, self.packet, query)
        state['terrain_pitch'], state['pitch'] = output[0], output[0] + suspension
        state['roll'], state['pose_sample'] = output[1], (x, z, yaw)
        return True

    def guard(self, state, tick_pose, tick_was_safe, attempted_yaw, suspension_snapshot=None):
        rt, module, bot_id = self.runtime, self.module, state['id']
        if suspension_snapshot is not None or rt._suspension_params.get(int(bot_id)) is not None:
            return self.source_guard(state, tick_pose, tick_was_safe, attempted_yaw, suspension_snapshot)
        blocked = self.backend.call([624, rt.navigator.handle] + list(module._position(state)) +
            list(tick_pose) + [state.get('yaw', 0.0), state.get('half_length', 3.5),
                              state.get('half_width', 1.7), int(bool(tick_was_safe))])[0]
        if not blocked:
            return False
        state['x'], state['y'], state['z'] = tick_pose
        state.update(speed=0.0, movement_dir=0, rotation_dir=0, push_x=0.0, push_z=0.0,
                     vertical_speed=0.0, airborne=False)
        rt._invalidate_realised_motion(bot_id, attempted_yaw)
        return True

    def contact_response(self, state, result, step, advance_push=True, apply_correction=True):
        rt, module, packet = self.runtime, self.module, self.packet
        values = ([625, self.handle, state['id']] + list(module._position(state)) +
                  [state['yaw'], state['speed'], state.get('push_x', 0.0), state.get('push_z', 0.0),
                   state.get('half_length', 3.5), state.get('half_width', 1.7)] +
                  list(result['delta_velocity']) + list(result['correction']) +
                  [step, int(bool(advance_push)), int(bool(apply_correction))])
        def query():
            if packet[0] != 635 or int(packet[1]) != state['id']:
                raise RuntimeError('unknown contact-motion event')
            state['speed'] = packet[9]
            answer = rt._clear(tuple(packet[2:5]), packet[5], packet[6], None, packet[7], packet[8])
            packet[0] = int(bool(answer))
        output = self.backend.call_sync(values, packet, query)
        state['x'], state['z'], state['speed'], state['push_x'], state['push_z'] = output[:5]
