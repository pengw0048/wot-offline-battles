"""Audit recovered constraint kernels against exact #1513 x86 instructions.

Requires pefile and unicorn, an x86 C++ compiler (Rosetta on Apple Silicon),
and WOT_0922_CLIENT. Imported D3DX normal transformation is an explicit oracle
boundary. RCPPS is compared within its hardware estimate precision; the solver
itself, callback-visible loads, aggregation and teardown are exact binary32.
No original client process is started.
"""
import argparse
from pathlib import Path
import platform
import subprocess
import tempfile
import ctypes as C
import struct
import random
import os
import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ESP, UC_X86_REG_ECX, UC_X86_REG_EIP
pe = None
lib = None

class Row(C.Structure):
    _fields_ = [('c', C.c_float * 36), ('a', C.c_uint32), ('b', C.c_uint32), ('s', C.c_uint32)]
pack = lambda fmt, *x: struct.pack('<' + fmt, *x)
unpack = lambda fmt, x: struct.unpack('<' + fmt, x)

def check(seed, groups, mid, it):
    r = random.Random(seed)
    N = len(groups)
    B = 4
    S = max(N, 1)
    base = 0x4000000
    ctx = base
    sp = base + 65536
    sources = base + 0x20000
    rowbase = base + 0x30000
    bodybase = base + 0x50000
    u = Uc(UC_ARCH_X86, UC_MODE_32)
    u.mem_map(pe.OPTIONAL_HEADER.ImageBase, pe.OPTIONAL_HEADER.SizeOfImage + 4095 & ~4095)
    u.mem_write(pe.OPTIONAL_HEADER.ImageBase, pe.get_memory_mapped_image())
    u.mem_map(base, 0x80000)
    u.mem_map(0x8000000, 65536)

    def w(addr, fmt, *v):
        u.mem_write(addr, pack(fmt, *v))
    w(ctx + 40, 'II', mid, it)
    w(ctx + 0xac, 'IIII', sp, B, 0, sources)
    w(ctx + 0xbc, 'I', 1)
    w(ctx + 16, 'III', base + 0x60000, base + 0x60014, base + 0x60014)
    w(ctx + 28, 'I', 1)
    w(base + 0x60000, 'IIIII', base + 0x61000, base + 0x61000 + S * 4, base + 0x61000 + S * 4, ctx, 0)
    w(base + 0x61000, 'I' * S, *range(S))
    w(ctx + 52, 'II', base + 0x62000, base + 0x62004)
    w(base + 0x62000, 'I', base + 0x62010)
    w(base + 0x62010, 'I', base + 0x62020)
    w(base + 0x62020, 'I', base + 0x70000)
    u.mem_write(base + 0x70000, b'\xc3')
    mid_actual = []

    def hook(uc, address, size, user):
        mid_actual[:] = [unpack('f', uc.mem_read(bodybase + 0xe0, 4))[0], unpack('f', uc.mem_read(base + 0x60010, 4))[0]]
    u.hook_add(UC_HOOK_CODE, hook, begin=base + 0x70000, end=base + 0x70000)
    scratch = [r.uniform(-0.1, 0.1) for _ in range(B * 16)]
    loads = [r.uniform(-0.1, 0.1) for _ in range(B * 12)]
    src = []
    for i in range(B):
        w(sp + i * 100, '16f', *scratch[i * 16:i * 16 + 16])
        w(sp + i * 100 + 64, 'I', bodybase + i * 512)
        w(bodybase + i * 512 + 0xe0, '12f', *loads[i * 12:i * 12 + 12])
    for i in range(S):
        v = [r.uniform(-1, 1), float(r.randrange(S)), r.uniform(0.1, 1), r.uniform(0.1, 1)]
        src += v
        w(sources + i * 144 + 0x4c, 'fIff', v[0], int(v[1]), *v[2:])
    rows = (Row * N)()
    offsets = [0] * 9
    locations = []
    for i, g in enumerate(groups):
        row = rows[i]
        row.a = r.randrange(B)
        row.b = (row.a + 1 + r.randrange(B - 1)) % B
        row.s = i
        for j in range(36):
            row.c[j] = r.uniform(-0.1, 0.1)
        row.c[24] = -0.3
        row.c[25] = 0.4
        row.c[33] = r.uniform(0, 0.5)
        loc = rowbase + g * 8192 + offsets[g] * 156
        locations.append(loc)
        offsets[g] += 1
        u.mem_write(loc, bytes(row))
    for g, n in enumerate(offsets):
        w(ctx + 64 + g * 12, 'III', rowbase + g * 8192, n, n)
    w(0x800f000, 'I', 0x8000000)
    u.reg_write(UC_X86_REG_ESP, 0x800f000)
    u.reg_write(UC_X86_REG_ECX, ctx)
    u.emu_start(0xf73c20, 0x8000000, count=1000000)
    sr = (C.c_float * len(scratch))(*scratch)
    ld = (C.c_float * len(loads))(*loads)
    sc = (C.c_float * len(src))(*src)
    gg = (C.c_int * N)(*groups)
    agg = C.c_float()
    mid_out = (C.c_float * 2)()
    lib.fixture(mid, it, N, gg, rows, B, sr, ld, S, sc, C.byref(agg), mid_out)
    if mid and list(mid_out) != mid_actual:
        return ('mid', seed, mid_actual, list(mid_out))
    actual_aggregate = unpack('f', u.mem_read(base + 0x60010, 4))[0]
    if agg.value != actual_aggregate:
        return ('aggregate', seed, agg.value, actual_aggregate)
    for i in range(S):
        actual_lambda = unpack('f', u.mem_read(sources + i * 144 + 0x4c, 4))[0]
        if sc[i * 4] != actual_lambda:
            return ('lambda', seed, i, sc[i * 4], actual_lambda)
    for i, loc in enumerate(locations):
        actual = unpack('36fIII', u.mem_read(loc, 156))
        expected = unpack('36fIII', bytes(rows[i]))
        if actual != expected:
            return ('row', seed, groups, mid, it, i, [(j, a, b) for j, (a, b) in enumerate(zip(actual, expected)) if a != b])
    for i in range(B):
        for label, actual, expected in [('scratch', unpack('16f', u.mem_read(sp + i * 100, 64)), list(sr)[i * 16:i * 16 + 16]), ('loads', unpack('12f', u.mem_read(bodybase + i * 512 + 0xe0, 48)), list(ld)[i * 12:i * 12 + 12])]:
            if list(actual) != list(expected):
                return (label, seed, groups, mid, it, i, [(j, a, b) for j, (a, b) in enumerate(zip(actual, expected)) if a != b])
    u.reg_write(UC_X86_REG_ESP, 0x800f000)
    u.reg_write(UC_X86_REG_ECX, ctx)
    u.emu_start(0xf718e0, 0x8000000, count=100000)
    for i in range(B):
        assert unpack('i', u.mem_read(bodybase + i * 512 + 452, 4))[0] == -1
    for offset in [28, 176, 188] + [68 + 12 * i for i in range(9)]:
        assert unpack('I', u.mem_read(ctx + offset, 4))[0] == 0
    assert unpack('I', u.mem_read(base + 0x60004, 4))[0] == base + 0x61000
    assert unpack('I', u.mem_read(base + 0x60010, 4))[0] == 0
    return None

