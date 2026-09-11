"""Native perception state; Python descriptor, engine, and message adapters.

Numeric roster templates are transferred at most once per actor motion phase
in a slice. The runtime's original pose dictionaries stay at its publication
boundary, including hidden-pose removal and per-team remembered articulation.
"""
from __future__ import print_function
from array import array

KINDS = {'bot': 0, 'human': 1}
OUTPUT, PACKET = 64, 576


class PerceptionBackend(object):
    def __init__(self, backend, runtime, runtime_module):
        self.backend, self.runtime, self.rt = backend, runtime, runtime_module
        self.originals = {}
        self.handle = None
        self.tick = None
        self.templates = {}
        self.roster = []
        self.cache_identity = None
        self.reset()
        for name, replacement in (
                ('_contacts_for', self.contacts), ('_visible', self.visible),
                ('_begin_visibility_frame', self.begin),
                ('_prepare_visibility_frame', self.prepare),
                ('_finish_visibility_frame', self.finish),
                ('_renew_team_spot', self.renew),
                ('_team_spot_time_left', self.remaining),
                ('diagnostic_totals', self.diagnostics)):
            self.originals[name] = (name in runtime.__dict__, getattr(runtime, name))
            setattr(runtime, name, replacement)

    def reset(self):
        if self.handle is not None:
            self.backend.call([312, self.handle])
        s = self.rt.spotting
        self.handle = int(self.backend.call(
            [300, self.rt.VISIBILITY_SAMPLE_SECONDS, s.SHOT_CAMOUFLAGE_SECONDS,
             s.PROXIMITY_SPOT_DISTANCE, s.MAX_SPOT_DISTANCE, s.SPOT_MEMORY_SECONDS,
             s.DESIGNATED_SPOT_MEMORY_SECONDS, self.rt.MAX_VISIBILITY_PROBES_PER_FRAME])[0])
        self.tick, self.templates, self.roster = None, {}, []
        self.cache_identity = self.runtime._visibility_cache

    def close(self):
        for name, (had_local, original) in self.originals.items():
            if had_local:
                setattr(self.runtime, name, original)
            else:
                delattr(self.runtime, name)
        self.originals.clear()

    def begin(self):
        if self.cache_identity is not self.runtime._visibility_cache:
            self.reset()
        self.backend.call([301, self.handle])

    def prepare(self, players, now, include_humans):
        rt = self.runtime
        rows = []
        for state in rt._ordered_states():
            if not state.get('alive', True):
                continue
            selected = rt._selected_visibility_target(state)
            rows.extend((0, int(state['id']), int(state.get('team', 0)),
                         self.fire(state), KINDS[selected[0]] if selected else -1,
                         selected[1] if selected else 0,
                         int(rt._visibility_decision_due(state, now))))
        for raw in players or ():
            if not isinstance(raw, dict) or raw.get('id') is None or not raw.get('alive', True):
                continue
            rows.extend((1, int(raw['id']), int(raw.get('team', 0)),
                         self.fire(raw), -1, 0, int(bool(include_humans))))
        return bool(self.backend.call([302, self.handle, now, len(rows) // 7] + rows)[0])

    def finish(self):
        return bool(self.backend.call([303, self.handle])[0])

    def diagnostics(self):
        report = self.originals['diagnostic_totals'][1]()
        values = self.backend.call([310, self.handle] + [0] * 18)
        for index, key in enumerate(('visibility_queue_depth', 'visibility_queue_max_depth',
                                      'visibility_oldest_stale_age_ms', 'visibility_oldest_stale_max_age_ms',
                                      'visibility_admitted', 'visibility_completed', 'visibility_deferred',
                                      'visibility_selected_services', 'visibility_fire_services',
                                      'visibility_new_services', 'visibility_ordinary_services')):
            report[key] = int(round(values[index] * 1000)) if index in (2, 3) else int(values[index])
        return report

    def renew(self, key, now, duration=None):
        duration = self.rt.spotting.SPOT_MEMORY_SECONDS if duration is None else duration
        self.backend.call([308, self.handle, key[0], KINDS[key[1]], key[2], now, duration])
        return True

    def remaining(self, key, now):
        return self.backend.call([309, self.handle, key[0], KINDS[key[1]], key[2], now])[0]

    def fire(self, raw):
        value = self.runtime._visibility_fire_sequence(raw)
        return -1 if value is None else value

    def record(self, raw, kind=None, identity=None, position=None):
        kind = raw.get('kind', 'bot') if kind is None else kind
        identity = raw.get('network_id', raw.get('id', 0)) if identity is None else identity
        position = position if position is not None else raw.get('position') or self.rt._position(raw)
        return [KINDS[kind], int(identity), int(raw.get('team', 0)),
                position[0], position[1], position[2], self.fire(raw)]

    def roster_for(self, players, tick):
        if tick is not None and self.tick is tick:
            return
        rows, roster = [], []
        for raw in players or ():
            if isinstance(raw, dict) and raw.get('id') is not None:
                roster.append(('human', int(raw['id']), raw))
        for bot_id, raw in self.runtime.states.items():
            roster.append(('bot', int(bot_id), raw))
        for kind, identity, raw in roster:
            rows.extend((KINDS[kind], identity, int(raw.get('team', 0)), int(bool(raw.get('alive', True)))))
        self.backend.call([305, self.handle, len(roster)] + rows)
        self.tick, self.roster, self.templates = tick, roster, {}

    def template(self, index, phase, tick, processed):
        key = (index, phase)
        target = self.templates.get(key)
        if target is None:
            kind, identity, raw = self.roster[index]
            if kind == 'human':
                target = self.runtime._human_observation_target(raw, tick, identity)
            else:
                target = self.runtime._bot_observation_target(identity, raw, tick, processed)
            self.templates[key] = target
        return target

    def run(self, values, source, now, tick, processed=None, target=None,
            view_resolver=None):
        packet = array('d', values)
        packet.extend([0] * (PACKET - len(packet)))
        view = [None]
        try:
            while True:
                self.backend.call(packet)
                stage = int(packet[OUTPUT])
                if not stage:
                    count = int(packet[OUTPUT + 1])
                    return [tuple(int(v) for v in packet[OUTPUT+2+i*4:OUTPUT+6+i*4])
                            for i in range(count)]
                index, phase, fired = int(packet[OUTPUT+1]), int(packet[OUTPUT+2]), bool(packet[OUTPUT+3])
                active = target if target is not None else self.template(index, phase, tick, processed)
                if stage == 1:
                    reply = self.record(active)
                elif stage == 2:
                    if view[0] is None:
                        view[0] = (view_resolver() if callable(view_resolver) else
                                   self.runtime._source_view_range(source, now, tick))
                    base, shot, unused_profile, moving, additive, multiplier = \
                        self.runtime._target_detection_projection(
                            active, active.get('network_id', active.get('id', 0)), now, tick)
                    reply = [view[0], base[0], base[1], shot, int(moving), additive, multiplier]
                elif stage == 3:
                    runtime = self.runtime
                    try:
                        runtime._probe_totals[0] += 1
                        started = runtime._probe_started()
                        try:
                            try:
                                visibility = runtime.visibility_probe(source, active, fired)
                            except TypeError:
                                visibility = runtime.visibility_probe(source, active)
                        finally:
                            runtime._probe_finished(0, started)
                    except Exception:
                        visibility = False
                    if isinstance(visibility, dict):
                        reply = [int(bool(visibility.get('line_of_sight', False))),
                                 self.rt._number(visibility.get('foliage_bonus'), 0.0)]
                    else:
                        reply = [int(bool(visibility)), 0.0]
                else:
                    raise RuntimeError('unknown native perception query')
                packet[:3+len(reply)] = array('d', [306, self.handle, stage] + reply)
        except Exception:
            self.backend.call([311, self.handle])
            raise

    def visible(self, source, target, now, tick_cache=None,
                source_position=None, view_range_resolver=None):
        rows = self.run([307, self.handle] + self.record(source, identity=source.get('id', 0),
                                                        position=source_position) +
                        [now] + self.record(target), source, now, tick_cache,
                        target=target, view_resolver=view_range_resolver)
        return bool(rows[0][1])

    def contacts(self, source, players, now, team_spotted=None,
                 visibility_tick=None, processed_bot_ids=None):
        self.roster_for(players, visibility_tick)
        source_team = int(source.get('team', 0))
        flags = []
        for kind, identity, raw in self.roster:
            key = (source_team, kind, identity)
            flags.extend((int(kind == 'bot' and processed_bot_ids is not None and identity in processed_bot_ids),
                          int(bool(team_spotted is not None and team_spotted.get(key, False))),
                          int(bool(raw.get('alive', True))), int(raw.get('team', 0))))
        rows = self.run([304, self.handle] + self.record(source, identity=source.get('id', 0),
                                                        position=self.rt._position(source)) +
                        [now] + flags, source, now, visibility_tick, processed_bot_ids)
        contacts, lookup = [], {}
        pose_cache = (visibility_tick.setdefault('target_pose_snapshots', {})
                      if isinstance(visibility_tick, dict) else None)
        hidden = (visibility_tick.setdefault('hidden_target_templates', {})
                  if isinstance(visibility_tick, dict) else {})
        for index, visible, direct, fresh in rows:
            kind, identity, unused_raw = self.roster[index]
            phase = int(kind == 'bot' and processed_bot_ids is not None and identity in processed_bot_ids)
            template = self.templates[(index, phase)]
            target = dict(template)
            target.update(visible=bool(visible), direct_visible=bool(direct), fresh_visible=bool(fresh))
            key = (source_team, kind, identity)
            if direct and team_spotted is not None:
                team_spotted[key] = True
            if fresh:
                remembered = self.runtime._target_pose_snapshot(template, pose_cache)
                self.runtime._visible_target_poses[key] = remembered
                target.update(remembered)
                keep = True
            else:
                remembered = self.runtime._visible_target_poses.get(key)
                cache_key = (source_team, id(template), id(remembered))
                cached = hidden.get(cache_key)
                if cached is not None:
                    keep = bool(visible and remembered is not None)
                    target = dict(cached[2])
                    target.update(visible=keep, direct_visible=bool(direct), fresh_visible=bool(fresh))
                else:
                    for name in self.rt._TARGET_POSE_FIELDS:
                        target.pop(name, None)
                    if remembered is None:
                        target.update(position=(0.0, 0.0, 0.0), x=0.0, y=0.0, z=0.0, yaw=0.0, speed=0.0, visible=False)
                    else:
                        target.update(remembered)
                    projection = dict(target)
                    for name in ('visible', 'direct_visible', 'fresh_visible'):
                        projection.pop(name, None)
                    hidden[cache_key] = (template, remembered, projection)
                    keep = bool(target.get('visible'))
            if keep:
                lookup[target['id']] = target
            contacts.append(target)
        return contacts, lookup
