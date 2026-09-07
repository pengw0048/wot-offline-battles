"""Buy a gold vehicle for one save, from the launcher.

#1513 prices 196 vehicles in gold and marks 145 of them ``notInShop``: reward
and event tanks the retail shop never sold and that no tech tree node leads
to. Offline they are exactly as reachable as the rest, and the launcher is the
only place a player can reach them, so the shop lives here.

The launcher does the arithmetic and the client does the building. A purchase
takes the gold out of the save's own ledger and leaves the vehicle's name in
the save's launcher inbox; the next client to start that save turns the name
into a real garage vehicle, because only a client can produce the compact
descriptors, crew and ammunition a garage record holds.
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
        path = os.path.join(
            save_slots.slot_dir(slot_id, game_root, environment, root),
            LEDGER_FILE_NAME)
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


def unnamed_vehicles(slot_id, game_root=None, environment=None, root=None):
    """Count the garage records this save does not name.

    Only a client can turn a compact descriptor into a vehicle name, so a save
    written before records carried names says nothing about what it owns until
    the game starts it once. The shop must not sell into such a save: it would
    take the gold for a vehicle the client already holds and then decline,
    correctly, to deliver a second copy.
    """
    return sum(
        1 for record in _garage_records(slot_id, game_root, environment, root)
        if not (isinstance(record.get("name"), str) and record["name"]))


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
    balances = save_ledger.read_balances(
        slot_id, game_root, environment, root)
    gold = balances["gold"] if balances else 0
    offers = []
    if catalogue is None:
        catalogue = vehicle_overlays.list_gold_vehicles(game_root)
    for row in catalogue:
        offer = dict(row)
        offer["owned"] = row["name"] in owned
        offer["pending"] = row["name"] in pending
        offer["affordable"] = row["gold"] <= gold
        offers.append(offer)
    return offers


def buy_vehicle(slot_id, name, game_root, environment=None, root=None,
                is_running=None):
    """Take the gold and leave the vehicle for the client to build.

    The two writes cannot be one transaction across two files, so the vehicle
    is queued before the gold is taken: a crash between them leaves a vehicle
    the player has not paid for rather than gold that bought nothing.
    """
    if is_running is None:
        try:
            from . import core
        except ImportError:
            import core

        is_running = core.game_is_running
    if callable(is_running) and is_running():
        raise GoldShopError("Close World of Tanks before buying a vehicle.")
    offers = dict(
        (row["name"], row)
        for row in vehicle_overlays.list_gold_vehicles(game_root))
    offer = offers.get(str(name))
    if offer is None:
        raise GoldShopError("This client does not sell %s." % (name,))
    if unnamed_vehicles(slot_id, game_root, environment, root):
        raise GoldShopError(
            "Start the game once on this save so it can list the vehicles it "
            "already owns.")
    if str(name) in owned_vehicles(slot_id, game_root, environment, root):
        raise GoldShopError("This save already owns %s." % offer["label"])
    pending = pending_vehicles(slot_id, game_root, environment, root)
    if str(name) in pending:
        raise GoldShopError(
            "%s is already bought and waiting for the game to start."
            % offer["label"])
    if len(pending) >= MAX_PENDING_VEHICLES:
        raise GoldShopError(
            "Start the game once to receive the vehicles already bought.")
    balances = save_ledger.read_balances(
        slot_id, game_root, environment, root)
    if balances is None:
        raise GoldShopError(
            "Start this save in the game once before buying a vehicle.")
    if balances["gold"] < offer["gold"]:
        raise GoldShopError(
            "%s costs %d gold and this save has %d."
            % (offer["label"], offer["gold"], balances["gold"]))
    _write_inbox(
        inbox_path(slot_id, game_root, environment, root),
        pending + [str(name)])
    save_ledger.write_balances(
        slot_id, {"gold": balances["gold"] - offer["gold"]},
        game_root, environment, root, is_running=lambda: False)
    return dict(offer, gold_left=balances["gold"] - offer["gold"])


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
        raise GoldShopError("The purchase could not be saved: %s" % error)
