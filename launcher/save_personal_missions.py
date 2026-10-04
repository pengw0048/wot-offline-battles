"""Edit regular personal missions and reconcile their reward eligibility.

The four #1513 operations each have five chains of fifteen missions. Values
are 1 (main conditions) or 2 (main and additional conditions); omission means
incomplete. The client grants missing rewards on the next garage load.
"""

import os
import gettext
import time
import zipfile

try:
    from . import core, save_ledger, save_slots, vehicle_overlays
except ImportError:
    import core
    import save_ledger
    import save_slots
    import vehicle_overlays


INITIAL_KEY = "initial_personal_missions"
OPERATIONS = ("StuG IV", "T28 Concept", "T 55A", "Object 260")
CHAINS = ("LT", "HT", "MT", "TD", "SPG")
MAX_ORDERS = 2 ** 31 - 1


def mission_ids(operation, chain):
    if operation not in range(4) or chain not in range(5):
        raise ValueError("Invalid personal-mission operation or chain.")
    start = operation * 75 + chain * 15 + 1
    return tuple(range(start, start + 15))


def normalize(progress):
    result = {}
    if not isinstance(progress, dict):
        return result
    for key, value in progress.items():
        try:
            mission = int(key)
        except (TypeError, ValueError):
            continue
        if 1 <= mission <= 300 and type(value) is int and value in (1, 2):
            result[str(mission)] = value
    return result


def prerequisites(mission):
    """The regular list.xml unlock graph: unordered tasks, then a final."""
    if type(mission) is not int or not 1 <= mission <= 300:
        raise ValueError("Invalid personal-mission ID.")
    operation, offset = divmod(mission - 1, 75)
    if offset % 15 == 14:
        return tuple(range(mission - 14, mission))
    if operation:
        return tuple((operation - 1) * 75 + 15 * chain for chain in range(1, 6))
    return ()


def complete_prerequisites(progress, newly_completed=None):
    """Add required main completions without upgrading any task to honors."""
    result = normalize(progress)
    pending = ([int(key) for key in result] if newly_completed is None
               else list(newly_completed))
    while pending:
        for required in prerequisites(pending.pop()):
            key = str(required)
            if key not in result:
                result[key] = 1
                pending.append(required)
    return result


