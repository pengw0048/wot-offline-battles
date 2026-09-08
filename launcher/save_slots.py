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

# What a save multiplies its battle earnings by, as a whole percentage.  The
# client reads this and scales the credits and experience every battle pays,
# so 100 is an ordinary save and 250 earns two and a half times as much.  It
# is an integer rather than a fraction because the launcher writes it from
# Python 3 and the game reads it from Python 2.7.
EARNINGS_KEY = "earnings_percent"
DEFAULT_EARNINGS_PERCENT = 100
MIN_EARNINGS_PERCENT = 1
MAX_EARNINGS_PERCENT = 10000

MAX_SLOT_NAME_LENGTH = 64
STATE_FILE_NAMES = (
    "garage_state.json",
    "postbattle_state.json",
    "account_state.json",
)
# The client deletes what it has delivered from this one, so a restore that
# left it behind would hand the player vehicles a different save paid for.
LAUNCHER_INBOX_NAME = "launcher_inbox.json"
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
    # The slot itself must not be a symlink or junction. Resolve the root once
    # so APPDATA may legitimately be redirected, then require every component
    # below that root to retain its expected real path.
    relative = os.path.relpath(absolute_path, absolute_root)
    expected_real_path = os.path.abspath(
        os.path.join(os.path.realpath(absolute_root), relative))
    real_path = os.path.realpath(absolute_path)
    if os.path.normcase(real_path) != os.path.normcase(expected_real_path):
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
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    os.replace(temporary, path)


def normalized_earnings_percent(value):
    """Return one save's earnings multiplier, clamped to what it may be."""
    try:
        percent = int(value)
    except (TypeError, ValueError):
        return DEFAULT_EARNINGS_PERCENT
    return max(MIN_EARNINGS_PERCENT, min(MAX_EARNINGS_PERCENT, percent))


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
        "earnings_percent": normalized_earnings_percent(
            metadata.get(EARNINGS_KEY, DEFAULT_EARNINGS_PERCENT)),
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
        try:
            directory = slot_dir(entry, root=root)
        except SaveSlotError:
            continue
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
    while (candidate.lower() == DEFAULT_SLOT_ID.lower() or
           os.path.exists(os.path.join(root, candidate))):
        candidate = "%s-%d" % (base, suffix)
        suffix += 1
        if suffix > 9999:
            raise SaveSlotError("Too many saves with a similar name.")
    return candidate


def create_slot(name, mode, game_root=None, environment=None, root=None,
                now=None, earnings_percent=DEFAULT_EARNINGS_PERCENT):
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
            EARNINGS_KEY: normalized_earnings_percent(earnings_percent),
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
    if (slot_id == DEFAULT_SLOT_ID and
            not os.path.isdir(record["path"])):
        try:
            os.makedirs(record["path"])
        except (IOError, OSError) as error:
            raise SaveSlotError("The save could not be renamed: %s" % error)
    path = os.path.join(record["path"], METADATA_NAME)
    metadata = _read_metadata(path) or {}
    metadata.update({
        "schema": METADATA_SCHEMA,
        "id": slot_id,
        "name": name,
        "mode": record["mode"],
        EARNINGS_KEY: record["earnings_percent"],
        "created": record["created"] or int(time.time()),
    })
    try:
        _write_metadata(path, metadata)
    except (IOError, OSError) as error:
        raise SaveSlotError("The save could not be renamed: %s" % error)
    return read_slot(slot_id, game_root, environment, root)


def set_earnings_percent(slot_id, percent, game_root=None, environment=None,
                         root=None):
    """Change what one save multiplies its battle earnings by.

    Unlike the balances, this lives in the launcher's own ``save.json`` rather
    than in the client's state, so it can be set on a save that has never been
    started -- which is when a player most wants to decide it.
    """
    percent = normalized_earnings_percent(percent)
    record = read_slot(slot_id, game_root, environment, root)
    if not os.path.isdir(record["path"]):
        try:
            os.makedirs(record["path"])
        except (IOError, OSError) as error:
            raise SaveSlotError("The save could not be changed: %s" % error)
    path = os.path.join(record["path"], METADATA_NAME)
    metadata = _read_metadata(path) or {}
    metadata.update({
        "schema": METADATA_SCHEMA,
        "id": slot_id,
        "name": record["name"],
        "mode": record["mode"],
        EARNINGS_KEY: percent,
        "created": record["created"] or int(time.time()),
    })
    try:
        _write_metadata(path, metadata)
    except (IOError, OSError) as error:
        raise SaveSlotError("The save could not be changed: %s" % error)
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


