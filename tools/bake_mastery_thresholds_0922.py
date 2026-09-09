#!/usr/bin/env python3
"""Bake the retail mastery-badge and Marks-of-Excellence thresholds.

Wargaming computes both quantities on its own servers and never ships them in
the client, so an offline battle cannot reproduce them from client data.  This
tool captures the published retail tables once and writes them into a generated
module the port reads at runtime.

Two independent retail-derived sources are used, both keyed so that the join to
the pinned client is exact rather than a name guess:

``poliroid.me/gunmarks``
    The data service behind the Marks of Excellence mods.  Its vehicle list
    carries the client's own internal vehicle name next to the integer compact
    descriptor, and its bulk endpoint returns average combined damage for a
    requested list of percentiles.  Combined damage is the retail definition:
    damage dealt plus the *largest* of the track, spotting and stun assist
    values, not their sum.

``protanki.eu/en/stats/masters``
    Base XP required for each of the four mastery classes.  Every row carries
    the client's internal vehicle name in its icon URL.

Both serve the European server's current population.  Wargaming never published
0.9.22-era tables and no archive holds them, so these are the closest retail
numbers that exist; the generated module records the fetch date and each
source's own version stamp.

    python3 tools/bake_mastery_thresholds_0922.py --out /tmp/mastery_catalog.py
    python3 tools/bake_mastery_thresholds_0922.py --client "$WOT_0922_CLIENT"

Without ``--client`` every vehicle the sources know about is baked.  With it,
the table is restricted to the pinned client's roster and the vehicles the
sources do not cover are reported.
"""

import argparse
import datetime
import html
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packed_xml import read_packed_xml, PackedElement

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'src', 'res', 'scripts', 'client'))
from gui.mods.offline_lan_0922.vehicle_configuration import (
    is_standard_battle_vehicle)


MARKS_API = ('https://poliroid.me/gunmarks/api/v2/data/%s/vehicles/'
             '5,10,15,20,25,30,35,40,45,50,55,60,65,70,75,80,85,90,95,100')
VEHICLES_API = 'https://poliroid.me/gunmarks/api/v2/vehicles/%s/en'
MASTERS_PAGE = 'https://protanki.%s/en/stats/masters'
PERCENTILES = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100)

# The client advertises vehicles in this nation order; the integer compact
# descriptor packs the nation index into bits 4-7 and the item type into 0-3.
NATIONS = ('ussr', 'germany', 'usa', 'china', 'france', 'uk', 'japan',
           'czech', 'sweden', 'poland')
VEHICLE_ITEM_TYPE = 1
SCRIPTS_PACKAGE = os.path.join('res', 'packages', 'scripts.pkg')

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'src', 'res', 'scripts', 'client', 'gui', 'mods', 'offline_lan_0922',
    'mastery_catalog.py')

HEADER = '''# -*- coding: utf-8 -*-
"""Generated retail mastery-badge and Marks-of-Excellence thresholds.

Do not edit by hand.  Regenerate with::

    python3 tools/bake_mastery_thresholds_0922.py

Wargaming computes both tables on its servers from the live player population
and never ships them in the client, so these are captured retail values, not
client data.  ``MASTERY_XP`` holds the base XP a single battle needs for the
III / II / I / Ace classes.  ``MARKS_DAMAGE`` holds average combined damage -
damage dealt plus the largest single assist component - at the percentiles in
``MARKS_PERCENTILES``; retail awards one, two and three marks at 65, 85 and 95.

Keys are the client's integer compact descriptor, so they join exactly to
``items.vehicles.makeIntCompactDescrByID``.
"""

# Retail source provenance, recorded so a stale table is recognisable.  The
# fetch date is separate so re-baking unchanged data produces a one-line diff.
SOURCE = %(source)r
FETCHED = %(fetched)r
'''


def fetch_json(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'wot-offline-battles-baker/1.0',
        'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode('utf8'))


def fetch_text(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'wot-offline-battles-baker/1.0'})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read().decode('utf8', 'replace')


def read_marks(server):
    payload = fetch_json(MARKS_API % server)
    if payload.get('status') != 'ok':
        raise ValueError('gun marks endpoint returned %r' % payload.get('status'))
    body = payload['data']
    table = {}
    for entry in body['data']:
        marks = entry['marks']
        try:
            row = tuple(int(marks[str(percentile)])
                        for percentile in PERCENTILES)
        except KeyError as error:
            raise ValueError('vehicle %s lacks percentile %s'
                             % (entry.get('id'), error))
        if sorted(row) != list(row):
            raise ValueError('vehicle %s percentile curve is not monotonic'
                             % entry.get('id'))
        table[int(entry['id'])] = row
    return table, body.get('meta', {})


