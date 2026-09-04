# -*- coding: utf-8 -*-
"""Routes for the three stock maps whose packed CTF nodes need disambiguation.

The first two float pairs under ``teamBasePositions`` were decoded directly
from the original 0.8.2 packed arena files, then checked against each mmap.
"""


_WEIGHTS = {
	'line': {'brawler': 1.00, 'support': 0.70, 'flanker': 0.35,
		'sniper': 0.22, 'scout': 0.24, 'artillery': 0.00},
	'flank': {'brawler': 0.32, 'support': 0.62, 'flanker': 1.00,
		'sniper': 0.42, 'scout': 0.86, 'artillery': 0.02},
	'fire': {'brawler': 0.16, 'support': 0.86, 'flanker': 0.46,
		'sniper': 1.00, 'scout': 0.62, 'artillery': 0.18},
}


def _route(route_id, kind, capacity, risk, waypoints):
	return {
		'id': route_id, 'capacity': capacity, 'risk': risk,
		'role_weights': dict(_WEIGHTS[kind]), 'waypoints': tuple(waypoints),
	}


def _map(name, bounds, base1, base2, routes):
	team1 = []
	team2 = []
	for route in routes:
		forward = dict(route)
		forward['role_weights'] = dict(route['role_weights'])
		points = list(route['waypoints'])
		points[0] = (base1[0], base1[1], 0)
		points[-1] = (base2[0], base2[1], 0)
		forward['waypoints'] = tuple(points)
		team1.append(forward)
		reverse = dict(forward)
		reverse['role_weights'] = dict(forward['role_weights'])
		reverse['waypoints'] = tuple(reversed(forward['waypoints']))
		team2.append(reverse)
	return {
		'name': name, 'bounds': bounds, 'bases': {1: base1, 2: base2},
		'routes': {1: tuple(team1), 2: tuple(team2)},
	}


MALINOVKA = _map('02_malinovka', (-500.0, -500.0, 500.0, 500.0),
	(75.60, -391.92), (-372.70, 108.12), (
		_route('west_lake_road', 'line', 5, 0.62,
			((0, 0, 0), (-80, -370, 0), (-220, -290, 1), (-340, -175, 1), (-410, -35, 1), (0, 0, 0))),
		_route('central_field', 'fire', 4, 0.70,
			((0, 0, 0), (25, -285, 0), (-35, -175, 1), (-110, -65, 1), (-215, 45, 1), (0, 0, 0))),
		_route('east_hill_loop', 'flank', 5, 0.78,
			((0, 0, 0), (230, -320, 0), (395, -180, 1), (410, 20, 1), (290, 235, 1), (35, 315, 0), (0, 0, 0))),
	))


SIEGFRIED_LINE = _map('14_siegfried_line', (-500.0, -500.0, 500.0, 500.0),
	(255.80, -439.83), (283.85, 434.60), (
		_route('west_field', 'flank', 5, 0.70,
			((0, 0, 0), (40, -365, 0), (-150, -260, 1), (-290, -85, 1), (-265, 125, 1), (-80, 315, 0), (0, 0, 0))),
		_route('fortification_line', 'fire', 4, 0.66,
			((0, 0, 0), (145, -345, 0), (65, -220, 1), (40, -55, 1), (55, 125, 1), (145, 300, 0), (0, 0, 0))),
		_route('east_city', 'line', 6, 0.64,
			((0, 0, 0), (330, -330, 0), (315, -205, 1), (300, -65, 1), (315, 90, 1), (330, 265, 0), (0, 0, 0))),
	))


AIRFIELD = _map('31_airfield', (-500.0, -500.0, 500.0, 500.0),
	(360.65, -154.44), (-324.05, -176.18), (
		_route('north_runway', 'flank', 5, 0.72,
			((0, 0, 0), (280, 20, 0), (170, 210, 1), (15, 325, 1), (-150, 260, 1), (-270, 25, 0), (0, 0, 0))),
		_route('central_ridges', 'fire', 4, 0.68,
			((0, 0, 0), (245, -95, 0), (135, 15, 1), (15, 70, 1), (-105, 25, 1), (-225, -85, 0), (0, 0, 0))),
		_route('south_towns', 'line', 5, 0.65,
			((0, 0, 0), (250, -235, 0), (145, -285, 1), (15, -305, 1), (-115, -285, 1), (-225, -235, 0), (0, 0, 0))),
	))


TACTICAL_MAPS_EXTRA = {
	'02_malinovka': MALINOVKA,
	'14_siegfried_line': SIEGFRIED_LINE,
	'31_airfield': AIRFIELD,
}
