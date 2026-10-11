# -*- coding: utf-8 -*-
from __future__ import division

"""Versioned, data-only launcher/host/worker Bot tactics contract (#1513).

No native game imports, environment reads or global mutable settings.  Hosts
load a bounded document only at an accepted round boundary.  An empty document
is deliberately the old behaviour, not an implicit retune.
"""
import copy
import hashlib
import io
import json
import math
import re

from gui.mods.offline_lan_0922.bot_editor_maps import MAPS

SCHEMA = 1
CLIENT = '0.9.22.0.1-cn-1513'
MAX_BYTES = 512 * 1024
CLASSES = ('lightTank', 'mediumTank', 'heavyTank', 'AT-SPG', 'SPG')
SKILLS = ('rookie', 'regular', 'veteran', 'elite')
PARAMETERS = {
    'reaction_seconds': (0.0, 5.0), 'patience_seconds': (0.0, 10.0),
    'converged_factor': (1.0, 5.0), 'aim_bias_factor': (0.0, 4.0),
    'lead_error': (0.0, 1.0),
}
TEXT = (str, type(u''))
ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,47}$')


class TacticsError(ValueError):
    pass


def _keys(raw, allowed, required=()):
    if not isinstance(raw, dict) or set(raw) - set(allowed) or set(required) - set(raw):
        raise TacticsError('Unexpected or missing configuration fields')


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TacticsError('A numeric value is required')
    value = float(value)
    if math.isnan(value) or math.isinf(value) or not low <= value <= high:
        raise TacticsError('Number outside allowed range [%s, %s]' % (low, high))
    return value


def integer(value, low, high):
    result = number(value, low, high)
    if result != int(result):
        raise TacticsError('An integer is required')
    return int(result)


def _text(value, limit=80):
    if not isinstance(value, TEXT) or not value.strip() or len(value) > limit:
        raise TacticsError('Text must contain 1..%d characters' % limit)
    if any(ord(c) < 32 for c in value):
        raise TacticsError('Control characters are not allowed')
    return value.strip()


def _id(value):
    if not isinstance(value, TEXT) or ID.match(value) is None:
        raise TacticsError('ID must be letters, digits, hyphen or underscore')
    return value


def empty(name='Default'):
    return {'schema': SCHEMA, 'client': CLIENT, 'name': name,
            'behavior': [], 'maps': {}}


def normalize_values(raw):
    _keys(raw, tuple(PARAMETERS) + ('skill', 'crew_level'))
    out = {}
    for key, value in raw.items():
        if key == 'skill':
            if value not in SKILLS:
                raise TacticsError('Unsupported Bot skill')
            out[key] = value
        elif key == 'crew_level':
            if isinstance(value, bool) or value not in (75, 90, 100):
                raise TacticsError('Crew level must be 75, 90 or 100')
            out[key] = int(value)
        else:
            out[key] = number(value, *PARAMETERS[key])
    return out


def point(raw, bounds):
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise TacticsError('Point must be [x, z]')
    return [round(number(raw[0], bounds[0], bounds[2]), 4),
            round(number(raw[1], bounds[1], bounds[3]), 4)]


