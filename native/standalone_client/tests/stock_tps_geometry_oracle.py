"""Compare original fa1920 double angle wrapping and 1440-entry cache lookup."""
import argparse
import json
from pathlib import Path
import random
import struct
import subprocess


def audit(client, probe, destination=None):
    import pefile
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
    from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX, UC_X86_REG_EAX
    pe = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
    base = pe.OPTIONAL_HEADER.ImageBase
    image = pe.get_memory_mapped_image()
    engine = Uc(UC_ARCH_X86, UC_MODE_32)
    engine.mem_map(base, (len(image) + 4095) // 4096 * 4096)
    engine.mem_write(base, image)
    engine.mem_map(0x30000000, 0x40000)
    owner, stack, terminal = 0x30000000, 0x30020000, 0x30030000
    rng = random.Random(1513)
    values = [struct.unpack('<f', struct.pack('<f', rng.uniform(-500, 500)))[0] for unused in range(4000)]
    expected = []
    for angle in values:
        engine.mem_write(stack, struct.pack('<If', terminal, angle))
        engine.reg_write(UC_X86_REG_ESP, stack)
        engine.reg_write(UC_X86_REG_ECX, owner)
        engine.emu_start(0xfa1920, terminal, count=2000)
        expected.append(str((engine.reg_read(UC_X86_REG_EAX) - owner - 4) // 52))
    data = '\n'.join(format(value, '.9g') for value in values) + '\n'
    actual = subprocess.run([str(probe), '--oracle'], input=data, capture_output=True, text=True, check=True).stdout.splitlines()
    if actual != expected:
        failures = [(index, a, b) for index, (a, b) in enumerate(zip(actual, expected)) if a != b]
        raise AssertionError('Original tPS angle cache mismatch: %r' % failures[:3])
    if destination:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'geometry-inputs.txt').write_text(data)
        (destination / 'geometry-expected.txt').write_text('\n'.join(expected) + '\n')
    return {'originalFunction': '00fa1920', 'cases': len(values), 'mismatches': 0, 'status': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client', type=Path)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.client, arguments.probe, arguments.output)))