def audit_solver():
    for group in (0, 1, 2, 3, 5, 6, 8):
        for seed in range(10):
            failure = check(seed, [group], 0, 2)
            assert failure is None, failure
    for seed in range(100):
        for mid, iterations in [(0, 2), (0, 7), (1, 2), (3, 5)]:
            failure = check(seed, [0, 2, 8, 1, 3, 5, 6] * 3, mid, iterations)
            assert failure is None, failure
    print('470 solver, callback, aggregation and cleanup cases matched original binary32 results')
pack = lambda f, *v: struct.pack('<' + f, *v)
f32 = lambda v: struct.unpack('<f', struct.pack('<f', v))[0]

def producer_case(seed, pair, contact, mode, kind, friction):
    r = random.Random(seed)
    dt = f32(0.033)
    data = [dt]
    for i in range(2):
        data.append(r.uniform(0.001, 1))
        data += [r.uniform(-0.1, 0.1) for _ in range(16)]
        data += [1 if i in (0, 5, 10, 15) else r.uniform(-10, 10) for i in range(16)]
        data += [r.uniform(-5, 5) for _ in range(12)]
    data += [r.uniform(-1, 1) for _ in range(12)] + [pair, -0.4, 0.8, mode, 0.8, 0.02, 0.12, 0.03, kind, 0.2, friction, contact]
    data = list(struct.unpack('<115f', pack('115f', *data)))
    return data

