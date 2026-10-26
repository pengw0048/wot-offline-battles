"""Compile the C API bridge and run original mod producers under CPython 2.7."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python27', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    native = root / 'native/standalone_client'
    include = subprocess.check_output([args.python27, '-c',
        'from distutils.sysconfig import get_python_inc; print(get_python_inc())'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='wot-offline-parameters-') as directory:
        output = str(Path(directory) / '_offline_vehicle_parameters_probe.so')
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wno-register',
            '-shared', '-fPIC', '-undefined', 'dynamic_lookup', '-Wall', '-Wextra', '-Werror',
            '-fno-fast-math', '-ffp-contract=off', '-I' + include,
            '-I' + str(native / 'include'), '-I' + str(native / 'bigworld'),
            str(native / 'bigworld/offline_vehicle_parameters.cpp'),
            str(native / 'tests/offline_vehicle_parameters_probe.cpp'), '-o', output], check=True)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([args.python27, str(native / 'tests/verify_offline_vehicle_parameters_py27.py'),
                        str(root), directory], env=environment, check=True)


if __name__ == '__main__':
    main()
