"""Own the 0.9.22 client preferences path without touching stock files.

The exact #1513 client reads ``res/engine_config.xml`` through BigWorld's
resource search path.  A complete Packed XML clone in ``res_mods`` can
therefore change only the preferences location while retaining every other
engine setting from the installed client.
"""

from __future__ import annotations

import copy
import os
import re
import sys
import tempfile

try:
    import packed_xml
except ImportError:
    _TOOLS_ROOT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools")
    if _TOOLS_ROOT not in sys.path:
        sys.path.insert(0, _TOOLS_ROOT)
    import packed_xml


TARGET_VERSION = "0.9.22.0.1"
TARGET_BUILD = "1513"
STOCK_ENGINE_CONFIG = "res/engine_config.xml"
OVERLAY_ENGINE_CONFIG = "res_mods/0.9.22.0.1/engine_config.xml"
PROFILE_RELATIVE_PATH = (
    "WoTOfflineBattles/client_profiles/0.9.22/preferences.xml")
PROFILE_PATH_BASE = "LOCAL_APP_DATA"
NORMAL_PROFILE_RELATIVE_PATH = "Wargaming.net/WorldOfTanks/preferences.xml"

_OWNED_PROFILE_PATH = re.compile(
    r"^WoTOfflineBattles/client_profiles/0\.9\.22"
    r"(?:\.[0-9]+)*/preferences\.xml$")


def _launcher_core():
    try:
        from . import core
    except ImportError:
        import core
    return core


def _packed_string(text):
    return packed_xml.PackedValue(
        packed_xml.TYPE_STRING, text.encode("utf-8"))


def _preference_section(path=PROFILE_RELATIVE_PATH):
    return packed_xml.PackedValue(
        packed_xml.TYPE_ELEMENT,
        packed_xml.PackedElement(children=[
            (b"path", _packed_string(path)),
            (b"pathBase", _packed_string(PROFILE_PATH_BASE)),
        ]))


def _preference_child(root):
    matches = [(index, value)
               for index, (name, value) in enumerate(root.children)
               if name == b"preferences"]
    if len(matches) != 1:
        raise ValueError(
            "engine_config.xml must contain exactly one preferences setting")
    return matches[0]


def _read_packed(path, label):
    try:
        with open(path, "rb") as stream:
            data = stream.read()
        return data, packed_xml.read_packed_xml(data)
    except (IOError, OSError, ValueError) as error:
        raise ValueError("%s is unreadable: %s" % (label, error))


def _stock_root(path):
    unused_data, root = _read_packed(path, "The stock engine_config.xml")
    unused_index, preferences = _preference_child(root)
    if (preferences.value_type != packed_xml.TYPE_STRING or
            preferences.value != b"preferences.xml"):
        raise ValueError(
            "The stock preferences setting does not match the exact #1513 "
            "client")
    return root


def _build_overlay(stock_root, profile_path=PROFILE_RELATIVE_PATH):
    result = copy.deepcopy(stock_root)
    index, unused = _preference_child(result)
    result.children[index] = (
        b"preferences", _preference_section(profile_path))
    return result


def _string_child(element, name):
    matches = [value for current, value in element.children
               if current == name]
    if (len(matches) != 1 or
            matches[0].value_type != packed_xml.TYPE_STRING):
        return None
    try:
        return matches[0].value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _owned_overlay_root(overlay_root, stock_root):
    """Return whether an older overlay changes only our preferences path."""
    try:
        index, preferences = _preference_child(overlay_root)
    except ValueError:
        return False
    if preferences.value_type != packed_xml.TYPE_ELEMENT:
        return False
    section = preferences.value
    if (section.value.value_type != packed_xml.TYPE_STRING or
            section.value.value != b"" or len(section.children) != 2):
        return False
    path = _string_child(section, b"path")
    path_base = _string_child(section, b"pathBase")
    if (path is None or _OWNED_PROFILE_PATH.fullmatch(path) is None or
            path_base != PROFILE_PATH_BASE):
        return False

    restored = copy.deepcopy(overlay_root)
    restored.children[index] = copy.deepcopy(
        stock_root.children[_preference_child(stock_root)[0]])
    return (packed_xml.write_packed_xml(restored) ==
            packed_xml.write_packed_xml(stock_root))


