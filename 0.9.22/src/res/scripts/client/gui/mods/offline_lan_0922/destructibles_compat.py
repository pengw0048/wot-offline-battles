# -*- coding: utf-8 -*-
"""Expose the 0.8.2 AreaDestructibles surface on pinned #1513.

The authority module retains the 0.8.2 law with explicit #1513 ABI fixes.
#1513 moved the encoders and damage-type constants to
``DestructiblesCache``; this adapter restores only those moved names.  It also
replaces one unsafe #1513 tree-descriptor lookup.

``wg_getDestructibleFilename`` (``WorldOfTanks.exe`` ``0x006b2580``) is not a
safe probe: for a resolved item whose native type owns no name handler it
reaches ``PyString_FromString(NULL)`` and faults natively.  The chunk list
``wg_getChunkDestrFilenames`` (``0x006b1a10``) guards both nulls and is the
safe boundary, but it may be compacted: it appends nothing for a skipped item,
while a handled item may append either a non-empty name or ``''``.  Positions
are native item indices only when the list length equals the exact item count.
The sensor rebuilds the exact ``item_index -> filename`` mapping from that list
and the null-safe effect-category call; this adapter consumes that mapping.
"""


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

    The name is this exact native item's own resource, recovered from the
    possibly compacted chunk list through the sensor's alignment.  That is what
    the only two stock callers of ``getDestructibleDesc`` need: tree
    fracture/touchdown effects and the tree animator.  Non-tree identities keep
    their existing native paths and are deliberately not inferred here.
    ``pending`` covers the legal stream boundary and a bounded alignment still
    advancing across render ticks.  An item with no exact name, a terminally
    unaligned chunk and an unresolved descriptor are all definitively
    ``invalid`` rather than another item's resource.
    """
    from gui.mods.offline_lan_0922 import destructibles_sensor

    space_id = int(space_id)
    chunk_id = int(chunk_id)
    item_index = int(item_index)
    if _SAFE_DESC_SPACE[0] != space_id:
        reset_safe_descriptor_cache(space_id)
    key = (chunk_id, item_index)
    if destructibles_sensor.is_isolated_1513(chunk_id, item_index):
        _SAFE_DESC_BY_WIRE.pop(key, None)
        return 'invalid', None
    cached = _SAFE_DESC_BY_WIRE.get(key)
    if cached is not None:
        return 'resolved', cached
    status, filename = destructibles_sensor.resolve_native_item_name_1513(
        space_id, chunk_id, item_index)
    if status == 'pending':
        return 'pending', None
    if status != 'exact':
        return 'invalid', None
    if not isinstance(filename, _STRING_TYPES) or not filename:
        return 'invalid', None
    try:
        desc = cache.getDescByFilename(filename)
    except Exception as error:
        _SAFE_DESC_BY_WIRE.pop(key, None)
        destructibles_sensor.isolate_destructible_1513(
            'descriptor_cache', chunk_id, item_index, detail=error)
        return 'invalid', None
    if isinstance(desc, dict) and 'type' in desc:
        _SAFE_DESC_BY_WIRE[key] = desc
        return 'resolved', desc
    destructibles_sensor.isolate_destructible_1513(
        'tree_descriptor', chunk_id, item_index,
        detail='exact native filename has invalid descriptor payload=%s' %
            type(desc).__name__)
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
