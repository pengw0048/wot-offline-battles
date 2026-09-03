# -*- coding: utf-8 -*-
"""Authoritative offline record of destroyed map objects.

Mirrors what the real BigWorld server kept in the AreaDestructibles
entity's ALL_CLIENTS properties (fallenTrees, fallenColumns,
destroyedFragiles, destroyedModules). Sensors (collision probes, the
proximity registry, later shot impacts) REPORT contacts here; this
module decides, encodes, records and pushes the update to the
per-chunk client entity like a server property push, so the game's
own set_* callbacks drive the visuals.

State lives per space: a new battle (new spaceID) resets everything,
which also makes cross-battle chunk-ID collisions harmless.
"""

import math
import sys

import BigWorld
import Math

_state = {'spaceID': None, 'chunks': {}, 'entities': {}}

_DESTRUCTIBLE_DIAGNOSTICS = False
_APPLY_REPORT_LIMIT = 24
_applies_reported = [0]

_CHUNK_REPORT_LIMIT = 24
_chunks_reported = [0]

_PROP_BY_KIND = {
	'tree': 'fallenTrees',
	'column': 'fallenColumns',
	'fragile': 'destroyedFragiles',
	'module': 'destroyedModules',
}

_PREV_BY_PROP = {
	'fallenTrees': '_AreaDestructibles__prevFallenTrees',
	'fallenColumns': '_AreaDestructibles__prevFallenColumns',
	'destroyedFragiles': '_AreaDestructibles__prevDestroyedFragiles',
	'destroyedModules': '_AreaDestructibles__prevDestroyedModules',
}


def _log(*args):
	try:
		from gui.mods.offhangar.logging import LOG_DEBUG
		LOG_DEBUG(*args)
	except Exception:
		pass


def reset(spaceID=None):
	"""Return _state to its initial shape. Callers (the battle sweep)
	MUST use this instead of _state.clear(): clear() removes the
	'spaceID'/'chunks'/'entities' keys, and every later access then
	raises KeyError, killing destructibles for the whole battle."""
	_state['spaceID'] = spaceID
	_state['chunks'] = {}
	_state['entities'] = {}
	_applies_reported[0] = 0
	_chunks_reported[0] = 0


def _report_chunk(event, spaceID, chunkID, pos, extra=''):
	"""Say what each of the first chunk-scoped orders this round asked for."""
	if not _DESTRUCTIBLE_DIAGNOSTICS:
		return
	if _chunks_reported[0] >= _CHUNK_REPORT_LIMIT:
		return
	_chunks_reported[0] += 1
	sys.stdout.write(
		'[Offline LAN 0.9.22] DESTR chunk %s chunk=%s space=%s '
		'at=(%.1f, %.1f, %.1f)%s\n' % (
			event, chunkID, spaceID, pos[0], pos[1], pos[2], extra))


def _ensure_shape():
	# Defensive: if anything wiped _state (e.g. a stray clear()), rebuild
	# the required keys so no access below can KeyError.
	if 'spaceID' not in _state:
		_state['spaceID'] = None
	if 'chunks' not in _state:
		_state['chunks'] = {}
	if not isinstance(_state.get('entities'), dict):
		_state['entities'] = {}


def _reset_if_new_space(spaceID):
	_ensure_shape()
	if _state['spaceID'] != spaceID:
		reset(spaceID)


def _chunk(chunkID):
	_ensure_shape()
	c = _state['chunks'].get(chunkID)
	if c is None:
		c = {
			'fallenTrees': [], 'fallenColumns': [],
			'destroyedFragiles': [], 'destroyedModules': [],
			'keys': set(), 'treePresentations': {},
		}
		_state['chunks'][chunkID] = c
	elif not isinstance(c.get('treePresentations'), dict):
		c['treePresentations'] = {}
	return c


def is_destroyed(chunkID, itemIndex, matKind=None):
	c = _state.get('chunks', {}).get(chunkID)
	if c is None:
		return False
	return (itemIndex, matKind) in c['keys'] or (itemIndex, None) in c['keys']


def destroyed_keys(chunkID):
	"""Return this chunk's accepted (itemIndex, matKind) keys. Read only."""
	c = _state.get('chunks', {}).get(chunkID)
	return c['keys'] if c is not None else ()


