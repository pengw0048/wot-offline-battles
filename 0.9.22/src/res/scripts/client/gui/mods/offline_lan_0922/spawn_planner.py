from __future__ import print_function

"""Strict consumer for the per-map #1513 standard-battle spawn contract."""

import math


TEAM_MAPPING_AMBIGUITY_METRES = 1.0
MAX_LEGACY_TACTICAL_ALIGNMENT_DIAGONAL_RATIO = 0.60


def _finite(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('%s is not numeric' % label)
    if result != result or abs(result) == float('inf'):
        raise ValueError('%s is not finite' % label)
    return result


def _team_value(values, team):
    if not isinstance(values, dict):
        return None
    return values.get(team, values.get(str(team)))


def _point(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('%s is invalid' % label)
    return (_finite(value[0], label), _finite(value[1], label))


def _distance(left, right):
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _server_team_mapping(fallback, navigation_graph, objective_bases):
    if fallback is None:
        return {1: 1, 2: 2}
    tactical_bases = fallback.get('bases') if isinstance(fallback, dict) \
        else None
    homes = {}
    for team in (1, 2):
        homes[team] = _point(
            _team_value(tactical_bases, team),
            'map tactical team %d base' % team)
    raw_bounds = navigation_graph.get('bounds')
    if (not isinstance(raw_bounds, (list, tuple)) or
            len(raw_bounds) != 4):
        raise ValueError('navigation bounds are invalid for team mapping')
    bounds = tuple(_finite(
        value, 'navigation bounds') for value in raw_bounds)
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError('navigation bounds are invalid for team mapping')

    def inside(point):
        return (bounds[0] <= point[0] <= bounds[2] and
                bounds[1] <= point[1] <= bounds[3])

    raw_anchors = navigation_graph.get('spawn_anchors')
    if not isinstance(raw_anchors, (list, tuple)) or len(raw_anchors) != 2:
        raise ValueError('spawn anchors are missing for team mapping')
    anchors = {
        1: _point(raw_anchors[0], 'navigation team 1 spawn anchor'),
        2: _point(raw_anchors[1], 'navigation team 2 spawn anchor'),
    }
    if (any(not inside(point) for point in homes.values()) or
            any(not inside(point) for point in anchors.values()) or
            any(not inside(point) for point in objective_bases.values())):
        raise ValueError('team-mapping candidate is outside navigation bounds')
    candidates = ((1, 2), (2, 1))
    scores = []
    for graph_teams in candidates:
        score = sum(_distance(
            homes[server_team], objective_bases[graph_teams[server_team - 1]])
                    for server_team in (1, 2))
        scores.append((score, graph_teams))
    scores.sort(key=lambda item: item[0])
    if abs(scores[1][0] - scores[0][0]) <= TEAM_MAPPING_AMBIGUITY_METRES:
        raise ValueError('navigation team mapping is ambiguous')
    mapping = dict((server_team, scores[0][1][server_team - 1])
                   for server_team in (1, 2))
    alignment_limit = math.hypot(
        bounds[2] - bounds[0], bounds[3] - bounds[1]) * \
        MAX_LEGACY_TACTICAL_ALIGNMENT_DIAGONAL_RATIO
    for server_team in (1, 2):
        graph_team = mapping[server_team]
        objective = objective_bases[graph_team]
        anchor = anchors[graph_team]
        if (_distance(homes[server_team], objective) > alignment_limit or
                _distance(homes[server_team], anchor) > alignment_limit or
                _distance(objective, anchor) > alignment_limit):
            raise ValueError(
                'legacy tactical catalog alignment exceeds map-relative '
                'tolerance for team %d' % server_team)
    return mapping


class SpawnPlanner(object):

    SLOT_COUNT = 15

    def __init__(self, arena_type=None, fallback=None,
                 navigation_graph=None):
        unused_arena_type = arena_type
        if not isinstance(navigation_graph, dict):
            raise ValueError(
                'validated navigation graph is required for spawn planning')
        self.navigation_graph = navigation_graph
        self.map_name = str(navigation_graph.get('map') or '<unknown>')
        formations = navigation_graph.get('spawn_formations')
        if not isinstance(formations, dict):
            raise ValueError(
                'spawn formations are missing for map %s' % self.map_name)
        graph_formations = {}
        for team in (1, 2):
            raw = formations.get(str(team), formations.get(team))
            if not isinstance(raw, (list, tuple)) or len(raw) != self.SLOT_COUNT:
                raise ValueError(
                    'map %s team %d requires exactly %d spawn slots' %
                    (self.map_name, team, self.SLOT_COUNT))
            values = []
            for slot, point in enumerate(raw):
                if not isinstance(point, (list, tuple)) or len(point) != 4:
                    raise ValueError(
                        'map %s team %d spawn slot %d is invalid' %
                        (self.map_name, team, slot))
                values.append(tuple(_finite(
                    value, 'map %s team %d spawn slot %d' %
                    (self.map_name, team, slot)) for value in point))
            graph_formations[team] = tuple(values)
        objective_bases = navigation_graph.get('objective_bases')
        if (not isinstance(objective_bases, (list, tuple)) or
                len(objective_bases) != 2):
            raise ValueError(
                'objective bases are missing for map %s' % self.map_name)
        graph_bases = {}
        for team, point in enumerate(objective_bases, 1):
            graph_bases[team] = _point(
                point, 'map %s team %d objective base' %
                (self.map_name, team))
        self.graph_team_by_server_team = _server_team_mapping(
            fallback, navigation_graph, graph_bases)
        self.formations = dict(
            (server_team, graph_formations[
                self.graph_team_by_server_team[server_team]])
            for server_team in (1, 2))
        self.bases = dict(
            (server_team, (graph_bases[
                self.graph_team_by_server_team[server_team]],))
            for server_team in (1, 2))
        self._validate_separation()

    def _validate_separation(self):
        all_slots = []
        for team in (1, 2):
            for slot, point in enumerate(self.formations[team]):
                for other_team, other_slot, other in all_slots:
                    distance = math.hypot(
                        point[0] - other[0], point[2] - other[2])
                    if distance < 9.0:
                        raise ValueError(
                            'map %s spawn overlap: team %d slot %d and '
                            'team %d slot %d are %.2f m apart' %
                            (self.map_name, team, slot, other_team,
                             other_slot, distance))
                all_slots.append((team, slot, point))

    def pose(self, team, slot):
        try:
            team = int(team)
            slot = int(slot)
        except (TypeError, ValueError):
            raise ValueError(
                'map %s spawn team/slot is not integral' % self.map_name)
        if team not in (1, 2):
            raise ValueError(
                'map %s has no spawn team %d' % (self.map_name, team))
        if slot < 0 or slot >= self.SLOT_COUNT:
            raise ValueError(
                'map %s team %d has no spawn slot %d' %
                (self.map_name, team, slot))
        x, y, z, yaw = self.formations[team][slot]
        return ((x, y, z), yaw)
