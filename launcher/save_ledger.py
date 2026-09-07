"""Read and edit one save's account balances from the Launcher.

The client keeps a save's credits, gold and free experience in the ``ledger``
section of its ``garage_state.json``.  Gold is the one currency an offline
account can never earn -- there is no store to buy it from and no battle that
pays it -- so the Launcher is where a player decides how much of it a save has,
and the same panel shows the credits and free experience the save earned.

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


def ledger_path(slot_id, game_root=None, environment=None, root=None):
    directory = save_slots.slot_dir(slot_id, game_root, environment, root)
    return os.path.join(directory, LEDGER_FILE_NAME)


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


def read_balances(slot_id, game_root=None, environment=None, root=None):
    """Return one save's balances, or None if it has never been started.

    A save that has not run yet has no state file at all.  That is not an
    error and not a zero balance: the client decides what a new save starts
    with, from the account type it was created as, and it has not done so yet.
    """
    try:
        path = ledger_path(slot_id, game_root, environment, root)
    except save_slots.SaveSlotError:
        # Without APPDATA or a game folder there is no save to read, which is
        # the same answer as a save that has never been started.
        return None
    state = _read_state(path)
    if state is None:
        return None
    ledger = state.get("ledger")
    wallet = ledger.get("wallet") if isinstance(ledger, dict) else None
    if not isinstance(wallet, dict):
        # A save written before the ledger existed keeps its garage; the
        # client seeds the balances the next time it starts.
        return None
    return dict((name, _balance(wallet.get(name))) for name in CURRENCIES)


def write_balances(slot_id, balances, game_root=None, environment=None,
                   root=None, is_running=None):
    """Replace one save's balances, keeping everything else it holds.

    Only the three balance fields are rewritten.  The garage, the crew, the
    research and the battle receipts in the same file belong to the client and
    are passed through untouched.
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
        raise SaveLedgerError(
            "Start this save in the game once before changing its balances.")
    ledger = state.get("ledger")
    if not isinstance(ledger, dict):
        raise SaveLedgerError(
            "Start this save in the game once before changing its balances.")
    wallet = ledger.get("wallet")
    if not isinstance(wallet, dict):
        raise SaveLedgerError(
            "Start this save in the game once before changing its balances.")
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
