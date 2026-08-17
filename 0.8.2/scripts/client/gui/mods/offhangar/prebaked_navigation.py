# -*- coding: utf-8 -*-
"""Load versioned navigation graphs shipped with the offline-battle mod."""

import json
import hashlib
import os

from gui.mods.offhangar.paths import mod_dir


FORMAT_NAME = 'offhangar-navgraph'
FORMAT_VERSION = 1
GAME_VERSION = '0.8.2'
MANIFEST_FORMAT = FORMAT_NAME + '-manifest'
STOCK_MAPS = (
	'01_karelia', '02_malinovka', '03_campania', '04_himmelsdorf',
	'05_prohorovka', '06_ensk', '07_lakeville', '08_ruinberg',
	'10_hills', '11_murovanka', '13_erlenberg', '14_siegfried_line',
	'15_komarin', '17_munchen', '18_cliff', '19_monastery',
	'22_slough', '23_westfeld', '28_desert', '29_el_hallouf',
	'31_airfield', '33_fjord', '34_redshire', '35_steppes',
	'36_fishing_bay', '37_caucasus', '38_mannerheim_line',
	'39_crimea', '42_north_america', '44_north_america',
	'45_north_america', '47_canada_a', '51_asia',
)

try:
	_INTEGER_TYPES = (int, long)
except NameError:
	_INTEGER_TYPES = (int,)


def _short_map_name(map_name):
	return str(map_name or '').replace('\\', '/').rstrip('/').split('/')[-1]


def _validate(graph, map_name):
	if not isinstance(graph, dict):
		raise ValueError('navigation graph root is not an object')
	if graph.get('format') != FORMAT_NAME:
		raise ValueError('unsupported navigation graph format')
	if int(graph.get('version', -1)) != FORMAT_VERSION:
		raise ValueError('unsupported navigation graph version')
	if str(graph.get('game_version', '')) != GAME_VERSION:
		raise ValueError('navigation graph belongs to a different client version')
	if _short_map_name(graph.get('map')) != map_name:
		raise ValueError('navigation graph map does not match the battle')
	width = int(graph.get('width', 0))
	height = int(graph.get('height', 0))
	if width <= 0 or height <= 0:
		raise ValueError('navigation graph dimensions are invalid')
	if len(graph.get('heights_mm') or ()) != width * height:
		raise ValueError('navigation graph height array is incomplete')
	if len(graph.get('links') or ()) != width * height:
		raise ValueError('navigation graph link array is incomplete')
	if len(graph.get('origin') or ()) != 2:
		raise ValueError('navigation graph origin is invalid')
	if float(graph.get('cell_size', 0.0)) <= 0.0:
		raise ValueError('navigation graph cell size is invalid')
	if map_name in STOCK_MAPS:
		formations = graph.get('spawn_formations')
		validation = graph.get('validation') or {}
		bake = graph.get('bake') or {}
		skipped_models = bake.get('spawn_obstacle_skipped_models')
		if (not isinstance(formations, dict) or
			set(formations.keys()) != set(('1', '2')) or
			not bool(validation.get('spawn_compiled_bsp_obb_clearance')) or
			not bool(validation.get('spawn_pairwise_obb_clearance')) or
			not bool(validation.get('spawn_terrain_footprint_clearance')) or
			not bool(validation.get('route_terminal_obb_clearance')) or
			isinstance(skipped_models, bool) or
			not isinstance(skipped_models, _INTEGER_TYPES) or
			skipped_models != 0):
			raise ValueError('stock navigation graph lacks validated spawn poses')
		for team in ('1', '2'):
			poses = formations.get(team)
			if not isinstance(poses, list) or len(poses) != 15:
				raise ValueError('stock navigation graph spawn formation is incomplete')
			for pose in poses:
				try:
					values = tuple(float(value) for value in pose)
				except Exception:
					values = ()
				if (not isinstance(pose, list) or len(values) != 4 or
						not all(value == value and abs(value) != float('inf')
							for value in values)):
					raise ValueError('stock navigation graph spawn pose is invalid')
	return graph


def _sha256(path):
	digest = hashlib.sha256()
	handle = open(path, 'rb')
	try:
		while True:
			block = handle.read(1024 * 1024)
			if not block:
				break
			digest.update(block)
	finally:
		handle.close()
	return digest.hexdigest()


