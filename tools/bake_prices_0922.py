#!/usr/bin/env python3
"""Bake the #1513 shop prices the client parses but then throws away.

``items.vehicles.init`` assigns ``_g_prices = pricesToCollect`` and the client
passes nothing, so every ``_readPriceForItem`` call returns before it stores a
price.  On retail that is correct: prices are server state that discounts
change, and the client receives them in the shop synchronization.  An offline
account has no such server, and inventing prices would contradict the exact
vehicle, module and shell values the pinned client already ships.

So read them back out of the same item definitions the client parsed.  The
vehicle prices in ``list.xml`` are proof that they survive packing: #1513 reads
that section unconditionally and derives the ``premium`` tag from whether the
price is denominated in gold.

    python3 tools/bake_prices_0922.py "$WOT_0922_CLIENT"

The generated module is data, not logic: it maps an item's exact definition
name to its exact shipped price. The research tree is deliberately absent
because the client does keep that: ``VehicleType.unlocksDescrs`` holds
``(xpCost, compactDescr, *requiredCompactDescrs)`` at runtime.
"""

import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vehicle_prices
from packed_xml import read_packed_xml, TYPE_ELEMENT


TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
SCRIPTS_PACKAGE = 'res/packages/scripts.pkg'
NATIONS = ('ussr', 'germany', 'usa', 'china', 'france', 'uk', 'japan',
           'czech', 'sweden', 'poland')

# The per-vehicle XML section that holds each installable component, mapped to
# the ``items.ITEM_TYPE_NAMES`` name the client uses for the same thing.
VEHICLE_COMPONENT_SECTIONS = {
    'chassis': 'vehicleChassis',
    'turrets0': 'vehicleTurret',
    'engines': 'vehicleEngine',
    'radios': 'vehicleRadio',
    'fuelTanks': 'vehicleFuelTank',
    'guns': 'vehicleGun',
}
# ``components/<file>.xml`` holds the definitions shared between the vehicles
# of one nation.  Every one of them keeps its items under ``shared``.
SHARED_COMPONENT_FILES = {
    'chassis': 'vehicleChassis',
    'turrets': 'vehicleTurret',
    'engines': 'vehicleEngine',
    'radios': 'vehicleRadio',
    'fuelTanks': 'vehicleFuelTank',
    'guns': 'vehicleGun',
}
SHELL_FILE = 'shells'
COMMON_ARTEFACT_FILES = {
    'optional_devices': 'optionalDevice',
    'equipments': 'equipment',
}

OUTPUT_PATH = (Path(__file__).resolve().parent.parent / 'src' / 'res' /
               'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922' /
               'price_catalogue.py')

HEADER = '''# -*- coding: utf-8 -*-
"""Generated catalogue of the exact #1513 shop prices.

Do not edit by hand.  Run ``tools/bake_prices_0922.py "$WOT_0922_CLIENT"``
to regenerate it against the pinned client.

Each entry is ``(credits, gold, not_in_shop)`` for one item definition name.
Exactly one currency is ever non-zero, because a #1513 ``<price>`` section
carries either a credit amount or a gold amount. ``not_in_shop`` marks an item
the retail shop never offered; it is still a real item with a real price, and
the launcher uses it to offer the gold vehicles a retail account could not buy.

The client parses all of this and then discards it: ``items.vehicles.init``
resets ``_g_prices`` to ``None``, so nothing below can be read back at runtime.
"""

CLIENT_VERSION = %(version)r
CLIENT_BUILD = %(build)r

# items/vehicles.py sets this literal for IS_CLIENT and IS_WEB while reading
# the vehicle list. Selling returns half of what an item cost.
SELL_PRICE_FACTOR = 0.5

CREDITS = 0
GOLD = 1
NOT_IN_SHOP = 2

'''

FOOTER = '''

def _price(table, key):
    return table.get(key)


def vehicle_names(nation):
    """Return every vehicle definition name baked for one nation."""
    prefix = nation + ':'
    return [key[len(prefix):] for key in VEHICLE_PRICES
            if key.startswith(prefix)]


def vehicle_price(nation, name):
    """Return ``(credits, gold, not_in_shop)`` for one vehicle type."""
    return _price(VEHICLE_PRICES, '%s:%s' % (nation, name))


def component_price(nation, item_type_name, name):
    """Return the price of one installable module definition."""
    return _price(
        COMPONENT_PRICES, '%s:%s:%s' % (nation, item_type_name, name))


def shell_price(nation, name):
    return _price(SHELL_PRICES, '%s:%s' % (nation, name))


def artefact_price(name):
    """Return the price of one equipment or optional device."""
    return _price(ARTEFACT_PRICES, name)


def money(price):
    """Return one #1513 shop price mapping, or None when the item has none.

    ``Money`` prefers gold whenever a gold key is present, so a credit price
    must not publish a zero gold key beside it.
    """
    if not price:
        return None
    return ({'gold': price[GOLD]} if price[GOLD] else
            {'credits': price[CREDITS]})
'''


def _client_identity(client_root):
    text = (client_root / 'version.xml').read_text(encoding='utf-8')
    match = re.search(r'v\.([^\s]+)\s+#(\d+)', text)
    if not match:
        raise SystemExit('unrecognized version.xml value')
    if match.group(1) != TARGET_VERSION or match.group(2) != TARGET_BUILD:
        raise SystemExit(
            'client is not %s #%s' % (TARGET_VERSION, TARGET_BUILD))
    return match.group(1), match.group(2)


def _text(value):
    return (value.decode('utf-8', 'replace')
            if isinstance(value, bytes) else str(value))