def canonical(raw):
    _keys(raw, ('schema', 'client', 'name', 'behavior', 'maps'),
          ('schema', 'client', 'name', 'behavior', 'maps'))
    if isinstance(raw['schema'], bool) or raw['schema'] != SCHEMA or raw['client'] != CLIENT:
        raise TacticsError('Unsupported tactics version / client')
    out = empty(_text(raw['name'], 48))
    rules = raw['behavior']
    if not isinstance(rules, list) or len(rules) > 128:
        raise TacticsError('At most 128 behavior rules are allowed')
    selectors = set()
    for rule in rules:
        _keys(rule, ('team', 'class_tag', 'slot', 'values'), ('values',))
        team = integer(rule.get('team', 0), 0, 2)
        slot = integer(rule.get('slot', -1), -1, 14)
        tag = rule.get('class_tag', 'all')
        if tag not in ('all',) + CLASSES or (slot >= 0 and not team):
            raise TacticsError('A slot override needs a specific team')
        key = (team, tag, slot)
        if key in selectors:
            raise TacticsError('Duplicate behavior selector')
        selectors.add(key)
        values = normalize_values(rule['values'])
        if values:
            out['behavior'].append(dict(team=team, class_tag=tag, slot=slot, values=values))
    out['behavior'].sort(key=lambda r: (r['slot'] >= 0, r['class_tag'] != 'all',
                                        r['team'] != 0, r['team'], r['class_tag'], r['slot']))
    if not isinstance(raw['maps'], dict) or len(raw['maps']) > len(MAPS):
        raise TacticsError('Invalid map collection')
    total = 0
    for name, settings in raw['maps'].items():
        if name not in MAPS:
            raise TacticsError('Unknown #1513 map: %s' % name)
        meta = MAPS[name]
        _keys(settings, ('mode', 'resource_sha256', 'routes', 'positions'),
              ('mode', 'resource_sha256', 'routes', 'positions'))
        if settings['mode'] != 'regular' or settings['resource_sha256'] != meta['resource_sha256']:
            raise TacticsError('Map mode or resource fingerprint mismatch: %s' % name)
        entry = dict(mode='regular', resource_sha256=meta['resource_sha256'], routes=[], positions=[])
        for kind, limit in (('routes', 32), ('positions', 48)):
            if not isinstance(settings[kind], list) or len(settings[kind]) > limit:
                raise TacticsError('Too many %s on %s' % (kind, name))
            seen = set()
            for item in settings[kind]:
                common = ('id', 'label', 'team')
                allowed = common + (('classes', 'slots', 'policy', 'capacity', 'weight', 'points')
                                    if kind == 'routes' else ('point', 'radius', 'heading', 'priority'))
                _keys(item, allowed, common)
                identity = _id(item['id'])
                if identity in seen:
                    raise TacticsError('Duplicate %s ID' % kind)
                seen.add(identity)
                result = dict(id=identity, label=_text(item['label']), team=integer(item['team'], 1, 2))
                if kind == 'routes':
                    tags, slots = item.get('classes', []), item.get('slots', [])
                    if (not isinstance(tags, list) or not tags or len(tags) > len(CLASSES) or
                            any(t not in CLASSES for t in tags) or len(set(tags)) != len(tags)):
                        raise TacticsError('Invalid route vehicle classes')
                    if not isinstance(slots, list) or len(slots) > 15:
                        raise TacticsError('Invalid route slot list')
                    slots = [integer(v, 0, 14) for v in slots]
                    if len(set(slots)) != len(slots):
                        raise TacticsError('Duplicate slot')
                    policy = item.get('policy', 'preferred')
                    if policy not in ('preferred', 'fixed'):
                        raise TacticsError('Invalid route policy')
                    pts = item.get('points')
                    if not isinstance(pts, list) or not 1 <= len(pts) <= 16:
                        raise TacticsError('A route must contain 1..16 waypoints')
                    points = []
                    for pt in pts:
                        if not isinstance(pt, (list, tuple)) or len(pt) not in (3, 4):
                            raise TacticsError('Waypoint must be [x, z, hold, optional wait seconds]')
                        value = point(pt[:2], meta['bounds']) + [integer(pt[2], 0, 1)]
                        if len(pt) == 4:
                            wait = number(pt[3], -1, 3600)
                            if -1 < wait < 0:
                                raise TacticsError('Use -1 for a permanent hold')
                            value.append(wait)
                        if points and sum((value[i] - points[-1][i]) ** 2 for i in (0, 1)) < 1:
                            raise TacticsError('Consecutive waypoints need at least one metre separation')
                        points.append(value)
                    result.update(classes=sorted(tags), slots=sorted(slots), policy=policy,
                                  capacity=integer(item.get('capacity', 6), 1, 15),
                                  weight=number(item.get('weight', 1.0), 0.01, 10.0), points=points)
                else:
                    result.update(point=point(item.get('point'), meta['bounds']),
                                  radius=number(item.get('radius', 12.0), 3.0, 80.0),
                                  heading=number(item.get('heading', 0.0), -180.0, 180.0),
                                  priority=integer(item.get('priority', 5), 0, 9))
                entry[kind].append(result)
                total += 1
            entry[kind].sort(key=lambda a: a['id'])
        if entry['routes'] or entry['positions']:
            out['maps'][name] = entry
    if total > 600 or len(dumps(out).encode('utf8')) > MAX_BYTES:
        raise TacticsError('Tactics profile exceeds its bounded size')
    for name in out['maps']:
        for_round(out, name)
    return out