def accept_tree_without_presentation(spaceID, chunkID, itemIndex):
	"""Record one canonical tree after its single native attempt was a no-op.

	The shared event is already authoritative.  Keeping this logical receipt
	out of ``fallenTrees`` prevents a later controller creation from replaying
	the native animation after the vehicle has left the contact.
	"""
	spaceID = int(spaceID); chunkID = int(chunkID); itemIndex = int(itemIndex)
	_reset_if_new_space(spaceID)
	c = _chunk(chunkID)
	key = (itemIndex, None)
	if key in c['keys']:
		return False
	c['keys'].add(key)
	return True


def _ensure_chunk(spaceID, chunkID, pos):
	"""Start the manager space, spawn the chunk controller entity once
	(the real server did this) and register the chunk. Returns the
	controller, or None while the entity is still spawning."""
	import AreaDestructibles
	mgr = AreaDestructibles.g_destructiblesManager
	if mgr is None:
		raise RuntimeError('destructibles manager is unavailable')
	# Keep the manager's space in sync with the CURRENT battle. Starting only
	# when None left a STALE spaceID after battle 1: mgr kept the previous,
	# now-RELEASED space, so the engine's __launchFallEffect later called
	# getDestructibleDesc(self.__spaceID, ...) -> wg_getDestructibleFilename
	# with a dead/None spaceID = "argument 1 must be set to an int", aborting
	# the tree/column fall (seen on a later-battle Fjords). startSpace() does
	# clear() first, so re-starting on a space change is safe and fires once.
	_cur_sid = int(spaceID) if spaceID is not None else None
	_mgr_sid = mgr.getSpaceID()
	if _cur_sid is not None and _mgr_sid != _cur_sid:
		# startSpace() runs clear(), which drops the manager's streamed-chunk
		# map, its controllers and its saved destructible matrices.
		loaded = getattr(
			mgr, '_DestructiblesManager__loadedChunkIDs', None)
		streamed = len(loaded) if isinstance(loaded, dict) else 'unavailable'
		mgr.startSpace(_cur_sid)
		_report_chunk('startSpace', spaceID, chunkID, pos,
			' was=%s now=%s dropped_streamed_chunks=%s' % (
				_mgr_sid, mgr.getSpaceID(), streamed))
	entities = _state['entities']
	controller = mgr.getController(chunkID)
	entry = entities.get(chunkID)
	if controller is not None:
		# A controller that the stock stream already owns needs no duplicate
		# client entity.  Only advance lifecycle state for a request created here.
		if entry is not None:
			entry['state'] = 'ready'
		return controller
	if entry is not None:
		# A positive createEntity id is only a pending request.  Once the
		# PyEntity is visible, AreaDestructibles.onEnterWorld must already have
		# registered its controller.  A visible entity without that controller is
		# a failed enter lifecycle and can be destroyed safely before retrying.
		lookup = getattr(BigWorld, 'entity', None)
		entity = None
		if callable(lookup):
			try:
				entity = lookup(int(entry['entityID']))
			except (KeyError, ReferenceError, TypeError, ValueError):
				entity = None
		if entity is None:
			return None
		destroy = getattr(BigWorld, 'destroyEntity', None)
		if not callable(destroy):
			raise RuntimeError(
				'AreaDestructibles failed entity cannot be destroyed')
		destroy(int(entry['entityID']))
		entities.pop(chunkID, None)
		_report_chunk('controller-retry', spaceID, chunkID, pos,
			' failed_entity=%s' % entry['entityID'])
	if chunkID not in entities:
		c = _chunk(chunkID)
		entityID = BigWorld.createEntity('AreaDestructibles', spaceID, 0, Math.Vector3(pos[0], pos[1], pos[2]), (0.0, 0.0, 0.0), {
			'fallenTrees': list(c['fallenTrees']),
			'fallenColumns': list(c['fallenColumns']),
			'destroyedFragiles': list(c['destroyedFragiles']),
			'destroyedModules': list(c['destroyedModules']),
		})
		if entityID is None or int(entityID) <= 0:
			raise RuntimeError(
				'AreaDestructibles entity was not created for chunk %s' %
				chunkID)
		# createEntity may complete asynchronously.  Keep the returned id pending
		# until onEnterWorld registers the controller; never treat the request id
		# itself as proof that the native object is usable.
		entry = {'entityID': int(entityID), 'state': 'pending'}
		entities[chunkID] = entry
		# AreaDestructibles.onEnterWorld ignores the caller's chunk and derives
		# its own from the entity position, so report both.
		reader = getattr(AreaDestructibles, 'chunkIDFromPosition', None)
		try:
			derived = reader(Math.Vector3(pos[0], pos[1], pos[2]))
		except Exception as error:
			derived = 'unavailable:%s' % (error,)
		controller = mgr.getController(chunkID)
		if controller is not None:
			entry['state'] = 'ready'
		_report_chunk('controller', spaceID, chunkID, pos,
			' entity=%s derived_chunk=%s' % (entityID, derived))
	# ``game.wg_onChunkLoad`` owns the manager's exact native slot count. The
	# filename helper may expose only the named SpeedTree prefix, so synthesising
	# onChunkLoad from its length truncates later fragile/structure/falling slots
	# and permanently poisons the manager for this streamed chunk.  If the native
	# callback has not arrived, the manager safely queues direct orders itself.
	return controller