def edit_progress(progress, mission_ids, value):
    """Apply one UI action, including dependent resets and unlock closure."""
    result = normalize(progress)
    if type(value) is not int or value not in (0, 1, 2):
        raise ValueError("Invalid personal-mission state.")
    ids = set(mission_ids)
    newly_completed = []
    for mission in ids:
        prerequisites(mission)
        if value:
            if str(mission) not in result:
                newly_completed.append(mission)
            result[str(mission)] = value
        else:
            result.pop(str(mission), None)
    if value:
        # Downgrading honors changes no other task, including a final that
        # was legitimately completed with orders instead of its fourteen tasks.
        return complete_prerequisites(result, newly_completed)
    if not ids:
        return result
    # Include absent intermediate finals: an older or order-skipped save can
    # contain later operations without every prerequisite completion flag.
    # A main reset invalidates every class in every later operation.
    finals = {((mission - 1) // 15 + 1) * 15 for mission in ids}
    last_allowed = (min((mission - 1) // 75 for mission in ids) + 1) * 75
    return {key: level for key, level in result.items()
            if int(key) not in finals and int(key) <= last_allowed}


def _target(slot_id, game_root=None, environment=None, root=None):
    path = save_ledger.ledger_path(slot_id, game_root, environment, root)
    state = save_ledger._read_state(path)
    if state is not None:
        ledger = state.get("ledger", {})
        if not isinstance(ledger, dict):
            raise save_ledger.SaveLedgerError("The save is not in the expected format.")
        missions = ledger.get("personalMissions", {})
        if not isinstance(missions, dict):
            raise save_ledger.SaveLedgerError("The save is not in the expected format.")
        progress = missions.get("requestedCompleted", missions.get("completed", {}))
        return path, state, progress, True
    path = save_slots.metadata_path(slot_id, game_root, environment, root)
    state = save_ledger._read_state(path) or {}
    return path, state, state.get(INITIAL_KEY, {}), False


def read_progress(slot_id, game_root=None, environment=None, root=None):
    return normalize(_target(slot_id, game_root, environment, root)[2])


def read_edit_status(slot_id, game_root=None, environment=None, root=None):
    unused, state, unused_progress, has_garage = _target(
        slot_id, game_root, environment, root)
    missions = state.get("ledger", {}).get("personalMissions", {}) if has_garage else {}
    return {"pending": "requestedCompleted" in missions,
            "error": str(missions.get("resetError") or "")}


def write_progress(slot_id, progress, game_root=None, environment=None,
                   root=None, is_running=None):
    if (is_running or core.game_is_running)():
        raise save_ledger.SaveLedgerError(
            "Close World of Tanks before editing personal missions.")
    normalized = normalize(progress)
    if normalized != progress:
        raise save_ledger.SaveLedgerError("Invalid personal-mission progress.")
    path, state, previous, has_garage = _target(
        slot_id, game_root, environment, root)
    previous = normalize(previous)
    removed = [int(key) for key in previous if key not in normalized]
    if removed:
        normalized = edit_progress(normalized, removed, 0)
    normalized = complete_prerequisites(normalized,
        [int(key) for key in normalized if key not in previous])
    if has_garage:
        missions = state.setdefault("ledger", {}).setdefault("personalMissions", {})
        # The native client can identify granted crew and settle orders. Keep
        # actual progress and paid markers together until that transaction is
        # durably committed; a failed withdrawal must not reopen rewards.
        missions["requestedCompleted"] = normalized
        missions.pop("resetError", None)
        def selectable(qid):
            return (type(qid) is int and 1 <= qid <= 300 and
                    normalized.get(str(qid)) != 2 and
                    all(str(required) in normalized for required in prerequisites(qid)))
        selected = [qid for qid in missions.get("requestedRegular", missions.get("regular", ()))
                    if selectable(qid)]
        # A reset final must be available for the player's next battle even if
        # its previous honors state removed it from the native selection.
        reset = sorted(int(key) for key, value in normalize(previous).items()
                       if normalized.get(key, 0) < value)
        reset_chains = set()
        for qid in reset:
            chain = ((qid - 1) % 75) // 15
            if chain not in reset_chains and selectable(qid):
                selected = [other for other in selected
                            if ((other - 1) % 75) // 15 != chain]
                selected.append(qid)
                reset_chains.add(chain)
        missions["requestedRegular"] = selected
    else:
        state[INITIAL_KEY] = normalized
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_ledger._write_state(path, state)
    return normalized


def read_account_fields(slot_id, game_root=None, environment=None, root=None):
    unused, state, unused_progress, has_garage = _target(slot_id, game_root, environment, root)
    if has_garage:
        ledger = state.get("ledger", {})
        orders = ledger.get("personalMissions", {}).get("orders", 0)
        badges = ledger.get("accountBadges", {})
    else:
        orders = state.get("initial_personal_orders", 0)
        badges = state.get("initial_account_badges", {})
    return {"orders": max(0, min(MAX_ORDERS, int(orders))),
            "badges": dict(badges) if isinstance(badges, dict) else {}}


def write_account_fields(slot_id, game_root=None, badges=None,
                         environment=None, root=None, is_running=None):
    if (is_running or core.game_is_running)():
        raise save_ledger.SaveLedgerError("Close World of Tanks before editing personal missions.")
    if badges is not None:
        available = {row["id"] for row in badge_catalogue(game_root)}
        if any(type(value) is not int or value not in available for value in badges):
            raise save_ledger.SaveLedgerError("Unknown account badge.")
    path, state, unused, has_garage = _target(slot_id, game_root, environment, root)
    container = state.setdefault("ledger", {}) if has_garage else state
    if badges is not None:
        key = "accountBadges" if has_garage else "initial_account_badges"
        previous = container.get(key, {})
        previous = previous if isinstance(previous, dict) else {}
        container[key] = {str(badge): previous.get(str(badge), int(time.time()))
                          for badge in badges}
        changes = []
        for phase, changed in (('granted', set(container[key]) - set(previous)),
                               ('revoked', set(previous) - set(container[key]))):
            if changed:
                changes.append({'phase': phase, 'rewards': [
                    {'kind': 'badge', 'id': identifier, 'count': 1}
                    for identifier in sorted(changed, key=int)]})
        save_ledger.queue_account_changes(container, changes, has_garage)
        if has_garage:
            services = container.get("offlineServices", {})
            if isinstance(services, dict):
                services["selectedBadges"] = [badge for badge in services.get("selectedBadges", ())
                                              if badge in badges]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_ledger._write_state(path, state)


def _read_badge_catalogue(package):
    """Read the fixed badge resource without invoking vehicle edit rules."""
    member = "scripts/item_defs/badges.xml"
    try:
        with zipfile.ZipFile(package, "r") as archive:
            matches = [info for info in archive.infolist()
                       if info.filename == member]
            if len(matches) != 1:
                raise save_ledger.SaveLedgerError(
                    "The original package must contain exactly one badge catalogue.")
            data = archive.read(matches[0])
        return vehicle_overlays.packed_xml.read_packed_xml(data)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        raise save_ledger.SaveLedgerError(
            "The original badge catalogue is unreadable: %s" % error)


def badge_catalogue(game_root):
    """Read actual badge IDs/names from the installed client's packed data."""
    status, package = vehicle_overlays._require_target(game_root)
    tree = _read_badge_catalogue(package)

    def child(node, name):
        values = [value for key, value in node.children if key == name.encode("ascii")]
        if len(values) != 1:
            raise save_ledger.SaveLedgerError("The badge catalogue is not in the expected format.")
        return values[0].value

    translator = None
    path = os.path.join(status["path"], "res", "text", "LC_MESSAGES", "badge.mo")
    if os.path.isfile(path):
        with open(path, "rb") as stream:
            translator = gettext.GNUTranslations(stream)
    rows, seen = [], set()
    try:
        for name, value in child(tree, "badges").children:
            if name != b"badge":
                continue
            badge = value.value
            fields = {}
            for unused, item in child(badge, "value").children:
                field = item.value
                fields[child(field, "name").decode("utf-8").strip()] = field.value.value
            badge_id = int(fields["id"])
            if badge_id in seen:
                raise ValueError("Duplicate badge ID")
            seen.add(badge_id)
            key = "badge_%d" % badge_id
            label = translator.gettext(key) if translator else key
            if label == key:
                label = child(badge, "name").decode("utf-8").strip()
            rows.append({"id": badge_id, "label": label,
                         "weight": float(fields.get("weight", -1))})
        if not rows:
            raise ValueError("No badges in catalogue")
    except (AttributeError, KeyError, TypeError, ValueError):
        raise save_ledger.SaveLedgerError(
            "The badge catalogue is not in the expected format.")
    return sorted(rows, key=lambda row: (row["weight"], row["id"]))
