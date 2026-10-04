"""Fingerprint active editor-owned resources; no mutation or auto-install.

A module compact descriptor selects IDs, not the XML values behind those IDs.
New recordings therefore bind the actual overlay content separately. Old
schema1 recordings remain readable, with an explicit unpinned warning.
"""
from __future__ import print_function
import hashlib
import json
import os
import re


def active_signature(game_root):
    root = os.path.abspath(os.path.join(game_root, 'res_mods', '0.9.22.0.1'))
    path = os.path.join(root, 'vehicle_overlays.json')
    if not os.path.isfile(path):
        return {'schema':1, 'members':[]}
    with open(path,'rb') as f:
        raw = f.read(32*1024*1024+1)
    if len(raw)>32*1024*1024:
        raise ValueError('vehicle overlay manifest exceeds replay evidence limit')
    doc = json.loads(raw.decode('utf-8'))
    members = doc.get('members')
    if not isinstance(members,list) or len(members)>1024:
        raise ValueError('invalid vehicle overlay manifest')
    result, seen, total = [], set(), 0
    for entry in members:
        name = entry.get('overlayRelativePath','')
        if (not isinstance(name,(str,type(u''))) or name in seen or
                not name.startswith('scripts/item_defs/vehicles/') or not name.endswith('.xml') or
                any(p in ('','.', '..') for p in name.split('/')) or
                not re.match(r'^[A-Za-z0-9_./-]+$',name)):
            raise ValueError('invalid vehicle overlay member in replay evidence')
        seen.add(name)
        filename = os.path.abspath(os.path.join(root,*name.split('/')))
        realroot = os.path.normcase(os.path.realpath(root))+os.sep
        if not os.path.normcase(os.path.realpath(filename)).startswith(realroot):
            raise ValueError('vehicle overlay escaped game directory')
        with open(filename,'rb') as f:
            data = f.read(32*1024*1024-total+1)
        total += len(data)
        if total>32*1024*1024:
            raise ValueError('vehicle overlay evidence exceeds byte limit')
        result.append([name,hashlib.sha256(data).hexdigest()])
    return {'schema':1,'members':sorted(result)}


def verify(signature, game_root):
    if signature is None:
        return False
    if signature != active_signature(game_root):
        raise ValueError('vehicle profile does not match this recording; select the original recorded profile (names alone do not identify its content)')
    return True
