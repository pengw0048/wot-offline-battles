from __future__ import division

"""Community-area initial SPG deployment; engine-free and Python 2.7 safe.

The catalog contains recommendations, NOT collision or firing permissions.
Resolve only existing nodes of the loaded #1513 graph inside a sourced area.
The original navigator, movement collision and exact shot proof stay in charge.
No enemy coordinates, ammunition changes, new native methods or wall bypasses.
"""

import heapq
import math

from gui.mods.offline_lan_0922.spg_position_data import CATALOG

ROWS = 'ABCDEFGHJK'
COLUMNS = '1234567890'
# Local parking/reservation policy, not recovered Wargaming constants.
PARKING_RADIUS = 2.0
CLEARANCE_MARGIN = 0.5
RESERVATION_GAP = 6.0
MAX_PARKING_SLOPE = math.tan(math.radians(12.0))
MAX_ALTERNATIVES_PER_CELL = 8


def _finite(value):
    return value == value and abs(value) != float('inf')


def _number(value):
    if isinstance(value, bool):
        raise ValueError('boolean is not a coordinate')
    result = float(value)
    if not _finite(result):
        raise ValueError('non-finite coordinate')
    return result


def cell_bounds(cell, bounds):
    """Map A1..K0 onto the exact arena boundingBox (not assumed +/-500)."""
    if not isinstance(cell, str) and not isinstance(cell, type(u'')):
        raise ValueError('invalid minimap cell')
    if len(cell) != 2 or cell[0] not in ROWS or cell[1] not in COLUMNS:
        raise ValueError('invalid minimap cell: %r' % (cell,))
    if len(bounds) != 4:
        raise ValueError('invalid arena bounds')
    left, bottom, right, top = [_number(value) for value in bounds]
    if not (left < right and bottom < top):
        raise ValueError('empty arena bounds')
    dx, dz = (right - left) / 10.0, (top - bottom) / 10.0
    column, row = COLUMNS.index(cell[1]), ROWS.index(cell[0])
    return (left + column * dx, top - (row + 1) * dz,
            left + (column + 1) * dx, top - row * dz)


def point_in_cell(point, cell, bounds, margin=0.0):
    left, bottom, right, top = cell_bounds(cell, bounds)
    return (left + margin <= point[0] <= right - margin and
            bottom + margin <= point[2] <= top - margin)


def validate_catalog(data):
    """Validate authoring input before generating the bundled Python data."""
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ValueError('unknown SPG catalog schema')
    if data.get('game_version') != '0.9.22.0.1-cn-1513':
        raise ValueError('SPG catalog is not for #1513')
    sources, maps = data.get('sources'), data.get('maps')
    if not isinstance(sources, dict) or not isinstance(maps, dict):
        raise ValueError('SPG catalog is incomplete')
    if not isinstance(data.get('revision'), (str, type(u''))):
        raise ValueError('SPG catalog has no revision')
    for key, source in sources.items():
        if not key or not isinstance(source, dict):
            raise ValueError('SPG source is malformed')
        if not str(source.get('url', '')).startswith('https://'):
            raise ValueError('SPG source requires an attributed URL')
    for map_name, entry in maps.items():
        if not isinstance(entry, dict) or entry.get('mode') != 'regular':
            raise ValueError('unsupported SPG map mode: %s' % map_name)
        sides = entry.get('spawn_sides')
        if (not isinstance(sides, dict) or set(sides) != set(('1', '2')) or
                set(sides.values()) not in (set(('north', 'south')), set(('east', 'west')))):
            raise ValueError('SPG map needs the native spawn-side mapping')
        bounds = entry.get('bounds', ())
        cell_bounds('A1', bounds)
        ids = set()
        zones = entry.get('zones')
        if not isinstance(zones, list) or not zones or len(zones) > 64:
            raise ValueError('SPG map requires a bounded zone list')
        for zone in zones:
            if not isinstance(zone, dict) or not zone.get('id'):
                raise ValueError('invalid SPG zone')
            if zone['id'] in ids:
                raise ValueError('duplicate SPG zone')
            ids.add(zone['id'])
            if zone.get('side') not in ('north', 'south', 'east', 'west'):
                raise ValueError('invalid SPG spawn side')
            if zone.get('source') not in sources:
                raise ValueError('unknown SPG source')
            cells = zone.get('cells')
            if not isinstance(cells, list) or not 1 <= len(cells) <= 20:
                raise ValueError('SPG zone needs explicit cells')
            if len(set(cells)) != len(cells):
                raise ValueError('duplicate SPG cell')
            for cell in cells:
                cell_bounds(cell, bounds)
            if zone.get('face_cell') is not None:
                cell_bounds(zone['face_cell'], bounds)
            support = zone.get('requires_support')
            if support is not None:
                if (not isinstance(support, dict) or
                        set(support) != set(('columns', 'minimum')) or
                        not isinstance(support['columns'], list) or
                        not support['columns'] or
                        any(c not in COLUMNS for c in support['columns']) or
                        isinstance(support['minimum'], bool) or
                        support['minimum'] not in range(1, 15)):
                    raise ValueError('invalid SPG friendly support condition')
            priority = zone.get('priority', 0)
            if isinstance(priority, bool) or priority not in range(10):
                raise ValueError('invalid SPG priority')
    return True


