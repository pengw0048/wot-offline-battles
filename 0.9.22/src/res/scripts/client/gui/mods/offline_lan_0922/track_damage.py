# -*- coding: utf-8 -*-
"""Retail-style track damage zones and shell damage-channel selection.

Pure data + math: NO BigWorld imports at module scope, so the whole module is
desktop-testable under Python 2 or 3.  ``ResMgr`` is imported lazily inside the
raw-resource resolver and may be injected by a test.

Two separate defects are addressed here.

1. Damage channel.  ``common/vehicle.xml`` gives both track materials
   ``damageKind=auto``.  ``vehicles.py::_readArmor()`` resolves ``auto`` to
   ``damageKind = 0 if armor else 1``, and every shipped chassis gives its
   ``leftTrack``/``rightTrack`` armour a nonzero value, so a normal track
   material selects the ARMOR channel ``shell.damage[0]`` - not the device
   channel ``shell.damage[1]`` an ordinary module uses.

2. Track zones.  Update 6.4 split the track into a leading/rearmost driving
   wheel that takes the full roll and an ordinary middle run that is much
   harder to break.  The cell/server descriptor carries ``bulkHealthFactor``
   for exactly that reduction, and the client/cell chassis reader publishes
   ``drivingWheelsSizes = (front_radius * 2.2, rear_radius * 2.2)``.

Both facts come from the pinned global 0.9.22.0.1 #788 decompiled reference.
The endpoint-zone geometry below is this product's documented 0.9.x
convention, NOT a recovered native server equation: the exact client exposes
only wheel node NAMES and radii, never their positions, so the two configured
wheel sizes are anchored to the two ends of the chassis' own longitudinal
carrying extent.  Only a real Windows run can calibrate the boundary.
"""

import math
import sys


# Shell ``damage`` is the exact two-element ``(armor, devices)`` tuple.
ARMOR_DAMAGE_INDEX = 0
DEVICE_DAMAGE_INDEX = 1

# The two device names whose material may carry a driving-wheel zone.
TRACK_DEVICE_NAMES = frozenset(['leftTrackHealth', 'rightTrackHealth'])

# ``itemTypeName`` of the component whose collision model owns both tracks.
# The zone law is expressed in this component's local frame, which is the
# chassis frame ``topRightCarryingPoint`` is measured in.
TRACK_COMPONENT_NAME = 'vehicleChassis'

ZONE_FRONT = 'front'
ZONE_REAR = 'rear'
ZONE_MIDDLE = 'middle'

# BigWorld local +Z is forward, so the leading driving wheel sits at +Z and the
# rearmost at -Z.  Boundaries are inclusive on the wheel side and there is no
# outer bound: an idler or sprocket that overhangs the carrying extent is still
# that end's wheel.
FORWARD_AXIS = 2

# ``ITEM_DEFS_PATH + 'vehicles/'`` in #1513 ``constants.py``/``vehicles.py``.
VEHICLE_TYPE_XML_PATH = 'scripts/item_defs/vehicles/'
# ``vehicles.VEHICLE_MODE_FILE_SUFFIX``; a siege-mode descriptor is read from a
# different file and may therefore select a different chassis section.
VEHICLE_MODE_FILE_SUFFIX = {0: '', 1: '_siege_mode'}

# Bounded diagnostics: one line per distinct signature, and a hard cap so a
# long session can never turn this into a per-shot log.
_MAX_DIAGNOSTIC_SIGNATURES = 512
_reported_signatures = set()

# Cached raw-resource lookups, keyed by stable vehicle/chassis identity.
_bulk_health_cache = {}


def reset_diagnostics():
    """Start a new round's bounded diagnostic budget."""
    _reported_signatures.clear()


def reset_caches():
    """Drop resolved raw-resource values (round change or test isolation)."""
    _bulk_health_cache.clear()


def report(signature, message):
    """Write one bounded ``[Offline LAN 0.9.22] TRACK`` diagnostic line."""
    try:
        key = tuple(str(part) for part in signature)
        if key in _reported_signatures:
            return False
        if len(_reported_signatures) >= _MAX_DIAGNOSTIC_SIGNATURES:
            return False
        _reported_signatures.add(key)
        sys.stdout.write('[Offline LAN 0.9.22] TRACK %s\n' % (message,))
    except Exception:
        # Diagnostics are best effort; a broken stream must never cost a hit.
        return False
    return True