def read_vehicle_names(server):
    payload = fetch_json(VEHICLES_API % server)
    if payload.get('status') != 'ok':
        raise ValueError('vehicle endpoint returned %r' % payload.get('status'))
    body = payload['data']
    names = {}
    for row in body['data']['vehicles']:
        names[str(row[1]).lower()] = int(row[0])
    return names, body.get('meta', {})


def read_mastery(server_domain, name_to_intcd):
    text = fetch_text(MASTERS_PAGE % server_domain)
    table = {}
    unmatched = []
    for block in re.findall(r'(?s)<tr[^>]*>(.*?)</tr>', text):
        cells = [re.sub(r'\s+', ' ', html.unescape(
                     re.sub(r'(?s)<[^>]+>', '', cell))).strip()
                 for cell in re.findall(r'(?s)<t[dh][^>]*>(.*?)</t[dh]>', block)]
        icons = [src for src in re.findall(r'(?:src|data-src)="([^"]+)"', block)
                 if '/tanks/' in src]
        if len(cells) < 9 or not icons:
            continue
        name = icons[0].rsplit('/', 1)[-1].lower()
        try:
            row = tuple(int(cells[index]) for index in (5, 6, 7, 8))
        except ValueError:
            continue
        if sorted(row) != list(row):
            # A vehicle whose classes collapse (too few battles) is unusable.
            continue
        intcd = name_to_intcd.get(name)
        if intcd is None:
            unmatched.append(name)
            continue
        table[intcd] = row
    if not table:
        raise ValueError('mastery page yielded no rows; layout changed')
    return table, unmatched


def read_client_roster(client_path):
    """Return ``{intcd: (nation, name, tier, tags)}`` for the pinned client."""
    if os.path.isdir(client_path):
        package_path = os.path.join(client_path, SCRIPTS_PACKAGE)
    else:
        package_path = client_path
    roster = {}
    with zipfile.ZipFile(package_path) as package:
        for nation_id, nation in enumerate(NATIONS):
            member = 'scripts/item_defs/vehicles/%s/list.xml' % nation
            root = read_packed_xml(package.read(member))
            for raw_name, node in root.children:
                name = raw_name.decode('utf8') if isinstance(
                    raw_name, bytes) else raw_name
                element = _unwrap(node)
                children = dict(
                    ((key.decode('utf8') if isinstance(key, bytes) else key),
                     _unwrap(value))
                    for key, value in getattr(element, 'children', ()))
                if 'id' not in children:
                    continue
                vehicle_id = int(_scalar(children['id']))
                tier = int(_scalar(children['level'])) if 'level' in children \
                    else 0
                tags = str(_scalar(children.get('tags', ''))).split()
                intcd = ((vehicle_id << 8) | (nation_id << 4) |
                         VEHICLE_ITEM_TYPE)
                roster[intcd] = (nation, name, tier, tags)
    if not roster:
        raise ValueError('client roster is empty; wrong package?')
    return roster


def _unwrap(value):
    while hasattr(value, 'value') and isinstance(value.value, PackedElement):
        value = value.value
    return value


