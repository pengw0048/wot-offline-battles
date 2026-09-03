# -*- coding: utf-8 -*-
"""Load the exact #1513 destructible contact catalog beside config.json."""

import json
import math
import os

from gui.mods.offline_lan_0922.navigation_graph_schema import (
	SUPPORTED_MAPS, short_map_name,
)
from gui.mods.offline_lan_0922.prebaked_navigation import mod_dir


FORMAT_NAME = 'offline-lan-0922-destructible-catalog'
FORMAT_VERSION = 7
MANIFEST_FORMAT = FORMAT_NAME + '-manifest'
_STRUCTURE_MAT_KIND_MIN_1513 = 73
_STRUCTURE_MAT_KIND_MAX_1513 = 86
try:
	_STRING_TYPES = (basestring,)
except NameError:
	_STRING_TYPES = (str,)
try:
	_INTEGER_TYPES = (int, long)
except NameError:
	_INTEGER_TYPES = (int,)


def _manifest_entry(directory, map_name):
	path = os.path.join(directory, 'manifest.json')
	if not os.path.isfile(path):
		if os.path.isdir(directory):
			raise ValueError('destructible catalog manifest is missing')
		return None
	handle = open(path, 'r')
	try:
		manifest = json.load(handle)
	finally:
		handle.close()
	try:
		version = int(manifest.get('version', -1))
		locator_quantization = int(manifest.get('locator_quantization'))
	except (AttributeError, TypeError, ValueError):
		raise ValueError('destructible catalog manifest is incompatible')
	if (not isinstance(manifest, dict) or
			manifest.get('format') != MANIFEST_FORMAT or
			version != FORMAT_VERSION or
			locator_quantization != 1000):
		raise ValueError('destructible catalog manifest is incompatible')
	records = manifest.get('maps')
	if not isinstance(records, list):
		raise ValueError(
			'destructible catalog manifest record list is invalid')
	selected = None
	for record in records:
		if (not isinstance(record, dict) or
				short_map_name(record.get('map')) != map_name):
			continue
		if selected is not None:
			raise ValueError(
				'destructible catalog manifest record is duplicated')
		selected = record
	if selected is None:
		raise ValueError(
			'destructible catalog manifest has no record for this map')
	name = short_map_name(selected.get('map'))
	filename = str(selected.get('file') or '')
	if name not in SUPPORTED_MAPS or filename != name + '.json':
		raise ValueError(
			'destructible catalog manifest record is invalid')
	if not os.path.isfile(os.path.join(directory, filename)):
		raise ValueError('destructible catalog is unavailable')
	return selected


def _finite_number(value):
	try:
		value = float(value)
	except (TypeError, ValueError):
		return False
	return not math.isnan(value) and not math.isinf(value)


def _instance_candidate(record, resources):
	if (not isinstance(record, list) or len(record) != 2 or
			not isinstance(record[0], _STRING_TYPES) or
			record[0] not in resources):
		raise ValueError('destructible instance candidate is invalid')
	filename, box_index = record
	resource = resources[filename]
	if resource.get('kind') == 'structure':
		if box_index is not None:
			raise ValueError('structure instance candidate has a box index')
	else:
		if type(box_index) not in _INTEGER_TYPES:
			raise ValueError('destructible instance box index is invalid')
		if box_index < 0 or box_index >= len(resource.get('boxes') or ()):
			raise ValueError('destructible instance box index is invalid')
	return filename, box_index