def _descriptor_value(value, name, default=None):
    """Read a copied mapping or a native #1513 descriptor object.

    #1513 item components inherit a legacy ``get`` that deliberately raises
    ``AssertionError('Operation is not allowed')``, so duck-typing ``get`` is
    invalid here exactly as it is in ``device_damage``.
    """
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _finite(value):
    """Return ``value`` as a finite float, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _finite_positive(value):
    number = _finite(value)
    if number is None or number <= 0.0:
        return None
    return number


def _component(value, index):
    """Read one component of a Vector2/Vector3, tuple, list or mapping."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(('x', 'y', 'z')[index])
    try:
        return value[index]
    except (TypeError, IndexError, KeyError):
        pass
    try:
        return getattr(value, ('x', 'y', 'z')[index])
    except (AttributeError, IndexError):
        return None


def material_damage_index(material):
    """Return the shell ``damage`` index this live material selects.

    None means the material does not expose a usable ``damageKind`` and the
    caller must keep its previous behaviour for that one hit.
    """
    kind = getattr(material, 'damageKind', None)
    if kind is None or isinstance(kind, bool):
        return None
    try:
        index = int(kind)
    except (TypeError, ValueError, OverflowError):
        return None
    if index not in (ARMOR_DAMAGE_INDEX, DEVICE_DAMAGE_INDEX):
        return None
    return index


def wheel_zone_bounds(chassis):
    """Return ``(front_bound, rear_bound)`` in chassis-local Z, or None.

    ``front_bound`` is the lowest Z still inside the leading wheel and
    ``rear_bound`` the highest Z still inside the rearmost wheel.  None means
    the descriptor cannot describe a driving-wheel zone, and the caller must
    not then declare every track point a driving wheel.
    """
    half_length = _finite_positive(
        _component(_descriptor_value(chassis, 'topRightCarryingPoint'), 1))
    sizes = _descriptor_value(chassis, 'drivingWheelsSizes')
    front = _finite_positive(_component(sizes, 0))
    rear = _finite_positive(_component(sizes, 1))
    if half_length is None or front is None or rear is None:
        return None
    if front + rear >= 2.0 * half_length:
        # The two end zones would meet or overlap, which would make the whole
        # track a driving wheel.  Impossible geometry: reject it locally.
        return None
    return (half_length - front, rear - half_length)


def classify_zone(local_z, bounds):
    """Return the zone a chassis-local longitudinal coordinate falls in."""
    if bounds is None:
        return None
    position = _finite(local_z)
    if position is None:
        return None
    front_bound, rear_bound = bounds
    if position >= front_bound:
        return ZONE_FRONT
    if position <= rear_bound:
        return ZONE_REAR
    return ZONE_MIDDLE


def local_contact_point(start, end, distance):
    """Return the component-local contact of a native hit at ``distance``.

    ``start``/``end`` are the component-local ray the native hit test ran on,
    so the contact is a pure function of the native distance.  Two collisions
    at the same distance on the same component therefore share one point,
    which is why the authoritative resolver can match evidence by
    ``(component, distance)`` without ever attaching the wrong local point.
    """
    origin = tuple(_finite(_component(start, index)) for index in range(3))
    target = tuple(_finite(_component(end, index)) for index in range(3))
    if None in origin or None in target:
        return None
    travel = _finite(distance)
    if travel is None or travel < 0.0:
        return None
    delta = tuple(target[index] - origin[index] for index in range(3))
    length = math.sqrt(sum(value * value for value in delta))
    if length <= 1.0e-9:
        return None
    scale = travel / length
    return tuple(origin[index] + delta[index] * scale for index in range(3))


def descriptor_bulk_health_factor(descriptor):
    """Return a valid ``chassis.bulkHealthFactor`` already on the descriptor."""
    chassis = _descriptor_value(descriptor, 'chassis')
    if chassis is None:
        return None
    return _finite_positive(_descriptor_value(chassis, 'bulkHealthFactor'))