def _scalar(value):
    value = getattr(value, 'value', value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf8', 'replace')
    return value


VEHICLE_CLASS_TAGS = ('lightTank', 'mediumTank', 'heavyTank', 'AT-SPG', 'SPG')


def standard_battle_roster(roster):
    """Use the garage's eligibility rule, including playable secret tanks."""
    vehicle_type = namedtuple('VehicleType', 'name tags')
    return dict((intcd, row) for intcd, row in roster.items()
                if is_standard_battle_vehicle(vehicle_type(
                    '%s:%s' % (row[0], row[1]), row[3])))


def vehicle_class(tags):
    for tag in VEHICLE_CLASS_TAGS:
        if tag in tags:
            return tag
    return ''


def median_rows(rows):
    """Return the element-wise median of equal-length integer rows."""
    ordered = list(zip(*rows))
    result = []
    for column in ordered:
        values = sorted(column)
        middle = len(values) // 2
        if len(values) % 2:
            result.append(int(values[middle]))
        else:
            result.append(int((values[middle - 1] + values[middle]) // 2))
    return tuple(result)


def build_fallbacks(table, roster):
    """Group covered rows by (tier, class) and take their median.

    A handful of vehicles the pinned client ships - Chinese-server exclusives
    and vehicles retail has since removed - have no retail row of their own.
    Their fallback is the median of the retail rows for the same tier and
    class, so the bar still comes from retail data rather than a guess.
    """
    groups = {}
    for intcd, row in table.items():
        if intcd not in roster:
            continue
        unused_nation, unused_name, tier, tags = roster[intcd]
        key = (tier, vehicle_class(tags))
        if not key[1] or not tier:
            continue
        groups.setdefault(key, []).append(row)
    return dict((key, median_rows(rows)) for key, rows in groups.items())


def render(mastery, marks, mastery_fallback, marks_fallback, profiles,
           source, fetched):
    lines = [HEADER % {'source': source, 'fetched': fetched}]
    lines.append('MARKS_PERCENTILES = %r\n' % (PERCENTILES,))
    lines.append('# Base XP for mastery classes III, II, I and Ace.')
    lines.append('MASTERY_XP = {')
    for intcd in sorted(mastery):
        lines.append('    %d: %r,' % (intcd, mastery[intcd]))
    lines.append('}\n')
    lines.append('# Average combined damage at MARKS_PERCENTILES.')
    lines.append('MARKS_DAMAGE = {')
    for intcd in sorted(marks):
        lines.append('    %d: %r,' % (intcd, marks[intcd]))
    lines.append('}\n')
    lines.append('# Median of the retail rows for the same (tier, class), used')
    lines.append('# only for the vehicles retail has no row for at all.')
    lines.append('MASTERY_XP_FALLBACK = {')
    for key in sorted(mastery_fallback):
        lines.append('    %r: %r,' % (key, mastery_fallback[key]))
    lines.append('}\n')
    lines.append('MARKS_DAMAGE_FALLBACK = {')
    for key in sorted(marks_fallback):
        lines.append('    %r: %r,' % (key, marks_fallback[key]))
    lines.append('}\n')
    lines.append('# The pinned client\'s own tier and class for every playable')
    lines.append('# vehicle, so the Tier V-X gate and the fallback key need no')
    lines.append('# loaded item definitions at runtime.')
    lines.append('VEHICLE_PROFILE = {')
    for intcd in sorted(profiles):
        lines.append('    %d: %r,' % (intcd, profiles[intcd]))
    lines.append('}')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', default=os.environ.get('WOT_0922_CLIENT'),
                        help='client directory or scripts.pkg to restrict the '
                             'table to the pinned roster')
    parser.add_argument('--server', default='eu',
                        help='gun marks server key (default: eu)')
    parser.add_argument('--server-domain', default='eu',
                        help='protanki domain suffix (default: eu)')
    parser.add_argument('--out', default=OUTPUT_PATH,
                        help='output module path')
    options = parser.parse_args(argv)

    marks, marks_meta = read_marks(options.server)
    names, names_meta = read_vehicle_names(options.server)
    mastery, unmatched = read_mastery(options.server_domain, names)
    print('gun marks: %d vehicles (version %s)'
          % (len(marks), marks_meta.get('version')))
    print('vehicle list: %d names (version %s)'
          % (len(names), names_meta.get('version')))
    print('mastery: %d vehicles (%d rows unmatched by name)'
          % (len(mastery), len(unmatched)))

    mastery_fallback = {}
    marks_fallback = {}
    profiles = {}
    if options.client:
        roster = read_client_roster(options.client)
        print('client roster: %d vehicles' % len(roster))
        playable = standard_battle_roster(roster)
        marks = dict((k, v) for k, v in marks.items() if k in playable)
        mastery = dict((k, v) for k, v in mastery.items() if k in playable)
        mastery_fallback = build_fallbacks(mastery, playable)
        marks_fallback = build_fallbacks(marks, playable)
        profiles = dict(
            (intcd, (row[2], vehicle_class(row[3])))
            for intcd, row in playable.items())
        missing_mastery = sorted(set(playable) - set(mastery))
        missing_marks = sorted(intcd for intcd in playable
                               if playable[intcd][2] >= 5 and
                               intcd not in marks)
        tier5 = [intcd for intcd in playable if playable[intcd][2] >= 5]
        print('covered: mastery %d/%d, marks (tier 5+) %d/%d'
              % (len(mastery), len(playable),
                 len([intcd for intcd in tier5 if intcd in marks]),
                 len(tier5)))
        for intcd in missing_mastery:
            print('  no mastery row: %s %s (tier %d)'
                  % (roster[intcd][0], roster[intcd][1], roster[intcd][2]))
        for intcd in missing_marks:
            print('  no marks row: %s %s (tier %d)'
                  % (roster[intcd][0], roster[intcd][1], roster[intcd][2]))
        print('fallback groups: mastery %d, marks %d'
              % (len(mastery_fallback), len(marks_fallback)))

    source = ('mastery: protanki.%s/en/stats/masters; marks: '
              'poliroid.me/gunmarks api v2 server=%s data version %s, '
              'vehicle list version %s'
              % (options.server_domain, options.server,
                 marks_meta.get('version'), names_meta.get('version')))
    if not profiles:
        print('WARNING: without --client the table has no tier map and no '
              'fallback rows; do not ship that output')
    text = render(mastery, marks, mastery_fallback, marks_fallback, profiles,
                  source, datetime.date.today().isoformat())
    with io.open(options.out, 'w', encoding='utf8', newline='\n') as handle:
        handle.write(text)
    print('wrote %s (%d bytes)' % (options.out, len(text)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