def _report_apply(spaceID, chunkID, pos, kind, destrData, ctrl,
		applyShotImmediately):
	"""Say what each of the first destructions this round asked the engine for."""
	if not _DESTRUCTIBLE_DIAGNOSTICS:
		return
	if _applies_reported[0] >= _APPLY_REPORT_LIMIT:
		return
	_applies_reported[0] += 1
	try:
		import AreaDestructibles
		manager_space = AreaDestructibles.g_destructiblesManager.getSpaceID()
	except Exception as error:
		manager_space = 'unavailable:%s' % (error,)
	sys.stdout.write(
		'[Offline LAN 0.9.22] DESTR apply kind=%s chunk=%s data=%s ctrl=%s '
		'shot_now=%s space=%s manager_space=%s at=(%.1f, %.1f, %.1f)\n' % (
			kind, chunkID, destrData, ctrl is not None,
			bool(applyShotImmediately), spaceID, manager_space,
			pos[0], pos[1], pos[2]))


def _apply(spaceID, chunkID, pos, kind, destrData, dedupKey,
		syncWithProjectile=False, applyShotImmediately=False):
	import AreaDestructibles
	_reset_if_new_space(spaceID)
	c = _chunk(chunkID)
	if dedupKey in c['keys']:
		return False
	ctrl = _ensure_chunk(spaceID, chunkID, pos)
	_report_apply(spaceID, chunkID, pos, kind, destrData, ctrl,
		applyShotImmediately)
	prop = _PROP_BY_KIND[kind]
	# Use one native delivery boundary for both ready and spawning controllers.
	# Calling set_* materializes and diffs the complete replicated collection on
	# every break before it reaches this same manager method.  The authority
	# ledger already supplies replay state, so deliver the single new item and
	# mirror the accepted controller collection without firing its setter.
	dmgTypes = {
		'tree': AreaDestructibles._DAMAGE_TYPE_TREE,
		'column': AreaDestructibles._DAMAGE_TYPE_COLUMN,
		'fragile': AreaDestructibles._DAMAGE_TYPE_FRAGILE,
		'module': AreaDestructibles._DAMAGE_TYPE_MODULE,
	}
	AreaDestructibles.g_destructiblesManager.orderDestructibleDestroy(
		chunkID, dmgTypes[kind], destrData, True,
		bool(syncWithProjectile))
	# Reaching this point is the native acceptance boundary.  The method either
	# applied the order or retained it for a streamed chunk; it reports failure by
	# raising.  Do not invent a second asynchronous receipt requirement here.
	c[prop].append(destrData)
	c['keys'].add(dedupKey)
	if ctrl is not None:
		try:
			values = getattr(ctrl, prop)
			if destrData not in values:
				values.append(destrData)
			setattr(ctrl, _PREV_BY_PROP[prop], frozenset(values))
		except Exception as error:
			# Native destruction cannot be rolled back.  Keep the authoritative
			# replay ledger committed and report only the optional local mirror.
			_log('DestrAuth: controller ledger sync failed', chunkID, kind, error)
	return True


def _tree_matrix_signature(spaceID, chunkID, itemIndex):
	"""Return a value signature for one native destructible transform."""
	matrix = Math.Matrix(BigWorld.wg_getDestructibleMatrix(
		spaceID, chunkID, itemIndex))
	translation = matrix.translation
	values = [translation.x, translation.y, translation.z]
	for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
			(0.0, 0.0, 1.0)):
		vector = matrix.applyVector(Math.Vector3(*axis))
		values.extend((vector.x, vector.y, vector.z))
	return tuple(float(value) for value in values)


def _tree_matrix_changed(before, after):
	return (len(before) == len(after) and
		any(abs(left - right) > 1.0e-5
			for left, right in zip(before, after)))


