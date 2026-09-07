"""Vehicles the launcher bought for a save, waiting for a client to build them.

The launcher can edit a save's balances on its own, because they are plain
numbers in the save's own file.  A vehicle is not: a garage record holds the
exact compact descriptors, crew and ammunition this client produces, and only
a client can produce them.  So the launcher's gold vehicle shop takes the gold
and leaves the vehicle names here, and the first client that starts that save
turns each one into a real garage vehicle.

Nothing here is an audit trail.  The file exists only because two programs
have to hand work to each other, and it is deleted as soon as the work is
done.  A name the client cannot build stays in it and says why in the log,
because a purchase the player already paid for must not vanish silently.
"""

import io
import json
import os

from gui.mods.offline_lan_0922 import config as port_config


INBOX_FILE_NAME = 'launcher_inbox.json'
SCHEMA = 1
# A save with more pending vehicles than this is a damaged or hostile file,
# not a shopping list.
MAX_PENDING_VEHICLES = 512
_NAME_CHARACTERS = set(
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:.')


def inbox_path(slot=port_config.ACTIVE_SAVE_SLOT, user_data_dir=None):
    if slot is port_config.ACTIVE_SAVE_SLOT:
        return port_config.save_slot_state_path(
            INBOX_FILE_NAME, user_data_dir=user_data_dir)
    return os.path.join(
        port_config.save_slot_dir(slot, user_data_dir), INBOX_FILE_NAME)


def _valid_name(value):
    """Accept only what a #1513 vehicle type name can look like.

    The name reaches ``vehicles.VehicleDescr(typeName=...)``, so a value that
    is not a plain ``nation:vehicle`` identifier is rejected here rather than
    handed to the client's parser.
    """
    if not isinstance(value, str) or not 3 <= len(value) <= 64:
        return False
    if value.count(':') != 1:
        return False
    nation, vehicle = value.split(':')
    if not nation or not vehicle:
        return False
    return not set(value) - _NAME_CHARACTERS


def pending_vehicles(path=None):
    """Return the vehicle names waiting to be built, newest last.

    An unreadable or unexpected file yields nothing rather than raising: a
    damaged inbox must not stop a garage from starting.
    """
    if path is None:
        path = inbox_path()
    try:
        if not os.path.isfile(path):
            return []
        with io.open(path, 'r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError, UnicodeError):
        return []
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        return []
    names = []
    for name in (value.get('vehicles') or ())[:MAX_PENDING_VEHICLES]:
        if _valid_name(name) and name not in names:
            names.append(name)
    return names


def keep_pending(names, path=None):
    """Leave only ``names`` waiting, or delete the file when none are left.

    A vehicle this client refused stays pending: the player paid for it in the
    launcher, and a client that cannot build it today is not a reason to throw
    the purchase away.
    """
    if path is None:
        path = inbox_path()
    if not names:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except (IOError, OSError):
            return False
        return True
    port_config.write_json(
        path, {'schema': SCHEMA, 'vehicles': list(names)})
    return True
