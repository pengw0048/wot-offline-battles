"""Safe 0.9.22 Packed XML vehicle-data profiles and temporary overlays.

The editor never writes ``scripts.pkg``. Saved profiles contain logical edits
only. Immediately before a profile launch, every edited package member is
rebuilt from the original archive and installed under
``res_mods/0.9.22.0.1``; the launcher removes those owned files after the game
exits. An existing overlay from another tool is always a conflict.
"""

from __future__ import annotations

import copy
import datetime
import errno
import gettext
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import zipfile

try:
    import packed_xml
except ImportError:
    # Source checkouts run the launcher from ``launcher``.  The packaged build
    # adds this same tools directory to PyInstaller's analysis path.
    _TOOLS_ROOT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "0.9.22", "tools")
    if _TOOLS_ROOT not in sys.path:
        sys.path.insert(0, _TOOLS_ROOT)
    import packed_xml

try:
    from . import core
except ImportError:
    import core


TARGET_VERSION = "0.9.22.0.1"
TARGET_BUILD = "1513"
SOURCE_PACKAGE = "res/packages/scripts.pkg"
OVERLAY_ROOT = "res_mods/0.9.22.0.1"
MANIFEST_NAME = "vehicle_overlays.json"
MANIFEST_SCHEMA = 1
PROFILE_STORE_RELATIVE = (
    "mods/configs/offline_lan_0922/vehicle_profiles.json")
PROFILE_STORE_NAME = "vehicle_profiles.json"
PROFILE_STORE_APPDATA_PARTS = (
    "Wargaming.net", "WorldOfTanks", "offline_lan_0922")
PROFILE_STORE_SCHEMA = 1
ORIGINAL_PROFILE_LABEL = "Original vehicle values"
MAX_PROFILE_NAME_LENGTH = 64
MAX_OVERLAY_MEMBERS = 1024
MAX_OVERLAY_MANIFEST_BYTES = 32 * 1024 * 1024

_COMPONENT_MEMBER = re.compile(
    r"^scripts/item_defs/vehicles/([a-z][a-z0-9_]*)/components/"
    r"(chassis|engines|fuelTanks|guns|radios|shells|turrets)\.xml$")
_VEHICLE_MEMBER = re.compile(
    r"^scripts/item_defs/vehicles/([a-z][a-z0-9_]*)/"
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)\.xml$")
_VEHICLE_CATALOG_PREFIX = re.compile(r"^[A-Za-z]{1,2}[0-9]{2,3}_")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NON_EDITABLE_VEHICLE_TAGS = frozenset((
    "event_battles", "premiumIGR", "observer", "unrecoverable"))
_NON_EDITABLE_VEHICLES = frozenset(("usa:T23",))

_TYPE_NAMES = {
    packed_xml.TYPE_STRING: "string",
    packed_xml.TYPE_INTEGER: "integer",
    packed_xml.TYPE_VECTOR: "vector",
    packed_xml.TYPE_BOOLEAN: "boolean",
    packed_xml.TYPE_COMPRESSED_STRING: "compressed-string",
}

_HEALTH_CONTAINERS = {
    "ammoBayHealth",
    "engineHealth",
    "fuelTankHealth",
    "radioHealth",
    "surveyingDeviceHealth",
    "turretRotatorHealth",
}

_CATEGORY_LABELS = {
    "vehicle": "Vehicle",
    "chassis": "Chassis",
    "turret": "Turret",
    "engines": "Engine",
    "fuelTanks": "Fuel tank",
    "guns": "Gun",
    "radios": "Radio",
    "shells": "Shell",
}
_CATEGORY_ORDER = dict(
    (name, index) for index, name in enumerate((
        "vehicle", "chassis", "turret", "engines", "fuelTanks",
        "guns", "radios", "shells")))
_FIELD_LABELS = {
    "speedLimits": "Speed limits",
    "forward": "Forward speed",
    "backward": "Reverse speed",
    "hull": "Hull",
    "ammoBayHealth": "Ammo rack",
    "engineHealth": "Engine health",
    "fuelTankHealth": "Fuel tank health",
    "radioHealth": "Radio health",
    "surveyingDeviceHealth": "Observation device",
    "turretRotatorHealth": "Turret traverse",
    "weight": "Weight",
    "maxLoad": "Load limit",
    "maxHealth": "Maximum health",
    "maxRegenHealth": "Repair threshold",
    "power": "Power",
    "rotationSpeed": "Traverse speed",
    "hullRotationSpeed": "Hull traverse speed (deg/s)",
    "gunElevationSpeed": "Gun elevation speed (deg/s)",
    "turretTraverseSpeed": "Turret traverse speed (deg/s)",
    "terrainResistance": (
        "Ground resistance (hard, medium, soft; lower is better)"),
    "pitchLimits": "Gun elevation limits",
    "minPitch": "Elevation curve",
    "maxPitch": "Depression curve",
    "turretYawLimits": "Horizontal traverse limits",
    "hull_aiming": "Hull aiming",
    "wheelsCorrectionAngles": "Suspension pitch limits",
    "pitchMin": "Minimum pitch",
    "pitchMax": "Maximum pitch",
    "reloadTime": "Reload time",
    "rate": "Magazine firing rate (higher is a shorter reload)",
    "clip": "Magazine",
    "count": "Rounds per magazine",
    "aimingTime": "Aiming time",
    "shotDispersionRadius": "Base accuracy",
    "shotDispersionFactors": "Dispersion factors",
    "vehicleMovement": "Hull movement dispersion",
    "vehicleRotation": "Hull rotation dispersion",
    "turretRotation": "Turret rotation dispersion",
    "afterShot": "Firing dispersion",
    "invisibilityFactorAtShot": "Firing camouflage factor",
    "circularVisionRadius": "View range",
    "invisibility": "Camouflage",
    "moving": "Moving camouflage",
    "still": "Stationary camouflage",
    "maxAmmo": "Ammunition capacity",
    "shots": "Shell",
    "speed": "Projectile speed",
    "maxDistance": "Maximum distance",
    "gravity": "Gravity",
    "piercingPower": "Penetration",
    "caliber": "Caliber",
    "damage": "Damage",
    "armor": "Vehicle damage",
    "devices": "Module damage",
    "explosionRadius": "Explosion radius",
}


