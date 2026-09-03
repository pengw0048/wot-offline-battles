# -*- coding: utf-8 -*-
"""0.8.2 destructible contact sensors on the #1513 engine boundary.

The three sensor bodies below are dedented copies from ``offline_battle.py``.
Only their former closure dependencies are supplied at module scope.
"""

_event_sink = None

_DESTRUCTIBLE_BIN_METRES = 8.0
_DESTRUCTIBLE_ORIGIN_RADIUS = 8.0
_DESTRUCTIBLE_CHUNK_METRES_1513 = 100.0
_SOLID_CONTACT_RADIUS_1513 = 0.5
_SOLID_CONTACT_NORMAL_DOT_1513 = 0.5
_TREE_SWEEP_ANGLE_STEP_1513 = 3.141592653589793 / 36.0
_TREE_SWEEP_TRANSLATION_STEP_1513 = 8.0
_TREE_SWEEP_MAX_SEGMENTS_1513 = 128
_TREE_CONTACT_TOKEN_LIMIT_1513 = 64
_CATALOG_POINT_EPSILON = 0.075
_SHOT_RAY_EPSILON = 1.0e-4
_SOFT_STATIC_MAX_SKIPS = 4
_NATIVE_HIDE_MIN_SECONDS = 0.2
_FALLING_REFRESH_SECONDS = 1.0 / 60.0
_DIAGNOSTICS_ENABLED = False
_DIAGNOSTIC_EMIT_SECONDS = 0.25
_DIAGNOSTIC_CHUNK_LIMIT = 24
_DIAGNOSTIC_PENDING_LIMIT = 4
_DIAGNOSTIC_CONTACT_LIMIT = 32
_ISOLATION_LOG_TYPE_LIMIT = 14
# Bounded per-chunk cache of the exact native item-name mapping.
_ITEM_NAME_CHUNK_CACHE_LIMIT = 64
# All callers share at most this many native effect-category probes per
# battle-local ``BigWorld.time()`` tick.  Chunk scans retry incomplete
# alignments on later ticks instead of multiplying work by human/Bot callers.
_ITEM_NAME_QUERY_BUDGET = 16
_EMPTY_CONTACT_RECEIPT_LIMIT = 512
_EMPTY_PROXIMITY_RECEIPT_LIMIT = 512
_destructible_catalog = None
_diagnostic_writer = None

# Server-side values pinned by #1513 ``scripts/destructibles.xml``.  The
# release client does not load these fields outside development builds.  Each
# listed effect material uses factor=0/minimum=25, hence max(P*0, 25)=25 mm.
_SHOT_THROUGH_MAX_HP_1513 = 19.0
_SHOT_THROUGH_MIN_REDUCTION_1513 = 25.0
_SHOT_AP_KINDS_1513 = frozenset((
	'ARMOR_PIERCING', 'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR'))

# Exact pinned #1513 native contract for
# ``BigWorld.wg_getDestructibleEffectCategory(spaceID, chunk, item, module)``,
# read from the reviewed x86 entry point of the exact client executable
# (PE timestamp 0x5a6edca4, checksum 0x019a5229):
#
# * a negative ``module`` selects the no-module resolve, a non-negative one the
#   per-module resolve; both then share the same post-processing;
# * when the (chunk, item[, module]) triple does not resolve, the entry returns
#   no object at all, which reaches Python as an error rather than a value;
# * when it does resolve, the category comes from the effect handler registered
#   for that item's native group.  If no handler is registered for the group the
#   entry returns exactly -1.
#
# So -1 proves the native item resolved and only says that the effect-category
# channel is unregistered for it; it carries no destructible kind.  It is
# neither a kind match nor evidence of a wrong wire, and the offline client
# legitimately leaves handler groups unregistered.  Treat it as one unverified
# identity channel, never as a wildcard.
_NATIVE_EFFECT_CATEGORY_UNREGISTERED_1513 = -1

# Exact constants from pinned #1513 ``constants.DESTRUCTIBLE_MATKIND``.
# ``NORMAL_MAX`` is an exclusive sentinel: DestructiblesCache allocates
# structure modules from 73 through 85 and maps 87 through 99 to damaged BSP
# materials.  Only a normal material may enter encodeDestructibleModule.
_DESTRUCTIBLE_MAT_KIND_MIN_1513 = 71
_DESTRUCTIBLE_MAT_KIND_MAX_1513 = 100
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


def _normalized_filename(filename):
	if not isinstance(filename, _STRING_TYPES):
		return None
	return filename.replace('\\', '/').strip().lower()


def _destructible_isolated_1513(chunk_id, item_index=None):
	"""Return whether runtime validation quarantined this native identity."""
	chunk_id = int(chunk_id)
	if chunk_id in globals().get('g_offh_destr_isolated_chunks', ()):
		return True
	if item_index is None:
		return False
	return (chunk_id, int(item_index)) in globals().get(
		'g_offh_destr_isolated_slots', ())


def is_isolated_1513(chunk_id, item_index):
	"""Expose the quarantine gate to canonical LAN event replay."""
	return _destructible_isolated_1513(chunk_id, item_index)


def isolate_destructible_1513(
		failure_type, chunk_id, item_index=None, detail=None):
	"""Expose exact identity quarantine to the stock-cache adapter."""
	_isolate_destructible_1513(
		failure_type, chunk_id, item_index, detail=detail)


def validate_tree_identity_1513(space_id, chunk_id, item_index):
	"""Prove a loaded #1513 slot has a safe named tree descriptor.

	An unloaded chunk retains the stock queued-order behavior.  Once loaded,
	the nullable scalar filename wrapper is never a legal probe: the safe chunk
	list must name this slot and resolve it to a tree descriptor first.
	"""
	chunk_id = int(chunk_id)
	item_index = int(item_index)
	if _destructible_isolated_1513(chunk_id, item_index):
		return False
	import AreaDestructibles
	manager = getattr(
		AreaDestructibles, 'g_destructiblesManager', None)
	if manager is None:
		raise RuntimeError('destructibles manager is unavailable')
	if not manager.isChunkLoaded(chunk_id):
		return True
	from gui.mods.offline_lan_0922 import destructibles_compat
	status, desc = destructibles_compat.inspect_destructible_desc(
		AreaDestructibles.g_cache, space_id, chunk_id, item_index)
	if status == 'pending':
		# ``game.wg_onChunkLoad`` and the chunk filename list can settle on
		# adjacent frames. Keep the object solid for this attempt and retry.
		return False
	if _destructible_isolated_1513(chunk_id, item_index):
		# The compat lookup already recorded the first exact descriptor failure.
		return False
	if (desc is None or
			desc.get('type') != AreaDestructibles.DESTR_TYPE_TREE):
		_isolate_destructible_1513(
			'tree_descriptor', chunk_id, item_index,
			detail='safe named tree descriptor is unavailable')
		return False
	return True


def _drop_isolated_destructible_1513(chunk_id, item_index=None):
	"""Remove synthetic collision state without touching native authority."""
	chunk_id = int(chunk_id)
	identity = (chunk_id, int(item_index)) if item_index is not None else None
	instances = globals().get('g_offh_destr_instances', {})
	identities = [key for key in list(instances)
		if key[0] == chunk_id and (identity is None or key == identity)]
	active = globals().get('g_offh_destr_falling_active', {})
	contact_bins = globals().get('g_offh_destr_contact_bins', {})
	for isolated_identity in identities:
		instance = instances.pop(isolated_identity)
		bin_keys = (instance.get('bin_keys')
			if isinstance(instance, dict) else None)
		if bin_keys is None:
			# Compatibility fixtures can install an instance without its reverse
			# index.  Real registered instances always retain the exact bin keys.
			bin_keys = list(contact_bins)
		for bin_key in bin_keys:
			members = contact_bins.get(bin_key)
			if members is None:
				continue
			members.discard(isolated_identity)
			if not members:
				contact_bins.pop(bin_key, None)
	for active_identity in list(active):
		if (active_identity[0] == chunk_id and
				(identity is None or active_identity == identity)):
			active.pop(active_identity, None)
	pending = globals().get('g_offh_destr_pending', {})
	for key in list(pending):
		if (key[0] == chunk_id and
				(identity is None or key[:2] == identity)):
			pending.pop(key, None)
	speculative = globals().get('g_offh_destr_speculative', set())
	for key in list(speculative):
		if (key[0] == chunk_id and
				(identity is None or key[:2] == identity)):
			speculative.discard(key)
	globals().get('g_offh_destr_broken_cache', {}).pop(chunk_id, None)
	if identity is None:
		# A chunk-level drop can precede a reload with different native content,
		# so forget both the list snapshot and its incremental alignment.  A
		# single isolated slot does not change that chunk-wide evidence.
		_invalidate_chunk_native_names_1513(chunk_id)
		state = globals().get('g_offh_tree_state')
		if isinstance(state, dict):
			state.get('chunks', {}).pop(chunk_id, None)
	_bump_spatial_revision_1513()


def _log_destructible_validation_1513(
		failure_type, action, chunk_id, item_index=None, detail=None):
	"""Emit one bounded sample per validation type and battle."""
	chunk_id = int(chunk_id)
	if item_index is not None:
		item_index = int(item_index)
	# A systemic offset can affect hundreds of slots at once.  Preserve the
	# exact runtime action, but report only the first sample of each failure type.
	key = str(failure_type)
	logged = globals().setdefault('g_offh_destr_isolation_logs', set())
	if key in logged:
		return
	if len(logged) >= _ISOLATION_LOG_TYPE_LIMIT:
		if globals().get('g_offh_destr_isolation_log_capped'):
			return
		globals()['g_offh_destr_isolation_log_capped'] = True
		try:
			import sys
			sys.stdout.write(
				'[Offline LAN 0.9.22] DESTR validation logs capped '
				'limit=%d additional_types=suppressed_for_battle\n' %
				_ISOLATION_LOG_TYPE_LIMIT)
		except Exception:
			pass
		return
	logged.add(key)
	parts = [
		'[Offline LAN 0.9.22] DESTR %s' % action,
		'type=%s' % failure_type,
		'scope=%s' % ('chunk' if item_index is None else 'slot'),
		'chunk=%s' % chunk_id,
	]
	if item_index is not None:
		parts.append('item=%s' % item_index)
	parts.append('map=%s' % (
		(_destructible_catalog or {}).get('map') or 'unknown'))
	parts.append('repeats=suppressed_for_battle')
	if detail is not None:
		try:
			text = str(detail).replace('\n', '?').replace('\r', '?')[:240]
		except Exception:
			text = 'unavailable'
		parts.append('detail=%s' % text)
	try:
		import sys
		sys.stdout.write('%s\n' % ' '.join(parts))
	except Exception:
		# Runtime handling is authoritative; the log stream is observational.
		pass


def _isolate_destructible_1513(
		failure_type, chunk_id, item_index=None, detail=None):
	"""Quarantine one unsafe slot/chunk and emit one bounded English line."""
	chunk_id = int(chunk_id)
	if item_index is None:
		globals().setdefault(
			'g_offh_destr_isolated_chunks', set()).add(chunk_id)
	else:
		item_index = int(item_index)
		globals().setdefault(
			'g_offh_destr_isolated_slots', set()).add(
				(chunk_id, item_index))
	_drop_isolated_destructible_1513(chunk_id, item_index)
	_log_destructible_validation_1513(
		failure_type, 'isolated', chunk_id, item_index, detail)


def _native_chunk_destructible_count_1513(manager, chunk_id):
	"""Read the count written by ``game.wg_onChunkLoad`` on pinned #1513.

	``wg_getChunkDestrFilenames`` is not a slot-count API.  Its ``0x006b1a10``
	loop appends one entry per handled item and nothing at all for an item that
	is unresolved, owns no name handler or yields a NULL name pointer.  A
	non-NULL pointer may still yield an empty Python string, so the list length
	is a lower bound on the item count and alone says nothing about which indices
	exist.
	The manager's private map is populated from the engine callback's
	``numDestructibles`` argument and is the only exact streamed-slot boundary
	available to Python.
	"""
	loaded = getattr(
		manager, '_DestructiblesManager__loadedChunkIDs', None)
	if not isinstance(loaded, dict):
		_isolate_destructible_1513(
			'native_count_abi', chunk_id,
			detail='manager loaded-count map is unavailable')
		return None
	chunk_id = int(chunk_id)
	if chunk_id not in loaded:
		return None
	count = loaded[chunk_id]
	if (isinstance(count, bool) or not isinstance(count, _INTEGER_TYPES) or
			count < 0):
		_isolate_destructible_1513(
			'native_count_value', chunk_id, detail='value=%r' % (count,))
		return None
	return int(count)


def _native_name_groups_1513(
		area_destructibles, names, ignored_items=(), positional=False):
	"""Type names and report only position-proven per-item failures."""
	try:
		cache = area_destructibles.g_cache
		query = cache.getDescByFilename
	except Exception:
		return None, 'descriptor_cache', ()
	if not callable(query):
		return None, 'descriptor_cache', ()
	ignored_items = set(int(value) for value in ignored_items)
	names_by_type = {}
	item_failures = []
	for item_index, name in enumerate(names):
		if item_index in ignored_items:
			continue
		# The pinned helper can append a non-NULL pointer to an empty C string.
		# That entry identifies no descriptor, but still occupies its emitted slot.
		if not name:
			continue
		try:
			descriptor = query(name)
		except Exception as error:
			if positional:
				item_failures.append(
					(item_index, 'descriptor_cache', error))
				continue
			return None, 'descriptor_cache', ()
		if not isinstance(descriptor, dict):
			if positional:
				item_failures.append((
					item_index, 'name_descriptor',
					'filename=%r expected descriptor dict' % name))
				continue
			return None, 'name_descriptor', ()
		descriptor_type = descriptor.get('type')
		if (isinstance(descriptor_type, bool) or
				not isinstance(descriptor_type, _INTEGER_TYPES)):
			if positional:
				item_failures.append((
					item_index, 'name_descriptor',
					'filename=%r type=%r' % (name, descriptor_type)))
				continue
			return None, 'name_descriptor', ()
		names_by_type.setdefault(int(descriptor_type), []).append(name)
	return names_by_type, 'ready', tuple(item_failures)


def _align_native_item_names_1513(
		names_by_type, items_by_type, positional_names=None,
		ignored_items=()):
	"""Finish a fully enumerated native-name alignment without guessing.

	When the helper emitted exactly one string per native item, cardinality and
	the native loop order prove that list positions are item indices.  Empty
	strings then represent anonymous slots.  A shorter list remains compacted
	and can only be reconstructed by the stricter per-type count alignment.
	"""
	if positional_names is not None:
		ignored_items = set(int(value) for value in ignored_items)
		item_types = {}
		for native_type, item_list in items_by_type.items():
			for item_index in item_list:
				item_types[item_index] = native_type
		name_types = {}
		for descriptor_type, name_list in names_by_type.items():
			for name in name_list:
				name_types[name] = descriptor_type
		mapping = {}
		item_failures = []
		for item_index, name in enumerate(positional_names):
			if item_index in ignored_items:
				continue
			if not name:
				continue
			descriptor_type = name_types.get(name)
			native_type = item_types.get(item_index)
			if descriptor_type != native_type:
				if descriptor_type is not None:
					item_failures.append((
						item_index, 'name_type_mismatch',
						'name=%r descriptor_type=%s native_type=%s' % (
							name, descriptor_type, native_type)))
				continue
			mapping[item_index] = name
		if item_failures:
			return mapping, 'partial', (), tuple(item_failures)
		return mapping, 'exact', (), ()
	mapping = {}
	anomalous = []
	for native_type in sorted(names_by_type):
		if native_type not in items_by_type:
			# A named type with no live item of that type cannot be placed.
			anomalous.append(native_type)
	for native_type in sorted(items_by_type):
		item_list = items_by_type[native_type]
		name_list = names_by_type.get(native_type) or ()
		if len(name_list) == len(item_list):
			mapping.update(zip(item_list, name_list))
			continue
		if name_list:
			# Some but not all items of this type are named, so this type's
			# compaction cannot be reconstructed.
			anomalous.append(native_type)
	if anomalous:
		return mapping, 'partial', tuple(sorted(set(anomalous))), ()
	return mapping, 'exact', (), ()


def _finish_native_item_name_alignment_1513(
		entry, positional_names, chunk_id):
	"""Consume exact positional failures without weakening compacted checks."""
	mapping, status, anomalous, item_failures = (
		_align_native_item_names_1513(
			entry['names_by_type'], entry['items_by_type'], positional_names,
			entry['ignored_items']))
	for item_index, failure_type, detail in item_failures:
		entry['ignored_items'].add(item_index)
		_isolate_destructible_1513(
			failure_type, chunk_id, item_index, detail=detail)
	if item_failures:
		return mapping, 'exact', ()
	return mapping, status, anomalous


def _item_name_query_allowance_1513(bigworld, work_key, requested):
	"""Take native-name probes from one battle-local render-tick budget.

	All callers that observe the same ``BigWorld.time()`` value share one
	allowance bucket.  Whether the pinned client keeps that value stable across
	every Python callback in one rendered frame remains a Windows measurement
	boundary.  Unit stubs without that clock can set
	``_offh_item_name_budget_tick`` explicitly; otherwise the stub object itself
	is one fixed synthetic tick and an incomplete alignment safely stays pending.
	"""
	requested = max(0, int(requested))
	if requested <= 0:
		return 0
	clock = getattr(bigworld, 'time', None)
	stamp = None
	if callable(clock):
		try:
			value = clock()
			if (not isinstance(value, bool) and
					isinstance(value, _INTEGER_TYPES + (float,))):
				numeric_value = float(value)
				if numeric_value == numeric_value:
					stamp = ('time', numeric_value)
		except Exception:
			pass
	if stamp is None:
		stamp = ('explicit', getattr(
			bigworld, '_offh_item_name_budget_tick', id(bigworld)))
	state = globals().setdefault('g_offh_destr_item_name_budget', {})
	# Battle reset clears this state.  Do not key the allowance by caller-supplied
	# space ID: alternating IDs in one tick must not replenish the shared pool.
	if state.get('stamp') != stamp:
		state['stamp'] = stamp
		state['tick_serial'] = int(state.get('tick_serial', 0)) + 1
		state['remaining'] = _ITEM_NAME_QUERY_BUDGET
	work_key = (int(work_key[0]), int(work_key[1]))
	focus = state.get('focus')
	if focus is not None and focus != work_key:
		# Preserve the current incremental job across caller order changes.  If
		# it has not been seen for a complete intervening tick, release it so an
		# unloaded/abandoned chunk cannot block the active working set forever.
		last_seen = int(state.get('focus_last_seen', 0))
		if int(state['tick_serial']) - last_seen <= 1:
			return 0
		state['focus'] = None
		focus = None
	if focus is None:
		state['focus'] = work_key
	state['focus_last_seen'] = int(state['tick_serial'])
	allowance = min(requested, state['remaining'])
	state['remaining'] -= allowance
	return allowance


def _release_item_name_query_focus_1513(space_id, chunk_id):
	state = globals().get('g_offh_destr_item_name_budget', {})
	if state.get('focus') == (int(space_id), int(chunk_id)):
		state.pop('focus', None)
		state.pop('focus_last_seen', None)


def _release_item_name_query_focus_1513_for_chunk(chunk_id):
	"""Release an alignment job when chunk-wide state is discarded."""
	state = globals().get('g_offh_destr_item_name_budget', {})
	focus = state.get('focus')
	if focus is not None and focus[1] == int(chunk_id):
		state.pop('focus', None)
		state.pop('focus_last_seen', None)


def _invalidate_chunk_native_names_1513(chunk_id):
	"""Forget cached native-name evidence after unload or native mutation."""
	chunk_id = int(chunk_id)
	for cache_name in ('g_offh_destr_item_names',
			'g_offh_destr_native_name_lists'):
		cache = globals().get(cache_name, {})
		for key in list(cache):
			if key[1] == chunk_id:
				cache.pop(key, None)
	_release_item_name_query_focus_1513_for_chunk(chunk_id)


def _touch_item_name_cache_entry_1513(entry):
	"""Stamp one alignment entry for deterministic battle-local LRU."""
	serial = int(globals().get('g_offh_destr_item_name_cache_serial', 0)) + 1
	globals()['g_offh_destr_item_name_cache_serial'] = serial
	entry['last_access'] = serial


def _item_name_cache_victim_1513(cache):
	"""Choose completed LRU first, then the oldest abandoned partial job."""
	focus = globals().get('g_offh_destr_item_name_budget', {}).get('focus')
	eligible = [(key, entry) for key, entry in cache.items()
		if key != focus]
	if not eligible:
		return None
	completed = [(key, entry) for key, entry in eligible
		if entry.get('result') is not None]
	candidates = completed or eligible
	return min(candidates,
		key=lambda value: (int(value[1].get('last_access', 0)), value[0]))[0]


def _chunk_item_names_1513(bigworld, area_destructibles, space_id, chunk_id,
		native_count, names):
	"""Incrementally rebuild one chunk's exact per-item native filenames.

	Exact pinned #1513 contract, read from ``WorldOfTanks.exe`` (x86 PE
	timestamp ``0x5a6edca4``, image size ``0x206a000``, PE checksum
	``0x019a5229`` - the same main module as the retained termination dumps):

	* ``wg_getChunkDestrFilenames`` (``0x006b1a10``) walks item indices
	  ``0 .. numDestructibles(chunk) - 1`` in order and appends one name per
	  item only when the item resolves, its native type owns a name handler,
	  and that handler returns a non-NULL pointer.  The pointer may address an
	  empty C string, which is appended as ``''``.  An unresolved item, missing
	  handler or NULL pointer appends nothing, so the returned list may be
	  compacted in item order.  Its positions are item indices only when its
	  length equals the exact native item count.
	* ``wg_getDestructibleEffectCategory(space, chunk, item, -1)``
	  (``0x006b1f10``) resolves the same item through the same provider entry
	  (vtable ``+0x10``) that the name loop and ``wg_getDestructibleMatrix``
	  (``0x006b2a90`` -> ``0x006b3f90``) use, so all three share one item index
	  space.  It fails for an unresolved item and returns ``-1`` through
	  ``or eax, 0xffffffff`` for a resolved item whose native type owns no
	  handler, and otherwise returns that item's native destructible type.
	* ``wg_getDestructibleFilename`` (``0x006b2580``) is deliberately never
	  used as a per-item probe.  For a resolved item with no type handler it
	  reaches ``PyString_FromString(NULL)`` and faults natively, and a native
	  access violation is not a catchable Python failure.

	The full-width positional proof and shorter-list type-count alignment are
	valid only after every resolvable native item has been typed.  Advance that
	enumeration from one shared render-tick budget and cache its progress; until
	it completes, callers receive
	``pending_alignment`` and must keep the chunk solid.  Unknown descriptors
	or malformed categories are terminal evidence failures: full-width position
	proof contains them to one item, while a compacted list stays unsafe as a
	whole.  Neither is converted into an unnamed item.  A completed compacted
	count mismatch is likewise terminal.  A resolver exception is the exact
	native loop's unnamed case, but that live slot is quarantined so no later
	native matrix/effect/destroy query can touch it.
	"""
	cache = globals().setdefault('g_offh_destr_item_names', {})
	key = (int(space_id), int(chunk_id))
	# The exact name tuple is part of the key: a mid-battle destruction can
	# legally remove an item from the compacted list, and the mapping must be
	# re-derived rather than reused from a superseded chunk state.
	fingerprint = (int(native_count), tuple(names))
	entry = cache.get(key)
	if entry is not None and entry['fingerprint'] != fingerprint:
		cache.pop(key, None)
		entry = None
	if entry is not None and entry['result'] is not None:
		_touch_item_name_cache_entry_1513(entry)
		return entry['result']
	if entry is None:
		query_count = _item_name_query_allowance_1513(
			bigworld, key, int(native_count))
		if native_count and query_count <= 0:
			return None, 'pending_alignment', ()
		if len(cache) >= _ITEM_NAME_CHUNK_CACHE_LIMIT:
			# Evict only after this job owns positive shared budget.  Otherwise a
			# zero-budget caller could churn useful mappings without making progress.
			# Completed mappings are cheapest to replace; when all entries are partial,
			# evict the least-recently-used non-focus entry so abandoned work cannot
			# permanently starve a newly active chunk.
			victim = _item_name_cache_victim_1513(cache)
			if victim is None:
				return None, 'pending_alignment', ()
			cache.pop(victim, None)
		full_width = len(names) == int(native_count)
		ignored_items = set()
		if full_width:
			ignored_items = set(item_index for chunk, item_index in
				globals().get('g_offh_destr_isolated_slots', ())
				if int(chunk) == int(chunk_id))
		names_by_type, status, item_failures = _native_name_groups_1513(
			area_destructibles, names, ignored_items, full_width)
		for item_index, failure_type, detail in item_failures:
			ignored_items.add(item_index)
			_isolate_destructible_1513(
				failure_type, chunk_id, item_index, detail=detail)
		if names_by_type is None:
			entry = {
				'fingerprint': fingerprint,
				'result': (None, status, ()),
			}
		else:
			entry = {
				'fingerprint': fingerprint,
				'names_by_type': names_by_type,
				'items_by_type': {},
				'ignored_items': ignored_items,
				'next_item': 0,
				'result': None,
			}
		_touch_item_name_cache_entry_1513(entry)
		cache[key] = entry
	else:
		_touch_item_name_cache_entry_1513(entry)
		query_count = _item_name_query_allowance_1513(
			bigworld, key, int(native_count) - entry['next_item'])
	if entry['result'] is not None:
		_release_item_name_query_focus_1513(space_id, chunk_id)
		return entry['result']
	if entry['next_item'] >= int(native_count):
		entry['result'] = _finish_native_item_name_alignment_1513(
			entry, names if len(names) == int(native_count) else None,
			chunk_id)
		_release_item_name_query_focus_1513(space_id, chunk_id)
		return entry['result']
	query = getattr(bigworld, 'wg_getDestructibleEffectCategory', None)
	if not callable(query):
		entry['result'] = (None, 'category_abi', ())
		_release_item_name_query_focus_1513(space_id, chunk_id)
		return entry['result']
	end_item = entry['next_item'] + query_count
	for item_index in range(entry['next_item'], end_item):
		identity = (int(chunk_id), int(item_index))
		if _destructible_isolated_1513(*identity):
			if identity in globals().get(
					'g_offh_destr_name_unresolved_slots', ()):
				# The first null-safe resolver query already proved that the native
				# name loop omits this slot.  Preserve that exact evidence across
				# mapping eviction without ever touching the quarantined slot again.
				continue
			if len(names) == int(native_count):
				# A full-width name list already proves every remaining slot by
				# position.  A catalog/matrix/descriptor failure for one exact
				# slot must stay slot-local when this mapping is rebuilt after a
				# neighbouring item is destroyed; do not turn it into a chunk-wide
				# outage.  The ignored slot itself remains quarantined everywhere.
				entry['ignored_items'].add(item_index)
				continue
			entry['result'] = (None, 'isolated_item', ())
			_release_item_name_query_focus_1513(space_id, chunk_id)
			return entry['result']
		try:
			native_type = query(space_id, chunk_id, item_index, -1)
		except Exception as error:
			# The native name loop resolves through the same provider and omits
			# this item.  Preserve that alignment fact, but quarantine the live
			# slot so no later matrix/effect/destroy call can touch it.
			globals().setdefault(
				'g_offh_destr_name_unresolved_slots', set()).add(identity)
			_isolate_destructible_1513(
				'name_item_unresolved', chunk_id, item_index, detail=error)
			if len(names) == int(native_count):
				entry['ignored_items'].add(item_index)
			continue
		if (isinstance(native_type, bool) or
				not isinstance(native_type, _INTEGER_TYPES)):
			if len(names) == int(native_count):
				entry['ignored_items'].add(item_index)
				_isolate_destructible_1513(
					'category_abi', chunk_id, item_index,
					detail='native category is not an integer')
				continue
			entry['result'] = (None, 'category_abi', ())
			_release_item_name_query_focus_1513(space_id, chunk_id)
			return entry['result']
		if native_type == -1:
			continue
		entry['items_by_type'].setdefault(
			int(native_type), []).append(item_index)
	entry['next_item'] = end_item
	if end_item < int(native_count):
		return None, 'pending_alignment', ()
	entry['result'] = _finish_native_item_name_alignment_1513(
		entry, names if len(names) == int(native_count) else None,
		chunk_id)
	_release_item_name_query_focus_1513(space_id, chunk_id)
	return entry['result']