def _atomic_write(path, data):
    directory = os.path.dirname(path)
    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".offline-preferences-", dir=directory)
    except (IOError, OSError) as error:
        raise ValueError(
            "The preferences overlay directory is not writable: %s" % error)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (IOError, OSError) as error:
        try:
            os.unlink(temporary)
        except (IOError, OSError):
            pass
        raise ValueError(
            "The preferences overlay could not be committed: %s" % error)


def ensure_preferences_overlay(game_root):
    """Install or refresh the launcher-owned engine-config overlay.

    An unknown existing ``engine_config.xml`` is a hard conflict.  The stock
    file is never changed, and no overlay is written until the complete stock
    Packed XML has been parsed and validated.
    """
    core = _launcher_core()
    status = core.inspect_game_root(game_root)
    if not status.get("has_executable"):
        raise core.LauncherError(
            "Select the folder that contains %s." % core.GAME_EXECUTABLE)
    if (status.get("client") != core.PORT_0_9_22 or
            status.get("version") != TARGET_VERSION or
            str(status.get("build") or "") != TARGET_BUILD):
        raise core.LauncherError(
            "Preferences isolation requires the exact supported 0.9.22 "
            "#1513 client.")

    try:
        stock_path = core._relative_path(status["path"], STOCK_ENGINE_CONFIG)
        overlay_path = core._relative_path(
            status["path"], OVERLAY_ENGINE_CONFIG)
        if os.path.islink(stock_path) or not os.path.isfile(stock_path):
            raise ValueError("The stock engine_config.xml is missing")
        stock_root = _stock_root(stock_path)
        expected_root = _build_overlay(stock_root)
        expected = packed_xml.write_packed_xml(expected_root)

        if os.path.lexists(overlay_path):
            if os.path.islink(overlay_path) or not os.path.isfile(overlay_path):
                raise ValueError(
                    "The existing engine_config.xml overlay is not a regular "
                    "file")
            current, current_root = _read_packed(
                overlay_path, "The existing engine_config.xml overlay")
            if current == expected:
                return (
                    "The isolated 0.9.22 client preferences redirect is "
                    "already up to date.")
            if not _owned_overlay_root(current_root, stock_root):
                raise ValueError(
                    "An engine_config.xml overlay from another tool already "
                    "exists; it was left unchanged")
            action = "Updated the isolated 0.9.22 client preferences redirect."
        else:
            action = "Created the isolated 0.9.22 client preferences redirect."

        _atomic_write(overlay_path, expected)
        return action
    except core.LauncherError:
        raise
    except (IOError, OSError, ValueError) as error:
        raise core.LauncherError(str(error))


def profile_path(environment=None):
    """Return the one launcher-owned preferences file, when resolvable."""
    environment = os.environ if environment is None else environment
    local_app_data = str(environment.get("LOCALAPPDATA") or "").strip()
    if not local_app_data or not os.path.isabs(local_app_data):
        return None
    root = os.path.realpath(os.path.abspath(local_app_data))
    path = os.path.realpath(os.path.abspath(os.path.join(
        root, *PROFILE_RELATIVE_PATH.split("/"))))
    root_key = os.path.normcase(root)
    path_key = os.path.normcase(path)
    try:
        if (os.path.commonpath((root_key, path_key)) != root_key or
                path_key == root_key):
            return None
    except ValueError:
        return None
    return path


def normal_profile_path(environment=None):
    """Return the stock client's shared preferences path, when resolvable."""
    environment = os.environ if environment is None else environment
    app_data = str(environment.get("APPDATA") or "").strip()
    if not app_data or not os.path.isabs(app_data):
        return None
    root = os.path.realpath(os.path.abspath(app_data))
    parts = NORMAL_PROFILE_RELATIVE_PATH.split("/")
    directory = os.path.realpath(os.path.abspath(os.path.join(
        root, *parts[:-1])))
    path = os.path.join(directory, parts[-1])
    root_key = os.path.normcase(root)
    path_key = os.path.normcase(path)
    try:
        if (os.path.commonpath((root_key, path_key)) != root_key or
                path_key == root_key):
            return None
    except ValueError:
        return None
    return path
