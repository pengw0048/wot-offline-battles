"""Owned native Bot state with JSON only at configuration/publication edges."""
from __future__ import print_function

from array import array
import json


def packet(prefix, value):
    payload = json.dumps(value, separators=(',', ':'), allow_nan=False)
    if not isinstance(payload, bytes):
        payload = payload.encode('utf-8')
    result = array('d', prefix)
    result.append(len(payload))
    result.extend(bytearray(payload))
    return result


def codec_config(codec):
    return dict(scalars=codec.SCALARS, clamps=codec.CLAMPS,
                groups=codec.OPTIONAL_GROUPS, devices=codec.DEVICE_NAMES,
                crew=codec.CREW_NAMES, device_states=codec.DEVICE_STATES)


def bot_config(state, gun, ammo, burst):
    return dict(state=state, gun=gun.__dict__, ammo=ammo.__dict__,
                burst=burst.__dict__)


def critical_config(rt, runtime, bot_id):
    descriptor = runtime._descriptors[bot_id]
    return descriptor_critical_config(rt, descriptor,
                                     runtime._bot_repair_factor(bot_id, descriptor))


def descriptor_critical_config(rt, descriptor, factor=1.0):
    damage = rt.device_damage
    return dict(devices=[dict(
        name=name, maximum=damage.device_max_hp(descriptor, name),
        cap=damage.device_regen_hp(descriptor, name),
        seconds=damage.repair_seconds(name, descriptor, 100.0, False, factor),
        no_fire_repair=name in damage.NO_REPAIR_PROGRESS_DEVICES)
        for name in rt.bot_state_codec.DEVICE_NAMES],
        roster=rt._descriptor_crew_roster(descriptor),
        fire_duration=damage.FIRE_DURATION_SECONDS,
        fire_fraction=damage.FIRE_DAMAGE_FRACTION_PER_SEC,
        critical_fraction=damage.CRITICAL_HP_FRACTION)


def player_config(rt, runtime, raw):
    rt._player_effective_params(raw)
    profile = runtime._player_vehicle_profile(raw)
    descriptor = profile['descriptor']
    result = dict(raw)
    result['_kernel'] = dict(
        class_tag=profile['class_tag'], armor=profile['armor'],
        base_view=rt._value(rt._value(descriptor, 'turret', {}),
                            'circularVisionRadius', 330.0),
        view_misc=rt._value(rt._value(descriptor, 'miscAttrs', {}),
                            'circularVisionRadiusFactor', 1.0),
        critical_config=descriptor_critical_config(rt, descriptor))
    return result


def health_config(rt, descriptor):
    names = (
        'BOT_DROWNING_PROBE_SECONDS', 'BOT_DROWNING_SECONDS',
        'BOT_DROWNING_DEATH_REASON', 'BOT_OVERTURN_IGNORE_SECONDS',
        'BOT_OVERTURN_WARNING_COSINE', 'BOT_OVERTURN_DANGER_COSINE',
        'BOT_OVERTURN_DEATH_SECONDS', 'BOT_OVERTURN_DEATH_REASON')
    result = dict((name, getattr(rt, name)) for name in names)
    result['water_offset'] = rt._water_sensor_geometry(descriptor)[0]
    return result


def factor_config(rt):
    damage = rt.device_damage
    names = rt.bot_state_codec.CREW_NAMES
    rows = {}
    for stat in ('reload', 'aim_time', 'dispersion', 'turret_speed',
                 'mobility', 'vision', 'signal'):
        rows[stat] = [damage.crew_stat_factor(
            [name for index, name in enumerate(names) if mask & (1 << index)],
            stat) for mask in range(1 << len(names))]
    return dict(crew_names=names, crew_rows=rows,
                module_specs=damage._MODULE_STAT_SPEC,
                minimum_vision=damage.MIN_VISION_FACTOR)