def _validate(data, map_name):
	if not isinstance(data, dict):
		raise ValueError('destructible catalog root is not an object')
	if data.get('format') != FORMAT_NAME:
		raise ValueError('unsupported destructible catalog format')
	if int(data.get('version', -1)) != FORMAT_VERSION:
		raise ValueError('unsupported destructible catalog version')
	if short_map_name(data.get('map')) != map_name:
		raise ValueError('destructible catalog map does not match the battle')
	resources = data.get('resources')
	if not isinstance(resources, dict) or not resources:
		raise ValueError('destructible catalog resources are unavailable')
	try:
		locator_quantization = int(data.get('locator_quantization'))
	except (TypeError, ValueError):
		raise ValueError('destructible locator quantization is invalid')
	if locator_quantization != 1000:
		raise ValueError('destructible locator quantization is invalid')
	seen = set()
	for filename, record in resources.items():
		if not isinstance(filename, _STRING_TYPES):
			raise ValueError('destructible resource filename is invalid')
		normalized = filename.replace('\\', '/').strip().lower()
		if (not normalized or not normalized.endswith('.model') or
				normalized in seen):
			raise ValueError('destructible resource filename is invalid')
		seen.add(normalized)
		if not isinstance(record, dict):
			raise ValueError('destructible resource record is invalid')
		kind = record.get('kind')
		if kind not in ('falling', 'fragile', 'structure'):
			raise ValueError('destructible resource kind is invalid')
		boxes = record.get('boxes')
		if not isinstance(boxes, list) or not boxes:
			raise ValueError('destructible resource boxes are invalid')
		for box in boxes:
			if (not isinstance(box, list) or len(box) != 7 or
					not all(_finite_number(value) for value in box[:6])):
				raise ValueError('destructible resource box is invalid')
			minimum_x, minimum_y, minimum_z = map(float, box[:3])
			maximum_x, maximum_y, maximum_z = map(float, box[3:6])
			if not (minimum_x < maximum_x and minimum_y < maximum_y and
					minimum_z < maximum_z):
				raise ValueError('destructible resource box is invalid')
			mat_kind = box[6]
			if kind != 'structure':
				if mat_kind is not None:
					raise ValueError(
						'non-structure resource carries a module material')
			else:
				try:
					mat_kind = int(mat_kind)
				except (TypeError, ValueError):
					raise ValueError(
						'structure module material is invalid')
				if not (_STRUCTURE_MAT_KIND_MIN_1513 <= mat_kind <
						_STRUCTURE_MAT_KIND_MAX_1513):
					raise ValueError(
						'structure module material is invalid')
		locators = record.get('locators')
		if kind == 'structure' and locators is not None:
			raise ValueError('structure resource carries instance locators')
		if kind != 'structure' and len(boxes) > 1:
			if not isinstance(locators, list) or not locators:
				raise ValueError(
					'ambiguous destructible resource has no instance locators')
		elif locators is not None:
			raise ValueError('unambiguous resource carries instance locators')
		seen_locators = set()
		for locator in locators or ():
			if (not isinstance(locator, list) or len(locator) != 13 or
					any(type(value) not in _INTEGER_TYPES
						for value in locator)):
				raise ValueError('destructible instance locator is invalid')
			signature = tuple(locator[:12])
			box_index = int(locator[12])
			if (signature in seen_locators or box_index < 0 or
					box_index >= len(boxes)):
				raise ValueError('destructible instance locator is invalid')
			seen_locators.add(signature)
	instances = data.get('instances')
	ambiguous_instances = data.get('ambiguous_instances')
	if not isinstance(instances, list) or not instances:
		raise ValueError('destructible instance index is unavailable')
	if not isinstance(ambiguous_instances, list):
		raise ValueError('ambiguous destructible instance index is invalid')
	seen_signatures = set()
	seen_wires = set()
	for row in instances:
		if (not isinstance(row, list) or len(row) != 17 or
				any(type(value) not in _INTEGER_TYPES for value in row[:12])):
			raise ValueError('destructible instance row is invalid')
		signature = tuple(row[:12])
		if signature in seen_signatures:
			raise ValueError('destructible instance signature is invalid')
		_instance_candidate(row[12:14], resources)
		chunk_id, item_index, item_scale = row[14:]
		if (type(chunk_id) not in _INTEGER_TYPES or
				type(item_index) not in _INTEGER_TYPES or
				chunk_id < 0 or chunk_id > 0xFFFFFFFF or item_index < 0):
			raise ValueError('destructible instance wire is invalid')
		if (chunk_id, item_index) in seen_wires:
			raise ValueError('destructible instance wire is duplicated')
		seen_wires.add((chunk_id, item_index))
		if not _finite_number(item_scale) or float(item_scale) <= 0.0:
			raise ValueError('destructible instance scale is invalid')
		seen_signatures.add(signature)
	for row in ambiguous_instances:
		if (not isinstance(row, list) or len(row) != 13 or
				any(type(value) not in _INTEGER_TYPES for value in row[:12]) or
				not isinstance(row[12], list) or len(row[12]) < 2):
			raise ValueError('ambiguous destructible instance row is invalid')
		signature = tuple(row[:12])
		if signature in seen_signatures:
			raise ValueError(
				'ambiguous destructible instance signature is invalid')
		for candidate in row[12]:
			_instance_candidate(candidate, resources)
		seen_signatures.add(signature)
	return data


def load_catalog(map_name, base_dir=None):
	"""Return the validated contact catalog for one supported arena."""
	short_name = short_map_name(map_name)
	if not short_name:
		return None
	directory = os.path.join(
		base_dir if base_dir is not None else mod_dir(), 'destructibles')
	entry = _manifest_entry(directory, short_name)
	path = os.path.join(directory, short_name + '.json')
	if not os.path.isfile(path):
		return None
	handle = open(path, 'r')
	try:
		data = json.load(handle)
	finally:
		handle.close()
	return _validate(data, short_name)
