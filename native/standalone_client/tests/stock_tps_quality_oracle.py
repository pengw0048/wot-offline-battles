"""Compare the rewritten performancer with #1513's original SSE update block."""
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
    image = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
    base = image.OPTIONAL_HEADER.ImageBase
    data = image.get_memory_mapped_image()
    engine = Uc(UC_ARCH_X86, UC_MODE_32)
    engine.mem_map(base, (len(data) + 4095) // 4096 * 4096)
    engine.mem_write(base, data)
    engine.mem_map(0x30000000, 0x20000)
    state, stack, terminal = 0x30000000, 0x30010000, 0x3001f000
    rng = random.Random(1513)
    cases, expected = [], []
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    for index in range(4000):
        minimum = f32(rng.uniform(1, 20))
        maximum = f32(minimum + rng.uniform(.01, 60))
        values = [f32(rng.uniform(.00000001, 1)), f32(rng.uniform(0, 2)),
                  minimum, maximum, f32(rng.uniform(.01, 2)),
                  f32(rng.uniform(-100, 4000)), f32(rng.uniform(.1, 8))]
        disabled, frozen = int(index % 5 == 0), int(index % 7 == 0)
        engine.mem_write(state, b'\0' * 0x90)
        for offset, value in zip((0x60, 0, 0x38, 0x3c, 0x40, 0x24, 0x28), values):
            if offset:
                engine.mem_write(state + offset, struct.pack('<f', value))
        for offset, value in ((0x50, 4), (0x48, 5), (0x4c, 6), (0x54, 7)):
            engine.mem_write(state + offset, struct.pack('<f', value))
        engine.mem_write(state + 0x58, struct.pack('<I', 4 if disabled else 0))
        engine.mem_write(state + 0x8c, bytes([frozen]))
        engine.mem_write(stack, struct.pack('<If', terminal, values[1]))
        engine.reg_write(UC_X86_REG_ESP, stack)
        engine.reg_write(UC_X86_REG_ECX, state)
        # Calls to the original fa81xx publication helpers execute as well;
        # no algorithm instructions or native coefficient writes are replaced.
        engine.emu_start(0xfac2c0, terminal, count=3000)
        expected.append(' '.join(str(struct.unpack('<I', engine.mem_read(state + offset, 4))[0])
                                 for offset in (0x60, 0x50, 0x48, 0x4c, 0x54)) + ' ')
        cases.append(' '.join(format(value, '.9g') for value in values) + ' %d %d' % (disabled, frozen))
    inputs = '\n'.join(cases) + '\n'
    result = subprocess.run([str(probe), '--oracle'], input=inputs, text=True,
                            capture_output=True, check=True)
    actual = result.stdout.splitlines()
    if actual != expected:
        failures = [(index, a, b, cases[index]) for index, (a, b) in enumerate(zip(actual, expected)) if a != b]
        raise AssertionError('Native tPS performancer mismatch: %r' % failures[:5])
    if destination:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'inputs.txt').write_text(inputs)
        (destination / 'expected.txt').write_text('\n'.join(expected) + '\n')
    return {'originalFunction': '00fac2c0', 'cases': len(cases),
            'bitwiseMismatches': 0, 'status': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client', type=Path)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.client, arguments.probe, arguments.output)))
