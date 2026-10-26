#!/usr/bin/env python3
"""Bundle local resource-preparation tools without copying WoT assets."""

import argparse
import io
from pathlib import Path
import shutil
from urllib.request import urlopen
import zipfile


ROOT = Path(__file__).resolve().parent.parent
PYTHON_VERSION = '3.14.9'
PYTHON_URL = ('https://www.python.org/ftp/python/{0}/'
              'python-{0}-embed-win32.zip').format(PYTHON_VERSION)
TOOL_FILES = (
    'prepare_native_prototype.py', 'prepare_native_scene.py',
    'prepare_native_vehicle.py', 'bake_navigation_0922.py',
    'bw_primitives_geometry.py', 'inspect_client.py', 'packed_xml.py',
    'space_bin_0922.py',
)


def package(client):
    if not (client / 'bwclient_h.exe').is_file():
        raise ValueError('client output does not contain bwclient_h.exe')
    target = client / 'tools'
    target.mkdir(exist_ok=True)
    for name in TOOL_FILES:
        shutil.copy2(ROOT / 'tools' / name, target / name)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo')
    for name in ('navigation_baseline_f5b0173', 'wot_space_bin_utils'):
        shutil.copytree(ROOT / 'tools/vendor' / name, target / 'vendor' / name,
                        dirs_exist_ok=True, ignore=ignore)
    # Keep the preparation tool's repository-relative bootstrap input intact.
    bootstrap = Path('native/standalone_client/bigworld/res')
    shutil.copytree(ROOT / bootstrap, client / bootstrap,
                    dirs_exist_ok=True, ignore=ignore)
    runtime = client / 'prepare-runtime'
    runtime.mkdir(exist_ok=True)
    print('Bundling official Python %s for local resource preparation' % PYTHON_VERSION,
          flush=True)
    with urlopen(PYTHON_URL, timeout=60) as response:
        archive = response.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as package_zip:
        package_zip.extractall(runtime)
    # The embedded distribution deliberately ignores global Python installs.
    (runtime / 'python314._pth').write_text('python314.zip\n.\n../tools\n',
                                          encoding='ascii')
    (runtime / 'SOURCE.txt').write_text(PYTHON_URL + '\n', encoding='ascii')
    entry = ('@echo off\r\ncd /d "%~dp0"\r\n'
             'prepare-runtime\\python.exe -B tools\\prepare_native_prototype.py %*\r\n'
             'if errorlevel 1 pause\r\n')
    (client / 'Start-Prototype.cmd').write_bytes(entry.encode('ascii'))
    readme = '''INDEPENDENT NATIVE CLIENT - WINDOWS PROTOTYPE

Extract the complete ZIP to a new writable folder on Windows 10/11.
Do not copy a previous prototype's prepared folder into this version.
Double-click Start-Prototype.cmd and paste the path to your installed
Chinese HD World of Tanks 0.9.22.0.1 #1513 client (the folder containing
WorldOfTanks.exe). The first run prepares Karelia and the stock T-34 locally,
then starts the prototype. Later runs reuse the prepared files.
Your original installation is read only; its executable is not started.
No original game maps, vehicles, or textures are included in this download.
The package includes public engine resources, runtime DLLs, and a portable
Python used only by the preparation tools. No separate Python install is needed.

CONTROLS
W/S: forward/reverse. A/D: steer. Space: handbrake.
Mouse: camera aim. Left click: fire. Arrows/Page Up/Page Down: move camera.
Alt+F4: close. Restart to restore target health and ammunition.

CURRENT LIMITS
This is a local driving and stationary-target firing prototype.
Buildings, trees, water, and audio are not connected. The sky uses its authored
static image; original animated clouds and PBS materials are not reproduced.
Driving uses the ported offline controller and terrain support.
The full retail suspension/contact solver, airborne motion, track animation,
turret articulation, crew/dispersion modifiers, ricochet continuation, HE
area damage, Bots, and multiplayer synchronization are pending.
Firing follows the camera aim and is not constrained to the visible barrel.
The hosted Windows startup test does not prove original resource rendering
or gameplay. Preserve standalone.log and any .dmp file if a local run fails.
Matching PDB symbols accompany the executable for crash investigation.
'''
    (client / 'README-FIRST.txt').write_bytes(readme.replace('\n', '\r\n').encode('ascii'))
    print('Packaged preparation tools; no original game resources were copied.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-output', type=Path, required=True)
    args = parser.parse_args()
    package(args.client_output.resolve())


if __name__ == '__main__':
    main()