def side_for_team(team, bases):
    """Derive side from actual map bases: team 1 is NOT always north."""
    if isinstance(team, bool) or team not in (1, 2) or not isinstance(bases, (list, tuple)) or len(bases) != 2:
        return None
    try:
        own, enemy = bases[team - 1], bases[2 - team]
        dx, dz = _number(own[0]) - _number(enemy[0]), _number(own[1]) - _number(enemy[1])
    except (IndexError, TypeError, ValueError, OverflowError):
        return None
    if abs(dx) + abs(dz) < 1.0:
        return None
    if abs(dz) >= abs(dx):
        return 'north' if dz > 0.0 else 'south'
    return 'east' if dx > 0.0 else 'west'


def canonical_plan(raw, map_name=None, vehicle=None, catalog=None, team=None, tactics=None):
    """Contain an invalid optional plan to that SPG, never abort the battle.

    A plan is a round-constant macro destination, not a proof of clear fire.
    The receiving server can validate provenance/area/shape but cannot certify
    native geometry; the authority's original navigator rechecks movement.
    """
    if isinstance(raw, dict) and raw.get('source') == 'launcher_manual_v1':
        from gui.mods.offline_lan_0922 import bot_tactics
        return bot_tactics.canonical_manual_plan(raw, tactics or bot_tactics.empty(), map_name, vehicle, team)
    data = CATALOG if catalog is None else catalog
    fields = set(('schema', 'catalog', 'map', 'mode', 'side', 'zone', 'cell',
                  'source', 'vehicle', 'point', 'face', 'radius', 'clearance',
                  'geometry', 'fire_validation'))
    if not isinstance(raw, dict) or set(raw) != fields:
        return None
    try:
        if (isinstance(raw['schema'], bool) or raw['schema'] != 1 or raw['catalog'] != data['revision'] or
                raw['mode'] != 'regular' or raw['geometry'] != 'baked_checked' or
                raw['fire_validation'] != 'runtime_required'):
            return None
        if map_name is not None and raw['map'] != map_name:
            return None
        if vehicle is not None and raw['vehicle'] != vehicle:
            return None
        if not isinstance(raw['vehicle'], (str, type(u''))) or not 1 <= len(raw['vehicle']) <= 80:
            return None
        entry = data['maps'].get(raw['map'])
        if entry is None:
            return None
        if team is not None and (isinstance(team, bool) or team not in (1, 2) or
                                 entry['spawn_sides'].get(str(team)) != raw['side']):
            return None
        zone = next((z for z in entry['zones'] if z['id'] == raw['zone']), None)
        if (zone is None or zone['source'] != raw['source'] or
                zone['side'] != raw['side'] or raw['cell'] not in zone['cells']):
            return None
        point, face = [], []
        for name, destination in (('point', point), ('face', face)):
            if not isinstance(raw[name], dict) or set(raw[name]) != set(('x', 'y', 'z')):
                return None
            for axis in ('x', 'y', 'z'):
                destination.append(_number(raw[name][axis]))
            left, bottom, right, top = entry['bounds']
            if not (left <= destination[0] <= right and bottom <= destination[2] <= top and
                    -1000.0 <= destination[1] <= 1000.0):
                return None
        radius, clearance = _number(raw['radius']), _number(raw['clearance'])
        if radius != PARKING_RADIUS or not radius <= clearance <= 50.0:
            return None
        if not point_in_cell(point, raw['cell'], entry['bounds'], radius):
            return None
        result = dict(raw)
        result['point'] = dict(zip(('x', 'y', 'z'), point))
        result['face'] = dict(zip(('x', 'y', 'z'), face))
        result['radius'], result['clearance'] = radius, clearance
        return result
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return None