def perception_config(rt, runtime):
    return dict(
        ttl=rt.VISIBILITY_SAMPLE_SECONDS,
        shot_seconds=rt.spotting.SHOT_CAMOUFLAGE_SECONDS,
        proximity=rt.spotting.PROXIMITY_SPOT_DISTANCE,
        maximum=rt.spotting.MAX_SPOT_DISTANCE,
        memory=rt.spotting.SPOT_MEMORY_SECONDS,
        designated=rt.spotting.DESIGNATED_SPOT_MEMORY_SECONDS,
        budget=rt.MAX_VISIBILITY_PROBES_PER_FRAME,
        moving_epsilon=rt.spotting.MOVING_SPEED_EPSILON,
        human_base=rt.HUMAN_TARGET_ID_BASE,
        last_effort=rt.spotting.LAST_EFFORT_SECONDS,
        pose_fields=rt._TARGET_POSE_FIELDS,
        bot_fields=rt._TARGET_DYNAMIC_FIELDS + rt._BOT_TARGET_STATIC_FIELDS,
        human_fields=rt._TARGET_DYNAMIC_FIELDS + rt._HUMAN_TARGET_STATIC_FIELDS,
        factors=factor_config(rt))


ENGINE_FIELDS = (
    'id team x y z yaw pitch roll aim_yaw turret_yaw gun_pitch speed '
    'fire_seq shell_index alive health max_health half_length half_width '
    'vertical_speed airborne grounded_once movement_dir rotation_dir '
    'siege_state siege_time_left_ms siege_transition_total_ms '
    'hull_aiming gun_aligned skill_rating _drowning _overturned '
    'combat_fire_elapsed combat_fire_timer stun_end_server_time_ms '
    'terrain_pitch suspension_pitch _kernel_turn_speed'
).split()


def lane_config(rt, runtime):
    return dict(phases=rt.SHOT_LANE_PHASES, refresh=rt.SHOT_LANE_REFRESH_SECONDS,
                seconds=rt.SHOT_LANE_SECONDS, distance=rt.SHOT_LANE_QUERY_DISTANCE,
                spg_distance=rt.SPG_SHOT_LANE_QUERY_DISTANCE,
                control=runtime._control_seconds,
                has_incoming=callable(runtime.incoming_lane_probe))


def motion_config(rt, runtime):
    from motion_adapter import CONSTANTS
    return dict(tuning=dict((name, getattr(rt.vehicle_physics, name)) for name in CONSTANTS),
                factors=factor_config(rt), probe_seconds=rt.MOTION_PROBE_SECONDS,
                navigation=getattr(runtime.navigator, 'handle', 0),
                driver=getattr(runtime.adapter.driver, 'handle', 0),
                receipt_budget=rt.MAX_WORLD_RECEIPTS_PER_FRAME,
                has_receipt=callable(runtime.world_receipt_probe),
                has_resolver=callable(runtime.motion_resolver),
                has_report=callable(runtime.motion_report),
                bake_admitted=getattr(runtime.adapter.driver, 'bake_admitted', False),
                siege_enabled=rt.siege_mechanics.ENABLED,
                slope_metres=rt.SLOPE_SAMPLE_METRES,
                slope_radians=rt.SLOPE_SAMPLE_RADIANS)


def gunner_config(rt, runtime, bot_id):
    result = dict(rt.bot_gunnery.rating_parameters(runtime.bot_rating(bot_id)))
    result.update(epoch_seconds=rt.bot_gunnery.AIM_BIAS_SECONDS,
                  maximum_offset=rt.bot_gunnery.MAX_AIM_OFFSET_METRES,
                  vertical_share=rt.bot_gunnery.VERTICAL_BIAS_SHARE)
    return result


def aim_config(rt, runtime):
    return dict(factors=factor_config(rt), maximum_time=rt.ballistics.PROJECTILE_MAX_FLIGHT_SECONDS,
                action_seconds=rt.LOCAL_ACTION_SECONDS, intent_seconds=rt.ARTILLERY_INTENT_SECONDS,
                total_seconds=rt.ARTILLERY_TOTAL_PROOF_SECONDS,
                reproof_seconds=rt.ARTILLERY_REPROOF_SECONDS,
                staleness_metres=rt.ARTILLERY_AIM_STALENESS_METRES,
                has_part=callable(runtime.direct_aim_point_probe),
                has_solution=callable(runtime.ballistic_solution_probe),
                has_launch=callable(runtime.artillery_launch_probe),
                has_cancel=callable(runtime.artillery_launch_cancel))