def _chunk_native_name_list_1513(bigworld, space_id, chunk_id, native_count):
	"""Read and validate one chunk's possibly compacted native name list.

	The native helper itself walks the whole chunk, so retain its validated
	snapshot for all scanners and streamed-shot callers until the chunk unloads
	or a known native mutation invalidates it.  Returns
	``(names, status)`` with ``'ready'``, ``'pending'`` at a legal streaming
	boundary, or an isolating reason with ``None``.  A malformed list is a
	chunk-level ABI violation, because the engine's own name loop cannot append
	a non-string or more entries than the chunk has native items.  An empty
	string is legal when a name handler returns a non-NULL pointer to ``'\0'``.
	"""
	key = (int(space_id), int(chunk_id))
	cache = globals().setdefault('g_offh_destr_native_name_lists', {})
	entry = cache.get(key)
	if entry is not None:
		if entry['native_count'] == int(native_count):
			_touch_item_name_cache_entry_1513(entry)
			return entry['names'], 'ready'
		cache.pop(key, None)
	try:
		names = bigworld.wg_getChunkDestrFilenames(space_id, chunk_id)
	except Exception as error:
		_isolate_destructible_1513(
			'filename_query', chunk_id, detail=error)
		return None, 'filename_query'
	if names is None:
		return None, 'pending'
	if not isinstance(names, (list, tuple)):
		_isolate_destructible_1513(
			'filename_payload', chunk_id,
			detail='expected list or tuple')
		return None, 'filename_payload'
	if len(names) > native_count:
		_isolate_destructible_1513(
			'filename_prefix', chunk_id,
			detail='names=%s count=%s' % (len(names), native_count))
		return None, 'filename_prefix'
	for name in names:
		if not isinstance(name, _STRING_TYPES):
			_isolate_destructible_1513(
				'filename_payload', chunk_id,
				detail='names=%s count=%s entry=%r' % (
					len(names), native_count, name))
			return None, 'name_abi'
	names = tuple(names)
	if len(cache) >= _ITEM_NAME_CHUNK_CACHE_LIMIT:
		victim = min(cache.items(), key=lambda value: (
			int(value[1].get('last_access', 0)), value[0]))[0]
		cache.pop(victim, None)
	entry = {'native_count': int(native_count), 'names': names}
	_touch_item_name_cache_entry_1513(entry)
	cache[key] = entry
	return names, 'ready'


def _chunk_native_names_1513(bigworld, area_destructibles, space_id, chunk_id,
		native_count, names):
	"""Align one chunk's validated name list to its native item indices.

	All calls and chunks share at most ``_ITEM_NAME_QUERY_BUDGET`` native category
	queries per render tick.  ``pending_alignment`` is retryable.  A shorter
	compacted list keeps every completed evidence failure chunk-wide because it
	cannot identify which slot owns contradictory name evidence.  A full-width
	list preserves slots, including legal empty strings, so descriptor, category,
	or type failures are contained to their exact item.
	"""
	mapping, status, anomalous = _chunk_item_names_1513(
		bigworld, area_destructibles, space_id, chunk_id, native_count, names)
	if status == 'pending_alignment':
		return None, status
	if mapping is None or anomalous or status != 'exact':
		_isolate_destructible_1513(
			'name_alignment', chunk_id,
			detail='status=%s types=%r names=%s count=%s' % (
				status, anomalous, len(names), native_count))
		return None, status
	return mapping, status


def resolve_native_item_name_1513(space_id, chunk_id, item_index):
	"""Return ``(status, filename)`` for one native slot on pinned #1513.

	``'exact'`` carries this item's own native filename, or ``None`` when the
	item owns no name.  ``'pending'`` is the legal streaming boundary.  Every
	other status means no per-item name evidence exists for this chunk.
	"""
	import AreaDestructibles
	import BigWorld
	chunk_id = int(chunk_id)
	item_index = int(item_index)
	if _destructible_isolated_1513(chunk_id, item_index):
		return 'invalid', None
	manager = getattr(AreaDestructibles, 'g_destructiblesManager', None)
	if manager is None:
		return 'pending', None
	try:
		manager_space_id = manager.getSpaceID()
	except Exception:
		return 'pending', None
	if manager_space_id != space_id:
		return 'pending', None
	native_count = _native_chunk_destructible_count_1513(manager, chunk_id)
	if native_count is None:
		return 'pending', None
	if item_index < 0 or item_index >= native_count:
		_isolate_destructible_1513(
			'native_count_range', chunk_id, item_index,
			detail='count=%s' % native_count)
		return 'invalid', None
	names, status = _chunk_native_name_list_1513(
		BigWorld, space_id, chunk_id, native_count)
	if names is None:
		return ('pending' if status == 'pending' else 'invalid'), None
	mapping, unused_status = _chunk_native_names_1513(
		BigWorld, AreaDestructibles, space_id, chunk_id, native_count, names)
	if unused_status == 'pending_alignment':
		return 'pending', None
	if (mapping is None or
			_destructible_isolated_1513(chunk_id, item_index)):
		return 'invalid', None
	return 'exact', mapping.get(item_index)



def _live_filename_identity_1513(area_destructibles, raw_filename,
		normalized_filename, catalog_filename, expected_kind):
	"""Classify one live/catalog filename disagreement for a native slot.

	The live name is now recovered for this exact native item rather than read
	directly out of the possibly compacted chunk list, so a disagreement is real
	evidence instead of an alignment artefact.  Classes:

	* ``none`` - this item owns no native name, so there is no evidence;
	* ``match`` - the normalized names are equal;
	* ``conflict`` - the exact normalized names differ.  Sharing only the broad
	  destructible kind does not prove an alias or identical geometry.

	An exact transform and an exact wire do not make a SpeedTree atom and a
	fragile model one item, so a conflict is never accepted.  Returns
	``(classification, live_kind)``.
	"""
	if not normalized_filename:
		return 'none', None
	if normalized_filename == catalog_filename:
		return 'match', expected_kind
	try:
		descriptor = area_destructibles.g_cache.getDescByFilename(raw_filename)
	except Exception:
		# An unreadable descriptor cache is not evidence of an alias.
		return 'conflict', None
	if not isinstance(descriptor, dict):
		return 'conflict', None
	live_kind = _catalog_kind_for_type_1513(
		area_destructibles, descriptor.get('type'))
	return 'conflict', live_kind


def set_diagnostics(enabled, writer=None):
	"""Enable bounded #1513 destructible diagnostics for measurement builds."""
	global _DIAGNOSTICS_ENABLED, _diagnostic_writer
	_DIAGNOSTICS_ENABLED = bool(enabled)
	_diagnostic_writer = writer
	globals().pop('g_offh_destr_diagnostics', None)


def _diagnostic_time_1513():
	try:
		import BigWorld
		return float(BigWorld.time())
	except (AttributeError, ImportError, TypeError, ValueError):
		return 0.0


def _diagnostic_flush_1513(now=None):
	if not _DIAGNOSTICS_ENABLED:
		return
	state = globals().get('g_offh_destr_diagnostics')
	if not state or not state['queue']:
		return
	if now is None:
		now = _diagnostic_time_1513()
	if float(now) < state['next_emit']:
		return
	line = state['queue'].pop(0)
	try:
		writer = _diagnostic_writer
		if writer is None:
			import sys
			writer = sys.stdout.write
		writer('[Offline LAN 0.9.22] DESTR %s\n' % line)
	except Exception:
		# Diagnostics are observational.  A closed stdout stream must not change
		# movement, destruction or authority state.
		state['queue'] = []
		state['disabled'] = True
		return
	state['next_emit'] = float(now) + _DIAGNOSTIC_EMIT_SECONDS


def _diagnostic_enqueue_1513(category, key, fields, now=None):
	"""Queue one bounded line; never query the engine or log per frame/slot."""
	if not _DIAGNOSTICS_ENABLED:
		return
	state = globals().setdefault('g_offh_destr_diagnostics', {
		'queue': [], 'seen_chunks': set(), 'seen_pending': set(),
		'seen_contacts': set(),
		'next_emit': 0.0, 'disabled': False,
	})
	if state.get('disabled'):
		return
	_diagnostic_flush_1513(now)
	seen_name = (
		'seen_chunks' if category == 'chunk' else
		'seen_pending' if category == 'chunk_pending' else
		'seen_contacts')
	seen = state[seen_name]
	if key in seen:
		return
	limit = (
		_DIAGNOSTIC_CHUNK_LIMIT if category == 'chunk' else
		_DIAGNOSTIC_PENDING_LIMIT if category == 'chunk_pending' else
		_DIAGNOSTIC_CONTACT_LIMIT)
	if len(seen) >= limit:
		return
	seen.add(key)
	parts = ['chunk' if category == 'chunk_pending' else category]
	for name, value in fields:
		text = str(value).replace('\n', '?').replace('\r', '?')
		parts.append('%s=%s' % (name, text))
	line = ' '.join(parts)
	if category == 'contact':
		state['queue'].insert(0, line)
	else:
		state['queue'].append(line)
	_diagnostic_flush_1513(now)


def _diagnostic_slot_1513(chunk_id, item_index):
	state = globals().get('g_offh_tree_state', {})
	registry = state.get('chunks', {}).get(int(chunk_id), {})
	return registry.get('slot_diagnostics', {}).get(int(item_index))


def _diagnostic_counts_1513(values):
	counts = {}
	for value in values:
		counts[value] = counts.get(value, 0) + 1
	return ','.join('%s:%s' % (key, counts[key]) for key in sorted(counts)) \
		or '-'


def _diagnostic_chunk_1513(chunk_id, native_count, names, item_names,
		names_status, registry):
	"""Emit one aggregate after a complete first scan of a streamed chunk."""
	if not _DIAGNOSTICS_ENABLED:
		return
	slots = registry['slot_diagnostics']
	ordered = [slots[index] for index in sorted(slots)]
	signatures = [slot['signature_state'] for slot in ordered]
	results = [slot['result'] for slot in ordered]
	effects = [slot['effect_category'] for slot in ordered
		if slot['effect_category'] != '-']
	registered = [result[len('registered_'):] for result in results
		if result.startswith('registered_')]
	_diagnostic_enqueue_1513('chunk', ('ready', int(chunk_id)), (
		('chunk', int(chunk_id)),
		('slots', int(native_count)),
		('names', len(names)),
		('named_items', -1 if item_names is None else len(item_names)),
		('names_status', str(names_status)),
		('named', sum(1 for slot in ordered if slot['raw'] == 'named')),
		('blank', sum(1 for slot in ordered if slot['raw'] == 'blank')),
		('name_mismatch', sum(
			1 for slot in ordered if slot.get('raw_mismatch'))),
		('name_conflict', sum(
			1 for slot in ordered if slot.get('raw_conflict'))),
		('v4_unique', signatures.count('unique')),
		('v4_ambig', signatures.count('ambig')),
		('v4_miss', signatures.count('miss')),
		('effects', _diagnostic_counts_1513(effects)),
		('registered', _diagnostic_counts_1513(registered)),
		('boxes', sum(slot['boxes'] for slot in ordered)),
		('rejects', _diagnostic_counts_1513(
			result for result in results
			if not result.startswith('registered_'))),
	))


def _diagnostic_chunk_pending_1513(stage, chunk_id, native_count=None):
	fields = [('chunk', int(chunk_id)), ('state', stage)]
	if native_count is not None:
		fields.append(('slots', int(native_count)))
	_diagnostic_enqueue_1513(
		'chunk_pending', (stage, int(chunk_id)), fields)