def dumps(raw):
    return json.dumps(raw, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(raw):
    return hashlib.sha256(dumps(raw).encode('ascii')).hexdigest()


def load(path):
    if not path:
        return empty()
    try:
        with io.open(path, 'rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise TacticsError('Tactics document is too large')
        return canonical(json.loads(data.decode('utf-8-sig')))
    except (IOError, UnicodeError, ValueError, TypeError) as error:
        raise TacticsError('Cannot load tactics: %s' % error)


def effective(raw, team, class_tag, slot):
    result = {}
    for rule in (raw or {}).get('behavior', ()):
        if (rule['team'] in (0, team) and rule['class_tag'] in ('all', class_tag) and
                rule['slot'] in (-1, slot)):
            result.update(rule['values'])
    return result


def map_settings(raw, name):
    return (raw or {}).get('maps', {}).get(name, {})


def route_config(raw, name, route_id):
    return next((r for r in map_settings(raw, name).get('routes', ())
                 if 'user_' + r['id'] == route_id), None)


def matches(route, state):
    return (route['team'] == state.get('team') and
            (state.get('profile') or {}).get('class_tag') in route['classes'] and
            (not route['slots'] or state.get('slot') in route['slots']))


def canonical_manual_plan(raw, config, map_name=None, vehicle=None, team=None):
    fields = ('schema', 'catalog', 'map', 'mode', 'side', 'zone', 'cell', 'source',
              'vehicle', 'point', 'face', 'radius', 'clearance', 'geometry', 'fire_validation')
    try:
        _keys(raw, fields, fields)
        name = raw['map']
        meta = MAPS[name]
        if (raw['schema'] != 1 or isinstance(raw['schema'], bool) or
                raw['catalog'] != digest(config) or raw['source'] != 'launcher_manual_v1' or
                raw['mode'] != 'regular' or raw['cell'] != 'manual' or raw['radius'] != 2.0 or
                raw['geometry'] != 'baked_checked' or raw['fire_validation'] != 'runtime_required' or
                (map_name is not None and name != map_name) or
                (vehicle is not None and raw['vehicle'] != vehicle)):
            return None
        zone = next(z for z in map_settings(config, name)['positions'] if z['id'] == raw['zone'])
        if team is not None and zone['team'] != team:
            return None
        if raw['side'] != 'team%d' % zone['team']:
            return None
        _text(raw['vehicle'])
        _keys(raw['point'], ('x', 'y', 'z'), ('x', 'y', 'z'))
        _keys(raw['face'], ('x', 'y', 'z'), ('x', 'y', 'z'))
        for p in (raw['point'], raw['face']):
            point([p['x'], p['z']], meta['bounds']); number(p['y'], -1000, 1000)
        number(raw['clearance'], 2, 50)
        if math.hypot(raw['point']['x'] - zone['point'][0], raw['point']['z'] - zone['point'][1]) + 2 > zone['radius'] + 1e-5:
            return None
        return copy.deepcopy(raw)
    except (KeyError, StopIteration, TypeError, ValueError):
        return None


def for_round(profile, map_name):
    """Transmit this map only; never inflate the 256 KiB battle-start frame."""
    selected = empty(profile['name'])
    selected['behavior'] = copy.deepcopy(profile['behavior'])
    if map_name in profile['maps']:
        selected['maps'][map_name] = copy.deepcopy(profile['maps'][map_name])
    if len(dumps(selected).encode('ascii')) > 64 * 1024:
        raise TacticsError('This round tactics payload exceeds 64 KiB')
    return selected
