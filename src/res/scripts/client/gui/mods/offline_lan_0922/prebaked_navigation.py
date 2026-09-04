# -*- coding: utf-8 -*-
"""Load versioned navigation graphs shipped with the offline-battle mod."""

import json
import os

from gui.mods.offline_lan_0922.config import CONFIG_PATH
from gui.mods.offline_lan_0922.navigation_graph_schema import (
	SUPPORTED_MAPS, short_map_name, validate_graph,
)


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


def pose_is_safe(graph, position, shoulder_cells=0):
	"""Return whether a pose stays outside baked water/cliff cells.

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
			if int(hazards[index]) & 3:
				return False
	return True
