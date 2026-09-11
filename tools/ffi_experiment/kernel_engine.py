"""Numeric native-query leaves; mutable simulation state stays in C++."""
from __future__ import print_function

from array import array
import math

from kernel_adapter import ENGINE_FIELDS
from motion_adapter import MotionFlowBackend


def _make_actor_decoder():
    # Compile the fixed numeric ABI once. Field names and conversions are
    # repository constants, so callbacks do not repeatedly classify fields.
    boolean = set(('alive', 'airborne', 'grounded_once', 'hull_aiming',
                   'gun_aligned', '_drowning', '_overturned'))
    integer = set(('id', 'team', 'fire_seq', 'shell_index', 'health', 'max_health',
                   'movement_dir', 'rotation_dir', 'siege_state', 'siege_time_left_ms',
                   'siege_transition_total_ms', 'stun_end_server_time_ms'))
    lines = ['def decode(query, at, mask, value):']
    for index, name in enumerate(ENGINE_FIELDS):
        expression = 'query[at + %d]' % (index + 3)
        if name in boolean:
            expression = 'bool(' + expression + ')'
        elif name in integer:
            expression = 'int(' + expression + ')'
        lines.extend(('    if mask & %d:' % (1 << index),
                      '        value[%r] = %s' % (name, expression),
                      '    else:', '        value.pop(%r, None)' % name))
    namespace = {}
    eval(compile('\n'.join(lines), 'kernel_actor_abi', 'exec'), namespace)
    return namespace['decode']


_DECODE_ACTOR = _make_actor_decoder()
_ACTOR_WIDTH = 3 + len(ENGINE_FIELDS) + 8
_ACTOR_ARGS = 1 + 2 * _ACTOR_WIDTH
_SOURCE_FIELDS = dict((name, 4 + index) for index, name in enumerate(ENGINE_FIELDS))
_SOURCE_XYZ = tuple(_SOURCE_FIELDS[name] for name in ('x', 'y', 'z'))


def _source_position(query):
    # BotRuntime._position reads x/y/z, even when a target position is present.
    return tuple(query[index] for index in _SOURCE_XYZ)


def _source_required(query, name):
    index = _SOURCE_FIELDS[name]
    if not int(query[3]) & (1 << (index - 4)):
        raise KeyError(name)
    return query[index]



