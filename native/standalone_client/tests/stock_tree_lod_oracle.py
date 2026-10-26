"""Original #1513 SPT constructor and complete reader instruction audit.

The SDK functions are never patched or replaced. Unicorn resolves imported CRT
allocation, memory, scalar parsing and transcendental functions to host services.
A fixed clock and serialized mutex acknowledgements bound this data-reader test;
this is not execution of a Windows DLL or proof of SDK animation/rendering.
"""

class OriginalTreeInstructionOracle:
    def __init__(self, executable):
        import ctypes
        import math
        import re
        import struct
        import pefile
        import sys
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
        from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX, UC_X86_REG_EAX, UC_X86_REG_EDX, UC_X86_REG_EIP, UC_X86_REG_FPCW, UC_X86_REG_MXCSR
        p = pefile.PE(str(executable))
        assert p.FILE_HEADER.Machine == 0x14c, 'Original tree oracle requires the pinned x86 PE'
        base = p.OPTIONAL_HEADER.ImageBase
        image = p.get_memory_mapped_image()
        u = Uc(UC_ARCH_X86, UC_MODE_32)
        u.mem_map(base, (len(image) + 4095) & ~4095)
        u.mem_write(base, image)
        u.mem_map(0, 4096)
        u.mem_map(0x10000000, 65536)
        u.mem_map(0x20000000, 0x4000000)
        u.mem_map(0x30000000, 65536)
        u.mem_write(0x3000f000, b'\xdd\x05' + struct.pack('<I', 0x3000f100) + b'\xc3')
        heap = 0x20100000
        allocs = {}
        imports = {}
        count = {}
        self.random_source = None
        scan = ctypes.CDLL('msvcrt' if sys.platform == 'win32' else None).sscanf
        scan.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        for d in p.DIRECTORY_ENTRY_IMPORT:
            for imp in d.imports:
                stub = 0x30001000 + len(imports) * 8
                imports[stub] = (d.dll.decode(), imp.name.decode() if imp.name else str(imp.ordinal))
                u.mem_write(imp.address, struct.pack('<I', stub))
                u.mem_write(stub, b'\xc3')

        def word(a):
            return struct.unpack('<I', u.mem_read(a, 4))[0]

        def string(a):
            b = bytearray()
            while True:
                c = bytes(u.mem_read(a, 1))
                a += 1
                if c == b'\x00':
                    return bytes(b)
                b.extend(c)

        def alloc(n):
            nonlocal heap
            a = heap
            heap += (n + 31) & ~31
            assert heap <= 0x24000000, 'Original reader exceeded the bounded audit heap'
            allocs[a] = n
            return a

        def hook(uc, a, size, data):
            if a not in imports:
                return
            dll, name = imports[a]
            count[name] = count.get(name, 0) + 1
            esp = u.reg_read(UC_X86_REG_ESP)
            arg = lambda n: word(esp + 4 + n * 4)
            ret = word(esp)
            value = 0
            cleanup = 0
            if name in ['malloc', '_malloc_base']:
                value = alloc(arg(0))
            elif name in ['calloc', '_calloc_base']:
                value = alloc(arg(0) * arg(1))
                u.mem_write(value, bytes(arg(0) * arg(1)))
            elif name == 'realloc':
                value = alloc(arg(1))
                if arg(0):
                    u.mem_write(value, bytes(u.mem_read(arg(0), min(allocs[arg(0)], arg(1)))))
            elif name in ['free', '_free_base', '_Mtx_lock', '_Mtx_unlock']:
                pass
            elif name == 'rand':
                if self.random_source is None:
                    raise RuntimeError('Original CRT rand import has no explicit input sequence')
                value = self.random_source()
                assert 0 <= value <= 32767, 'Original CRT rand input exceeds the x86 domain'
            elif name in ['sqrt', 'sin', 'cos', 'acos', 'asin', 'atan2', 'pow', 'fabs']:
                n = 2 if name in ['atan2', 'pow'] else 1
                args = struct.unpack('<' + 'd' * n, u.mem_read(esp + 4, n * 8))
                v = getattr(math, name)(*args)
                u.mem_write(0x3000f100, struct.pack('<d', v))
                u.reg_write(UC_X86_REG_EIP, 0x3000f000)
                return
            elif name in ['memmove', 'memcpy']:
                value = arg(0)
                u.mem_write(value, bytes(u.mem_read(arg(1), arg(2))))
            elif name == 'memset':
                value = arg(0)
                u.mem_write(value, bytes([arg(1) & 255]) * arg(2))
            elif name == 'memcmp':
                x = bytes(u.mem_read(arg(0), arg(2)))
                y = bytes(u.mem_read(arg(1), arg(2)))
                value = (x > y) - (x < y)
            elif name == 'strlen':
                value = len(string(arg(0)))
            elif name == 'atoi':
                m = re.match(b'\\s*([+-]?\\d+)', string(arg(0)))
                value = int(m.group(1)) if m else 0
            elif name == 'memchr':
                b = bytes(u.mem_read(arg(0), arg(2)))
                n = b.find(bytes([arg(1) & 255]))
                value = arg(0) + n if n >= 0 else 0
            elif name == '_time64':
                value = 123456
                u.reg_write(UC_X86_REG_EDX, 0)
                if arg(0):
                    u.mem_write(arg(0), struct.pack('<Q', value))
            elif name == '__stdio_common_vsscanf':
                source, fmt = (string(arg(2)), string(arg(4)))
                assert len(source) < 8192 and arg(3) == 0xffffffff, 'Unexpected CRT scan domain'
                types = re.findall(b'%(?:[0-9.]*)(l?[sdfgeuixc])', fmt)
                buffers = [ctypes.create_string_buffer(8192) for _ in types]
                value = scan(source, fmt, *buffers)
                for j, (t, buf) in enumerate(zip(types, buffers)):
                    if j >= value:
                        break
                    size = len(buf.value) + 1 if t == b's' else 8 if t.startswith(b'l') else 1 if t == b'c' else 4
                    u.mem_write(word(arg(6) + j * 4), buf.raw[:size])
            else:
                raise RuntimeError(('unsupported', hex(a), dll, name, hex(ret), [hex(arg(j)) for j in range(4)]))
            u.reg_write(UC_X86_REG_EAX, value & 0xffffffff)
            u.reg_write(UC_X86_REG_ESP, esp + 4 + cleanup)
            u.reg_write(UC_X86_REG_EIP, ret)
        u.hook_add(UC_HOOK_CODE, hook, begin=0x30001000, end=0x3000efff)
        stack = 0x10008000
        stop = 0x30000000
        owner = 0x20000000

        def call(fn, args=(), ecx=owner):
            u.mem_write(stack, struct.pack('<' + 'I' * (1 + len(args)), stop, *args))
            u.reg_write(UC_X86_REG_ESP, stack)
            u.reg_write(UC_X86_REG_ECX, ecx)
            u.reg_write(UC_X86_REG_FPCW, 895)
            u.reg_write(UC_X86_REG_MXCSR, 8064)
            try:
                u.emu_start(fn, stop, count=10000000)
            except Exception:
                print('failed', hex(u.reg_read(UC_X86_REG_EIP)), count)
                raise
        call(0x11d78b0)
        self.emulator = u
        self.allocate = alloc
        self.word = word
        self.call = call
        self.import_counts = count
        self.owner = owner
        self.stack = stack
        regions = []
        for section in p.sections:
            if section.Characteristics & 0x80000000:
                address = base + section.VirtualAddress
                regions.append((address, bytes(u.mem_read(address, section.Misc_VirtualSize))))
        initial_heap_end = heap
        initial_heap = bytes(u.mem_read(owner, heap - owner))
        initial_allocations = dict(allocs)

        def restore():
            nonlocal heap, allocs
            for address, data in regions:
                u.mem_write(address, data)
            u.mem_write(owner, initial_heap)
            heap = initial_heap_end
            allocs = dict(initial_allocations)
        self.restore = restore


def original_spt_states(executable, files):
    import struct
    from unicorn.x86_const import UC_X86_REG_EAX
    oracle = OriginalTreeInstructionOracle(executable)
    emulator = oracle.emulator
    owner = oracle.owner
    expected = bytearray()
    for number, data in enumerate(files):
        oracle.restore()
        source = oracle.allocate(len(data))
        emulator.mem_write(source, data)
        oracle.call(0x11da950, (source, len(data)))
        assert emulator.reg_read(UC_X86_REG_EAX) & 255 == 1, number
        expected.extend(emulator.mem_read(owner+0x20, 12))
        expected.extend(emulator.mem_read(owner+0x30, 4))
        expected.extend(emulator.mem_read(owner+0x4c, 4))
        expected.extend(emulator.mem_read(oracle.word(owner+4)+0x68, 4))
        expected.extend(emulator.mem_read(oracle.word(owner+0x64)+0x44, 4))
        expected.extend(emulator.mem_read(oracle.word(owner+4)+0xac, 4))
    return bytes(expected), oracle.import_counts
