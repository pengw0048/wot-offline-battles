# -*- coding: utf-8 -*-
from __future__ import division
"""One-time plan construction from launcher-authored static intent.

Never reads unobserved enemy locations or alters native collision/shot rules.
All geometric checks here are baked navigation checks, not native mesh/arc proof.
"""
import copy
import math
import random

from gui.mods.offline_lan_0922 import bot_tactics as config
from gui.mods.offline_lan_0922 import spg_positions


def graph_view(name, graph):
    if not isinstance(graph, dict) or graph.get('map') != name:
        raise config.TacticsError('Missing matching navigation graph')
    # The engine already clips its copy to the exact arena rectangle. Authoring
    # may open the original bake; _Graph.usable applies these same bounds.
    view = dict(graph)
    view['bounds'] = config.MAPS[name]['bounds']
    return spg_positions._Graph(view, config.MAPS[name]['bounds'])


def validate_route(grid, route):
    previous = None
    for point in route['points']:
        target = grid.closest((point[0], 0, point[1]))
        if target is None:
            return 'waypoint_unusable'
        if previous is not None and target not in grid.distances(previous):
            return 'waypoints_disconnected'
        previous = grid.point(target)
    return None


def route_value(route):
    return {'id': 'user_' + route['id'], 'capacity': route['capacity'], 'risk': 0.5,
            'role_weights': {}, 'class_weights': dict((c, 1.0 if c in route['classes'] else 0.0) for c in config.CLASSES),
            # Wait conditions stay in the canonical tactics document on the
            # server. The manifest keeps its existing three-field geometry.
            'waypoints': tuple(tuple(p[:3]) for p in route['points'])}


def assign_routes(profile, name, graph, states, round_id):
    routes = config.map_settings(profile, name).get('routes', ())
    if not routes:
        return {}, {}
    grid = graph_view(name, graph)
    errors = dict((r['id'], validate_route(grid, r)) for r in routes)
    result, outcomes, usage = {}, {}, {}
    for state in sorted(states, key=lambda s: (s['team'], s.get('slot', 0), s['id'])):
        applicable = [r for r in routes if config.matches(r, state)]
        if not applicable:
            continue
        distances = grid.distances((state['x'], state['y'], state['z']))
        available = []
        for route in applicable:
            if errors[route['id']] or usage.get(route['id'], 0) >= route['capacity']:
                continue
            p = route['points'][0]
            target = grid.closest((p[0], 0, p[1]))
            if target not in distances:
                continue
            # A deterministic weighted draw without global random-state changes.
            seed = '%s:%s:%s:%s' % (config.digest(profile), round_id, state['id'], route['id'])
            rank = -math.log(max(1e-12, random.Random(seed).random())) / route['weight']
            available.append((rank, route['id'], route))
        if not available:
            outcomes[state['id']] = 'no_usable_user_route'
            continue
        route = min(available)[2]
        result[state['id']] = route_value(route)
        usage[route['id']] = usage.get(route['id'], 0) + 1
        outcomes[state['id']] = route['policy']
    return result, outcomes


def _manual_candidates(grid, zone, clearance):
    x, z = zone['point']; radius = zone['radius'] - 2.0
    ox, oz = grid.origin; cell = grid.cell
    lo_x = max(0, int(math.ceil((x-radius-ox)/cell)))
    hi_x = min(grid.width-1, int(math.floor((x+radius-ox)/cell)))
    lo_z = max(0, int(math.ceil((z-radius-oz)/cell)))
    hi_z = min(grid.height-1, int(math.floor((z+radius-oz)/cell)))
    candidates = []
    for row in range(lo_z, hi_z+1):
        for col in range(lo_x, hi_x+1):
            index = row*grid.width+col
            if not grid.usable(index):
                continue
            p = grid.point(index)
            distance = math.hypot(p[0]-x, p[2]-z)
            if distance <= radius and grid.parking_clear(index, clearance):
                candidates.append((distance, index, p))
    return sorted(candidates)


def assign_manual_positions(profile, name, graph, states, mode='regular'):
    zones = config.map_settings(profile, name).get('positions', ()) if mode == 'regular' else ()
    if not zones:
        return {}, {}
    grid = graph_view(name, graph)
    plans, outcomes, reservations, cache = {}, {}, {1: [], 2: []}, {}
    identity = config.digest(profile)
    for state in sorted(states, key=lambda s: (s['team'], s.get('slot', 0), s['id'])):
        if (state.get('profile') or {}).get('class_tag') != 'SPG':
            continue
        choices = [z for z in zones if z['team'] == state['team']]
        if not choices:
            continue
        shape = state['collision_shape']
        clearance = math.hypot(float(shape[0]), float(shape[1])) + 2.0
        distances = grid.distances((state['x'], state['y'], state['z']))
        candidates = []
        for zone in choices:
            key = (zone['id'], clearance)
            if key not in cache:
                cache[key] = _manual_candidates(grid, zone, clearance)
            for centre_distance, index, p in cache[key]:
                if index not in distances:
                    continue
                if any(math.hypot(p[0]-old[0][0], p[2]-old[0][2]) < clearance+old[1]+3
                       for old in reservations[state['team']]):
                    continue
                candidates.append((-zone['priority'], centre_distance, distances[index], zone['id'], p, zone))
        if not candidates:
            outcomes[state['id']] = 'manual_no_reachable_parking_space'
            continue
        unused_a, unused_b, unused_c, unused_id, p, zone = min(candidates)
        angle = math.radians(zone['heading']); bounds = config.MAPS[name]['bounds']
        face = (max(bounds[0], min(bounds[2], p[0]+math.sin(angle)*100)), p[1],
                max(bounds[1], min(bounds[3], p[2]+math.cos(angle)*100)))
        plan = dict(schema=1, catalog=identity, map=name, mode='regular',
                    side='team%d' % state['team'], zone=zone['id'], cell='manual',
                    source='launcher_manual_v1', vehicle=state['vehicle'],
                    point=dict(zip(('x','y','z'),p)), face=dict(zip(('x','y','z'),face)),
                    radius=2.0, clearance=clearance,
                    geometry='baked_checked', fire_validation='runtime_required')
        if config.canonical_manual_plan(plan, profile, name, state['vehicle'], state['team']) is None:
            raise config.TacticsError('Generated manual plan failed its own contract')
        plans[state['id']] = plan
        outcomes[state['id']] = 'manual_selected'
        reservations[state['team']].append((p, clearance))
    return plans, outcomes


def authoring_check(profile, name, graph):
    """Cheap UI evidence only; actual vehicle-sized parking is tested on load."""
    grid = graph_view(name, graph)
    messages = []
    for route in config.map_settings(profile, name).get('routes', ()):
        error = validate_route(grid, route)
        messages.append((route['id'], error or 'baked_route_connected'))
    for zone in config.map_settings(profile, name).get('positions', ()):
        # Generic radius is explicitly not a claim about a particular vehicle.
        valid = bool(_manual_candidates(grid, zone, 6.0))
        messages.append((zone['id'], 'generic_parking_found' if valid else 'no_generic_parking'))
    return messages