class EngineLeaves(object):
    def __init__(self, module, runtime):
        self.module, self.runtime = module, runtime
        self.bots = dict((key, dict(value)) for key, value in runtime.states.items())
        self.players = {}
        self.objects = MotionFlowBackend.__new__(MotionFlowBackend)
        self.objects.module = module
        self.objects.active = {0: {}}
        self.objects.next_object = 1
        # The native motion owner consumes the full reviewed receipt schema.
        # No opaque Python probe object survives its callback.
        self.objects._save = lambda unused: 0
        self.calls = {}
        self.tokens, self.token_keys = {}, {}
        self.next_token = 1
        self.owned = False
        self.actor_cache = {}
        self.actor_calls = 0
        self.actor_decodes = 0

    @classmethod
    def freeze(cls, value):
        if isinstance(value, dict):
            return ('mapping', tuple(sorted((key, cls.freeze(item)) for key, item in value.items())))
        if isinstance(value, (tuple, list)):
            return (type(value).__name__, tuple(cls.freeze(item) for item in value))
        return value

    def token(self, value):
        if value is None:
            return 0
        key = self.freeze(value)
        token = self.token_keys.get(key)
        if token is None:
            token = self.next_token
            self.next_token += 1
            self.tokens[token] = value
            self.token_keys[key] = token
        return token

    def prepare(self, value, name=None):
        if name in ('_aim_token', 'aim_token', 'proof_key', 'shot_proof_key'):
            return None if value is None else dict(_engine_token=self.token(value))
        if isinstance(value, dict):
            return dict((key, self.prepare(item, key)) for key, item in value.items())
        if isinstance(value, (tuple, list)):
            return [self.prepare(item) for item in value]
        return value

    def resolve(self, value):
        if isinstance(value, dict):
            if set(value) == set(('_engine_token',)):
                return self.tokens[int(value['_engine_token'])]
            raw = self.resolve(value.get('_engine_payload'))
            result = dict(raw) if isinstance(raw, dict) else {}
            result.update((key, self.resolve(item)) for key, item in value.items()
                          if key != '_engine_payload')
            return result
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    def retain(self, identities):
        retained = set(identities)
        for identity in tuple(self.tokens):
            if identity not in retained:
                value = self.tokens.pop(identity)
                self.token_keys.pop(self.freeze(value))

    def actor(self, query, at):
        self.actor_calls += 1
        kind, identity, mask = int(query[at]), int(query[at + 1]), int(query[at + 2])
        end = at + _ACTOR_WIDTH
        if identity == 0 and mask == 0:
            return dict(kind='human' if kind else 'bot', network_id=0, position=(0.0, 0.0, 0.0)), end
        raw = query[at:end]
        signature = raw.tostring() if hasattr(raw, 'tostring') else raw.tobytes()
        # Owned updates clear this cache at their frame boundary. Different
        # projections of one actor can then reuse their exact numeric snapshot
        # without evicting each other. Standalone leaf audits stay bounded.
        cache_key = signature if self.owned else (kind, identity)
        cached = self.actor_cache.get(cache_key)
        if cached is not None and cached[0] == signature:
            return dict(cached[1]), end
        value = dict((self.players if kind else self.bots).get(identity, {}))
        self.actor_decodes += 1
        _DECODE_ACTOR(query, at, mask, value)
        value['kind'] = 'human' if kind else 'bot'
        value['network_id'] = identity
        at += 3 + len(ENGINE_FIELDS)
        for name in ('position', 'velocity'):
            if query[at]:
                value[name] = tuple(query[at + 1:at + 4])
            else:
                value.pop(name, None)
            at += 4
        if 'position' not in value:
            value['position'] = self.module._position(value)
        self.actor_cache[cache_key] = (signature, value)
        return dict(value), at

    def __call__(self, query):
        kind = int(query[0])
        if 500 <= kind <= 504:
            self.runtime.navigator._publish_event(query)
            return 0
        if self.owned:
            self.runtime._sample_time_us = int(query[254])
            self.runtime._equipment_now = query[255]
        at = _ACTOR_ARGS
        self.calls[kind] = self.calls.get(kind, 0) + 1
        # Primitive leaves do not consume vehicle mappings. Keep fresh copies
        # for callbacks that do: they may retain or mutate their arguments.
        if kind == 760:
            return self.motion(query, at)
        if kind == 769:
            try:
                value = float(self.runtime._water_depth_probe(_source_position(query)))
                if math.isnan(value) or math.isinf(value):
                    value = -1
            except Exception:
                value = -1
            query[0], query[1] = value >= 0, value
            return 0
        if kind == 771:
            self.runtime.destructible_body_scan(
                int(_source_required(query, 'id')), _source_position(query),
                _source_required(query, 'yaw'), _source_required(query, 'speed'))
            return 0
        if kind == 766:
            try:
                self.runtime.artillery_launch_cancel(dict(id=int(_source_required(query, 'id'))))
            except Exception:
                pass
            query[0] = 0
            return 0
        source, unused = self.actor(query, 1)
        target = self.actor(query, 1 + _ACTOR_WIDTH)[0] if kind != 761 else None
        if kind in (751, 752):
            callback = (self.runtime.firing_lane_probe if kind == 751
                        else self.runtime.incoming_lane_probe)
            try:
                result = callback(source, target)
            except Exception:
                if kind == 751:
                    raise
                result = None
            query[0] = bool(result) if kind == 751 else result is not None
            query[1] = bool(result)
            return 0
        if 761 <= kind <= 766:
            return self.aim(query, source, target, at)
        if kind == 767:
            try:
                try:
                    value = self.runtime.visibility_probe(source, target, bool(query[at]))
                except TypeError:
                    value = self.runtime.visibility_probe(source, target)
            except Exception:
                value = False
            query[0] = bool(value.get('line_of_sight', False)) if isinstance(value, dict) else bool(value)
            query[1] = float(value.get('foliage_bonus', 0)) if isinstance(value, dict) else 0
            return 0
        if kind == 770:
            route = tuple(query[at:at + 3])
            count = int(query[at + 3])
            allies = tuple(tuple(query[at + 4 + index * 3:at + 7 + index * 3]) for index in range(count))
            try:
                candidates = self.runtime.cover_probe(source, target, route, allies,
                    self.runtime.navigator.grid.segment_clear if self.runtime.navigator is not None else None)
                candidates = list(candidates) if candidates else []
            except Exception:
                candidates = []
            query[0], query[1] = bool(candidates), self.token(candidates) if candidates else 0
            return 0
        if kind == 768:
            bodies = []
            for actor in (source, target):
                raw = query[at:at + 13]
                body = dict((key, actor[key]) for key in ('alive', 'team', 'vehicle', 'x', 'y', 'z', 'yaw') if key in actor)
                body.update(id=int(raw[0]), kind='player' if raw[1] else 'bot',
                            network_id=actor['network_id'], mass=raw[3],
                            vx=raw[4], vz=raw[6], shape=tuple(raw[7:11]),
                            ram_profile=dict(spall_coefficient=raw[11], ramming_bonus=raw[12]))
                if raw[1]:
                    body['impulse'] = bool(raw[2])
                else:
                    body.update(vy=raw[5], pitch=actor.get('pitch', 0), roll=actor.get('roll', 0))
                bodies.append(body)
                at += 13
            value = self.runtime.ram_contact_probe(bodies[0], bodies[1], tuple(query[at:at + 3]))
            reply = [0]
            if value is not None:
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise RuntimeError('tank contact armor probe result is invalid')
                reply = [1, value[0] is not None, float(value[0] or 0),
                         value[1] is not None, float(value[1] or 0)]
            query[:len(reply)] = array('d', reply)
            return 0
        raise RuntimeError('unknown native kernel engine query %d' % kind)

    def descriptor(self, source):
        identity = source.get('network_id', source.get('id'))
        return self.descriptor_at(identity, int(source.get('siege_state', 0)))

    def descriptor_at(self, identity, siege_state):
        pair = self.runtime._descriptor_pairs.get(identity) if self.owned else None
        return (self.module.siege_mechanics.active_descriptor(pair, siege_state)
                if pair is not None else self.runtime._descriptors.get(identity))

    def motion(self, query, at):
        runtime, module = self.runtime, self.module
        values = query[at:at + 128]
        kind, bot_id = int(values[0]), int(values[1])
        if kind in (610, 611, 615):
            descriptor = self.descriptor_at(int(query[2]), int(query[_SOURCE_FIELDS['siege_state']]))
        self.calls[kind] = self.calls.get(kind, 0) + 1
        if kind == 610:
            try:
                result = runtime.direction_probe(tuple(values[2:5]), values[5], values[6],
                    descriptor, values[8] if values[7] and values[9] else None, None)
            except Exception:
                result = dict(clear=False, collision=True, water=False, slope=0.0)
            reply = self.objects._probe(result)
        elif kind == 611:
            try:
                result = runtime.world_receipt_probe(tuple(values[2:5]), values[5], values[6],
                    descriptor, values[9] if values[8] else None)
            except Exception:
                result = None
            reply = self.objects._receipt(result)
        elif kind == 630:
            result = runtime._physics_ground_probe(values[2], values[3], values[4])
            reply = [result is not None, float(result) if result is not None else 0]
        elif kind == 615:
            source = self.actor(query, 1)[0]
            # The engine resolver reads these exact callback-visible fields.
            # Its borrowed mirror exists only during this synchronous leaf.
            before = runtime.states.get(bot_id)
            turn = runtime._turn_speeds.get(bot_id)
            corridor = runtime.motion_world_corridor_reusable
            receipt = runtime.motion_world_receipt_reusable
            runtime.states[bot_id] = source
            runtime._turn_speeds[bot_id] = source.get('_kernel_turn_speed', 0)
            runtime.motion_world_corridor_reusable = lambda *unused: bool(values[20])
            runtime.motion_world_receipt_reusable = lambda *unused: bool(values[21])
            try:
                args = (bot_id, tuple(values[2:5]), values[5], values[6],
                        descriptor, values[7], values[8])
                if values[9]:
                    args = (bot_id, tuple(values[2:5]), source.get('yaw', 0), values[6],
                            descriptor, values[7], values[8], bool(values[10]))
                    result = runtime.motion_resolver(*args, motion_yaw=(
                        values[5] if values[6] >= 0 else values[5] + math.pi))
                else:
                    result = runtime.motion_resolver(*args)
            finally:
                runtime.states[bot_id] = before
                if turn is None:
                    runtime._turn_speeds.pop(bot_id, None)
                else:
                    runtime._turn_speeds[bot_id] = turn
                runtime.motion_world_corridor_reusable = corridor
                runtime.motion_world_receipt_reusable = receipt
            reply = [('clear', 'crushed', 'soft', 'cap_crushed', 'hard').index(result)]
        elif kind == 616:
            runtime.motion_report(bot_id, ('clear', 'crushed', 'soft', 'cap_crushed', 'hard')[int(values[2])], values[3], values[4])
            reply = [0]
        elif kind == 635:
            try:
                result = runtime.direction_probe(tuple(values[2:5]), values[5], values[6],
                                                   None, values[7], values[8])
            except Exception:
                result = dict(clear=False, collision=True, water=False, slope=0.0)
            reply = [runtime._probe_is_clear(result)]
        else:
            raise RuntimeError('unknown native motion leaf %d' % kind)
        query[:len(reply)] = array('d', reply)
        return 0

    @staticmethod
    def point(value):
        result = tuple(float(item) for item in value)
        if len(result) != 3 or any(math.isnan(item) or math.isinf(item) for item in result):
            raise ValueError('invalid native point')
        return result

    def aim(self, query, source, target, at):
        kind, runtime = int(query[0]), self.runtime
        args = query[at:]
        descriptor = self.descriptor(source)
        reply = [0]
        if kind == 761:
            try:
                point = runtime.direct_launch_origin_probe(
                    source, descriptor, int(args[0]), int(args[1]), args[2], args[3], args[4])
                reply = [1] + list(self.point(point))
            except Exception:
                pass
        elif kind == 762:
            value = runtime.direct_aim_point_probe(source, target)
            try:
                reply = [1] + list(self.point(value['aim_position'])) + [self.token(value['aim_token'])]
            except (TypeError, KeyError, ValueError, OverflowError):
                pass
        elif kind == 763:
            value = runtime.ballistic_solution_probe(source, target, descriptor, int(args[0]), args[1])
            try:
                position = self.point(value['aim_position'])
                pitch, flight, yaw = (float(value[name]) for name in ('pitch', 'flight_time', 'yaw'))
                if any(math.isnan(item) or math.isinf(item) for item in (pitch, flight, yaw)):
                    raise ValueError('invalid ballistic solution')
                reply = [1] + list(position) + [pitch, flight, yaw, int(value.get('arc') == 'high'), self.token(value)]
            except (TypeError, KeyError, ValueError, OverflowError):
                pass
        elif kind == 764:
            value = runtime.artillery_launch_probe(source, target, descriptor,
                int(args[0]), int(args[1]), args[2], args[3], args[4], args[5])
            try:
                point, velocity = self.point(value['origin']), self.point(value['velocity'])
                numbers = [float(value[name]) for name in (
                    'shot_yaw', 'shot_pitch', 'gravity', 'max_distance', 'max_time_ms',
                    'fire_seq', 'shell_index', 'flight_time')]
                if any(math.isnan(item) or math.isinf(item) for item in numbers):
                    raise ValueError('invalid artillery receipt')
                reply = [1] + list(point + velocity) + numbers + [self.token(value['proof_key']), self.token(value)]
            except (TypeError, KeyError, ValueError, OverflowError):
                pass
        elif kind == 765:
            artillery = bool(args[0])
            launch = dict(shell_index=int(args[1]), fire_seq=int(args[2]),
                          shot_yaw=args[3], shot_pitch=args[4], flight_time=args[5],
                          origin=tuple(args[6:9]))
            if artillery:
                raw = self.tokens.get(int(args[16]))
                if isinstance(raw, dict):
                    launch = dict(raw)
                launch.update(velocity=tuple(args[9:12]), gravity=args[12],
                              max_distance=args[13], max_time_ms=int(args[14]),
                              proof_key=self.tokens.get(int(args[15])))
                value = runtime.artillery_friendly_lane_probe(source, target, descriptor,
                                                               int(args[1]), launch)
            else:
                value = runtime.friendly_lane_probe(source, target, descriptor,
                                                     int(args[1]), launch)
            clear, verdict = runtime._friendly_lane_verdict(value)
            reply = [int(clear), 0]
            try:
                identity = verdict['blocker_kind']
                if identity not in ('bot', 'player'):
                    raise ValueError('invalid blocker kind')
                position = self.point(verdict['blocker_position'])
                reply = [int(clear), 1, int(identity == 'player'),
                         int(verdict['blocker_id']), int(verdict['blocker_team'])] + list(position) + [float(verdict.get('blocker_radius', 0))]
            except (TypeError, KeyError, ValueError, OverflowError):
                pass
        query[:len(reply)] = array('d', reply)
        return 0
