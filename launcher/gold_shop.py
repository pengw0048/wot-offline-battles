"""Add a vehicle to a save without charging its account.

The launcher queues a vehicle name; the client constructs the exact native
record on the next startup. Both sides reject duplicates, including names
queued before the first garage or before a legacy save names its vehicles.
"""

import io
import json
import os

try:
    from . import save_ledger, save_slots, vehicle_overlays
except ImportError:
    import save_ledger
    import save_slots
    import vehicle_overlays


class GoldShopError(Exception):
    """One purchase could not be made."""


INBOX_FILE_NAME = "launcher_inbox.json"
INBOX_SCHEMA = 1
LEDGER_FILE_NAME = save_ledger.LEDGER_FILE_NAME
# A save with more pending vehicles than this is a damaged file, not a
# shopping list.  The client applies the same limit.
MAX_PENDING_VEHICLES = 512


def inbox_path(slot_id, game_root=None, environment=None, root=None):
    directory = save_slots.slot_dir(slot_id, game_root, environment, root)
    return os.path.join(directory, INBOX_FILE_NAME)


def _read_json(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (IOError, OSError, ValueError, UnicodeError) as error:
        raise GoldShopError("The save could not be read: %s" % error)


def pending_vehicles(slot_id, game_root=None, environment=None, root=None):
    """Return the vehicle names this save has bought and not yet received."""
    try:
        value = _read_json(inbox_path(slot_id, game_root, environment, root))
    except save_slots.SaveSlotError:
        return []
    if not isinstance(value, dict) or value.get("schema") != INBOX_SCHEMA:
        return []
    names = value.get("vehicles")
    return [name for name in (names or ()) if isinstance(name, str)]


def _garage_records(slot_id, game_root=None, environment=None, root=None):
    """Return this save's garage records, empty when it has no garage yet."""
    try:
        path = save_ledger.ledger_path(slot_id, game_root, environment, root)
    except save_slots.SaveSlotError:
        return []
    value = _read_json(path)
    vehicles = value.get("vehicles") if isinstance(value, dict) else None
    if not isinstance(vehicles, dict):
        return []
    return [record for record in vehicles.values()
            if isinstance(record, dict)]


def owned_vehicles(slot_id, game_root=None, environment=None, root=None):
    """Return the vehicle names this save's garage already holds."""
    names = []
    for record in _garage_records(slot_id, game_root, environment, root):
        name = record.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return sorted(set(names))


def list_offers(slot_id, game_root, environment=None, root=None,
                catalogue=None):
    """Return every gold vehicle with what this save can do about it.

    ``catalogue`` lets a caller reuse a listing it already has. Reading it
    means opening the client's 50 MB package and parsing ten rosters, and it
    cannot change while the launcher runs, so a window that shows this on
    every save change should read it once.
    """
    owned = set(owned_vehicles(slot_id, game_root, environment, root))
    pending = set(pending_vehicles(slot_id, game_root, environment, root))
    offers = []
    if catalogue is None:
        catalogue = vehicle_overlays.list_gold_vehicles(game_root)
    for row in catalogue:
        offer = dict(row)
        offer["owned"] = row["name"] in owned
        offer["pending"] = row["name"] in pending
        offer["available"] = not (offer["owned"] or offer["pending"])
        offers.append(offer)
    return offers


def add_vehicle(slot_id, name, game_root, environment=None, root=None,
                is_running=None):
    """Queue one missing vehicle without changing any balance or garage row."""
    if is_running is None:
        try:
            from . import core
        except ImportError:
            import core

        is_running = core.game_is_running
    if callable(is_running) and is_running():
        raise GoldShopError("Close World of Tanks before adding a vehicle.")
    offers = dict(
        (row["name"], row)
        for row in vehicle_overlays.list_gold_vehicles(game_root))
    offer = offers.get(str(name))
    if offer is None:
        raise GoldShopError("This client does not offer %s." % (name,))
    if str(name) in owned_vehicles(slot_id, game_root, environment, root):
        raise GoldShopError("This save already owns %s." % offer["label"])
    pending = pending_vehicles(slot_id, game_root, environment, root)
    if str(name) in pending:
        raise GoldShopError(
            "%s is already queued and waiting for the game to start."
            % offer["label"])
    if len(pending) >= MAX_PENDING_VEHICLES:
        raise GoldShopError(
            "Start the game once to receive the vehicles already queued.")
    _write_inbox(
        inbox_path(slot_id, game_root, environment, root),
        pending + [str(name)])
    return dict(offer)


def _write_inbox(path, names):
    payload = {"schema": INBOX_SCHEMA, "vehicles": list(names)}
    temporary = path + ".tmp"
    try:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(temporary, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(
                payload, indent=2, sort_keys=True, ensure_ascii=False))
            stream.write(u"\n")
        os.replace(temporary, path)
    except (IOError, OSError, ValueError, UnicodeError) as error:
        try:
            os.remove(temporary)
        except (IOError, OSError):
            pass
        raise GoldShopError("The vehicle addition could not be saved: %s" % error)
