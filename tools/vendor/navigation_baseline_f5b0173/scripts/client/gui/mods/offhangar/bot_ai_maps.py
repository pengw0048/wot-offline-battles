# -*- coding: utf-8 -*-
"""Tactical route data for the offline bot director.

Coordinates in this file belong to the pinned 0.8.2 client, not a modern WoT
map. Himmelsdorf was traced against the original ``04_himmelsdorf.pkg``
minimap and its packed arena definition:

* bounds: (-300, -300) -> (400, 400)
* team 1 ctf base: south, approximately (-48, -303)
* team 2 ctf base: north, approximately (17, 300)

The routes are intentionally sparse macro waypoints. The existing multi-ray
driver remains responsible for local obstacle avoidance between them.
"""


HIMMELSDORF = {
	'name': '04_himmelsdorf',
	'bounds': (-300.0, -300.0, 400.0, 400.0),
	'bases': {
		1: (-47.5, -302.6),
		2: (17.1, 300.0),
	},
	'routes': {
		1: (
			{
				'id': 'banana',
				'capacity': 6,
				'risk': 0.62,
				'role_weights': {
					'brawler': 1.00, 'support': 0.58, 'flanker': 0.30,
					'sniper': 0.10, 'scout': 0.18, 'artillery': 0.00,
				},
				'waypoints': (
					(10.0, -255.0, 0), (110.0, -215.0, 0),
					(180.0, -145.0, 0), (200.0, -75.0, 1),
					(180.0, 0.0, 1), (100.0, 80.0, 1),
					(50.0, 210.0, 1), (30.0, 270.0, 0),
					(17.1, 300.0, 0),
				),
			},
			{
				'id': 'hill',
				'capacity': 4,
				'risk': 0.78,
				'role_weights': {
					'brawler': 0.35, 'support': 0.58, 'flanker': 1.00,
					'sniper': 0.18, 'scout': 0.72, 'artillery': 0.00,
				},
				'waypoints': (
					(70.0, -280.0, 0), (250.0, -260.0, 0),
					(365.0, -70.0, 1),
					(365.0, 20.0, 1), (355.0, 100.0, 1),
					(350.0, 180.0, 1), (250.0, 270.0, 0),
					(170.0, 275.0, 0), (17.1, 300.0, 0),
				),
			},
			{
				'id': 'rail',
				'capacity': 4,
				'risk': 0.42,
				'role_weights': {
					'brawler': 0.18, 'support': 0.62, 'flanker': 0.58,
					'sniper': 1.00, 'scout': 0.88, 'artillery': 0.12,
				},
				'waypoints': (
					(-120.0, -260.0, 0), (-210.0, -210.0, 0),
					(-250.0, -100.0, 1),
					(-250.0, 20.0, 1), (-235.0, 140.0, 1),
					(-190.0, 230.0, 0), (-80.0, 285.0, 0),
					(17.1, 300.0, 0),
				),
			},
			{
				'id': 'rear_guard',
				'capacity': 2,
				'risk': 0.08,
				'role_weights': {
					'brawler': 0.00, 'support': 0.18, 'flanker': 0.00,
					'sniper': 0.28, 'scout': 0.00, 'artillery': 1.00,
				},
				'waypoints': ((-80.0, -270.0, 1),),
			},
		),
		2: (),
	},
}


def _reverse_route(route):
	"""Build the north-to-south route without sharing mutable containers."""
	result = dict(route)
	result['role_weights'] = dict(route.get('role_weights', {}))
	result['waypoints'] = tuple(reversed(route.get('waypoints', ())))
	return result


# All three fighting corridors are bidirectional on this version of the map.
# The rear guard is anchored separately because each base needs its own cover.
_north_routes = []
for _route in HIMMELSDORF['routes'][1]:
	if _route['id'] == 'rear_guard':
		_north = _reverse_route(_route)
		_north['waypoints'] = ((45.0, 270.0, 1),)
	else:
		_north = _reverse_route(_route)
	_north_routes.append(_north)
HIMMELSDORF['routes'][2] = tuple(_north_routes)
del _north_routes
del _route
del _north


TACTICAL_MAPS = {
	'04_himmelsdorf': HIMMELSDORF,
}

# The stock 0.8.2 map set is split into data-only modules so this registry
# stays reviewable. In the legacy arena data, "ctf" names the normal
# base-capture battle type; assault and encounter variants are not annotated.
from gui.mods.offhangar import bot_ai_maps_group_a
from gui.mods.offhangar import bot_ai_maps_group_b
from gui.mods.offhangar import bot_ai_maps_group_c
from gui.mods.offhangar import bot_ai_maps_extra

TACTICAL_MAPS.update(bot_ai_maps_group_a.TACTICAL_MAPS_GROUP_A)
TACTICAL_MAPS.update(bot_ai_maps_group_b.TACTICAL_MAPS_GROUP_B)
TACTICAL_MAPS.update(bot_ai_maps_group_c.TACTICAL_MAPS_GROUP_C)
TACTICAL_MAPS.update(bot_ai_maps_extra.TACTICAL_MAPS_EXTRA)


def normalize_map_name(map_name):
	name = str(map_name or '').replace('\\', '/').split('/')[-1]
	if name.endswith('.xml'):
		name = name[:-4]
	return name.lower()


def get_tactical_map(map_name):
	return TACTICAL_MAPS.get(normalize_map_name(map_name))
