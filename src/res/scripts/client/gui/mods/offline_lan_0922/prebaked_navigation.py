# -*- coding: utf-8 -*-
"""Load versioned navigation graphs shipped with the offline-battle mod."""

import json
import math
import os

from gui.mods.offline_lan_0922.config import CONFIG_PATH
from gui.mods.offline_lan_0922.navigation_graph_schema import (
	SUPPORTED_MAPS, short_map_name, validate_graph,
)


# Cell centres are baked on an exact multiple of the cell size, so this only
# absorbs the float error of the division itself.
_CELL_EPSILON = 1.0e-6


def mod_dir():
	"""Return the real filesystem directory copied beside config.json.

	The #1513 package is a ``.wotmod`` archive.  Its Python modules are imported
	through the resource VFS, but stdlib ``open`` cannot read adjacent JSON from
	that virtual path.  Navigation graphs therefore live in the install
	overlay's ordinary ``mods/configs`` tree.
	"""
	return os.path.dirname(CONFIG_PATH)


def _short_map_name(map_name):
	return short_map_name(map_name)


def _validate(graph, map_name):
	return validate_graph(graph, map_name)


def load_graph(map_name, base_dir=None):
	"""Return a validated graph, or None when this map has not been baked.

	The batch manifest is a build/install audit artifact. Runtime safety comes
	from validating the selected graph itself, so stale optional metadata cannot
	turn a structurally valid deployed map into a worker-fatal loading error.
	"""
	short_name = _short_map_name(map_name)
	if not short_name:
		return None
	directory = os.path.join(
		base_dir if base_dir is not None else mod_dir(), 'navgraphs')
	path = os.path.join(directory, short_name + '.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		graph = json.load(handle)
	finally:
		handle.close()
	graph = _validate(graph, short_name)
	return _pack_cell_arrays(graph)


def _pack_cell_arrays(graph):
	"""Store the byte-valued cell arrays as bytearrays.

	``links`` and ``hazards`` hold one value below 256 per cell, so a bytearray
	replaces a list of 62750 pointers. ``heights_mm`` keeps its list because
	its consumers test individual cells for ``None``.
	"""
	if not isinstance(graph, dict):
		return graph
	for name in ('links', 'hazards'):
		values = graph.get(name)
		if isinstance(values, list):
			try:
				graph[name] = bytearray(values)
			except (TypeError, ValueError):
				pass
	return graph


def _cell_span(minimum, maximum, origin, cell_size, count):
	"""Return the inclusive cell index range whose centres stay in a limit."""
	first = int(math.ceil((minimum - origin) / cell_size - _CELL_EPSILON))
	last = int(math.floor((maximum - origin) / cell_size + _CELL_EPSILON))
	return max(0, first), min(count - 1, last)


def clip_graph_to_arena(graph, arena_bounds):
	"""Narrow a baked graph to the exact #1513 arena rectangle.

	A baked ``bounds`` value is the *sampling* rectangle. The baker widens the
	stock rectangle to cover authored route anchors and to align the cell grid,
	so on several maps it reaches metres past the official red border and cells
	were baked out there. Prebaked A* walks the cell arrays directly and the Bot
	authority's map-edge guard reads ``bounds``, so both would send a Bot where
	the local player's own border contact refuses to go. Publish the arena
	rectangle and retire every cell centred outside it.

	``arena_bounds`` of None means this battle has no readable stock rectangle;
	the graph is then left exactly as baked. Returns the number of navigable
	cells retired, and is safe to apply twice.
	"""
	if arena_bounds is None:
		return 0
	if not isinstance(graph, dict):
		raise ValueError('navigation graph is unavailable')
	limits = [float(value) for value in arena_bounds]
	if len(limits) != 4:
		raise ValueError('arena rectangle is incomplete')
	for value in limits:
		if math.isnan(value) or math.isinf(value):
			raise ValueError('arena rectangle is not finite')
	minimum_x, minimum_z, maximum_x, maximum_z = limits
	bounds = graph.get('bounds')
	if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
		# Never widen: the shipped rectangle stays authoritative wherever it is
		# already the tighter of the two.
		minimum_x = max(minimum_x, float(bounds[0]))
		minimum_z = max(minimum_z, float(bounds[1]))
		maximum_x = min(maximum_x, float(bounds[2]))
		maximum_z = min(maximum_z, float(bounds[3]))
	if minimum_x >= maximum_x or minimum_z >= maximum_z:
		raise ValueError('navigation graph does not overlap the arena')
	width = int(graph['width'])
	height = int(graph['height'])
	cell_size = float(graph['cell_size'])
	origin = graph['origin']
	heights = graph['heights_mm']
	links = graph['links']
	if (width <= 0 or height <= 0 or cell_size <= 0.0 or len(origin) != 2 or
			len(heights) != width * height or len(links) != width * height):
		raise ValueError('navigation graph cell arrays are invalid')
	graph['bounds'] = [minimum_x, minimum_z, maximum_x, maximum_z]
	first_column, last_column = _cell_span(
		minimum_x, maximum_x, float(origin[0]), cell_size, width)
	first_row, last_row = _cell_span(
		minimum_z, maximum_z, float(origin[1]), cell_size, height)
	if (first_column <= 0 and first_row <= 0 and
			last_column >= width - 1 and last_row >= height - 1):
		return 0
	if first_column > last_column or first_row > last_row:
		raise ValueError('the arena rectangle retires every baked cell')
	if not isinstance(heights, list):
		graph['heights_mm'] = heights = list(heights)
	if not isinstance(links, (bytearray, list)):
		graph['links'] = links = bytearray(links)
	retired = 0
	for row in range(height):
		base = row * width
		if first_row <= row <= last_row:
			columns = list(range(0, first_column))
			columns.extend(range(last_column + 1, width))
		else:
			columns = range(width)
		for column in columns:
			index = base + column
			if heights[index] is not None:
				heights[index] = None
				retired += 1
			links[index] = 0
	# A retired cell already fails every height lookup, but the surviving edge
	# cells still carry link bits that point at it. Drop them so link counts,
	# clearance costs and A* expansion all describe the same graph.
	directions = graph.get('directions')
	if not isinstance(directions, (list, tuple)) or not directions:
		raise ValueError('navigation graph directions are missing')
	edge = set()
	for column in range(first_column, last_column + 1):
		edge.add((column, first_row))
		edge.add((column, last_row))
	for row in range(first_row, last_row + 1):
		edge.add((first_column, row))
		edge.add((last_column, row))
	for column, row in edge:
		index = row * width + column
		mask = int(links[index])
		if heights[index] is None or not mask:
			continue
		for bit, direction in enumerate(directions):
			if not mask & (1 << bit):
				continue
			neighbour_column = column + int(direction[0])
			neighbour_row = row + int(direction[1])
			if (neighbour_column < first_column or
					neighbour_column > last_column or
					neighbour_row < first_row or neighbour_row > last_row):
				mask &= ~(1 << bit)
		links[index] = mask
	return retired


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


# Runtime-only bit: routing continues to use the original edge bit (2).
SHORE_EDGE = 8
MOTION_FATAL_HAZARDS = 1 | SHORE_EDGE


def with_shore_hazards(graph):
	"""Classify wet edges once, without changing the baked routing graph.

	Edge erosion also marks continuous dry slopes. Only edges within the bake's
	clearance radius of water remain a hard motion veto. Rounding outward to
	whole cells conservatively includes the shoreline's coarse-grid apron.
	This is local shoreline protection, not a complete fall-trajectory test.
	"""
	result = dict(graph)
	hazards = graph['hazards']
	width, height = int(graph['width']), int(graph['height'])
	radii = (graph.get('bake') or {}).get('edge_clearance_radii') or ()
	radius = (int(math.ceil(max(radii) / float(graph['cell_size'])))
			  if radii else None)
	classified = [int(value) & ~SHORE_EDGE for value in hazards]
	for index, value in enumerate(hazards):
		if not int(value) & 2:
			continue
		# Without the bake's reviewed clearance extent, retain its edge veto.
		near_water = radius is None
		if radius is not None:
			row, column = divmod(index, width)
			for z in range(max(0, row - radius), min(height, row + radius + 1)):
				if any(int(hazards[z * width + x]) & (1 | 4)
					   for x in range(max(0, column - radius),
									  min(width, column + radius + 1))):
					near_water = True
					break
		if near_water:
			classified[index] |= SHORE_EDGE
	result['hazards'] = tuple(classified)
	return result


def pose_is_safe(graph, position, shoulder_cells=0, hazard_mask=3):
	"""Return whether a pose stays outside the selected baked hazards.

	Missing height cells may be ordinary building footprints and are therefore
	not classified as cliffs. A coordinate outside the baked grid is invalid.
	The caller receives validation errors directly; an incompatible graph must
	not silently disable the presentation safety boundary.
	"""
	if not isinstance(graph, dict):
		raise ValueError('navigation graph is unavailable')
	width = int(graph['width'])
	height = int(graph['height'])
	cell_size = float(graph['cell_size'])
	origin = graph['origin']
	hazards = graph['hazards']
	if len(position) < 3:
		raise ValueError('navigation pose is incomplete')
	column = int(round((float(position[0]) - float(origin[0])) / cell_size))
	row = int(round((float(position[2]) - float(origin[1])) / cell_size))
	radius = max(0, int(shoulder_cells))
	if (column - radius < 0 or column + radius >= width or
			row - radius < 0 or row + radius >= height):
		return False
	for check_row in range(row - radius, row + radius + 1):
		for check_column in range(column - radius, column + radius + 1):
			index = check_row * width + check_column
			if int(hazards[index]) & hazard_mask:
				return False
	return True