def _manifest_entry(directory, map_name):
	"""Validate the complete batch marker and return this map's record.

	A missing manifest remains valid for one-file developer builds. Once a batch
	manifest is present, however, a partial copy or a stale/tampered graph must
	not silently drive bots with mixed navigation data.
	"""
	path = os.path.join(directory, 'manifest.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		manifest = json.load(handle)
	finally:
		handle.close()
	if (not isinstance(manifest, dict) or
			manifest.get('format') != MANIFEST_FORMAT or
			int(manifest.get('version', -1)) != FORMAT_VERSION or
			str(manifest.get('game_version', '')) != GAME_VERSION):
		raise ValueError('navigation manifest is incompatible')
	records = manifest.get('maps') or ()
	if len(records) != len(STOCK_MAPS):
		raise ValueError('navigation manifest is incomplete')
	expected = set(STOCK_MAPS)
	seen = set()
	selected = None
	for record in records:
		if not isinstance(record, dict):
			raise ValueError('navigation manifest record is invalid')
		name = _short_map_name(record.get('map'))
		filename = str(record.get('file') or '')
		if (name not in expected or name in seen or
				filename != name + '.json' or
				len(str(record.get('sha256') or '')) != 64):
			raise ValueError('navigation manifest record is invalid')
		seen.add(name)
		if not os.path.isfile(os.path.join(directory, filename)):
			raise ValueError('navigation graph batch is incomplete')
		if name == map_name:
			selected = record
	if seen != expected:
		raise ValueError('navigation manifest is incomplete')
	return selected


def load_graph(map_name):
	"""Return a validated graph, or None when this map has not been baked."""
	short_name = _short_map_name(map_name)
	if not short_name:
		return None
	directory = os.path.join(mod_dir(), 'navgraphs')
	entry = _manifest_entry(directory, short_name)
	path = os.path.join(directory, short_name + '.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		graph = json.load(handle)
	finally:
		handle.close()
	return _validate(graph, short_name)


def nearest_ground_point(graph, x, z, max_radius=3):
	"""Return the nearest baked-safe cell centre and its ground height."""
	if not isinstance(graph, dict):
		return None
	try:
		width = int(graph.get('width', 0))
		height = int(graph.get('height', 0))
		cell_size = float(graph.get('cell_size', 0.0))
		origin = graph.get('origin') or ()
		heights = graph.get('heights_mm') or ()
		hazards = graph.get('hazards') or (0,) * (width * height)
		if (width <= 0 or height <= 0 or cell_size <= 0.0 or
				len(origin) != 2 or len(heights) != width * height):
			return None
		cx = int(round((float(x) - float(origin[0])) / cell_size))
		cz = int(round((float(z) - float(origin[1])) / cell_size))
	except Exception:
		return None
	best = None
	best_distance = None
	radius_limit = max(0, int(max_radius))
	for radius in range(radius_limit + 1):
		for row in range(cz - radius, cz + radius + 1):
			for column in range(cx - radius, cx + radius + 1):
				if radius and max(abs(column - cx), abs(row - cz)) != radius:
					continue
				if column < 0 or column >= width or row < 0 or row >= height:
					continue
				index = row * width + column
				value = heights[index]
				if value is None or int(hazards[index]) & 3:
					continue
				distance = (column - cx) ** 2 + (row - cz) ** 2
				if best_distance is None or distance < best_distance:
					best_distance = distance
					best = (
						float(origin[0]) + column * cell_size,
						float(value) / 1000.0,
						float(origin[1]) + row * cell_size,
					)
		if best is not None:
			return best
	return None


def spawn_pose(graph, team, slot):
	"""Return one offline-validated stock spawn pose without runtime projection."""
	try:
		team = int(team)
		slot = int(slot)
		formations = graph.get('spawn_formations') if isinstance(graph, dict) else None
		poses = formations.get(str(team)) if isinstance(formations, dict) else None
		pose = poses[slot]
		if team not in (1, 2) or slot < 0 or slot >= 15 or len(pose) != 4:
			return None
		values = tuple(float(value) for value in pose)
		for value in values:
			if value != value or abs(value) == float('inf'):
				return None
		return values
	except Exception:
		return None
