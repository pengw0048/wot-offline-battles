"""Destructible authority, taken from the 0.9.22 port.

The law is unchanged. Version differences belong in the
adapters in this package, never in this file.

Contract, from the original module:
Authoritative offline record of destroyed map objects.

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
from __future__ import absolute_import
# -*- coding: utf-8 -*-

import math

import BigWorld
import Math

_state = {'spaceID': None, 'chunks': {}, 'entities': set()}

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
    _state['entities'] = set()


def _ensure_shape():
    # Defensive: if anything wiped _state (e.g. a stray clear()), rebuild
    # the required keys so no access below can KeyError.
    if 'spaceID' not in _state:
        _state['spaceID'] = None
    if 'chunks' not in _state:
        _state['chunks'] = {}
    if 'entities' not in _state:
        _state['entities'] = set()


def _reset_if_new_space(spaceID):
    _ensure_shape()
    if _state['spaceID'] != spaceID:
        reset(spaceID)


def _chunk(chunkID):
    _ensure_shape()
    c = _state['chunks'].get(chunkID)
    if c is None:
        c = {'fallenTrees': [], 'fallenColumns': [], 'destroyedFragiles': [], 'destroyedModules': [], 'keys': set()}
        _state['chunks'][chunkID] = c
    return c


def is_destroyed(chunkID, itemIndex, matKind=None):
    c = _state.get('chunks', {}).get(chunkID)
    if c is None:
        return False
    return (itemIndex, matKind) in c['keys'] or (itemIndex, None) in c['keys']


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
        mgr.startSpace(_cur_sid)
    if chunkID not in _state['entities']:
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
        # createEntity may complete asynchronously, but a failed call must remain
        # retryable instead of permanently poisoning this chunk.
        _state['entities'].add(chunkID)
    # ``game.wg_onChunkLoad`` owns the manager's exact native slot count. The
    # filename helper may expose only the named SpeedTree prefix, so synthesising
    # onChunkLoad from its length truncates later fragile/structure/falling slots
    # and permanently poisons the manager for this streamed chunk.  If the native
    # callback has not arrived, the manager safely queues direct orders itself.
    return mgr.getController(chunkID)


def _apply(spaceID, chunkID, pos, kind, destrData, dedupKey,
        syncWithProjectile=False, applyShotImmediately=False):
    import AreaDestructibles
    _reset_if_new_space(spaceID)
    c = _chunk(chunkID)
    if dedupKey in c['keys']:
        return False
    ctrl = _ensure_chunk(spaceID, chunkID, pos)
    prop = _PROP_BY_KIND[kind]
    if ctrl is not None:
        # Server-style push: update the entity property and fire its
        # set_ callback; the entity diffs vs its prev-set and animates
        # only the new entry.
        values = getattr(ctrl, prop)
        length = len(values)
        prevName = _PREV_BY_PROP[prop]
        previous = getattr(ctrl, prevName)
        values.append(destrData)
        try:
            if applyShotImmediately:
                # The 2.3.1.2 property setter decodes the shot bit and always asks
                # the native manager to synchronize with PlayerAvatar's later
                # ``damagedDestructibles`` projectile payload.  The copied shell
                # resolver has no such payload.  Preserve the encoded shot bit for
                # hit effects and replicated state, but advance the controller's
                # diff snapshot ourselves and issue the native order unsynchronized.
                setattr(ctrl, prevName, frozenset(values))
                dmgTypes = {
                    'tree': AreaDestructibles._DAMAGE_TYPE_TREE,
                    'column': AreaDestructibles._DAMAGE_TYPE_COLUMN,
                    'fragile': AreaDestructibles._DAMAGE_TYPE_FRAGILE,
                    'module': AreaDestructibles._DAMAGE_TYPE_MODULE,
                }
                AreaDestructibles.g_destructiblesManager.orderDestructibleDestroy(
                    chunkID, dmgTypes[kind], destrData, True, False)
            else:
                getattr(ctrl, 'set_' + prop)(None)
        except Exception:
            # A failed setter may not leave the replicated property claiming that
            # native geometry changed.  Roll back only our own tail append; any
            # other mutation means the controller contract is already corrupted.
            if (len(values) != length + 1 or
                    values[-1] != destrData):
                raise RuntimeError(
                    'destructible controller rollback is unsafe: '
                    'chunk=%s kind=%s' % (chunkID, kind))
            try:
                del values[-1]
                setattr(ctrl, prevName, previous)
            except Exception:
                raise RuntimeError(
                    'destructible controller rollback failed: '
                    'chunk=%s kind=%s' % (chunkID, kind))
            raise
    else:
        # Controller still spawning: order directly - the manager queues it
        # per chunk until onChunkLoad and animates on flush.  2.3.1.2's fifth
        # argument synchronises shot destruction with the projectile explosion;
        # contact damage always passes False.
        dmgTypes = {
            'tree': AreaDestructibles._DAMAGE_TYPE_TREE,
            'column': AreaDestructibles._DAMAGE_TYPE_COLUMN,
            'fragile': AreaDestructibles._DAMAGE_TYPE_FRAGILE,
            'module': AreaDestructibles._DAMAGE_TYPE_MODULE,
        }
        AreaDestructibles.g_destructiblesManager.orderDestructibleDestroy(
            chunkID, dmgTypes[kind], destrData, True,
            bool(syncWithProjectile))
    # The native operation is irreversible.  Commit the replay/dedup ledger only
    # after it has accepted the destroy; otherwise a transient ABI/engine failure
    # would make every later canonical replay look like a successful duplicate.
    c[prop].append(destrData)
    c['keys'].add(dedupKey)
    return True


def destroy_tree(spaceID, chunkID, itemIndex, fallYaw, speed, pos):
    import AreaDestructibles
    # Native getDestructibleDesc (called by the game's __launchFallEffect)
    # demands a plain int destrID; a float/long index reaching it raised
    # 'argument 1 must be set to an int'. Coerce here.
    chunkID = int(chunkID); itemIndex = int(itemIndex)
    pitch = math.pi / 2.0
    pc = BigWorld.wg_getDestructibleFallPitchConstr(
        spaceID, chunkID, itemIndex, fallYaw)
    try:
        pitchConstr, _collisionFlags = pc
    except (TypeError, ValueError):
        raise RuntimeError(
            '2.3.1.2 destructible fall-pitch payload must contain 2 items')
    if pitchConstr is not None:
        pitch = pitchConstr
    speed = max(1, min(3, int(abs(speed))))
    data = AreaDestructibles.encodeFallenTree(itemIndex, fallYaw, pitch, speed)
    return _apply(spaceID, chunkID, pos, 'tree', data, (itemIndex, None))


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
    # The copied shell resolver does not receive 2.3.1.2's server-authored
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