class VehicleOverlayError(Exception):
    """A safe, user-correctable editor refusal."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _contained_path(root, path, label):
    """Return one lexical path only when it also resolves below ``root``.

    Checking both forms matters on Windows: a junction in the middle of the
    nominal game path can otherwise redirect an owned overlay outside the
    selected installation without appearing as a symlink at the final file.
    """
    absolute_root = os.path.abspath(root)
    absolute_path = os.path.abspath(path)
    pairs = (
        (os.path.normcase(absolute_root), os.path.normcase(absolute_path)),
        (os.path.normcase(os.path.realpath(absolute_root)),
         os.path.normcase(os.path.realpath(absolute_path))),
    )
    for checked_root, checked_path in pairs:
        try:
            if os.path.commonpath((checked_root, checked_path)) != checked_root:
                raise VehicleOverlayError(
                    "%s escapes its owned root through a path, symlink, or "
                    "junction." % label)
        except ValueError:
            raise VehicleOverlayError(
                "%s escapes its owned root through a path, symlink, or "
                "junction." % label)
    return absolute_path


def _game_owned_path(game_root, path, label):
    return _contained_path(os.path.abspath(game_root), path, label)


def manifest_path(game_root):
    path = os.path.join(
        os.path.abspath(game_root), *OVERLAY_ROOT.split("/"), MANIFEST_NAME)
    return _game_owned_path(game_root, path, "The vehicle overlay manifest")


def legacy_profile_store_path(game_root):
    path = os.path.join(
        os.path.abspath(game_root), *PROFILE_STORE_RELATIVE.split("/"))
    return _game_owned_path(game_root, path, "The legacy vehicle profile store")


def _appdata_profile_root(environment=None):
    environment = os.environ if environment is None else environment
    appdata = environment.get("APPDATA")
    if not isinstance(appdata, str) or not appdata.strip():
        return None
    return os.path.join(
        os.path.abspath(appdata.strip()), *PROFILE_STORE_APPDATA_PARTS)


def profile_store_path(game_root, environment=None):
    root = _appdata_profile_root(environment)
    if root is None:
        return legacy_profile_store_path(game_root)
    return _contained_path(
        root, os.path.join(root, PROFILE_STORE_NAME),
        "The vehicle profile store")


def _normalize_profile_name(raw_name):
    if not isinstance(raw_name, str):
        raise VehicleOverlayError("The profile name must be text.")
    name = raw_name.strip()
    if not name:
        raise VehicleOverlayError("Enter a profile name.")
    if len(name) > MAX_PROFILE_NAME_LENGTH:
        raise VehicleOverlayError(
            "Profile names may contain at most %d characters." %
            MAX_PROFILE_NAME_LENGTH)
    if any(ord(character) < 32 or ord(character) == 127
           for character in name):
        raise VehicleOverlayError(
            "Profile names cannot contain control characters.")
    if name.casefold() == ORIGINAL_PROFILE_LABEL.casefold():
        raise VehicleOverlayError(
            "%s is reserved for unmodified data." % ORIGINAL_PROFILE_LABEL)
    return name


def _validate_member(member):
    if not isinstance(member, str):
        raise VehicleOverlayError("The package member must be text.")
    if (not member or member.startswith("/") or "\\" in member or
            any(part in ("", ".", "..") for part in member.split("/"))):
        raise VehicleOverlayError("The package member path is unsafe.")
    component = _COMPONENT_MEMBER.fullmatch(member)
    if component is not None:
        return ("component", component.group(2))
    if _VEHICLE_MEMBER.fullmatch(member) is not None:
        return ("vehicle", None)
    raise VehicleOverlayError(
        "Only 0.9.22 vehicle definitions and their known component members "
        "can be edited.")


def _field_parts(field_path):
    if not isinstance(field_path, str):
        raise VehicleOverlayError("The field path must be text.")
    parts = field_path.split("/")
    if (not field_path or "\\" in field_path or
            any(not _SAFE_SEGMENT.fullmatch(part) for part in parts)):
        raise VehicleOverlayError("The field path is unsafe.")
    return parts


def _rule(rule_id, description, minimum, inclusive=False,
          integer_required=False):
    relation = ">=" if inclusive else ">"
    return {
        "id": rule_id,
        "description": "%s; finite value %s %s" % (
            description, relation, minimum),
        "minimum": float(minimum),
        "inclusive": bool(inclusive),
        "integerRequired": bool(integer_required),
    }


_POSITIVE = _rule("positive", "stock parser requires a positive number", 0)
_NONNEGATIVE = _rule(
    "nonnegative", "stock parser requires a non-negative number", 0, True)
_MAX_AMMO = _rule(
    "max-ammo", "ammunition capacity must be a non-negative integer",
    0, True, True)
_CLIP_COUNT = _rule(
    "clip-count", "magazine capacity must be a positive integer",
    1, True, True)
_MAX_HEALTH = _rule(
    "max-health", "device maximum health must be at least one", 1, True)
_MAX_REGEN = _rule(
    "max-regen-health",
    "regeneration health must be non-negative and no greater than maxHealth",
    0, True)
_PIERCING_PAIR = {
    "id": "piercing-pair",
    "description": (
        "penetration must contain exactly two positive finite numbers; "
        "the first value must be no less than the second"),
    "arity": 2,
}
_TERRAIN_RESISTANCE = {
    "id": "terrain-resistance",
    "description": (
        "ground resistance must contain exactly three positive finite "
        "numbers in hard / medium / soft order; lower is better"),
    "sequence": "terrain",
}
_PITCH_CURVE = {
    "id": "pitch-curve",
    "description": (
        "angle may be one finite degree value or a complete 0..1 piecewise "
        "curve; every angle must be between -90 and 90 degrees"),
    "sequence": "pitch",
}
_YAW_LIMITS = {
    "id": "yaw-limits",
    "description": (
        "horizontal traverse must contain exactly two finite degree values "
        "between -180 and 180; minimum must not exceed maximum"),
    "sequence": "yaw",
}
_PITCH_ANGLE = {
    "id": "pitch-angle",
    "description": "pitch angle must be finite and between -90 and 90 degrees",
    "bounded": (-90.0, 90.0),
}


def _health_rule(name):
    return _MAX_HEALTH if name == "maxHealth" else _MAX_REGEN


def _gun_value_rule(parts):
    if len(parts) == 1 and parts[-1] == "rotationSpeed":
        return _NONNEGATIVE
    if len(parts) == 1 and parts[-1] in (
            "weight", "reloadTime", "aimingTime", "shotDispersionRadius"):
        return _POSITIVE
    if len(parts) == 1 and parts[-1] == "maxAmmo":
        return _MAX_AMMO
    if (len(parts) == 2 and parts[0] == "pitchLimits" and
            parts[-1] in ("minPitch", "maxPitch")):
        return _PITCH_CURVE
    if len(parts) == 1 and parts[-1] == "turretYawLimits":
        return _YAW_LIMITS
    if (len(parts) == 2 and parts[0] == "shotDispersionFactors" and
            parts[-1] in ("turretRotation", "afterShot")):
        return _NONNEGATIVE
    if len(parts) == 2 and parts == ["clip", "rate"]:
        return _POSITIVE
    if len(parts) == 2 and parts == ["clip", "count"]:
        return _CLIP_COUNT
    if len(parts) == 1 and parts[-1] == "invisibilityFactorAtShot":
        return _NONNEGATIVE
    if (len(parts) == 3 and parts[0] == "shots" and
            parts[-1] in ("speed", "maxDistance")):
        return _POSITIVE
    if (len(parts) == 3 and parts[0] == "shots" and
            parts[-1] == "gravity"):
        return _NONNEGATIVE
    if (len(parts) == 3 and parts[0] == "shots" and
            parts[-1] == "piercingPower"):
        return _PIERCING_PAIR
    return None


def _field_rule(member, field_path):
    member_kind, component_name = _validate_member(member)
    parts = _field_parts(field_path)

    if member_kind == "vehicle":
        if (len(parts) == 2 and parts[0] == "invisibility" and
                parts[-1] in ("moving", "still")):
            return _NONNEGATIVE
        if parts in (["speedLimits", "forward"],
                     ["speedLimits", "backward"]):
            return _POSITIVE
        if parts == ["hull", "maxHealth"]:
            return _MAX_HEALTH
        if parts == ["hull", "weight"]:
            return _POSITIVE
        if (len(parts) == 3 and parts[0] == "hull" and
                parts[1] == "armor"):
            return _NONNEGATIVE
        if (len(parts) == 4 and parts[:3] == [
                "hull_aiming", "pitch", "wheelsCorrectionAngles"] and
                parts[-1] in ("pitchMin", "pitchMax")):
            return _PITCH_ANGLE
        if (len(parts) == 3 and parts[0] == "chassis" and
                parts[2] in ("weight", "maxLoad")):
            return _POSITIVE
        if (len(parts) == 3 and parts[0] == "chassis" and
                parts[-1] == "rotationSpeed"):
            return _POSITIVE
        if (len(parts) == 3 and parts[0] == "chassis" and
                parts[-1] == "terrainResistance"):
            return _TERRAIN_RESISTANCE
        if (len(parts) == 4 and parts[0] == "chassis" and
                parts[2] == "shotDispersionFactors" and
                parts[-1] in ("vehicleMovement", "vehicleRotation")):
            return _NONNEGATIVE
        if (len(parts) == 4 and parts[0] == "chassis" and
                parts[2] == "armor"):
            return _NONNEGATIVE
        if parts in (["hull", "ammoBayHealth", "maxHealth"],
                     ["hull", "ammoBayHealth", "maxRegenHealth"]):
            return _health_rule(parts[-1])
        if (len(parts) == 3 and parts[0] == "chassis" and
                parts[-1] in ("maxHealth", "maxRegenHealth")):
            return _health_rule(parts[-1])
        if (len(parts) == 3 and re.fullmatch(r"turrets\d+", parts[0]) and
                parts[-1] in ("maxHealth", "maxRegenHealth")):
            return _health_rule(parts[-1])
        if (len(parts) == 3 and re.fullmatch(r"turrets\d+", parts[0]) and
                parts[-1] == "weight"):
            return _POSITIVE
        if (len(parts) == 3 and re.fullmatch(r"turrets\d+", parts[0]) and
                parts[-1] in ("circularVisionRadius", "rotationSpeed")):
            return _POSITIVE
        if (len(parts) == 4 and re.fullmatch(r"turrets\d+", parts[0]) and
                parts[2] == "armor"):
            return _NONNEGATIVE
        if (len(parts) == 4 and re.fullmatch(r"turrets\d+", parts[0]) and
                parts[2] in _HEALTH_CONTAINERS and
                parts[-1] in ("maxHealth", "maxRegenHealth")):
            return _health_rule(parts[-1])
        if (len(parts) >= 5 and
                re.fullmatch(r"turrets\d+", parts[0]) and
                parts[2] == "guns"):
            rule = _gun_value_rule(parts[4:])
            if rule is not None:
                return rule
            if len(parts) == 6 and parts[4] == "armor":
                return _NONNEGATIVE

    if member_kind == "component":
        if (component_name == "chassis" and len(parts) == 4 and
                parts[0] == "shared" and
                parts[2] == "shotDispersionFactors" and
                parts[-1] in ("vehicleMovement", "vehicleRotation")):
            return _NONNEGATIVE
        if (component_name == "engines" and len(parts) == 3 and
                parts[0] == "shared" and parts[-1] == "power"):
            return _POSITIVE
        if (component_name == "chassis" and len(parts) == 3 and
                parts[0] == "shared" and parts[-1] == "maxLoad"):
            return _POSITIVE
        if (component_name in (
                "chassis", "engines", "fuelTanks", "guns", "radios",
                "turrets") and len(parts) == 3 and
                parts[0] == "shared" and parts[-1] == "weight"):
            return _POSITIVE
        if component_name == "guns" and parts[:1] == ["shared"]:
            rule = _gun_value_rule(parts[2:])
            if rule is not None:
                return rule
        if (component_name == "turrets" and len(parts) == 3 and
                parts[0] == "shared" and
                parts[-1] in ("circularVisionRadius", "rotationSpeed")):
            return _POSITIVE
        if (component_name in ("chassis", "guns", "turrets") and
                len(parts) == 4 and parts[0] == "shared" and
                parts[2] == "armor"):
            return _NONNEGATIVE
        if component_name == "shells":
            if len(parts) == 2 and parts[-1] == "caliber":
                return _POSITIVE
            if len(parts) == 2 and parts[-1] == "explosionRadius":
                return _POSITIVE
            if (len(parts) == 3 and parts[1] == "damage" and
                    parts[-1] in ("armor", "devices")):
                return _POSITIVE
        if component_name != "shells" and parts[:1] == ["shared"]:
            if (len(parts) == 3 and
                    parts[-1] in ("maxHealth", "maxRegenHealth")):
                return _health_rule(parts[-1])
            if (len(parts) == 4 and parts[2] in _HEALTH_CONTAINERS and
                    parts[-1] in ("maxHealth", "maxRegenHealth")):
                return _health_rule(parts[-1])

    raise VehicleOverlayError(
        "This field is not in the first safe scalar allowlist. IDs, topology, "
        "resource paths, arbitrary vectors, compressed strings, and unknown "
        "fields are never editable.")


def _overlay_path(game_root, member):
    _validate_member(member)
    path = os.path.join(
        os.path.abspath(game_root), *OVERLAY_ROOT.split("/"),
        *member.split("/"))
    return _game_owned_path(game_root, path, "The vehicle data overlay")


def _require_target(game_root, require_closed=False, is_running=None):
    status = core.inspect_game_root(game_root)
    if not status.get("has_executable"):
        raise VehicleOverlayError(
            "Select the folder that contains %s." % core.GAME_EXECUTABLE)
    if status.get("client") != core.PORT_0_9_22:
        raise VehicleOverlayError(
            "Vehicle data editing requires the exact supported 0.9.22 client.")
    package_path = os.path.join(
        status["path"], *SOURCE_PACKAGE.split("/"))
    if not os.path.isfile(package_path):
        raise VehicleOverlayError("The original scripts.pkg is missing.")
    if require_closed:
        checker = core.game_is_running if is_running is None else is_running
        if checker():
            raise VehicleOverlayError(
                "Close World of Tanks before changing vehicle data.")
    return status, package_path


def _read_source_member(package_path, member):
    _validate_member(member)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            matches = [info for info in archive.infolist()
                       if info.filename == member]
            if len(matches) != 1:
                raise VehicleOverlayError(
                    "The original package must contain exactly one %s." % member)
            data = archive.read(matches[0])
        return data, packed_xml.read_packed_xml(data)
    except VehicleOverlayError:
        raise
    except (IOError, OSError, KeyError, ValueError,
            zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original Packed XML member is unreadable: %s" % error)


def _find_value(root, field_path):
    parts = _field_parts(field_path)
    element = root
    for offset, part in enumerate(parts):
        encoded = part.encode("utf-8")
        matches = [(index, value)
                   for index, (name, value) in enumerate(element.children)
                   if name == encoded]
        if len(matches) != 1:
            raise VehicleOverlayError(
                "Field path component %s must exist exactly once." % part)
        unused_index, value = matches[0]
        if offset == len(parts) - 1:
            if value.value_type == packed_xml.TYPE_ELEMENT:
                # Packed XML elements may carry their own scalar in addition
                # to named children. Armor records use this shape for a plate
                # thickness plus attributes such as vehicleDamageFactor.
                return value.value.value
            return value
        if value.value_type != packed_xml.TYPE_ELEMENT:
            raise VehicleOverlayError(
                "Field path component %s is not an element." % part)
        element = value.value
    raise VehicleOverlayError("The field path is empty.")


def _scalar_text(value):
    if value.value_type == packed_xml.TYPE_INTEGER:
        return str(int(value.value))
    if value.value_type == packed_xml.TYPE_STRING:
        try:
            return value.value.decode("ascii").strip()
        except (AttributeError, UnicodeDecodeError):
            raise VehicleOverlayError(
                "The original numeric string is not ASCII text.")
    type_name = _TYPE_NAMES.get(value.value_type, "unknown")
    raise VehicleOverlayError(
        "Packed type %s is not an editable numeric scalar." % type_name)


def _numeric_value(value):
    text = _scalar_text(value)
    try:
        numeric = float(text)
    except (TypeError, ValueError, OverflowError):
        raise VehicleOverlayError(
            "The original field is not a numeric scalar.")
    if not math.isfinite(numeric):
        raise VehicleOverlayError("The original field is not finite.")
    return numeric


def _normalize_piercing_pair(raw_value, label):
    parts = str(raw_value).strip().split()
    if len(parts) != 2:
        raise VehicleOverlayError(
            "%s must contain exactly two numbers." % label)
    numbers = []
    for part in parts:
        try:
            number = float(part)
        except (TypeError, ValueError, OverflowError):
            raise VehicleOverlayError(
                "%s must contain exactly two numbers." % label)
        if not math.isfinite(number) or number <= 0:
            raise VehicleOverlayError(
                "%s values must be positive and finite." % label)
        numbers.append(number)
    if numbers[0] < numbers[1]:
        raise VehicleOverlayError(
            "%s first value must be no less than the second." % label)
    return " ".join(format(number, ".15g") for number in numbers)


def _finite_numbers(raw_value, label):
    parts = str(raw_value).strip().split()
    numbers = []
    for part in parts:
        try:
            number = float(part)
        except (TypeError, ValueError, OverflowError):
            raise VehicleOverlayError("%s must contain finite numbers." % label)
        if not math.isfinite(number):
            raise VehicleOverlayError("%s must contain finite numbers." % label)
        numbers.append(number)
    return numbers


def _normalize_pitch_curve(raw_value, label, scalar_shortcut=True):
    numbers = _finite_numbers(raw_value, label)
    if scalar_shortcut and len(numbers) == 1:
        numbers = [0.0, numbers[0], 1.0, numbers[0]]
    if len(numbers) < 4 or len(numbers) % 2:
        raise VehicleOverlayError(
            "%s must be one angle or complete position/angle pairs." % label)
    positions = numbers[0::2]
    angles = numbers[1::2]
    if (positions[0] != 0.0 or positions[-1] != 1.0 or
            any(position < 0.0 or position > 1.0
                for position in positions) or
            any(positions[index] >= positions[index + 1]
                for index in range(len(positions) - 1))):
        raise VehicleOverlayError(
            "%s curve positions must start at 0, end at 1, and increase." %
            label)
    if any(angle < -90.0 or angle > 90.0 for angle in angles):
        raise VehicleOverlayError(
            "%s angles must be between -90 and 90 degrees." % label)
    return " ".join(format(number, ".15g") for number in numbers)


def _normalize_yaw_limits(raw_value, label):
    numbers = _finite_numbers(raw_value, label)
    if len(numbers) != 2:
        raise VehicleOverlayError(
            "%s must contain exactly two angles." % label)
    if (numbers[0] < -180.0 or numbers[1] > 180.0 or
            numbers[0] > numbers[1]):
        raise VehicleOverlayError(
            "%s must be ordered inside -180..180 degrees." % label)
    return " ".join(format(number, ".15g") for number in numbers)


def _normalize_terrain_resistance(raw_value, label):
    numbers = _finite_numbers(raw_value, label)
    if len(numbers) != 3:
        raise VehicleOverlayError(
            "%s must contain hard, medium, and soft values." % label)
    if any(number <= 0.0 for number in numbers):
        raise VehicleOverlayError(
            "%s values must be positive." % label)
    return " ".join(format(number, ".15g") for number in numbers)


def _validate_original(value, rule):
    if rule.get("arity") == 2:
        if value.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "piercingPower must use the stock Packed string type.")
        _normalize_piercing_pair(_scalar_text(value), "Original penetration")
        return
    if rule.get("sequence") == "pitch":
        if value.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "pitchLimits must use the stock Packed string type.")
        _normalize_pitch_curve(
            _scalar_text(value), "Original gun elevation curve",
            scalar_shortcut=False)
        return
    if rule.get("sequence") == "yaw":
        if value.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "turretYawLimits must use the stock Packed string type.")
        _normalize_yaw_limits(
            _scalar_text(value), "Original horizontal traverse")
        return
    if rule.get("sequence") == "terrain":
        if value.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "terrainResistance must use the stock Packed string type.")
        _normalize_terrain_resistance(
            _scalar_text(value), "Original ground resistance")
        return
    numeric = _numeric_value(value)
    if rule.get("bounded") is not None:
        minimum, maximum = rule["bounded"]
        if numeric < minimum or numeric > maximum:
            raise VehicleOverlayError(rule["description"] + ".")
    if (rule.get("integerRequired") and
            value.value_type != packed_xml.TYPE_INTEGER):
        raise VehicleOverlayError(
            "This field does not use the required Packed integer type.")


def list_vehicle_members(game_root):
    """List source package members that the editor can safely address."""
    unused_status, package_path = _require_target(game_root)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            counts = {}
            for info in archive.infolist():
                name = info.filename
                if (_COMPONENT_MEMBER.fullmatch(name) is not None or
                        _VEHICLE_MEMBER.fullmatch(name) is not None):
                    counts[name] = counts.get(name, 0) + 1
    except (IOError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original scripts.pkg member list is unreadable: %s" % error)
    return sorted(name for name, count in counts.items() if count == 1)


def list_vehicle_choices(game_root):
    """List real vehicle definitions as nation/vehicle choices."""
    status, package_path = _require_target(game_root)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            counts = {}
            for info in archive.infolist():
                counts[info.filename] = counts.get(info.filename, 0) + 1
            roster = _vehicle_roster_from_archive(archive, counts)
    except VehicleOverlayError:
        raise
    except (IOError, OSError, KeyError, TypeError, ValueError,
            zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original vehicle roster is unreadable: %s" % error)
    translators = {}
    choices = []
    for record in roster:
        if not record["selectable"]:
            continue
        nation = record["nation"]
        if nation not in translators:
            translators[nation] = _vehicle_translations(
                status["path"], nation)
        label = _vehicle_label(record, translators[nation])
        choice = dict((key, record[key]) for key in (
            "nation", "vehicle", "member", "tags"))
        choice["label"] = label
        choices.append(choice)
    return choices


def _vehicle_translations(game_root, nation):
    """Load one stock vehicle catalog, falling back to internal names."""
    relative = "res/text/LC_MESSAGES/%s_vehicles.mo" % nation
    try:
        path = _game_owned_path(
            game_root, os.path.join(game_root, *relative.split("/")),
            "The stock vehicle translation catalog")
    except VehicleOverlayError:
        return None
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as stream:
            return gettext.GNUTranslations(stream)
    except (IOError, OSError, EOFError, UnicodeError, ValueError,
            struct.error):
        return None


def _vehicle_label(record, translations):
    """Return one readable label without exposing the catalog prefix.

    ``record['vehicle']`` remains the exact stable resource ID used for every
    package lookup.  Only the label shown by the editor drops prefixes such as
    ``R11_`` or ``J20_``.
    """
    vehicle = record["vehicle"]
    display_id = _vehicle_display_id(vehicle)
    for raw_key in (
            record.get("shortUserString"), record.get("userString")):
        key = raw_key or ""
        if ":" in key:
            key = key.rsplit(":", 1)[1]
        key = key.lstrip("#")
        localized = translations.gettext(key) if translations and key else key
        if localized and localized not in (key, vehicle):
            return localized
    return display_id


def _vehicle_display_id(vehicle):
    """Strip only the nation's numeric catalog prefix for presentation."""
    value = str(vehicle or "")
    display = _VEHICLE_CATALOG_PREFIX.sub("", value, count=1)
    return display or value