def _tree_wait_ordered(manager, chunkID, destrData):
	waiting = getattr(
		manager, '_DestructiblesManager__destructiblesWaitDestroy', None)
	if not isinstance(waiting, dict):
		return False
	for order in waiting.get(chunkID, ()):
		if (isinstance(order, (tuple, list)) and len(order) == 3 and
				order[0] == _tree_damage_type() and order[1] == destrData):
			return True
	return False


def _tree_damage_type():
	import AreaDestructibles
	return AreaDestructibles._DAMAGE_TYPE_TREE


def _tree_body_present(spaceID, chunkID, itemIndex, start_if_stalled=False):
	import AreaDestructibles
	animator = AreaDestructibles.g_destructiblesAnimator
	bodies = getattr(animator, '_DestructiblesAnimator__bodies')
	for body in bodies:
		if (body.get('spaceID') != spaceID or
				body.get('chunkID') != chunkID or
				body.get('destrIndex') != itemIndex):
			continue
		if start_if_stalled:
			# This belongs to the original native application, never to a later
			# network echo or frame retry.  Repair the stable lost-callback state
			# immediately while the contact is being handled.
			is_updating = getattr(
				animator, '_DestructiblesAnimator__isUpdating')
			callback_id = getattr(
				animator, '_DestructiblesAnimator__updateCallbackID')
			if bodies and is_updating and callback_id is None:
				setattr(animator, '_DestructiblesAnimator__isUpdating', False)
				getattr(animator, '_DestructiblesAnimator__startUpdate')()
		return True
	return False


def _tree_presentation_state(
		spaceID, chunkID, itemIndex, entry, start_if_stalled=False):
	"""Observe the synchronous native receipt for one accepted tree order."""
	import AreaDestructibles
	manager = AreaDestructibles.g_destructiblesManager
	if _tree_body_present(
			spaceID, chunkID, itemIndex, start_if_stalled):
		entry['status'] = 'presented'
		return 'presented'
	loaded = manager.isChunkLoaded(chunkID)
	if loaded and entry['initialMatrixSignature'] is not None:
		try:
			current_signature = _tree_matrix_signature(
				spaceID, chunkID, itemIndex)
		except Exception:
			current_signature = None
		if (current_signature is not None and _tree_matrix_changed(
				entry['initialMatrixSignature'], current_signature)):
			entry['status'] = 'presented'
			return 'presented'
	if _tree_wait_ordered(manager, chunkID, entry['data']):
		entry['status'] = 'queued'
		return 'queued'
	# Once a synchronous body/matrix receipt or exact unloaded wait order has
	# admitted the tree, losing that transient observation later does not undo
	# the accepted native operation.  Most importantly, observation never sends
	# another destroy order after the vehicle has left the contact.
	if entry.get('status') in ('presented', 'queued'):
		return entry['status']
	entry['status'] = 'pending'
	return 'pending'


def ensure_tree_presentation(spaceID, chunkID, itemIndex, fallYaw, speed, pos):
	"""Return ``presented``, ``queued`` or ``pending`` for an accepted tree.

	The fall arguments are retained in this public signature so callers can use
	the same identity tuple as ``destroy_tree``.  This is observation only: a
	canonical echo must never become a delayed local destroy-order retry.
	"""
	spaceID = int(spaceID); chunkID = int(chunkID); itemIndex = int(itemIndex)
	_reset_if_new_space(spaceID)
	c = _chunk(chunkID)
	entry = c['treePresentations'].get(itemIndex)
	if entry is None or (itemIndex, None) not in c['keys']:
		return 'pending'
	return _tree_presentation_state(
		spaceID, chunkID, itemIndex, entry)


