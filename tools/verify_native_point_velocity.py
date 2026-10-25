#!/usr/bin/env python3
"""Compare one recovered #1513 arithmetic kernel with locally supplied x86 code.

No game process is started. Only the verified, straight-line SSE instructions
between Python argument conversion and result wrapping enter Unicorn. Executable
bytes are read from the user's client and are never embedded in this repository.
The comparison covers a fixed finite-input corpus, with round-to-nearest and
gradual underflow. It does not establish parity for every float32 bit pattern,
overflow-generated NaNs, vehicle simulation or Windows lifecycle behavior.
"""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import sys

from inspect_client import inspect_client

IMAGE_BASE = 0x00400000
KERNEL_START = 0x00F4A481
KERNEL_END = 0x00F4A533
WRAPPER = 0x00F4A420
METHOD_ENTRY = 0x01A5B4B0
METHOD_NAME = 0x0174A9E4

# Exact instruction semantics and boundaries, rather than redistributed bytes.
INSTRUCTIONS = (
    (0xF4A481, 'mov', 'eax, dword ptr [edi + 0x910]'),
    (0xF4A487, 'movss', 'xmm1, dword ptr [ebp - 8]'),
    (0xF4A48C, 'add', 'eax, 0x1c8'),
    (0xF4A491, 'movss', 'xmm5, dword ptr [ebp - 0xc]'),
    (0xF4A496, 'movss', 'xmm7, dword ptr [ebp - 0x10]'),
    (0xF4A49B, 'subss', 'xmm1, dword ptr [eax + 0x178]'),
    (0xF4A4A3, 'subss', 'xmm5, dword ptr [eax + 0x174]'),
    (0xF4A4AB, 'movss', 'xmm2, dword ptr [eax + 0x114]'),
    (0xF4A4B3, 'movss', 'xmm6, dword ptr [eax + 0x118]'),
    (0xF4A4BB, 'movaps', 'xmm3, xmm2'),
    (0xF4A4BE, 'subss', 'xmm7, dword ptr [eax + 0x170]'),
    (0xF4A4C6, 'movss', 'xmm4, dword ptr [eax + 0x110]'),
    (0xF4A4CE, 'movaps', 'xmm0, xmm6'),
    (0xF4A4D1, 'mulss', 'xmm0, xmm5'),
    (0xF4A4D5, 'mulss', 'xmm3, xmm1'),
    (0xF4A4D9, 'mulss', 'xmm2, xmm7'),
    (0xF4A4DD, 'subss', 'xmm3, xmm0'),
    (0xF4A4E1, 'mulss', 'xmm6, xmm7'),
    (0xF4A4E5, 'movaps', 'xmm0, xmm4'),
    (0xF4A4E8, 'mulss', 'xmm4, xmm5'),
    (0xF4A4EC, 'mulss', 'xmm0, xmm1'),
    (0xF4A4F0, 'movss', 'xmm1, dword ptr [eax + 0x120]'),
    (0xF4A4F8, 'subss', 'xmm4, xmm2'),
    (0xF4A4FC, 'movss', 'xmm2, dword ptr [eax + 0x11c]'),
    (0xF4A504, 'subss', 'xmm6, xmm0'),
    (0xF4A508, 'movss', 'xmm0, dword ptr [eax + 0x124]'),
    (0xF4A510, 'addss', 'xmm2, xmm3'),
    (0xF4A514, 'addss', 'xmm0, xmm4'),
    (0xF4A518, 'addss', 'xmm1, xmm6'),
    (0xF4A51C, 'movss', 'dword ptr [ebp - 0x20], xmm0'),
    (0xF4A521, 'mov', 'eax, dword ptr [ebp - 0x20]'),
    (0xF4A524, 'mov', 'dword ptr [ebp - 0x14], eax'),
    (0xF4A527, 'lea', 'eax, [ebp - 0x1c]'),
    (0xF4A52A, 'unpcklps', 'xmm2, xmm1'),
    (0xF4A52D, 'push', 'eax'),
    (0xF4A52E, 'movq', 'qword ptr [ebp - 0x1c], xmm2'),
)