def _vehicle_roster_from_archive(archive, counts, nation=None):
    """Resolve vehicle definitions from each nation's stock list.xml."""
    list_members = []
    for member, count in counts.items():
        match = _VEHICLE_MEMBER.fullmatch(member)
        if (match is not None and match.group(2) == "list" and
                (nation is None or match.group(1) == nation)):
            if count != 1:
                raise VehicleOverlayError(
                    "A stock vehicle roster member is repeated.")
            list_members.append((match.group(1), member))
    if nation is not None and not list_members:
        raise VehicleOverlayError(
            "The selected nation's stock vehicle roster is missing.")

    records = []
    for roster_nation, list_member in sorted(list_members):
        root = packed_xml.read_packed_xml(archive.read(list_member))
        seen = set()
        for raw_name, value in root.children:
            # China and Japan retain the stock XML-reference namespace as a
            # scalar Packed XML metadata node.  It is not a vehicle entry.
            if (raw_name == b"xmlns:xmlref" and
                    value.value_type != packed_xml.TYPE_ELEMENT):
                continue
            try:
                vehicle = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                raise VehicleOverlayError(
                    "A stock vehicle roster name is not valid UTF-8.")
            if (vehicle in seen or _SAFE_SEGMENT.fullmatch(vehicle) is None or
                    value.value_type != packed_xml.TYPE_ELEMENT):
                raise VehicleOverlayError(
                    "A stock vehicle roster entry is ambiguous.")
            seen.add(vehicle)
            member = "scripts/item_defs/vehicles/%s/%s.xml" % (
                roster_nation, vehicle)
            if counts.get(member) != 1:
                raise VehicleOverlayError(
                    "A listed vehicle definition is missing or repeated: %s" %
                    member)

            tags = ""
            user_strings = {}
            tag_values = [child for name, child in value.value.children
                          if name == b"tags"]
            if len(tag_values) == 1:
                try:
                    tags = _scalar_text(tag_values[0])
                except VehicleOverlayError:
                    tags = ""
            for field_name in (b"userString", b"shortUserString"):
                values = [child for name, child in value.value.children
                          if name == field_name]
                if len(values) == 1:
                    try:
                        user_strings[field_name.decode("ascii")] = (
                            _scalar_text(values[0]))
                    except VehicleOverlayError:
                        pass
            record = {
                "nation": roster_nation,
                "vehicle": vehicle,
                "member": member,
                "tags": tuple(tags.split()),
                "userString": user_strings.get("userString", ""),
                "shortUserString": user_strings.get("shortUserString", ""),
            }
            # ``secret`` hides vehicles from the retail tech tree, but it is
            # not a data-safety boundary for this local editor. Keep only
            # native construction hazards out of the player catalogue.
            type_name = "%s:%s" % (roster_nation, vehicle)
            record["selectable"] = bool(
                not _NON_EDITABLE_VEHICLE_TAGS.intersection(record["tags"]) and
                type_name not in _NON_EDITABLE_VEHICLES)
            records.append(record)
    return sorted(records, key=lambda record: (
        record["nation"], record["vehicle"], record["member"]))


def _editable_fields_from_root(member, root):
    records = []

    def append_field(field_path, value):
        try:
            rule = _field_rule(member, field_path)
            # Duplicate child names are not addressable without changing
            # topology, even when one of them happens to be allowlisted.
            if _find_value(root, field_path) is not value:
                return
            _validate_original(value, rule)
        except VehicleOverlayError:
            return
        records.append({
            "fieldPath": field_path,
            "originalValue": _scalar_text(value),
            "packedType": _TYPE_NAMES.get(value.value_type, "unknown"),
            "constraint": rule["description"],
        })

    def visit(element, prefix=()):
        for raw_name, value in element.children:
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                continue
            path_parts = prefix + (name,)
            if value.value_type == packed_xml.TYPE_ELEMENT:
                append_field("/".join(path_parts), value.value.value)
                visit(value.value, path_parts)
                continue
            field_path = "/".join(path_parts)
            append_field(field_path, value)

    visit(root)
    return sorted(records, key=lambda record: record["fieldPath"])


def _element_child(element, name):
    encoded = name.encode("utf-8")
    values = [value for current, value in element.children
              if current == encoded]
    if (len(values) != 1 or
            values[0].value_type != packed_xml.TYPE_ELEMENT):
        return None
    return values[0].value


def _shared_component_names(root):
    """Return unambiguous element records from one component shared table."""
    shared = _element_child(root, "shared")
    if shared is None:
        return set()
    entries = {}
    for raw_name, value in shared.children:
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _SAFE_SEGMENT.fullmatch(name) is None:
            continue
        entries.setdefault(name, []).append(value)
    return set(
        name for name, values in entries.items()
        if (len(values) == 1 and
            values[0].value_type == packed_xml.TYPE_ELEMENT))


def _element_leaf_paths(element, prefix=()):
    paths = set()
    for raw_name, value in element.children:
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            continue
        path = prefix + (name,)
        if value.value_type == packed_xml.TYPE_ELEMENT:
            try:
                if _scalar_text(value.value.value):
                    paths.add("/".join(path))
            except VehicleOverlayError:
                pass
            paths.update(_element_leaf_paths(value.value, path))
        else:
            paths.add("/".join(path))
    return paths


def _vehicle_shared_component_occurrences(root, shared_components=None):
    """Return stock shared references and any vehicle-local leaf overrides."""
    shared_components = shared_components or {}
    occurrences = dict((name, {}) for name in (
        "chassis", "engines", "fuelTanks", "radios"))
    for category in occurrences:
        container = _element_child(root, category)
        if container is None:
            continue
        known = shared_components.get(category, set())
        for raw_name, value in container.children:
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if value.value_type == packed_xml.TYPE_ELEMENT:
                if name not in known:
                    continue
                overrides = _element_leaf_paths(value.value)
            else:
                try:
                    if _scalar_text(value) != "shared":
                        continue
                except VehicleOverlayError:
                    continue
                overrides = set()
            occurrences[category].setdefault(name, []).append(overrides)
    return occurrences


def _vehicle_component_references(root, shared_components=None):
    """Read only component references explicitly present in one vehicle."""
    references = dict((name, set()) for name in (
        "chassis", "engines", "fuelTanks", "guns", "radios", "turrets"))
    shared_occurrences = _vehicle_shared_component_occurrences(
        root, shared_components)
    for category, components in shared_occurrences.items():
        references[category].update(components)

    for raw_group, group_value in root.children:
        try:
            group_name = raw_group.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if (re.fullmatch(r"turrets\d+", group_name) is None or
                group_value.value_type != packed_xml.TYPE_ELEMENT):
            continue
        for raw_turret, turret_value in group_value.value.children:
            try:
                turret_name = raw_turret.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if turret_value.value_type != packed_xml.TYPE_ELEMENT:
                try:
                    if _scalar_text(turret_value) == "shared":
                        references["turrets"].add(turret_name)
                except VehicleOverlayError:
                    pass
                continue
            guns = _element_child(turret_value.value, "guns")
            if guns is None:
                continue
            for raw_gun, unused_value in guns.children:
                try:
                    references["guns"].add(raw_gun.decode("utf-8"))
                except UnicodeDecodeError:
                    continue
    return references


def _vehicle_local_gun_overrides(root):
    """Return every gun occurrence and the leaves overriding shared data."""
    occurrences = {}

    for raw_group, group_value in root.children:
        try:
            group_name = raw_group.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if (re.fullmatch(r"turrets\d+", group_name) is None or
                group_value.value_type != packed_xml.TYPE_ELEMENT):
            continue
        for unused_turret_name, turret_value in group_value.value.children:
            if turret_value.value_type != packed_xml.TYPE_ELEMENT:
                continue
            guns = _element_child(turret_value.value, "guns")
            if guns is None:
                continue
            for raw_gun, gun_value in guns.children:
                try:
                    gun_name = raw_gun.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                overrides = set()
                if gun_value.value_type == packed_xml.TYPE_ELEMENT:
                    overrides = _element_leaf_paths(gun_value.value)
                occurrences.setdefault(gun_name, []).append(overrides)
    return occurrences


def _gun_shell_references(guns_root, gun_names):
    shared = _element_child(guns_root, "shared")
    if shared is None:
        return set()
    shells = set()
    for gun_name in gun_names:
        gun = _element_child(shared, gun_name)
        if gun is None:
            continue
        shots = _element_child(gun, "shots")
        if shots is None:
            continue
        for raw_shell, unused_value in shots.children:
            try:
                shells.add(raw_shell.decode("utf-8"))
            except UnicodeDecodeError:
                continue
    return shells


def _component_name(category, field_path):
    parts = _field_parts(field_path)
    if category == "shells":
        return parts[0] if len(parts) >= 2 else None
    if len(parts) >= 3 and parts[0] == "shared":
        return parts[1]
    return None


def _direct_category(field_path):
    parts = _field_parts(field_path)
    first = parts[0]
    if first == "chassis":
        return "chassis"
    if re.fullmatch(r"turrets\d+", first) is not None:
        if len(parts) >= 5 and parts[2] == "guns":
            return "guns"
        return "turret"
    return "vehicle"


def _field_label(category, field_path):
    parts = _field_parts(field_path)
    if category != "shells" and parts[:1] == ["shared"]:
        parts = parts[1:]
    elif (category == "guns" and len(parts) >= 5 and
          re.fullmatch(r"turrets\d+", parts[0]) and parts[2] == "guns"):
        parts = [parts[1], parts[3]] + parts[4:]
    if (category != "shells" and len(parts) >= 2 and
            parts[-2] == "armor"):
        parts = parts[:-2] + ["Armor thickness (%s)" % parts[-1]]
    if category == "chassis" and parts[-1:] == ["rotationSpeed"]:
        parts[-1] = "hullRotationSpeed"
    elif category == "guns" and parts[-1:] == ["rotationSpeed"]:
        parts[-1] = "gunElevationSpeed"
    elif category == "turret" and parts[-1:] == ["rotationSpeed"]:
        parts[-1] = "turretTraverseSpeed"
    return " / ".join(_FIELD_LABELS.get(part, part) for part in parts)


