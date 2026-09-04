"""Small persistent account state owned by the offline server facade."""

from __future__ import print_function

import json
import os

from gui.mods.offline_lan_0922 import config as port_config


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


STATE_PATH = os.path.join(
    port_config.USER_DATA_DIR, 'account_state.json')
LEGACY_STATE_PATH = os.path.join(
    port_config.LEGACY_USER_DATA_DIR, 'account_state.json')


class AccountState(object):
    """Persist the integer settings that #1513 normally stores server-side."""

    def __init__(self, path=STATE_PATH):
        self._path = (port_config.migrate_legacy_user_file(
            path, LEGACY_STATE_PATH) if path == STATE_PATH else path)
        self._int_user_settings = {}
        self._load()

    def snapshot(self):
        return dict(self._int_user_settings)

    def add_int_settings(self, values):
        values = tuple(values or ())
        if len(values) % 2:
            raise ValueError('integer settings must contain key/value pairs')
        update = {}
        for offset in range(0, len(values), 2):
            key = values[offset]
            value = values[offset + 1]
            if (isinstance(key, bool) or isinstance(value, bool) or
                    not isinstance(key, integer_types) or
                    not isinstance(value, integer_types)):
                raise ValueError('integer settings require integer values')
            update[int(key)] = int(value)
        previous = self.snapshot()
        self._int_user_settings.update(update)
        try:
            self._save()
        except Exception:
            self._int_user_settings = previous
            raise

    def del_int_settings(self, keys):
        previous = self.snapshot()
        try:
            for key in tuple(keys or ()):
                if (isinstance(key, bool) or
                        not isinstance(key, integer_types)):
                    raise ValueError('integer setting keys must be integers')
                self._int_user_settings.pop(int(key), None)
            self._save()
        except Exception:
            self._int_user_settings = previous
            raise

    def _load(self):
        if self._path is None or not os.path.isfile(self._path):
            return
        try:
            with open(self._path, 'rb') as stream:
                value = json.load(stream)
            if not isinstance(value, dict) or value.get('schema') != 1:
                return
            stored = value.get('intUserSettings', {})
            if not isinstance(stored, dict):
                return
            loaded = {}
            for raw_key, raw_value in stored.items():
                if isinstance(raw_value, bool) or not isinstance(
                        raw_value, integer_types):
                    return
                key = int(raw_key)
                loaded[key] = int(raw_value)
            self._int_user_settings = loaded
        except (IOError, OSError, TypeError, ValueError):
            # A stale optional cache must not prevent an offline login.  The
            # next successful settings update rewrites the canonical shape.
            self._int_user_settings = {}

    def _save(self):
        if self._path is None:
            return
        value = {
            'schema': 1,
            'intUserSettings': dict(
                (str(key), setting)
                for key, setting in self._int_user_settings.items()),
        }
        port_config.write_json(self._path, value)
