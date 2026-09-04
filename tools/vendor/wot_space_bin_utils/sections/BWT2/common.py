""" BWT2 (Terrain 2) """

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class Chunks:
    gchunk: 'Element'
    secs: list
    chunk_size: float
    name_to_tree: dict = field(default_factory=dict)
    name_to_path: dict = field(default_factory=dict)
    loc_to_name: dict = field(default_factory=dict)

    def gets(self, key):
        return self.secs['BWST'].get(key)

    def add_chunk(self, chunk, out_dir: Path):
        name = self.gets(chunk['resource_fnv']).split('.')[0]

        root = ET.Element('root')
        el = ET.SubElement(root, 'terrain')
        el = ET.SubElement(el, 'resource')
        el.text = f'{name}.cdata/terrain2'

        self.name_to_tree[name] = root
        self.name_to_path[name] = (out_dir / f'{name}.chunk')

        x = chunk['loc_x'] * self.chunk_size
        y = chunk['loc_y'] * self.chunk_size

        self.loc_to_name[(x, y)] = name

        return name

    def get_by_worldpos(self, pos):
        x = pos[0]
        y = pos[2]

        # TODO: check this
        for loc, name in self.loc_to_name.items():
            if x < loc[0]:
                continue
            if x > loc[0] + self.chunk_size:
                continue
            if y < loc[1]:
                continue
            if y > loc[1] + self.chunk_size:
                continue

            x -= loc[0]
            y -= loc[1]

            transform = [
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                x, pos[1], y,  1.0,
            ]

            return (self.name_to_tree[name], transform)

        transform = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            pos[0], pos[1], pos[2],  1.0,
        ]
        return (self.gchunk, transform)

    def get_by_transform(self, transform):
        x = transform[12]
        y = transform[14]

        # TODO: check this
        for loc, name in self.loc_to_name.items():
            if x < loc[0]:
                continue
            if x > loc[0] + self.chunk_size:
                continue
            if y < loc[1]:
                continue
            if y > loc[1] + self.chunk_size:
                continue

            x -= loc[0]
            y -= loc[1]

            transform = list(transform)

            transform[12] = x
            transform[14] = y

            return (self.name_to_tree[name], transform)

        return (self.gchunk, transform)