# What a backup archive holds: exactly the files that describe one save, plus
# the launcher's own record of it.  The rotated and quarantined copies the
# client keeps are deliberately included: a player restoring a broken save
# wants the evidence back too, and they are small.
BACKUP_SCHEMA = 1
BACKUP_MANIFEST_NAME = "wot-offline-save.json"
# A save is a handful of JSON files.  Anything far larger is not one, and
# unpacking it would be the archive deciding how much disk to use.
MAX_BACKUP_MEMBER_BYTES = 64 * 1024 * 1024
MAX_BACKUP_MEMBERS = 256


def _state_file_names(directory):
    """Return the save's own files, live copies and kept evidence alike."""
    try:
        names = sorted(os.listdir(directory))
    except (IOError, OSError) as error:
        raise SaveSlotError("The save could not be read: %s" % error)
    kept = []
    for name in names:
        if not os.path.isfile(os.path.join(directory, name)):
            continue
        if name == METADATA_NAME or _belongs_to_state(name):
            kept.append(name)
    return kept


def _belongs_to_state(name):
    """Say whether one file name is part of a save's own state.

    The client writes ``garage_state.json`` and keeps ``.backup1``,
    ``.rejected-...`` and ``.shrunk-...`` copies of it beside the live file,
    all with the same stem.  Matching on the stem keeps this launcher from
    having to know each suffix the client may add.
    """
    if name.endswith(".tmp"):
        # A half-written replacement is not state; it is what the atomic
        # write left behind when it was interrupted.
        return False
    for state_name in STATE_FILE_NAMES + (LAUNCHER_INBOX_NAME,):
        stem = os.path.splitext(state_name)[0]
        if name == state_name or name.startswith(stem + "."):
            return True
    return False


def backup_slot(slot_id, archive_path, game_root=None, environment=None,
                root=None):
    """Write one save to a ZIP archive the player owns and can keep anywhere.

    Copying the folder by hand works and is documented, but a single file a
    player can put on another disk is what actually gets kept.  The archive
    records which save it came from so a restore can say when it is being
    put back into a different one.
    """
    import zipfile

    record = read_slot(slot_id, game_root, environment, root)
    directory = record["path"]
    if not os.path.isdir(directory):
        raise SaveSlotError("This save has nothing to back up yet.")
    names = _state_file_names(directory)
    if not names:
        raise SaveSlotError("This save has nothing to back up yet.")
    archive_path = os.path.abspath(archive_path)
    manifest = {
        "schema": BACKUP_SCHEMA,
        "id": record["id"],
        "name": record["name"],
        "mode": record["mode"],
        "files": names,
        "created": int(time.time()),
    }
    temporary = archive_path + ".tmp"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                BACKUP_MANIFEST_NAME,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            for name in names:
                archive.write(os.path.join(directory, name), name)
        os.replace(temporary, archive_path)
    except (IOError, OSError, zipfile.BadZipFile) as error:
        try:
            if os.path.isfile(temporary):
                os.unlink(temporary)
        except (IOError, OSError):
            pass
        raise SaveSlotError("The backup could not be written: %s" % error)
    return {"path": archive_path, "files": names, "id": record["id"],
            "name": record["name"]}


