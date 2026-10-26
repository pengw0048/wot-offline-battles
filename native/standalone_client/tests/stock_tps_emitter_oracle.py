"""Compare emission scheduling against the original fa3980 instruction stream."""
import argparse
import json
from pathlib import Path
import random
import struct
import subprocess


def audit(client, probe, destination=None):
    import pefile
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX, UC_X86_REG_EIP
    pe = pefile.PE(str(Path(client) / 'WorldOfTanks.exe'))
    base = pe.OPTIONAL_HEADER.ImageBase
    image = pe.get_memory_mapped_image()
    engine = Uc(UC_ARCH_X86, UC_MODE_32)
    engine.mem_map(base, (len(image) + 4095) // 4096 * 4096)
    engine.mem_write(base, image)
    engine.mem_map(0x30000000, 0x40000)
    owner, vtable, context, stack, terminal, spawn = (0x30000000, 0x30001000, 0x30002000,
                                                   0x30020000, 0x30030000, 0x30031000)
    engine.mem_write(0x2132ee9, b'\1')
    engine.mem_write(0x2143681, b'\0')
    engine.mem_write(vtable + 0x7c, struct.pack('<I', spawn))
    calls = []

    def hook(uc, address, unused_size, unused_data):
        esp = uc.reg_read(UC_X86_REG_ESP)
        returned = struct.unpack('<I', uc.mem_read(esp, 4))[0]
        if address == spawn:
            calls.append('2:0')
            consumed = 4
        else:
            value = struct.unpack('<I', uc.mem_read(esp + 4, 4))[0]
            calls.append('%d:%d' % (0 if address == 0xfa3f90 else 1, value))
            consumed = 16
        # Only particle/child update calls are observed. All scheduling,
        # density arithmetic, branches and loop limits execute unmodified.
        uc.reg_write(UC_X86_REG_ESP, esp + consumed)
        uc.reg_write(UC_X86_REG_EIP, returned)

    for address in (0xfa3770, 0xfa3f90, spawn):
        engine.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)
    rng = random.Random(1513)
    inputs, expected = [], []
    f32 = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
    for index in range(4000):
        rate = f32(10 ** rng.uniform(-2, 5))
        remaining = f32(rng.uniform(-.05, .5))
        elapsed = f32(rng.uniform(0, 2))
        density = f32(rng.uniform(.01, 1))
        engine.mem_write(owner, b'\0' * 0x820)
        engine.mem_write(owner, struct.pack('<I', vtable))
        engine.mem_write(owner + 0x6d0, struct.pack('<f', rate))
        engine.mem_write(owner + 0x6d8, struct.pack('<f', remaining))
        engine.mem_write(0x1a5bebc, struct.pack('<f', density))
        engine.mem_write(stack, struct.pack('<IfII', terminal, elapsed, context, context))
        engine.reg_write(UC_X86_REG_ESP, stack)
        engine.reg_write(UC_X86_REG_ECX, owner)
        calls.clear()
        engine.emu_start(0xfa3980, terminal, count=20000)
        final = struct.unpack('<I', engine.mem_read(owner + 0x6d8, 4))[0]
        expected.append(str(final) + ' ' + ' '.join(calls) + (' ' if calls else ''))
        inputs.append(' '.join(format(value, '.9g') for value in (rate, remaining, elapsed, density)))
    data = '\n'.join(inputs) + '\n'
    actual = subprocess.run([str(probe), '--oracle'], input=data, capture_output=True,
                            text=True, check=True).stdout.splitlines()
    if actual != expected:
        failures = [(i, a, b, inputs[i]) for i, (a, b) in enumerate(zip(actual, expected)) if a != b]
        raise AssertionError('Original tPS emission mismatch: %r' % failures[:3])
    if destination:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'emitter-inputs.txt').write_text(data)
        (destination / 'emitter-expected.txt').write_text('\n'.join(expected) + '\n')
    scheduling = len(inputs)
    inputs, expected = [], []
    for index in range(2000):
        components = 1 if index % 2 else 4
        times = [f32(0), f32(.25), f32(.5), f32(1)]
        time = f32(rng.uniform(-.2, 1))
        values = [f32(rng.uniform(-100, 100)) for unused in range(4 * components)]
        selected = next(i for i in range(1, 4) if time <= times[i])
        engine.mem_write(owner, b'\0' * 0x80)
        engine.mem_write(owner + 0xc, struct.pack('<I', owner + 0x100))
        engine.mem_write(owner + 0x14, struct.pack('<I', owner + 0x200))
        engine.mem_write(owner + 0x100, struct.pack('<4f', *times))
        engine.mem_write(owner + 0x200, struct.pack('<%df' % len(values), *values))
        engine.mem_write(stack, struct.pack('<IfI', terminal, time, selected))
        engine.reg_write(UC_X86_REG_ESP, stack)
        engine.reg_write(UC_X86_REG_ECX, owner)
        engine.emu_start(0xfadfc0 if components == 1 else 0xfadf20, terminal, count=100)
        bits = struct.unpack('<%dI' % components, engine.mem_read(owner + 0x20, components * 4))
        expected.append('1 ' + ' '.join(str(value) for value in bits) + ' ')
        inputs.append('%d 4 ' % components + ' '.join(format(value, '.9g') for value in [time] + times + values))
    data = '\n'.join(inputs) + '\n'
    actual = subprocess.run([str(probe), '--curve'], input=data, capture_output=True,
                            text=True, check=True).stdout.splitlines()
    if actual != expected:
        failures = [(i, a, b) for i, (a, b) in enumerate(zip(actual, expected)) if a != b]
        raise AssertionError('Original tPS curve mismatch: %r' % failures[:3])
    if destination:
        (destination / 'curve-inputs.txt').write_text(data)
        (destination / 'curve-expected.txt').write_text('\n'.join(expected) + '\n')
    return {'originalFunction': ['00fa3980', '00fadfc0', '00fadf20'],
            'schedulingCases': scheduling, 'curveCases': len(inputs),
            'bitwiseMismatches': 0, 'status': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client', type=Path)
    parser.add_argument('probe', type=Path)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.client, arguments.probe, arguments.output)))
