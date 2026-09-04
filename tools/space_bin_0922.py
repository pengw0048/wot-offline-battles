#!/usr/bin/env python3
"""Strict readers for the WoT 0.9.22 compiled-space container.

This is intentionally a *metadata* reader, not a pretend collision reader.
It covers the stable BWTB directory and the 0.9.20-era BWT2 terrain header
used by the pinned 0.9.22.0.1 client.  BWSG/BSGD geometry and BWWa/WTCP water
remain explicit required inputs for a safe navigation bake; callers must fail
closed until those sections are decoded.

The wire layout is cross-checked against SkepticalFox/wot-space.bin-utils
(compiled_space/sections/BWT2/v0_9_20.py and BWSG/v0_9_14.py), whose version
selection covers the 0.9.22 generation.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


class CompiledSpaceError(ValueError):
    """The compiled space is missing or does not match the supported layout."""


class UnsafeBakeInputError(CompiledSpaceError):
    """The input lacks a decoded collision or water source needed for a bake."""


@dataclass(frozen=True)
class Section:
    name: str
    version: int
    offset: int
    size: int
    rows: int


@dataclass(frozen=True)
class TerrainInfo:
    chunk_size: float
    bounds: Tuple[int, int, int, int]
    chunks: Tuple[Tuple[int, int, int], ...]


class CompiledSpace(object):
    """Read the BWTB directory without guessing offsets or section sizes."""

    _ROOT = struct.Struct('<4s5I')

    def __init__(self, data):
        self._data = bytes(data)
        self.sections = self._read_directory()

    def _read_directory(self):
        if len(self._data) < self._ROOT.size:
            raise CompiledSpaceError('space.bin is shorter than the BWTB header')
        magic, version, directory_end, unused, unused_length, count = (
            self._ROOT.unpack_from(self._data, 0))
        if magic != b'BWTB':
            raise CompiledSpaceError('space.bin is not a BWTB container')
        if version != 1:
            raise CompiledSpaceError('unsupported BWTB version %d' % version)
        expected_end = self._ROOT.size + count * self._ROOT.size
        if directory_end != expected_end or directory_end > len(self._data):
            raise CompiledSpaceError('invalid BWTB directory size')
        records = {}
        for index in range(count):
            start = self._ROOT.size + index * self._ROOT.size
            raw_name, section_version, offset, unused_value, size, rows = (
                self._ROOT.unpack_from(self._data, start))
            try:
                name = raw_name.decode('ascii')
            except UnicodeDecodeError:
                raise CompiledSpaceError('non-ASCII BWTB section name')
            if offset + size > len(self._data):
                raise CompiledSpaceError('section %s extends past space.bin' % name)
            if name in records:
                raise CompiledSpaceError('duplicate BWTB section %s' % name)
            records[name] = Section(name, section_version, offset, size, rows)
        return records

    def section_data(self, name):
        record = self.sections.get(name)
        if record is None:
            raise CompiledSpaceError('space.bin has no %s section' % name)
        return self._data[record.offset:record.offset + record.size]

    def terrain_info_0920(self):
        record = self.sections.get('BWT2')
        if record is None:
            raise CompiledSpaceError('space.bin has no BWT2 terrain section')
        if record.version != 2:
            raise CompiledSpaceError('unsupported BWT2 version %d' % record.version)
        data = self.section_data('BWT2')
        if len(data) < 4 + 32 + 4:
            raise CompiledSpaceError('truncated BWT2 header')
        setting_size = struct.unpack_from('<I', data, 0)[0]
        if setting_size != 32:
            raise CompiledSpaceError('unsupported BWT2 settings size %d' % setting_size)
        chunk_size, min_x, max_x, min_y, max_y, unused_a, unused_b, unused_c = (
            struct.unpack_from('<f4i3I', data, 4))
        count_offset = 4 + setting_size
        entry_size, count = struct.unpack_from('<II', data, count_offset)
        if entry_size != 8:
            raise CompiledSpaceError('unsupported BWT2 terrain entry size %d' %
                                     entry_size)
        entries_offset = count_offset + 8
        expected = entries_offset + count * entry_size
        if expected > len(data):
            raise CompiledSpaceError('truncated BWT2 terrain chunk list')
        chunks = tuple(struct.unpack_from('<Ihh', data, entries_offset + index * entry_size)
                       for index in range(count))
        if not chunk_size > 0.0:
            raise CompiledSpaceError('invalid BWT2 chunk size')
        return TerrainInfo(chunk_size, (min_x, max_x, min_y, max_y), chunks)

    def require_safe_navigation_sources(self):
        """Reject a bake until every non-terrain safety source is decoded.

        Presence is checked separately from implementation on purpose: treating
        an opaque BWSG/BSGD or water section as empty would turn houses, bridges
        and deep water into drivable terrain.
        """
        required = ('BWSG', 'BSGD', 'BWWa', 'WTCP')
        missing = [name for name in required if name not in self.sections]
        if missing:
            raise UnsafeBakeInputError('space.bin lacks safety section(s): %s' %
                                       ', '.join(missing))
        raise UnsafeBakeInputError(
            'BWSG/BSGD static collision and BWWa/WTCP water are present but '
            'not yet decoded; refusing to emit an unsafe navigation graph')


def describe_space(data):
    """Return deterministic, JSON-ready metadata for an inspected space."""
    space = CompiledSpace(data)
    terrain = space.terrain_info_0920()
    return {
        'format': 'wot-compiled-space-inspection',
        'bwt2': {
            'bounds': list(terrain.bounds),
            'chunk_count': len(terrain.chunks),
            'chunk_size': terrain.chunk_size,
        },
        'sections': dict((name, {
            'version': section.version,
            'size': section.size,
            'rows': section.rows,
        }) for name, section in sorted(space.sections.items())),
    }