def plan_identity(plan):
    if plan is None:
        return None
    return (plan['catalog'], plan['map'], plan['zone'], plan['cell'],
            tuple(plan['point'][axis] for axis in ('x', 'y', 'z')))


class _Graph(object):
    """Bounded read-only graph view used once at initial deployment."""

    def __init__(self, graph, bounds):
        if not isinstance(graph, dict) or graph.get('game_version') != CATALOG['game_version']:
            raise ValueError('navigation graph is not the pinned #1513 graph')
        self.width, self.height = int(graph['width']), int(graph['height'])
        self.size = self.width * self.height
        self.cell = _number(graph['cell_size'])
        self.origin = tuple(_number(v) for v in graph['origin'])
        if not (0 < self.width <= 4096 and 0 < self.height <= 4096 and
                0 < self.size <= 1000000 and 0 < self.cell <= 32 and len(self.origin) == 2):
            raise ValueError('invalid SPG navigation grid')
        self.heights, self.links, self.hazards = [graph[k] for k in ('heights_mm', 'links', 'hazards')]
        if any(len(v) != self.size for v in (self.heights, self.links, self.hazards)):
            raise ValueError('incomplete SPG navigation grid')
        self.directions = tuple(tuple(int(v) for v in d) for d in graph['directions'])
        if (len(self.directions) != 8 or len(set(self.directions)) != 8 or
                any(len(d) != 2 or d == (0, 0) or max(abs(v) for v in d) != 1
                    for d in self.directions)):
            raise ValueError('invalid SPG graph edges')
        if tuple(_number(v) for v in graph['bounds']) != tuple(bounds):
            raise ValueError('catalog and map bounds differ')
        self.bounds = tuple(bounds)
        self._clearance = {}

    def point(self, index):
        row, column = divmod(index, self.width)
        return (self.origin[0] + column * self.cell,
                _number(self.heights[index]) / 1000.0,
                self.origin[1] + row * self.cell)

    def usable(self, index):
        if not 0 <= index < self.size or self.heights[index] is None:
            return False
        # Water/edge/shallow-water/runtime shore bits are excluded from parking
        # and these initial paths. Other navigation modes keep their own policy.
        if int(self.hazards[index]) & 15:
            return False
        x, unused_y, z = self.point(index)
        return self.bounds[0] <= x <= self.bounds[2] and self.bounds[1] <= z <= self.bounds[3]

    def closest(self, point):
        # Start in the actor's own grid square. A nearest-free search across
        # several squares could jump a wall and falsely prove connectivity.
        x, z = _number(point[0]), _number(point[2])
        if not (self.bounds[0] <= x <= self.bounds[2] and self.bounds[1] <= z <= self.bounds[3]):
            return None
        col = int(round((x - self.origin[0]) / self.cell))
        row = int(round((z - self.origin[1]) / self.cell))
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        index = row * self.width + col
        return index if self.usable(index) else None

    def distances(self, position):
        start = self.closest(position)
        if start is None:
            return {}
        distance = {start: 0.0}
        todo = [(0.0, start)]
        while todo:
            length, index = heapq.heappop(todo)
            if length != distance.get(index):
                continue
            row, col = divmod(index, self.width)
            for bit, (dx, dz) in enumerate(self.directions):
                if not int(self.links[index]) & (1 << bit):
                    continue
                nx, nz = col + dx, row + dz
                if not (0 <= nx < self.width and 0 <= nz < self.height):
                    continue
                following = nz * self.width + nx
                if not self.usable(following):
                    continue
                cost = length + self.cell * math.hypot(dx, dz)
                if cost < distance.get(following, float('inf')):
                    distance[following] = cost
                    heapq.heappush(todo, (cost, following))
        return distance

    def parking_clear(self, index, radius):
        key = (index, round(radius, 5))
        if key in self._clearance:
            return self._clearance[key]
        result = self._parking_clear(index, radius)
        self._clearance[key] = result
        return result

    def _parking_clear(self, index, radius):
        if not self.usable(index):
            return False
        row, col = divmod(index, self.width)
        x, y, z = self.point(index)
        left, bottom, right, top = self.bounds
        if not (left + radius <= x <= right - radius and bottom + radius <= z <= top - radius):
            return False
        extent = int(math.ceil(radius / self.cell + 0.5))
        for dz in range(-extent, extent + 1):
            for dx in range(-extent, extent + 1):
                # Include every grid square touched by the full turn/parking
                # disk, not just centres lying inside it.
                near_x = max(0.0, abs(dx) * self.cell - self.cell * 0.5)
                near_z = max(0.0, abs(dz) * self.cell - self.cell * 0.5)
                if near_x * near_x + near_z * near_z > radius * radius:
                    continue
                cx, cz = col + dx, row + dz
                if not (0 <= cx < self.width and 0 <= cz < self.height):
                    return False
                other = cz * self.width + cx
                if not self.usable(other):
                    return False
                horizontal = self.cell * math.hypot(dx, dz)
                if horizontal and abs(self.point(other)[1] - y) > horizontal * MAX_PARKING_SLOPE:
                    return False
        return True

    def candidates(self, entry, side, radius):
        result = []
        for zone in entry['zones']:
            if zone['side'] != side:
                continue
            for cell in zone['cells']:
                left, bottom, right, top = cell_bounds(cell, entry['bounds'])
                centre_x, centre_z = (left + right) * 0.5, (bottom + top) * 0.5
                min_col = max(0, int(math.ceil((left + radius - self.origin[0]) / self.cell)))
                max_col = min(self.width - 1, int(math.floor((right - radius - self.origin[0]) / self.cell)))
                min_row = max(0, int(math.ceil((bottom + radius - self.origin[1]) / self.cell)))
                max_row = min(self.height - 1, int(math.floor((top - radius - self.origin[1]) / self.cell)))
                ordered = []
                for row in range(min_row, max_row + 1):
                    for col in range(min_col, max_col + 1):
                        index = row * self.width + col
                        if self.usable(index):
                            point = self.point(index)
                            ordered.append(((point[0] - centre_x) ** 2 + (point[2] - centre_z) ** 2, index))
                selected = []
                for unused_score, index in sorted(ordered):
                    point = self.point(index)
                    if any(math.hypot(point[0] - old[0], point[2] - old[2]) < radius * 2 + RESERVATION_GAP
                           for old in selected):
                        continue
                    if not self.parking_clear(index, radius):
                        continue
                    selected.append(point)
                    result.append((zone, cell, index, point))
                    if len(selected) >= MAX_ALTERNATIVES_PER_CELL:
                        break
        return result