def _diagnostic_contact_1513(stage, chunk_id=None, item_index=None,
		point=None, fields=(), now=None):
	if not _DIAGNOSTICS_ENABLED:
		return
	if chunk_id is None or item_index is None:
		if point is None:
			cell = ('unknown',)
		else:
			cell = (int(float(point.x) // 2.0),
				int(float(point.y) // 2.0), int(float(point.z) // 2.0))
		key = (stage,) + cell
		base = [('stage', stage), ('identity', 'none'), ('cell', cell)]
	else:
		chunk_id = int(chunk_id)
		item_index = int(item_index)
		key = (stage, chunk_id, item_index)
		base = [('stage', stage), ('chunk', chunk_id), ('item', item_index)]
		slot = _diagnostic_slot_1513(chunk_id, item_index)
		if slot is not None:
			base.extend((
				('raw', slot.get('raw', '-')),
				('sig', slot.get('signature_state', '-')),
				('effect', slot.get('effect_category', '-')),
				('registered', slot.get('result', '-')),
				('boxes', slot.get('boxes', 0)),
			))
	_diagnostic_enqueue_1513('contact', key, base + list(fields), now)


def _diagnostic_static_recast_1513(cleared, now=None):
	last = globals().pop('g_offh_destr_diag_last_static', None)
	if last is None:
		return
	chunk_id, item_index, fields = last
	_diagnostic_contact_1513(
		'static_recast_clear' if cleared else 'static_recast_blocked',
		chunk_id, item_index, fields=fields, now=now)


def _clear_runtime_registry():
	for name in ('g_offh_destr_seen', 'g_offh_destr_nodesc',
			'g_offh_tree_state', 'g_offh_destr_ordered',
			'g_offh_destr_chunks', 'g_offh_destr_instances',
			'g_offh_destr_contact_bins', 'g_offh_destr_pending',
			'g_offh_destr_speculative',
			'g_offh_destr_catalog_published',
			'g_offh_destr_catalog_publish_pending',
			'g_offh_destr_falling_active', 'g_offh_destr_ground_skips',
			'g_offh_destr_broken_cache',
			'g_offh_destr_item_names',
			'g_offh_destr_native_name_lists',
			'g_offh_destr_item_name_budget',
			'g_offh_destr_item_name_cache_serial',
			'g_offh_destr_isolated_chunks',
			'g_offh_destr_isolated_slots',
			'g_offh_destr_name_unresolved_slots',
			'g_offh_destr_isolation_logs',
			'g_offh_destr_isolation_log_capped',
			'g_offh_destr_diagnostics', 'g_offh_destr_diag_last_static',
			'g_offh_destr_spatial_revision',
			'g_offh_destr_empty_contact_receipts',
			'g_offh_destr_empty_proximity_receipts',
			'g_offh_destr_receipt_stats',
			'g_offh_destr_runtime_space'):
		globals().pop(name, None)


def _baked_world_boxes_1513(record, signature, box_index, quantization):
	"""Rebuild one catalog instance OBB from its pinned world signature."""
	scale = float(quantization)
	origin = tuple(float(value) / scale for value in signature[:3])
	basis = tuple(tuple(float(signature[3 + axis * 3 + component]) / scale
		for component in range(3)) for axis in range(3))
	boxes = record['boxes']
	if record['kind'] != 'structure':
		if box_index is None or box_index < 0 or box_index >= len(boxes):
			raise ValueError('baked destructible box index is invalid')
		boxes = (boxes[box_index],)
	result = []
	for box in boxes:
		local_center = ((box[0] + box[3]) * 0.5,
			(box[1] + box[4]) * 0.5,
			(box[2] + box[5]) * 0.5)
		center = tuple(origin[component] + sum(
			basis[axis][component] * local_center[axis]
			for axis in range(3)) for component in range(3))
		half_sizes = ((box[3] - box[0]) * 0.5,
			(box[4] - box[1]) * 0.5,
			(box[5] - box[2]) * 0.5)
		half_axes = tuple(tuple(
			basis[axis][component] * half_sizes[axis]
			for component in range(3)) for axis in range(3))
		volume = abs(_vector_dot(
			half_axes[0], _vector_cross(half_axes[1], half_axes[2])))
		if volume <= 1.0e-9:
			# Older unit/donation fixtures did not promise a usable transform.
			# Keep their identity rows, but never expose invalid geometry to the
			# projectile broad phase.
			return ()
		result.append((center, half_axes, box[6]))
	return tuple(result)


def _baked_quantization_margin_1513(record, box_index, quantization):
	"""Bound world-space error introduced by the locator signature grid."""
	import math
	boxes = record['boxes']
	if record['kind'] != 'structure':
		boxes = (boxes[box_index],)
	maximum_local_sum = max(
		max(abs(box[0]), abs(box[3])) +
		max(abs(box[1]), abs(box[4])) +
		max(abs(box[2]), abs(box[5]))
		for box in boxes)
	# Origin and every basis component are rounded independently to the
	# nearest 1/quantization.  The component error for a transformed point is
	# therefore at most half a grid step times (1 + |x| + |y| + |z|).
	# Convert that component bound to a conservative 3-D distance so both the
	# footprint bins and ray prefilter contain every possible live OBB.
	return (math.sqrt(3.0) * 0.5 / float(quantization) *
		(1.0 + maximum_local_sum))


def _baked_bin_keys_for_bounds_1513(
		minimum_x, maximum_x, minimum_z, maximum_z):
	"""Yield footprint bins without depending on Python 2-only ``xrange``."""
	import math
	minimum_bin_x = int(math.floor(minimum_x / _DESTRUCTIBLE_BIN_METRES))
	maximum_bin_x = int(math.floor(maximum_x / _DESTRUCTIBLE_BIN_METRES))
	minimum_bin_z = int(math.floor(minimum_z / _DESTRUCTIBLE_BIN_METRES))
	maximum_bin_z = int(math.floor(maximum_z / _DESTRUCTIBLE_BIN_METRES))
	bin_x = minimum_bin_x
	while bin_x <= maximum_bin_x:
		bin_z = minimum_bin_z
		while bin_z <= maximum_bin_z:
			yield bin_x, bin_z
			bin_z += 1
		bin_x += 1


def set_catalog(catalog):
	"""Install one validated per-map #1513 collider catalog.

	``reset`` intentionally preserves this immutable map input.  Battle startup
	always replaces it (or explicitly passes ``None``), while reset only drops
	the streamed native item registry.
	"""
	global _destructible_catalog
	if catalog is None:
		_destructible_catalog = None
		_clear_runtime_registry()
		return
	if not isinstance(catalog, dict):
		raise ValueError('destructible catalog root is invalid')
	try:
		quantization = int(catalog.get('locator_quantization'))
	except (TypeError, ValueError):
		raise ValueError('destructible locator quantization is invalid')
	if quantization != 1000:
		raise ValueError('destructible locator quantization is invalid')
	resources = catalog.get('resources')
	if not isinstance(resources, dict) or not resources:
		raise ValueError('destructible catalog resources are unavailable')
	prepared = {}
	max_radius = 0.0
	for filename, raw in resources.items():
		normalized = _normalized_filename(filename)
		if not normalized or normalized in prepared or not isinstance(raw, dict):
			raise ValueError('destructible catalog resource is invalid')
		kind = raw.get('kind')
		if kind not in ('fragile', 'structure', 'falling'):
			raise ValueError('destructible catalog resource kind is invalid')
		boxes = []
		for raw_box in raw.get('boxes') or ():
			if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 7:
				raise ValueError('destructible catalog box is invalid')
			try:
				values = tuple(float(value) for value in raw_box[:6])
			except (TypeError, ValueError):
				raise ValueError('destructible catalog box is invalid')
			if not (values[0] < values[3] and values[1] < values[4] and
					values[2] < values[5]):
				raise ValueError('destructible catalog box is invalid')
			mat_kind = raw_box[6]
			if kind != 'structure':
				if mat_kind is not None:
					raise ValueError(
						'non-structure catalog box has a material')
			else:
				try:
					mat_kind = int(mat_kind)
				except (TypeError, ValueError):
					raise ValueError('structure catalog material is invalid')
				if not (_STRUCTURE_MAT_KIND_MIN_1513 <= mat_kind <
						_STRUCTURE_MAT_KIND_MAX_1513):
					raise ValueError('structure catalog material is invalid')
			boxes.append(values + (mat_kind,))
			for local_x in (values[0], values[3]):
				for local_z in (values[2], values[5]):
					radius = (local_x * local_x + local_z * local_z) ** 0.5
					max_radius = max(max_radius, radius)
		if not boxes:
			raise ValueError('destructible catalog boxes are unavailable')
		locators = {}
		for row in raw.get('locators') or ():
			if (not isinstance(row, (list, tuple)) or len(row) != 13 or
					any(type(value) is not int for value in row)):
				raise ValueError('destructible instance locator is invalid')
			signature = tuple(row[:12])
			box_index = int(row[12])
			if (signature in locators or box_index < 0 or
					box_index >= len(boxes)):
				raise ValueError('destructible instance locator is invalid')
			locators[signature] = box_index
		if kind != 'structure' and len(boxes) > 1 and not locators:
			raise ValueError('ambiguous catalog has no locators')
		if (kind == 'structure' or len(boxes) == 1) and locators:
			raise ValueError('destructible catalog has unexpected locators')
		prepared[normalized] = {
			'filename': filename, 'kind': kind, 'boxes': tuple(boxes),
			'locators': locators,
		}
	try:
		catalog_version = int(catalog.get('version', 1))
	except (TypeError, ValueError):
		raise ValueError('destructible catalog version is invalid')
	if catalog_version == 3:
		raise ValueError('destructible catalog version is unsupported')
	raw_instances = catalog.get('instances')
	raw_ambiguous = catalog.get('ambiguous_instances')
	if catalog_version >= 4:
		if not isinstance(raw_instances, list) or not raw_instances:
			raise ValueError('destructible instance index is unavailable')
		if not isinstance(raw_ambiguous, list):
			raise ValueError(
				'ambiguous destructible instance index is invalid')
	else:
		raw_instances = raw_instances or ()
		raw_ambiguous = raw_ambiguous or ()
	import math
	instance_index = {}
	baked_instances = {}
	baked_shot_bins = {}
	seen_wires = set()
	for row in raw_instances:
		if (not isinstance(row, (list, tuple)) or len(row) != 17 or
				any(type(value) not in _INTEGER_TYPES
					for value in row[:12])):
			raise ValueError('destructible instance row is invalid')
		signature = tuple(row[:12])
		normalized = _normalized_filename(row[12])
		record = prepared.get(normalized)
		if record is None or signature in instance_index:
			raise ValueError('destructible instance row is invalid')
		box_index = row[13]
		if record['kind'] == 'structure':
			if box_index is not None:
				raise ValueError(
					'structure instance has a box index')
		else:
			if (type(box_index) not in _INTEGER_TYPES or box_index < 0 or
					box_index >= len(record['boxes'])):
				raise ValueError(
					'destructible instance box index is invalid')
		chunk_id, item_index, item_scale = row[14:]
		if (type(chunk_id) not in _INTEGER_TYPES or
				type(item_index) not in _INTEGER_TYPES or
				chunk_id < 0 or item_index < 0):
			raise ValueError('destructible instance wire is invalid')
		wire = (int(chunk_id), int(item_index))
		if wire in seen_wires:
			raise ValueError('destructible instance wire is duplicated')
		seen_wires.add(wire)
		try:
			item_scale = float(item_scale)
		except (TypeError, ValueError):
			raise ValueError('destructible instance scale is invalid')
		if (math.isnan(item_scale) or math.isinf(item_scale) or
				item_scale <= 0.0):
			raise ValueError('destructible instance scale is invalid')
		instance = {
			'filename': normalized, 'kind': record['kind'],
			'box_index': box_index, 'wire': wire,
			'exact_scale': item_scale,
		}
		instance_index[signature] = instance
		world_boxes = _baked_world_boxes_1513(
			record, signature, box_index, quantization)
		broad_phase_margin = _baked_quantization_margin_1513(
			record, box_index, quantization)
		baked_instances[wire] = {
			'filename': normalized,
			'descriptor_filename': record['filename'],
			'kind': record['kind'], 'boxes': world_boxes,
			'item_scale': item_scale, 'box_index': box_index,
			'signature': signature,
			'broad_phase_margin': broad_phase_margin,
		}
		for world_box in world_boxes:
			bounds = _box_xz_bounds(world_box)
			for bin_key in _baked_bin_keys_for_bounds_1513(
					bounds[0] - broad_phase_margin,
					bounds[1] + broad_phase_margin,
					bounds[2] - broad_phase_margin,
					bounds[3] + broad_phase_margin):
				baked_shot_bins.setdefault(bin_key, set()).add(wire)
	ambiguous_signatures = set()
	for row in raw_ambiguous:
		if (not isinstance(row, (list, tuple)) or len(row) != 13 or
				any(type(value) is not int for value in row[:12]) or
				not isinstance(row[12], list) or len(row[12]) < 2):
			raise ValueError(
				'ambiguous destructible instance row is invalid')
		signature = tuple(row[:12])
		if signature in instance_index or signature in ambiguous_signatures:
			raise ValueError(
				'ambiguous destructible instance row is invalid')
		for candidate in row[12]:
			if (not isinstance(candidate, (list, tuple)) or
					len(candidate) != 2 or
					_normalized_filename(candidate[0]) not in prepared):
				raise ValueError(
					'ambiguous destructible candidate is invalid')
		ambiguous_signatures.add(signature)
	_destructible_catalog = {
		'map': catalog.get('map'),
		'resources': prepared, 'quantization': quantization,
		'max_radius': max_radius, 'instances': instance_index,
		'ambiguous_instances': ambiguous_signatures,
		'has_instance_index': catalog_version >= 4,
		'baked_instances': baked_instances,
		'baked_shot_bins': baked_shot_bins,
	}
	_clear_runtime_registry()


def _destructible_bin_key(x, z):
	import math
	return (int(math.floor(float(x) / _DESTRUCTIBLE_BIN_METRES)),
		int(math.floor(float(z) / _DESTRUCTIBLE_BIN_METRES)))


def _bin_keys_for_bounds(minimum_x, maximum_x, minimum_z, maximum_z):
	import math
	minimum_bin_x = int(math.floor(minimum_x / _DESTRUCTIBLE_BIN_METRES))
	maximum_bin_x = int(math.floor(maximum_x / _DESTRUCTIBLE_BIN_METRES))
	minimum_bin_z = int(math.floor(minimum_z / _DESTRUCTIBLE_BIN_METRES))
	maximum_bin_z = int(math.floor(maximum_z / _DESTRUCTIBLE_BIN_METRES))
	for bin_x in xrange(minimum_bin_x, maximum_bin_x + 1):
		for bin_z in xrange(minimum_bin_z, maximum_bin_z + 1):
			yield bin_x, bin_z


def _bin_rectangle_signature_1513(bounds):
	"""Return the exact 8 m cell envelope covering XZ bounds."""
	minimum = _destructible_bin_key(bounds[0], bounds[2])
	maximum = _destructible_bin_key(bounds[1], bounds[3])
	return minimum[0], maximum[0], minimum[1], maximum[1]


def _spatial_revision_1513():
	return int(globals().get('g_offh_destr_spatial_revision', 0))


def _receipt_stat_1513(name, amount=1):
	stats = globals().setdefault('g_offh_destr_receipt_stats', {})
	stats[name] = int(stats.get(name, 0)) + int(amount)


def _receipt_prefix_1513(name):
	return ('contact' if name == 'g_offh_destr_empty_contact_receipts'
		else 'proximity')


def _bump_spatial_revision_1513():
	"""Invalidate receipts after any exact spatial-index mutation."""
	revision = _spatial_revision_1513() + 1
	globals()['g_offh_destr_spatial_revision'] = revision
	_receipt_stat_1513('spatial_invalidations')
	for cache_name, prefix in (
			('g_offh_destr_empty_contact_receipts', 'contact'),
			('g_offh_destr_empty_proximity_receipts', 'proximity')):
		state = globals().get(cache_name)
		if isinstance(state, dict):
			_receipt_stat_1513(
				'%s_invalidated' % prefix,
				len(state.get('entries', {})))
	globals().pop('g_offh_destr_empty_contact_receipts', None)
	globals().pop('g_offh_destr_empty_proximity_receipts', None)
	return revision


def _receipt_cache_get_1513(name, key):
	state = globals().get(name)
	if not isinstance(state, dict):
		_receipt_stat_1513('%s_misses' % _receipt_prefix_1513(name))
		return None
	value = state.get('entries', {}).get(key)
	_receipt_stat_1513('%s_%s' % (
		_receipt_prefix_1513(name), 'hits' if value is not None else 'misses'))
	return value


def _receipt_cache_put_1513(name, key, value, limit):
	"""Store one bounded battle-local receipt without OrderedDict reliance."""
	state = globals().setdefault(name, {'entries': {}, 'order': []})
	entries = state['entries']
	order = state['order']
	if key in entries:
		entries[key] = value
		return
	while len(order) >= int(limit):
		entries.pop(order.pop(0), None)
		_receipt_stat_1513('%s_evictions' % _receipt_prefix_1513(name))
	entries[key] = value
	order.append(key)
	_receipt_stat_1513('%s_stores' % _receipt_prefix_1513(name))


def _loaded_chunk_signature_1513(manager, chunk_ids):
	"""Return exact streamed counts, or ``None`` while any chunk is pending."""
	loaded = getattr(
		manager, '_DestructiblesManager__loadedChunkIDs', None)
	if not isinstance(loaded, dict):
		return None
	result = []
	for chunk_id in sorted(chunk_ids):
		if chunk_id not in loaded:
			return None
		count = loaded[chunk_id]
		if (isinstance(count, bool) or not isinstance(count, _INTEGER_TYPES) or
				count < 0):
			return None
		result.append((int(chunk_id), int(count)))
	return tuple(result)


def _proximity_receipt_key_1513(spaceID, current_chunk, pos, vehicle_box):
	origin_bounds = (float(pos.x) - _DESTRUCTIBLE_ORIGIN_RADIUS,
		float(pos.x) + _DESTRUCTIBLE_ORIGIN_RADIUS,
		float(pos.z) - _DESTRUCTIBLE_ORIGIN_RADIUS,
		float(pos.z) + _DESTRUCTIBLE_ORIGIN_RADIUS)
	return (int(spaceID), int(current_chunk),
		_bin_rectangle_signature_1513(origin_bounds),
		_bin_rectangle_signature_1513(_box_xz_bounds(vehicle_box)))


def _empty_proximity_receipt_valid_1513(
		key, manager, chunk_registry):
	"""Reuse only a complete, unchanged streamed empty-cell receipt."""
	state = globals().get('g_offh_destr_empty_proximity_receipts')
	entry = (state.get('entries', {}).get(key)
		if isinstance(state, dict) else None)
	valid = (
		not globals().get('g_offh_destr_falling_active') and
		isinstance(entry, dict) and
		entry.get('revision') == _spatial_revision_1513())
	chunk_ids = entry.get('chunks') if valid else None
	valid = (valid and isinstance(chunk_ids, tuple) and not any(
		chunk_id not in chunk_registry for chunk_id in chunk_ids))
	valid = (valid and
		_loaded_chunk_signature_1513(manager, chunk_ids) ==
		entry.get('stream_signature'))
	_receipt_stat_1513(
		'proximity_hits' if valid else 'proximity_misses')
	return bool(valid)


def _nearby_destructibles(registry, pos, vehicle_box=None):
	"""Yield origin items and catalog-footprint items near the hull."""
	origin_bounds = (float(pos.x) - _DESTRUCTIBLE_ORIGIN_RADIUS,
		float(pos.x) + _DESTRUCTIBLE_ORIGIN_RADIUS,
		float(pos.z) - _DESTRUCTIBLE_ORIGIN_RADIUS,
		float(pos.z) + _DESTRUCTIBLE_ORIGIN_RADIUS)
	extended_bounds = (_box_xz_bounds(vehicle_box) if vehicle_box is not None
		else origin_bounds)
	seen = set()
	for bins, bounds in ((registry['bins'], origin_bounds),
			(registry['extended_bins'], extended_bounds)):
		for bin_key in _bin_keys_for_bounds(*bounds):
			for item in bins.get(bin_key, ()):
				if item[0] in seen:
					continue
				seen.add(item[0])
				yield item

def _symmetric_quantize(value, scale):
	import math
	scaled = float(value) * scale
	if scaled >= 0.0:
		return int(math.floor(scaled + 0.5))
	return int(math.ceil(scaled - 0.5))


def _matrix_point(matrix, math_module, x, y, z, chunk_translation):
	point = matrix.applyPoint(math_module.Vector3(x, y, z))
	return (float(chunk_translation.x + point.x),
		float(chunk_translation.y + point.y),
		float(chunk_translation.z + point.z))


def _locator_signature(matrix, chunk_translation, math_module, scale):
	origin = _matrix_point(
		matrix, math_module, 0.0, 0.0, 0.0, chunk_translation)
	basis = []
	for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
			(0.0, 0.0, 1.0)):
		vector = matrix.applyVector(math_module.Vector3(*axis))
		basis.extend((vector.x, vector.y, vector.z))
	return tuple(_symmetric_quantize(value, scale)
		for value in origin + tuple(basis))


def _vector_dot(left, right):
	return (left[0] * right[0] + left[1] * right[1] +
		left[2] * right[2])


def _vector_cross(left, right):
	return (left[1] * right[2] - left[2] * right[1],
		left[2] * right[0] - left[0] * right[2],
		left[0] * right[1] - left[1] * right[0])


def _matrix_vector(matrix, math_module, x, y, z):
	vector = matrix.applyVector(math_module.Vector3(x, y, z))
	return float(vector.x), float(vector.y), float(vector.z)


def _matrix_item_scale_1513(matrix, math_module):
	"""Return the exact scale convention used by stock AreaDestructibles."""
	import math
	y_axis = _matrix_vector(matrix, math_module, 0.0, 1.0, 0.0)
	item_scale = _vector_dot(y_axis, y_axis) ** 0.5
	if (item_scale <= 0.0 or math.isinf(item_scale) or
			math.isnan(item_scale)):
		raise RuntimeError('#1513 destructible item scale is invalid')
	return item_scale


def _box_xz_bounds(box):
	center, half_axes = box[:2]
	radius_x = sum(abs(axis[0]) for axis in half_axes)
	radius_z = sum(abs(axis[2]) for axis in half_axes)
	return (center[0] - radius_x, center[0] + radius_x,
		center[2] - radius_z, center[2] + radius_z)


def _world_catalog_boxes(record, matrix, chunk_translation, math_module,
		instance_box_index=None):
	boxes = record['boxes']
	if record['kind'] != 'structure' and instance_box_index is not None:
		boxes = (boxes[instance_box_index],)
	elif record['kind'] != 'structure' and len(boxes) > 1:
		signature = _locator_signature(matrix, chunk_translation, math_module,
			_destructible_catalog['quantization'])
		box_index = record['locators'].get(signature)
		if box_index is None:
			return ()
		boxes = (boxes[box_index],)
	result = []
	for box in boxes:
		center = _matrix_point(matrix, math_module,
			(box[0] + box[3]) * 0.5, (box[1] + box[4]) * 0.5,
			(box[2] + box[5]) * 0.5, chunk_translation)
		half_axes = (
			_matrix_vector(matrix, math_module,
				(box[3] - box[0]) * 0.5, 0.0, 0.0),
			_matrix_vector(matrix, math_module,
				0.0, (box[4] - box[1]) * 0.5, 0.0),
			_matrix_vector(matrix, math_module,
				0.0, 0.0, (box[5] - box[2]) * 0.5))
		volume = abs(_vector_dot(
			half_axes[0], _vector_cross(half_axes[1], half_axes[2])))
		if volume <= 1.0e-9:
			continue
		result.append((center, half_axes, box[6]))
	return tuple(result)


def _catalog_kind_for_type_1513(area_destructibles, destr_type):
	if destr_type == getattr(
			area_destructibles, 'DESTR_TYPE_FALLING_ATOM', None):
		return 'falling'
	if destr_type == getattr(
			area_destructibles, 'DESTR_TYPE_FRAGILE', None):
		return 'fragile'
	if destr_type == getattr(
			area_destructibles, 'DESTR_TYPE_STRUCTURE', None):
		return 'structure'
	return None


def _catalog_instance_for_matrix_1513(matrix, chunk_translation,
		math_module):
	if (_destructible_catalog is None or
			not _destructible_catalog.get('has_instance_index')):
		return None, None
	signature = _locator_signature(
		matrix, chunk_translation, math_module,
		_destructible_catalog['quantization'])
	if signature in _destructible_catalog['ambiguous_instances']:
		return signature, None
	return signature, _destructible_catalog['instances'].get(signature)


def _box_face_axes(half_axes):
	return (_vector_cross(half_axes[1], half_axes[2]),
		_vector_cross(half_axes[2], half_axes[0]),
		_vector_cross(half_axes[0], half_axes[1]))


def _boxes_intersect(left, right):
	left_center, left_half_axes = left[:2]
	right_center, right_half_axes = right[:2]
	delta = tuple(right_center[index] - left_center[index]
		for index in range(3))
	# A translated OBB is a four-generator zonotope: its three original
	# half-axes plus half of the translation.  The separating axes for two
	# zonotopes are the cross products of every generator pair.  Ordinary
	# three-axis OBBs therefore retain the same 15 SAT axes, while a swept hull
	# can preserve its real orientation without collapsing into a lossy box.
	generators = tuple(left_half_axes) + tuple(right_half_axes)
	axes = (_vector_cross(generators[left_index], generators[right_index])
		for left_index in range(len(generators))
		for right_index in range(left_index + 1, len(generators)))
	for axis in axes:
		length_squared = _vector_dot(axis, axis)
		if length_squared <= 1.0e-16:
			continue
		left_radius = sum(abs(_vector_dot(axis, half_axis))
			for half_axis in left_half_axes)
		right_radius = sum(abs(_vector_dot(axis, half_axis))
			for half_axis in right_half_axes)
		if (abs(_vector_dot(delta, axis)) > left_radius + right_radius +
				1.0e-7 * length_squared ** 0.5):
			return False
	return True


def _point_in_world_box(point, world_box):
	center, half_axes = world_box[:2]
	delta = (point.x - center[0], point.y - center[1],
		point.z - center[2])
	for axis in _box_face_axes(half_axes):
		length = _vector_dot(axis, axis) ** 0.5
		if length <= 1.0e-8:
			return False
		radius = sum(abs(_vector_dot(axis, half_axis))
			for half_axis in half_axes)
		if abs(_vector_dot(delta, axis)) > (
				radius + _CATALOG_POINT_EPSILON * length):
			return False
	return True


def _segment_world_box_interval(start, end, world_box, padding=None):
	"""Return the exact normalized ray interval inside one transformed OBB."""
	if padding is None:
		padding = _CATALOG_POINT_EPSILON
	center, half_axes = world_box[:2]
	start_delta = (start.x - center[0], start.y - center[1],
		start.z - center[2])
	end_delta = (end.x - center[0], end.y - center[1],
		end.z - center[2])
	entry = 0.0
	exit = 1.0
	for index, axis in enumerate(_box_face_axes(half_axes)):
		denominator = _vector_dot(axis, half_axes[index])
		axis_length = _vector_dot(axis, axis) ** 0.5
		if abs(denominator) <= 1.0e-12 or axis_length <= 1.0e-12:
			return None
		bound = 1.0 + float(padding) * axis_length / abs(denominator)
		start_value = _vector_dot(start_delta, axis) / denominator
		end_value = _vector_dot(end_delta, axis) / denominator
		delta = end_value - start_value
		if abs(delta) <= 1.0e-12:
			if abs(start_value) > bound:
				return None
			continue
		near = (-bound - start_value) / delta
		far = (bound - start_value) / delta
		if near > far:
			near, far = far, near
		entry = max(entry, near)
		exit = min(exit, far)
		if entry > exit:
			return None
	return entry, exit


def _instance_descriptor_filename_1513(instance):
	"""Return the case-preserved filename required by DestructiblesCache."""
	filename = instance.get('descriptor_filename')
	if filename:
		return filename
	record = (_destructible_catalog or {}).get('resources', {}).get(
		instance.get('filename'))
	return record['filename'] if record is not None else instance['filename']


def _validate_native_effect_categories_1513(
		bigworld, area_destructibles, record, descriptor,
		spaceID, chunk_id, item_index):
	"""Validate the live native kind using the exact #1513 module index.

	``destructibleModuleDestroyed`` carries the BSP ``matKind``, but stock
	``AreaDestructibles.__destroyModule`` subtracts ``NORMAL_MIN`` before
	calling the native effect-category API.  Passing the raw material kind makes
	valid structures such as Malinovka's ``mil203_MilitaryDefences01`` report
	the category for an unrelated native module.
	"""
	expected_kind = record['kind']
	module_indices = (-1,)
	if expected_kind == 'structure':
		try:
			normal_min = int(area_destructibles.
				DESTRUCTIBLE_MATKIND.NORMAL_MIN)
			normal_max = int(area_destructibles.
				DESTRUCTIBLE_MATKIND.NORMAL_MAX)
		except Exception as error:
			_isolate_destructible_1513(
				'effect_contract', chunk_id, item_index,
				detail='material range error=%s' % error)
			return False
		if (normal_min != _STRUCTURE_MAT_KIND_MIN_1513 or
				normal_max != _STRUCTURE_MAT_KIND_MAX_1513):
			_isolate_destructible_1513(
				'effect_contract', chunk_id, item_index,
				detail='normal_range=%s..%s' % (normal_min, normal_max))
			return False
		modules = descriptor.get('modules') if isinstance(
			descriptor, dict) else None
		raw_materials = tuple(sorted(set(
			int(box[6]) for box in record['boxes'])))
		if (not isinstance(modules, dict) or any(
				raw_material < normal_min or raw_material >= normal_max or
				modules.get(raw_material) is None
				for raw_material in raw_materials)):
			_isolate_destructible_1513(
				'effect_contract', chunk_id, item_index,
				detail='descriptor_modules=%r catalog_modules=%r' % (
					tuple(sorted(modules)) if isinstance(modules, dict) else None,
					raw_materials))
			return False
		module_indices = tuple(
			raw_material - normal_min for raw_material in raw_materials)
	descriptor_type = descriptor.get('type') if isinstance(
		descriptor, dict) else None
	identity = 'catalog=%s catalog_kind=%s descriptor_type=%r' % (
		record['filename'], expected_kind, descriptor_type)
	for module_index in module_indices:
		try:
			native_type = bigworld.wg_getDestructibleEffectCategory(
				spaceID, chunk_id, item_index, module_index)
		except Exception as error:
			# The pinned entry point reports an unresolved (chunk, item, module)
			# triple as an error, so this is a real identity failure.
			_isolate_destructible_1513(
				'effect_query', chunk_id, item_index,
				detail='operation=effect_category module=%s %s error=%s' % (
					module_index, identity, error))
			return False
		if native_type == _NATIVE_EFFECT_CATEGORY_UNREGISTERED_1513:
			# The item resolved but its native effect group has no handler, so
			# this channel cannot confirm or contradict the catalog kind.  The
			# exact wire and the unique matrix signature already agreed, and
			# isolating here would hide legal #1513 destructibles.
			_log_destructible_validation_1513(
				'effect_category_unregistered', 'accepted_native_identity',
				chunk_id, item_index,
				detail='operation=effect_category module=%s %s '
					'native=-1 wire=live_validated' % (
						module_index, identity))
			continue
		if _catalog_kind_for_type_1513(
				area_destructibles, native_type) != expected_kind:
			_isolate_destructible_1513(
				'effect_category', chunk_id, item_index,
				detail='operation=effect_category module=%s %s '
					'native=%r wire=live_validated' % (
						module_index, identity, native_type))
			return False
	return True


def _stream_baked_shot_instance_1513(spaceID, identity):
	"""Validate and register one baked wire when its chunk is streamed.

	The complete catalog is safe for broad-phase geometry only.  A shell may
	reach a visible chunk before the vehicle-near scanner visits it, so admit
	the candidate only after the same live matrix, exact wire, descriptor kind
	and native effect-category checks used by the proximity registry.
	"""
	chunk_id, item_index = identity
	if _destructible_isolated_1513(chunk_id, item_index):
		return None
	instances = globals().setdefault('g_offh_destr_instances', {})
	instance = instances.get(identity)
	if instance is not None:
		return instance
	catalog = _destructible_catalog
	if catalog is None:
		return None
	baked = catalog.get('baked_instances', {}).get(identity)
	if baked is None:
		return None
	import AreaDestructibles
	import BigWorld
	import Math
	mgr = getattr(AreaDestructibles, 'g_destructiblesManager', None)
	try:
		manager_space = None if mgr is None else mgr.getSpaceID()
	except Exception as error:
		if baked['kind'] == 'falling':
			_isolate_destructible_1513(
				'falling_manager', chunk_id, item_index, detail=error)
		return None
	if mgr is None or manager_space != spaceID:
		return None
	native_count = _native_chunk_destructible_count_1513(mgr, chunk_id)
	if native_count is None:
		return None
	if item_index >= native_count:
		_isolate_destructible_1513(
			'native_count_range', chunk_id, item_index,
			detail='count=%s' % native_count)
		return None
	names, names_status = _chunk_native_name_list_1513(
		BigWorld, spaceID, chunk_id, native_count)
	if names is None:
		return None
	item_names, names_status = _chunk_native_names_1513(
		BigWorld, AreaDestructibles, spaceID, chunk_id, native_count, names)
	if names_status == 'pending_alignment':
		return None
	if item_names is None or _destructible_isolated_1513(
			chunk_id, item_index):
		return None
	try:
		chunk_matrix = BigWorld.wg_getChunkMatrix(spaceID, chunk_id)
		chunk_translation = getattr(chunk_matrix, 'translation', None)
	except Exception as error:
		_isolate_destructible_1513(
			'native_chunk_matrix', chunk_id, detail=error)
		return None
	if chunk_translation is None:
		return None
	try:
		matrix = Math.Matrix(BigWorld.wg_getDestructibleMatrix(
			spaceID, chunk_id, item_index))
	except Exception as error:
		_isolate_destructible_1513(
			('falling_matrix_query' if baked['kind'] == 'falling' else
			 'native_matrix_query'),
			chunk_id, item_index, detail=error)
		return None
	try:
		signature, located = _catalog_instance_for_matrix_1513(
			matrix, chunk_translation, Math)
	except Exception as error:
		_isolate_destructible_1513(
			('falling_matrix_signature' if baked['kind'] == 'falling' else
			 'native_matrix_signature'),
			chunk_id, item_index, detail=error)
		return None
	if located is None:
		failure_type = ('catalog_signature_ambiguous'
			if signature in catalog.get('ambiguous_instances', ())
			else 'catalog_signature_miss')
		_isolate_destructible_1513(
			failure_type, chunk_id, item_index,
			detail='signature=%r' % (signature,))
		return None
	if located['wire'] != identity:
		baked_wire = located['wire']
		_isolate_destructible_1513(
			'wire_identity_mismatch', chunk_id, item_index,
			detail='live=%r baked=%r' % (identity, baked_wire))
		_isolate_destructible_1513(
			'wire_identity_mismatch', baked_wire[0], baked_wire[1],
			detail='live=%r baked=%r' % (identity, baked_wire))
		return None
	if signature != baked['signature']:
		_isolate_destructible_1513(
			'catalog_signature_mismatch', chunk_id, item_index,
			detail='live=%r baked=%r' % (
				signature, baked['signature']))
		return None
	record = catalog['resources'].get(located['filename'])
	if record is None or record['kind'] != baked['kind']:
		_isolate_destructible_1513(
			'catalog_resource_identity', chunk_id, item_index,
			detail='filename=%s live_kind=%s baked_kind=%s' % (
				located['filename'],
				record.get('kind') if record is not None else None,
				baked['kind']))
		return None
	raw_filename = item_names.get(item_index) or ''
	name_class, live_kind = _live_filename_identity_1513(
		AreaDestructibles, raw_filename,
		_normalized_filename(raw_filename), located['filename'],
		record['kind'])
	if name_class == 'conflict':
		# The proximity scanner and this streamed-shot admission must reach the
		# same identity decision for the same native item.  ``located['wire']``
		# is already proved equal to this identity above.
		detail = ('native=%s catalog=%s native_kind=%s catalog_kind=%s '
			'names=%s' % (
				_normalized_filename(raw_filename), located['filename'],
				live_kind, record['kind'], names_status))
		_isolate_destructible_1513(
			'filename_identity_conflict', chunk_id, item_index, detail=detail)
		return None
	filename = record['filename']
	try:
		desc = AreaDestructibles.g_cache.getDescByFilename(filename)
	except Exception as error:
		_isolate_destructible_1513(
			'catalog_descriptor', chunk_id, item_index, detail=error)
		return None
	if not isinstance(desc, dict):
		_isolate_destructible_1513(
			'catalog_descriptor', chunk_id, item_index,
			detail='filename=%s payload=%s' % (
				filename, type(desc).__name__))
		return None
	expected_kind = _catalog_kind_for_type_1513(
		AreaDestructibles, desc.get('type'))
	if expected_kind != record['kind']:
		_isolate_destructible_1513(
			'catalog_kind_identity', chunk_id, item_index,
			detail='filename=%s descriptor_kind=%s catalog_kind=%s' % (
				filename, expected_kind, record['kind']))
		return None
	if not _validate_native_effect_categories_1513(
			BigWorld, AreaDestructibles, record, desc,
			spaceID, chunk_id, item_index):
		return None
	try:
		world_boxes = _world_catalog_boxes(
			record, matrix, chunk_translation, Math, located['box_index'])
		item_scale = _matrix_item_scale_1513(matrix, Math)
		chunk_translation_value = (
			float(chunk_translation.x), float(chunk_translation.y),
			float(chunk_translation.z))
	except Exception as error:
		_isolate_destructible_1513(
			('falling_matrix_transform' if record['kind'] == 'falling' else
			 'native_matrix_transform'),
			chunk_id, item_index, detail=error)
		return None
	if not world_boxes:
		_isolate_destructible_1513(
			('falling_collision_boxes' if record['kind'] == 'falling' else
			 'native_collision_boxes'),
			chunk_id, item_index,
			detail='collision box is unavailable')
		return None
	instance = {
		'filename': located['filename'],
		'descriptor_filename': filename,
		'kind': expected_kind, 'boxes': world_boxes,
		'item_scale': item_scale,
		'box_index': located['box_index'], 'signature': signature,
		'chunk_translation': chunk_translation_value,
	}
	instances[identity] = instance
	_index_catalog_instance_1513(
		globals().setdefault('g_offh_destr_contact_bins', {}),
		identity, instance)
	return instance


def _catalog_shot_intersection(spaceID, start, end, maximum_distance=None):
	"""Resolve the nearest live-validated catalog OBB along a shell ray."""
	segment = end - start
	segment_length = segment.length
	if segment_length <= 1.0e-9:
		return None
	instances = globals().get('g_offh_destr_instances', {})
	catalog = _destructible_catalog or {}
	baked_instances = catalog.get('baked_instances', {})
	if not instances and not baked_instances:
		return None
	authority = _get_destr_authority()
	identities = set(instances)
	effective_end = end
	if (maximum_distance is not None and
			float(maximum_distance) < segment_length):
		effective_end = start + segment.scale(
			max(0.0, float(maximum_distance)) / segment_length)
	bounds = (min(start.x, effective_end.x), max(start.x, effective_end.x),
		min(start.z, effective_end.z), max(start.z, effective_end.z))
	for bin_key in _baked_bin_keys_for_bounds_1513(*bounds):
		identities.update(
			catalog.get('baked_shot_bins', {}).get(bin_key, ()))
	hits = {}
	for identity in sorted(identities):
		if _destructible_isolated_1513(identity[0], identity[1]):
			continue
		instance = instances.get(identity)
		pending = False
		if instance is None:
			baked = baked_instances.get(identity)
			if baked is None:
				continue
			intersects = False
			for world_box in baked['boxes']:
				mat_kind = (world_box[2]
					if baked['kind'] == 'structure' else None)
				if authority.is_destroyed(
						identity[0], identity[1], mat_kind):
					continue
				interval = _segment_world_box_interval(
					start, end, world_box,
					baked['broad_phase_margin'])
				if interval is None:
					continue
				distance = interval[0] * segment_length
				if (maximum_distance is None or distance <=
						float(maximum_distance) + 1.0e-6):
					intersects = True
					break
			if not intersects:
				continue
			instance = _stream_baked_shot_instance_1513(spaceID, identity)
			if instance is None:
				if _destructible_isolated_1513(identity[0], identity[1]):
					continue
				instance = baked
				pending = True
		for world_box in instance['boxes']:
			interval = _segment_world_box_interval(
				start, end, world_box, 0.0)
			if interval is None:
				continue
			entry, exit = interval
			distance = entry * segment_length
			if (maximum_distance is not None and
					distance > float(maximum_distance) + 1.0e-6):
				continue
			mat_kind = (world_box[2]
				if instance['kind'] == 'structure' else None)
			if authority.is_destroyed(identity[0], identity[1], mat_kind):
				continue
			key = (identity[0], identity[1], mat_kind)
			previous = hits.get(key)
			if previous is None or distance < previous[0]:
				point = start + segment.scale(entry)
				candidate = None if pending else identity + (
					mat_kind, _instance_descriptor_filename_1513(instance),
					instance['kind'], instance['item_scale'],
					(float(point.x), float(point.y), float(point.z)))
				hits[key] = (
					distance, exit * segment_length, candidate, pending)
	if not hits:
		return None
	nearest_distance = min(value[0] for value in hits.values())
	nearest = [value for value in hits.values()
		if abs(value[0] - nearest_distance) <= _CATALOG_POINT_EPSILON]
	if len(nearest) != 1 or nearest[0][3]:
		return {
			'candidate': None, 'distance': nearest_distance,
			'exit_distance': nearest_distance, 'ambiguous': True,
		}
	distance, exit_distance, candidate, unused_pending = nearest[0]
	return {
		'candidate': candidate, 'distance': distance,
		'exit_distance': exit_distance, 'ambiguous': False,
	}


def _stream_baked_motion_instances_1513(spaceID, vehicle_box):
	"""Live-validate catalog wires covering one exact vehicle sweep."""
	catalog = _destructible_catalog or {}
	if not catalog.get('has_instance_index'):
		return
	identities = set()
	for bin_key in _baked_bin_keys_for_bounds_1513(
			*_box_xz_bounds(vehicle_box)):
		identities.update(
			catalog.get('baked_shot_bins', {}).get(bin_key, ()))
	instances = globals().get('g_offh_destr_instances', {})
	for identity in sorted(identities):
		if identity not in instances:
			_stream_baked_shot_instance_1513(spaceID, identity)


def _vehicle_swept_box(pos, yaw, vel, bbox, travel_reach=None,
		motion_yaw=None):
	import math
	minimum, maximum = bbox[:2]
	half_width = max(abs(minimum[0]), abs(maximum[0])) + 0.5
	back = abs(minimum[2])
	front = abs(maximum[2])
	if travel_reach is None:
		# Registration/streaming look-ahead keeps the historical generous reach.
		# Commit-side callers pass the exact frame travel separately below.
		reach = 0.8 + min(abs(vel) * 0.25, 1.2)
	else:
		reach = max(0.0, float(travel_reach))
	if motion_yaw is not None:
		# Ram separation, slope slip and wall deflection translate the chassis
		# independently of its orientation.  Preserve the real hull OBB and add
		# half of the translation as a fourth zonotope generator.  This is the
		# exact swept volume for a fixed-orientation OBB; rotating the hull to the
		# travel direction would lose the long front/rear corners.
		cos_y = math.cos(yaw)
		sin_y = math.sin(yaw)
		center_forward = (front - back) * 0.5
		half_forward = (front + back) * 0.5
		motion_sin = math.sin(float(motion_yaw))
		motion_cos = math.cos(float(motion_yaw))
		travel_x = motion_sin * reach
		travel_z = motion_cos * reach
		center_y = pos.y + (minimum[1] + maximum[1]) * 0.5
		half_y = (maximum[1] - minimum[1]) * 0.5
		center = (
			pos.x + sin_y * center_forward + travel_x * 0.5,
			center_y,
			pos.z + cos_y * center_forward + travel_z * 0.5)
		half_axes = (
			(cos_y * half_width, 0.0, -sin_y * half_width),
			(0.0, half_y, 0.0),
			(sin_y * half_forward, 0.0, cos_y * half_forward),
			(travel_x * 0.5, 0.0, travel_z * 0.5))
		return center, half_axes
	if vel < 0.0:
		minimum_forward = -(back + reach)
		maximum_forward = front
	else:
		minimum_forward = -back
		maximum_forward = front + reach
	cos_y = math.cos(yaw)
	sin_y = math.sin(yaw)
	center_forward = (minimum_forward + maximum_forward) * 0.5
	half_forward = (maximum_forward - minimum_forward) * 0.5
	center_y = pos.y + (minimum[1] + maximum[1]) * 0.5
	half_y = (maximum[1] - minimum[1]) * 0.5
	center = (pos.x + sin_y * center_forward, center_y,
		pos.z + cos_y * center_forward)
	half_axes = ((cos_y * half_width, 0.0, -sin_y * half_width),
		(0.0, half_y, 0.0),
		(sin_y * half_forward, 0.0, cos_y * half_forward))
	return center, half_axes


def _tree_trig_interval_1513(cosine_factor, sine_factor, start, end):
	"""Return exact extrema of ``a*cos(yaw) + b*sin(yaw)``."""
	import math
	start = float(start)
	end = float(end)
	if end < start:
		start, end = end, start
	values = (
		cosine_factor * math.cos(start) + sine_factor * math.sin(start),
		cosine_factor * math.cos(end) + sine_factor * math.sin(end))
	result = [values[0], values[1]]
	stationary = math.atan2(sine_factor, cosine_factor)
	first = int(math.ceil((start - stationary) / math.pi))
	last = int(math.floor((end - stationary) / math.pi))
	for offset in range(first, last + 1):
		angle = stationary + offset * math.pi
		result.append(
			cosine_factor * math.cos(angle) +
			sine_factor * math.sin(angle))
	return min(result), max(result)


def _tree_rotation_interval_bbox_1513(bbox, half_angle):
	"""Enclose the native hull over one bounded yaw interval."""
	minimum, maximum = bbox[:2]
	half_angle = abs(float(half_angle))
	x_values = []
	z_values = []
	for local_x in (float(minimum[0]), float(maximum[0])):
		for local_z in (float(minimum[2]), float(maximum[2])):
			low, high = _tree_trig_interval_1513(
				local_x, local_z, -half_angle, half_angle)
			x_values.extend((low, high))
			low, high = _tree_trig_interval_1513(
				local_z, -local_x, -half_angle, half_angle)
			z_values.extend((low, high))
	return (
		(min(x_values), float(minimum[1]), min(z_values)),
		(max(x_values), float(maximum[1]), max(z_values)), None)


def _finite_tree_motion_value_1513(value):
	import math
	try:
		value = float(value)
	except (TypeError, ValueError, OverflowError):
		return None
	if math.isnan(value) or math.isinf(value):
		return None
	return value


def _tree_pose_sweep_boxes_1513(
		start_pos, start_yaw, end_pos, end_yaw, bbox):
	"""Build bounded zonotope slices for one previous-to-current hull sweep.

	Each slice analytically encloses every intermediate hull orientation, then
	adds the exact linear translation as a fourth generator.  The union is a
	continuous swept-hull cover; no finite set of contact rays is used.
	"""
	import math
	values = tuple(_finite_tree_motion_value_1513(value) for value in (
		start_pos.x, start_pos.y, start_pos.z, start_yaw,
		end_pos.x, end_pos.y, end_pos.z, end_yaw))
	if any(value is None for value in values):
		return None
	(sx, sy, sz, start_yaw, ex, ey, ez, end_yaw) = values
	try:
		minimum, maximum = bbox[:2]
		minimum = tuple(_finite_tree_motion_value_1513(value)
			for value in minimum[:3])
		maximum = tuple(_finite_tree_motion_value_1513(value)
			for value in maximum[:3])
	except (AttributeError, KeyError, TypeError, IndexError):
		return None
	if (any(value is None for value in minimum + maximum) or
			any(minimum[index] > maximum[index] for index in range(3))):
		return None
	dx = ex - sx
	dy = ey - sy
	dz = ez - sz
	distance = (dx * dx + dz * dz) ** 0.5
	yaw_delta = ((end_yaw - start_yaw + math.pi) %
		(2.0 * math.pi)) - math.pi
	steps = max(1,
		int(math.ceil(distance / _TREE_SWEEP_TRANSLATION_STEP_1513)),
		int(math.ceil(abs(yaw_delta) / _TREE_SWEEP_ANGLE_STEP_1513)))
	if steps > _TREE_SWEEP_MAX_SEGMENTS_1513:
		return None
	boxes = []
	for index in range(steps):
		t0 = float(index) / float(steps)
		t1 = float(index + 1) / float(steps)
		p0 = (sx + dx * t0, sy + dy * t0, sz + dz * t0)
		p1 = (sx + dx * t1, sy + dy * t1, sz + dz * t1)
		yaw0 = start_yaw + yaw_delta * t0
		yaw1 = start_yaw + yaw_delta * t1
		mid_yaw = (yaw0 + yaw1) * 0.5
		interval_bbox = _tree_rotation_interval_bbox_1513(
			(minimum, maximum, None), (yaw1 - yaw0) * 0.5)
		interval_minimum, interval_maximum = interval_bbox[:2]
		local_center_x = (
			interval_minimum[0] + interval_maximum[0]) * 0.5
		local_center_y = (
			interval_minimum[1] + interval_maximum[1]) * 0.5
		local_center_z = (
			interval_minimum[2] + interval_maximum[2]) * 0.5
		half_x = (interval_maximum[0] - interval_minimum[0]) * 0.5
		half_y = (interval_maximum[1] - interval_minimum[1]) * 0.5
		half_z = (interval_maximum[2] - interval_minimum[2]) * 0.5
		cos_y = math.cos(mid_yaw)
		sin_y = math.sin(mid_yaw)
		travel = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
		center = (
			p0[0] + cos_y * local_center_x +
				sin_y * local_center_z + travel[0] * 0.5,
			p0[1] + local_center_y + travel[1] * 0.5,
			p0[2] - sin_y * local_center_x +
				cos_y * local_center_z + travel[2] * 0.5)
		half_axes = (
			(cos_y * half_x, 0.0, -sin_y * half_x),
			(0.0, half_y, 0.0),
			(sin_y * half_z, 0.0, cos_y * half_z),
			(travel[0] * 0.5, travel[1] * 0.5,
				travel[2] * 0.5))
		boxes.append((center, half_axes))
	return tuple(boxes)


def _tree_xz_zonotope_hull_1513(sweep_box):
	"""Return the convex XZ polygon of one generated sweep slice."""
	center, half_axes = sweep_box[:2]
	generators = tuple((float(axis[0]), float(axis[2]))
		for axis in half_axes
		if axis[0] * axis[0] + axis[2] * axis[2] > 1.0e-16)
	points = [(float(center[0]), float(center[2]))]
	for generator in generators:
		points = [(point[0] + sign * generator[0],
			point[1] + sign * generator[1])
			for point in points for sign in (-1.0, 1.0)]
	points = sorted(set(points))
	if len(points) <= 2:
		return tuple(points)

	def cross(origin, left, right):
		return ((left[0] - origin[0]) * (right[1] - origin[1]) -
			(left[1] - origin[1]) * (right[0] - origin[0]))

	lower = []
	for point in points:
		while (len(lower) >= 2 and
				cross(lower[-2], lower[-1], point) <= 1.0e-12):
			lower.pop()
		lower.append(point)
	upper = []
	for point in reversed(points):
		while (len(upper) >= 2 and
				cross(upper[-2], upper[-1], point) <= 1.0e-12):
			upper.pop()
		upper.append(point)
	return tuple(lower[:-1] + upper[:-1])


def _point_near_tree_sweep_1513(x, z, sweep_box,
		contact_radius=_SOLID_CONTACT_RADIUS_1513):
	"""Test a tree origin against a swept zonotope plus its circular skin."""
	hull = _tree_xz_zonotope_hull_1513(sweep_box)
	if not hull:
		return False
	point = (float(x), float(z))
	if len(hull) == 1:
		dx = point[0] - hull[0][0]
		dz = point[1] - hull[0][1]
		return dx * dx + dz * dz <= contact_radius * contact_radius
	inside = True
	minimum_distance_squared = None
	for index, start in enumerate(hull):
		end = hull[(index + 1) % len(hull)]
		edge_x = end[0] - start[0]
		edge_z = end[1] - start[1]
		if edge_x * (point[1] - start[1]) - edge_z * (
				point[0] - start[0]) < -1.0e-8:
			inside = False
		length_squared = edge_x * edge_x + edge_z * edge_z
		if length_squared <= 1.0e-16:
			fraction = 0.0
		else:
			fraction = ((point[0] - start[0]) * edge_x +
				(point[1] - start[1]) * edge_z) / length_squared
			fraction = max(0.0, min(1.0, fraction))
		nearest_x = start[0] + edge_x * fraction
		nearest_z = start[1] + edge_z * fraction
		distance_squared = ((point[0] - nearest_x) ** 2 +
			(point[1] - nearest_z) ** 2)
		if (minimum_distance_squared is None or
				distance_squared < minimum_distance_squared):
			minimum_distance_squared = distance_squared
	if inside:
		return True
	return (minimum_distance_squared is not None and
		minimum_distance_squared <=
		contact_radius * contact_radius + 1.0e-8)


def _tree_candidates_for_sweeps_1513(
		chunk_id, registry, sweep_boxes, tree_type,
		contact_radius=_SOLID_CONTACT_RADIUS_1513):
	"""Return exact named tree records and known isolated contacts."""
	candidates = {}
	isolated_hits = set()
	seen = set()
	for sweep_box in sweep_boxes:
		minimum_x, maximum_x, minimum_z, maximum_z = (
			_box_xz_bounds(sweep_box))
		minimum_x -= contact_radius
		maximum_x += contact_radius
		minimum_z -= contact_radius
		maximum_z += contact_radius
		for bin_key in _bin_keys_for_bounds(
				minimum_x, maximum_x, minimum_z, maximum_z):
			for item in registry.get('bins', {}).get(bin_key, ()):
				item_index = int(item[0])
				identity = (int(chunk_id), item_index, None)
				if identity in seen:
					continue
				if (item[4] != tree_type or
						not _normalized_filename(item[5])):
					continue
				if not _point_near_tree_sweep_1513(
						item[1], item[3], sweep_box, contact_radius):
					continue
				seen.add(identity)
				if _destructible_isolated_1513(chunk_id, item_index):
					isolated_hits.add(identity)
					continue
				candidates[identity] = item
	return candidates, isolated_hits


def _vehicle_contact_box(pos, yaw, bbox, epsilon=0.075, travel=0.0,
		motion_yaw=None):
	"""Return the complete current hull plus only this frame's real travel."""
	import math
	minimum, maximum = bbox[:2]
	margin = max(0.0, float(epsilon))
	travel = float(travel)
	minimum_x = float(minimum[0]) - margin
	maximum_x = float(maximum[0]) + margin
	minimum_forward = float(minimum[2]) - margin
	maximum_forward = float(maximum[2]) + margin
	center_x = (minimum_x + maximum_x) * 0.5
	half_width = (maximum_x - minimum_x) * 0.5
	center_forward = (minimum_forward + maximum_forward) * 0.5
	half_forward = (maximum_forward - minimum_forward) * 0.5
	if motion_yaw is None:
		travel_yaw = float(yaw) if travel >= 0.0 else float(yaw) + math.pi
	else:
		travel_yaw = float(motion_yaw)
	travel_distance = abs(travel)
	travel_x = math.sin(travel_yaw) * travel_distance
	travel_z = math.cos(travel_yaw) * travel_distance
	cos_y = math.cos(yaw)
	sin_y = math.sin(yaw)
	center_y = pos.y + (float(minimum[1]) + float(maximum[1])) * 0.5
	half_y = (float(maximum[1]) - float(minimum[1])) * 0.5 + margin
	center = (
		pos.x + cos_y * center_x + sin_y * center_forward + travel_x * 0.5,
		center_y,
		pos.z - sin_y * center_x + cos_y * center_forward + travel_z * 0.5)
	half_axes = ((cos_y * half_width, 0.0, -sin_y * half_width),
		(0.0, half_y, 0.0),
		(sin_y * half_forward, 0.0, cos_y * half_forward),
		(travel_x * 0.5, 0.0, travel_z * 0.5))
	return center, half_axes


def _catalog_intersections(world_boxes, vehicle_box):
	result = []
	for world_box in world_boxes:
		if not _boxes_intersect(vehicle_box, world_box):
			continue
		result.append(world_box)
	return result


def _native_hide_delay():
	import AreaDestructibles
	try:
		delay = float(getattr(
			AreaDestructibles, 'DESTRUCTIBLE_HIDING_DELAY',
			_NATIVE_HIDE_MIN_SECONDS))
	except (TypeError, ValueError):
		delay = _NATIVE_HIDE_MIN_SECONDS
	return max(_NATIVE_HIDE_MIN_SECONDS, delay)


def note_destroyed(kind, chunkID, itemIndex, matKind=None, now=None):
	"""Track native hide or falling-matrix collision after destruction."""
	if kind not in ('fragile', 'module', 'column'):
		return False
	if _destructible_isolated_1513(chunkID, itemIndex):
		return False
	_invalidate_chunk_native_names_1513(chunkID)
	if now is None:
		import BigWorld
		now = BigWorld.time()
	if kind == 'column':
		identity = (int(chunkID), int(itemIndex))
		active = globals().setdefault('g_offh_destr_falling_active', {})
		if identity not in active:
			active[identity] = {'last_refresh': None}
		return True
	key = (int(chunkID), int(itemIndex),
		int(matKind) if matKind is not None else None)
	pending = globals().setdefault('g_offh_destr_pending', {})
	if key not in pending:
		pending[key] = float(now) + _native_hide_delay()
	return True


def begin_local_prediction(token):
	"""Temporarily hide exact fragile/module collision before LAN commit.

	The visible player keeps moving while the worker verifies its proposal, but
	does not mutate or animate the local object. The canonical event remains the
	sole owner of shared state and presentation. Falling columns and
	unclassified geometry stay solid.
	"""
	instances = globals().get('g_offh_destr_instances', {})
	predicted = globals().setdefault('g_offh_destr_speculative', set())
	changed = False
	for raw in token or ():
		try:
			chunk_id = int(raw[0])
			item_index = int(raw[1])
			mat_kind = None if raw[2] is None else int(raw[2])
		except (IndexError, TypeError, ValueError, OverflowError):
			continue
		instance = instances.get((chunk_id, item_index))
		if not isinstance(instance, dict):
			continue
		kind = instance.get('kind')
		if ((kind == 'fragile' and mat_kind is None) or
				(kind == 'structure' and mat_kind is not None)):
			key = (chunk_id, item_index, mat_kind)
			if key not in predicted:
				predicted.add(key)
				changed = True
	return changed


def commit_local_prediction(spaceID, token, position, yaw, speed):
	"""Apply an exact visible hull crush through the stock #1513 seam.

	The visible client owns the native contact that its copied vehicle physics
	just observed.  Commit that presentation before movement is advanced; the
	hidden worker still publishes the canonical LAN event for other clients.
	Only checksum-pinned fragile items, falling atoms and exact structure modules
	reach here.
	"""
	import Math
	authority = _get_destr_authority()
	instances = globals().get('g_offh_destr_instances', {})
	committed = []
	for raw in token or ():
		try:
			chunk_id = int(raw[0])
			item_index = int(raw[1])
			mat_kind = None if raw[2] is None else int(raw[2])
		except (IndexError, TypeError, ValueError, OverflowError):
			continue
		instance = instances.get((chunk_id, item_index))
		if not isinstance(instance, dict):
			continue
		kind = instance.get('kind')
		if not ((kind == 'fragile' and mat_kind is None) or
				(kind == 'falling' and mat_kind is None) or
				(kind == 'structure' and mat_kind is not None)):
			continue
		boxes = tuple(box for box in instance.get('boxes', ())
			if kind != 'structure' or box[2] == mat_kind)
		if not boxes:
			continue
		center = boxes[0][0]
		point = Math.Vector3(center[0], center[1], center[2])
		if authority.is_destroyed(chunk_id, item_index, mat_kind):
			accepted = True
		elif kind == 'fragile':
			accepted = authority.destroy_fragile(
				spaceID, chunk_id, item_index, point, False)
		elif kind == 'falling':
			accepted = authority.destroy_column(
				spaceID, chunk_id, item_index, yaw, speed, point)
		else:
			accepted = authority.destroy_module(
				spaceID, chunk_id, item_index, mat_kind, point, False)
		if not accepted:
			raise RuntimeError(
				'local native destructible prediction was not accepted: '
				'chunk=%s item=%s' % (chunk_id, item_index))
		event_kind = ('fragile' if kind == 'fragile' else
			'column' if kind == 'falling' else 'module')
		note_destroyed(event_kind, chunk_id, item_index, mat_kind)
		committed.append((chunk_id, item_index, mat_kind))
	return begin_local_prediction(committed) or bool(committed)


def clear_local_prediction(token):
	"""Stop hiding exact speculative collision after a terminal outcome."""
	predicted = globals().get('g_offh_destr_speculative')
	if not predicted:
		return False
	changed = False
	for raw in token or ():
		try:
			key = (int(raw[0]), int(raw[1]),
				None if raw[2] is None else int(raw[2]))
		except (IndexError, TypeError, ValueError, OverflowError):
			continue
		if key in predicted:
			predicted.remove(key)
			changed = True
	return changed


def _catalog_contact_candidates(vehicle_box):
	instances = globals().get('g_offh_destr_instances', {})
	contact_bins = globals().get('g_offh_destr_contact_bins', {})
	bounds = _box_xz_bounds(vehicle_box)
	receipt_key = (
		_spatial_revision_1513(), _bin_rectangle_signature_1513(bounds))
	if _receipt_cache_get_1513(
			'g_offh_destr_empty_contact_receipts', receipt_key):
		return []
	candidates = []
	seen = set()
	had_members = False
	for bin_key in _bin_keys_for_bounds(*bounds):
		members = contact_bins.get(bin_key, ())
		if members:
			had_members = True
		for chunk_id, item_index in sorted(members):
			identity = (int(chunk_id), int(item_index))
			if _destructible_isolated_1513(*identity):
				continue
			if identity in seen:
				continue
			seen.add(identity)
			instance = instances.get(identity)
			if instance is None:
				continue
			for world_box in _catalog_intersections(
					instance['boxes'], vehicle_box):
				mat_kind = (world_box[2]
					if instance['kind'] == 'structure' else None)
				candidate = identity + (mat_kind,
					_instance_descriptor_filename_1513(instance),
					instance['kind'], instance['item_scale'], world_box[0])
				if candidate not in candidates:
					candidates.append(candidate)
	if not had_members:
		_receipt_cache_put_1513(
			'g_offh_destr_empty_contact_receipts', receipt_key, True,
			_EMPTY_CONTACT_RECEIPT_LIMIT)
	return candidates


def _synthetic_mat_info(candidate, math_module):
	chunk_id, item_index, mat_kind, filename = candidate[:4]
	center = candidate[6]
	point = math_module.Vector3(center[0], center[1], center[2])
	normal = math_module.Vector3(0.0, 1.0, 0.0)
	return (True, point, normal,
		mat_kind if mat_kind is not None else 73,
		filename, item_index, chunk_id)


def _catalog_candidate_on_ray_1513(
		contact_pt, segment_start, segment_end, prefer_destroyed=False):
	"""Resolve one exact registered OBB on the current native ray.

	Point containment deliberately has a 7.5 cm tolerance for compiled BSP
	contacts.  That tolerance makes two touching fence tiles look ambiguous at
	the shared face, even after the ray has already advanced past the first
	tile's exact exit.  Intersecting the *remaining* ray with each unpadded OBB
	removes the tile behind ``segment_start`` without advancing the native ray or
	weakening the wall epsilon.
	"""
	if _destructible_catalog is None:
		return None
	segment_length = (segment_end - segment_start).length
	if segment_length <= 1.0e-9:
		return None
	hit_distance = (contact_pt - segment_start).length
	instances = globals().get('g_offh_destr_instances', {})
	contact_bins = globals().get('g_offh_destr_contact_bins', {})
	bin_key = _destructible_bin_key(contact_pt.x, contact_pt.z)
	candidates = []
	for chunk_id, item_index in contact_bins.get(bin_key, ()):
		if _destructible_isolated_1513(chunk_id, item_index):
			continue
		instance = instances.get((chunk_id, item_index))
		if instance is None:
			continue
		for world_box in instance['boxes']:
			interval = _segment_world_box_interval(
				segment_start, segment_end, world_box, 0.0)
			if interval is None:
				continue
			entry_distance = interval[0] * segment_length
			exit_distance = interval[1] * segment_length
			if (entry_distance > hit_distance + _CATALOG_POINT_EPSILON or
					exit_distance + _CATALOG_POINT_EPSILON < hit_distance):
				continue
			mat_kind = (world_box[2]
				if instance['kind'] == 'structure' else None)
			candidate = (int(chunk_id), int(item_index), mat_kind,
				_instance_descriptor_filename_1513(instance),
				instance['kind'], instance['item_scale'])
			entry = (candidate, entry_distance, exit_distance)
			if not any(value[0] == candidate for value in candidates):
				candidates.append(entry)
	if len(candidates) == 1:
		return candidates[0][0]
	if len(candidates) > 1 and prefer_destroyed:
		# Adjacent fence/module OBBs commonly share the exact native face.
		# Once the first member has a canonical destroy receipt, that shared
		# point is no longer ambiguous: skip only an already-destroyed member,
		# then recast immediately after its nearest exit.  Active neighbours and
		# backing walls remain visible to the next native ray.
		authority = _get_destr_authority()
		destroyed = [value for value in candidates if authority.is_destroyed(
			value[0][0], value[0][1], value[0][2])]
		if destroyed:
			destroyed.sort(key=lambda value: (
				value[2], value[1], value[0][0], value[0][1],
				-1 if value[0][2] is None else value[0][2]))
			destroyed_keys = set(value[0][:3] for value in destroyed)
			for value in destroyed:
				# Do not jump past an active OBB that overlaps this member's
				# interior (for example two side-by-side pieces sharing the
				# entire ray).  A following segment whose entry merely touches
				# this exit is safe: the recast starts just beyond the destroyed
				# face and the native query sees that neighbour immediately.
				if all(
						other[0][:3] in destroyed_keys or
						other[1] >= value[2] - _SHOT_RAY_EPSILON
						for other in candidates):
					return value[0]
	return None


def _catalog_soft_static_path(spaceID, segment_start, segment_end,
		collision, vel, td, recast_budget=None,
		require_pending_first=False, allow_kinetic_first=False,
		kinetic_speed=None):
	"""Classify a far static ray without destroying anything.

	A bot direction probe may look 15--20 metres ahead.  It may regard a
	proved crushable as a soft obstacle so the bot can reach real hull contact,
	but it must never destroy from that distance or skip unrelated geometry
	behind the item.  Every skipped hit therefore needs one unique registered
	OBB, the retail kinetic gate, an exact OBB exit and a clear/native-next-hit
	recast.  Unknown and ambiguous chains remain solid.  Exhausting the shared
	native recast budget instead returns ``'deferred'`` so the caller can avoid
	caching a false hard wall.
	"""
	if (_destructible_catalog is None or collision is None or td is None):
		return False
	import BigWorld
	import Math
	try:
		direction = segment_end - segment_start
		remaining = direction.length
		if remaining <= 1.0e-6:
			return False
		direction.normalise()
	except (AttributeError, TypeError, ValueError):
		return False

	current_start = segment_start
	current_hit = collision
	authority = _get_destr_authority()
	kinetic_contact = False
	pending_contact = False
	for candidate_index in range(_SOFT_STATIC_MAX_SKIPS):
		try:
			hit_point = current_hit[0]
		except (TypeError, IndexError):
			return 'pending_hard' if pending_contact else False
		candidate = _catalog_candidate_on_ray_1513(
			hit_point, current_start, segment_end,
			prefer_destroyed=(require_pending_first and candidate_index == 0))
		if candidate is None:
			return 'pending_hard' if pending_contact else False
		# #1513 ``Vehicle._isDestructibleMayBeBroken`` returns True as soon as the
		# chunk controller reports the item broken, whatever the vehicle speed and
		# whatever the hide callback still draws.  A broken skin therefore never
		# resists again, and a felled column stops being an obstacle.
		broken = (candidate[4] in ('fragile', 'structure', 'falling') and
			authority.is_destroyed(candidate[0], candidate[1], candidate[2]))
		mat_info = _synthetic_mat_info(candidate + ((
			float(hit_point.x), float(hit_point.y), float(hit_point.z)),), Math)
		current_crushable = broken or _stock_crushable_1513(
			mat_info, vel, td, candidate[5])
		if require_pending_first and candidate_index == 0:
			if broken:
				pending_contact = True
			elif allow_kinetic_first and current_crushable:
				# The visible player asks this read-only path to prove the
				# complete native ray before submitting an exact catalog
				# proposal.  A prop already crushable at the current physical
				# speed is just as valid as one admitted by the directional cap;
				# both still require an OBB-exit recast for a backing wall.
				kinetic_contact = True
			elif (allow_kinetic_first and kinetic_speed is not None and
					not current_crushable and _stock_crushable_1513(
						mat_info, kinetic_speed, td, candidate[5])):
				kinetic_contact = True
				current_crushable = True
			else:
				return False
		elif (allow_kinetic_first and
				kinetic_speed is not None and not current_crushable and
				_stock_crushable_1513(
					mat_info, kinetic_speed, td, candidate[5])):
			kinetic_contact = True
			current_crushable = True
		if not current_crushable:
			return 'pending_hard' if pending_contact else False
		exit_distance = _registered_shot_exit_1513(
			candidate[0], candidate[1], candidate[2], candidate[3],
			current_start, segment_end, hit_point)
		if exit_distance is None:
			return 'pending_hard' if pending_contact else False
		next_start = current_start + direction.scale(
			float(exit_distance) + _SHOT_RAY_EPSILON)
		if (segment_end - next_start).length <= _SHOT_RAY_EPSILON:
			return 'kinetic' if kinetic_contact else True
		if recast_budget is not None:
			if not recast_budget or int(recast_budget[0]) <= 0:
				return 'pending_hard' if pending_contact else 'deferred'
			recast_budget[0] = int(recast_budget[0]) - 1
		current_hit = BigWorld.wg_collideSegment(
			spaceID, next_start, segment_end, 128)
		if current_hit is None:
			return 'kinetic' if kinetic_contact else True
		current_start = next_start
	return 'pending_hard' if pending_contact else False


def _motion_travel_reach(vel, dt):
	# Match the grounded native sweep in world_collision.  A shorter catalog
	# reach can accept the static hit, then miss the pending native skin during
	# the copied pose commit and incorrectly feed it through hard-wall braking.
	return max(0.4, abs(float(vel)) * max(0.0, float(dt)) + 0.2)


def _broken_collision_filter(members):
	"""Filter exact broken or locally predicted identities in ``members``."""
	if not members:
		return None
	authority = _get_destr_authority()
	# The production authority always exposes its accepted-key ledger.  Keep
	# injected compatibility/test seams fail-closed instead of guessing from an
	# ``is_destroyed`` result that cannot enumerate structure materials.
	if not callable(getattr(authority, 'destroyed_keys', None)):
		return None
	member_ids = set((int(chunk_id), int(item_index))
		for chunk_id, item_index in members)
	broken = set()
	for chunk_id, item_index in member_ids:
		for mat_kind in _broken_item_materials_1513(
				authority, chunk_id).get(int(item_index), ()):
			broken.add((int(chunk_id), int(item_index), mat_kind))
	broken.update(key for key in
		globals().get('g_offh_destr_speculative', set())
		if key[:2] in member_ids)
	if not broken:
		return None

	def reject_broken_skin(*hit):
		# #1513 passes (matKind, collFlags, itemIndex, chunkID).  Keeping
		# every unrecognised surface means a backing wall is still returned by
		# the same native query after the broken skin is skipped.
		try:
			identity = (int(hit[3]), int(hit[2]))
		except (IndexError, TypeError, ValueError, OverflowError):
			return True
		if (identity + (hit[0],)) not in broken and (
				identity + (None,)) not in broken:
			return True
		globals()['g_offh_destr_ground_skips'] = globals().get(
			'g_offh_destr_ground_skips', 0) + 1
		return False

	return reject_broken_skin


def ground_collision_filter(x, z):
	"""Return a ``wg_collideSegment`` filter that hides broken destructibles.

	#1513 passes such a callback as the fifth argument itself: the falling
	animator builds ``partial(_fallCollideCallback, destrIndex, chunkID)`` so
	the engine skips that surface and reports the next real one.  A broken item
	is not part of the vehicle's collision in retail, so the suspension and the
	drive slope must not sample its skin while the model waits to hide.
	``None`` keeps the native fast path where no broken item covers this column.
	"""
	if _destructible_catalog is None:
		return None
	contact_bins = globals().get('g_offh_destr_contact_bins')
	if not contact_bins:
		return None
	members = contact_bins.get(_destructible_bin_key(x, z))
	return _broken_collision_filter(members)


def horizontal_collision_filter(start, end):
	"""Hide exact broken identities from one horizontal hull ray.

	The native callback is safer than treating a 0.2-second hide window as a
	blanket pass: the engine skips only the accepted ``(chunk, item, material)``
	and immediately returns an unrelated prop or backing wall on the same ray.
	"""
	if _destructible_catalog is None:
		return None
	contact_bins = globals().get('g_offh_destr_contact_bins')
	if not contact_bins:
		return None
	try:
		keys = _bin_keys_for_bounds(
			min(float(start.x), float(end.x)),
			max(float(start.x), float(end.x)),
			min(float(start.z), float(end.z)),
			max(float(start.z), float(end.z)))
	except (AttributeError, TypeError, ValueError):
		return None
	members = set()
	for key in keys:
		members.update(contact_bins.get(key, ()))
	return _broken_collision_filter(members)


def _broken_item_materials_1513(authority, chunkID):
	"""Index one chunk's accepted keys by item, refreshed as the set grows."""
	cache = globals().setdefault('g_offh_destr_broken_cache', {})
	keys = authority.destroyed_keys(chunkID)
	entry = cache.get(chunkID)
	if entry is not None and entry[0] == len(keys):
		return entry[1]
	items = {}
	for item_index, mat_kind in keys:
		items.setdefault(int(item_index), set()).add(mat_kind)
	cache[chunkID] = (len(keys), items)
	return items


def take_ground_skip_count():
	"""Return and clear how many broken surfaces the ground filter hid."""
	count = globals().get('g_offh_destr_ground_skips', 0)
	globals()['g_offh_destr_ground_skips'] = 0
	return int(count)


def _catalog_pending_at_hull(pos, yaw, vel, td, now, dt=0.04,
		motion_yaw=None):
	"""Return whether a fragile/module hide window still covers the hull.

	This is classification only.  Callers keep the pose blocked while the native
	skin of a broken item is still drawn, but preserve impact momentum instead
	of applying the hard wall exponential brake.  The window is the pinned
	``DESTRUCTIBLE_HIDING_DELAY``, so a wall that outlives it is a real wall.
	"""
	bbox = _vehicle_hull_bbox(td)
	if _destructible_catalog is None or bbox is None:
		return False
	vehicle_box = _vehicle_swept_box(
		pos, yaw, vel, bbox, _motion_travel_reach(vel, dt),
		motion_yaw=motion_yaw)
	pending = globals().get('g_offh_destr_pending', {})
	for candidate in _catalog_contact_candidates(vehicle_box):
		deadline = pending.get((candidate[0], candidate[1], candidate[2]))
		if deadline is not None and float(now) < float(deadline):
			return True
	return False


def _catalog_hull_contact(pos, yaw, vel, td, dt=0.04,
		motion_yaw=None):
	"""Cheap contact-bin guard for the copied player/Bot pose integrators."""
	bbox = _vehicle_hull_bbox(td)
	if _destructible_catalog is None or bbox is None:
		return False
	return bool(_catalog_contact_candidates(
		_vehicle_swept_box(
			pos, yaw, vel, bbox, _motion_travel_reach(vel, dt),
			motion_yaw=motion_yaw)))


def _catalog_motion_result(status, token=None, accepted_now=False,
		used_kinetic_speed=False, return_status=False, return_detail=False,
		kinds=None, requires_commit=None):
	"""Keep the legacy status seam while exposing an exact commit receipt."""
	if return_detail:
		result = {
			'status': status,
			'token': tuple(sorted(token or ())) or None,
			'accepted_now': bool(accepted_now),
			'used_kinetic_speed': bool(used_kinetic_speed),
			'kinds': ','.join(sorted(kinds or ())) or '-',
		}
		if requires_commit is not None:
			result['requires_commit'] = bool(requires_commit)
		return result
	# ``approach`` is meaningful only to the combined world+catalog resolver.
	# Older callers must continue to fail closed on a non-contact lookahead.
	legacy_status = 'hard' if status == 'approach' else status
	return legacy_status if return_status else legacy_status != 'clear'


def _catalog_motion_blocked(spaceID, pos, yaw, vel, td, now,
		return_status=False, dt=0.04, kinetic_speed=None,
		return_detail=False, kinetic_commit=False, commit_enabled=True,
		proposal_only=False, motion_yaw=None):
	"""Resolve exact streamed OBB contact before committing local movement."""
	if proposal_only and (not return_detail or not kinetic_commit):
		raise ValueError(
			'catalog motion proposals require detail and kinetic classification')
	_diagnostic_flush_1513(now)
	publish_failures, publish_kinds = _retry_catalog_publications_1513()
	if publish_failures:
		# Backpressure is an operation-local pending result.  Do not admit more
		# irreversible native mutations until the already committed event is
		# observable, and retain its exact identity for the worker retry.
		return _catalog_motion_result(
			'pending', publish_failures, return_status=return_status,
			return_detail=return_detail, kinds=publish_kinds,
			requires_commit=False if proposal_only else None)
	if _destructible_catalog is None:
		return _catalog_motion_result(
			'pending' if publish_failures else 'clear', publish_failures,
			return_status=return_status, return_detail=return_detail,
			kinds=publish_kinds,
			requires_commit=False if proposal_only else None)
	bbox = _vehicle_hull_bbox(td)
	if bbox is None:
		return _catalog_motion_result(
			'pending' if publish_failures else 'clear', publish_failures,
			return_status=return_status, return_detail=return_detail,
			kinds=publish_kinds,
			requires_commit=False if proposal_only else None)
	import Math
	auth = _get_destr_authority()
	_refresh_destroyed_falling_instances_1513(spaceID, auth, now)
	vehicle_box = _vehicle_swept_box(
		pos, yaw, vel, bbox, _motion_travel_reach(vel, dt),
		motion_yaw=motion_yaw)
	# The visible player does not run the authority Bot scan that normally
	# populates the live item registry.  Admit only checksum-pinned wires in the
	# current hull bins through the same read-only native validation as shells.
	_stream_baked_motion_instances_1513(spaceID, vehicle_box)
	candidates = _catalog_contact_candidates(vehicle_box)
	if not candidates:
		return _catalog_motion_result(
			'pending' if publish_failures else 'clear', publish_failures,
			return_status=return_status, return_detail=return_detail,
			kinds=publish_kinds,
			requires_commit=False if proposal_only else None)

	grouped = {}
	for candidate in candidates:
		grouped.setdefault((candidate[0], candidate[1]), []).append(candidate)
	instances = globals().get('g_offh_destr_instances', {})
	contact_box = (_vehicle_contact_box(
		pos, yaw, bbox, travel=float(vel) * max(0.0, float(dt)),
		motion_yaw=motion_yaw)
		if kinetic_speed is not None else None)
	blocked = False
	crushed = False
	kinetic = False
	approach = False
	publication_pending = bool(publish_failures)
	exact_token = set(publish_failures)
	contact_kinds = set(publish_kinds)
	commit_candidates = []

	for identity in sorted(grouped):
		by_material = {}
		for candidate in grouped[identity]:
			by_material.setdefault(candidate[2], candidate)
		active = []
		for mat_kind in sorted(
				by_material, key=lambda value: -1 if value is None else value):
			candidate = by_material[mat_kind]
			chunk_id, item_index, unused_mat, unused_filename, kind = (
				candidate[:5])
			key = (chunk_id, item_index, mat_kind)
			contact_candidate = (contact_box is not None and
				kind in ('fragile', 'structure', 'falling') and
				any(_boxes_intersect(contact_box, world_box)
					for world_box in instances.get(
						(chunk_id, item_index), {}).get('boxes', ())
					if (kind != 'structure' or world_box[2] == mat_kind)))
			contact_kinds.add(kind)
			# #1513 ``Vehicle._isDestructibleMayBeBroken`` returns True for any
			# item the chunk controller already reports broken, so a hiding skin
			# and a felled column are both transparent from that moment.
			if auth.is_destroyed(chunk_id, item_index, mat_kind):
				if kind != 'falling':
					note_destroyed(
						'module' if mat_kind is not None else 'fragile',
						chunk_id, item_index, mat_kind, now)
				crushed = True
				if contact_candidate:
					exact_token.add(key)
				_diagnostic_contact_1513(
					'swept_destroyed', chunk_id, item_index,
					fields=(('kind', kind), ('mat', mat_kind)), now=now)
				continue
			active.append((candidate, contact_candidate))

		if not active:
			continue
		for candidate, contact_candidate in active:
			chunk_id, item_index, mat_kind, unused_filename, kind = (
				candidate[:5])
			key = (chunk_id, item_index, mat_kind)
			mat_info = _synthetic_mat_info(candidate, Math)
			physical_crushable = _stock_crushable_1513(
				mat_info, vel, td, candidate[5])
			cap_crushable = (kinetic_speed is not None and
				kind in ('fragile', 'structure') and
				_stock_crushable_1513(
					mat_info, kinetic_speed, td, candidate[5]))
			if (kinetic_speed is not None and not contact_candidate and
					not physical_crushable):
				# A real frame sweep at sufficient physical speed keeps the old
				# crush-through behaviour.  Only the directional-cap shortcut is
				# restricted to exact hull contact; otherwise it is planning-only.
				if kind in ('fragile', 'structure') and cap_crushable:
					approach = True
				else:
					blocked = True
				continue
			if physical_crushable and commit_enabled:
				commit_candidates.append((candidate, vel, False))
			elif physical_crushable:
				exact_token.add(key)
				blocked = True
			elif cap_crushable:
				# Cap-only admission reaches here only for exact current-hull
				# contact; planning look-ahead returned ``approach`` above.
				exact_token.add(key)
				if kinetic_commit:
					commit_candidates.append((candidate, kinetic_speed, True))
				else:
					kinetic = True
			else:
				blocked = True
			_diagnostic_contact_1513(
				('swept_kinetic_hold' if cap_crushable else
				'swept_kinetic_reject'), chunk_id, item_index,
				fields=(('kind', kind), ('mat', mat_kind),
					('speed', '%.3f' % float(vel)),
					('scale', '%.5f' % float(candidate[5]))), now=now)

	accepted_now = False
	used_kinetic_speed = False
	requires_commit = False
	# A hard or still-kinetic sibling may stop the chassis, but it must not hide
	# an independently proved crushable identity in the same sweep.  Preserve
	# and, when requested, commit that exact subset before returning the overall
	# blocking status.
	for candidate, gate_speed, used_cap in commit_candidates:
		chunk_id, item_index, mat_kind, unused_filename, kind = (
			candidate[:5])
		if _destructible_isolated_1513(chunk_id, item_index):
			continue
		if proposal_only:
			exact_token.add((chunk_id, item_index, mat_kind))
			requires_commit = True
			used_kinetic_speed = used_kinetic_speed or used_cap
			crushed = True
			continue
		mat_info = _synthetic_mat_info(candidate, Math)
		point = mat_info[1]
		if kind == 'fragile':
			accepted = auth.destroy_fragile(
				spaceID, chunk_id, item_index, point, False)
			event_kind = 'fragile'
		elif kind == 'structure':
			accepted = auth.destroy_module(
				spaceID, chunk_id, item_index, mat_kind, point, False)
			event_kind = 'module'
		elif kind == 'falling' and not used_cap:
			accepted = auth.destroy_column(
				spaceID, chunk_id, item_index, yaw, vel, point)
			event_kind = 'column'
		else:
			blocked = True
			continue
		if not accepted:
			raise RuntimeError(
				'native catalog contact destroy was not accepted: '
				'chunk=%s item=%s' % (chunk_id, item_index))
		# Return every identity that authority was asked to mutate.  This
		# includes physical-speed look-ahead and exact cap-qualified contact;
		# the native mutation itself is the authoritative commit receipt.
		exact_token.add((chunk_id, item_index, mat_kind))
		note_destroyed(
			event_kind, chunk_id, item_index, mat_kind, now)
		if not _publish_catalog_once_1513(
				event_kind, chunk_id, item_index, point, yaw, vel,
				mat_kind if event_kind == 'module' else None):
			publication_pending = True
		accepted_now = True
		used_kinetic_speed = used_kinetic_speed or used_cap
		_diagnostic_contact_1513(
			'swept_native_accept', chunk_id, item_index,
			fields=(('kind', kind), ('mat', mat_kind),
				('speed', '%.3f' % float(gate_speed))), now=now)
		crushed = True

	status = ('pending' if publication_pending else
		'hard' if blocked else
		'kinetic' if kinetic else
		'crushed' if crushed else
		'approach' if approach else 'clear')
	return _catalog_motion_result(
		status, exact_token, accepted_now,
		used_kinetic_speed, return_status, return_detail, contact_kinds,
		requires_commit if proposal_only else None)


def _catalog_motion_proposal(spaceID, pos, yaw, vel, td, now,
		dt=0.04, kinetic_speed=None, motion_yaw=None):
	"""Return a mutation-free exact hull-sweep proposal for worker review."""
	return _catalog_motion_blocked(
		spaceID, pos, yaw, vel, td, now, dt=dt,
		kinetic_speed=kinetic_speed, return_detail=True,
		kinetic_commit=True, commit_enabled=True, proposal_only=True,
		motion_yaw=motion_yaw)


def _catalog_instance_boxes(chunkID, itemIndex, filename, kind,
		matKind=None):
	if _destructible_catalog is None:
		return None
	if _destructible_isolated_1513(chunkID, itemIndex):
		return ()
	normalized = _normalized_filename(filename)
	record = _destructible_catalog['resources'].get(normalized)
	instance = globals().get('g_offh_destr_instances', {}).get(
		(chunkID, itemIndex))
	if (record is None or record['kind'] != kind or instance is None or
			instance['filename'] != normalized or instance['kind'] != kind):
		return ()
	if kind == 'structure':
		return tuple(box for box in instance['boxes'] if box[2] == matKind)
	return instance['boxes']


def _catalog_candidate_at_contact(contact_pt):
	"""Resolve exactly one registered catalog item/module at native contact."""
	if _destructible_catalog is None:
		return None
	candidates = []
	instances = globals().get('g_offh_destr_instances', {})
	contact_bins = globals().get('g_offh_destr_contact_bins', {})
	bin_key = _destructible_bin_key(contact_pt.x, contact_pt.z)
	for chunk_id, item_index in contact_bins.get(bin_key, ()):
		if _destructible_isolated_1513(chunk_id, item_index):
			continue
		instance = instances.get((chunk_id, item_index))
		if instance is None:
			continue
		for world_box in instance['boxes']:
			if not _point_in_world_box(contact_pt, world_box):
				continue
			mat_kind = (world_box[2]
				if instance['kind'] == 'structure' else None)
			candidate = (int(chunk_id), int(item_index), mat_kind,
				_instance_descriptor_filename_1513(instance),
				instance['kind'], instance['item_scale'])
			if candidate not in candidates:
				candidates.append(candidate)
			if len(candidates) > 1:
				return None
	return candidates[0] if len(candidates) == 1 else None


def _catalog_candidate_for_native_identity_1513(
		chunk_id, item_index, mat_kind, contact_pt):
	"""Recover an anonymous native material hit without guessing identity."""
	if _destructible_isolated_1513(chunk_id, item_index):
		return None
	instance = globals().get('g_offh_destr_instances', {}).get(
		(int(chunk_id), int(item_index)))
	if instance is None:
		return None
	if instance['kind'] == 'structure':
		boxes = tuple(box for box in instance['boxes']
			if box[2] == mat_kind and _point_in_world_box(contact_pt, box))
		candidate_mat = mat_kind
	else:
		boxes = tuple(box for box in instance['boxes']
			if _point_in_world_box(contact_pt, box))
		candidate_mat = None
	if not boxes:
		return None
	return (int(chunk_id), int(item_index), candidate_mat,
		_instance_descriptor_filename_1513(instance),
		instance['kind'], instance['item_scale'])


def _catalog_bin_keys_1513(world_boxes):
	"""Validate and return every spatial bin touched by exact world boxes."""
	bin_keys = set()
	for world_box in world_boxes:
		bin_keys.update(_bin_keys_for_bounds(*_box_xz_bounds(world_box)))
	return bin_keys


def _index_catalog_instance_1513(contact_bins, key, instance,
		bin_keys=None):
	"""Index one streamed instance into every exact world-footprint bin."""
	if bin_keys is None:
		bin_keys = _catalog_bin_keys_1513(instance['boxes'])
	changed = False
	for bin_key in bin_keys:
		members = contact_bins.setdefault(bin_key, set())
		before = len(members)
		members.add(key)
		changed = changed or len(members) != before
	instance['bin_keys'] = tuple(sorted(bin_keys))
	if changed:
		_bump_spatial_revision_1513()
	return bin_keys


def _falling_initial_matrix_1513(spaceID, chunkID, itemIndex, math_module):
	"""Read the exact pre-animation matrix cached by the pinned manager."""
	import AreaDestructibles
	mgr = getattr(AreaDestructibles, 'g_destructiblesManager', None)
	try:
		manager_space = None if mgr is None else mgr.getSpaceID()
	except Exception as error:
		_isolate_destructible_1513(
			'falling_manager', chunkID, itemIndex, detail=error)
		return None
	if mgr is None or manager_space != spaceID:
		_isolate_destructible_1513(
			'falling_manager', chunkID, itemIndex,
			detail='manager is unavailable for space')
		return None
	matrices = getattr(
		mgr, '_DestructiblesManager__destrInitialMatrices', None)
	if not isinstance(matrices, dict):
		_isolate_destructible_1513(
			'falling_initial_cache', chunkID, itemIndex,
			detail='initial-matrix cache is unavailable')
		return None
	raw_matrix = matrices.get((int(chunkID), int(itemIndex)))
	if raw_matrix is None:
		return None
	try:
		return math_module.Matrix(raw_matrix)
	except Exception as error:
		_isolate_destructible_1513(
			'falling_initial_matrix', chunkID, itemIndex, detail=error)
		return None


def _falling_native_state_1513(spaceID, chunkID, itemIndex, math_module):
	"""Return the initial matrix and whether synthetic collision still owns it.

	The pinned animator keeps a body for spring settling after the first ground
	contact.  Its exact touchdown boundary is deletion of ``touchdownCallback``
	from that body.  Before deletion the moving catalog OBB is a conservative
	contact body; afterwards the native matrix/BSP owns collision by itself.
	"""
	initial_matrix = _falling_initial_matrix_1513(
		spaceID, chunkID, itemIndex, math_module)
	if initial_matrix is None:
		return None, False

	import AreaDestructibles
	animator = getattr(AreaDestructibles, 'g_destructiblesAnimator', None)
	bodies = getattr(
		animator, '_DestructiblesAnimator__bodies', None)
	if not isinstance(bodies, list):
		_isolate_destructible_1513(
			'falling_animator_bodies', chunkID, itemIndex,
			detail='animator body list is unavailable')
		return None, False
	matches = []
	for body in bodies:
		if not isinstance(body, dict):
			_isolate_destructible_1513(
				'falling_animator_body', chunkID, itemIndex,
				detail='animator body is not a dict')
			return None, False
		try:
			body_identity = (int(body['spaceID']), int(body['chunkID']),
				int(body['destrIndex']))
		except (KeyError, TypeError, ValueError, OverflowError):
			_isolate_destructible_1513(
				'falling_animator_identity', chunkID, itemIndex,
				detail='animator body identity is invalid')
			return None, False
		if body_identity == (int(spaceID), int(chunkID), int(itemIndex)):
			matches.append(body)
	if len(matches) > 1:
		_isolate_destructible_1513(
			'falling_animator_ambiguity', chunkID, itemIndex,
			detail='multiple animator bodies match the native identity')
		return None, False
	return initial_matrix, bool(
		matches and 'touchdownCallback' in matches[0])


def _refresh_destroyed_falling_instances_1513(spaceID, authority, now):
	"""Follow each destroyed falling atom's live native transform exactly."""
	instances = globals().get('g_offh_destr_instances', {})
	active = globals().setdefault('g_offh_destr_falling_active', {})
	identities = []
	for identity in sorted(active):
		if _destructible_isolated_1513(*identity):
			continue
		instance = instances.get(identity)
		state = active[identity]
		if instance is None:
			# Canonical destruction can arrive before this chunk is registered.
			continue
		if not isinstance(instance, dict):
			_isolate_destructible_1513(
				'falling_instance_identity', identity[0], identity[1],
				detail='registered instance is not a dict')
			continue
		if instance.get('kind') != 'falling':
			_isolate_destructible_1513(
				'falling_instance_kind', identity[0], identity[1],
				detail='registered instance is not falling')
			continue
		if not authority.is_destroyed(identity[0], identity[1], None):
			continue
		try:
			last_refresh = state['last_refresh']
		except (KeyError, TypeError):
			_isolate_destructible_1513(
				'falling_refresh_state', identity[0], identity[1],
				detail='last-refresh state is invalid')
			continue
		if (last_refresh is not None and
				float(now) - last_refresh < _FALLING_REFRESH_SECONDS):
			continue
		identities.append(identity)
	if not identities:
		return

	import BigWorld
	import Math
	contact_bins = globals().setdefault('g_offh_destr_contact_bins', {})
	for identity in identities:
		chunk_id, item_index = identity
		instance = instances[identity]
		try:
			record = _destructible_catalog['resources'].get(
				instance['filename'])
		except (KeyError, TypeError):
			record = None
		if record is None or record['kind'] != 'falling':
			_isolate_destructible_1513(
				'falling_catalog_identity', chunk_id, item_index,
				detail='catalog record is unavailable or not falling')
			continue
		initial_matrix, synthetic_collision_active = _falling_native_state_1513(
			spaceID, chunk_id, item_index, Math)
		if initial_matrix is None:
			if _destructible_isolated_1513(chunk_id, item_index):
				continue
			# The manager has admitted the canonical result but has not flushed its
			# streamed-chunk queue yet.  Preserve the last exact OBB and retry.
			active[identity]['last_refresh'] = float(now)
			continue
		try:
			matrix = Math.Matrix(BigWorld.wg_getDestructibleMatrix(
				spaceID, chunk_id, item_index))
		except Exception as error:
			_isolate_destructible_1513(
				'falling_matrix_query', chunk_id, item_index, detail=error)
			continue
		try:
			chunk_translation = Math.Vector3(*instance['chunk_translation'])
		except (KeyError, TypeError, ValueError) as error:
			_isolate_destructible_1513(
				'falling_chunk_translation', chunk_id, item_index,
				detail=error)
			continue
		try:
			boxes = _world_catalog_boxes(
				record, matrix, chunk_translation, Math,
				instance.get('box_index'))
		except Exception as error:
			_isolate_destructible_1513(
				'falling_matrix_transform', chunk_id, item_index,
				detail=error)
			continue
		if not boxes:
			_isolate_destructible_1513(
				'falling_collision_boxes', chunk_id, item_index,
				detail='current collision box is unavailable')
			continue
		try:
			current_scale = _matrix_item_scale_1513(matrix, Math)
			initial_scale = float(instance['item_scale'])
		except (KeyError, TypeError, ValueError, RuntimeError) as error:
			_isolate_destructible_1513(
				'falling_scale', chunk_id, item_index, detail=error)
			continue
		if abs(current_scale - initial_scale) > max(
				1.0e-5, initial_scale * 1.0e-5):
			_isolate_destructible_1513(
				'falling_scale_change', chunk_id, item_index,
				detail='initial=%s current=%s' %
					(initial_scale, current_scale))
			continue
		# Validate the complete replacement index before mutating the live one.
		# Any malformed native matrix therefore preserves the previous solid OBB.
		try:
			new_bin_keys = _catalog_bin_keys_1513(boxes)
		except Exception as error:
			_isolate_destructible_1513(
				'falling_collision_index', chunk_id, item_index,
				detail=error)
			continue

		old_bin_keys = instance.get('bin_keys')
		if old_bin_keys is None:
			old_bin_keys = set()
			for world_box in instance['boxes']:
				old_bin_keys.update(_bin_keys_for_bounds(
					*_box_xz_bounds(world_box)))
		for bin_key in old_bin_keys:
			members = contact_bins.get(bin_key)
			if members is None:
				continue
			members.discard(identity)
			if not members:
				del contact_bins[bin_key]
		instance['boxes'] = boxes
		state = active[identity]
		state['last_refresh'] = float(now)
		_index_catalog_instance_1513(
			contact_bins, identity, instance, new_bin_keys)
		if not synthetic_collision_active:
			# The animator deletes touchdownCallback on first ground contact.  Stop
			# following the matrix at that exact boundary, but keep the resting OBB
			# indexed: the ray and sweep seams need the identity to recognise the
			# felled column as broken, and a broken item never blocks.
			del active[identity]


def _decode_mat_info_1513(payload):
	"""Translate the pinned #1513 material-hit ABI to the 0.8.2 law.

	The older engine returned six values and used ``None`` for a miss.  #1513
	always returns seven values and carries the hit/miss bit in element zero.
	Its native tail is ``(itemIndex, chunkID)``; the canonical copied law below
	still consumes ``(chunkID, itemIndex)``.
	Keeping that translation here lets the copied contact law retain its mature
	internal field order without guessing at the native tuple shape.
	"""
	if not isinstance(payload, tuple):
		raise RuntimeError(
			'#1513 wg_getMatInfoNearPoint payload must be a tuple')
	width = len(payload)
	if width != 7:
		raise RuntimeError(
			'#1513 wg_getMatInfoNearPoint payload must contain 7 items; got %d' %
			width)
	(collided, hitPt, surfNormal, matKind, fname,
	 itemIndex, chunkID) = payload
	if type(collided) is not bool:
		raise RuntimeError(
			'#1513 wg_getMatInfoNearPoint collided flag must be bool')
	if not collided:
		return None
	return hitPt, surfNormal, chunkID, itemIndex, matKind, fname


def _runtime_material_descriptor_1513(
		area_destructibles, filename, chunk_id, item_index):
	"""Read one material descriptor without escaping an exact runtime wire.

	A non-empty native filename is identity evidence for this slot, so an
	unreadable or malformed descriptor permanently quarantines that wire.  The
	pinned client may legitimately report an anonymous material; without a name
	the same cache failure is only a failed attempt and remains retryable through
	the exact catalog/matrix path.
	"""
	normalized = _normalized_filename(filename)
	try:
		descriptor = area_destructibles.g_cache.getDescByFilename(filename)
	except Exception as error:
		if normalized:
			_isolate_destructible_1513(
				'material_descriptor', chunk_id, item_index, detail=error)
		return None
	if not isinstance(descriptor, dict) or 'type' not in descriptor:
		if normalized:
			_isolate_destructible_1513(
				'material_descriptor', chunk_id, item_index,
				detail='filename=%s payload=%s' % (
					normalized, type(descriptor).__name__))
		return None
	return descriptor


def _descriptor_value(value, name, default=None):
	"""Read copied mappings or native #1513 component attributes."""
	if isinstance(value, dict):
		return value.get(name, default)
	return getattr(value, name, default)


def _vehicle_hull_bbox(type_descriptor):
	"""Return the native hull bbox without touching disabled LegacyStuff APIs."""
	if type_descriptor is None:
		return None
	hull = _descriptor_value(type_descriptor, 'hull')
	if hull is None:
		raise RuntimeError('#1513 vehicle hull descriptor is unavailable')
	hit_tester = _descriptor_value(hull, 'hitTester')
	if hit_tester is None:
		raise RuntimeError('#1513 hull hit tester is unavailable')
	bbox = getattr(hit_tester, 'bbox', None)
	if bbox is None:
		raise RuntimeError('#1513 hull hit tester bbox is unavailable')
	return bbox

def LOG_DEBUG(*unused_args):
	# The user requested no trace-heavy battle logging.
	pass


def _get_destr_authority():
	from gui.mods.offline_lan_0922 import destructibles_authority
	return destructibles_authority


def set_event_sink(callback):
	global _event_sink
	if callback is not None and not callable(callback):
		raise TypeError('destructible event sink must be callable')
	_event_sink = callback


def _position_payload(pos):
	try:
		return float(pos.x), float(pos.y), float(pos.z)
	except AttributeError:
		return float(pos[0]), float(pos[1]), float(pos[2])


def _publish_destroyed(kind, chunkID, itemIndex, pos, fallYaw=0.0,
		speed=0.0, matKind=None, isShotDamage=False):
	if _event_sink is None:
		return True
	x, y, z = _position_payload(pos)
	event = {
		'destructible_kind': str(kind),
		'chunk_id': int(chunkID),
		'item_index': int(itemIndex),
		'x': x, 'y': y, 'z': z,
		'fall_yaw': float(fallYaw),
		'speed': float(speed),
		'is_shot': bool(isShotDamage),
	}
	if matKind is not None:
		event['mat_kind'] = int(matKind)
	if not _event_sink(event):
		raise RuntimeError('destructible event was not admitted by LAN client')
	return True


def _publish_catalog_once_1513(
		kind, chunk_id, item_index, point, yaw, speed, mat_kind=None):
	"""Retry one native-committed catalog event until LAN admission."""
	key = (int(chunk_id), int(item_index),
		int(mat_kind) if mat_kind is not None else None)
	published = globals().setdefault(
		'g_offh_destr_catalog_published', set())
	if key in published:
		return True
	pending = globals().setdefault(
		'g_offh_destr_catalog_publish_pending', {})
	payload = pending.get(key)
	if payload is None:
		payload = (
			str(kind), key[0], key[1], _position_payload(point),
			float(yaw), float(speed), key[2])
		pending[key] = payload
	try:
		_publish_destroyed(
			payload[0], payload[1], payload[2], payload[3],
			payload[4], payload[5], payload[6])
	except Exception:
		return False
	pending.pop(key, None)
	published.add(key)
	return True


def _retry_catalog_publications_1513():
	"""Retry native-committed catalog events before geometry can move away."""
	pending = globals().get('g_offh_destr_catalog_publish_pending', {})
	failed = set()
	kinds = set()
	for key in sorted(pending):
		payload = pending.get(key)
		if payload is None:
			continue
		if not _publish_catalog_once_1513(
				payload[0], payload[1], payload[2], payload[3],
				payload[4], payload[5], payload[6]):
			failed.add(key)
			kinds.add('structure' if payload[0] == 'module' else
				'falling' if payload[0] == 'column' else 'fragile')
	return failed, kinds


def reset(spaceID=None):
	_clear_runtime_registry()
	if spaceID is not None:
		globals()['g_offh_destr_runtime_space'] = int(spaceID)
	_get_destr_authority().reset(spaceID)


def _try_destroy_destructible(spaceID, matInfo, yaw, vel,
		isShotDamage=False):
	decoded = _decode_mat_info_1513(matInfo)
	if decoded is None:
		return False
	hitPt, surfNormal, chunkID, itemIndex, matKind, fname = decoded
	if _destructible_isolated_1513(chunkID, itemIndex):
		return False
	import AreaDestructibles
	if (not hasattr(AreaDestructibles, 'g_destructiblesManager') or
			not AreaDestructibles.g_destructiblesManager):
		raise RuntimeError('destructibles manager is unavailable')

	_dseen = globals().setdefault('g_offh_destr_seen', set())
	_dkey = (matKind, fname)
	if _dkey not in _dseen:
		_dseen.add(_dkey)
		LOG_DEBUG('Destr hit: matKind=', matKind, 'fname=', repr(fname),
			'chunk=', chunkID, 'idx=', itemIndex)
	# #1513's native material namespace ends at 100.  Do not feed arbitrary BSP
	# material values into destructible encoders.
	if (matKind < _DESTRUCTIBLE_MAT_KIND_MIN_1513 or
			matKind > _DESTRUCTIBLE_MAT_KIND_MAX_1513):
		return False
	desc = _runtime_material_descriptor_1513(
		AreaDestructibles, fname, chunkID, itemIndex)
	if not desc:
		_dnd = globals().setdefault('g_offh_destr_nodesc', set())
		if _dkey not in _dnd:
			_dnd.add(_dkey)
			LOG_DEBUG('Destr no desc: matKind=', matKind,
				'fname=', repr(fname), 'chunk=', chunkID, 'idx=', itemIndex)
		# The native helper can legally return an anonymous destructible; its
		# exact registered/catalog OBB may provide identity later in this contact
		# traversal.  A non-empty native name with no descriptor is contradictory
		# evidence for this exact wire and must remain quarantined.  The shared
		# descriptor reader above records that first divergence.
		return False

	# Data-driven vegetation gate: soft vegetation (bush/shrub/fern)
	# ships with health <= 5; real fallable trees start at 10.
	typ = desc.get('type')
	known_types = (
		AreaDestructibles.DESTR_TYPE_TREE,
		AreaDestructibles.DESTR_TYPE_FALLING_ATOM,
		AreaDestructibles.DESTR_TYPE_FRAGILE,
		AreaDestructibles.DESTR_TYPE_STRUCTURE)
	if typ not in known_types:
		return False
	if typ == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		modules = desc.get('modules')
		if not isinstance(modules, dict):
			if _normalized_filename(fname):
				_isolate_destructible_1513(
					'material_descriptor', chunkID, itemIndex,
					detail='structure modules payload is unavailable')
			return False
		if (not (_STRUCTURE_MAT_KIND_MIN_1513 <= matKind <
				_STRUCTURE_MAT_KIND_MAX_1513) or modules.get(matKind) is None):
			return False
	if _destructible_catalog is not None and typ in (
			AreaDestructibles.DESTR_TYPE_FALLING_ATOM,
			AreaDestructibles.DESTR_TYPE_FRAGILE,
			AreaDestructibles.DESTR_TYPE_STRUCTURE):
		expected_kind = _catalog_kind_for_type_1513(
			AreaDestructibles, typ)
		normalized_fname = _normalized_filename(fname)
		if _destructible_catalog.get('has_instance_index'):
			identity = (int(chunkID), int(itemIndex))
			instance = globals().setdefault(
				'g_offh_destr_instances', {}).get(identity)
			if instance is None:
				instance = _stream_baked_shot_instance_1513(
					spaceID, identity)
			if instance is None:
				# This exact wire is pending, isolated, absent from the catalog, or
				# failed live matrix/category validation.  The native surface stays
				# solid; a global same-kind resource is not admission evidence.
				return False
			if (not isinstance(instance, dict) or
					instance.get('kind') != expected_kind or
					instance.get('filename') != normalized_fname):
				failure_type = ('filename_identity_conflict'
					if isinstance(instance, dict) and
						instance.get('filename') != normalized_fname
					else 'catalog_live_admission')
				_isolate_destructible_1513(
					failure_type, chunkID, itemIndex,
					detail='material=%s admitted=%s material_kind=%s admitted_kind=%s' % (
						normalized_fname,
						instance.get('filename') if isinstance(instance, dict) else None,
						expected_kind,
						instance.get('kind') if isinstance(instance, dict) else None))
				return False
			if expected_kind == 'structure':
				try:
					module_admitted = any(
						box[2] == matKind for box in instance.get('boxes', ()))
				except (IndexError, TypeError):
					module_admitted = False
				if not module_admitted:
					_isolate_destructible_1513(
						'catalog_module_identity', chunkID, itemIndex,
						detail='material=%s admitted_modules=%r' % (
							matKind, tuple(box[2] for box in
								instance.get('boxes', ())
								if isinstance(box, (list, tuple)) and
								len(box) > 2)))
					return False
		else:
			record = _destructible_catalog['resources'].get(normalized_fname)
			if record is None or record['kind'] != expected_kind:
				return False
	if typ == AreaDestructibles.DESTR_TYPE_TREE:
		_hp_gate = desc.get('health', 0)
		try:
			_valid_tree_health = 10 <= _hp_gate <= 1000
		except TypeError:
			_valid_tree_health = False
		if not _valid_tree_health:
			if _normalized_filename(fname) and not isinstance(
					_hp_gate, _INTEGER_TYPES + (float,)):
				_isolate_destructible_1513(
					'material_descriptor', chunkID, itemIndex,
					detail='tree health=%r' % (_hp_gate,))
			return False
		if not validate_tree_identity_1513(
				spaceID, chunkID, itemIndex):
			return False
		# An unloaded tree retains stock queued-order admission.  Once the manager
		# reports the chunk loaded, require this exact slot's reconstructed name;
		# ``validate_tree_identity_1513`` above has already established the same
		# safe descriptor boundary and normally leaves this mapping cached.
		if AreaDestructibles.g_destructiblesManager.isChunkLoaded(chunkID):
			name_status, native_filename = resolve_native_item_name_1513(
				spaceID, chunkID, itemIndex)
			if name_status != 'exact':
				return False
			if (_normalized_filename(native_filename) !=
					_normalized_filename(fname)):
				_isolate_destructible_1513(
					'filename_identity_conflict', chunkID, itemIndex,
					detail='material=%s admitted=%s material_kind=tree' % (
						_normalized_filename(fname),
						_normalized_filename(native_filename)))
				return False
	# All bookkeeping (chunk bootstrap, dedup, encoding) lives in
	# the authority - this path is now just a contact sensor.
	_auth = _get_destr_authority()
	# STRUCTURE (buildings) now falls through to the module-destroy path.
	if _auth.is_destroyed(chunkID, itemIndex, matKind):
		# This only means the order was accepted.  Animated #1513 fragile and
		# module skins remove native collision later, so movement must still
		# re-cast the actual solid ray before it becomes passable.
		return False

	if typ == AreaDestructibles.DESTR_TYPE_TREE:
		_destr_ok = _auth.destroy_tree(
			spaceID, chunkID, itemIndex, yaw, vel, hitPt)
	elif typ == AreaDestructibles.DESTR_TYPE_FALLING_ATOM:
		_destr_ok = _auth.destroy_column(
			spaceID, chunkID, itemIndex, yaw, vel, hitPt)
	elif typ == AreaDestructibles.DESTR_TYPE_FRAGILE:
		_destr_ok = _auth.destroy_fragile(
			spaceID, chunkID, itemIndex, hitPt, isShotDamage)
	elif typ == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		_destr_ok = _auth.destroy_module(
			spaceID, chunkID, itemIndex, matKind, hitPt, isShotDamage)
	else:
		return False
	if not _destr_ok:
		if (typ == AreaDestructibles.DESTR_TYPE_TREE and
				not validate_tree_identity_1513(
					spaceID, chunkID, itemIndex)):
			return False
		raise RuntimeError(
			'native destructible destroy was not accepted: chunk=%s item=%s' %
			(chunkID, itemIndex))
	_invalidate_chunk_native_names_1513(chunkID)
	_event_kind = (
		'tree' if typ == AreaDestructibles.DESTR_TYPE_TREE else
		'column' if typ == AreaDestructibles.DESTR_TYPE_FALLING_ATOM else
		'fragile' if typ == AreaDestructibles.DESTR_TYPE_FRAGILE else
		'module')
	# Fragile/module skins hide after #1513's delayed callback.  Falling atoms
	# instead animate their native matrix and remain in the world at the final
	# pose; their catalog OBB follows that matrix in the motion contact path.
	if _event_kind in ('fragile', 'module', 'column'):
		try:
			import BigWorld
		except ImportError:
			# Unit-level callers may exercise the pure transaction helper
			# without the engine module.  The real #1513 runtime always supplies
			# BigWorld.time; zero still preserves one monotonic pending window.
			_now = 0.0
		else:
			_now = BigWorld.time()
		note_destroyed(
			_event_kind, chunkID, itemIndex,
			matKind if _event_kind == 'module' else None,
			_now)
	_publish_destroyed(
		_event_kind,
		chunkID, itemIndex, hitPt, yaw, vel,
		matKind if typ == AreaDestructibles.DESTR_TYPE_STRUCTURE else None,
		isShotDamage)
	return True


def _drop_streamed_chunk_registry_1513(state, chunk_id):
	"""Drop stale streamed geometry while preserving canonical destroy state."""
	chunk_id = int(chunk_id)
	_invalidate_chunk_native_names_1513(chunk_id)
	changed = state.get('chunks', {}).pop(chunk_id, None) is not None
	instances = globals().get('g_offh_destr_instances', {})
	contact_bins = globals().get('g_offh_destr_contact_bins', {})
	for identity in [key for key in list(instances) if key[0] == chunk_id]:
		instance = instances.pop(identity)
		changed = True
		bin_keys = (instance.get('bin_keys')
			if isinstance(instance, dict) else None)
		if bin_keys is None:
			bin_keys = list(contact_bins)
		for bin_key in bin_keys:
			members = contact_bins.get(bin_key)
			if members is None:
				continue
			members.discard(identity)
			if not members:
				contact_bins.pop(bin_key, None)
	if changed:
		_bump_spatial_revision_1513()
	return changed


def _tree_runtime_state_1513(space_id=None):
	state = globals().setdefault('g_offh_tree_state', {
		'chunks': {}, 'felled': set(), 'spaceID': None})
	if space_id is not None and state.get('spaceID') != int(space_id):
		return None
	native_committed = state.setdefault(
		'native_committed', state.setdefault('felled', set()))
	state['felled'] = native_committed
	state.setdefault('canonical_published', set())
	state.setdefault('publish_pending', {})
	return state


def _tree_motion_detail_1513(status, token=None, accepted_now=False,
		requires_commit=False):
	return {
		'status': str(status),
		'token': tuple(sorted(token or ())) or None,
		'accepted_now': bool(accepted_now),
		'kinds': 'tree',
		'requires_commit': bool(requires_commit),
	}


def _tree_motion_axis_samples_1513(minimum, maximum):
	import math
	minimum = float(minimum)
	maximum = float(maximum)
	span = maximum - minimum
	segments = max(1, int(math.ceil(
		span / (_DESTRUCTIBLE_CHUNK_METRES_1513 * 0.5))))
	if segments > 32:
		return None
	return tuple(minimum + span * float(index) / float(segments)
		for index in range(segments + 1))


def _tree_motion_required_chunks_1513(sweep_boxes):
	"""Map only chunk cells intersected by the segmented sweep broadphase."""
	import AreaDestructibles
	import Math
	mapper = getattr(AreaDestructibles, 'chunkIDFromPosition', None)
	if not callable(mapper):
		return 'hard', ()
	chunk_ids = set()
	for sweep_box in sweep_boxes:
		minimum_x, maximum_x, minimum_z, maximum_z = _box_xz_bounds(
			sweep_box)
		minimum_x -= _SOLID_CONTACT_RADIUS_1513
		maximum_x += _SOLID_CONTACT_RADIUS_1513
		minimum_z -= _SOLID_CONTACT_RADIUS_1513
		maximum_z += _SOLID_CONTACT_RADIUS_1513
		x_values = _tree_motion_axis_samples_1513(minimum_x, maximum_x)
		z_values = _tree_motion_axis_samples_1513(minimum_z, maximum_z)
		if x_values is None or z_values is None:
			return 'hard', ()
		y = float(sweep_box[0][1])
		for x in x_values:
			for z in z_values:
				try:
					chunk_id = mapper(Math.Vector3(x, y, z))
				except Exception:
					return 'pending', ()
				if chunk_id is None:
					continue
				if (isinstance(chunk_id, bool) or
						not isinstance(chunk_id, _INTEGER_TYPES) or
						int(chunk_id) < 0):
					return 'hard', ()
				chunk_ids.add(int(chunk_id))
	if not chunk_ids:
		return 'pending', ()
	return 'ready', tuple(sorted(chunk_ids))


def prewarm_tree_registry(spaceID, pos, yaw, td=None, now=None):
	"""Build nearby exact native registries without destroying any object."""
	try:
		result = _fell_trees_near(
			spaceID, pos, yaw, 0.0, td, registration_only=True)
	except Exception:
		result = None
	if isinstance(result, dict):
		return result
	return {
		'status': 'pending', 'ready_chunks': (),
		'pending_chunks': (), 'isolated_chunks': (),
	}


def _tree_motion_resolution_1513(
		spaceID, start_pos, start_yaw, end_pos, end_yaw, speed, td, now,
		dt, requested_chunks=None):
	"""Return registry-complete tree candidates for one trusted pose sweep."""
	speed = _finite_tree_motion_value_1513(speed)
	dt = _finite_tree_motion_value_1513(dt)
	if speed is None or dt is None or dt <= 0.0 or dt > 0.25:
		return 'hard', {}, set(), {}, set()
	bbox = _vehicle_hull_bbox(td)
	if bbox is None:
		return 'hard', {}, set(), {}, set()
	sweep_boxes = _tree_pose_sweep_boxes_1513(
		start_pos, start_yaw, end_pos, end_yaw, bbox)
	if not sweep_boxes:
		return 'hard', {}, set(), {}, set()
	# One registration pass covers the current chunk and its eight neighbours.
	# Do not repeat the complete native alignment for every sweep slice: the
	# shared 16-query frame budget must advance one focused chunk predictably.
	prewarm_tree_registry(spaceID, end_pos, end_yaw, td, now)
	required_status, required_chunks = (
		_tree_motion_required_chunks_1513(sweep_boxes))
	if required_status == 'hard':
		return required_status, {}, set(), {}, set()
	if required_status != 'ready':
		required_chunks = ()
	scan_chunks = set(required_chunks)
	for chunk_id in requested_chunks or ():
		if (isinstance(chunk_id, bool) or
				not isinstance(chunk_id, _INTEGER_TYPES) or
				int(chunk_id) < 0):
			return 'hard', {}, set(), {}, set()
		scan_chunks.add(int(chunk_id))
	state = _tree_runtime_state_1513(spaceID)
	chunk_status = {}
	for chunk_id in sorted(scan_chunks):
		if _destructible_isolated_1513(chunk_id):
			chunk_status[chunk_id] = 'hard'
		elif state is None or chunk_id not in state.get('chunks', {}):
			chunk_status[chunk_id] = 'pending'
		else:
			chunk_status[chunk_id] = 'ready'
	try:
		import AreaDestructibles
		tree_type = AreaDestructibles.DESTR_TYPE_TREE
	except (AttributeError, ImportError):
		return 'hard', {}, set(), chunk_status, set()
	candidates = {}
	isolated_hits = set()
	for chunk_id in sorted(scan_chunks):
		if chunk_status.get(chunk_id) != 'ready':
			continue
		chunk_candidates, chunk_isolated_hits = (
			_tree_candidates_for_sweeps_1513(
				chunk_id, state['chunks'][chunk_id], sweep_boxes, tree_type))
		candidates.update(chunk_candidates)
		isolated_hits.update(chunk_isolated_hits)
	if len(candidates) > _TREE_CONTACT_TOKEN_LIMIT_1513:
		return 'hard', {}, set(), chunk_status, isolated_hits
	try:
		authority = _get_destr_authority()
		active = set(identity for identity in candidates
			if not authority.is_destroyed(*identity))
	except Exception:
		active = set(candidates)
	# A terminal chunk quarantine has no exact positional proof and must not
	# create an endless pending retry.  Exact candidates in ready chunks remain
	# actionable even when another intersected chunk is pending or isolated.
	status = ('ready' if candidates or (
		required_status == 'ready' and not any(
			value == 'pending' for value in chunk_status.values())) else 'pending')
	return status, candidates, active, chunk_status, isolated_hits


def _tree_motion_proposal(
		spaceID, start_pos, start_yaw, end_pos, end_yaw, speed, td, now,
		dt=0.04):
	"""Return a mutation-free exact tree token for a continuous hull sweep."""
	_diagnostic_flush_1513(now)
	status, candidates, active, unused_chunk_status, isolated_hits = (
		_tree_motion_resolution_1513(
		spaceID, start_pos, start_yaw, end_pos, end_yaw, speed, td, now, dt)
	)
	# A position-proven isolated identity is a terminal conflict for this exact
	# contact even when an unrelated intersected chunk is still streaming.
	if not candidates and isolated_hits:
		return _tree_motion_detail_1513('hard')
	if status != 'ready':
		return _tree_motion_detail_1513(status)
	if not candidates:
		return _tree_motion_detail_1513('clear')
	return _tree_motion_detail_1513(
		'crushed', set(candidates), accepted_now=False,
		requires_commit=bool(active))


def _parse_tree_contact_token_1513(token):
	if not isinstance(token, (list, tuple, set, frozenset)):
		return None
	result = set()
	for row in token:
		if not isinstance(row, (list, tuple)) or len(row) != 3:
			return None
		chunk_id, item_index, mat_kind = row
		if (isinstance(chunk_id, bool) or
				not isinstance(chunk_id, _INTEGER_TYPES) or
				int(chunk_id) < 0 or
				isinstance(item_index, bool) or
				not isinstance(item_index, _INTEGER_TYPES) or
				int(item_index) < 0 or mat_kind is not None):
			return None
		result.add((int(chunk_id), int(item_index), None))
	if not result or len(result) > _TREE_CONTACT_TOKEN_LIMIT_1513:
		return None
	return result


def _tree_commit_identity_status_1513(
		spaceID, identity, item, authority):
	chunk_id, item_index, mat_kind = identity
	try:
		if authority.is_destroyed(chunk_id, item_index, mat_kind):
			return 'destroyed'
	except Exception:
		return 'pending'
	status, filename = resolve_native_item_name_1513(
		spaceID, chunk_id, item_index)
	if status == 'pending':
		return 'pending'
	if (status != 'exact' or
			_normalized_filename(filename) !=
			_normalized_filename(item[5])):
		if status == 'exact':
			_isolate_destructible_1513(
				'tree_prediction_name', chunk_id, item_index,
				detail='registry=%r native=%r' % (item[5], filename))
		return 'hard'
	try:
		valid = validate_tree_identity_1513(
			spaceID, chunk_id, item_index)
	except Exception:
		return 'pending'
	if valid:
		return 'ready'
	return ('hard' if _destructible_isolated_1513(
		chunk_id, item_index) else 'pending')


def _publish_tree_once_1513(
		state, spaceID, identity, object_pos, fall_yaw, speed):
	key = identity[:2]
	if key in state['canonical_published']:
		return True
	payload = state['publish_pending'].get(key)
	if payload is None:
		payload = (
			int(spaceID), int(identity[0]), int(identity[1]),
			(float(object_pos.x), float(object_pos.y), float(object_pos.z)),
			float(fall_yaw), float(speed))
		state['publish_pending'][key] = payload
	try:
		_publish_destroyed(
			'tree', payload[1], payload[2], payload[3], payload[4],
			payload[5])
	except Exception:
		return False
	state['publish_pending'].pop(key, None)
	state['canonical_published'].add(key)
	return True


def _commit_tree_contacts_1513(
		spaceID, token, start_pos, start_yaw, end_pos, end_yaw, speed, td,
		now, dt, publish):
	requested = _parse_tree_contact_token_1513(token)
	if requested is None:
		return _tree_motion_detail_1513('hard')
	status, candidates, unused_active, chunk_status, isolated_hits = (
		_tree_motion_resolution_1513(
		spaceID, start_pos, start_yaw, end_pos, end_yaw, speed, td, now, dt,
		set(identity[0] for identity in requested))
	)
	if status == 'hard':
		return _tree_motion_detail_1513(status)
	requested_chunk_status = set(chunk_status.get(identity[0], 'pending')
		for identity in requested)
	if 'hard' in requested_chunk_status:
		return _tree_motion_detail_1513('hard')
	if 'pending' in requested_chunk_status:
		return _tree_motion_detail_1513('pending')
	if requested.intersection(isolated_hits):
		return _tree_motion_detail_1513('hard')
	if not requested.issubset(set(candidates)):
		return _tree_motion_detail_1513('hard')
	authority = _get_destr_authority()
	identity_status = {}
	for identity in sorted(requested):
		identity_status[identity] = _tree_commit_identity_status_1513(
			spaceID, identity, candidates[identity], authority)
	if any(value == 'hard' for value in identity_status.values()):
		return _tree_motion_detail_1513('hard')
	if any(value == 'pending' for value in identity_status.values()):
		return _tree_motion_detail_1513('pending')
	import Math
	dx = float(end_pos.x) - float(start_pos.x)
	dz = float(end_pos.z) - float(start_pos.z)
	distance = (dx * dx + dz * dz) ** 0.5
	if distance > 1.0e-8:
		import math
		fall_yaw = math.atan2(dx, dz)
	else:
		fall_yaw = float(end_yaw)
		if float(speed) < 0.0:
			import math
			fall_yaw += math.pi
	fall_speed = float(speed)
	state = _tree_runtime_state_1513(spaceID)
	if state is None:
		return _tree_motion_detail_1513('pending')
	accepted_now = False
	object_positions = {}
	for identity in sorted(requested):
		item = candidates[identity]
		object_pos = Math.Vector3(item[1], item[2], item[3])
		object_positions[identity] = object_pos
		if identity_status[identity] == 'destroyed':
			state['native_committed'].add(identity[:2])
			continue
		try:
			accepted = authority.destroy_tree(
				spaceID, identity[0], identity[1], fall_yaw,
				fall_speed, object_pos)
		except Exception:
			accepted = False
		if not accepted:
			try:
				accepted = authority.is_destroyed(*identity)
			except Exception:
				accepted = False
		if not accepted:
			return _tree_motion_detail_1513('pending')
		accepted_now = True
		state['native_committed'].add(identity[:2])
		_invalidate_chunk_native_names_1513(identity[0])
	if publish:
		for identity in sorted(requested):
			if not _publish_tree_once_1513(
					state, spaceID, identity, object_positions[identity],
					fall_yaw, fall_speed):
				return _tree_motion_detail_1513('pending')
	return _tree_motion_detail_1513(
		'crushed', requested, accepted_now=accepted_now,
		requires_commit=False)


def commit_local_tree_prediction(
		spaceID, token, start_pos, start_yaw, end_pos, end_yaw, speed, td,
		now, dt=0.04, publish=False):
	"""Apply the trusted visible client's exact tree token locally."""
	return _commit_tree_contacts_1513(
		spaceID, token, start_pos, start_yaw, end_pos, end_yaw, speed, td,
		now, dt, bool(publish))


def commit_tree_contacts(
		spaceID, token, start_pos, start_yaw, end_pos, end_yaw, speed, td,
		now, dt=0.04, publish=True):
	"""Commit and publish a worker-validated exact tree contact token."""
	return _commit_tree_contacts_1513(
		spaceID, token, start_pos, start_yaw, end_pos, end_yaw, speed, td,
		now, dt, bool(publish))


def _fell_trees_near(
		spaceID, pos, yaw, vel, td=None, registration_only=False):
	# Offline tree/pole felling. Online the SERVER detected tank-vs-tree
	# contact; the client-side collision probes never return tree/column
	# materials, so trees could never fall offline. Instead: enumerate
	# each chunk's destructibles once (filename + world matrix), then
	# fell TREE / FALLING_ATOM items that intersect the moving hull.
	import math
	import AreaDestructibles
	import BigWorld
	import Math
	try:
		mgr = getattr(AreaDestructibles, 'g_destructiblesManager', None)
		if not mgr:
			raise RuntimeError('destructibles manager is unavailable')
		structure_type = getattr(
			AreaDestructibles, 'DESTR_TYPE_STRUCTURE', None)
		if mgr.getSpaceID() != spaceID:
			# Countdown prewarm is the earliest safe battle-owned caller.  Bind a
			# stale cross-battle manager now so later registration ticks can consume
			# fresh onChunkLoad counts instead of deferring this reset to contact.
			mgr.startSpace(spaceID)
		if globals().get('g_offh_destr_runtime_space') != spaceID:
			_clear_runtime_registry()
			globals()['g_offh_destr_runtime_space'] = int(spaceID)
		_st = globals().setdefault('g_offh_tree_state', {'chunks': {}, 'felled': set(), 'spaceID': None})
		if _st.get('spaceID') != spaceID:
			# New battle/space: chunk IDs collide between maps and the
			# dedup sets would suppress destruction of fresh objects.
			_st['chunks'] = {}
			_st['felled'] = set()
			_st['native_committed'] = _st['felled']
			_st['canonical_published'] = set()
			_st['publish_pending'] = {}
			_st['spaceID'] = spaceID
			globals().setdefault('g_offh_destr_ordered', set())
			globals().setdefault('g_offh_destr_chunks', set())
			globals().setdefault('g_offh_destr_seen', set())
			globals().setdefault('g_offh_destr_instances', {})
			globals().setdefault('g_offh_destr_contact_bins', {})
			globals().setdefault('g_offh_destr_pending', {})
			globals().setdefault('g_offh_destr_falling_active', {})
		_st = _tree_runtime_state_1513(spaceID)
		cos_y = math.cos(yaw); sin_y = math.sin(yaw)
		bbox = ((-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None)
		bbox = _vehicle_hull_bbox(td)
		if bbox is None:
			bbox = ((-1.6, -1.0, -3.6), (1.6, 1.0, 3.6), None)
		try:
			hw = max(abs(bbox[0][0]), abs(bbox[1][0]))
			hl_b = abs(bbox[0][2])
			hl_f = abs(bbox[1][2])
		except (AttributeError, KeyError, TypeError, IndexError):
			raise RuntimeError('#1513 hull hit tester bbox is invalid')
		vehicle_box = _vehicle_swept_box(pos, yaw, vel, bbox)
		_current_cid = AreaDestructibles.chunkIDFromPosition(
			Math.Vector3(pos.x, pos.y, pos.z))
		_receipt_key = None
		if _current_cid is not None and _destructible_catalog is not None:
			_receipt_key = _proximity_receipt_key_1513(
				spaceID, _current_cid, pos, vehicle_box)
			if (not registration_only and
					_empty_proximity_receipt_valid_1513(
					_receipt_key, mgr, _st['chunks'])):
				return
		cids = set((_current_cid,))
		_mapped_cid = AreaDestructibles.chunkIDFromPosition(
			Math.Vector3(pos.x + sin_y * (6.0 if vel >= 0 else -6.0),
				pos.y, pos.z + cos_y * (6.0 if vel >= 0 else -6.0)))
		cids.add(_mapped_cid)
		_prewarm_priority = {}
		# #1513 chunks are 100 m squares.  Catalog instances can be non-uniformly
		# scaled, so raw resource bounds cannot determine the origin reach.  Sample
		# the current chunk plus all eight neighbours through the native mapper.
		# Registration-only tree prewarm deliberately uses the same neighbourhood:
		# at 16 name probes per frame it starts the next chunk before contact rather
		# than waiting 0.5-1 seconds after the vehicle crosses the boundary.
		if _destructible_catalog is not None or registration_only:
			for _offset_x in (-_DESTRUCTIBLE_CHUNK_METRES_1513, 0.0,
					_DESTRUCTIBLE_CHUNK_METRES_1513):
				for _offset_z in (-_DESTRUCTIBLE_CHUNK_METRES_1513, 0.0,
						_DESTRUCTIBLE_CHUNK_METRES_1513):
					_neighbour_cid = AreaDestructibles.chunkIDFromPosition(
						Math.Vector3(pos.x + _offset_x, pos.y,
							pos.z + _offset_z))
					cids.add(_neighbour_cid)
					if _neighbour_cid is not None:
						_forward_offset = (
							_offset_x * sin_y + _offset_z * cos_y)
						_lateral_offset = abs(
							_offset_x * cos_y - _offset_z * sin_y)
						_prewarm_priority[_neighbour_cid] = max(
							_prewarm_priority.get(
								_neighbour_cid,
								(-float('inf'), -float('inf'))),
							(_forward_offset, -_lateral_offset))
		cids.discard(None)
		instances = globals().setdefault('g_offh_destr_instances', {})
		contact_bins = globals().setdefault(
			'g_offh_destr_contact_bins', {})
		_found_nearby = False
		_cid_order = sorted(cids)
		if registration_only:
			# Finish the occupied chunk first, then the most forward mapped
			# neighbours.  This makes the single shared 16-query budget useful
			# for the chunk the vehicle will enter instead of depending on opaque
			# numeric chunk-ID ordering.
			_cid_order = sorted(cids, key=lambda cid: (
				0 if cid == _current_cid else 1,
				-_prewarm_priority.get(cid, (0.0, 0.0))[0],
				-_prewarm_priority.get(cid, (0.0, 0.0))[1], cid))
		for cid in _cid_order:
			if _destructible_isolated_1513(cid):
				continue
			registry = _st['chunks'].get(cid)
			_native_count = _native_chunk_destructible_count_1513(mgr, cid)
			if (registry is not None and
					(_native_count is None or
					registry.get('native_count') != _native_count)):
				_drop_streamed_chunk_registry_1513(_st, cid)
				registry = None
			if registry is None:
				if _native_count is None:
					if _destructible_isolated_1513(cid):
						continue
					if cid == _current_cid:
						_diagnostic_chunk_pending_1513(
							'count_pending', cid)
					# ``game.wg_onChunkLoad`` has not admitted this chunk yet. Do not
					# infer a count from the possibly compacted name list, whose length is only
					# a lower bound; retry after the native streaming callback
					# populates the manager map.
					continue
				# The pinned chunk name list may be compacted, so recover the exact
				# ``item_index -> filename`` mapping under the cardinality contract.
				_names, _names_status = _chunk_native_name_list_1513(
					BigWorld, spaceID, cid, _native_count)
				if _names_status == 'pending':
					if cid == _current_cid:
						_diagnostic_chunk_pending_1513(
							'names_pending', cid, _native_count)
					continue # chunk not streamed in yet; retry next tick
				if _names is None:
					continue
				# Complete the bounded native alignment before admitting any slot.
				# A shorter list uses per-type reconstruction; a full-width list keeps
				# its positions.  An incomplete alignment retries on a later scan and a
				# terminal evidence failure isolates the whole chunk.
				_item_names, _names_status = _chunk_native_names_1513(
					BigWorld, AreaDestructibles, spaceID, cid,
					_native_count, _names)
				if _names_status == 'pending_alignment':
					if cid == _current_cid:
						_diagnostic_chunk_pending_1513(
							'name_alignment_pending', cid, _native_count)
					continue
				if (_item_names is None or
						_destructible_isolated_1513(cid)):
					continue
				registry = {
					'bins': {}, 'extended_bins': {}, 'count': 0,
					'native_count': _native_count,
					'max_radius': 0.0, 'slot_diagnostics': {},
				}
				_retry_registry = False
				try:
					_cm_t = BigWorld.wg_getChunkMatrix(
						spaceID, cid).translation
				except Exception as error:
					_isolate_destructible_1513(
						'native_chunk_matrix', cid, detail=error)
					continue
				if _cm_t is None:
					continue
				for _ti in xrange(_native_count):
					try:
						if _destructible_isolated_1513(cid, _ti):
							continue
						# Recover the resource through the checksum-pinned whole-map
						# instance signature.  The already aligned exact native name is
						# an independent identity channel.
						_is_active_falling = ((cid, _ti) in globals().setdefault(
							'g_offh_destr_falling_active', {}))
						_baked_slot = ((_destructible_catalog or {}).get(
							'baked_instances', {}).get((int(cid), int(_ti))))
						_is_known_falling = (_is_active_falling or
							(_baked_slot is not None and
								_baked_slot.get('kind') == 'falling'))
						try:
							_m = Math.Matrix(BigWorld.wg_getDestructibleMatrix(
								spaceID, cid, _ti))
						except Exception as error:
							_isolate_destructible_1513(
								('falling_matrix_query' if _is_known_falling else
								 'native_matrix_query'),
								cid, _ti, detail=error)
							continue
						_raw_filename = _item_names.get(_ti) or ''
						_raw_normalized = _normalized_filename(_raw_filename)
						_slot_diag = {
							'raw': ('named' if _raw_normalized else 'blank'),
							'signature_state': 'none',
							'effect_category': '-',
							'result': 'pending',
							'boxes': 0,
							'raw_mismatch': False,
							'raw_conflict': False,
						}
						# Retained for the whole battle, one dict per native slot
						# including every tree, so only keep it when a reader exists.
						if _DIAGNOSTICS_ENABLED:
							registry['slot_diagnostics'][_ti] = _slot_diag
						if _is_active_falling:
							# A falling item's live matrix is no longer its catalog
							# placement and can even quantize to another valid resource.
							# Recover identity only from the exact pre-animation cache;
							# the live matrix below remains authoritative for its OBB.
							_initial_matrix = _falling_initial_matrix_1513(
								spaceID, cid, _ti, Math)
							if _initial_matrix is None:
								if _destructible_isolated_1513(cid, _ti):
									_slot_diag['result'] = 'isolated'
									continue
								_retry_registry = True
								_slot_diag['result'] = 'native_matrix_pending'
								continue
							try:
								_signature, _located = (
									_catalog_instance_for_matrix_1513(
										_initial_matrix, _cm_t, Math))
							except Exception as error:
								_isolate_destructible_1513(
									'falling_initial_matrix', cid, _ti,
									detail=error)
								_slot_diag['result'] = 'isolated'
								continue
						else:
							try:
								_signature, _located = (
									_catalog_instance_for_matrix_1513(
										_m, _cm_t, Math))
							except Exception as error:
								_isolate_destructible_1513(
									'native_matrix_signature', cid, _ti,
									detail=error)
								_slot_diag['result'] = 'isolated'
								continue
						if (_destructible_catalog is not None and
								_destructible_catalog.get('has_instance_index')):
							if _signature in _destructible_catalog[
									'ambiguous_instances']:
								_slot_diag['signature_state'] = 'ambig'
								# Multiple native identities have exactly the same
								# matrix. No native slot may select one without
								# guessing, even when it owns a name.
								_isolate_destructible_1513(
									'catalog_signature_ambiguous', cid, _ti,
									detail='signature=%r' % (_signature,))
								_slot_diag['result'] = 'isolated'
								continue
							_slot_diag['signature_state'] = (
								'unique' if _located is not None else 'miss')
							if _located is None and not _raw_normalized:
								# An unnamed item needs the catalog placement as its
								# only resource identity.  Without one, canonical replay
								# must not later treat the wire as admitted.
								_isolate_destructible_1513(
									'catalog_signature_miss', cid, _ti,
									detail='unnamed signature=%r' % (_signature,))
								_slot_diag['result'] = 'isolated'
								continue
						_instance_box_index = None
						if _located is not None:
							if _located['wire'] != (int(cid), int(_ti)):
								_live_wire = (int(cid), int(_ti))
								_baked_wire = _located['wire']
								_wire_detail = 'live=%r baked=%r' % (
									_live_wire, _baked_wire)
								_isolate_destructible_1513(
									'wire_identity_mismatch', cid, _ti,
									detail=_wire_detail)
								_isolate_destructible_1513(
									'wire_identity_mismatch', _baked_wire[0],
									_baked_wire[1], detail=_wire_detail)
								_slot_diag['result'] = 'isolated'
								continue
							_located_kind = _destructible_catalog['resources'][
								_located['filename']]['kind']
							_name_class, _live_kind = (
								_live_filename_identity_1513(
									AreaDestructibles, _raw_filename,
									_raw_normalized, _located['filename'],
									_located_kind))
							if _name_class not in ('none', 'match'):
								_slot_diag['raw_mismatch'] = True
							_name_detail = (
								'native=%s catalog=%s native_kind=%s '
								'catalog_kind=%s names=%s' % (
									_raw_normalized, _located['filename'],
									_live_kind, _located_kind, _names_status))
							if _name_class == 'conflict':
								# This item's own exact native name resolves to a
								# different native kind than the catalog instance
								# sharing its transform, so an exact matrix and an
								# exact wire are not enough.  The wire is already
								# proved equal above, so one isolation covers both
								# sides of the conflicting mapping and keeps it out of
								# every native descriptor, effect and destroy call.
								_isolate_destructible_1513(
									'filename_identity_conflict', cid, _ti,
									detail=_name_detail)
								_slot_diag['raw_conflict'] = True
								_slot_diag['result'] = 'isolated'
								continue
							_catalog_record = _destructible_catalog[
								'resources'][_located['filename']]
							_filename = _catalog_record['filename']
							_instance_box_index = _located['box_index']
						else:
							_filename = _raw_filename
							_catalog_record = None
							if (_raw_normalized and
									_destructible_catalog is not None):
								_catalog_record = _destructible_catalog[
									'resources'].get(_raw_normalized)
								if (_catalog_record is not None and
										_destructible_catalog.get(
											'has_instance_index')):
									# A v4 catalog resource without its exact placement
									# signature is not this native item.
									_isolate_destructible_1513(
										'catalog_signature_miss', cid, _ti,
										detail='filename=%s signature=%r' % (
											_raw_normalized, _signature))
									_slot_diag['result'] = 'isolated'
									continue
						try:
							desc = AreaDestructibles.g_cache.getDescByFilename(
								_filename)
						except Exception as error:
							_isolate_destructible_1513(
								'native_descriptor', cid, _ti, detail=error)
							_slot_diag['result'] = 'isolated'
							continue
						if desc is None:
							# This item's own exact native name has no client
							# descriptor, so it cannot be animated or destroyed safely.
							# An unnamed item can still be a real non-tree object and
							# stays solid and available to catalog recovery.
							if _raw_normalized or _catalog_record is not None:
								_isolate_destructible_1513(
									('native_descriptor' if _raw_normalized else
									 'catalog_descriptor'),
									cid, _ti,
									detail='named filename has no descriptor')
								_slot_diag['result'] = 'isolated'
								continue
							_slot_diag['result'] = 'desc_missing'
							continue
						try:
							typ = desc['type']
						except Exception as error:
							_isolate_destructible_1513(
								'native_descriptor', cid, _ti, detail=error)
							_slot_diag['result'] = 'isolated'
							continue
						_descriptor_kind = _catalog_kind_for_type_1513(
							AreaDestructibles, typ)
						if (_catalog_record is not None and
								_catalog_record['kind'] != _descriptor_kind):
							_isolate_destructible_1513(
								'catalog_kind_identity', cid, _ti,
								detail='filename=%s descriptor_kind=%s '
									'catalog_kind=%s' % (
										_raw_normalized, _descriptor_kind,
										_catalog_record['kind']))
							_slot_diag['result'] = 'isolated'
							continue
						if typ in (AreaDestructibles.DESTR_TYPE_FRAGILE,
								structure_type,
								AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
							_expected_kind = _descriptor_kind
							if _catalog_record is None:
								if _destructible_catalog is not None:
									_isolate_destructible_1513(
										'catalog_identity_missing', cid, _ti,
										detail='filename=%s descriptor_kind=%s' % (
											_raw_normalized, _expected_kind))
									_slot_diag['result'] = 'isolated'
								else:
									_slot_diag['result'] = 'kind_mismatch'
								continue
							if _located is not None:
								if not _validate_native_effect_categories_1513(
										BigWorld, AreaDestructibles, _catalog_record,
										desc,
										spaceID, cid, _ti):
									_slot_diag['result'] = 'effect_mismatch'
									continue
								_slot_diag['effect_category'] = _expected_kind
						elif typ not in (
								AreaDestructibles.DESTR_TYPE_TREE,
								AreaDestructibles.DESTR_TYPE_FALLING_ATOM):
							_slot_diag['result'] = 'type_unsupported'
							continue
						# Data-driven vegetation gate: destructibles.xml gives
						# soft vegetation (bushes/shrubs/ferns/weeds) health<=5
						# (or -2); real fallable trees start at health 10.
						# ChristmasTree sentinels use 40000 = unrammable.
						if typ == AreaDestructibles.DESTR_TYPE_TREE:
							_hp_gate = desc.get('health', 0)
							if _hp_gate < 10 or _hp_gate > 1000:
								_slot_diag['result'] = 'health_gate'
								continue
						# Destructible matrices are CHUNK-LOCAL: world pos =
						# chunk translation + destructible translation
						# (see AreaDestructibles.__launchEffect)
						try:
							if _catalog_record is None:
								_origin = (_cm_t.x + _m.translation.x,
									_cm_t.y + _m.translation.y,
									_cm_t.z + _m.translation.z)
							else:
								_origin = _matrix_point(
									_m, Math, 0.0, 0.0, 0.0, _cm_t)
							_world_boxes = ()
							_contact_radius = 0.0
							_item_scale = None
							if _catalog_record is not None:
								_item_scale = _matrix_item_scale_1513(_m, Math)
								_world_boxes = _world_catalog_boxes(
									_catalog_record, _m, _cm_t, Math,
									_instance_box_index)
								if not _world_boxes:
									_isolate_destructible_1513(
										('falling_collision_boxes'
										 if _catalog_record['kind'] == 'falling'
										 else 'native_collision_boxes'),
										cid, _ti,
										detail='collision box is unavailable')
									_slot_diag['result'] = 'isolated'
									continue
								for _world_box in _world_boxes:
									_center, _half_axes = _world_box[:2]
									_center_radius = ((_center[0] - _origin[0]) ** 2 +
										(_center[2] - _origin[2]) ** 2) ** 0.5
									_horizontal_radius = sum(
										(axis[0] * axis[0] + axis[2] * axis[2]) ** 0.5
										for axis in _half_axes)
									_contact_radius = max(
										_contact_radius,
										_center_radius + _horizontal_radius)
						except Exception as error:
							_isolate_destructible_1513(
								('falling_matrix_transform'
								 if (_catalog_record is not None and
									_catalog_record['kind'] == 'falling')
								 else 'native_matrix_transform'),
								cid, _ti,
								detail=error)
							_slot_diag['result'] = 'isolated'
							continue
						_item = (
							_ti, _origin[0], _origin[1], _origin[2], typ,
							_filename, desc.get('health', 0),
							desc.get('mass', 0), _world_boxes,
							_contact_radius)
						if _catalog_record is not None:
							instances[(cid, _ti)] = {
								'filename': _normalized_filename(_filename),
								'descriptor_filename': _filename,
								'kind': _catalog_record['kind'],
								'boxes': _world_boxes,
								'item_scale': _item_scale,
								'box_index': _instance_box_index,
								'signature': (tuple(_signature)
									if _signature is not None else None),
								'chunk_translation': (
									float(_cm_t.x), float(_cm_t.y), float(_cm_t.z)),
							}
						if _world_boxes:
							_item_bins = registry['extended_bins']
							_bin_keys = _index_catalog_instance_1513(
								contact_bins, (cid, _ti),
								instances[(cid, _ti)])
							for _bin_key in _bin_keys:
								_item_bins.setdefault(_bin_key, []).append(_item)
						else:
							registry['bins'].setdefault(
								_destructible_bin_key(_item[1], _item[3]), []).append(
									_item)
						registry['count'] += 1
						registry['max_radius'] = max(
							registry['max_radius'], _contact_radius)
						_slot_diag['result'] = 'registered_%s' % (
							_catalog_record['kind']
							if _catalog_record is not None else 'tree')
						_slot_diag['boxes'] = len(_world_boxes)
					except Exception as error:
						# Every operation in this loop belongs to one exact native
						# slot.  Keep a malformed descriptor/transform local instead
						# of aborting the whole chunk or proximity callback.
						_isolate_destructible_1513(
							'native_slot_registry', cid, _ti, detail=error)
						continue
				if not _retry_registry:
					_st['chunks'][cid] = registry
					_bump_spatial_revision_1513()
					_diagnostic_chunk_1513(
						cid, _native_count, _names, _item_names,
						_names_status, registry)
				LOG_DEBUG('DestrTree: chunk registry', cid,
					registry['count'], 'trees/poles')
			if registration_only:
				continue
			if not registry['count']:
				continue
			_tree_vehicle_box = vehicle_box
			if vel < 0.0:
				# Preserve the legacy scanner's fixed 0.8 m reverse reach.  Its
				# velocity-scaled look-ahead historically applied only forwards.
				_tree_vehicle_box = _vehicle_swept_box(
					pos, yaw, vel, bbox, travel_reach=0.8)
			_tree_candidates, unused_tree_isolated_hits = (
				_tree_candidates_for_sweeps_1513(
					cid, registry, (_tree_vehicle_box,),
					AreaDestructibles.DESTR_TYPE_TREE, 0.0))
			_tree_candidate_keys = set(_tree_candidates)
			for (_ti, _tx, _ty, _tz, _ttyp, _tfn, _thp, _tmass,
					_world_boxes, _contact_radius) in _nearby_destructibles(
						registry, pos, vehicle_box):
				_found_nearby = True
				if _destructible_isolated_1513(cid, _ti):
					continue
				dx = _tx - pos.x; dz = _tz - pos.z
				_origin_radius = (
					_DESTRUCTIBLE_ORIGIN_RADIUS + _contact_radius)
				if dx * dx + dz * dz > _origin_radius * _origin_radius:
					continue
				_mat_kind = None
				if (_world_boxes and _ttyp in (
						AreaDestructibles.DESTR_TYPE_FRAGILE,
						structure_type,
						AreaDestructibles.DESTR_TYPE_FALLING_ATOM)):
					# Fragile/structure catalog boxes register exact native
					# identities for the anchored material probe.  They are not a
					# collision event and must never destroy or permit movement on
					# proximity alone.  Stock WGVehiclePhysics (when available) or
					# the native solid ray below remains the contact authority.
					continue
				elif _ttyp == AreaDestructibles.DESTR_TYPE_TREE:
					if abs(vel) < 1.0:
						continue
					if (cid, _ti, None) not in _tree_candidate_keys:
						continue
				else:
					if abs(vel) < 1.0:
						continue
					fwd = dx * sin_y + dz * cos_y
					lat = dx * cos_y - dz * sin_y
					reach_f = hl_f + 0.8 + min(abs(vel) * 0.25, 1.2)
					if vel < 0:
						in_reach = -(hl_b + 0.8) <= fwd <= hl_f
					else:
						in_reach = -hl_b <= fwd <= reach_f
					if abs(lat) > hw + 0.5 or not in_reach:
						continue
				_key = ((cid, _ti, _mat_kind) if _mat_kind is not None
					else (cid, _ti))
				if _key in _st['felled']:
					if (_ttyp == AreaDestructibles.DESTR_TYPE_TREE and
							_key in _st['publish_pending']):
						_object_pos = Math.Vector3(_tx, _ty, _tz)
						if not _publish_tree_once_1513(
								_st, spaceID, (cid, _ti, None), _object_pos,
								yaw if vel >= 0 else yaw + math.pi, vel):
							raise RuntimeError(
								'tree proximity event was not admitted')
					continue
				fall_yaw = yaw if vel >= 0 else (yaw + math.pi)
				_auth = _get_destr_authority()
				if _auth.is_destroyed(cid, _ti, _mat_kind):
					_st['felled'].add(_key)
					continue
				_object_pos = Math.Vector3(_tx, _ty, _tz)
				if _destructible_isolated_1513(cid, _ti):
					continue
				if _ttyp == AreaDestructibles.DESTR_TYPE_FRAGILE:
					_ok = _auth.destroy_fragile(
						spaceID, cid, _ti, _object_pos, False)
				elif _ttyp == structure_type:
					_ok = _auth.destroy_module(
						spaceID, cid, _ti, _mat_kind, _object_pos, False)
				elif _ttyp == AreaDestructibles.DESTR_TYPE_TREE:
					if not validate_tree_identity_1513(
							spaceID, cid, _ti):
						continue
					_ok = _auth.destroy_tree(
						spaceID, cid, _ti, fall_yaw, vel, _object_pos)
				else:
					_ok = _auth.destroy_column(
						spaceID, cid, _ti, fall_yaw, vel, _object_pos)
				if not _ok:
					if (_ttyp == AreaDestructibles.DESTR_TYPE_TREE and
							not validate_tree_identity_1513(
								spaceID, cid, _ti)):
						continue
					raise RuntimeError(
						'native proximity destroy was not accepted: '
						'chunk=%s item=%s' % (cid, _ti))
				_invalidate_chunk_native_names_1513(cid)
				_st['felled'].add(_key)
				if _ttyp == AreaDestructibles.DESTR_TYPE_TREE:
					if not _publish_tree_once_1513(
							_st, spaceID, (cid, _ti, None), _object_pos,
							fall_yaw, vel):
						raise RuntimeError(
							'tree proximity event was not admitted')
				else:
					_publish_destroyed(
						('fragile'
						 if _ttyp == AreaDestructibles.DESTR_TYPE_FRAGILE
						 else 'module' if _ttyp == structure_type
						 else 'column'),
						cid, _ti, _object_pos, fall_yaw, vel,
						_mat_kind)
				LOG_DEBUG('DestrTree: FELLED', cid, _ti, 'type', _ttyp,
					'hp', _thp, 'mass', _tmass, _tfn)
		if registration_only:
			_ready_chunks = tuple(sorted(cid for cid in cids
				if cid in _st['chunks'] and
				not _destructible_isolated_1513(cid)))
			_pending_chunks = tuple(sorted(cid for cid in cids
				if cid not in _st['chunks'] and
				not _destructible_isolated_1513(cid)))
			_isolated_chunks = tuple(sorted(cid for cid in cids
				if _destructible_isolated_1513(cid)))
			return {
				'status': ('invalid' if _isolated_chunks else
					'pending' if _pending_chunks or not cids else 'ready'),
				'ready_chunks': _ready_chunks,
				'pending_chunks': _pending_chunks,
				'isolated_chunks': _isolated_chunks,
			}
		if (_receipt_key is not None and not _found_nearby and
				not globals().get('g_offh_destr_falling_active') and
				all(cid in _st['chunks'] and
					not _destructible_isolated_1513(cid) for cid in cids)):
			_chunk_ids = tuple(sorted(cids))
			_stream_signature = _loaded_chunk_signature_1513(
				mgr, _chunk_ids)
			if _stream_signature is not None:
				_receipt_cache_put_1513(
					'g_offh_destr_empty_proximity_receipts', _receipt_key,
					{'revision': _spatial_revision_1513(),
					 'chunks': _chunk_ids,
					 'stream_signature': _stream_signature},
					_EMPTY_PROXIMITY_RECEIPT_LIMIT)
	except Exception:
		raise


def _solid_destructible_candidate_1513(mat_info, contact_pt,
		contact_normal):
	"""Accept only a material hit proved to belong to this solid contact."""
	decoded = _decode_mat_info_1513(mat_info)
	if decoded is None:
		return False
	hit_pt, surf_normal, chunkID, itemIndex, matKind, fname = decoded
	if _destructible_isolated_1513(chunkID, itemIndex):
		return False
	if matKind < 71 or matKind > 130:
		return False

	import AreaDestructibles
	desc = _runtime_material_descriptor_1513(
		AreaDestructibles, fname, chunkID, itemIndex)
	if not desc:
		return False
	typ = desc.get('type')
	if typ == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		modules = desc.get('modules')
		if not isinstance(modules, dict):
			if _normalized_filename(fname):
				_isolate_destructible_1513(
					'material_descriptor', chunkID, itemIndex,
					detail='structure modules payload is unavailable')
			return False
		if modules.get(matKind) is None:
			return False
	elif typ not in (AreaDestructibles.DESTR_TYPE_FRAGILE,
			getattr(AreaDestructibles, 'DESTR_TYPE_FALLING_ATOM', None)):
		return False

	if _destructible_catalog is not None:
		expected_kind = _catalog_kind_for_type_1513(
			AreaDestructibles, typ)
		world_boxes = _catalog_instance_boxes(
			chunkID, itemIndex, fname, expected_kind, matKind)
		if not world_boxes:
			return False
		for world_box in world_boxes:
			if (_point_in_world_box(contact_pt, world_box) and
					_point_in_world_box(hit_pt, world_box)):
				return True
		return False

	# Preserve the direct-helper contract for callers without a prebaked map.
	# Installed catalogs instead fail closed on every unknown/mismatched item.
	if (hit_pt - contact_pt).length > _SOLID_CONTACT_RADIUS_1513:
		return False
	try:
		_hit_normal = type(surf_normal)(
			surf_normal.x, surf_normal.y, surf_normal.z)
		if _hit_normal.length <= 0.001:
			return False
		_hit_normal.normalise()
	except (AttributeError, TypeError, ValueError):
		return False
	_dot = (_hit_normal.x * contact_normal.x +
		_hit_normal.y * contact_normal.y +
		_hit_normal.z * contact_normal.z)
	return abs(_dot) >= _SOLID_CONTACT_NORMAL_DOT_1513


def donation_rows_1513():
	"""Project the baked native identities and scaled healths to JSON.

	The server authority reproduces the retail crush and shot-through laws
	only from these values, so every health is scaled here with the native
	DestructiblesCache law rather than re-derived elsewhere.  The bundle
	covers the complete baked catalog without waiting for streamed chunks,
	and any inconsistent entry fails the whole donation.
	"""
	catalog = _destructible_catalog
	if catalog is None or not catalog.get('has_instance_index'):
		return None
	if (globals().get('g_offh_destr_isolated_chunks') or
			globals().get('g_offh_destr_isolated_slots')):
		# One quarantined wire means this client can no longer claim that the
		# donated native map is complete or safe for canonical replay.
		return None
	import AreaDestructibles
	import DestructiblesCache
	tree_type = getattr(AreaDestructibles, 'DESTR_TYPE_TREE', None)
	falling_type = getattr(AreaDestructibles, 'DESTR_TYPE_FALLING_ATOM', None)
	fragile_type = getattr(AreaDestructibles, 'DESTR_TYPE_FRAGILE', None)
	resources = {}
	rows = []
	seen_wires = set()
	for signature in sorted(catalog['instances']):
		instance = catalog['instances'][signature]
		wire = instance.get('wire')
		scale = instance.get('exact_scale')
		normalized = instance['filename']
		record = catalog['resources'].get(normalized)
		if wire is None or scale is None or record is None:
			raise RuntimeError(
				'baked destructible instance is incomplete: %r' % (signature,))
		if wire in seen_wires:
			raise RuntimeError(
				'baked destructible wire is duplicated: %r' % (wire,))
		seen_wires.add(wire)
		fname = record['filename']
		desc = AreaDestructibles.g_cache.getDescByFilename(fname)
		if desc is None:
			raise RuntimeError(
				'baked destructible has no #1513 descriptor: %s' % fname)
		desc_type = desc.get('type')
		if desc_type == falling_type:
			destr_type = 'column'
		elif desc_type == fragile_type:
			destr_type = 'fragile'
		elif desc_type == tree_type:
			destr_type = 'tree'
		else:
			destr_type = 'structure'
		expected_kind = _catalog_kind_for_type_1513(
			AreaDestructibles, desc_type)
		if expected_kind != instance['kind']:
			raise RuntimeError(
				'baked destructible kind disagrees with the #1513 '
				'descriptor: %s' % fname)
		scaled_health = None
		modules = None
		if instance['kind'] == 'structure':
			modules = {}
			for mat_kind, module in (desc.get('modules') or {}).items():
				try:
					health = module['health']
				except (KeyError, TypeError):
					raise RuntimeError(
						'#1513 structure module is invalid: %s' % fname)
				modules[str(int(mat_kind))] = [
					float(DestructiblesCache.scaledDestructibleHealth(
						scale, health)),
					float(module.get('armor', 0.0) or 0.0)]
			if not modules:
				raise RuntimeError(
					'#1513 structure descriptor has no modules: %s' % fname)
		else:
			scaled_health = float(DestructiblesCache.scaledDestructibleHealth(
				scale, desc['health']))
		if normalized not in resources:
			resources[normalized] = {
				'destr_type': destr_type,
				'kinetic_correction': float(
					desc.get('kineticDamageCorrection', 0.0) or 0.0),
			}
		rows.append([list(signature), int(wire[0]), int(wire[1]),
			scaled_health, modules])
	if not rows:
		return None
	return {
		'unit_vehicle_mass': float(
			AreaDestructibles.g_cache.unitVehicleMass),
		'resources': resources,
		'instances': rows,
	}


def _registered_item_scale_1513(chunkID, itemIndex, filename):
	if _destructible_isolated_1513(chunkID, itemIndex):
		return None
	instance = globals().get('g_offh_destr_instances', {}).get(
		(chunkID, itemIndex))
	if (instance is None or instance['filename'] !=
			_normalized_filename(filename)):
		return None
	# Runtime-registered exact catalog items retain descriptor case.  Legacy
	# direct-helper fixtures without that field still have no case claim to
	# validate and preserve their kinetic contract.
	if (instance.get('descriptor_filename') is not None and
			_instance_descriptor_filename_1513(instance) != filename):
		return None
	return instance['item_scale']


def _registered_shot_exit_1513(chunkID, itemIndex, matKind, filename,
		start_pos, end_pos, contact_pt):
	"""Return one exact registered item/module OBB exit on a native hit."""
	if _destructible_isolated_1513(chunkID, itemIndex):
		return None
	instance = globals().get('g_offh_destr_instances', {}).get(
		(int(chunkID), int(itemIndex)))
	if (instance is None or instance['filename'] !=
			_normalized_filename(filename) or
			_instance_descriptor_filename_1513(instance) != filename):
		return None
	if instance['kind'] == 'structure':
		boxes = tuple(box for box in instance['boxes'] if box[2] == matKind)
	else:
		boxes = instance['boxes']
	segment_length = (end_pos - start_pos).length
	if segment_length <= 1.0e-9:
		return None
	contact_distance = (contact_pt - start_pos).length
	exits = []
	for world_box in boxes:
		interval = _segment_world_box_interval(
			start_pos, end_pos, world_box, 0.0)
		if interval is None:
			continue
		entry_distance = interval[0] * segment_length
		exit_distance = interval[1] * segment_length
		if (entry_distance <= contact_distance + _CATALOG_POINT_EPSILON and
				exit_distance + _CATALOG_POINT_EPSILON >= contact_distance):
			exits.append(exit_distance)
	# More than one matching interval is not an exact module/instance answer;
	# do not jump over unknown static geometry between disjoint boxes.
	return exits[0] if len(exits) == 1 else None


def _stock_crushable_1513(mat_info, vel, td, item_scale=None):
	"""Apply the exact retail Vehicle kinetic law to one proved contact."""
	decoded = _decode_mat_info_1513(mat_info)
	if decoded is None or td is None:
		return False
	unused_hit, unused_normal, chunkID, itemIndex, matKind, fname = decoded
	import AreaDestructibles
	import DestructiblesCache
	desc = _runtime_material_descriptor_1513(
		AreaDestructibles, fname, chunkID, itemIndex)
	if desc is None:
		return False
	if item_scale is None:
		item_scale = _registered_item_scale_1513(
			chunkID, itemIndex, fname)
	if item_scale is None:
		# A native material result identifies the item, but without its streamed
		# matrix we cannot reproduce scaledDestructibleHealth safely.
		return False
	try:
		item_scale = float(item_scale)
		mass = float(_descriptor_value(
			_descriptor_value(td, 'physics'), 'weight'))
	except (AttributeError, KeyError, TypeError, ValueError):
		raise RuntimeError(
			'#1513 vehicle destructible kinetic inputs are unavailable')
	if item_scale <= 0.0 or mass <= 0.0:
		raise RuntimeError('#1513 destructible kinetic inputs are invalid')
	instant_damage = 0.5 * mass * vel * vel * 0.00015
	desc_type = desc.get('type')
	if desc_type == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		modules = desc.get('modules')
		if not isinstance(modules, dict):
			if _normalized_filename(fname):
				_isolate_destructible_1513(
					'material_descriptor', chunkID, itemIndex,
					detail='structure modules payload is unavailable')
			return False
		module = modules.get(matKind)
		if module is None:
			return False
		try:
			ref_health = module['health']
		except (KeyError, TypeError):
			if _normalized_filename(fname):
				_isolate_destructible_1513(
					'material_descriptor', chunkID, itemIndex,
					detail='structure module health is unavailable')
			return False
	elif desc_type in (AreaDestructibles.DESTR_TYPE_FRAGILE,
			getattr(AreaDestructibles, 'DESTR_TYPE_FALLING_ATOM', None)):
		try:
			instant_damage *= pow(
				mass / float(AreaDestructibles.g_cache.unitVehicleMass),
				float(desc['kineticDamageCorrection']))
			ref_health = desc['health']
		except (AttributeError, KeyError, TypeError, ValueError,
				ZeroDivisionError) as error:
			if _normalized_filename(fname):
				_isolate_destructible_1513(
					'material_descriptor', chunkID, itemIndex, detail=error)
			return False
	else:
		return False
	try:
		return (DestructiblesCache.scaledDestructibleHealth(
			item_scale, ref_health) < instant_damage)
	except Exception as error:
		if _normalized_filename(fname):
			_isolate_destructible_1513(
				'material_descriptor', chunkID, itemIndex, detail=error)
		return False


def _try_destroy_solid_hit(spaceID, segment_start, hit_pt, surf_normal,
		yaw, vel, td=None):
	# wg_collideSegment does not return the descriptor filename needed by the
	# copied contact law. Prefer #1513's stock point/normal probe, then retain
	# the mature incoming-ray probe for compiled skins that only resolve in that
	# direction. Both paths share a strict same-contact descriptor gate before
	# native destruction is attempted.
	import BigWorld
	try:
		_normal = type(surf_normal)(
			surf_normal.x, surf_normal.y, surf_normal.z)
		if _normal.length <= 0.001:
			raise RuntimeError(
				'#1513 static collision surface normal is invalid')
		_normal.normalise()
		_probes = ((hit_pt - _normal.scale(3.0),
			hit_pt + _normal.scale(2.0)),)
		_incoming = hit_pt - segment_start
		if _incoming.length > 0.001:
			_incoming.normalise()
			_probes += ((hit_pt + _incoming.scale(3.0),
				hit_pt - _incoming.scale(2.0)),)
		for _seg_a, _seg_b in _probes:
			_mi = BigWorld.wg_getMatInfoNearPoint(
				spaceID, _seg_a, _seg_b, hit_pt, lambda *a: False)
			_decoded = _decode_mat_info_1513(_mi)
			if not _solid_destructible_candidate_1513(
					_mi, hit_pt, _normal):
				if _decoded is None:
					_diagnostic_contact_1513(
						'static_mat_miss', point=hit_pt,
						fields=(('speed', '%.3f' % float(vel)),))
				else:
					_diagnostic_contact_1513(
						'static_descriptor_reject',
						_decoded[2], _decoded[3],
						fields=(('mat', _decoded[4]),
							('name', _normalized_filename(_decoded[5]) or '-'),
							('speed', '%.3f' % float(vel))))
				continue
			if not _stock_crushable_1513(_mi, vel, td):
				_diagnostic_contact_1513(
					'static_kinetic_reject', _decoded[2], _decoded[3],
					fields=(('mat', _decoded[4]),
						('speed', '%.3f' % float(vel))))
				continue
			if _try_destroy_destructible(spaceID, _mi, yaw, vel):
				globals()['g_offh_destr_diag_last_static'] = (
					_decoded[2], _decoded[3],
					(('path', 'material'), ('mat', _decoded[4]),
						('speed', '%.3f' % float(vel))))
				_diagnostic_contact_1513(
					'static_native_accept', _decoded[2], _decoded[3],
					fields=(('path', 'material'), ('mat', _decoded[4]),
						('speed', '%.3f' % float(vel))))
				return True
		# Some compiled #1513 skins participate in wg_collideSegment but do not
		# resolve through wg_getMatInfoNearPoint.  Fall back only when the real
		# contact point lies in exactly one registered catalog item/module.  This
		# path uses the exact stock kinetic gate before publishing the identity.
		_candidate = _catalog_candidate_at_contact(hit_pt)
		if _candidate is None:
			_diagnostic_contact_1513(
				'static_catalog_miss', point=hit_pt,
				fields=(('speed', '%.3f' % float(vel)),))
		elif td is not None:
			_chunk, _item, _mat, _fname, _kind, _item_scale = _candidate
			_synthetic = (True, hit_pt, _normal,
				_mat if _mat is not None else 73, _fname,
				_item, _chunk)
			if not _stock_crushable_1513(
					_synthetic, vel, td, _item_scale):
				_diagnostic_contact_1513(
					'static_fallback_kinetic_reject', _chunk, _item,
					fields=(('kind', _kind), ('mat', _mat),
						('speed', '%.3f' % float(vel)),
						('scale', '%.5f' % float(_item_scale))))
			elif _try_destroy_destructible(
					spaceID, _synthetic, yaw, vel):
				globals()['g_offh_destr_diag_last_static'] = (
					_chunk, _item,
					(('path', 'catalog'), ('mat', _mat),
						('speed', '%.3f' % float(vel))))
				_diagnostic_contact_1513(
					'static_native_accept', _chunk, _item,
					fields=(('path', 'catalog'), ('kind', _kind),
						('mat', _mat), ('speed', '%.3f' % float(vel))))
				return True
	except Exception:
		raise
	return False


def _shot_kind_1513(shot):
	shell = _descriptor_value(shot, 'shell', {})
	kind = _descriptor_value(shell, 'kind')
	return str(kind) if kind in (
		'ARMOR_PIERCING', 'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR',
		'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE') else None


def _shot_through_health_1513(desc, mat_kind):
	import AreaDestructibles
	if desc['type'] == AreaDestructibles.DESTR_TYPE_STRUCTURE:
		module = desc.get('modules', {}).get(mat_kind)
		return None if module is None else float(module.get('health', 0.0))
	return float(desc.get('health', 0.0))


def _scaled_shot_through_health_1513(desc, mat_kind, item_scale):
	if desc is None:
		return None
	try:
		health = _shot_through_health_1513(desc, mat_kind)
		if health is None or item_scale is None:
			return None
		import DestructiblesCache
		return float(DestructiblesCache.scaledDestructibleHealth(
			float(item_scale), health))
	except Exception:
		return None


def _typed_shot_result_1513(world_distance, stop_distance=None,
		piercing_loss=0.0, continue_from=None, loss_distance=None,
		stopped_by_destructible=False):
	return {
		'world_distance': float(world_distance),
		'stop_distance': (None if stop_distance is None
			else float(stop_distance)),
		'piercing_loss': float(piercing_loss),
		'continue_from': (None if continue_from is None
			else float(continue_from)),
		'loss_distance': (None if loss_distance is None
			else float(loss_distance)),
		'stopped_by_destructible': bool(stopped_by_destructible),
	}


def _validated_tree_shot_identity_1513(spaceID, decoded):
	"""Return one exact SpeedTree identity that cannot obstruct a shell."""
	if decoded is None:
		return None
	unused_hit, unused_normal, chunk_id, item_index, mat_kind, filename = decoded
	if (_destructible_isolated_1513(chunk_id, item_index) or
			mat_kind < _DESTRUCTIBLE_MAT_KIND_MIN_1513 or
			mat_kind > _DESTRUCTIBLE_MAT_KIND_MAX_1513):
		return None
	import AreaDestructibles
	desc = _runtime_material_descriptor_1513(
		AreaDestructibles, filename, chunk_id, item_index)
	if desc is None or desc.get('type') != AreaDestructibles.DESTR_TYPE_TREE:
		return None
	health = desc.get('health', 0)
	try:
		valid_health = 10 <= health <= 1000
	except TypeError:
		valid_health = False
	if not valid_health:
		if _normalized_filename(filename) and not isinstance(
				health, _INTEGER_TYPES + (float,)):
			_isolate_destructible_1513(
				'material_descriptor', chunk_id, item_index,
				detail='tree health=%r' % (health,))
		return None
	if not validate_tree_identity_1513(spaceID, chunk_id, item_index):
		return None
	return int(chunk_id), int(item_index)


def _transparent_tree_shot_filter_1513(ignored_trees):
	"""Keep every native surface except an exact validated SpeedTree."""
	def keep_surface(*hit):
		# #1513 passes (matKind, collFlags, itemIndex, chunkID).  Malformed or
		# ordinary surfaces stay authoritative; only exact tree wires are skipped.
		try:
			identity = int(hit[3]), int(hit[2])
		except (IndexError, TypeError, ValueError, OverflowError):
			return True
		return identity not in ignored_trees
	return keep_surface


def shot_world_distance(bigworld, spaceID, start_pos, end_pos, dir_vec,
		shot=None):
	"""Resolve the first native or exact-catalog destructible on a shell ray.

	Passing a shot opts into typed traversal metadata.  Omitting it preserves the
	legacy float contract for diagnostics and old fixtures.
	"""
	import math
	ignored_trees = set()
	tree_filter = _transparent_tree_shot_filter_1513(ignored_trees)
	tree_hits = 0
	world_dist = 99999.0
	world_collision = bigworld.wg_collideSegment(
		spaceID, start_pos, end_pos, 128)
	shot_yaw = math.atan2(dir_vec.x, dir_vec.z)
	decoded = None
	catalog_hit = None
	while world_collision is not None:
		world_dist = (world_collision[0] - start_pos).length
		mat_info = bigworld.wg_getMatInfoNearPoint(
			spaceID, start_pos,
			world_collision[0] + dir_vec.scale(0.3),
			world_collision[0], lambda *unused: False)
		decoded = _decode_mat_info_1513(mat_info)
		tree_identity = _validated_tree_shot_identity_1513(spaceID, decoded)
		if tree_identity is not None:
			# A catalog-only prop may be closer than this native tree.  Resolve
			# it before publishing tree destruction so unreachable trees stay put.
			catalog_hit = _catalog_shot_intersection(
				spaceID, start_pos, end_pos, world_dist)
			if catalog_hit is not None:
				break
		destruction_accepted = _try_destroy_destructible(
			spaceID, mat_info, shot_yaw, 12.0, True)
		if destruction_accepted:
			if decoded is not None:
				_diagnostic_contact_1513(
					'shot_material_accept', decoded[2], decoded[3],
					fields=(('mat', decoded[4]),))
		if tree_identity is not None and (
				destruction_accepted or _get_destr_authority().is_destroyed(
					tree_identity[0], tree_identity[1], decoded[4])):
			if tree_identity in ignored_trees:
				raise RuntimeError(
					'#1513 transparent tree shot filter did not advance')
			ignored_trees.add(tree_identity)
			tree_hits += 1
			if tree_hits > 64:
				raise RuntimeError(
					'#1513 transparent tree shot traversal exceeded 64 hits')
			world_collision = bigworld.wg_collideSegment(
				spaceID, start_pos, end_pos, 128, tree_filter)
			continue
		if destruction_accepted:
			if shot is not None:
				area_destructibles = __import__('AreaDestructibles')
				desc = _runtime_material_descriptor_1513(
					area_destructibles, decoded[5], decoded[2], decoded[3])
				item_scale = _registered_item_scale_1513(
					decoded[2], decoded[3], decoded[5])
				health = _scaled_shot_through_health_1513(
					desc, decoded[4], item_scale)
				can_continue = (_shot_kind_1513(shot) in _SHOT_AP_KINDS_1513 and
					health is not None and health <= _SHOT_THROUGH_MAX_HP_1513)
				if can_continue:
					registered_exit = _registered_shot_exit_1513(
						decoded[2], decoded[3], decoded[4], decoded[5],
						start_pos, end_pos, decoded[0])
					if registered_exit is None:
						if _destructible_catalog is not None:
							return _typed_shot_result_1513(
								world_dist, stop_distance=world_dist,
								stopped_by_destructible=True)
						continue_from = world_dist + 0.6
					else:
						continue_from = (registered_exit +
							_SHOT_RAY_EPSILON)
					return _typed_shot_result_1513(
						99999.0, piercing_loss=_SHOT_THROUGH_MIN_REDUCTION_1513,
						continue_from=continue_from, loss_distance=world_dist)
				return _typed_shot_result_1513(
					world_dist, stop_distance=world_dist,
					stopped_by_destructible=True)
			# Destructible broken by the shell: re-cast past the debris.
			second = bigworld.wg_collideSegment(
				spaceID, world_collision[0] + dir_vec.scale(0.6),
				end_pos, 128)
			return ((second[0] - start_pos).length
				if second is not None else 99999.0)
		break
	if world_collision is None:
		world_dist = 99999.0
		decoded = None

	# Dynamic destructible BSPs frequently do not participate in mask 128, and
	# anonymous #1513 slots also make the point-material query return no usable
	# filename.  Resolve only the nearest unique streamed catalog OBB.  A real
	# static hit caps the ray, so an object behind an unrelated wall is never
	# destroyed by this fallback.
	if catalog_hit is None and world_collision is not None:
		point_candidate = None
		if decoded is not None:
			point_candidate = _catalog_candidate_for_native_identity_1513(
				decoded[2], decoded[3], decoded[4], decoded[0])
		if point_candidate is None:
			point_candidate = _catalog_candidate_at_contact(world_collision[0])
		if (point_candidate is not None and
				_get_destr_authority().is_destroyed(
					point_candidate[0], point_candidate[1],
					point_candidate[2])):
			# The next static surface may sit within the small point-identity
			# tolerance of the OBB that this shell just destroyed.  Never map
			# that backing surface back onto an already-removed module.
			point_candidate = None
		if point_candidate is not None:
			point = world_collision[0]
			exit_distance = _registered_shot_exit_1513(
				point_candidate[0], point_candidate[1], point_candidate[2],
				point_candidate[3], start_pos, end_pos, point)
			catalog_hit = {
				'candidate': point_candidate + ((
					float(point.x), float(point.y), float(point.z)),),
				'distance': world_dist,
				'exit_distance': (world_dist if exit_distance is None
					else exit_distance),
				'exit_proved': exit_distance is not None,
				'ambiguous': False,
			}
	if catalog_hit is None:
		catalog_hit = _catalog_shot_intersection(
			spaceID, start_pos, end_pos,
			world_dist if world_collision is not None else None)
	if catalog_hit is None:
		if world_collision is not None:
			_diagnostic_contact_1513(
				'shot_catalog_miss', point=world_collision[0])
		return (_typed_shot_result_1513(
			world_dist, stop_distance=(world_dist
				if world_collision is not None else None))
			if shot is not None else world_dist)
	if catalog_hit['ambiguous']:
		_diagnostic_contact_1513(
			'shot_catalog_ambiguous', point=(
				world_collision[0] if world_collision is not None else start_pos))
		ambiguous_distance = float(catalog_hit['distance'])
		return (_typed_shot_result_1513(
			ambiguous_distance, stop_distance=ambiguous_distance,
			stopped_by_destructible=True)
			if shot is not None else ambiguous_distance)

	candidate = catalog_hit['candidate']
	chunk_id, item_index, mat_kind, unused_filename, kind = candidate[:5]
	center = candidate[6]
	point = type(start_pos)(center[0], center[1], center[2])
	normal = type(start_pos)(0.0, 1.0, 0.0)
	mat_info = (True, point, normal,
		mat_kind if mat_kind is not None else 73,
		candidate[3], item_index, chunk_id)
	if not _try_destroy_destructible(
			spaceID, mat_info, shot_yaw, 12.0, True):
		_diagnostic_contact_1513(
			'shot_native_reject', chunk_id, item_index,
			fields=(('kind', kind), ('mat', mat_kind)))
		return (_typed_shot_result_1513(
			world_dist, stop_distance=(world_dist
				if world_collision is not None else None))
			if shot is not None else world_dist)
	_diagnostic_contact_1513(
		'shot_catalog_accept', chunk_id, item_index,
		fields=(('kind', kind), ('mat', mat_kind)))
	if shot is not None:
		import AreaDestructibles
		desc = _runtime_material_descriptor_1513(
			AreaDestructibles, candidate[3], chunk_id, item_index)
		health = _scaled_shot_through_health_1513(
			desc, mat_kind, candidate[5])
		can_continue = (_shot_kind_1513(shot) in _SHOT_AP_KINDS_1513 and
			health is not None and health <= _SHOT_THROUGH_MAX_HP_1513)
		if can_continue and catalog_hit.get('exit_proved', True):
			return _typed_shot_result_1513(
				99999.0, piercing_loss=_SHOT_THROUGH_MIN_REDUCTION_1513,
				continue_from=(catalog_hit['exit_distance'] +
					_SHOT_RAY_EPSILON),
				loss_distance=catalog_hit['distance'])
		return _typed_shot_result_1513(
			catalog_hit['distance'], stop_distance=catalog_hit['distance'],
			stopped_by_destructible=True)
	# Re-cast beyond the proved OBB just like the legacy material path.  This
	# lets a shell continue after a dynamic-only prop while a surviving static
	# backing remains authoritative for structures during native replacement.
	if not catalog_hit.get('exit_proved', True):
		return catalog_hit['distance']
	recast_distance = catalog_hit['exit_distance'] + _SHOT_RAY_EPSILON
	recast_start = start_pos + dir_vec.scale(recast_distance)
	second = bigworld.wg_collideSegment(
		spaceID, recast_start, end_pos, 128)
	return ((second[0] - start_pos).length
		if second is not None else 99999.0)


def registry_counts():
	"""Return the streamed registry sizes for the memory diagnostic."""
	counts = {}
	for name in ('g_offh_destr_seen', 'g_offh_destr_instances',
			'g_offh_destr_contact_bins', 'g_offh_destr_pending',
			'g_offh_destr_falling_active', 'g_offh_destr_chunks'):
		value = globals().get(name)
		try:
			counts[name[13:]] = len(value)
		except TypeError:
			counts[name[13:]] = 0
	stats = globals().get('g_offh_destr_receipt_stats', {})
	for name in ('contact_hits', 'contact_misses', 'contact_stores',
			'contact_evictions', 'contact_invalidated',
			'proximity_hits', 'proximity_misses', 'proximity_stores',
			'proximity_evictions', 'proximity_invalidated',
			'spatial_invalidations'):
		counts['receipt_' + name] = int(stats.get(name, 0))
	for cache_name, output_name in (
			('g_offh_destr_empty_contact_receipts',
				'receipt_contact_entries'),
			('g_offh_destr_empty_proximity_receipts',
				'receipt_proximity_entries')):
		state = globals().get(cache_name, {})
		counts[output_name] = len(state.get('entries', {})) \
			if isinstance(state, dict) else 0
	counts['spatial_revision'] = _spatial_revision_1513()
	return counts


def registry_sizes():
	"""Approximate retained bytes per streamed registry, for the memory log."""
	import sys
	sizes = {}
	for name in ('g_offh_destr_seen', 'g_offh_destr_instances',
			'g_offh_destr_contact_bins', 'g_offh_destr_pending',
			'g_offh_destr_falling_active', 'g_offh_destr_chunks'):
		value = globals().get(name)
		total = sys.getsizeof(value, 64) if value is not None else 0
		try:
			for item in value:
				total += sys.getsizeof(item, 64)
				child = value[item] if isinstance(value, dict) else None
				if child is not None:
					total += sys.getsizeof(child, 64)
		except TypeError:
			pass
		sizes[name[13:]] = total
	return sizes
