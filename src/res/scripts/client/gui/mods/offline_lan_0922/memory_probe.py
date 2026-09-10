"""Address-space snapshot for the 32-bit #1513 client processes.

Two field reports ended with the hidden worker faulting inside Scaleform's SWF
tag loader at ``WorldOfTanks.exe+0x0075DAE2``: an allocation returned NULL, the
stock null branch joined a path that dereferences the object anyway, and the
process died.  Both dumps showed the same cause - 3396 MiB and 3057 MiB of
private commit, with 119 MiB and 260 MiB free, on the seventh round of the
session.  Neither log could have shown that; only the dumps did.

``WorldOfTanks.exe`` sets ``IMAGE_FILE_LARGE_ADDRESS_AWARE`` (COFF
characteristics 0x0123), so on 64-bit Windows the user address space is 4 GB,
not 2 GB.  The walk below must cover all of it: in the 223333 dump 2077 MB of
committed memory lives above ``0x80000000``, and a walk that stopped at the
2 GB line would have reported a healthy process minutes before it died.

This module answers the same question from inside the process, once per round
boundary, so the next report says how the address space grew without needing a
dump at all.  It is diagnostics: every failure is swallowed and the caller
continues.
"""

import sys

try:
    import ctypes
except ImportError:
    # #1513 includes ctypes Python files but omits the native _ctypes module.
    # Optional measurements must never prevent BattleRuntime from importing.
    ctypes = None

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_FREE = 0x10000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000

# The walk costs one VirtualQuery per region.  The two crashed workers held
# 6754 and 7293 regions, so the cap only guards against a pathological process
# stalling a frame; it is not meant to bound a healthy walk.
MAX_REGIONS = 32768
# Allocation size classes, with no allocator or ownership attribution.
# CPython 2.7 arenas can be 256 KiB, but VirtualQuery does not identify their
# owner, and Python can allocate larger objects outside its small-object
# arenas. These totals include reserved and committed private regions sharing
# an AllocationBase; they are not a measurement of Python's total footprint.
ALLOC_1MIB_BYTES = 1024 * 1024
ALLOC_256KIB_BYTES = 256 * 1024

# Top of the 32-bit user address space on a large-address-aware image, less the
# final no-access guard page.  A process that only gets 2 GB simply fails the
# VirtualQuery past its own limit and the walk stops there on its own.
ADDRESS_SPACE_LIMIT = 0xFFFF0000


if ctypes is not None:
    class _MemoryBasicInformation(ctypes.Structure):
        _fields_ = [
            ('BaseAddress', ctypes.c_void_p),
            ('AllocationBase', ctypes.c_void_p),
            ('AllocationProtect', ctypes.c_ulong),
            ('RegionSize', ctypes.c_size_t),
            ('State', ctypes.c_ulong),
            ('Protect', ctypes.c_ulong),
            ('Type', ctypes.c_ulong),
        ]


    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ('dwLength', ctypes.c_ulong),
            ('dwMemoryLoad', ctypes.c_ulong),
            ('ullTotalPhys', ctypes.c_ulonglong),
            ('ullAvailPhys', ctypes.c_ulonglong),
            ('ullTotalPageFile', ctypes.c_ulonglong),
            ('ullAvailPageFile', ctypes.c_ulonglong),
            ('ullTotalVirtual', ctypes.c_ulonglong),
            ('ullAvailVirtual', ctypes.c_ulonglong),
            ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
        ]


def _kernel32():
    try:
        return ctypes.windll.kernel32
    except (AttributeError, OSError):
        return None


def snapshot():
    """Return one address-space summary, or None when it cannot be taken."""
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    try:
        return _walk(kernel32)
    except Exception:
        return None