def _choice_record(nation, vehicle, category, member, field, shared,
                   component, affected, mode=None, paired_member=None):
    affected = tuple(sorted(affected))
    display_vehicle = _vehicle_display_id(vehicle)
    affected_labels = tuple(_vehicle_display_id(name) for name in affected)
    if shared:
        scope = ("Shared %s %s; affects %d vehicle%s in %s: %s" % (
            _CATEGORY_LABELS[category].lower(), component, len(affected),
            "" if len(affected) == 1 else "s", nation,
            ", ".join(affected_labels)))
    elif mode == "all":
        scope = (
            "Stored in both the travel-mode and Siege-mode descriptors for "
            "%s; one edit is applied to both." % display_vehicle)
    elif mode == "travel":
        scope = ("Stored in the travel-mode descriptor for %s only." %
                 display_vehicle)
    elif mode == "siege":
        scope = ("Stored in the Siege-mode descriptor for %s only." %
                 display_vehicle)
    else:
        scope = ("Stored in %s only; affects this vehicle only." %
                 display_vehicle)
        if (field["fieldPath"] == "hull/maxHealth" or
                re.fullmatch(
                    r"turrets\d+/[^/]+/maxHealth",
                    field["fieldPath"]) is not None):
            scope += (
                " Effective battle HP is hull maximum health plus the "
                "mounted turret maximum health.")
    field_label = _field_label(category, field["fieldPath"])
    if mode == "travel":
        field_label = "Travel mode / " + field_label
    elif mode == "siege":
        field_label = "Siege mode / " + field_label
    result = dict(field)
    result.update({
        "nation": nation,
        "vehicle": vehicle,
        "displayVehicle": display_vehicle,
        "category": category,
        "categoryLabel": _CATEGORY_LABELS[category],
        "fieldLabel": field_label,
        "member": member,
        "shared": bool(shared),
        "component": component,
        "affectedVehicles": affected,
        "affectedVehicleLabels": affected_labels,
        "scope": scope,
        "mode": mode,
        "pairedMember": paired_member,
    })
    return result


def _siege_peer_member(member):
    """Return the syntactic travel/Siege sibling for one vehicle member."""
    match = _VEHICLE_MEMBER.fullmatch(member)
    if match is None or "/components/" in member:
        return None
    nation, vehicle = match.groups()
    suffix = "_siege_mode"
    if vehicle.endswith(suffix):
        vehicle = vehicle[:-len(suffix)]
    else:
        vehicle += suffix
    if not vehicle:
        return None
    return "scripts/item_defs/vehicles/%s/%s.xml" % (nation, vehicle)


def _same_field_contract(left, right):
    return (
        left.get("fieldPath") == right.get("fieldPath") and
        left.get("originalValue") == right.get("originalValue") and
        left.get("packedType") == right.get("packedType") and
        left.get("constraint") == right.get("constraint"))


def list_vehicle_field_choices(game_root, vehicle_member):
    """Resolve one vehicle to safe fields through its original topology.

    Shared component choices are included only when every affected vehicle can
    be derived from the same nation's original vehicle definitions.
    """
    status, package_path = _require_target(game_root)
    selected_match = _VEHICLE_MEMBER.fullmatch(vehicle_member)
    if selected_match is None or "/components/" in vehicle_member:
        raise VehicleOverlayError("Select one original vehicle definition.")
    nation, vehicle = selected_match.groups()
    component_members = dict(
        (category, "scripts/item_defs/vehicles/%s/components/%s.xml" %
         (nation, category))
        for category in (
            "chassis", "engines", "fuelTanks", "guns", "radios",
            "shells", "turrets"))
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            counts = {}
            for info in archive.infolist():
                counts[info.filename] = counts.get(info.filename, 0) + 1

            roster = _vehicle_roster_from_archive(
                archive, counts, nation=nation)
            selectable = [record for record in roster
                          if record["selectable"]]
            if vehicle_member not in [record["member"]
                                      for record in selectable]:
                raise VehicleOverlayError(
                    "The selected vehicle is not in the stock vehicle roster.")

            roots = {}
            for choice in roster:
                member = choice["member"]
                roots[member] = packed_xml.read_packed_xml(archive.read(member))

            component_roots = {}
            for category, member in component_members.items():
                if counts.get(member) == 1:
                    component_roots[category] = packed_xml.read_packed_xml(
                        archive.read(member))

            shared_components = dict(
                (category, _shared_component_names(root))
                for category, root in component_roots.items())
            references = {}
            local_component_overrides = {}
            local_gun_overrides = {}
            for choice in roster:
                member = choice["member"]
                root = roots[member]
                references[member] = _vehicle_component_references(
                    root, shared_components)
                local_component_overrides[member] = (
                    _vehicle_shared_component_occurrences(
                        root, shared_components))
                local_gun_overrides[member] = (
                    _vehicle_local_gun_overrides(root))

            siege_member = _siege_peer_member(vehicle_member)
            siege_root = None
            if siege_member is not None:
                siege_count = counts.get(siege_member, 0)
                if siege_count > 1:
                    raise VehicleOverlayError(
                        "The paired Siege-mode vehicle definition is "
                        "repeated in scripts.pkg.")
                if siege_count == 1:
                    siege_root = packed_xml.read_packed_xml(
                        archive.read(siege_member))
    except VehicleOverlayError:
        raise
    except (IOError, OSError, KeyError, TypeError, ValueError,
            zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original vehicle topology is unreadable: %s" % error)

    selected_refs = references[vehicle_member]
    all_guns = set()
    for value in references.values():
        all_guns.update(value["guns"])
    gun_shells = {}
    guns_root = component_roots.get("guns")
    if guns_root is not None:
        for gun_name in all_guns:
            gun_shells[gun_name] = _gun_shell_references(
                guns_root, (gun_name,))

    affected = {}
    for choice in roster:
        member = choice["member"]
        vehicle_name = choice["vehicle"]
        for category, names in references[member].items():
            for component in names:
                affected.setdefault(
                    (category, component), set()).add(vehicle_name)
        shells = set()
        for gun_name in references[member]["guns"]:
            shells.update(gun_shells.get(gun_name, ()))
        for shell_name in shells:
            affected.setdefault(
                ("shells", shell_name), set()).add(vehicle_name)

    records = []
    direct_fields = _editable_fields_from_root(
        vehicle_member, roots[vehicle_member])
    if siege_root is None:
        for field in direct_fields:
            category = _direct_category(field["fieldPath"])
            records.append(_choice_record(
                nation, vehicle, category, vehicle_member, field, False,
                vehicle, (vehicle,)))
    else:
        siege_fields = _editable_fields_from_root(siege_member, siege_root)
        direct_by_path = dict(
            (field["fieldPath"], field) for field in direct_fields)
        siege_by_path = dict(
            (field["fieldPath"], field) for field in siege_fields)
        all_paths = sorted(set(direct_by_path) | set(siege_by_path))
        for field_path in all_paths:
            travel_field = direct_by_path.get(field_path)
            siege_field = siege_by_path.get(field_path)
            if (travel_field is not None and siege_field is not None and
                    _same_field_contract(travel_field, siege_field)):
                category = _direct_category(field_path)
                records.append(_choice_record(
                    nation, vehicle, category, vehicle_member, travel_field,
                    False, vehicle, (vehicle,), mode="all",
                    paired_member=siege_member))
                continue
            if travel_field is not None:
                category = _direct_category(field_path)
                records.append(_choice_record(
                    nation, vehicle, category, vehicle_member, travel_field,
                    False, vehicle, (vehicle,), mode="travel"))
            if siege_field is not None:
                category = _direct_category(field_path)
                records.append(_choice_record(
                    nation, vehicle, category, siege_member, siege_field,
                    False, vehicle, (vehicle,), mode="siege"))

    selected_components = dict(
        (category, set(names)) for category, names in selected_refs.items())
    selected_shells = set()
    for gun_name in selected_refs["guns"]:
        selected_shells.update(gun_shells.get(gun_name, ()))
    selected_components["shells"] = selected_shells

    for category, components in selected_components.items():
        if not components:
            continue
        member = component_members.get(category)
        root = component_roots.get(category)
        if member is None or root is None:
            raise VehicleOverlayError(
                "The shared %s topology for %s cannot be resolved safely." %
                (_CATEGORY_LABELS.get(category, category), vehicle))
        for field in _editable_fields_from_root(member, root):
            component = _component_name(category, field["fieldPath"])
            if component not in components:
                continue
            users = affected.get((category, component), set())
            if category in local_component_overrides[vehicle_member]:
                shared_parts = _field_parts(field["fieldPath"])
                shared_suffix = "/".join(shared_parts[2:])
                users = set()
                for choice in roster:
                    occurrences = local_component_overrides[
                        choice["member"]][category].get(component, ())
                    if any(shared_suffix not in overrides
                           for overrides in occurrences):
                        users.add(choice["vehicle"])
            elif category == "guns":
                shared_parts = _field_parts(field["fieldPath"])
                shared_suffix = "/".join(shared_parts[2:])
                users = set()
                for choice in roster:
                    occurrences = local_gun_overrides[
                        choice["member"]].get(component, ())
                    if any(shared_suffix not in overrides
                           for overrides in occurrences):
                        users.add(choice["vehicle"])
            if vehicle not in users:
                # Every occurrence on this vehicle has a local leaf which the
                # stock reader prefers over this shared value.
                continue
            records.append(_choice_record(
                nation, vehicle, category, member, field, True,
                component, users))

    return sorted(records, key=lambda record: (
        _CATEGORY_ORDER[record["category"]], record["fieldLabel"],
        record["member"], record["fieldPath"]))


def list_editable_fields(game_root, member):
    """List existing allowlisted fields and their original contracts."""
    unused_status, package_path = _require_target(game_root)
    unused_data, root = _read_source_member(package_path, member)
    return _editable_fields_from_root(member, root)


def _manifest_scalar(value):
    if value.value_type == packed_xml.TYPE_INTEGER:
        return int(value.value)
    return _scalar_text(value)


def _parse_replacement(raw_value, original, rule):
    if original.value_type not in (
            packed_xml.TYPE_STRING, packed_xml.TYPE_INTEGER):
        raise VehicleOverlayError(
            "Only existing string or integer numeric scalars can be edited.")
    text = str(raw_value).strip()
    if not text:
        raise VehicleOverlayError("Enter a replacement value.")

    if rule.get("arity") == 2:
        if original.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "piercingPower must preserve the stock Packed string type.")
        manifest_value = _normalize_piercing_pair(
            text, "Replacement penetration")
        return (packed_xml.PackedValue(
            packed_xml.TYPE_STRING, manifest_value.encode("ascii")),
                manifest_value)

    if rule.get("sequence") in ("pitch", "yaw", "terrain"):
        if original.value_type != packed_xml.TYPE_STRING:
            raise VehicleOverlayError(
                "This numeric sequence must preserve the stock Packed string "
                "type.")
        if rule["sequence"] == "pitch":
            manifest_value = _normalize_pitch_curve(
                text, "Replacement gun elevation curve")
        elif rule["sequence"] == "yaw":
            manifest_value = _normalize_yaw_limits(
                text, "Replacement horizontal traverse")
        else:
            manifest_value = _normalize_terrain_resistance(
                text, "Replacement ground resistance")
        return (packed_xml.PackedValue(
            packed_xml.TYPE_STRING, manifest_value.encode("ascii")),
                manifest_value)

    if (original.value_type == packed_xml.TYPE_INTEGER and
            rule.get("integerRequired")):
        try:
            value = int(text)
        except (TypeError, ValueError, OverflowError):
            raise VehicleOverlayError(
                "This field uses the Packed integer type; enter a whole number.")
        if value < -(1 << 63) or value > (1 << 63) - 1:
            raise VehicleOverlayError(
                "Packed integers must fit signed 64-bit storage.")
        numeric = float(value)
        replacement = packed_xml.PackedValue(
            packed_xml.TYPE_INTEGER, value)
        manifest_value = value
    else:
        if (original.value_type != packed_xml.TYPE_INTEGER and
                rule.get("integerRequired")):
            raise VehicleOverlayError(
                "This field must use the stock Packed integer type.")
        try:
            numeric = float(text)
        except (TypeError, ValueError, OverflowError):
            raise VehicleOverlayError("Enter one numeric scalar.")
        if not math.isfinite(numeric):
            raise VehicleOverlayError("The replacement must be finite.")
        if (original.value_type == packed_xml.TYPE_INTEGER and
                numeric.is_integer() and
                -(1 << 63) <= numeric <= (1 << 63) - 1):
            manifest_value = int(numeric)
            replacement = packed_xml.PackedValue(
                packed_xml.TYPE_INTEGER, manifest_value)
        else:
            manifest_value = format(numeric, ".15g")
            replacement = packed_xml.PackedValue(
                packed_xml.TYPE_STRING, manifest_value.encode("ascii"))

    if not math.isfinite(numeric):
        raise VehicleOverlayError("The replacement must be finite.")
    if rule.get("bounded") is not None:
        minimum, maximum = rule["bounded"]
        if numeric < minimum or numeric > maximum:
            raise VehicleOverlayError(rule["description"] + ".")
        return replacement, manifest_value
    minimum = rule["minimum"]
    accepted = (numeric >= minimum if rule["inclusive"]
                else numeric > minimum)
    if not accepted:
        raise VehicleOverlayError(rule["description"] + ".")
    return replacement, manifest_value


def _empty_manifest():
    timestamp = _now()
    return {
        "schema": MANIFEST_SCHEMA,
        "targetVersion": TARGET_VERSION,
        "targetBuild": TARGET_BUILD,
        "sourcePackage": SOURCE_PACKAGE,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "members": [],
    }


