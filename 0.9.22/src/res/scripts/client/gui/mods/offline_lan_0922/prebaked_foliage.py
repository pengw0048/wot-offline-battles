# -*- coding: utf-8 -*-
"""Load versioned #1513 foliage volumes shipped beside config.json."""

import json
import math
import os

from gui.mods.offline_lan_0922.foliage import FoliageMap
from gui.mods.offline_lan_0922.navigation_graph_schema import (
	SUPPORTED_MAPS, short_map_name,
)
from gui.mods.offline_lan_0922.prebaked_navigation import mod_dir


FORMAT_NAME = 'offline-lan-0922-foliage'
FORMAT_VERSION = 4
MANIFEST_FORMAT = FORMAT_NAME + '-manifest'
try:
	_INTEGER_TYPES = (int, long)
except NameError:
	_INTEGER_TYPES = (int,)


def _manifest_entry(directory, map_name):
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
			int(manifest.get('version', -1)) != FORMAT_VERSION):
		raise ValueError('foliage manifest is incompatible')
	records = manifest.get('maps')
	if not isinstance(records, list):
		raise ValueError('foliage manifest record list is invalid')
	selected = None
	for record in records:
		if (not isinstance(record, dict) or
				short_map_name(record.get('map')) != map_name):
			continue
		if selected is not None:
			raise ValueError('foliage manifest record is duplicated')
		selected = record
	if selected is None:
		raise ValueError('foliage manifest has no record for this map')
	name = short_map_name(selected.get('map'))
	filename = str(selected.get('file') or '')
	if name not in SUPPORTED_MAPS or filename != name + '.json':
		raise ValueError('foliage manifest record is invalid')
	if not os.path.isfile(os.path.join(directory, filename)):
		raise ValueError('foliage data is unavailable')
	return selected


def _validate(data, map_name):
	if not isinstance(data, dict):
		raise ValueError('foliage root is not an object')
	if data.get('format') != FORMAT_NAME:
		raise ValueError('unsupported foliage format')
	if int(data.get('version', -1)) != FORMAT_VERSION:
		raise ValueError('unsupported foliage version')
	if short_map_name(data.get('map')) != map_name:
		raise ValueError('foliage map does not match the battle')
	if float(data.get('cell_size', 0.0)) <= 0.0:
		raise ValueError('foliage cell size is invalid')
	instances = data.get('instances') or ()
	for instance in instances:
		if not isinstance(instance, (list, tuple)) or len(instance) != 10:
			raise ValueError('foliage instance is invalid')
	for ids in (data.get('cells') or {}).values():
		for instance_id in ids:
			if int(instance_id) < 0 or int(instance_id) >= len(instances):
				raise ValueError(
					'foliage cell references an invalid instance')
	fallen_trees = data.get('fallen_trees')
	if not isinstance(fallen_trees, list):
		raise ValueError('fallen tree foliage profiles are invalid')
	seen_wires = set()
	seen_standing_instances = set()
	for row in fallen_trees:
		if (not isinstance(row, list) or len(row) != 9 or
				type(row[0]) not in _INTEGER_TYPES or
				type(row[1]) not in _INTEGER_TYPES or
				row[0] < 0 or row[1] < 0):
			raise ValueError('fallen tree foliage profile is invalid')
		wire = (int(row[0]), int(row[1]))
		if wire in seen_wires:
			raise ValueError('fallen tree foliage wire is duplicated')
		seen_wires.add(wire)
		try:
			bounds = tuple(float(value) for value in row[2:8])
		except (TypeError, ValueError, OverflowError):
			raise ValueError('fallen tree foliage profile is invalid')
		if (not all(not math.isnan(value) and not math.isinf(value)
				for value in bounds) or
				not all(bounds[index] < bounds[index + 3]
					for index in range(3))):
			raise ValueError('fallen tree foliage profile is invalid')
		standing_instance_id = row[8]
		if (standing_instance_id is not None and
				(type(standing_instance_id) not in _INTEGER_TYPES or
				 standing_instance_id < 0 or
				 standing_instance_id >= len(instances) or
				 standing_instance_id in seen_standing_instances)):
			raise ValueError(
				'fallen tree standing foliage reference is invalid')
		if standing_instance_id is not None:
			seen_standing_instances.add(standing_instance_id)
	return data


def load_foliage(map_name, base_dir=None):
	short_name = short_map_name(map_name)
	if not short_name:
		return None
	directory = os.path.join(
		base_dir if base_dir is not None else mod_dir(), 'foliage')
	entry = _manifest_entry(directory, short_name)
	path = os.path.join(directory, short_name + '.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		data = json.load(handle)
	finally:
		handle.close()
	return FoliageMap(_validate(data, short_name))
