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
    'prepare_native_client.py', 'prepare_native_static_models.py',
    'prepare_native_vehicle.py', 'bake_navigation_0922.py',
    'bake_destructibles_0922.py', 'bw_primitives_geometry.py',
    'inspect_client.py', 'packed_xml.py', 'space_bin_0922.py',
)


def package(client):
    if not (client / 'bwclient_h.exe').is_file():
        raise ValueError('client output does not contain bwclient_h.exe')
    target = client / 'tools'
    target.mkdir(exist_ok=True)
    for name in TOOL_FILES:
        shutil.copy2(ROOT / 'tools' / name, target / name)
    schema = ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922/navigation_graph_schema.py'
    shutil.copy2(schema, target / schema.name)
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
             'prepare-runtime\\python.exe -B tools\\prepare_native_client.py %*\r\n'
             'if not "%errorlevel%"=="0" pause\r\n')
    (client / 'Start-Client.cmd').write_bytes(entry.encode('ascii'))
    readme = '''INDEPENDENT NATIVE CLIENT - DEVELOPMENT BUILD

Extract the complete ZIP to a new writable folder on Windows 10/11.
Run Start-Client.cmd and enter the path to your installed Chinese HD
World of Tanks 0.9.22.0.1 #1513 client (the WorldOfTanks.exe folder).
The original installation is read only. Its game entry, entity scripts,
models, maps, interface and audio are resolved through the original paths.
The original executable is never started. Game assets are not distributed
in this package. The included Python runtime only prepares resource paths.

This rewrite is in development. A successful build does not establish
successful original GUI startup, complete map rendering or playable battles.
The extracted package folder contains prepare.log (preparation, exceptions
and native exit codes), standalone.log and python.log (when created).
Before each native launch, previous standalone.log and python.log files are
moved to logs/<UTC timestamp>/ in this package folder. prepare.log is appended.
On failure the console stays open. Preserve its error text, prepare.log,
the native logs, logs/ and any .dmp file if generated when reporting a failure.
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
