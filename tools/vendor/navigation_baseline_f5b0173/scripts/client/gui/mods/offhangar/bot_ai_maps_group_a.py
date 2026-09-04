# -*- coding: utf-8 -*-
"""Conservative macro corridors traced from the original 0.8.2 minimaps.

Each route is intentionally only a chain of road, field, or open-valley
anchors.  The local multi-ray driver still owns collision avoidance.
"""


_WEIGHTS = {
	'brawl': {'brawler': 1.00, 'support': 0.60, 'flanker': 0.30,
		'sniper': 0.12, 'scout': 0.18, 'artillery': 0.00},
	'flank': {'brawler': 0.26, 'support': 0.62, 'flanker': 1.00,
		'sniper': 0.40, 'scout': 0.82, 'artillery': 0.00},
	'fire': {'brawler': 0.16, 'support': 0.82, 'flanker': 0.52,
		'sniper': 1.00, 'scout': 0.64, 'artillery': 0.12},
}


def _route(route_id, kind, capacity, risk, waypoints):
	"""Make a route with independent weights for the director's callers."""
	return {
		'id': route_id,
		'capacity': capacity,
		'risk': risk,
		'hold': True,
		'role_weights': dict(_WEIGHTS[kind]),
		'waypoints': tuple(waypoints),
	}


def _reverse(route):
	result = dict(route)
	result['role_weights'] = dict(route['role_weights'])
	result['waypoints'] = tuple(reversed(route['waypoints']))
	return result


def _map(name, bounds, base1, base2, routes):
	team1 = tuple(routes)
	return {
		'name': name,
		'bounds': bounds,
		'bases': {1: base1, 2: base2},
		'routes': {1: team1, 2: tuple([_reverse(route) for route in team1])},
	}


KARELIA = _map('01_karelia', (-500.0, -500.0, 500.0, 500.0),
	(397.6, 402.6), (-401.3, -399.9), (
		_route('west_ridge', 'brawl', 5, 0.62, ((-390, -380, 0), (-330, -280, 0), (-260, -155, 1), (-190, -30, 1), (-125, 115, 1), (-35, 255, 0))),
		_route('middle_road', 'fire', 4, 0.56, ((-370, -365, 0), (-250, -285, 0), (-105, -185, 1), (35, -55, 1), (150, 95, 1), (270, 275, 0))),
		_route('east_shelf', 'flank', 4, 0.74, ((-355, -385, 0), (-225, -360, 0), (-75, -295, 1), (95, -180, 1), (230, -15, 1), (345, 180, 0))),
	))

CAMPANIA = _map('03_campania', (-300.0, -300.0, 300.0, 300.0),
	(-0.1, -209.3), (0.0, 209.4), (
		_route('west_valley', 'brawl', 5, 0.60, ((-5, -205, 0), (-85, -165, 0), (-145, -75, 1), (-155, 25, 1), (-120, 115, 1), (-45, 190, 0))),
		_route('central_village', 'fire', 4, 0.58, ((0, -205, 0), (15, -125, 0), (20, -45, 1), (15, 40, 1), (10, 120, 1), (0, 195, 0))),
		_route('east_hill', 'flank', 4, 0.76, ((15, -205, 0), (95, -165, 0), (155, -85, 1), (165, 10, 1), (130, 105, 1), (55, 185, 0))),
	))

PROHOROVKA = _map('05_prohorovka', (-500.0, -500.0, 500.0, 500.0),
	(-125.2, 448.5), (51.6, -447.0), (
		_route('west_ridge', 'flank', 4, 0.78, ((-125, 435, 0), (-225, 345, 0), (-300, 215, 1), (-310, 60, 1), (-275, -105, 1), (-185, -300, 0))),
		_route('central_field', 'fire', 5, 0.66, ((-110, 430, 0), (-80, 305, 0), (-45, 165, 1), (-15, 15, 1), (10, -145, 1), (35, -325, 0))),
		_route('rail_line', 'brawl', 5, 0.70, ((-90, 425, 0), (90, 345, 0), (235, 225, 1), (310, 55, 1), (300, -125, 1), (175, -300, 0))),
	))

ENSK = _map('06_ensk', (-300.0, -300.0, 300.0, 300.0),
	(20.3, 249.7), (19.1, -248.7), (
		_route('west_city', 'brawl', 6, 0.64, ((20, 240, 0), (-75, 205, 0), (-145, 125, 1), (-145, 25, 1), (-125, -85, 1), (-65, -200, 0))),
		_route('rail_yard', 'fire', 4, 0.58, ((15, 240, 0), (75, 185, 0), (125, 95, 1), (135, -10, 1), (115, -110, 1), (65, -205, 0))),
		_route('east_field', 'flank', 4, 0.73, ((25, 240, 0), (155, 210, 0), (225, 115, 1), (230, 10, 1), (210, -100, 1), (130, -205, 0))),
	))

