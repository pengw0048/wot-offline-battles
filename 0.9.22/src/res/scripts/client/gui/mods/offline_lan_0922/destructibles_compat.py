# -*- coding: utf-8 -*-
"""Expose the 0.8.2 AreaDestructibles surface on pinned #1513.

The authority module retains the 0.8.2 law with explicit #1513 ABI fixes.
#1513 moved the encoders and damage-type constants to
``DestructiblesCache``; this adapter restores only those moved names.  It also
replaces one unsafe #1513 tree-descriptor lookup: the stock method passes the
nullable result of ``wg_getDestructibleFilename`` straight to
``PyString_FromString``.  The chunk-list helper returns ``None`` to Python
normally and is therefore the safe boundary for offline tree animation.
"""

from gui.mods.offline_lan_0922 import hidden_worker_profiler


_INSTALLED = False
_SAFE_DESC_SPACE = [None]
_SAFE_DESC_BY_WIRE = {}

try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


def reset_safe_descriptor_cache(space_id=None):
    """Forget descriptors from the previous battle space."""
    _SAFE_DESC_SPACE[0] = space_id
    _SAFE_DESC_BY_WIRE.clear()


def inspect_destructible_desc(cache, space_id, chunk_id, item_index):
    """Inspect one streamed descriptor without the nullable scalar wrapper.

    On pinned #1513 ``wg_getChunkDestrFilenames`` is a named SpeedTree prefix,
    not the native slot count.  That is exactly sufficient for the only two
    stock callers of ``getDestructibleDesc``: tree fracture/touchdown effects
    and the tree animator.  Non-tree identities keep their existing native
    paths and are deliberately not inferred here. ``pending`` is reserved for
    the legal stream boundary where the chunk list is not available yet;
    malformed, unnamed and unresolved entries are definitively ``invalid``.
    """
    import BigWorld

    space_id = int(space_id)
    chunk_id = int(chunk_id)
    item_index = int(item_index)
    if _SAFE_DESC_SPACE[0] != space_id:
        reset_safe_descriptor_cache(space_id)
    key = (chunk_id, item_index)
    cached = _SAFE_DESC_BY_WIRE.get(key)
    if cached is not None:
        return 'resolved', cached
    filenames = hidden_worker_profiler.native_call(
        'destructible_setup', 'wg_getChunkDestrFilenames',
        BigWorld.wg_getChunkDestrFilenames, space_id, chunk_id)
    if filenames is None:
        return 'pending', None
    if not isinstance(filenames, (list, tuple)):
        return 'invalid', None
    if item_index < 0 or item_index >= len(filenames):
        return 'invalid', None
    filename = filenames[item_index]
    if not isinstance(filename, _STRING_TYPES) or not filename:
        return 'invalid', None
    desc = cache.getDescByFilename(filename)
    if desc is not None:
        _SAFE_DESC_BY_WIRE[key] = desc
        return 'resolved', desc
    return 'invalid', None


def resolve_destructible_desc(cache, space_id, chunk_id, item_index):
    """Return the safe descriptor, or ``None`` at pending/invalid boundaries."""
    return inspect_destructible_desc(
        cache, space_id, chunk_id, item_index)[1]


def _safe_get_destructible_desc(self, space_id, chunk_id, item_index):
    return resolve_destructible_desc(
        self, space_id, chunk_id, item_index)


def _safe_missing_descriptor_log(area_module, space_id, chunk_id, item_index):
    """Log a failed safe lookup without re-entering the crashing wrapper."""
    logger = getattr(area_module, 'LOG_ERROR', None)
    if callable(logger):
        logger(
            'Destructible descriptor is not available, space: %s, '
            'chunk: %s, id: %s' %
            (space_id, chunk_id, item_index))


def install(area_module=None, cache_module=None):
    global _INSTALLED
    if _INSTALLED:
        # ``BattleRuntime.start`` calls install once per round.  Space IDs are
        # engine-owned and need not be treated as a cross-round cache key.
        reset_safe_descriptor_cache()
        return True

    if area_module is None:
        import AreaDestructibles as area_module
    if cache_module is None:
        import DestructiblesCache as cache_module

    AreaDestructibles = area_module
    DestructiblesCache = cache_module

    moved_functions = {
        'chunkIDFromPosition': DestructiblesCache.chunkIDFromPosition,
        'encodeFallenTree': DestructiblesCache.encodeFallenTree,
        'encodeFallenColumn': DestructiblesCache.encodeFallenColumn,
        'encodeFragile': DestructiblesCache.encodeFragile,
        'encodeDestructibleModule':
            DestructiblesCache.encodeDestructibleModule,
    }
    moved_constants = {
        'DESTR_TYPE_TREE': DestructiblesCache.DESTR_TYPE_TREE,
        'DESTR_TYPE_FALLING_ATOM':
            DestructiblesCache.DESTR_TYPE_FALLING_ATOM,
        'DESTR_TYPE_FRAGILE': DestructiblesCache.DESTR_TYPE_FRAGILE,
        'DESTR_TYPE_STRUCTURE': DestructiblesCache.DESTR_TYPE_STRUCTURE,
        '_DAMAGE_TYPE_TREE': DestructiblesCache.DESTR_TYPE_TREE,
        '_DAMAGE_TYPE_COLUMN': DestructiblesCache.DESTR_TYPE_FALLING_ATOM,
        '_DAMAGE_TYPE_FRAGILE': DestructiblesCache.DESTR_TYPE_FRAGILE,
        '_DAMAGE_TYPE_MODULE': DestructiblesCache.DESTR_TYPE_STRUCTURE,
    }
    for name, value in moved_functions.items():
        if not hasattr(AreaDestructibles, name):
            setattr(AreaDestructibles, name, value)
    for name, value in moved_constants.items():
        if not hasattr(AreaDestructibles, name):
            setattr(AreaDestructibles, name, value)

    # Exact #1513 bytecode has only two consumers of this method, both in the
    # tree path.  Replacing the class method also covers the already-created
    # global cache instance.  Its stock error logger repeats the same unsafe
    # scalar lookup, so replace that exact companion boundary as well.
    cache_type = getattr(
        AreaDestructibles, 'ClientDestructiblesCache', None)
    if cache_type is not None:
        cache_type.getDestructibleDesc = _safe_get_destructible_desc
        if hasattr(AreaDestructibles, '_printErrDescNotAvailable'):
            AreaDestructibles._printErrDescNotAvailable = (
                lambda space_id, chunk_id, item_index:
                _safe_missing_descriptor_log(
                    AreaDestructibles, space_id, chunk_id, item_index))

    reset_safe_descriptor_cache()
    _INSTALLED = True
    return True