def _destroy_tree(
		spaceID, chunkID, itemIndex, fallYaw, speed, pos,
		validate_identity):
	import AreaDestructibles
	from gui.mods.offline_lan_0922 import destructibles_compat
	# Native getDestructibleDesc (called by the game's __launchFallEffect)
	# demands a plain int destrID; a float/long index reaching it raised
	# 'argument 1 must be set to an int'. Coerce here.
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	# The pinned scalar ``wg_getDestructibleFilename`` (``0x006b2580``) is safe
	# only for an item whose native type owns a name handler: otherwise it
	# reaches ``PyString_FromString(NULL)`` at ``0x006b270c`` and faults, which
	# Python cannot contain.  For a currently loaded chunk, resolve this item's
	# own exact name through the reconstructed chunk-list compaction and cache
	# its descriptor before the stock animation path.  Unloaded chunks retain the
	# stock queued-order behavior; the installed safe resolver performs the same
	# check on load.
	mgr = AreaDestructibles.g_destructiblesManager
	if validate_identity and mgr.isChunkLoaded(chunkID):
		desc = destructibles_compat.resolve_destructible_desc(
			AreaDestructibles.g_cache, spaceID, chunkID, itemIndex)
		if (desc is None or
				desc.get('type') != AreaDestructibles.DESTR_TYPE_TREE):
			# A streamed slot without a safe named tree descriptor must remain
			# solid.  Do not enter the stock animation path: its scalar filename
			# wrapper dereferences NULL before Python can handle it.
			return False
	pitch = math.pi / 2.0
	pc = BigWorld.wg_getDestructibleFallPitchConstr(
		spaceID, chunkID, itemIndex, fallYaw)
	try:
		pitchConstr, _collisionFlags = pc
	except (TypeError, ValueError):
		raise RuntimeError(
			'#1513 destructible fall-pitch payload must contain 2 items')
	if pitchConstr is not None:
		pitch = pitchConstr
	speed = max(1, min(3, int(abs(speed))))
	data = AreaDestructibles.encodeFallenTree(itemIndex, fallYaw, pitch, speed)
	initial_signature = None
	if mgr.isChunkLoaded(chunkID):
		try:
			initial_signature = _tree_matrix_signature(
				spaceID, chunkID, itemIndex)
		except Exception:
			# Matrix observation is a receipt, not a prerequisite for the stock
			# destroy call.  The animator body remains the primary evidence.
			initial_signature = None
	entry = {
		'data': data,
		'pos': (float(pos[0]), float(pos[1]), float(pos[2])),
		'initialMatrixSignature': initial_signature,
		'status': 'pending',
	}
	accepted = _apply(
		spaceID, chunkID, pos, 'tree', data, (itemIndex, None))
	if accepted:
		# Observe/kick only as part of this original contact.  No frame or
		# canonical-echo path is allowed to start this animation later.
		try:
			_tree_presentation_state(
				spaceID, chunkID, itemIndex, entry, start_if_stalled=True)
		except Exception as error:
			# Native acceptance is already irreversible.  Optional presentation
			# observation must never swallow the trusted contact publication.
			_log('DestrAuth: tree presentation observation failed',
				chunkID, itemIndex, error)
		_chunk(chunkID)['treePresentations'][itemIndex] = entry
	return accepted


def destroy_tree(spaceID, chunkID, itemIndex, fallYaw, speed, pos):
	"""Safely destroy a tree whose exact native identity is not yet proved."""
	return _destroy_tree(
		spaceID, chunkID, itemIndex, fallYaw, speed, pos, True)


def destroy_validated_tree(spaceID, chunkID, itemIndex, fallYaw, speed, pos):
	"""Destroy an exact tree wire already proved by a trusted proposal/event."""
	return _destroy_tree(
		spaceID, chunkID, itemIndex, fallYaw, speed, pos, False)


def destroy_column(spaceID, chunkID, itemIndex, fallYaw, speed, pos):
	import AreaDestructibles
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	speed = max(1, min(3, int(abs(speed))))
	data = AreaDestructibles.encodeFallenColumn(itemIndex, fallYaw, speed)
	return _apply(spaceID, chunkID, pos, 'column', data, (itemIndex, None))


def destroy_fragile(spaceID, chunkID, itemIndex, pos, isShotDamage=False):
	import AreaDestructibles
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	data = AreaDestructibles.encodeFragile(itemIndex, isShotDamage)
	# The copied shell resolver does not receive #1513's server-authored
	# ``damagedDestructibles`` payload, so there is no later
	# ``onProjectileExploded`` transaction to release a synced native order.
	# Preserve the shot bit in the encoded destruction, but apply it now.
	return _apply(spaceID, chunkID, pos, 'fragile', data,
		(itemIndex, None), False, bool(isShotDamage))


def destroy_module(spaceID, chunkID, itemIndex, matKind, pos, isShotDamage=False):
	import AreaDestructibles
	chunkID = int(chunkID); itemIndex = int(itemIndex)
	if matKind is not None:
		matKind = int(matKind)
	data = AreaDestructibles.encodeDestructibleModule(itemIndex, matKind, isShotDamage)
	return _apply(spaceID, chunkID, pos, 'module', data,
		(itemIndex, matKind), False, bool(isShotDamage))
