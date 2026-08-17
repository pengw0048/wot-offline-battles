# -*- coding: utf-8 -*-
"""Load versioned foliage volumes shipped with the offline-battle mod."""

import hashlib
import json
import os

from gui.mods.offhangar.foliage import FoliageMap
from gui.mods.offhangar.paths import mod_dir


FORMAT_NAME = 'offhangar-foliage'
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


def _short_map_name(map_name):
	return str(map_name or '').replace('\\', '/').rstrip('/').split('/')[-1]


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
		raise ValueError('foliage manifest is incompatible')
	records = manifest.get('maps') or ()
	if len(records) != len(STOCK_MAPS):
		raise ValueError('foliage manifest is incomplete')
	expected = set(STOCK_MAPS)
	seen = set()
	selected = None
	for record in records:
		if not isinstance(record, dict):
			raise ValueError('foliage manifest record is invalid')
		name = _short_map_name(record.get('map'))
		filename = str(record.get('file') or '')
		if (name not in expected or name in seen or
				filename != name + '.json' or
				len(str(record.get('sha256') or '')) != 64):
			raise ValueError('foliage manifest record is invalid')
		seen.add(name)
		if not os.path.isfile(os.path.join(directory, filename)):
			raise ValueError('foliage batch is incomplete')
		if name == map_name:
			selected = record
	if seen != expected:
		raise ValueError('foliage manifest is incomplete')
	return selected


def _validate(data, map_name):
	if not isinstance(data, dict):
		raise ValueError('foliage root is not an object')
	if data.get('format') != FORMAT_NAME:
		raise ValueError('unsupported foliage format')
	if int(data.get('version', -1)) != FORMAT_VERSION:
		raise ValueError('unsupported foliage version')
	if str(data.get('game_version', '')) != GAME_VERSION:
		raise ValueError('foliage belongs to a different client version')
	if _short_map_name(data.get('map')) != map_name:
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
				raise ValueError('foliage cell references an invalid instance')
	return data


def load_foliage(map_name):
	short_name = _short_map_name(map_name)
	if not short_name:
		return None
	directory = os.path.join(mod_dir(), 'foliage')
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
