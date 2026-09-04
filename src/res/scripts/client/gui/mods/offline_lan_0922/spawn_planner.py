from __future__ import print_function

"""Strict consumer for the per-map #1513 standard-battle spawn contract."""

import math


def _finite(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('%s is not numeric' % label)
    if result != result or abs(result) == float('inf'):
        raise ValueError('%s is not finite' % label)
    return result


class SpawnPlanner(object):

    SLOT_COUNT = 15

    def __init__(self, arena_type=None, fallback=None,
                 navigation_graph=None):
        unused_arena_type = arena_type
        unused_fallback = fallback
        if not isinstance(navigation_graph, dict):
            raise ValueError(
                'validated navigation graph is required for spawn planning')
        self.navigation_graph = navigation_graph
        self.map_name = str(navigation_graph.get('map') or '<unknown>')
        formations = navigation_graph.get('spawn_formations')
        if not isinstance(formations, dict):
            raise ValueError(
                'spawn formations are missing for map %s' % self.map_name)
        self.formations = {}
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
            self.formations[team] = tuple(values)
        self._validate_separation()
        objective_bases = navigation_graph.get('objective_bases')
        if (not isinstance(objective_bases, (list, tuple)) or
                len(objective_bases) != 2):
            raise ValueError(
                'objective bases are missing for map %s' % self.map_name)
        self.bases = {}
        for team, point in enumerate(objective_bases, 1):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(
                    'map %s team %d objective base is invalid' %
                    (self.map_name, team))
            self.bases[team] = ((
                _finite(point[0], 'map %s team %d objective base x' %
                        (self.map_name, team)),
                _finite(point[1], 'map %s team %d objective base z' %
                        (self.map_name, team))),)

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