def read_backup(archive_path):
    """Describe one archive without unpacking it."""
    import zipfile

    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = [info.filename for info in archive.infolist()
                     if not info.is_dir()]
            if len(names) > MAX_BACKUP_MEMBERS:
                raise SaveSlotError("This file holds too many members.")
            for info in archive.infolist():
                if info.file_size > MAX_BACKUP_MEMBER_BYTES:
                    raise SaveSlotError(
                        "This file holds a member that is too large.")
            if BACKUP_MANIFEST_NAME in names:
                manifest = json.loads(
                    archive.read(BACKUP_MANIFEST_NAME).decode("utf-8"))
            else:
                manifest = {}
    except SaveSlotError:
        raise
    except (IOError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise SaveSlotError("This is not a readable save backup: %s" % error)
    if not isinstance(manifest, dict):
        manifest = {}
    state_names = [name for name in names
                   if name != BACKUP_MANIFEST_NAME and _restorable_name(name)]
    if not state_names:
        raise SaveSlotError(
            "This file holds no save data. Pick a backup this Launcher "
            "wrote, or a folder copy of a save.")
    return {
        "path": os.path.abspath(archive_path),
        "id": (manifest.get("id") if isinstance(manifest.get("id"), str)
               else None),
        "name": (manifest.get("name")
                 if isinstance(manifest.get("name"), str) else None),
        "files": sorted(state_names),
    }


def _restorable_name(name):
    """Accept only a plain file name that belongs to a save.

    A member with a path, a drive or a parent reference is refused rather
    than sanitised: this Launcher wrote the archives it restores, and a
    hand-made one that disagrees is not worth guessing about.
    """
    if not name or name != os.path.basename(name):
        return False
    if name in (os.curdir, os.pardir) or os.path.isabs(name):
        return False
    if "\\" in name or "/" in name or ":" in name:
        return False
    return name == METADATA_NAME or _belongs_to_state(name)


def restore_slot(slot_id, archive_path, game_root=None, environment=None,
                 root=None, is_running=None, keep_name=True):
    """Replace one save's files with a backup's, keeping what it replaces.

    The client owns these files while it runs, so a restore refuses to touch
    a save the game may be writing.  The files being replaced are moved into
    the same slot under a ``pre-restore`` stamp rather than deleted, because a
    player who restores the wrong archive has then lost the save twice.
    """
    import zipfile

    if is_running is None:
        try:
            from . import core
        except ImportError:
            import core

        is_running = core.game_is_running
    if callable(is_running) and is_running():
        raise SaveSlotError(
            "Close World of Tanks before restoring a save.")
    backup = read_backup(archive_path)
    record = read_slot(slot_id, game_root, environment, root)
    directory = record["path"]
    if not os.path.isdir(directory):
        try:
            os.makedirs(directory)
        except (IOError, OSError) as error:
            raise SaveSlotError("The save could not be restored: %s" % error)
    # Fixed width, milliseconds included, so two restores in one second do
    # not collide on the folder that holds what they replaced.
    now = time.time()
    stamp = "pre-restore-%s-%03d" % (
        time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)),
        int((now % 1) * 1000))
    replaced = os.path.join(directory, stamp)
    moved = []
    written = []
    try:
        existing = _state_file_names(directory)
        if existing:
            os.makedirs(replaced)
            for name in existing:
                os.replace(os.path.join(directory, name),
                           os.path.join(replaced, name))
                moved.append(name)
                if keep_name and name == METADATA_NAME:
                    shutil.copyfile(os.path.join(replaced, name),
                                    os.path.join(directory, name))
                    written.append(name)
        with zipfile.ZipFile(archive_path) as archive:
            for name in backup["files"]:
                if keep_name and name == METADATA_NAME:
                    # The archive's own name would rename the save it is put
                    # into, which is not what restoring one file of it means.
                    continue
                target = _contained(
                    directory, os.path.join(directory, name),
                    "The restored file")
                with archive.open(name) as source:
                    payload = source.read(MAX_BACKUP_MEMBER_BYTES + 1)
                if len(payload) > MAX_BACKUP_MEMBER_BYTES:
                    raise SaveSlotError(
                        "This file holds a member that is too large.")
                with open(target, "wb") as destination:
                    destination.write(payload)
                written.append(name)
    except Exception as error:
        for name in written:
            try:
                os.unlink(os.path.join(directory, name))
            except (IOError, OSError):
                pass
        rollback_failed = False
        for name in moved:
            try:
                os.replace(os.path.join(replaced, name),
                           os.path.join(directory, name))
            except (IOError, OSError):
                rollback_failed = True
        if not rollback_failed:
            shutil.rmtree(replaced, ignore_errors=True)
        if isinstance(error, SaveSlotError):
            raise
        raise SaveSlotError("The save could not be restored: %s" % error)
    return {
        "id": record["id"],
        "name": record["name"],
        "files": sorted(name for name in written
                        if not (keep_name and name == METADATA_NAME)),
        "replaced": replaced if moved else None,
        "from_id": backup["id"],
        "from_name": backup["name"],
    }


def open_slot_folder(slot_id, game_root=None, environment=None, root=None,
                     runner=None):
    """Open one save's folder in Windows Explorer.

    Copying a file out and putting it back is the recovery every player
    already knows how to do, so the folder itself is the feature.  The
    directory is created when it is missing: a save that has never been
    started still has a folder the player can drop a backup into.
    """
    import subprocess

    directory = slot_dir(slot_id, game_root, environment, root)
    if not os.path.isdir(directory):
        try:
            os.makedirs(directory)
        except (IOError, OSError) as error:
            raise SaveSlotError("The save folder is unavailable: %s" % error)
    runner = subprocess.Popen if runner is None else runner
    try:
        runner(["explorer.exe", os.path.normpath(directory)],
               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (IOError, OSError) as error:
        raise SaveSlotError("The save folder could not be opened: %s" % error)
    return directory