def aim_profile(rt, runtime, bot_id, combat):
    descriptor = runtime._descriptors[bot_id]
    gun = runtime._gun_states[bot_id]
    return dict(handle=combat.profile(descriptor),
                shells=[rt._shot_ballistics(descriptor, index) for index in range(gun.shell_count)],
                turret_speed=rt._rotation_speed(rt._value(descriptor, 'turret', {}) or {}, .5) *
                             max(0.0, gun.loadout.get('crew_factor', 1)),
                gun_speed=rt._rotation_speed(rt._value(descriptor, 'gun', {}) or {}, .35) *
                          max(0.0, gun.loadout.get('gun_rotation_factor', 1)))


def driver_config(rt, runtime):
    return dict(driver=runtime.adapter.driver.handle,
                navigation=getattr(runtime.navigator, 'handle', 0),
                bake_admitted=runtime.adapter.driver.bake_admitted,
                lookahead=rt.BAKED_MOTION_LOOKAHEAD_SECONDS,
                publication_seconds=rt.PUBLICATION_SECONDS,
                speed_epsilon=rt.TRAFFIC_DIRECTION_SPEED_EPSILON)


class Kernel(object):
    def __init__(self, backend, codec, bots, **configuration):
        self.backend = backend
        configuration.update(codec=codec_config(codec), bots=bots)
        self.handle = int(backend.call(packet(
            [700], configuration))[0])

    def output(self, size):
        size = int(size)
        values = self.backend.call(array('d', [719, self.handle]) +
                                   array('d', [0]) * max(0, (size + 7) // 8 - 1))
        raw = values.tostring() if hasattr(values, 'tostring') else values.tobytes()
        return json.loads(raw[8:8 + size].decode('utf-8'))

    def weapon_actions(self, bot_id, actions):
        result = self.backend.call(packet([710, self.handle, bot_id], actions))
        return self.output(result[0])

    def encode(self, states):
        result = self.backend.call(packet([711, self.handle], states))
        return self.output(result[0])

    def equipment_actions(self, bot_id, actions):
        result = self.backend.call(packet([712, self.handle, bot_id], actions))
        return self.output(result[0])

    def round(self, inputs):
        result = self.backend.call(packet([713, self.handle], inputs))
        return self.output(result[0])

    def gunnery_actions(self, bot_id, actions):
        result = self.backend.call(packet([716, self.handle, bot_id], actions))
        return self.output(result[0])

    def launch_actions(self, bot_id, actions):
        result = self.backend.call(packet([717, self.handle, bot_id], actions))
        return self.output(result[0])

    def motion_actions(self, actions, callback):
        query = array('d', [0]) * 256
        result = self.backend.call_sync(packet([718, self.handle], actions), query,
                                        lambda: callback(query))
        return self.output(result[0])

    def aim_actions(self, actions, callback):
        query = array('d', [0]) * 256
        result = self.backend.call_sync(packet([720, self.handle], callback.prepare(actions)), query,
                                        lambda: callback(query))
        return callback.resolve(self.output(result[0]))

    def driver_actions(self, actions, callback):
        query = array('d', [0]) * 32768
        result = self.backend.call_sync(packet([721, self.handle], actions), query,
                                        lambda: callback(query))
        return self.output(result[0])

    def contact_actions(self, actions, callback):
        query = array('d', [0]) * 256
        result = self.backend.call_sync(packet([722, self.handle], actions), query,
                                        lambda: callback(query))
        return self.output(result[0])

    def health_actions(self, bot_id, actions):
        result = self.backend.call(packet([714, self.handle, bot_id], actions))
        return self.output(result[0])

    def perception_actions(self, actions, visibility, engine=None):
        query = array('d', [0]) * 256

        def callback():
            if int(query[0]) in (751, 752) and engine is not None:
                return engine(query)
            if int(query[0]) != 750:
                raise RuntimeError('unknown kernel perception query')
            source_kind, source_id, target_kind, target_id = (
                int(v) for v in query[1:5])
            source = dict(kind='human' if source_kind else 'bot',
                          id=source_id, network_id=source_id,
                          x=query[6], y=query[7], z=query[8])
            target = dict(kind='human' if target_kind else 'bot',
                          id=target_id, network_id=target_id,
                          x=query[9], y=query[10], z=query[11],
                          position=tuple(query[9:12]))
            try:
                try:
                    result = visibility(source, target, bool(query[5]))
                except TypeError:
                    result = visibility(source, target)
            except Exception:
                result = False
            query[0] = bool(result.get('line_of_sight', False)) if isinstance(result, dict) else bool(result)
            query[1] = float(result.get('foliage_bonus', 0)) if isinstance(result, dict) else 0
            return 0

        result = self.backend.call_sync(packet([715, self.handle], actions), query, callback)
        return self.output(result[0])

    def close(self):
        if self.handle:
            self.backend.call([705, self.handle])
            self.handle = 0


def simulation_config(rt, runtime):
    from gui.mods.offline_lan_0922.ai import navigation
    import sys
    return dict(
        debug=runtime.debug_logging, native_motion=runtime.native_motion,
        camera=runtime._camera_position, accumulator=runtime._accumulator,
        next_publication=runtime._next_publication,
        next_observation=runtime._next_observation,
        next_lane=runtime._next_shot_lane_refresh,
        next_cover=runtime._next_cover_refresh, equipment_now=runtime._equipment_now,
        sample=runtime._sample_time_us, edge_sample=runtime._edge_sample_time_us,
        edge_revision=runtime._edge_revision, control=runtime._control_seconds,
        maximum_step=rt.MAX_CONTROL_ELAPSED_SECONDS,
        decision_seconds=rt.DECISION_SECONDS, decision_tiers=rt.DECISION_TIER_FACTOR,
        near=rt.DETAIL_NEAR_METRES, far=rt.DETAIL_FAR_METRES,
        observation_seconds=rt.OBSERVATION_SECONDS,
        lane_seconds=rt.SHOT_LANE_REFRESH_SECONDS,
        final_lane_budget=rt.MAX_SHOT_LANE_PAIRS_PER_FRAME,
        lane_budget=rt.MAX_WORKER_SHOT_LANE_PAIRS_PER_FRAME,
        cover_seconds=rt.COVER_REFRESH_SECONDS, cover_jobs=rt.COVER_JOBS_PER_OBSERVATION,
        cover_window=rt.COVER_JOB_WINDOW_SECONDS,
        has_cover=callable(runtime.cover_probe), has_water=callable(runtime._water_depth_probe),
        has_destructible=callable(runtime.destructible_body_scan),
        destructible_speed=rt.DESTRUCTIBLE_SCAN_MIN_SPEED,
        human_base=rt.HUMAN_TARGET_ID_BASE, py2=sys.version_info[0] == 2,
        arrival=rt.ai_driver.WAYPOINT_ARRIVAL_RADIUS,
        contact_slop=rt.tank_collision.POSITION_SLOP,
        reposition_seconds=rt.FRIENDLY_REPOSITION_SECONDS,
        hull_penalty=navigation.STATIC_HULL_EDGE_PENALTY,
        slope_budget=rt.MAX_SLOPE_POSE_SAMPLES_PER_FRAME,
        siege_long=rt.SIEGE_LONG_TRAVEL_METRES,
        siege_enable_debounce=rt.SIEGE_ENABLE_DEBOUNCE_SECONDS,
        siege_disable_debounce=rt.SIEGE_DISABLE_DEBOUNCE_SECONDS,
        edge_fields=rt._PUBLICATION_EDGE_SCALAR_FIELDS,
        suspension_failures=runtime._suspension_param_failures)


class KernelBackend(object):
    """One native update call owns all mutable Bot simulation phases."""
    def __init__(self, backend, runtime, rt):
        from combat_adapter import CombatBackend
        from driver_adapter import DriverBackend
        from navigation_flow_adapter import NavigationFlowBackend
        from kernel_engine import EngineLeaves
        from gui.mods.offline_lan_0922.ai import navigation
        if runtime.native_motion or runtime._suspension_ground_probe is not None:
            raise ValueError('Owned kernel experiment requires the copied vertical controller')
        self.backend, self.runtime, self.module = backend, runtime, rt
        self.parts = [NavigationFlowBackend(backend, runtime, navigation),
                      DriverBackend(backend, runtime, True)]
        combat = CombatBackend(backend, rt)
        self.parts.append(combat)
        bots = []
        for identity, state in runtime.states.items():
            descriptor = runtime._descriptors[identity]
            value = bot_config(state, runtime._gun_states[identity],
                               runtime._ammo_states[identity], runtime._burst_states[identity])
            target = dict(state, kind='bot', network_id=identity)
            value.update(critical_config=critical_config(rt, runtime, identity),
                         equipment=[dict((name, getattr(item, name)) for name in item.__slots__)
                                    for item in runtime._equipment_states.get(identity, ())],
                         sync=runtime._combat_sync.get(identity),
                         turn_speed=runtime._turn_speeds.get(identity, 0))
            value['config'] = health_config(rt, descriptor)
            value['config'].update(
                gunnery=gunner_config(rt, runtime, identity),
                aim=aim_profile(rt, runtime, identity, combat),
                perception=dict(profile=runtime._spotting_profile(target),
                                vision=runtime._vision_ranges.get(identity)),
                motion=dict(physics=runtime._physics_params_for(identity),
                            yaw_limits=rt.ai_driver.gun_yaw_limits(descriptor)))
            pair = runtime._descriptor_pairs.get(identity)
            if pair is not None and pair[1] is not None:
                value['config']['siege_modes'] = [descriptor_mode(rt, runtime, identity, mode, combat)
                                                  for mode in (0, 2)]
                value['config']['siege_params'] = rt.siege_mechanics.params(pair[0])
            bots.append(value)
        orders = [[identity, dict(order, _kernel_token=runtime._server_order_tokens.get(identity, 0))]
                  for identity, order in runtime._server_orders.items()]
        self.kernel = Kernel(backend, rt.bot_state_codec, bots, round_id=runtime.round_id,
            engine=dict(fields=ENGINE_FIELDS), perception=perception_config(rt, runtime),
            lanes=lane_config(rt, runtime), motion=motion_config(rt, runtime),
            aim=aim_config(rt, runtime), driver=driver_config(rt, runtime), orders=orders,
            contacts=dict(human_base=rt.HUMAN_TARGET_ID_BASE,
                          has_armor=callable(runtime.ram_contact_probe),
                          py2=simulation_config(rt, runtime)['py2'], sequence=runtime._ram_seq),
            simulation=simulation_config(rt, runtime))
        self.engine = EngineLeaves(rt, runtime)
        self.engine.owned = True
        self.originals = {}
        self.order_revision = runtime._order_revision
        self.query = array('d', [0]) * 32768

    def update(self, dt, now, players=None, neighbours=None):
        self.engine.actor_cache.clear()
        supplied = []
        for raw in players or ():
            value = player_config(self.module, self.runtime, raw)
            value['_kernel']['collision'] = self.runtime._player_collision_profile(raw)
            supplied.append(value)
        self.engine.players = dict((item['id'], item) for item in players or ())
        inputs = dict(
            dt=dt, now=now, players=supplied, neighbours=neighbours or (),
            camera=self.runtime._camera_position)
        if self.runtime._order_revision != self.order_revision:
            inputs['orders'] = [[identity, dict(order, _kernel_token=self.runtime._server_order_tokens.get(identity, 0))]
                                for identity, order in self.runtime._server_orders.items()]
        command = packet([723, self.kernel.handle], self.engine.prepare(inputs))
        result = self.backend.call_sync(command, self.query, lambda: self.engine(self.query))
        output = self.kernel.output(result[0])
        self.order_revision = self.runtime._order_revision
        messages = output['messages']
        for message in messages:
            for key in ('launches', 'affordances'):
                if key in message:
                    message[key] = self.engine.resolve(message[key])
        for row in output['logs']:
            print('[%s] %s' % (str(row['kind']), json.dumps(row['payload'], sort_keys=True, separators=(',', ':'))))
        self.engine.retain(output['tokens'])
        self.runtime._sample_time_us = output['sample']
        self.runtime._equipment_now = output['equipment_now']
        self.runtime._last_update_control_steps = output['steps']
        self.runtime._last_update_max_control_step = output['maximum_step']
        self.runtime.navigator._snapshot = None
        self.runtime.navigator._progress = None
        return messages

    def counters(self):
        result = self.backend.call([726, self.kernel.handle])
        output = self.kernel.output(result[0])
        output['decisions'] = dict((int(key), value) for key, value in output['decisions'].items())
        return output

    def probes(self):
        counts = self.counters()
        values = counts['motion_probes']
        values[0] = self.engine.calls.get(767, 0)
        values[1] = counts['lane_probes']
        values[2] = counts['cover_probes']
        return tuple(values)

    def diagnostics(self):
        return self.counters()['diagnostics']

    def install(self):
        for name, replacement in (('update', self.update), ('ack_projectile_launch', self.ack),
                                  ('probe_totals', self.probes), ('diagnostic_totals', self.diagnostics)):
            self.originals[name] = getattr(self.runtime, name)
            setattr(self.runtime, name, replacement)
        return self

    def snapshot(self):
        result = self.backend.call([724, self.kernel.handle])
        return self.engine.resolve(self.kernel.output(result[0]))

    def ack(self, identity, sequence):
        return bool(self.backend.call([725, self.kernel.handle, identity, sequence])[0])

    def close(self):
        for name, original in self.originals.items():
            setattr(self.runtime, name, original)
        self.engine.actor_cache.clear()
        self.kernel.close()
        for part in reversed(self.parts):
            part.close()


def descriptor_mode(rt, runtime, identity, mode, combat):
    """Read both immutable stock descriptor modes once at owner construction."""
    import copy
    shadow = copy.copy(runtime)
    for name in ('_descriptors', '_physics_params', '_suspension_params', '_gun_yaw_limits',
                 '_gun_states', '_repair_factors', '_vision_ranges', '_spotting_profiles',
                 '_motion_probe_cache', '_traffic_stopping_cache', '_artillery_intents',
                 '_artillery_reproofs'):
        setattr(shadow, name, dict(getattr(runtime, name)))
    shadow._gun_states[identity] = copy.deepcopy(runtime._gun_states[identity])
    shadow._traffic_coordinator = copy.deepcopy(runtime._traffic_coordinator)
    shadow.artillery_launch_cancel = None
    state = copy.deepcopy(runtime.states[identity])
    shadow._install_bot_descriptor(identity, state, mode)
    descriptor = shadow._descriptors[identity]
    target = dict(state, kind='bot', network_id=identity)
    names = ('move_speed', 'view_range', 'half_length', 'half_width', 'collision_shape', 'mass', 'ram_profile')
    return dict(gun=shadow._gun_states[identity].__dict__,
                critical=critical_config(rt, shadow, identity),
                health=health_config(rt, descriptor),
                state=dict((name, state[name]) for name in names),
                aim=aim_profile(rt, shadow, identity, combat),
                perception=dict(profile=shadow._spotting_profile(target),
                                vision=shadow._vision_ranges.get(identity)),
                motion=dict(physics=shadow._physics_params_for(identity),
                            yaw_limits=rt.ai_driver.gun_yaw_limits(descriptor),
                            siege_limit=rt.siege_mechanics.enabled_speed_limit(descriptor)))
