# -*- coding: utf-8 -*-
"""Pure-data validation shared by the #1513 runtime and release builder."""


FORMAT_NAME = 'offline-lan-0922-navgraph'
FORMAT_VERSION = 2
GAME_VERSION = '0.9.22.0.1-cn-1513'
MANIFEST_FORMAT = FORMAT_NAME + '-manifest'
SUPPORTED_MAPS = (
	'01_karelia', '02_malinovka', '04_himmelsdorf',
	'05_prohorovka', '06_ensk', '07_lakeville', '08_ruinberg',
	'10_hills', '11_murovanka', '13_erlenberg', '14_siegfried_line',
	'17_munchen', '18_cliff', '19_monastery',
	'22_slough', '23_westfeld', '28_desert', '29_el_hallouf',
	'31_airfield', '33_fjord', '34_redshire', '35_steppes',
	'36_fishing_bay', '37_caucasus', '38_mannerheim_line',
	'44_north_america',
	'45_north_america', '47_canada_a', '59_asia_great_wall', '63_tundra',
	'73_asia_korea', '83_kharkiv', '84_winter', '86_himmelsdorf_winter',
	'92_stalingrad', '95_lost_city', '100_thepit', '101_dday',
	'103_ruinberg_winter', '112_eiffel_tower_ctf', '114_czech',
)


def short_map_name(map_name):
	return str(map_name or '').replace('\\', '/').rstrip('/').split('/')[-1]


def _finite(value, label):
	try:
		result = float(value)
	except (TypeError, ValueError):
		raise ValueError('%s is not numeric' % label)
	if result != result or abs(result) == float('inf'):
		raise ValueError('%s is not finite' % label)
	return result


def _point(value, label, exact_length=None):
	if not isinstance(value, (list, tuple)):
		raise ValueError('%s is not a coordinate array' % label)
	if ((exact_length is not None and len(value) != exact_length) or
			exact_length is None and len(value) < 2):
		raise ValueError('%s has invalid dimensions' % label)
	_finite(value[0], label + ' x')
	_finite(value[1], label + ' z')
	return value


def _team_points(graph, name):
	values = graph.get(name)
	if not isinstance(values, (list, tuple)) or len(values) != 2:
		raise ValueError('navigation graph %s must contain two teams' % name)
	for index, value in enumerate(values):
		_point(value, '%s team %d' % (name, index + 1), exact_length=2)


def _spawn_formations(graph):
	values = graph.get('spawn_formations')
	if not isinstance(values, dict):
		raise ValueError('navigation graph spawn formations are missing')
	for team in (1, 2):
		formation = values.get(str(team), values.get(team))
		if not isinstance(formation, (list, tuple)) or len(formation) != 15:
			raise ValueError(
				'navigation graph team %d must contain 15 spawn slots' % team)
		for slot, value in enumerate(formation):
			if not isinstance(value, (list, tuple)) or len(value) != 4:
				raise ValueError(
					'navigation graph team %d spawn slot %d is invalid' %
					(team, slot))
			for coordinate, label in zip(value, ('x', 'y', 'z', 'yaw')):
				_finite(coordinate, 'team %d spawn slot %d %s' %
				        (team, slot, label))


def _routes(graph):
	routes = graph.get('routes')
	if not isinstance(routes, dict):
		raise ValueError('navigation graph routes are missing')
	for team in (1, 2):
		values = routes.get(str(team), routes.get(team))
		if not isinstance(values, (list, tuple)) or not values:
			raise ValueError(
				'navigation graph routes are missing for team %d' % team)
		seen = set()
		for route in values:
			if not isinstance(route, dict):
				raise ValueError('navigation graph route is invalid')
			route_id = str(route.get('id') or '')
			if not route_id or route_id in seen:
				raise ValueError('navigation graph route id is invalid')
			seen.add(route_id)
			waypoints = route.get('waypoints')
			if (not isinstance(waypoints, (list, tuple)) or
					len(waypoints) < 2 or len(waypoints) > 16):
				raise ValueError(
					'navigation graph route must contain 2..16 waypoints')
			for index, point in enumerate(waypoints):
				_point(point, 'route %s waypoint %d' %
				       (route_id, index))


def validate_graph(graph, map_name):
	"""Validate the complete navigation contract consumed during battle."""
	map_name = short_map_name(map_name)
	if not isinstance(graph, dict):
		raise ValueError('navigation graph root is not an object')
	if graph.get('format') != FORMAT_NAME:
		raise ValueError('unsupported navigation graph format')
	try:
		version = int(graph.get('version', -1))
	except (TypeError, ValueError):
		raise ValueError('unsupported navigation graph version')
	if version != FORMAT_VERSION:
		raise ValueError('unsupported navigation graph version')
	if short_map_name(graph.get('map')) != map_name:
		raise ValueError('navigation graph map does not match the battle')
	try:
		width = int(graph.get('width', 0))
		height = int(graph.get('height', 0))
	except (TypeError, ValueError):
		raise ValueError('navigation graph dimensions are invalid')
	if width <= 0 or height <= 0:
		raise ValueError('navigation graph dimensions are invalid')
	cell_count = width * height
	# A loaded graph stores its byte-valued cell arrays as bytearrays, so the
	# same graph validates before and after packing.
	for name in ('heights_mm', 'links', 'hazards'):
		values = graph.get(name)
		if (not isinstance(values, (list, tuple, bytearray)) or
				len(values) != cell_count):
			raise ValueError(
				'navigation graph %s array is incomplete' % name)
	_point(graph.get('origin'), 'navigation graph origin', exact_length=2)
	bounds = graph.get('bounds')
	if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
		raise ValueError('navigation graph bounds are invalid')
	for index, value in enumerate(bounds):
		_finite(value, 'navigation graph bounds %d' % index)
	if _finite(graph.get('cell_size'), 'navigation graph cell size') <= 0.0:
		raise ValueError('navigation graph cell size is invalid')
	_team_points(graph, 'spawn_anchors')
	_team_points(graph, 'objective_bases')
	_spawn_formations(graph)
	_routes(graph)
	return graph
