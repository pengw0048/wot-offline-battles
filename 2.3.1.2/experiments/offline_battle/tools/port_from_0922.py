#!/usr/bin/env python3
"""Copy 0.9.22 modules into this port without changing their law.

Run it again after any upstream change; it rewrites the copies in place.
Only three things are rewritten: tabs become four spaces, the package
path becomes this port's, and the version note in the header. Anything
else that differs between the two clients belongs in an adapter here,
never in the copy.
"""
import ast
import pathlib
import re
import sys

SOURCE = pathlib.Path('/Users/peng/wot-offline-2311-probe/0.9.22/src/res/'
                      'scripts/client/gui/mods/offline_lan_0922')
TARGET = pathlib.Path(__file__).resolve().parents[1] / (
    'src/res/scripts/client/gui/mods/offline_battle_2312')
OLD_PACKAGE = 'gui.mods.offline_lan_0922'
NEW_PACKAGE = 'gui.mods.offline_battle_2312'

# Every module copied whole, with the one-line reason it is here.
MODULES = {
    'ballistics.py': 'Shell trajectory maths',
    'battle_feedback.py': 'Battle event reporting',
    'combat_rules.py': 'Armour and damage law',
    'device_damage.py': 'Module and crew damage law',
    'gun_mechanics.py': 'Gun state and dispersion',
    'internal_geometry.py': 'Interior box geometry',
    'internal_layout_store.py': 'Interior layout storage',
    'projectile_manager.py': 'Projectile authority',
    'projectile_runtime.py': 'Projectile stepping helpers',
    'spawn_planner.py': 'Spawn placement',
    'spotting.py': 'Spotting law',
    'tank_collision.py': 'Vehicle-against-vehicle collision',
    'vehicle_physics.py': 'Vehicle motion law',
    'world_collision.py': 'Horizontal world collision',
    'ai/cover.py': 'Cover scoring',
    'ai/driver.py': 'Bot driving',
    'ai/maps.py': 'Tactical map index',
    'ai/maps_extra.py': 'Tactical map data',
    'ai/maps_group_a.py': 'Tactical map data',
    'ai/maps_group_b.py': 'Tactical map data',
    'ai/maps_group_c.py': 'Tactical map data',
    'ai/maps_0922_extra.py': 'Tactical map data',
    'ai/reviewed_routes_20260811.py': 'Reviewed route data maps.py imports',
    'ai/navigation.py': 'Bot navigation',
    'ai/planner.py': 'Bot planning',
    'ai/adapter.py': 'Bot driver adapter',
    'critical_damage.py': 'Critical damage and crew injury law',
    'destructibles_authority.py': 'Destructible authority',
    'destructibles_compat.py': 'Destructible compatibility',
    'destructibles_sensor.py': 'Destructible sensing',
    'foliage.py': 'Foliage bending',
    'internal_hit_layouts.py': 'Interior hit layouts',
    'internal_layout_profiles.py': 'Interior layout profiles',
    'map_catalog.py': 'Map catalogue',
    'user_config.py': 'User data paths',
}

# vehicle_physics keeps this port's name, because motion.py is what the
# driver imports and the name is load-bearing across the package.
RENAMED = {'vehicle_physics.py': 'motion.py'}


def dedent(text):
    lines = []
    for line in text.split('\n'):
        depth = 0
        while line.startswith('\t'):
            depth += 1
            line = line[1:]
        lines.append((('    ' * depth) + line).rstrip())
    return '\n'.join(lines)


def convert(path, note):
    text = dedent(SOURCE.joinpath(path).read_text())
    text = text.replace(OLD_PACKAGE, NEW_PACKAGE)
    text = text.replace('#1513', '2.3.1.2')
    for source_name, target_name in RENAMED.items():
        stem = source_name[:-3]
        text = re.sub(r'\b%s\b' % stem, target_name[:-3], text)
    tree = ast.parse(text)
    lines = text.split('\n')
    header = ('"""%s, taken from the 0.9.22 port.\n\n'
              'The law is unchanged. Version differences belong in the\n'
              'adapters in this package, never in this file.\n"""\n' % note)
    futures = [line for line in lines
               if line.startswith('from __future__ import')]
    if 'from __future__ import absolute_import' not in futures:
        futures.append('from __future__ import absolute_import')
    body = [line for line in lines
            if not line.startswith('from __future__ import')]
    if (tree.body and isinstance(tree.body[0], ast.Expr) and
            isinstance(tree.body[0].value, ast.Constant) and
            isinstance(tree.body[0].value.value, str)):
        original = tree.body[0].value.value.strip()
        header = header[:-4] + '\nContract, from the original module:\n%s\n"""\n' % original
        start = tree.body[0].lineno - 1
        end = tree.body[0].end_lineno
        keep = lines[:start] + lines[end:]
        body = [line for line in keep
                if not line.startswith('from __future__ import')]
    return header + '\n'.join(sorted(set(futures))) + '\n' + (
        '\n'.join(body).lstrip('\n')).rstrip('\n') + '\n'


def main():
    written = []
    for path, note in sorted(MODULES.items()):
        target_name = RENAMED.get(path, path)
        destination = TARGET / target_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = convert(path, note)
        ast.parse(text)
        destination.write_text(text)
        written.append(target_name)
    package_init = TARGET / 'ai' / '__init__.py'
    if not package_init.exists():
        package_init.write_text('')
        written.append('ai/__init__.py')
    for name in written:
        print(name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
