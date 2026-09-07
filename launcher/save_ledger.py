"""Read and edit one save's account balances from the Launcher.

The client keeps a save's credits, gold and free experience in the ``ledger``
section of its ``garage_state.json``.  Gold is the one currency an offline
account can never earn -- there is no store to buy it from and no battle that
pays it -- so the Launcher is where a player decides how much of it a save has,
and the same panel edits the credits and free experience the save earned.
Before a garage exists, initial balances live in the save metadata; the client
uses them when it creates the first garage, without fabricated vehicle data.

Only one process may own that file at a time.  The client writes it at every
accepted garage change, so every edit here refuses while a game is running,
and every write replaces the file atomically rather than updating it in place.
"""

import io
import json
import os

try:
    from . import save_slots
except ImportError:
    import save_slots


class SaveLedgerError(Exception):
    """One balance could not be read or written."""


LEDGER_FILE_NAME = "garage_state.json"
CURRENCIES = ("credits", "gold", "freeXP")
# The client refuses to publish a balance it cannot represent, and #1513's own
# account fields are 32-bit signed.
MAX_BALANCE = 2 ** 31 - 1
INITIAL_WALLET_KEY = "initial_wallet"
DEFAULT_BALANCES = {
    save_slots.MODE_NEW_ACCOUNT: {"credits": 100000, "gold": 0, "freeXP": 0},
    save_slots.MODE_UNLOCKED: {
        "credits": 100000000, "gold": 1000000, "freeXP": 100000000},
}


def ledger_path(slot_id, game_root=None, environment=None, root=None):
    directory = save_slots.slot_dir(slot_id, game_root, environment, root)
    path = os.path.join(directory, LEDGER_FILE_NAME)
    if slot_id == save_slots.DEFAULT_SLOT_ID and not os.path.isfile(path):
        # Match the client's default-slot migration order before its first run.
        legacy_dirs = [os.path.dirname(os.path.dirname(directory))]
        if game_root is not None and root is None:
            legacy_dirs.append(os.path.join(
                game_root, *save_slots.LEGACY_RELATIVE.split("/")))
        for legacy in legacy_dirs:
            candidate = os.path.join(legacy, LEDGER_FILE_NAME)
            if os.path.isfile(candidate):
                return candidate
    return path


def _read_state(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError, UnicodeError) as error:
        raise SaveLedgerError("The save could not be read: %s" % error)
    if not isinstance(value, dict):
        raise SaveLedgerError("The save is not in the expected format.")
    return value


def _balance(value):
    try:
        return max(0, min(MAX_BALANCE, int(value)))
    except (TypeError, ValueError):
        return 0


def _initial_balances(slot_id, game_root=None, environment=None, root=None):
    path = save_slots.metadata_path(slot_id, game_root, environment, root)
    metadata = _read_state(path) or {}
    mode = metadata.get("mode", save_slots.MODE_UNLOCKED)
    balances = dict(DEFAULT_BALANCES.get(mode, DEFAULT_BALANCES[save_slots.MODE_UNLOCKED]))
    initial = metadata.get(INITIAL_WALLET_KEY)
    if isinstance(initial, dict):
        balances.update((name, _balance(initial[name]))
                        for name in CURRENCIES if name in initial)
    return balances


def read_balances(slot_id, game_root=None, environment=None, root=None):
    """Read the earned wallet, or the editable initial wallet before startup."""
    try:
        path = ledger_path(slot_id, game_root, environment, root)
    except save_slots.SaveSlotError:
        return None
    state = _read_state(path)
    ledger = state.get("ledger") if state else None
    wallet = ledger.get("wallet") if isinstance(ledger, dict) else None
    if not isinstance(wallet, dict):
        return _initial_balances(slot_id, game_root, environment, root)
    return dict((name, _balance(wallet.get(name))) for name in CURRENCIES)


def write_balances(slot_id, balances, game_root=None, environment=None,
                   root=None, is_running=None):
    """Replace one save's balances, keeping everything else it holds.

    Existing garages keep their crew, research and receipts untouched.
    A save without a garage stores initial balances in save.json instead.
    """
    if is_running is None:
        try:
            from . import core
        except ImportError:
            import core

        is_running = core.game_is_running
    if callable(is_running) and is_running():
        raise SaveLedgerError(
            "Close World of Tanks before changing a save's balances.")
    try:
        path = ledger_path(slot_id, game_root, environment, root)
    except save_slots.SaveSlotError as error:
        raise SaveLedgerError(str(error))
    state = _read_state(path)
    if state is None:
        updated = _initial_balances(slot_id, game_root, environment, root)
        updated.update((name, _balance(balances[name]))
                       for name in CURRENCIES if name in balances)
        metadata_path = save_slots.metadata_path(
            slot_id, game_root, environment, root)
        metadata = _read_state(metadata_path) or {}
        metadata[INITIAL_WALLET_KEY] = updated
        os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
        _write_state(metadata_path, metadata)
        return updated
    ledger = state.setdefault("ledger", {})
    if not isinstance(ledger, dict):
        raise SaveLedgerError("The save is not in the expected format.")
    wallet = ledger.get("wallet")
    if not isinstance(wallet, dict):
        wallet = _initial_balances(slot_id, game_root, environment, root)
    updated = dict(wallet)
    for name in CURRENCIES:
        if name in balances:
            updated[name] = _balance(balances[name])
    ledger["wallet"] = updated
    _write_state(path, state)
    return dict((name, _balance(updated.get(name))) for name in CURRENCIES)


def _write_state(path, state):
    temporary = path + ".tmp"
    try:
        with io.open(temporary, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(
                state, indent=2, sort_keys=True, ensure_ascii=False))
            stream.write(u"\n")
        os.replace(temporary, path)
    except (IOError, OSError, ValueError, UnicodeError) as error:
        try:
            os.remove(temporary)
        except (IOError, OSError):
            pass
        raise SaveLedgerError("The save could not be written: %s" % error)