def _walk(kernel32):
    info = _MemoryBasicInformation()
    size = ctypes.sizeof(info)
    query = kernel32.VirtualQuery
    address = 0
    limit = ADDRESS_SPACE_LIMIT
    regions = 0
    truncated = 0
    committed = {MEM_PRIVATE: 0, MEM_MAPPED: 0, MEM_IMAGE: 0}
    reserved = 0
    free_total = 0
    free_largest = 0
    alloc_1mib = 0
    alloc_256kib = 0
    allocation_sizes = {}
    while address < limit:
        if query(ctypes.c_void_p(address), ctypes.byref(info), size) != size:
            break
        region = int(info.RegionSize or 0)
        if region <= 0:
            break
        regions += 1
        if regions > MAX_REGIONS:
            truncated = 1
            break
        state = int(info.State)
        if state == MEM_FREE:
            free_total += region
            if region > free_largest:
                free_largest = region
        else:
            kind = int(info.Type)
            if state == MEM_COMMIT:
                if kind in committed:
                    committed[kind] += region
            else:
                reserved += region
            if kind == MEM_PRIVATE:
                base = int(info.AllocationBase or 0)
                allocation_sizes[base] = allocation_sizes.get(base, 0) + region
        address += region
    histogram = {}
    for total in allocation_sizes.values():
        if total == ALLOC_1MIB_BYTES:
            alloc_1mib += 1
        elif total == ALLOC_256KIB_BYTES:
            alloc_256kib += 1
        histogram[total] = histogram.get(total, 0) + 1
    result = {
        'private': committed[MEM_PRIVATE],
        'mapped': committed[MEM_MAPPED],
        'image': committed[MEM_IMAGE],
        'reserved': reserved,
        'free': free_total,
        'largest_free': free_largest,
        'regions': regions,
        'alloc_1mib': alloc_1mib,
        'alloc_256kib': alloc_256kib,
        'classes': _top_classes(histogram),
        'truncated': truncated,
        'system_load': -1,
        'total_virtual': 0,
        'total_phys': 0,
    }
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    try:
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            result['system_load'] = int(status.dwMemoryLoad)
            # These two decide whether the machine can host two clients at all,
            # and total_virtual says outright whether this process got 2 GB or
            # the large-address-aware 4 GB.
            result['total_virtual'] = int(status.ullTotalVirtual)
            result['total_phys'] = int(status.ullTotalPhys)
    except Exception:
        pass
    return result


# How many distinct allocation sizes to name.  The point is to see *which*
# size class grows across rounds: in the crashed workers the 1 MiB class held
# 1739 blocks, but 32768 KiB x 13 and 240448 KiB x 1 were also present, and a
# bare private_mb total cannot tell those apart.
TOP_CLASSES = 6


def _top_classes(histogram):
    """Return the allocation sizes holding the most bytes, largest first."""
    ranked = sorted(histogram.items(),
                    key=lambda item: item[0] * item[1], reverse=True)
    return [(size, count) for size, count in ranked[:TOP_CLASSES]]


def _megabytes(value):
    return round(float(value) / (1024.0 * 1024.0), 1)


def format_line(phase, round_id, state=None):
    """Return the one MEMORY line for this boundary, or None."""
    if state is None and ctypes is None:
        return ('[Offline LAN 0.9.22] MEMORY phase=%s round=%s '
                'unavailable=ctypes' % (phase, round_id))
    state = snapshot() if state is None else state
    if not state:
        return None
    return ('[Offline LAN 0.9.22] MEMORY phase=%s round=%s private_mb=%s '
            'image_mb=%s mapped_mb=%s reserved_mb=%s free_mb=%s '
            'largest_free_mb=%s regions=%d alloc_1mib=%d alloc_256kib=%d '
            'alloc_256kib_mb=%s classes=%s system_load=%d '
            'total_virtual_mb=%s total_phys_mb=%s truncated=%d' % (
                phase, round_id,
                _megabytes(state['private']), _megabytes(state['image']),
                _megabytes(state['mapped']), _megabytes(state['reserved']),
                _megabytes(state['free']), _megabytes(state['largest_free']),
                state['regions'], state['alloc_1mib'],
                state.get('alloc_256kib', 0),
                _megabytes(state.get('alloc_256kib', 0) * ALLOC_256KIB_BYTES),
                _format_classes(state.get('classes')),
                state['system_load'],
                _megabytes(state.get('total_virtual', 0)),
                _megabytes(state.get('total_phys', 0)),
                state['truncated']))


def _format_classes(classes):
    """Render the size histogram as KiBxCOUNT pairs, largest bytes first."""
    if not classes:
        return '-'
    return ','.join('%dKx%d' % (size // 1024, count)
                    for size, count in classes)


def log(phase, round_id):
    """Write one MEMORY line. Never raises into a caller."""
    try:
        line = format_line(phase, round_id)
        if line is not None:
            sys.stdout.write(line + '\n')
    except Exception:
        pass