def audit_producers(producer_fixture):
    cases = []
    for pair in (0, 1):
        for contact in (0, 1):
            for mode in (0, 1, 2):
                for kind in (0, 2):
                    for friction in (-1, 0):
                        for seed in range(8):
                            cases.append(producer_case(seed, pair, contact, mode, kind, friction))
    output = subprocess.check_output([producer_fixture], input=b''.join((pack('115f', *v) for v in cases)))
    out = [struct.unpack('<105f', output[i:i + 420]) for i in range(0, len(output), 420)]
    assert len(out) == len(cases), (len(out), len(cases))
    worst = 0
    single_worst = 0
    pair_worst = 0
    for n, (data, expected) in enumerate(zip(cases, out)):
        u = Uc(UC_ARCH_X86, UC_MODE_32)
        base = 0x4000000
        ctx = base
        sp = base + 65536
        source = base + 0x20000
        cache = base + 0x30000
        body = base + 0x50000
        spec = base + 0x60000
        u.mem_map(p.OPTIONAL_HEADER.ImageBase, p.OPTIONAL_HEADER.SizeOfImage + 4095 & ~4095)
        u.mem_write(p.OPTIONAL_HEADER.ImageBase, p.get_memory_mapped_image())
        u.mem_map(base, 0x80000)
        u.mem_map(0x8000000, 65536)

        def w(a, f, *v):
            u.mem_write(a, pack(f, *v))

        def read(a, count):
            return struct.unpack('<' + 'f' * count, u.mem_read(a, count * 4))
        w(ctx + 32, 'ff', data[0], f32(1 / data[0]))
        w(ctx + 0xac, 'III', sp, 0, 10)
        w(ctx + 184, 'III', source, 0, 10)
        for g in range(9):
            w(ctx + 64 + g * 12, 'III', cache + g * 4096, 0, 10)
        at = 1
        for i in range(2):
            b = body + i * 512
            w(b + 4, 'f', data[at])
            at += 1
            w(b + 136, '16f', *data[at:at + 16])
            at += 16
            w(b + 320, '16f', *data[at:at + 16])
            w(b + 368, '3f', *data[at + 12:at + 15])
            at += 16
            w(b + 284, '3f', *data[at:at + 3])
            at += 3
            w(b + 272, '3f', *data[at:at + 3])
            at += 3
            w(b + 200, '3f', *data[at:at + 3])
            at += 3
            w(b + 212, '3f', *data[at:at + 3])
            at += 3
            w(b + 452, 'i', -1)
        geom = data[at:at + 12]
        at += 12
        pair = int(data[at])
        at += 1
        w(spec, '12f', *geom)
        w(spec + 48, 'II', body, body + 0x200 if pair else 0)
        w(spec + 56, 'ff', *data[at:at + 2])
        at += 2
        mode = int(data[at])
        at += 1
        w(spec + 0x44, 'I', mode)
        w(spec + 72, '4f', *data[at:at + 4])
        at += 4
        w(spec + 92, 'I', int(data[at]))
        at += 1
        error = data[at]
        friction = int(data[at + 1])
        contact = int(data[at + 2])
        if friction != -1:
            w(ctx + 0xbc, 'I', 1)

        def normal(uc, address, size, user):
            stack = uc.reg_read(UC_X86_REG_ESP)
            ret, dst, src, mat = struct.unpack('<4I', uc.mem_read(stack, 16))
            v = read(src, 3)
            m = read(mat, 16)
            values = [f32(f32(f32(v[0] * m[c]) + f32(v[1] * m[4 + c])) + f32(v[2] * m[8 + c])) for c in range(3)]
            w(dst, '3f', *values)
            uc.reg_write(UC_X86_REG_EAX, dst)
            uc.reg_write(UC_X86_REG_ESP, stack + 16)
            uc.reg_write(UC_X86_REG_EIP, ret)
        u.hook_add(UC_HOOK_CODE, normal, begin=0x11c3c06, end=0x11c3c06)
        w(0x800f000, 'IIfi', 0x8000000, spec, error, friction)
        u.reg_write(UC_X86_REG_ESP, 0x800f000)
        u.reg_write(UC_X86_REG_ECX, ctx)
        entry = (0xf71ed0 if contact else 0xf71af0) if pair else 0xf72770 if contact else 0xf723a0
        u.emu_start(entry, 0x8000000, count=100000)
        source += 144 if friction != -1 else 0
        sourcegroup = struct.unpack('<I', u.mem_read(source + 48, 4))[0]
        g = (sourcegroup - (ctx + 64)) // 12
        actual = [float(g)] + list(read(cache + g * 4096, 36)) + list(read(source, 12))
        for i in range(2 if pair else 1):
            actual += list(read(sp + i * 100, 16)) + list(read(sp + i * 100 + 0x44, 6))
        for j, (a, b) in enumerate(zip(actual, expected)):
            err = abs(a - b) / max(1, abs(a), abs(b))
            worst = max(worst, err)
            if pair:
                pair_worst = max(pair_worst, err)
            else:
                single_worst = max(single_worst, err)
            if err > (0.0004 if pair else 1e-06):
                raise AssertionError((n, pair, contact, mode, j, a, b, err))
    print('Producer cases', len(cases), 'single maximum', single_worst, 'paired maximum', pair_worst)