def read_kernel(executable):
    """Resolve reviewed PE virtual addresses without loading the executable."""
    payload = Path(executable).read_bytes()
    if len(payload) < 64 or payload[:2] != b'MZ':
        raise ValueError('not a DOS/PE executable')
    pe = struct.unpack_from('<I', payload, 0x3c)[0]
    if payload[pe:pe + 4] != b'PE\0\0':
        raise ValueError('invalid PE signature')
    machine, count = struct.unpack_from('<HH', payload, pe + 4)
    optional_size = struct.unpack_from('<H', payload, pe + 20)[0]
    optional = pe + 24
    if (machine != 0x14c or not 1 <= count <= 96 or optional_size < 96 or
            struct.unpack_from('<H', payload, optional)[0] != 0x10b or
            struct.unpack_from('<I', payload, optional + 28)[0] != IMAGE_BASE):
        raise ValueError('unexpected #1513 PE layout')
    sections = []
    for index in range(count):
        at = optional + optional_size + 40 * index
        virtual_size, rva, raw_size, offset = struct.unpack_from('<4I', payload, at + 8)
        flags = struct.unpack_from('<I', payload, at + 36)[0]
        sections.append((rva, raw_size, offset, flags))

    def read(address, size, executable=False):
        rva = address - IMAGE_BASE
        for section, length, offset, flags in sections:
            if section <= rva and rva + size <= section + length:
                if executable and not flags & 0x20000000:
                    raise ValueError('kernel is not in an executable PE section')
                at = offset + rva - section
                result = payload[at:at + size]
                if len(result) != size:
                    raise ValueError('truncated PE section')
                return result
        raise ValueError('reviewed address is outside the PE file sections')

    if read(METHOD_NAME, 17) != b'getPointVelocity\0':
        raise ValueError('native method name does not match #1513')
    if struct.unpack('<4I', read(METHOD_ENTRY, 16)) != (METHOD_NAME, WRAPPER, 3, 0):
        raise ValueError('native method registration does not match #1513')
    code = read(KERNEL_START, KERNEL_END - KERNEL_START, executable=True)
    check_instructions(code)
    return code


def check_instructions(code):
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    decoded = list(Cs(CS_ARCH_X86, CS_MODE_32).disasm(code, KERNEL_START))
    actual = tuple((item.address, item.mnemonic, item.op_str) for item in decoded)
    if (actual != INSTRUCTIONS or not decoded or
            decoded[-1].address + decoded[-1].size != KERNEL_END or
            len(code) != KERNEL_END - KERNEL_START):
        raise ValueError('native kernel instructions differ from the reviewed #1513 block')


