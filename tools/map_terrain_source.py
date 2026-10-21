#!/usr/bin/env python3
"""Split an authored height field into raw-data PNG tiles.

This exports authoring data only, not a loadable WoT map. It deliberately does
not invent a terrain header, compiled space, collision, or derived textures.
Rows follow increasing world Z; columns follow increasing world X. The source
includes two samples beyond each edge of the playable rectangle.
"""

import argparse
import json
import math
from pathlib import Path
import struct
import zlib


CHUNK_SIZE_METRES = 100
INTERVALS_PER_CHUNK = 32
BORDER = 2
TILE_SIZE = INTERVALS_PER_CHUNK + 2 * BORDER + 1


def validate_source(source):
    """Validate the source contract before creating any output files."""
    if not isinstance(source, dict) or source.get('format') != 'wot-map-source':
        raise ValueError('source format must be wot-map-source')
    if type(source.get('version')) is not int or source['version'] != 1:
        raise ValueError('source version must be 1')
    terrain = source.get('terrain')
    if not isinstance(terrain, dict):
        raise ValueError('terrain must be an object')
    for name, expected in (('intervals_per_chunk', INTERVALS_PER_CHUNK),
                           ('border', BORDER)):
        if type(terrain.get(name)) is not int or terrain[name] != expected:
            raise ValueError('%s must be %d' % (name, expected))
    origin = terrain.get('origin')
    if not isinstance(origin, list) or len(origin) != 2:
        raise ValueError('origin must contain world X and Z coordinates')
    for value in origin:
        try:
            valid = (type(value) in (int, float) and math.isfinite(value))
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('origin coordinates must be finite numbers')
    chunks = terrain.get('chunks')
    if (not isinstance(chunks, list) or len(chunks) != 2 or
            any(type(value) is not int or value <= 0 for value in chunks)):
        raise ValueError('chunks must contain two positive integers')
    width = chunks[0] * INTERVALS_PER_CHUNK + 2 * BORDER + 1
    height = chunks[1] * INTERVALS_PER_CHUNK + 2 * BORDER + 1
    rows = terrain.get('heights_mm')
    if not isinstance(rows, list) or len(rows) != height:
        raise ValueError('heights_mm must contain %d rows' % height)
    for z, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != width:
            raise ValueError('heights_mm row %d must contain %d samples' % (z, width))
        for x, value in enumerate(row):
            if type(value) is not int or not -(2 ** 31) <= value < 2 ** 31:
                raise ValueError('heights_mm[%d][%d] must be a signed int32' % (z, x))
    return terrain


def _png_chunk(kind, data):
    # PNG requires a CRC for each chunk; this is not an artifact checksum.
    return (struct.pack('>I', len(data)) + kind + data +
            struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))


def _encode_height_png(rows):
    """Encode millimetres as four raw bytes, with no color transformation."""
    width, height = len(rows[0]), len(rows)
    pixels = b''.join(b'\0' + struct.pack('<%di' % width, *row) for row in rows)
    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + _png_chunk(b'IHDR', header) +
            _png_chunk(b'IDAT', zlib.compress(pixels)) + _png_chunk(b'IEND', b''))


def export_source(source, output):
    """Write PNG tiles and an explicit authoring-only manifest to an empty folder."""
    terrain = validate_source(source)
    output = Path(output)
    if output.is_symlink():
        raise ValueError('output must not be a symbolic link')
    if output.exists():
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError('output must be a new or empty directory')
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        'format': 'wot-map-terrain-tiles',
        'version': 1,
        'status': 'authoring-only',
        'game_loadable': False,
        'note': ('Raw height samples only. No terrain header, compiled space, '
                 'collision, derived textures, or game installation is produced.'),
        'origin': terrain['origin'],
        'chunks': terrain['chunks'],
        'chunk_size_metres': CHUNK_SIZE_METRES,
        'intervals_per_chunk': INTERVALS_PER_CHUNK,
        'border': BORDER,
        'sample_spacing_metres': CHUNK_SIZE_METRES / INTERVALS_PER_CHUNK,
        'encoding': 'RGBA8 PNG; each pixel is a little-endian signed int32 in millimetres',
        'sample_order': 'Rows increase world Z; columns increase world X; no color management',
        'tiles': [],
    }
    for tile_z in range(terrain['chunks'][1]):
        for tile_x in range(terrain['chunks'][0]):
            start_x = tile_x * INTERVALS_PER_CHUNK
            start_z = tile_z * INTERVALS_PER_CHUNK
            rows = [row[start_x:start_x + TILE_SIZE]
                    for row in terrain['heights_mm'][start_z:start_z + TILE_SIZE]]
            filename = 'height_%d_%d.png' % (tile_x, tile_z)
            with (output / filename).open('xb') as stream:
                stream.write(_encode_height_png(rows))
            manifest['tiles'].append({
                'tile_index': [tile_x, tile_z],
                'world_origin': [terrain['origin'][0] + tile_x * CHUNK_SIZE_METRES,
                                 terrain['origin'][1] + tile_z * CHUNK_SIZE_METRES],
                'file': filename,
                'size': [TILE_SIZE, TILE_SIZE],
                'minimum_mm': min(min(row) for row in rows),
                'maximum_mm': max(max(row) for row in rows),
            })
    with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Authoring source JSON')
    parser.add_argument('--output', required=True, type=Path,
                        help='New or empty directory for authoring PNG tiles')
    args = parser.parse_args(argv)
    try:
        with args.input.open('r', encoding='utf-8') as stream:
            source = json.load(stream)
        manifest = export_source(source, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print('Wrote %d authoring-only height tiles to %s (not a loadable game map).' %
          (len(manifest['tiles']), args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