def main():
    global pe, p, lib
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', default=os.environ.get('WOT_0922_CLIENT'))
    arguments = parser.parse_args()
    if not arguments.client:
        parser.error('--client or WOT_0922_CLIENT is required')
    pe = p = pefile.PE(str(Path(arguments.client) / 'WorldOfTanks.exe'))
    assert pe.FILE_HEADER.Machine == 0x14c
    assert pe.OPTIONAL_HEADER.ImageBase == 0x400000
    source = Path(__file__).resolve().parents[1]
    compiler = os.environ.get('CXX', 'c++')
    flags = ['-std=c++17', '-O2', '-fno-fast-math', '-ffp-contract=off',
             '-Wall', '-Wextra', '-Werror', '-I' + str(source / 'include')]
    kernel = str(source / 'src/vehicle_constraint_solver.cpp')
    with tempfile.TemporaryDirectory(prefix='wot-constraint-oracle-') as temp:
        library = str(Path(temp) / 'solver.so')
        subprocess.run([compiler] + flags + ['-shared', '-fPIC', kernel,
            str(source / 'tests/vehicle_constraint_solver_fixture.cpp'),
            '-o', library], check=True)
        lib = C.CDLL(library)
        audit_solver()
        fixture = str(Path(temp) / 'producer')
        architecture = ['-arch', 'x86_64'] if platform.system() == 'Darwin' else []
        subprocess.run([compiler] + architecture + flags + [kernel,
            str(source / 'tests/vehicle_constraint_producer_fixture.cpp'),
            '-o', fixture], check=True)
        audit_producers(fixture)


if __name__ == '__main__':
    main()