def _has_initial_support(zone, team, states, bounds):
    """Interpret an explicit source caveat using friendly planned routes only.

    Three projected non-SPG routes is local policy for the guide's qualitative
    strong-field-group requirement. It is not a promise of future protection.
    """
    required = zone.get('requires_support')
    if not required:
        return True
    count = 0
    for state in states:
        if state.get('team') != team or (state.get('profile') or {}).get('class_tag') == 'SPG':
            continue
        points = (state.get('route') or {}).get('waypoints') or ()
        supported = False
        for raw in points[1:]:
            try:
                x = _number(raw.get('x')) if isinstance(raw, dict) else _number(raw[0])
            except (IndexError, TypeError, ValueError, OverflowError):
                continue
            for column in required['columns']:
                left, unused_bottom, right, unused_top = cell_bounds('A' + column, bounds)
                if left <= x <= right:
                    supported = True
                    break
            if supported:
                break
        if supported:
            count += 1
    return count >= required['minimum']


def assign_initial_positions(map_name, graph, states, mode='regular', catalog=None):
    """Return bot-id -> plan and typed per-SPG outcomes without mutating input.

    This runs once before the round manifest is published. It selects a whole
    team's distinct destinations using only sourced cells and the static map.
    Native full-arc proof remains mandatory when actually firing.
    """
    data = CATALOG if catalog is None else catalog
    plans, outcomes = {}, {}
    states = list(states)
    artillery = sorted((state for state in states if
                        (state.get('profile') or {}).get('class_tag') == 'SPG'),
                       key=lambda state: (state.get('team', 0), state['id']))
    if not artillery:
        return plans, outcomes
    entry = data['maps'].get(map_name)
    if mode != 'regular' or entry is None:
        reason = 'unsupported_mode' if mode != 'regular' else 'source_not_catalogued'
        return plans, dict((state['id'], reason) for state in artillery)
    try:
        validate_catalog(data)
        if not isinstance(graph, dict) or graph.get('map') != map_name:
            raise ValueError('SPG graph map mismatch')
        grid = _Graph(graph, entry['bounds'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return plans, dict((state['id'], 'incompatible_graph') for state in artillery)
    reservations = {1: [], 2: []}
    used_zones = {1: {}, 2: {}}
    candidate_cache = {}
    for state in artillery:
        actor, team = state['id'], state.get('team')
        if team in reservations and len(reservations[team]) >= 3:
            outcomes[actor] = 'outside_three_spg_initial_capacity'
            continue
        side = side_for_team(team, graph.get('bases'))
        if side is None or entry['spawn_sides'].get(str(team)) != side:
            outcomes[actor] = 'unknown_spawn_side'
            continue
        try:
            shape = state['collision_shape']
            hw, hl = _number(shape[0]), _number(shape[1])
            if not (0.3 <= hw <= 20 and 0.5 <= hl <= 30):
                raise ValueError('invalid body footprint')
            radius = math.hypot(hw, hl) + PARKING_RADIUS + CLEARANCE_MARGIN
            origin = tuple(_number(state[axis]) for axis in ('x', 'y', 'z'))
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            outcomes[actor] = 'vehicle_footprint_unavailable'
            continue
        key = (side, round(radius, 5))
        if key not in candidate_cache:
            candidate_cache[key] = grid.candidates(entry, side, radius)
        candidates = candidate_cache[key]
        if not candidates:
            outcomes[actor] = 'no_safe_sourced_cell'
            continue
        reachable = grid.distances(origin)
        choices = []
        for zone, cell, index, point in candidates:
            if not _has_initial_support(zone, team, states, entry['bounds']):
                continue
            if index not in reachable:
                continue
            if any(math.hypot(point[0] - old[0][0], point[2] - old[0][2]) < radius + old[1] + RESERVATION_GAP
                   for old in reservations[team]):
                continue
            # Prefer separate support regions, then the author's relative
            # caveat/priority, then the shortest dry graph path. All are local
            # deployment policy; no hidden enemy positions are consulted.
            score = (used_zones[team].get(zone['id'], 0), zone.get('priority', 0),
                     reachable[index], zone['id'], cell, index)
            choices.append((score, zone, cell, point))
        if not choices:
            outcomes[actor] = 'no_reachable_unreserved_sourced_cell'
            continue
        unused_score, zone, cell, point = min(choices, key=lambda row: row[0])
        enemy = graph['bases'][2 - team]
        face = (float(enemy[0]), point[1], float(enemy[1]))
        if zone.get('face_cell'):
            left, bottom, right, top = cell_bounds(zone['face_cell'], entry['bounds'])
            face = ((left + right) * 0.5, point[1], (bottom + top) * 0.5)
        # Facing is a public map sector, NOT a target or permission to fire.
        plan = {'schema': 1, 'catalog': data['revision'], 'map': map_name,
                'mode': 'regular', 'side': side, 'zone': zone['id'], 'cell': cell,
                'source': zone['source'], 'vehicle': state['vehicle'],
                'point': dict(zip(('x', 'y', 'z'), point)),
                'face': dict(zip(('x', 'y', 'z'), face)),
                'radius': PARKING_RADIUS, 'clearance': radius,
                'geometry': 'baked_checked', 'fire_validation': 'runtime_required'}
        plan = canonical_plan(plan, map_name, state['vehicle'], data, team)
        if plan is None:
            outcomes[actor] = 'plan_validation_failed'
            continue
        reservations[team].append((point, radius))
        used_zones[team][zone['id']] = used_zones[team].get(zone['id'], 0) + 1
        plans[actor], outcomes[actor] = plan, 'assigned'
    return plans, outcomes
