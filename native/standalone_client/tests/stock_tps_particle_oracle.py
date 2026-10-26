"""Compare native tPS fixed-step physics, curves and lifetime transitions by bits."""
import argparse
import json
from pathlib import Path
import random
import struct
import subprocess


def audit(client, probe, destination=None):
    import pefile
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
    from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX
    pe = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
    base = pe.OPTIONAL_HEADER.ImageBase
    image = pe.get_memory_mapped_image()
    engine = Uc(UC_ARCH_X86, UC_MODE_32)
    engine.mem_map(base, (len(image) + 4095) // 4096 * 4096)
    engine.mem_write(base, image)
    engine.mem_map(0x30000000, 0x40000)
    owner, parent, stack, terminal = 0x30000000, 0x30001000, 0x30020000, 0x30030000
    rng = random.Random(1513)
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    curve_values = ((.2, -1, .4), (.5, 1.2, .4), (1, .5, 2))
    for index, values in enumerate(curve_values):
        curve = parent + 0x1000 + index * 0x100
        engine.mem_write(curve, struct.pack('<I', 0x174e3a0))
        engine.mem_write(curve + 8, struct.pack('<II', 3, curve + 0x40))
        engine.mem_write(curve + 0x14, struct.pack('<I', curve + 0x60))
        engine.mem_write(curve + 0x40, struct.pack('<3f', 0, .5, 1))
        engine.mem_write(curve + 0x60, struct.pack('<3f', *values))
    inputs, expected = [], []
    for index in range(4000):
        elapsed = f32(rng.uniform(0, 1))
        special, paused, has_source = index % 2, int(index % 13 == 0), int(index % 3 != 0)
        flags = rng.choice([0, 0x80, 0x40, 0x201, 1, 0x4000, 0x10000, 0x2000, 0x16000])
        own_identity, identity = (-1 if index % 5 else index), index + (index % 7)
        children = index % 2
        values = [f32(rng.uniform(-20, 20)) for unused in range(6)] + [
            f32(rng.uniform(.2, 2)), f32(rng.uniform(.2, 2)), f32(rng.uniform(-.1, .2)),
            f32(rng.uniform(.05, 10)), f32(rng.uniform(0, 10)), f32(rng.uniform(-1, 1)),
            f32(rng.uniform(0, 1)), f32(rng.uniform(-.2, 3))] + [
            f32(rng.uniform(-10, 10)) for unused in range(9)]
        engine.mem_write(owner, b'\0' * 0x130)
        engine.mem_write(owner, struct.pack('<I', 0x174e8d8))
        engine.mem_write(owner + 0x18, struct.pack('<I', children))
        engine.mem_write(owner + 0x20, struct.pack('<I', flags))
        engine.mem_write(owner + 0x34, struct.pack('<I', parent))
        engine.mem_write(owner + 0xa8, struct.pack('<q', own_identity))
        engine.mem_write(0x1a5beb0, struct.pack('<q', identity))
        engine.mem_write(0x2143681, bytes([paused]))
        for offset, value in zip((0x38, 0x3c, 0x40, 0x48, 0x4c, 0x50, 0x44, 0x11c,
                                  0x6c, 0x70, 0x74, 0x78, 0x7c, 0xb0, 0xb4, 0xb8, 0xbc,
                                  0x110, 0x114, 0x118), values):
            engine.mem_write(owner + offset, struct.pack('<f', value))
        engine.mem_write(0x1a5bf04, struct.pack('<3f', *values[-3:]))
        for member, curve_index in ((0x7a8, 0), (0x794, 1), (0x798, 2)):
            engine.mem_write(parent + member, struct.pack('<I', parent + 0x1000 + curve_index * 0x100 if has_source else 0))
        engine.mem_write(stack, struct.pack('<If', terminal, elapsed))
        engine.reg_write(UC_X86_REG_ESP, stack)
        engine.reg_write(UC_X86_REG_ECX, owner)
        engine.emu_start(0xfaa760 if special else 0xfaa540, terminal, count=100000)
        state = [struct.unpack('<I', engine.mem_read(owner + offset, 4))[0] for offset in
                 (0x20, 0x38, 0x3c, 0x40, 0x48, 0x4c, 0x50, 0x44, 0x11c, 0x6c, 0x74, 0xb0)]
        expected.append(' '.join(str(value) for value in state) + ' ')
        inputs.append(' '.join([format(elapsed, '.9g')] + [str(value) for value in
                     (special, paused, has_source, flags, own_identity, identity, children)] +
                     [format(value, '.9g') for value in values]))
    data = '\n'.join(inputs) + '\n'
    actual = subprocess.run([str(probe)], input=data, capture_output=True, text=True, check=True).stdout.splitlines()
    if actual != expected:
        failures = [(i, a, b, inputs[i]) for i, (a, b) in enumerate(zip(actual, expected)) if a != b]
        raise AssertionError('Original tPS physics mismatch: %r' % failures[:3])
    if destination:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'particle-inputs.txt').write_text(data)
        (destination / 'particle-expected.txt').write_text('\n'.join(expected) + '\n')
    return {'originalFunctions': ['00faa540', '00faa760', '00fa3770', '00fa3030'],
            'cases': len(inputs), 'bitwiseMismatches': 0, 'status': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client', type=Path)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.client, arguments.probe, arguments.output)))
