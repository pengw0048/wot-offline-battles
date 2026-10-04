"""Launcher profile storage and original minimap loading. No game-file writes."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

try:
    from . import core
except ImportError:
    import core

# Source checkout and packaged source tree share the same pure-data contract.
_source = Path(__file__).resolve().parents[1] / 'src/res/scripts/client'
_bundled = Path(getattr(sys, '_MEIPASS', '')) / 'servers/0.9.22/src/res/scripts/client'
for path in (_source, _bundled):
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))
from gui.mods.offline_lan_0922 import bot_tactics as contract
from gui.mods.offline_lan_0922 import bot_tactics_runtime as runtime

try:
    from . import bot_tactics_labels as labels
except ImportError:
    import bot_tactics_labels as labels

# Backward-compatible Chinese display lookup; runtime keys stay unchanged.
MAP_LABELS = {key: labels.map_label(key, 'zh') for key in contract.MAPS}



def root_path():
    return Path(core.settings_path()).parent / 'bot_tactics'


def _atomic(path, document):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf8')
    if len(data) > contract.MAX_BYTES:
        raise contract.TacticsError('Document is too large')
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            shutil.copy2(str(path), str(path) + '.bak')
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, directory=None):
        self.root = Path(directory) if directory else root_path()
        self.profiles = self.root / 'profiles'
        self.active_path = self.root / 'active.json'

    def ensure_active(self):
        if not self.active_path.exists():
            _atomic(self.active_path, contract.empty())
        contract.load(str(self.active_path))  # a corrupt file never becomes defaults
        return str(self.active_path.resolve())

    def active(self):
        return contract.load(self.ensure_active())

    def names(self):
        result = []
        if self.profiles.exists():
            for path in sorted(self.profiles.glob('*.json')):
                try:
                    result.append(contract.load(str(path))['name'])
                except contract.TacticsError:
                    continue  # not deleted; importing it still reports the error
        return sorted(set(result))

    def profile_path(self, name):
        # Labels need not be safe path components; never use a label as a path.
        name = contract._text(name, 48)
        return self.profiles / (hashlib.sha256(name.encode('utf8')).hexdigest() + '.json')

    def read(self, name):
        return contract.load(str(self.profile_path(name)))

    def save(self, document, apply=False):
        document = contract.canonical(document)
        _atomic(self.profile_path(document['name']), document)
        if apply:
            _atomic(self.active_path, document)
        return copy.deepcopy(document)

    def export(self, document, filename):
        _atomic(filename, contract.canonical(document))

    @staticmethod
    def import_file(filename):
        return contract.load(str(filename))


def graph_data(name):
    if name not in contract.MAPS:
        raise contract.TacticsError('Unknown map')
    filename = name + '.json'
    source = Path(__file__).resolve().parents[1] / 'navgraphs' / filename
    if source.is_file() and not getattr(sys, 'frozen', False):
        data = source.read_bytes()
    else:
        archive = core.client_archive('0.9.22')
        with zipfile.ZipFile(archive) as pack:
            member = 'mods/configs/offline_lan_0922/navgraphs/' + filename
            info = pack.getinfo(member)
            if info.file_size > 40 * 1024 * 1024:
                raise contract.TacticsError('Navigation member too large')
            data = pack.read(member)
    if hashlib.sha256(data).hexdigest() != contract.MAPS[name]['resource_sha256']:
        raise contract.TacticsError('Navigation resource hash mismatch')
    return json.loads(data)


def minimap_image(game_root, name):
    """Read original DDS on demand; never distribute Wargaming map artwork."""
    from PIL import Image
    package = Path(game_root) / 'res/packages' / (name + '.pkg')
    with zipfile.ZipFile(package) as pack:
        names = [n for n in pack.namelist() if n.replace('\\', '/').lower().endswith('/mmap.dds')]
        if len(names) != 1 or pack.getinfo(names[0]).file_size > 32 * 1024 * 1024:
            raise contract.TacticsError('Original minimap missing or too large')
        raw = pack.read(names[0])
    with Image.open(io.BytesIO(raw)) as image:
        if image.width > 4096 or image.height > 4096:
            raise contract.TacticsError('Minimap dimensions too large')
        return image.convert('RGB')


def navigation_image(graph):
    """Explicitly labelled fallback, a navigation raster, not original map art."""
    from PIL import Image
    w, h = int(graph['width']), int(graph['height'])
    data = bytearray(w * h * 3)
    for index, height in enumerate(graph['heights_mm']):
        row, col = divmod(index, w)
        color = ((62, 77, 64) if height is not None and not int(graph['hazards'][index]) & 15 else (32, 37, 41))
        target = ((h - 1 - row) * w + col) * 3
        data[target:target + 3] = bytes(color)
    image = Image.frombytes('RGB', (w, h), bytes(data))
    # Raster sample origins may extend outside the exact playable rectangle.
    b = contract.MAPS[graph['map']]['bounds']; ox, oz = graph['origin']; cell = graph['cell_size']
    box = ((b[0]-ox)/cell+0.5, h-0.5-(b[3]-oz)/cell,
           (b[2]-ox)/cell+0.5, h-0.5-(b[1]-oz)/cell)
    return image.crop(tuple(int(round(v)) for v in box))


class ViewTransform:
    """North-up world/pixel transform, including non-centred arenas."""
    def __init__(self, bounds, width=700, height=600):
        self.bounds = bounds; self.width = width; self.height = height
        self.zoom = 1.0; self.pan_x = 0.0; self.pan_y = 0.0

    def frame(self):
        b = self.bounds
        scale = min((self.width-48)/(b[2]-b[0]), (self.height-48)/(b[3]-b[1])) * self.zoom
        return ((self.width-(b[2]-b[0])*scale)/2+self.pan_x,
                (self.height-(b[3]-b[1])*scale)/2+self.pan_y, scale)

    def screen(self, point):
        left, top, scale = self.frame(); b = self.bounds
        return left + (point[0]-b[0])*scale, top + (b[3]-point[1])*scale

    def world(self, x, y):
        left, top, scale = self.frame(); b = self.bounds
        return (max(b[0], min(b[2], b[0]+(x-left)/scale)),
                max(b[1], min(b[3], b[3]-(y-top)/scale)))