def _vehicle_xml_identity(descriptor):
    """Return ``(nation, xml_name, mode_suffix, chassis_name)`` or None.

    ``VehicleDescr.name``/``VehicleType.name`` is ``'<nation>:<xmlName>'`` and
    ``VehicleType.mode`` selects the siege-mode file, which is the evidenced
    layout that can carry a different chassis section.
    """
    name = _descriptor_value(descriptor, 'name')
    if name is None:
        name = _descriptor_value(
            _descriptor_value(descriptor, 'type'), 'name')
    chassis_name = _descriptor_value(
        _descriptor_value(descriptor, 'chassis'), 'name')
    if not name or not chassis_name:
        return None
    try:
        nation, xml_name = str(name).split(':', 1)
    except ValueError:
        return None
    if not nation or not xml_name:
        return None
    mode = _descriptor_value(
        _descriptor_value(descriptor, 'type'), 'mode', 0)
    try:
        suffix = VEHICLE_MODE_FILE_SUFFIX[int(mode)]
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
    return (nation, xml_name, suffix, str(chassis_name))


def _read_resource_bulk_health_factor(identity, res_mgr):
    """Read one vehicle-local ``chassis/<name>/bulkHealthFactor`` section.

    The exact client's own reader leaves the slot unset (``_readChassis``
    guards it with ``not IS_CLIENT and not IS_BOT``), so the value has to come
    from the raw resource.  ``DataSection.readFloat`` is the same access the
    stock ``_xml.readPositiveFloat`` performs.
    """
    nation, xml_name, suffix, chassis_name = identity
    path = '%s%s/%s%s.xml' % (
        VEHICLE_TYPE_XML_PATH, nation, xml_name, suffix)
    if res_mgr is None:
        import ResMgr
        res_mgr = ResMgr
    section = res_mgr.openSection(path)
    if section is None:
        return (None, 'section_unavailable')
    try:
        value = section.readFloat(
            'chassis/%s/bulkHealthFactor' % (chassis_name,), 0.0)
    finally:
        purge = getattr(res_mgr, 'purge', None)
        if callable(purge):
            # Match the stock readers, which release a vehicle XML tree as
            # soon as they are done with it.
            try:
                purge(path, True)
            except Exception:
                pass
    factor = _finite_positive(value)
    if factor is None:
        return (None, 'factor_absent')
    return (factor, None)


def bulk_health_factor(descriptor, res_mgr=None):
    """Return the target chassis' ``bulkHealthFactor``, or None.

    A descriptor or fixture that already carries a valid positive value wins.
    Otherwise the value is resolved once per vehicle/chassis identity from the
    client's own raw resource data and cached.  A failure is contained: the
    caller keeps the previous middle-track behaviour for that one hit.
    """
    direct = descriptor_bulk_health_factor(descriptor)
    if direct is not None:
        return direct
    identity = _vehicle_xml_identity(descriptor)
    if identity is None:
        report(('bulk', 'identity', repr(
            _descriptor_value(descriptor, 'name'))),
            'bulkHealthFactor unresolved: vehicle/chassis identity '
            'unavailable')
        return None
    if identity in _bulk_health_cache:
        return _bulk_health_cache[identity]
    try:
        factor, reason = _read_resource_bulk_health_factor(identity, res_mgr)
    except Exception as error:
        factor, reason = None, '%s: %s' % (error.__class__.__name__, error)
    _bulk_health_cache[identity] = factor
    if factor is None:
        report(('bulk', identity[0], identity[1], identity[3]),
               'bulkHealthFactor unresolved: vehicle=%s:%s chassis=%s '
               'reason=%s' % (identity[0], identity[1], identity[3], reason))
    return factor


def zone_damage_scale(zone, factor):
    """Return the multiplier the resolved zone applies to the shell roll."""
    if zone in (ZONE_FRONT, ZONE_REAR):
        return 1.0
    if zone == ZONE_MIDDLE:
        scale = _finite_positive(factor)
        if scale is None:
            return None
        return 1.0 / scale
    return None
