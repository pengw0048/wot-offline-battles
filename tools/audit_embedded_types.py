#!/usr/bin/env python2
"""Verify Python-2 values at BigWorld's strict entity STRING boundary."""

from __future__ import print_function

import json
import imp
import os
import sys


def audit(port_root):
    if sys.version_info[:2] != (2, 7):
        raise ValueError('embedded type audit requires Python 2.7')
    module_root = os.path.join(
        os.path.abspath(port_root), 'src', 'res', 'scripts', 'client', 'gui',
        'mods', 'offline_lan_0922')
    compatibility = imp.load_source(
        '_offline_lan_0922_type_audit_compat',
        os.path.join(module_root, 'compat.py'))
    binding = imp.load_source(
        '_offline_lan_0922_type_audit_binding',
        os.path.join(module_root, 'entities', 'bigworld_binding.py'))

    # This is the exact receive shape: LANClient decodes the TCP frame before
    # json.loads(), so Python 2 returns unicode for every roster name.
    name = json.loads(
        u'{"name":"Player-\u73a9\u5bb6"}')['name']
    if type(name) is not unicode:
        raise ValueError('Python 2 JSON did not produce unicode')

    checked = (
        ('Avatar.name', compatibility._entity_bytes(name)),
        ('Avatar.clientCtx', compatibility._entity_bytes(u'')),
        ('Vehicle.publicInfo.name', binding._entity_string(name)),
    )
    for field, value in checked:
        if type(value) is not str:
            raise ValueError('%s is not a Python 2 str' % field)
        value.decode('utf-8')
    return len(checked)


if __name__ == '__main__':
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    print('embedded type audit passed: %d STRING fields' % audit(root))
