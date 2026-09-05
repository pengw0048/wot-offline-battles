"""Named save slots for one installation's earned player progress.

A save slot is a directory holding exactly the files that record what a player
has earned: the garage, the post-battle results, and the account settings the
retail server would otherwise own.  The client mod resolves those three paths
from ``config.json``'s ``save_slot`` field, so switching slots here and writing
that field is the whole of the switch.

What a slot deliberately does not own:

- ``server_endpoint.json`` and the waiting-room state describe this machine's
  current room, not the player's progress;
- ``vehicle_profiles.json`` (``vehicle_overlays``) modifies the shared client
  catalogue for a whole room.  It is a data mod, not a save, and mixing the two
  would make a room's vehicle data change when a player picked another slot.

The mod owns the contents of the three state files.  This module only creates,
renames, lists, and deletes their containing directories, plus the small
``save.json`` record naming the slot and how it was created.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time

SAVES_DIR_NAME = "saves"
DEFAULT_SLOT_ID = "default"
METADATA_NAME = "save.json"
METADATA_SCHEMA = 1

# How a slot was created.  ``unlocked`` is the historical offline garage: every
# vehicle owned and every balance effectively unlimited.  ``new_account``
# starts from the tier-1 vehicles the way a fresh retail account does.
MODE_UNLOCKED = "unlocked"
MODE_NEW_ACCOUNT = "new_account"
MODES = (MODE_UNLOCKED, MODE_NEW_ACCOUNT)

MAX_SLOT_NAME_LENGTH = 64
STATE_FILE_NAMES = (
    "garage_state.json",
    "postbattle_state.json",
    "account_state.json",
)
APPDATA_PARTS = ("Wargaming.net", "WorldOfTanks", "offline_lan_0922")
LEGACY_RELATIVE = "mods/configs/offline_lan_0922"

# The id becomes one directory name.  Keep it to characters that need no
# escaping on Windows and cannot walk out of the saves root.
_SLOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


class SaveSlotError(Exception):
    pass


def valid_slot_id(value):
    return bool(isinstance(value, str) and _SLOT_ID.match(value))


def _contained(root, path, label):
    absolute_root = os.path.abspath(root)
    absolute_path = os.path.abspath(path)
    try:
        if os.path.commonpath((absolute_root, absolute_path)) != absolute_root:
            raise SaveSlotError("%s escapes the saves directory." % label)
    except ValueError:
        raise SaveSlotError("%s escapes the saves directory." % label)
    return absolute_path


def saves_root(game_root=None, environment=None):
    """Return the saves directory, matching the mod's own resolution order.

    ``config.USER_DATA_DIR`` prefers ``%APPDATA%`` and falls back to the
    directory holding ``config.json``.  Resolving it the same way here means
    the launcher and the client always agree on where a slot lives.
    """
    environment = os.environ if environment is None else environment
    appdata = environment.get("APPDATA")
    if isinstance(appdata, str) and appdata.strip():
        return os.path.join(
            os.path.abspath(appdata.strip()), *APPDATA_PARTS, SAVES_DIR_NAME)
    if not game_root:
        raise SaveSlotError(
            "Save slots need either APPDATA or the game folder.")
    return os.path.join(
        os.path.abspath(game_root), *LEGACY_RELATIVE.split("/"),
        SAVES_DIR_NAME)


def slot_dir(slot_id, game_root=None, environment=None, root=None):
    if not valid_slot_id(slot_id):
        raise SaveSlotError(
            "A save id may only use letters, digits, _ and -.")
    root = saves_root(game_root, environment) if root is None else root
    return _contained(root, os.path.join(root, slot_id), "The save")


def metadata_path(slot_id, game_root=None, environment=None, root=None):
    return os.path.join(
        slot_dir(slot_id, game_root, environment, root), METADATA_NAME)


def _normalized_name(raw_name):
    if not isinstance(raw_name, str):
        raise SaveSlotError("The save name must be text.")
    name = " ".join(raw_name.split())
    if not name:
        raise SaveSlotError("The save name must not be empty.")
    if len(name) > MAX_SLOT_NAME_LENGTH:
        raise SaveSlotError(
            "The save name may be at most %d characters."
            % MAX_SLOT_NAME_LENGTH)
    return name


def _normalized_mode(raw_mode):
    mode = str(raw_mode or "").strip()
    if mode not in MODES:
        raise SaveSlotError("Unknown save type: %r" % (raw_mode,))
    return mode


def _read_metadata(path):
    try:
        with open(path, "rb") as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_metadata(path, value):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    os.replace(temporary, path)


def _record(slot_id, directory, metadata):
    """Return one displayable slot record.

    A slot whose ``save.json`` is missing or unreadable is still a real save:
    the three state files beside it are what the player earned.  Report it with
    a fallback name rather than hiding progress behind a damaged label.
    """
    metadata = metadata if isinstance(metadata, dict) else {}
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        name = slot_id
    mode = metadata.get("mode")
    if mode not in MODES:
        mode = MODE_UNLOCKED
    try:
        created = int(metadata.get("created", 0) or 0)
    except (TypeError, ValueError):
        created = 0
    return {
        "id": slot_id,
        "name": " ".join(name.split())[:MAX_SLOT_NAME_LENGTH],
        "mode": mode,
        "created": created,
        "path": directory,
        "has_state": any(
            os.path.isfile(os.path.join(directory, state_name))
            for state_name in STATE_FILE_NAMES),
    }


def default_record(game_root=None, environment=None, root=None):
    """Return the always-present default slot, created or not.

    An installation that upgraded from an older package has no ``saves``
    directory at all until the client migrates its state on the next start, so
    the default slot must be listable before it exists on disk.
    """
    directory = slot_dir(DEFAULT_SLOT_ID, game_root, environment, root)
    return _record(
        DEFAULT_SLOT_ID, directory,
        _read_metadata(os.path.join(directory, METADATA_NAME)))


def list_slots(game_root=None, environment=None, root=None):
    """Return every save slot, default first and the rest by name."""
    root = saves_root(game_root, environment) if root is None else root
    records = {DEFAULT_SLOT_ID: default_record(root=root)}
    try:
        entries = sorted(os.listdir(root))
    except (IOError, OSError):
        entries = []
    for entry in entries:
        if not valid_slot_id(entry) or entry == DEFAULT_SLOT_ID:
            continue
        directory = os.path.join(root, entry)
        if not os.path.isdir(directory):
            continue
        records[entry] = _record(
            entry, directory,
            _read_metadata(os.path.join(directory, METADATA_NAME)))
    ordered = [records.pop(DEFAULT_SLOT_ID)]
    ordered.extend(sorted(
        records.values(), key=lambda row: (row["name"].lower(), row["id"])))
    return ordered


def read_slot(slot_id, game_root=None, environment=None, root=None):
    directory = slot_dir(slot_id, game_root, environment, root)
    if slot_id != DEFAULT_SLOT_ID and not os.path.isdir(directory):
        raise SaveSlotError("This save no longer exists.")
    return _record(
        slot_id, directory,
        _read_metadata(os.path.join(directory, METADATA_NAME)))


def _allocate_slot_id(name, root):
    """Derive a directory name from the display name.

    A Chinese or otherwise non-ASCII name leaves nothing usable behind, so fall
    back to a numbered ``save`` id instead of encoding the name into a path.
    """
    base = _UNSAFE_ID_CHARS.sub("-", name).strip("-")[:48]
    if not base or not _SLOT_ID.match(base):
        base = "save"
    candidate = base
    suffix = 2
    while os.path.exists(os.path.join(root, candidate)):
        candidate = "%s-%d" % (base, suffix)
        suffix += 1
        if suffix > 9999:
            raise SaveSlotError("Too many saves with a similar name.")
    return candidate


def create_slot(name, mode, game_root=None, environment=None, root=None,
                now=None):
    """Create one empty slot directory and its ``save.json`` record.

    The state files are deliberately not written here.  The client creates each
    one the first time it has something to save, and an empty slot is exactly
    what a new save is.
    """
    name = _normalized_name(name)
    mode = _normalized_mode(mode)
    root = saves_root(game_root, environment) if root is None else root
    if not os.path.isdir(root):
        os.makedirs(root)
    slot_id = _allocate_slot_id(name, root)
    directory = slot_dir(slot_id, root=root)
    try:
        os.makedirs(directory)
    except (IOError, OSError) as error:
        raise SaveSlotError("The save could not be created: %s" % error)
    try:
        _write_metadata(os.path.join(directory, METADATA_NAME), {
            "schema": METADATA_SCHEMA,
            "id": slot_id,
            "name": name,
            "mode": mode,
            "created": int(time.time() if now is None else now),
        })
    except (IOError, OSError) as error:
        shutil.rmtree(directory, ignore_errors=True)
        raise SaveSlotError("The save could not be created: %s" % error)
    return read_slot(slot_id, root=root)


def rename_slot(slot_id, name, game_root=None, environment=None, root=None):
    """Change a slot's display name, keeping its directory and state."""
    name = _normalized_name(name)
    record = read_slot(slot_id, game_root, environment, root)
    path = os.path.join(record["path"], METADATA_NAME)
    metadata = _read_metadata(path) or {}
    metadata.update({
        "schema": METADATA_SCHEMA,
        "id": slot_id,
        "name": name,
        "mode": record["mode"],
        "created": record["created"] or int(time.time()),
    })
    try:
        _write_metadata(path, metadata)
    except (IOError, OSError) as error:
        raise SaveSlotError("The save could not be renamed: %s" % error)
    return read_slot(slot_id, game_root, environment, root)


def delete_slot(slot_id, game_root=None, environment=None, root=None):
    """Delete one slot directory and everything the player earned in it.

    The default slot is the fallback every install can always select, and it
    also owns the state migrated from packages that predate save slots, so it
    is never deletable.  Resetting it is what "Reset all offline data" is for.
    """
    if slot_id == DEFAULT_SLOT_ID:
        raise SaveSlotError("The default save cannot be deleted.")
    record = read_slot(slot_id, game_root, environment, root)
    try:
        shutil.rmtree(record["path"])
    except (IOError, OSError) as error:
        raise SaveSlotError("The save could not be deleted: %s" % error)
    return record