LAKEVILLE = _map('07_lakeville', (-400.0, -400.0, 400.0, 400.0),
	(-169.5, 319.4), (-169.5, -319.0), (
		# Lakeville's world axes line up with the minimap.  The previous west
		# route crossed the mountain and the previous town route put three
		# anchors in the lake, leaving the terrain navigator with an impossible
		# goal.  These are sparse corridor gates: live A* still chooses the exact
		# road around rocks, buildings and traffic between them.
		_route('west_valley', 'brawl', 5, 0.63, ((-169, 319, 0), (-314, 298, 0), (-330, 189, 1), (-331, 40, 1), (-315, -101, 1), (-278, -211, 0), (-225, -273, 0))),
		_route('lake_road', 'fire', 4, 0.62, ((-169, 319, 0), (-110, 268, 0), (-76, 189, 0), (-98, 74, 1), (-90, -98, 1), (-102, -211, 0), (-165, -294, 0))),
		_route('east_town', 'flank', 5, 0.74, ((-169, 319, 0), (-9, 325, 0), (164, 306, 0), (289, 267, 0), (322, 173, 1), (314, 40, 1), (284, -93, 1), (218, -187, 0), (70, -265, 0), (-79, -297, 0))),
	))

RUINBERG = _map('08_ruinberg', (-400.0, -400.0, 400.0, 400.0),
	(-66.4, 306.1), (-82.9, -290.9), (
		_route('west_city', 'brawl', 6, 0.66, ((-65, 295, 0), (-175, 235, 0), (-225, 130, 1), (-220, 10, 1), (-190, -105, 1), (-120, -225, 0))),
		_route('central_streets', 'fire', 4, 0.67, ((-65, 295, 0), (-70, 205, 0), (-55, 110, 1), (-45, 10, 1), (-55, -100, 1), (-75, -225, 0))),
		_route('east_fields', 'flank', 5, 0.72, ((-55, 295, 0), (105, 245, 0), (205, 145, 1), (220, 25, 1), (190, -105, 1), (75, -220, 0))),
	))

HILLS = _map('10_hills', (-400.0, -400.0, 400.0, 400.0),
	(175.8, -305.8), (-236.7, 329.7), (
		_route('southwest_road', 'brawl', 5, 0.66, ((170, -295, 0), (65, -240, 0), (-45, -155, 1), (-135, -45, 1), (-190, 85, 1), (-225, 240, 0))),
		_route('central_hills', 'fire', 4, 0.72, ((175, -295, 0), (125, -190, 0), (55, -95, 1), (-15, 20, 1), (-85, 125, 1), (-175, 245, 0))),
		_route('east_coast', 'flank', 4, 0.77, ((180, -295, 0), (270, -210, 0), (305, -85, 1), (270, 55, 1), (165, 155, 1), (-20, 245, 0))),
	))

MUROVANKA = _map('11_murovanka', (-400.0, -400.0, 400.0, 400.0),
	(202.8, 296.1), (-205.0, -292.8), (
		_route('west_woods', 'brawl', 5, 0.66, ((195, 285, 0), (80, 235, 0), (-35, 150, 1), (-120, 40, 1), (-175, -95, 1), (-195, -220, 0))),
		_route('central_field', 'fire', 4, 0.65, ((200, 285, 0), (120, 195, 0), (45, 105, 1), (-20, 5, 1), (-90, -105, 1), (-165, -225, 0))),
		_route('east_village', 'flank', 4, 0.74, ((210, 285, 0), (285, 195, 0), (285, 75, 1), (220, -35, 1), (95, -135, 1), (-65, -230, 0))),
	))

ERLENBERG = _map('13_erlenberg', (-500.0, -500.0, 500.0, 500.0),
	(-146.2, -0.1), (146.4, 0.1), (
		_route('north_bridge', 'brawl', 5, 0.72, ((-140, 0, 0), (-135, 110, 0), (-105, 225, 1), (-20, 300, 1), (85, 230, 1), (135, 105, 0))),
		_route('middle_crossing', 'fire', 4, 0.76, ((-140, 0, 0), (-75, 20, 0), (-20, 15, 1), (35, 10, 1), (90, 15, 1), (135, 0, 0))),
		_route('south_bridge', 'flank', 4, 0.74, ((-140, 0, 0), (-130, -105, 0), (-90, -220, 1), (5, -295, 1), (100, -220, 1), (135, -105, 0))),
	))


TACTICAL_MAPS_GROUP_A = {
	'01_karelia': KARELIA,
	'03_campania': CAMPANIA,
	'05_prohorovka': PROHOROVKA,
	'06_ensk': ENSK,
	'07_lakeville': LAKEVILLE,
	'08_ruinberg': RUINBERG,
	'10_hills': HILLS,
	'11_murovanka': MUROVANKA,
	'13_erlenberg': ERLENBERG,
}