class NativeKernel:
    """Map only the checked code slice and synthetic input/output memory."""

    def __init__(self, code):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
        from unicorn.x86_const import UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_MXCSR
        check_instructions(code)
        self.machine = Uc(UC_ARCH_X86, UC_MODE_32)
        self.machine.mem_map(KERNEL_START & ~0xfff, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.machine.mem_write(KERNEL_START, code)
        self.state = 0x30000000
        self.physics = self.state + 0x1000
        self.frame = 0x31000800
        self.machine.mem_map(self.state, 0x2000, UC_PROT_READ | UC_PROT_WRITE)
        self.machine.mem_map(self.frame & ~0xfff, 0x1000, UC_PROT_READ | UC_PROT_WRITE)
        self.machine.mem_write(self.state + 0x910, struct.pack('<I', self.physics))
        self.machine.reg_write(UC_X86_REG_EDI, self.state)
        self.machine.reg_write(UC_X86_REG_EBP, self.frame)
        self.stack_register = UC_X86_REG_ESP
        self.mxcsr_register = UC_X86_REG_MXCSR

    def evaluate(self, words):
        from unicorn.x86_const import UC_X86_REG_EIP
        if len(words) != 12:
            raise ValueError('expected twelve float32 words')
        point, origin, angular, linear = (words[i:i + 3] for i in (0, 3, 6, 9))
        machine = self.machine
        machine.mem_write(self.frame - 0x10, struct.pack('<3I', *point))
        machine.mem_write(self.physics + 0x338, struct.pack('<3I', *origin))
        machine.mem_write(self.physics + 0x2d8, struct.pack('<3I', *angular))
        machine.mem_write(self.physics + 0x2e4, struct.pack('<3I', *linear))
        machine.reg_write(self.stack_register, self.frame - 0x40)
        machine.reg_write(self.mxcsr_register, 0x1f80)
        machine.emu_start(KERNEL_START, KERNEL_END, timeout=100000, count=len(INSTRUCTIONS))
        if machine.reg_read(UC_X86_REG_EIP) != KERNEL_END:
            raise ValueError('native kernel did not stop at the verified result boundary')
        return struct.unpack('<3I', machine.mem_read(self.frame - 0x1c, 12))


def float_bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def generate_cases(random_count, seed):
    """Cover axes, signs, cancellation, subnormals and bounded physical scales."""
    cases = []

    def add(label, point, origin, angular, linear):
        cases.append((label, tuple(float_bits(x) for row in (point, origin, angular, linear) for x in row)))

    zero = (0.0, 0.0, 0.0)
    for sign in (0.0, -0.0):
        add('signed_zero', (sign,) * 3, (sign,) * 3, (sign,) * 3, (sign,) * 3)
    add('linear', (12.0, -3.0, 5.0), (2.0, 6.0, -9.0), zero, (3.0, -7.0, 11.0))
    add('at_origin', (2048.25, -10.5, 600.0), (2048.25, -10.5, 600.0), (1.0, -2.0, 3.0), (3.0, -7.0, 11.0))
    for axis in range(3):
        for point_axis in range(3):
            for sign in (-1.0, 1.0):
                angular = [0.0] * 3
                point = [0.0] * 3
                angular[axis] = sign
                point[point_axis] = 2.0
                add('axis_rotation', point, zero, angular, zero)
    for exponent in (-40, -20, 0, 20, 40):
        scale = math.ldexp(1.0, exponent)
        add('cancellation', (scale, -scale, scale), (scale, -scale, scale), (scale, scale, -scale), (1.0, -1.0, 0.0))
        add('exponent_range', (scale, -scale, scale), zero, (scale, scale, -scale), (scale, -scale, scale))
    tiny = struct.unpack('<f', struct.pack('<I', 1))[0]
    for sign in (-1.0, 1.0):
        add('subnormal', (tiny * sign, 2.0 * tiny, -tiny), zero, (1.0, -1.0, 1.0), zero)
    generator = random.Random(seed)
    for _ in range(random_count):
        origin = [generator.uniform(-10000.0, 10000.0) for _ in range(3)]
        point = [value + generator.uniform(-50.0, 50.0) for value in origin]
        angular = [generator.uniform(-20.0, 20.0) for _ in range(3)]
        linear = [generator.uniform(-1000.0, 1000.0) for _ in range(3)]
        add('random_physical_scale', point, origin, angular, linear)
    return cases


def compare(native_core, kernel, cases):
    request = ''.join(' '.join('%08x' % word for word in words) + '\n' for _, words in cases)
    result = subprocess.run([str(Path(native_core).resolve()), 'point-velocity-bits'],
                            input=request, text=True, capture_output=True, timeout=60, check=True)
    lines = result.stdout.splitlines()
    if len(lines) != len(cases):
        raise ValueError('native core returned %d rows for %d cases' % (len(lines), len(cases)))
    for index, ((label, words), line) in enumerate(zip(cases, lines)):
        fields = line.split()
        if len(fields) != 3 or any(len(value) != 8 or any(c not in '0123456789abcdefABCDEF' for c in value) for value in fields):
            raise ValueError('invalid native core result at row %d' % index)
        actual = tuple(int(value, 16) for value in fields)
        expected = kernel.evaluate(words)
        if actual != expected:
            raise ValueError('bitwise mismatch at row %d (%s): input=%s original=%s reconstructed=%s' % (
                index, label, ' '.join('%08x' % v for v in words),
                ' '.join('%08x' % v for v in expected), ' '.join('%08x' % v for v in actual)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', default=os.environ.get('WOT_0922_CLIENT'),
                        help='exact #1513 client directory (or WOT_0922_CLIENT)')
    parser.add_argument('--native-core', required=True, help='locally built wot-native-core executable')
    parser.add_argument('--random-cases', type=int, default=4096)
    parser.add_argument('--seed', type=int, default=1513)
    args = parser.parse_args(argv)
    if not args.client_root:
        parser.error('--client-root or WOT_0922_CLIENT is required')
    if not 0 <= args.random_cases <= 100000:
        parser.error('--random-cases must be between 0 and 100000')
    try:
        inspect_client(args.client_root)
        code = read_kernel(Path(args.client_root) / 'WorldOfTanks.exe')
        cases = generate_cases(args.random_cases, args.seed)
        compare(args.native_core, NativeKernel(code), cases)
    except ImportError as error:
        parser.exit(2, 'Analysis dependency missing: %s; install capstone and unicorn in an analysis environment.\n' % error)
    except (OSError, ValueError, struct.error, subprocess.SubprocessError) as error:
        parser.exit(1, 'Native kernel verification failed: %s\n' % error)
    print(json.dumps({'result': 'passed', 'comparison': 'bitwise float32',
                      'client_build': '0.9.22.0.1 #1513',
                      'kernel_start': hex(KERNEL_START), 'kernel_end_exclusive': hex(KERNEL_END),
                      'cases': len(cases), 'categories': dict(Counter(label for label, _ in cases)),
                      'scope': 'getPointVelocity arithmetic only; no game process or Python wrapper executed'},
                     indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
