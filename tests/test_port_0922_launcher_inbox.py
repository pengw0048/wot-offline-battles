"""JSON handoff tests runnable under both Python 3 and the build's Python 2.7."""

from __future__ import unicode_literals

import json
import os
import shutil
import sys
import tempfile
import types
import unittest


class LauncherInboxTests(unittest.TestCase):
    def setUp(self):
        root = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'src', 'res', 'scripts', 'client', 'gui', 'mods',
                            'offline_lan_0922')
        self.modules = dict(sys.modules)
        self.addCleanup(self._restore_modules)
        for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
            module = types.ModuleType(str(name))
            module.__path__ = [root]
            sys.modules[name] = module
        for name in ('config', 'launcher_inbox'):
            module = types.ModuleType(str(name))
            path = os.path.join(root, name + '.py')
            module.__file__ = path
            with open(path, 'rb') as stream:
                eval(compile(stream.read(), path, 'exec'), module.__dict__)
            setattr(sys.modules['gui.mods.offline_lan_0922'], name, module)
            sys.modules['gui.mods.offline_lan_0922.' + name] = module
        self.inbox = module
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.path = os.path.join(self.directory, 'launcher_inbox.json')

    def _restore_modules(self):
        for name in list(sys.modules):
            if name not in self.modules:
                del sys.modules[name]
        sys.modules.update(self.modules)

    def test_a_launcher_json_purchase_is_deliverable(self):
        names = ['ussr:R31_Valentine_LL', 'germany:G32_PzV_PzIV']
        with open(self.path, 'w') as stream:
            json.dump({'schema': 1, 'vehicles': names}, stream)
        self.assertEqual(names, self.inbox.pending_vehicles(self.path))
        self.inbox.keep_pending(names[1:], self.path)
        self.assertEqual(names[1:], self.inbox.pending_vehicles(self.path))
        self.assertTrue(self.inbox.keep_pending([], self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_invalid_names_do_not_hide_a_valid_purchase(self):
        with open(self.path, 'w') as stream:
            json.dump({'schema': 1, 'vehicles': [
                '../outside', 'ussr:', 1, 'ussr:R31_Valentine_LL']}, stream)
        self.assertEqual(['ussr:R31_Valentine_LL'],
                         self.inbox.pending_vehicles(self.path))


if __name__ == '__main__':
    unittest.main()