# The price rule is shared with the launcher, which reads the installed
# client to build the same catalogue for its gold vehicle shop. The two must
# agree, so neither of them owns it.
_children = vehicle_prices.children
_element = vehicle_prices.element
_own_text = vehicle_prices.own_text
_number = vehicle_prices.number
_read_price = vehicle_prices.read_price


def _record(table, key, price, conflicts):
    if price is None:
        return
    existing = table.get(key)
    if existing is not None and existing != price:
        conflicts.append((key, existing, price))
        return
    table[key] = price


def _sections(archive, member):
    try:
        return read_packed_xml(archive.read(member))
    except KeyError:
        return None


def _scan_vehicle(archive, nation, vehicle, components, conflicts):
    root = _sections(
        archive,
        'scripts/item_defs/vehicles/%s/%s.xml' % (nation, vehicle))
    if root is None:
        return
    for section_name, item_type_name in VEHICLE_COMPONENT_SECTIONS.items():
        section = _element(dict(_children(root)).get(section_name))
        if section is None:
            continue
        for name, value in _children(section):
            item = _element(value)
            if item is None:
                continue
            _record(components, '%s:%s:%s' % (nation, item_type_name, name),
                    _read_price(item), conflicts)
            # A turret carries the guns that can be mounted in it.
            guns = _element(dict(_children(item)).get('guns'))
            if guns is None:
                continue
            for gun_name, gun_value in _children(guns):
                gun = _element(gun_value)
                if gun is None:
                    continue
                _record(components,
                        '%s:vehicleGun:%s' % (nation, gun_name),
                        _read_price(gun), conflicts)


def scan(client_root):
    """Return every baked table plus the catalogue census."""
    vehicles = {}
    components = {}
    shells = {}
    artefacts = {}
    conflicts = []
    with zipfile.ZipFile(str(client_root / SCRIPTS_PACKAGE)) as archive:
        for nation in NATIONS:
            listing = _sections(
                archive,
                'scripts/item_defs/vehicles/%s/list.xml' % nation)
            if listing is None:
                raise SystemExit('nation %s has no vehicle list' % nation)
            for vehicle, value in _children(listing):
                item = _element(value)
                if item is None:
                    continue
                _record(vehicles, '%s:%s' % (nation, vehicle),
                        _read_price(item), conflicts)
                _scan_vehicle(
                    archive, nation, vehicle, components, conflicts)

            for file_name, item_type_name in SHARED_COMPONENT_FILES.items():
                root = _sections(
                    archive,
                    'scripts/item_defs/vehicles/%s/components/%s.xml' % (
                        nation, file_name))
                shared = None if root is None else _element(
                    dict(_children(root)).get('shared'))
                if shared is None:
                    continue
                for name, value in _children(shared):
                    item = _element(value)
                    if item is None:
                        continue
                    _record(components,
                            '%s:%s:%s' % (nation, item_type_name, name),
                            _read_price(item), conflicts)

            root = _sections(
                archive,
                'scripts/item_defs/vehicles/%s/components/%s.xml' % (
                    nation, SHELL_FILE))
            if root is not None:
                for name, value in _children(root):
                    item = _element(value)
                    if item is None:
                        continue
                    _record(shells, '%s:%s' % (nation, name),
                            _read_price(item), conflicts)

        for file_name in COMMON_ARTEFACT_FILES:
            root = _sections(
                archive,
                'scripts/item_defs/vehicles/common/%s.xml' % file_name)
            if root is None:
                raise SystemExit('common %s definitions are absent' % file_name)
            for name, value in _children(root):
                item = _element(value)
                if item is None:
                    continue
                _record(artefacts, name, _read_price(item), conflicts)
    return vehicles, components, shells, artefacts, conflicts


def _render_table(name, table, comment):
    text = '\n# %s\n%s = {\n' % (comment, name)
    for key in sorted(table):
        credits_amount, gold, not_in_shop = table[key]
        text += '    %r: (%d, %d, %s),\n' % (
            key, credits_amount, gold, not_in_shop)
    return text + '}\n'


def render(version, build, vehicles, components, shells, artefacts):
    text = HEADER % {'version': version, 'build': build}
    text += _render_table(
        'VEHICLE_PRICES', vehicles, "Keyed '<nation>:<vehicle name>'.")
    text += _render_table(
        'COMPONENT_PRICES', components,
        "Keyed '<nation>:<item type name>:<component name>'.")
    text += _render_table(
        'SHELL_PRICES', shells, "Keyed '<nation>:<shell name>'.")
    text += _render_table(
        'ARTEFACT_PRICES', artefacts,
        'Equipment and optional devices are nation independent.')
    return text + FOOTER


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: bake_prices_0922.py <client root>')
    client_root = Path(sys.argv[1]).resolve()
    version, build = _client_identity(client_root)
    vehicles, components, shells, artefacts, conflicts = scan(client_root)
    if conflicts:
        for key, existing, found in conflicts:
            print('conflicting price for %s: %r and %r' % (
                key, existing, found), file=sys.stderr)
        raise SystemExit('the client defines an item at two different prices')
    for name, table in (('vehicle', vehicles), ('component', components),
                        ('shell', shells), ('artefact', artefacts)):
        if not table:
            raise SystemExit('no %s prices were found' % name)
        print('%s prices: %d' % (name, len(table)))
    gold_vehicles = [key for key, price in vehicles.items() if price[1]]
    print('gold vehicles: %d (%d not in shop)' % (
        len(gold_vehicles),
        len([key for key in gold_vehicles if vehicles[key][2]])))
    OUTPUT_PATH.write_text(
        render(version, build, vehicles, components, shells, artefacts),
        encoding='utf-8')
    print('written: %s' % OUTPUT_PATH)


if __name__ == '__main__':
    main()
