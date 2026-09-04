from __future__ import print_function


def _text(value):
    try:
        return unicode(value)
    except NameError:
        return str(value)


def _allowed_names(map_pool):
    if map_pool is None:
        return None
    return set(_text(name) for name in map_pool if name)


class MapCatalog(object):
    """Stock TrainingSettingsWindow map rows constrained by LAN policy."""

    def __init__(self, rows):
        self.cache = list(rows)


def _map_icon_path(arena_type):
    try:
        from gui.Scaleform.daapi.view.lobby.trainings import formatters
        return formatters.getMapIconPath(arena_type)
    except Exception:
        return ''


def build(arena_cache, map_pool):
    """Return CTF-only TrainingSettingsWindow rows for a server map pool."""
    allowed = _allowed_names(map_pool)
    rows = []
    items = getattr(arena_cache, 'iteritems', arena_cache.items)
    for arena_type_id, arena_type in items():
        if getattr(arena_type, 'gameplayName', None) != 'ctf':
            continue
        geometry_name = _text(getattr(arena_type, 'geometryName', ''))
        if not geometry_name or (allowed is not None and
                                 geometry_name not in allowed):
            continue
        name = _text(getattr(arena_type, 'name', geometry_name))
        rows.append({
            'label': name,
            'name': name,
            'arenaType': '',
            'key': arena_type_id,
            'size': int(getattr(arena_type, 'maxPlayersInTeam', 0) or 0),
            'time': int(getattr(arena_type, 'roundLength', 0) or 0) / 60,
            'description': '',
            'icon': _map_icon_path(arena_type),
        })
    rows.sort(key=lambda row: (row['label'].lower(), row['name'].lower(),
                               row['key']))
    return MapCatalog(rows)


def geometry_name(arena_cache, arena_type_id, map_pool):
    """Resolve one UI arena id only when it remains selectable by policy."""
    try:
        arena_type = arena_cache[arena_type_id]
    except (KeyError, TypeError):
        return None
    if getattr(arena_type, 'gameplayName', None) != 'ctf':
        return None
    name = _text(getattr(arena_type, 'geometryName', ''))
    if not name:
        return None
    allowed = _allowed_names(map_pool)
    if allowed is not None and name not in allowed:
        return None
    return name