def _validate_manifest(value):
    if not isinstance(value, dict):
        raise VehicleOverlayError("vehicle_overlays.json must be an object.")
    if (value.get("schema") != MANIFEST_SCHEMA or
            value.get("sourcePackage") != SOURCE_PACKAGE):
        raise VehicleOverlayError(
            "vehicle_overlays.json does not belong to this editor.")
    members = value.get("members")
    if not isinstance(members, list):
        raise VehicleOverlayError("The overlay manifest member list is invalid.")
    if len(members) > MAX_OVERLAY_MEMBERS:
        raise VehicleOverlayError(
            "The overlay manifest contains more than %d members." %
            MAX_OVERLAY_MEMBERS)
    seen_members = set()
    for entry in members:
        if not isinstance(entry, dict):
            raise VehicleOverlayError("An overlay manifest member is invalid.")
        member = entry.get("sourceMember")
        _validate_member(member)
        if member in seen_members:
            raise VehicleOverlayError("The overlay manifest repeats a member.")
        seen_members.add(member)
        if (entry.get("sourcePackage") != SOURCE_PACKAGE or
                entry.get("overlayRelativePath") != member or
                not _DIGEST.fullmatch(str(entry.get("overlaySha256", "")))):
            raise VehicleOverlayError(
                "The overlay manifest ownership record is invalid.")
        edits = entry.get("edits")
        if not isinstance(edits, list) or not edits:
            raise VehicleOverlayError("An owned member has no logical edits.")
        seen_fields = set()
        for edit in edits:
            if not isinstance(edit, dict):
                raise VehicleOverlayError("A manifest edit is invalid.")
            field_path = edit.get("fieldPath")
            _field_rule(member, field_path)
            if field_path in seen_fields:
                raise VehicleOverlayError("A manifest repeats one field edit.")
            seen_fields.add(field_path)
            if edit.get("originalPackedType") not in (
                    "integer", "string"):
                raise VehicleOverlayError(
                    "A manifest edit has an unsupported Packed type.")
            if "originalValue" not in edit or "replacementValue" not in edit:
                raise VehicleOverlayError(
                    "A manifest edit is missing its values.")
            if edit["originalPackedType"] == "integer":
                if (not isinstance(edit["originalValue"], int) or
                        isinstance(edit["originalValue"], bool)):
                    raise VehicleOverlayError(
                        "A Packed integer manifest edit has an invalid "
                        "original value.")
                replacement = edit["replacementValue"]
                rule = _field_rule(member, field_path)
                if rule.get("integerRequired"):
                    valid_replacement = (
                        isinstance(replacement, int) and
                        not isinstance(replacement, bool))
                else:
                    valid_replacement = (
                        isinstance(replacement, int) and
                        not isinstance(replacement, bool))
                    if isinstance(replacement, str):
                        try:
                            valid_replacement = math.isfinite(
                                float(replacement))
                        except (TypeError, ValueError, OverflowError):
                            valid_replacement = False
                if not valid_replacement:
                    raise VehicleOverlayError(
                        "A Packed integer manifest edit has an invalid "
                        "replacement value.")
            elif (not isinstance(edit["originalValue"], str) or
                  not isinstance(edit["replacementValue"], str)):
                raise VehicleOverlayError(
                    "A Packed string manifest edit must keep string values.")
    if len(_manifest_bytes(value)) > MAX_OVERLAY_MANIFEST_BYTES:
        raise VehicleOverlayError(
            "The overlay manifest is larger than %d MiB." %
            (MAX_OVERLAY_MANIFEST_BYTES // (1024 * 1024)))
    return value


def _load_manifest(game_root):
    path = manifest_path(game_root)
    if not os.path.lexists(path):
        return _empty_manifest(), False
    if os.path.islink(path) or not os.path.isfile(path):
        raise VehicleOverlayError(
            "vehicle_overlays.json is not a regular file.")
    try:
        with open(path, "rb") as stream:
            value = json.load(stream)
    except (IOError, OSError, TypeError, ValueError) as error:
        raise VehicleOverlayError(
            "vehicle_overlays.json is unreadable: %s" % error)
    return _validate_manifest(value), True


def _entry_map(manifest):
    return dict((entry["sourceMember"], entry)
                for entry in manifest["members"])


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read_file(path):
    with open(path, "rb") as stream:
        return stream.read()


def _ownership_problem(game_root, member, entry):
    path = _overlay_path(game_root, member)
    if not os.path.lexists(path):
        return ("Owned overlay is missing; Apply will rebuild it."
                if entry is not None else "")
    if os.path.islink(path) or not os.path.isfile(path):
        return "Conflict: the overlay target is not a regular file."
    if entry is None:
        return (
            "Conflict: this complete package member already exists in "
            "res_mods but is not owned by vehicle_overlays.json.")
    # A structurally valid manifest is the ownership proof. The overlay is a
    # disposable materialization of logical edits and is rebuilt from the
    # original package on Apply; content drift is therefore self-healing.
    return ""


def _assert_owned_files_safe(game_root, old_entries, new_members=()):
    for member, entry in old_entries.items():
        problem = _ownership_problem(game_root, member, entry)
        if problem and not problem.startswith("Owned overlay is missing"):
            raise VehicleOverlayError(problem)
    for member in new_members:
        if member in old_entries:
            continue
        problem = _ownership_problem(game_root, member, None)
        if problem:
            raise VehicleOverlayError(problem)


def _same_recorded_value(recorded, current, value_type):
    if value_type == packed_xml.TYPE_INTEGER:
        return (isinstance(recorded, int) and not isinstance(recorded, bool)
                and recorded == int(current))
    return isinstance(recorded, str) and recorded == str(current)


def _compare_trees(original, rebuilt, edited_paths, prefix=()):
    if (prefix not in edited_paths and
            original.value.value_type != rebuilt.value.value_type):
        raise VehicleOverlayError("Packed XML root type changed during rebuild.")
    if prefix not in edited_paths and original.value.value != rebuilt.value.value:
        raise VehicleOverlayError("An unedited Packed XML value changed.")
    if len(original.children) != len(rebuilt.children):
        raise VehicleOverlayError("Packed XML topology changed during rebuild.")
    for (old_name, old_value), (new_name, new_value) in zip(
            original.children, rebuilt.children):
        if old_name != new_name:
            raise VehicleOverlayError(
                "Packed XML names changed during rebuild.")
        child_path = prefix + (old_name.decode("utf-8"),)
        if (old_value.value_type != new_value.value_type and
                child_path not in edited_paths):
            raise VehicleOverlayError(
                "An unedited Packed XML type changed during rebuild.")
        if old_value.value_type == packed_xml.TYPE_ELEMENT:
            if new_value.value_type != packed_xml.TYPE_ELEMENT:
                raise VehicleOverlayError(
                    "Packed XML topology changed during rebuild.")
            _compare_trees(
                old_value.value, new_value.value, edited_paths, child_path)
        elif (child_path not in edited_paths and
              old_value.value != new_value.value):
            raise VehicleOverlayError(
                "An unedited Packed XML scalar changed during rebuild.")


def _validate_health_relations(member, element, prefix=()):
    direct = {}
    for name, value in element.children:
        decoded = name.decode("utf-8")
        direct.setdefault(decoded, []).append(value)
    if (len(direct.get("maxHealth", ())) == 1 and
            len(direct.get("maxRegenHealth", ())) == 1):
        max_path = "/".join(prefix + ("maxHealth",))
        regen_path = "/".join(prefix + ("maxRegenHealth",))
        try:
            _field_rule(member, max_path)
            _field_rule(member, regen_path)
        except VehicleOverlayError:
            pass
        else:
            maximum = _numeric_value(direct["maxHealth"][0])
            regeneration = _numeric_value(direct["maxRegenHealth"][0])
            if maximum < 1 or regeneration < 0 or regeneration > maximum:
                raise VehicleOverlayError(
                    "%s must be between zero and maxHealth (%s)." %
                    (regen_path, _scalar_text(direct["maxHealth"][0])))
    for name, value in element.children:
        if value.value_type == packed_xml.TYPE_ELEMENT:
            _validate_health_relations(
                member, value.value, prefix + (name.decode("utf-8"),))


def _validate_clip_relations(element, prefix=()):
    """Keep #1513 burst size within the installed magazine capacity."""
    direct = {}
    for name, value in element.children:
        direct.setdefault(name.decode("utf-8"), []).append(value)
    clip = direct.get("clip", ())
    burst = direct.get("burst", ())
    if (len(clip) == 1 and len(burst) == 1 and
            clip[0].value_type == packed_xml.TYPE_ELEMENT and
            burst[0].value_type == packed_xml.TYPE_ELEMENT):
        values = {}
        for container_name, container in (("clip", clip[0].value),
                                           ("burst", burst[0].value)):
            rows = {}
            for name, value in container.children:
                rows.setdefault(name.decode("utf-8"), []).append(value)
            if len(rows.get("count", ())) == 1:
                values[container_name] = _numeric_value(rows["count"][0])
        if ("clip" in values and "burst" in values and
                values["clip"] < values["burst"]):
            raise VehicleOverlayError(
                "%s clip/count must be at least burst/count (%s)." % (
                    "/".join(prefix), format(values["burst"], ".15g")))
    for name, value in element.children:
        if value.value_type == packed_xml.TYPE_ELEMENT:
            _validate_clip_relations(
                value.value, prefix + (name.decode("utf-8"),))


def _pitch_curve_points(value, label):
    normalized = _normalize_pitch_curve(
        _scalar_text(value), label, scalar_shortcut=False)
    numbers = [float(part) for part in normalized.split()]
    return tuple(zip(numbers[0::2], numbers[1::2]))


def _curve_at(points, position):
    for index in range(len(points) - 1):
        left = points[index]
        right = points[index + 1]
        if position <= right[0]:
            span = right[0] - left[0]
            ratio = 0.0 if span <= 0.0 else (position - left[0]) / span
            return left[1] + (right[1] - left[1]) * ratio
    return points[-1][1]


def _validate_angle_relations(element, prefix=()):
    """Reject inverted gun/suspension limits after all edits are applied."""
    direct = {}
    for name, value in element.children:
        direct.setdefault(name.decode("utf-8"), []).append(value)

    pitch_limits = direct.get("pitchLimits", ())
    if (len(pitch_limits) == 1 and
            pitch_limits[0].value_type == packed_xml.TYPE_ELEMENT):
        pitch = pitch_limits[0].value
        values = {}
        for name, value in pitch.children:
            values.setdefault(name.decode("utf-8"), []).append(value)
        if (len(values.get("minPitch", ())) == 1 and
                len(values.get("maxPitch", ())) == 1):
            try:
                minimum = _pitch_curve_points(
                    values["minPitch"][0], "Gun elevation curve")
                maximum = _pitch_curve_points(
                    values["maxPitch"][0], "Gun depression curve")
            except VehicleOverlayError:
                # A few stock local overrides are deliberately empty and
                # inherit the shared gun curve. They are never offered by the
                # editor, so leave that original inheritance contract intact.
                minimum = maximum = None
            if minimum is not None:
                positions = sorted(set(
                    [point[0] for point in minimum] +
                    [point[0] for point in maximum]))
                if any(_curve_at(minimum, position) >
                       _curve_at(maximum, position) + 1e-9
                       for position in positions):
                    raise VehicleOverlayError(
                        "%s minimum pitch must not exceed maximum pitch." %
                        "/".join(prefix + ("pitchLimits",)))

    correction = direct.get("wheelsCorrectionAngles", ())
    if (len(correction) == 1 and
            correction[0].value_type == packed_xml.TYPE_ELEMENT):
        values = {}
        for name, value in correction[0].value.children:
            values.setdefault(name.decode("utf-8"), []).append(value)
        if (len(values.get("pitchMin", ())) == 1 and
                len(values.get("pitchMax", ())) == 1 and
                _numeric_value(values["pitchMin"][0]) >
                _numeric_value(values["pitchMax"][0])):
            raise VehicleOverlayError(
                "%s suspension minimum pitch must not exceed maximum pitch." %
                "/".join(prefix + ("wheelsCorrectionAngles",)))

    for name, value in element.children:
        if value.value_type == packed_xml.TYPE_ELEMENT:
            _validate_angle_relations(
                value.value, prefix + (name.decode("utf-8"),))


def _build_member(package_path, entry):
    member = entry["sourceMember"]
    unused_source, original_root = _read_source_member(package_path, member)
    rebuilt_root = copy.deepcopy(original_root)
    edited_paths = set()
    expected_values = {}
    normalized_edits = []
    for edit in sorted(entry["edits"], key=lambda item: item["fieldPath"]):
        field_path = edit["fieldPath"]
        rule = _field_rule(member, field_path)
        original = _find_value(original_root, field_path)
        target = _find_value(rebuilt_root, field_path)
        type_name = _TYPE_NAMES.get(original.value_type, "unknown")
        if edit["originalPackedType"] != type_name:
            raise VehicleOverlayError(
                "The original Packed type changed for %s." % field_path)
        original_value = _manifest_scalar(original)
        if not _same_recorded_value(
                edit["originalValue"], original_value, original.value_type):
            raise VehicleOverlayError(
                "The original scripts.pkg value changed for %s." % field_path)
        replacement, manifest_value = _parse_replacement(
            edit["replacementValue"], original, rule)
        target.value_type = replacement.value_type
        target.value = replacement.value
        edited_paths.add(tuple(_field_parts(field_path)))
        expected_values[field_path] = (
            replacement.value_type, replacement.value)
        normalized = dict(edit)
        normalized["replacementValue"] = manifest_value
        normalized["constraint"] = rule["description"]
        normalized_edits.append(normalized)

    _validate_health_relations(member, rebuilt_root)
    _validate_clip_relations(rebuilt_root)
    _validate_angle_relations(rebuilt_root)
    try:
        output = packed_xml.write_packed_xml(rebuilt_root)
        reparsed = packed_xml.read_packed_xml(output)
    except (TypeError, ValueError, OverflowError) as error:
        raise VehicleOverlayError(
            "The rebuilt Packed XML failed validation: %s" % error)
    _compare_trees(original_root, reparsed, edited_paths)
    for edit in normalized_edits:
        value = _find_value(reparsed, edit["fieldPath"])
        expected_type, expected_value = expected_values[edit["fieldPath"]]
        if (value.value_type != expected_type or
                value.value != expected_value):
            raise VehicleOverlayError(
                "The rebuilt value did not round-trip for %s." %
                edit["fieldPath"])
    return output, normalized_edits


def _read_optional_source_root(package_path, member):
    """Read one derived sibling only when it exists exactly once."""
    _validate_member(member)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            matches = [info for info in archive.infolist()
                       if info.filename == member]
            if not matches:
                return None
            if len(matches) != 1:
                raise VehicleOverlayError(
                    "The original package must contain at most one %s." %
                    member)
            return packed_xml.read_packed_xml(archive.read(matches[0]))
    except VehicleOverlayError:
        raise
    except (IOError, OSError, KeyError, TypeError, ValueError,
            zipfile.BadZipFile) as error:
        raise VehicleOverlayError(
            "The original paired vehicle definition is unreadable: %s" %
            error)


def _equal_mode_peer(package_path, member, field_path):
    """Return a travel/Siege peer only for one identical scalar contract."""
    peer = _siege_peer_member(member)
    if peer is None:
        return None
    peer_root = _read_optional_source_root(package_path, peer)
    if peer_root is None:
        return None
    unused_data, source_root = _read_source_member(package_path, member)
    try:
        source_rule = _field_rule(member, field_path)
        peer_rule = _field_rule(peer, field_path)
        source = _find_value(source_root, field_path)
        paired = _find_value(peer_root, field_path)
        _validate_original(source, source_rule)
        _validate_original(paired, peer_rule)
    except VehicleOverlayError:
        return None
    if (source.value_type != paired.value_type or
            source_rule != peer_rule or
            not _same_recorded_value(
                _manifest_scalar(source), _manifest_scalar(paired),
                source.value_type)):
        return None
    return peer


def _merge_saved_edit(entries, package_path, member, field_path,
                      replacement_value):
    """Merge one validated logical scalar edit into an entry dictionary."""
    rule = _field_rule(member, field_path)
    unused_data, source_root = _read_source_member(package_path, member)
    original = _find_value(source_root, field_path)
    _validate_original(original, rule)
    unused_replacement, manifest_value = _parse_replacement(
        replacement_value, original, rule)
    entry = entries.get(member)
    if entry is None:
        entry = {
            "sourcePackage": SOURCE_PACKAGE,
            "sourceMember": member,
            "overlayRelativePath": member,
            "overlaySha256": "0" * 64,
            "edits": [],
        }
        entries[member] = entry
    edits = dict((edit["fieldPath"], edit) for edit in entry["edits"])
    existing = edits.get(field_path)
    original_type = _TYPE_NAMES.get(original.value_type, "unknown")
    original_value = _manifest_scalar(original)
    if existing is not None:
        if (existing.get("originalPackedType") != original_type or
                not _same_recorded_value(
                    existing.get("originalValue"), original_value,
                    original.value_type)):
            raise VehicleOverlayError(
                "The original package contract changed for this saved edit.")
    edits[field_path] = {
        "fieldPath": field_path,
        "originalPackedType": original_type,
        "originalValue": original_value,
        "replacementValue": manifest_value,
        "constraint": rule["description"],
    }
    entry["edits"] = sorted(
        edits.values(), key=lambda item: item["fieldPath"])
    return manifest_value


def _expand_equal_mode_edits(entries, package_path):
    """Keep stock-identical travel/Siege scalar leaves synchronized."""
    pending = []
    snapshot = copy.deepcopy(entries)
    for member in sorted(snapshot):
        for edit in snapshot[member]["edits"]:
            field_path = edit["fieldPath"]
            peer = _equal_mode_peer(package_path, member, field_path)
            if peer is None:
                continue
            peer_entry = snapshot.get(peer, {})
            peer_edits = dict(
                (item["fieldPath"], item)
                for item in peer_entry.get("edits", ()))
            existing = peer_edits.get(field_path)
            if (existing is not None and
                    existing.get("replacementValue") !=
                    edit.get("replacementValue")):
                raise VehicleOverlayError(
                    "Travel-mode and Siege-mode copies of %s have "
                    "conflicting saved values." % field_path)
            if existing is None:
                pending.append((
                    peer, field_path, edit["replacementValue"]))
    for member, field_path, replacement in pending:
        _merge_saved_edit(
            entries, package_path, member, field_path, replacement)
    return entries


def _write_staged(path, data):
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as stream:
        stream.write(data)
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except (AttributeError, OSError):
            pass


def _open_exclusive(game_root, path):
    path = _game_owned_path(
        game_root, path, "The vehicle overlay transaction target")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    try:
        return os.open(path, flags, 0o666)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise VehicleOverlayError(
                "A vehicle-data override appeared after the stock-data "
                "check; it was left unchanged: %s" % path)
        raise


def _retire_transaction_root(game_root, transaction_root):
    """Move completed recovery data out of the live prefix before deletion."""
    transaction_root = _game_owned_path(
        game_root, transaction_root, "The vehicle recovery directory")
    cleanup_root = None
    try:
        cleanup_root = tempfile.mkdtemp(
            prefix=".wot-vehicle-cleanup-",
            dir=os.path.dirname(transaction_root))
        cleanup_root = _game_owned_path(
            game_root, cleanup_root, "The vehicle recovery cleanup directory")
        os.rmdir(cleanup_root)
        _game_owned_path(
            game_root, transaction_root, "The vehicle recovery directory")
        os.replace(transaction_root, cleanup_root)
    except (IOError, OSError):
        if cleanup_root is not None and os.path.isdir(cleanup_root):
            try:
                os.rmdir(cleanup_root)
            except (IOError, OSError):
                pass
        return False
    shutil.rmtree(cleanup_root, ignore_errors=True)
    return True


def _transaction_target(game_root, target):
    target = _game_owned_path(
        game_root, target, "The vehicle overlay transaction target")
    relative = os.path.relpath(target, game_root).replace(os.sep, "/")
    _recovery_target_kind(relative)
    return target


def _recovery_bytes(game_root, operation, targets):
    records = []
    for index, target in enumerate(targets):
        target = _transaction_target(game_root, target)
        records.append({
            "backup": "backup-%d" % index,
            "hadTarget": bool(os.path.lexists(target)),
            "target": os.path.relpath(target, game_root).replace(os.sep, "/"),
        })
    return (json.dumps({
        "operation": operation,
        "targets": records,
    }, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _transactional_write(game_root, writes, expected_absent=()):
    game_root = os.path.abspath(game_root)
    writes = [(_transaction_target(game_root, target), data)
              for target, data in writes]
    expected_absent = set(
        _transaction_target(game_root, path) for path in expected_absent)
    write_targets = set(target for target, unused_data in writes)
    if len(write_targets) != len(writes):
        raise VehicleOverlayError(
            "A vehicle overlay transaction repeats one target.")
    if not expected_absent.issubset(write_targets):
        raise VehicleOverlayError(
            "An expected-absent transaction target is not being written.")
    try:
        transaction_root = tempfile.mkdtemp(
            prefix=".wot-vehicle-overlay-", dir=game_root)
        transaction_root = _game_owned_path(
            game_root, transaction_root, "The vehicle recovery directory")
    except (IOError, OSError) as error:
        raise VehicleOverlayError(
            "The overlay transaction could not start: %s" % error)
    staged = []
    backups = []
    installed = []
    preserve_recovery = False
    try:
        _write_staged(
            _game_owned_path(
                game_root, os.path.join(transaction_root, "recovery.json"),
                "The vehicle recovery journal"),
            _recovery_bytes(
                game_root, "apply",
                [target for target, unused_data in writes]))
        for index, (target, data) in enumerate(writes):
            staged_path = _game_owned_path(
                game_root, os.path.join(transaction_root, "new-%d" % index),
                "The staged vehicle overlay")
            _write_staged(staged_path, data)
            staged.append((staged_path, target, data))
        for index, (unused_staged, target, unused_data) in enumerate(staged):
            target = _transaction_target(game_root, target)
            if target in expected_absent and os.path.lexists(target):
                raise VehicleOverlayError(
                    "A vehicle-data override appeared after the stock-data "
                    "check; it was left unchanged: %s" % target)
            if os.path.lexists(target):
                backup = os.path.join(
                    transaction_root, "backup-%d" % index)
                backup = _game_owned_path(
                    game_root, backup, "The vehicle overlay backup")
                os.replace(target, backup)
                backups.append((target, backup))
        for staged_path, target, data in staged:
            target = _transaction_target(game_root, target)
            directory = os.path.dirname(target)
            if not os.path.isdir(directory):
                os.makedirs(directory)
            target = _transaction_target(game_root, target)
            if target in expected_absent:
                descriptor = _open_exclusive(game_root, target)
                installed.append(target)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        try:
                            os.fsync(stream.fileno())
                        except (AttributeError, OSError):
                            pass
                except Exception:
                    raise
            else:
                os.replace(staged_path, target)
                installed.append(target)
    except Exception as error:
        rollback_errors = []
        backed_targets = set(target for target, unused_backup in backups)
        for target in reversed(installed):
            if target not in backed_targets and os.path.lexists(target):
                try:
                    target = _transaction_target(game_root, target)
                    os.unlink(target)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
        for target, backup in reversed(backups):
            if os.path.lexists(backup):
                try:
                    target = _transaction_target(game_root, target)
                    backup = _game_owned_path(
                        game_root, backup, "The vehicle overlay backup")
                    directory = os.path.dirname(target)
                    if not os.path.isdir(directory):
                        os.makedirs(directory)
                    target = _transaction_target(game_root, target)
                    os.replace(backup, target)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            else:
                rollback_errors.append("A transaction backup is missing.")
        if rollback_errors:
            preserve_recovery = True
            raise VehicleOverlayError(
                "The overlay transaction failed and automatic rollback was "
                "incomplete. Recovery files were kept in %s: %s" %
                (transaction_root, "; ".join(rollback_errors)))
        raise VehicleOverlayError(
            "The overlay transaction was rolled back: %s" % error)
    finally:
        if not preserve_recovery:
            _retire_transaction_root(game_root, transaction_root)


_TRANSACTION_PREFIXES = (
    ".wot-vehicle-overlay-", ".wot-vehicle-restore-")


def _pending_transaction_roots(game_root):
    game_root = os.path.abspath(game_root)
    try:
        names = os.listdir(game_root)
    except (IOError, OSError) as error:
        raise VehicleOverlayError(
            "The game folder cannot be checked for profile recovery: %s" %
            error)
    return sorted(
        os.path.join(game_root, name) for name in names
        if name.startswith(_TRANSACTION_PREFIXES))


def has_pending_vehicle_recovery(game_root):
    return bool(_pending_transaction_roots(game_root))


def _recovery_target(game_root, relative):
    if (not isinstance(relative, str) or not relative or "\\" in relative or
            any(part in ("", ".", "..") for part in relative.split("/"))):
        raise VehicleOverlayError(
            "A vehicle profile recovery target is unsafe.")
    game_root = os.path.abspath(game_root)
    target = os.path.abspath(os.path.join(game_root, *relative.split("/")))
    return _game_owned_path(
        game_root, target, "A vehicle profile recovery target")


def _recovery_target_kind(relative):
    if relative == PROFILE_STORE_RELATIVE:
        return "profile-store"
    manifest_relative = "%s/%s" % (OVERLAY_ROOT, MANIFEST_NAME)
    if relative == manifest_relative:
        return "manifest"
    prefix = OVERLAY_ROOT + "/"
    if relative.startswith(prefix):
        member = relative[len(prefix):]
        _validate_member(member)
        return "overlay"
    raise VehicleOverlayError(
        "A vehicle profile recovery target is outside the owned files.")


def _load_recovery_records(game_root, transaction_root):
    transaction_root = _game_owned_path(
        game_root, transaction_root, "The vehicle recovery directory")
    recovery_path = _game_owned_path(
        game_root, os.path.join(transaction_root, "recovery.json"),
        "The vehicle recovery journal")
    if not os.path.lexists(recovery_path):
        try:
            if not os.listdir(transaction_root):
                return []
        except (IOError, OSError) as error:
            raise VehicleOverlayError(
                "A vehicle profile recovery directory is unreadable: %s" %
                error)
        raise VehicleOverlayError(
            "A vehicle profile recovery directory has no recovery journal: %s" %
            transaction_root)
    if os.path.islink(recovery_path) or not os.path.isfile(recovery_path):
        raise VehicleOverlayError(
            "A vehicle profile recovery journal is not a regular file.")
    try:
        with open(recovery_path, "rb") as stream:
            recovery = json.load(stream)
    except (IOError, OSError, TypeError, ValueError) as error:
        raise VehicleOverlayError(
            "A vehicle profile recovery journal is unreadable: %s" % error)
    if (not isinstance(recovery, dict) or
            recovery.get("operation") not in ("apply", "restore-defaults") or
            not isinstance(recovery.get("targets"), list)):
        raise VehicleOverlayError(
            "A vehicle profile recovery journal is invalid.")
    operation = recovery["operation"]
    records = []
    kinds = []
    seen = set()
    for index, record in enumerate(recovery["targets"]):
        if (not isinstance(record, dict) or
                record.get("backup") != "backup-%d" % index or
                not isinstance(record.get("hadTarget"), bool)):
            raise VehicleOverlayError(
                "A vehicle profile recovery record is invalid.")
        relative = record.get("target")
        target = _recovery_target(game_root, relative)
        kinds.append(_recovery_target_kind(relative))
        if target in seen:
            raise VehicleOverlayError(
                "A vehicle profile recovery journal repeats a target.")
        seen.add(target)
        backup = _game_owned_path(
            game_root, os.path.join(transaction_root, record["backup"]),
            "A vehicle profile recovery backup")
        if os.path.lexists(backup) and (
                os.path.islink(backup) or not os.path.isfile(backup)):
            raise VehicleOverlayError(
                "A vehicle profile recovery backup is not a regular file.")
        records.append({
            "backup": backup,
            "hadTarget": record["hadTarget"],
            "target": target,
        })
    valid_apply = (
        kinds == ["profile-store"] or
        (len(kinds) >= 2 and kinds[-1] == "manifest" and
         all(kind == "overlay" for kind in kinds[:-1])))
    valid_restore = (
        bool(kinds) and kinds[-1] == "manifest" and
        all(kind == "overlay" for kind in kinds[:-1]))
    if ((operation == "apply" and not valid_apply) or
            (operation == "restore-defaults" and not valid_restore)):
        raise VehicleOverlayError(
            "A vehicle profile recovery journal has an invalid target set.")
    return records


def recover_vehicle_profile_transactions(game_root, is_running=None):
    """Roll back profile writes interrupted before their staging was removed."""
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    recovered = 0
    for transaction_root in _pending_transaction_roots(status["path"]):
        transaction_root = _game_owned_path(
            status["path"], transaction_root,
            "The vehicle recovery directory")
        if os.path.islink(transaction_root) or not os.path.isdir(
                transaction_root):
            raise VehicleOverlayError(
                "A vehicle profile recovery path is not a regular directory: "
                "%s" % transaction_root)
        records = _load_recovery_records(status["path"], transaction_root)
        try:
            for index, record in enumerate(records):
                target = _transaction_target(
                    status["path"], record["target"])
                backup = _game_owned_path(
                    status["path"], record["backup"],
                    "A vehicle profile recovery backup")
                discard = _game_owned_path(
                    status["path"], os.path.join(
                        transaction_root, "discard-%d" % index),
                    "A vehicle profile recovery discard")
                if record["hadTarget"]:
                    if os.path.lexists(backup):
                        if os.path.islink(backup) or not os.path.isfile(backup):
                            raise VehicleOverlayError(
                                "A vehicle profile recovery backup is not a "
                                "regular file.")
                        if os.path.lexists(target):
                            if os.path.lexists(discard):
                                raise VehicleOverlayError(
                                    "A vehicle profile recovery discard path "
                                    "already exists.")
                            os.replace(target, discard)
                        directory = os.path.dirname(target)
                        if not os.path.isdir(directory):
                            os.makedirs(directory)
                        target = _transaction_target(status["path"], target)
                        os.replace(backup, target)
                    elif not os.path.lexists(target):
                        raise VehicleOverlayError(
                            "A vehicle profile recovery backup is missing.")
                elif os.path.lexists(target):
                    if os.path.lexists(discard):
                        raise VehicleOverlayError(
                            "A vehicle profile recovery discard path already "
                            "exists.")
                    os.replace(target, discard)
        except VehicleOverlayError:
            raise
        except Exception as error:
            raise VehicleOverlayError(
                "Vehicle profile crash recovery is incomplete; recovery "
                "files were kept in %s: %s" % (transaction_root, error))
        if not _retire_transaction_root(status["path"], transaction_root):
            raise VehicleOverlayError(
                "Vehicle profile crash recovery succeeded but its staging "
                "directory could not be retired safely.")
        recovered += 1
    return recovered


def _manifest_bytes(manifest):
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def inspect_vehicle_field(game_root, member, field_path):
    """Describe one original scalar and any currently owned override."""
    status, package_path = _require_target(game_root)
    rule = _field_rule(member, field_path)
    unused_data, original_root = _read_source_member(package_path, member)
    original = _find_value(original_root, field_path)
    _validate_original(original, rule)

    manifest, unused_exists = _load_manifest(status["path"])
    entry = _entry_map(manifest).get(member)
    conflict = _ownership_problem(status["path"], member, entry)
    current = original
    overlay_path = _overlay_path(status["path"], member)
    if entry is not None and not conflict and os.path.isfile(overlay_path):
        try:
            current_root = packed_xml.read_packed_xml(_read_file(overlay_path))
            current = _find_value(current_root, field_path)
        except (IOError, OSError, TypeError, ValueError) as error:
            conflict = "Conflict: the installed overlay is unreadable: %s" % error

    return {
        "member": member,
        "fieldPath": field_path,
        "originalValue": _scalar_text(original),
        "currentValue": _scalar_text(current),
        "packedType": _TYPE_NAMES.get(original.value_type, "unknown"),
        "constraint": rule["description"],
        "overlayPath": overlay_path,
        "conflict": conflict,
    }


def apply_vehicle_edit(game_root, member, field_path, replacement_value,
                       is_running=None):
    """Merge one edit, rebuild every owned member, and commit atomically."""
    status, package_path = _require_target(
        game_root, require_closed=True, is_running=is_running)

    manifest, unused_exists = _load_manifest(status["path"])
    old_entries = _entry_map(manifest)
    entries = copy.deepcopy(old_entries)
    manifest_value = _merge_saved_edit(
        entries, package_path, member, field_path, replacement_value)
    peer = _equal_mode_peer(package_path, member, field_path)
    if peer is not None:
        _merge_saved_edit(
            entries, package_path, peer, field_path, manifest_value)
    _expand_equal_mode_edits(entries, package_path)

    _assert_owned_files_safe(status["path"], old_entries, entries)
    rebuilt = {}
    for owned_member in sorted(entries):
        output, normalized_edits = _build_member(
            package_path, entries[owned_member])
        entries[owned_member]["edits"] = normalized_edits
        entries[owned_member]["overlaySha256"] = _sha256(output)
        rebuilt[owned_member] = output

    manifest["members"] = [entries[name] for name in sorted(entries)]
    manifest["targetVersion"] = TARGET_VERSION
    manifest["targetBuild"] = TARGET_BUILD
    manifest["updatedAt"] = _now()
    _validate_manifest(manifest)
    writes = [(_overlay_path(status["path"], name), rebuilt[name])
              for name in sorted(rebuilt)]
    # The ownership record is installed last.  Any ordinary failure restores
    # both earlier overlays and the previous manifest.
    writes.append((manifest_path(status["path"]), _manifest_bytes(manifest)))
    _transactional_write(status["path"], writes)
    return inspect_vehicle_field(
        status["path"], member, field_path)


def restore_vehicle_defaults(game_root, is_running=None):
    """Remove only complete members proven to be owned by this manifest."""
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    manifest, exists = _load_manifest(status["path"])
    if not exists:
        return 0
    entries = _entry_map(manifest)
    for member, entry in entries.items():
        problem = _ownership_problem(status["path"], member, entry)
        if problem and not problem.startswith("Owned overlay is missing"):
            raise VehicleOverlayError(problem)

    try:
        transaction_root = tempfile.mkdtemp(
            prefix=".wot-vehicle-restore-", dir=status["path"])
        transaction_root = _game_owned_path(
            status["path"], transaction_root,
            "The vehicle recovery directory")
    except (IOError, OSError) as error:
        raise VehicleOverlayError(
            "Default restoration could not start: %s" % error)
    moved = []
    preserve_recovery = False
    try:
        targets = [(_overlay_path(status["path"], member), member)
                   for member in sorted(entries)]
        targets.append((manifest_path(status["path"]), MANIFEST_NAME))
        _write_staged(
            _game_owned_path(
                status["path"], os.path.join(
                    transaction_root, "recovery.json"),
                "The vehicle recovery journal"),
            _recovery_bytes(
                status["path"], "restore-defaults",
                [target for target, unused_name in targets]))
        for index, (target, unused_name) in enumerate(targets):
            target = _transaction_target(status["path"], target)
            if not os.path.lexists(target):
                continue
            backup = _game_owned_path(
                status["path"], os.path.join(
                    transaction_root, "backup-%d" % index),
                "The vehicle overlay backup")
            os.replace(target, backup)
            moved.append((target, backup))
    except Exception as error:
        rollback_errors = []
        for target, backup in reversed(moved):
            if os.path.lexists(backup):
                try:
                    target = _transaction_target(status["path"], target)
                    backup = _game_owned_path(
                        status["path"], backup,
                        "The vehicle overlay backup")
                    directory = os.path.dirname(target)
                    if not os.path.isdir(directory):
                        os.makedirs(directory)
                    target = _transaction_target(status["path"], target)
                    os.replace(backup, target)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            else:
                rollback_errors.append("A restoration backup is missing.")
        if rollback_errors:
            preserve_recovery = True
            raise VehicleOverlayError(
                "Default restoration failed and automatic rollback was "
                "incomplete. Recovery files were kept in %s: %s" %
                (transaction_root, "; ".join(rollback_errors)))
        raise VehicleOverlayError(
            "Default restoration was rolled back: %s" % error)
    finally:
        if not preserve_recovery:
            _retire_transaction_root(status["path"], transaction_root)
    return len(entries)


def _empty_profile_store():
    timestamp = _now()
    return {
        "schema": PROFILE_STORE_SCHEMA,
        "targetVersion": TARGET_VERSION,
        "targetBuild": TARGET_BUILD,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "profiles": [],
    }


def _profile_manifest(profile):
    manifest = _empty_manifest()
    manifest["createdAt"] = profile["createdAt"]
    manifest["updatedAt"] = profile["updatedAt"]
    manifest["activeProfile"] = profile["name"]
    manifest["members"] = copy.deepcopy(profile["members"])
    return _validate_manifest(manifest)


def _validate_profile_store(value):
    if not isinstance(value, dict):
        raise VehicleOverlayError("vehicle_profiles.json must be an object.")
    if value.get("schema") != PROFILE_STORE_SCHEMA:
        raise VehicleOverlayError(
            "vehicle_profiles.json does not belong to this editor.")
    if not isinstance(value.get("createdAt"), str) or not isinstance(
            value.get("updatedAt"), str):
        raise VehicleOverlayError(
            "The vehicle profile store timestamps are invalid.")
    profiles = value.get("profiles")
    if not isinstance(profiles, list):
        raise VehicleOverlayError("The vehicle profile list is invalid.")
    seen = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise VehicleOverlayError("A vehicle profile is invalid.")
        name = _normalize_profile_name(profile.get("name"))
        if name != profile.get("name"):
            raise VehicleOverlayError(
                "A saved vehicle profile name is not normalized.")
        key = name.casefold()
        if key in seen:
            raise VehicleOverlayError(
                "Vehicle profile names must be unique ignoring case.")
        seen.add(key)
        if not isinstance(profile.get("createdAt"), str) or not isinstance(
                profile.get("updatedAt"), str):
            raise VehicleOverlayError(
                "A vehicle profile timestamp is invalid.")
        if not isinstance(profile.get("members"), list):
            raise VehicleOverlayError(
                "A vehicle profile member list is invalid.")
        _profile_manifest(profile)
    return value


def _read_profile_store(path):
    if not os.path.lexists(path):
        return None, None
    if os.path.islink(path) or not os.path.isfile(path):
        raise VehicleOverlayError(
            "vehicle_profiles.json is not a regular file.")
    try:
        with open(path, "rb") as stream:
            payload = stream.read()
        value = json.loads(payload.decode("utf-8"))
    except (IOError, OSError, TypeError, ValueError) as error:
        raise VehicleOverlayError(
            "vehicle_profiles.json is unreadable: %s" % error)
    return _validate_profile_store(value), payload


def _atomic_profile_store_write(path, payload):
    """Replace the profile store from a temporary file beside its target."""
    directory = os.path.dirname(os.path.abspath(path))
    descriptor = None
    temporary_path = None
    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
        path = _contained_path(
            directory, path, "The vehicle profile store")
        if os.path.lexists(path) and (
                os.path.islink(path) or not os.path.isfile(path)):
            raise VehicleOverlayError(
                "vehicle_profiles.json is not a regular file.")
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".vehicle-profiles-", suffix=".tmp", dir=directory)
        temporary_path = _contained_path(
            directory, temporary_path,
            "The staged vehicle profile store")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except (AttributeError, OSError):
                pass
        path = _contained_path(
            directory, path, "The vehicle profile store")
        os.replace(temporary_path, path)
        temporary_path = None
    except VehicleOverlayError:
        raise
    except (IOError, OSError) as error:
        raise VehicleOverlayError(
            "vehicle_profiles.json could not be saved: %s" % error)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None and os.path.lexists(temporary_path):
            try:
                os.unlink(temporary_path)
            except (IOError, OSError):
                pass


def _load_profile_store(game_root):
    path = profile_store_path(game_root)
    value, unused_payload = _read_profile_store(path)
    if value is not None:
        return value, True

    # The profile feature originally stored user data below ``mods``.  Copy a
    # valid old store on first use, but retain it for rollback to that build.
    # If APPDATA is unavailable, ``path`` already is this legacy location.
    if _appdata_profile_root() is not None:
        legacy_path = legacy_profile_store_path(game_root)
        legacy_value, legacy_payload = _read_profile_store(legacy_path)
        if legacy_value is not None:
            try:
                _atomic_profile_store_write(path, legacy_payload)
            except VehicleOverlayError:
                return legacy_value, True
            return legacy_value, True
    return _empty_profile_store(), False


def _profile_store_bytes(store):
    return (json.dumps(store, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def _save_profile_store(game_root, store):
    store["targetVersion"] = TARGET_VERSION
    store["targetBuild"] = TARGET_BUILD
    store["updatedAt"] = _now()
    _validate_profile_store(store)
    _atomic_profile_store_write(
        profile_store_path(game_root), _profile_store_bytes(store))


def _logical_profile_signature(members):
    return tuple(
        (entry["sourceMember"], tuple(
            (edit["fieldPath"], edit["originalPackedType"],
             edit["originalValue"], edit["replacementValue"])
            for edit in entry["edits"]))
        for entry in sorted(members, key=lambda item: item["sourceMember"]))


def preserve_legacy_vehicle_overlay(game_root, is_running=None):
    """Import the old persistent editor manifest before deactivating it."""
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    manifest, exists = _load_manifest(status["path"])
    if not exists or manifest.get("activeProfile") is not None:
        return None
    entries = _entry_map(manifest)
    for member, entry in entries.items():
        problem = _ownership_problem(status["path"], member, entry)
        if problem and not problem.startswith("Owned overlay is missing"):
            raise VehicleOverlayError(problem)

    store, unused_exists = _load_profile_store(status["path"])
    signature = _logical_profile_signature(manifest["members"])
    for profile in store["profiles"]:
        if _logical_profile_signature(profile["members"]) == signature:
            return profile["name"]

    base_name = "Imported vehicle edits"
    used = set(profile["name"].casefold() for profile in store["profiles"])
    name = base_name
    suffix = 2
    while name.casefold() in used:
        name = "%s %d" % (base_name, suffix)
        suffix += 1
    timestamp = _now()
    store["profiles"].append({
        "name": name,
        "createdAt": manifest.get("createdAt", timestamp),
        "updatedAt": timestamp,
        "members": copy.deepcopy(manifest["members"]),
    })
    store["profiles"].sort(key=lambda profile: profile["name"].casefold())
    _save_profile_store(status["path"], store)
    return name


def _profile_index(store, profile_name):
    normalized = _normalize_profile_name(profile_name)
    key = normalized.casefold()
    matches = [index for index, profile in enumerate(store["profiles"])
               if profile["name"].casefold() == key]
    if len(matches) != 1:
        raise VehicleOverlayError(
            "Vehicle profile %s does not exist." % normalized)
    return matches[0]


def list_vehicle_profiles(game_root):
    """List saved profiles without materializing anything into res_mods."""
    status, unused_package = _require_target(game_root)
    store, unused_exists = _load_profile_store(status["path"])
    return sorted(
        (profile["name"] for profile in store["profiles"]),
        key=lambda name: name.casefold())


def create_vehicle_profile(game_root, profile_name):
    status, unused_package = _require_target(game_root)
    name = _normalize_profile_name(profile_name)
    store, unused_exists = _load_profile_store(status["path"])
    if any(profile["name"].casefold() == name.casefold()
           for profile in store["profiles"]):
        raise VehicleOverlayError(
            "A vehicle profile named %s already exists." % name)
    timestamp = _now()
    store["profiles"].append({
        "name": name,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "members": [],
    })
    store["profiles"].sort(key=lambda profile: profile["name"].casefold())
    _save_profile_store(status["path"], store)
    return name


def delete_vehicle_profile(game_root, profile_name, is_running=None):
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    # A previous launcher crash may have left one temporary profile active.
    # Clear it before allowing its logical definition to be deleted.
    recover_vehicle_profile_transactions(
        status["path"], is_running=lambda: False)
    preserve_legacy_vehicle_overlay(
        status["path"], is_running=lambda: False)
    restore_vehicle_defaults(status["path"], is_running=lambda: False)
    store, unused_exists = _load_profile_store(status["path"])
    index = _profile_index(store, profile_name)
    deleted = store["profiles"].pop(index)["name"]
    _save_profile_store(status["path"], store)
    return deleted


def clear_vehicle_profile(game_root, profile_name, is_running=None):
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    store, unused_exists = _load_profile_store(status["path"])
    index = _profile_index(store, profile_name)
    profile = store["profiles"][index]
    count = len(profile["members"])
    profile["members"] = []
    profile["updatedAt"] = _now()
    _save_profile_store(status["path"], store)
    return count


def inspect_profile_field(game_root, profile_name, member, field_path):
    """Show one original value and the value saved in a named profile."""
    status, package_path = _require_target(game_root)
    rule = _field_rule(member, field_path)
    unused_data, original_root = _read_source_member(package_path, member)
    original = _find_value(original_root, field_path)
    _validate_original(original, rule)

    store, unused_exists = _load_profile_store(status["path"])
    profile = store["profiles"][_profile_index(store, profile_name)]
    entry = _entry_map(_profile_manifest(profile)).get(member)
    current = original
    if entry is not None:
        output, unused_edits = _build_member(package_path, entry)
        current_root = packed_xml.read_packed_xml(output)
        current = _find_value(current_root, field_path)

    return {
        "profileName": profile["name"],
        "member": member,
        "fieldPath": field_path,
        "originalValue": _scalar_text(original),
        "currentValue": _scalar_text(current),
        "packedType": _TYPE_NAMES.get(original.value_type, "unknown"),
        "constraint": rule["description"],
        "overlayPath": profile_store_path(status["path"]),
        "conflict": "",
    }


def list_vehicle_profile_field_choices(game_root, profile_name,
                                       vehicle_member):
    """Return safe fields with the current values from one named profile.

    This is the batch counterpart to :func:`inspect_profile_field`.  It keeps
    the editor and armor viewer on one profile snapshot without rebuilding a
    complete Packed XML member once per displayed armor plate.
    """
    status, unused_package = _require_target(game_root)
    fields = list_vehicle_field_choices(status["path"], vehicle_member)
    store, unused_exists = _load_profile_store(status["path"])
    profile = store["profiles"][_profile_index(store, profile_name)]
    entries = _entry_map(_profile_manifest(profile))
    replacements = {}
    for member, entry in entries.items():
        for edit in entry["edits"]:
            replacements[(member, edit["fieldPath"])] = edit[
                "replacementValue"]
    result = []
    for field in fields:
        current = dict(field)
        current["currentValue"] = str(replacements.get(
            (field["member"], field["fieldPath"]),
            field["originalValue"]))
        result.append(current)
    return result


def apply_profile_edit(game_root, profile_name, member, field_path,
                       replacement_value, is_running=None):
    """Save one logical edit without leaving modified data in res_mods."""
    status, package_path = _require_target(
        game_root, require_closed=True, is_running=is_running)

    store, unused_exists = _load_profile_store(status["path"])
    profile_index = _profile_index(store, profile_name)
    profile = store["profiles"][profile_index]
    entries = copy.deepcopy(_entry_map(_profile_manifest(profile)))
    manifest_value = _merge_saved_edit(
        entries, package_path, member, field_path, replacement_value)
    peer = _equal_mode_peer(package_path, member, field_path)
    if peer is not None:
        _merge_saved_edit(
            entries, package_path, peer, field_path, manifest_value)
    _expand_equal_mode_edits(entries, package_path)

    for owned_member in sorted(entries):
        output, normalized_edits = _build_member(
            package_path, entries[owned_member])
        entries[owned_member]["edits"] = normalized_edits
        entries[owned_member]["overlaySha256"] = _sha256(output)

    profile["members"] = [entries[name] for name in sorted(entries)]
    profile["updatedAt"] = _now()
    _save_profile_store(status["path"], store)
    return inspect_profile_field(
        status["path"], profile["name"], member, field_path)


def ensure_original_vehicle_data(game_root, is_running=None):
    """Remove only this launcher's temporary vehicle-data overlay."""
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    recover_vehicle_profile_transactions(
        status["path"], is_running=lambda: False)
    preserve_legacy_vehicle_overlay(
        status["path"], is_running=lambda: False)
    removed = restore_vehicle_defaults(
        status["path"], is_running=lambda: False)
    return removed


def activate_vehicle_profile(game_root, profile_name, is_running=None):
    """Materialize one profile for a single-player process only."""
    status, package_path = _require_target(
        game_root, require_closed=True, is_running=is_running)
    ensure_original_vehicle_data(status["path"], is_running=lambda: False)
    store, unused_exists = _load_profile_store(status["path"])
    profile = store["profiles"][_profile_index(store, profile_name)]
    entries = copy.deepcopy(_entry_map(_profile_manifest(profile)))
    if not entries:
        return 0
    _expand_equal_mode_edits(entries, package_path)

    rebuilt = {}
    for member in sorted(entries):
        output, normalized_edits = _build_member(
            package_path, entries[member])
        entries[member]["edits"] = normalized_edits
        entries[member]["overlaySha256"] = _sha256(output)
        rebuilt[member] = output

    manifest = _empty_manifest()
    manifest["activeProfile"] = profile["name"]
    manifest["members"] = [entries[name] for name in sorted(entries)]
    _validate_manifest(manifest)
    writes = [(_overlay_path(status["path"], member), rebuilt[member])
              for member in sorted(rebuilt)]
    writes.append((manifest_path(status["path"]), _manifest_bytes(manifest)))
    # A loose same-path override cannot coexist with this profile. Refuse only
    # that exact collision so unrelated third-party mods remain unrestricted.
    _transactional_write(
        status["path"], writes,
        expected_absent=[target for target, unused_data in writes])
    return len(entries)


def prepare_vehicle_profile(game_root, profile_name=None, is_running=None):
    """Prepare stock data or one named profile immediately before launch."""
    if profile_name is None or not str(profile_name).strip():
        removed = ensure_original_vehicle_data(
            game_root, is_running=is_running)
        return {
            "profile": None,
            "installedMembers": 0,
            "removedMembers": removed,
        }
    installed = activate_vehicle_profile(
        game_root, profile_name, is_running=is_running)
    return {
        "profile": _normalize_profile_name(profile_name),
        "installedMembers": installed,
        "removedMembers": 0,
    }


def vehicle_overlay_digest(game_root, is_running=None):
    """Return the SHA-256 of the installed overlay manifest, or ''.

    The digest covers the exact ``vehicle_overlays.json`` bytes, so the room
    server (which hashes the same file) and this launcher agree without any
    cross-machine serialization contract.
    """
    status, unused_package = _require_target(game_root, is_running=is_running)
    path = manifest_path(status["path"])
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as stream:
            return _sha256(stream.read())
    except (IOError, OSError) as error:
        raise VehicleOverlayError(
            "vehicle_overlays.json is unreadable: %s" % error)


def active_vehicle_overlay(game_root, is_running=None):
    """Return ``(manifest, {member: bytes}, digest)`` of the installed overlay.

    ``(None, {}, '')`` means no launcher-owned overlay is installed.  Every
    member is re-read and checked against the manifest checksum, so the caller
    can safely hand the payload to the room server.
    """
    status, unused_package = _require_target(game_root, is_running=is_running)
    manifest, exists = _load_manifest(status["path"])
    if not exists or not manifest["members"]:
        return None, {}, ""
    root = os.path.join(status["path"], *OVERLAY_ROOT.split("/"))
    payload = {}
    for entry in manifest["members"]:
        member = entry["sourceMember"]
        path = os.path.join(root, *member.split("/"))
        if os.path.islink(path) or not os.path.isfile(path):
            raise VehicleOverlayError(
                "The installed overlay member is missing: %s" % member)
        try:
            data = _read_file(path)
        except (IOError, OSError) as error:
            raise VehicleOverlayError(
                "The installed overlay member is unreadable: %s (%s)" %
                (member, error))
        if _sha256(data) != entry["overlaySha256"]:
            raise VehicleOverlayError(
                "The installed overlay member was changed: %s" % member)
        payload[member] = data
    digest = _sha256(_read_file(manifest_path(status["path"])))
    return manifest, payload, digest


def install_vehicle_overlay(game_root, manifest, members, is_running=None):
    """Install the room host's vehicle-data overlay, transactionally.

    The manifest is validated with the same ownership rules as a local
    profile, every member must match its checksum, and the commit uses the
    same recovery-journaled transaction as ``activate_vehicle_profile``.  Any
    previously installed overlay is removed first, so the room host's data
    always wins.
    """
    if not isinstance(manifest, dict) or not isinstance(members, dict):
        raise VehicleOverlayError("The host vehicle-data overlay is invalid.")
    _validate_manifest(manifest)
    expected = _entry_map(manifest)
    if not expected:
        raise VehicleOverlayError(
            "The host vehicle-data overlay carries no members.")
    if set(members) != set(expected):
        raise VehicleOverlayError(
            "The host vehicle-data overlay members do not match its "
            "manifest.")
    for member, data in members.items():
        if not isinstance(data, bytes) or not data:
            raise VehicleOverlayError(
                "The host vehicle-data member is invalid: %s" % member)
        if _sha256(data) != expected[member]["overlaySha256"]:
            raise VehicleOverlayError(
                "The host vehicle-data member failed its checksum: %s" %
                member)
    status, unused_package = _require_target(
        game_root, require_closed=True, is_running=is_running)
    ensure_original_vehicle_data(status["path"], is_running=lambda: False)
    writes = [(_overlay_path(status["path"], member), members[member])
              for member in sorted(members)]
    writes.append((manifest_path(status["path"]), _manifest_bytes(manifest)))
    _transactional_write(
        status["path"], writes,
        expected_absent=[target for target, unused_data in writes])
    return len(members)
