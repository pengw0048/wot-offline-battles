"""One persistent native LocalDriver with synchronous engine-query yields."""
from __future__ import print_function
from array import array

MODES = ('arrived', 'blocked', 'pivot_recovery', 'reverse_turn', 'avoid', 'drive')


class DriverBackend(object):
    def __init__(self, backend, runtime, synchronous=False):
        self.backend, self.runtime = backend, runtime
        self.original = runtime.adapter.driver
        self.driver = NativeDriver(backend, self.original, synchronous, runtime)
        runtime.adapter.driver = self.driver

    def close(self):
        if self.runtime.adapter.driver is self.driver:
            self.runtime.adapter.driver = self.original


class NativeDriver(object):
    def __init__(self, backend, original, synchronous=False, runtime=None):
        self.backend, self.original = backend, original
        self.stuck_seconds = original.stuck_seconds
        self.recovery_seconds = original.recovery_seconds
        self.failure_ttl = original.failure_ttl
        self.handle = int(backend.call([200, self.stuck_seconds,
                                       self.recovery_seconds, self.failure_ttl])[0])
        # The route observer reads only traffic_waiting. All other driver
        # state lives in C++; dump_state is explicit diagnostic output.
        self.states = {}
        self.queries = 0
        self.synchronous = synchronous
        self.runtime = runtime
        self.bake_admitted = False
        if runtime is not None:
            bake = (runtime.baked_graph or {}).get('bake') or {}
            radii = bake.get('edge_clearance_radii') or ()
            self.bake_admitted = (float(bake.get('vehicle_half_width', 0.0)) >= 2.15 and
                                  max([float(value) for value in radii] or [0.0]) >= 3.0)

    def __getattr__(self, name):
        return getattr(self.original, name)

    def forget(self, bot_id):
        self.backend.call([203, self.handle, bot_id])
        self.states.pop(bot_id, None)

    def wait_for_traffic(self, bot_id, elapsed=None):
        result = bool(self.backend.call([204, self.handle, bot_id,
                                        int(elapsed is not None), elapsed or 0.0])[0])
        if result:
            self.states[bot_id]['traffic_waiting'] = True
        return result

    def remember_failure(self, bot_id, yaw, ttl=None):
        self.backend.call([205, self.handle, bot_id, yaw, int(ttl is not None), ttl or 0.0])

    def drive(self, bot_id, team_slot, position, yaw, speed, dt, target,
              neighbours, direction_clear, velocity=None,
              half_length=3.5, half_width=1.7, movement_intent=True,
              stopping_distance=None, stop_at_target=True,
              decision_horizon=0.0, pose_clear=None):
        own_length, own_width = max(0.5, float(half_length)), max(0.3, float(half_width))
        rows = []
        for raw in neighbours or ():
            point = self.original._neighbour_position(raw)
            if point is None:
                continue
            try:
                values = [float(point[0]), float(point[1]), float(point[2])]
                if isinstance(raw, dict):
                    values.extend((float(raw.get('yaw', 0.0) or 0.0),
                                   float(raw.get('half_length', own_length) or own_length),
                                   float(raw.get('half_width', own_width) or own_width),
                                   int(raw.get('id') is not None), raw.get('id') or 0,
                                   int(bool(raw.get('alive', True)))))
                else:
                    values.extend((0.0, own_length, own_width, 0, 0, 1))
                rows.extend(values)
            except (TypeError, ValueError, IndexError, OverflowError):
                # Runtime neighbours are already admitted plain numeric data.
                # Fail explicitly rather than silently change malformed-input
                # behavior of the differently guarded source geometry loops.
                raise ValueError('native driver requires normalized neighbours')
        try:
            stopping = max(0.0, float(stopping_distance)) if stopping_distance is not None else 0.0
            horizon = max(0.0, float(decision_horizon))
        except (TypeError, ValueError, OverflowError):
            stopping, horizon = 0.0, 0.0
        packet = ([201, self.handle, bot_id, int(team_slot)] + list(position) +
                  [yaw, speed, dt] + list(target) +
                  [half_length, half_width, int(bool(movement_intent)),
                   int(stopping_distance is not None), stopping, int(bool(stop_at_target)),
                   horizon, int(pose_clear is not None), round(float(target[0]), 2),
                   round(float(target[2]), 2), len(rows) // 9] + rows + [0] * 7)
        try:
            if self.synchronous:
                navigator = getattr(self.runtime, 'navigator', None)
                nav_handle = getattr(navigator, 'handle', None)
                state = self.runtime.states.get(bot_id, {}) if self.runtime is not None else {}
                packet[0] = 208
                packet[-7:-7] = [nav_handle or 0, int(state.get('_water_depth', -1.0) > 0.90),
                                 int(self.bake_admitted)]
                query_packet = array('d', [0.0] * 16)

                def query():
                    kind, query_yaw, distance = int(query_packet[0]), query_packet[1], query_packet[2]
                    self.queries += 1
                    if kind == 1:
                        answer = self.original._clear(direction_clear, query_yaw,
                                                      None if distance < 0 else distance)
                    elif kind == 2:
                        answer = self.original._pose_fits(pose_clear, query_yaw)
                    else:
                        raise RuntimeError('unknown synchronous driver query')
                    query_packet[0] = int(answer)

                result = self.backend.call_sync(packet, query_packet, query)[-7:]
            else:
                result = self.backend.call(packet)[-7:]
            while result[0]:
                kind, query_yaw, distance = int(result[0]), result[1], result[2]
                self.queries += 1
                if kind == 1:
                    answer = self.original._clear(direction_clear, query_yaw,
                                                  None if distance < 0 else distance)
                elif kind == 2:
                    answer = self.original._pose_fits(pose_clear, query_yaw)
                else:
                    raise RuntimeError('unknown native driver query')
                result = self.backend.call([202, self.handle, int(answer)] + [0] * 7)[-7:]
        except Exception:
            self.backend.call([207, self.handle])
            raise
        self.states.setdefault(bot_id, {}).pop('traffic_waiting', None)
        output = dict(throttle=result[1], turn=result[2], target_yaw=result[3],
                      recovery_mode=MODES[int(result[4])])
        if result[5]:
            output['reverse_blocked_by'] = True if result[5] == 1 else int(result[6])
        return output

    def dump_state(self, bot_id):
        values = self.backend.call([206, self.handle, bot_id] + [0] * 97)
        if not values[0]:
            return None
        it = iter(values[1:])
        state = dict(team_slot=int(next(it)), last_position=(next(it), next(it)),
                     stuck_time=next(it), recovery_time=next(it),
                     recovery_count=int(next(it)), recovery_side=next(it))
        def optional():
            present, value = next(it), next(it)
            return value if present else None
        state['steering_yaw'] = optional()
        state['steering_reason'] = 'obstacle' if next(it) else 'route'
        for key in ('steering_age', 'plan_age', 'recovery_timing_phase', 'clock',
                    'escape_side', 'escape_side_until'):
            state[key] = next(it)
        for key in ('last_desired_yaw', 'heading_progress_yaw', 'best_heading_error'):
            state[key] = optional()
        present, waiting = next(it), next(it)
        if present:
            state['traffic_waiting'] = bool(waiting)
        state['traffic_wait_time'], state['last_step'] = next(it), next(it)
        braking, x, z = next(it), next(it), next(it)
        state['braking_target'] = (x, z) if braking else None
        clear_yaw = optional()
        if clear_yaw is not None:
            state['last_clear_yaw'] = clear_yaw
        failed = {}
        for unused in range(int(next(it))):
            key, expires = int(next(it)), next(it)
            failed[key] = expires
        state['failed_yaws'] = failed
        return state
