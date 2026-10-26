"""Execute the original tPS shape samplers with the same random and circle tables."""
import argparse
import json
import math
from pathlib import Path
import random
import struct
import subprocess


def audit(client, probe, destination=None):
    import pefile
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX
    pe = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
    base = pe.OPTIONAL_HEADER.ImageBase
    image = pe.get_memory_mapped_image()
    engine = Uc(UC_ARCH_X86, UC_MODE_32)
    engine.mem_map(base, (len(image) + 4095) // 4096 * 4096)
    engine.mem_write(base, image)
    engine.mem_map(0x30000000, 0x50000)
    owner, output, circles, stack, terminal, round_stub = (0x30000000, 0x30001000, 0x30010000,
                                                        0x30020000, 0x30030000, 0x30040000)
    # The PE imports system CRT roundf. Supply only this library operation;
    # every shape algorithm instruction and random-table access runs unchanged.
    round_iat = next(symbol.address for dll in pe.DIRECTORY_ENTRY_IMPORT
                     for symbol in dll.imports if symbol.name == b'roundf')
    engine.mem_write(round_iat, struct.pack('<I', round_stub))
    engine.mem_write(round_stub, b'\xd9\x44\x24\x04\xc3')  # fld [esp+4]; ret

    def roundf(uc, unused_address, unused_size, unused_data):
        esp = uc.reg_read(UC_X86_REG_ESP)
        value = struct.unpack('<f', uc.mem_read(esp + 4, 4))[0]
        uc.mem_write(esp + 4, struct.pack('<f', math.floor(value + .5)))
    engine.hook_add(UC_HOOK_CODE, roundf, begin=round_stub, end=round_stub)
    rng = random.Random(1513)
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    values = [f32(rng.randrange(32768) * f32(1 / 32767)) for unused in range(4096)]
    values[50] = 1.0
    circle = [[0., 0.] for unused in range(80)]
    angle = 0.
    for index in range(72):
        circle[index] = [f32(math.cos(angle)), f32(math.sin(angle))]
        angle = f32(angle + f32(.0872664600610733))
    engine.mem_write(0x21436a0, struct.pack('<4096f', *values))
    engine.mem_write(0x1a5bf64, struct.pack('<I', circles))
    flat_circle = [value for pair in circle for value in pair]
    engine.mem_write(circles, struct.pack('<160f', *flat_circle))
    inputs = [' '.join(format(value, '.9g') for value in values),
              ' '.join(format(value, '.9g') for value in flat_circle)]
    expected = []
    for index in range(4000):
        shape, cursor, axis, sign = index % 7, rng.randrange(4096), rng.randrange(3), rng.choice([-1, 1])
        if index % 13 == 0:
            cursor = 49  # Exercise the native roundf(72) zero entry.
        coordinates = [f32(rng.uniform(-20, 20)) for unused in range(6)]
        position, extent = coordinates[:3], coordinates[3:]
        engine.mem_write(output, b'\0' * 0x80)
        engine.mem_write(owner, b'\0' * 0x820)
        engine.mem_write(0x21476a0, struct.pack('<I', cursor))
        engine.mem_write(0x2148908, struct.pack('<I', axis))
        engine.mem_write(0x1a5bf68, struct.pack('<i', sign))
        if shape < 2:
            engine.mem_write(output + 0x18, struct.pack('<3f', *position))
            engine.mem_write(owner + 0x160, struct.pack('<I3f', shape, *extent))
            arguments = struct.pack('<II', terminal, output)
            address, offset = 0xf9e010, 0x18
        else:
            engine.mem_write(output, struct.pack('<3f', *position))
            arguments = struct.pack('<II3f', terminal, output, *extent)
            if shape in (3, 4, 5, 6): arguments += struct.pack('<I', int(shape in (4, 6)))
            address = 0xfa1080 if shape == 2 else 0xfa1240 if shape in (3, 4) else 0xfa0f10
            offset = 0
        engine.mem_write(stack, arguments)
        engine.reg_write(UC_X86_REG_ESP, stack)
        engine.reg_write(UC_X86_REG_ECX, owner)
        engine.emu_start(address, terminal, count=1000)
        final = list(struct.unpack('<3I', engine.mem_read(output + offset, 12)))
        final.extend(struct.unpack('<I', engine.mem_read(0x21476a0, 4)))
        final.extend(struct.unpack('<I', engine.mem_read(0x2148908, 4)))
        final.extend(struct.unpack('<i', engine.mem_read(0x1a5bf68, 4)))
        expected.append(' '.join(str(value) for value in final))
        inputs.append(' '.join(str(value) for value in (shape, cursor, axis, sign)) + ' ' +
                      ' '.join(format(value, '.9g') for value in coordinates))
    data = '\n'.join(inputs) + '\n'
    actual = subprocess.run([str(probe)], input=data, capture_output=True, text=True, check=True).stdout.splitlines()
    if actual != expected:
        failures = [(i, a, b, inputs[i + 2]) for i, (a, b) in enumerate(zip(actual, expected)) if a != b]
        raise AssertionError('Original tPS shape mismatch: %r' % failures[:3])
    if destination:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'random-inputs.txt').write_text(data)
        (destination / 'random-expected.txt').write_text('\n'.join(expected) + '\n')
    return {'originalFunctions': ['00f9e010', '00fa1080', '00fa1240', '00fa0f10'],
            'cases': len(expected), 'bitwiseMismatches': 0, 'status': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client', type=Path)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.client, arguments.probe, arguments.output)))
