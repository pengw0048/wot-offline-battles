"""Check XMPP lifecycle composition against the unmodified public source."""
from pathlib import Path
import importlib.util
import itertools
import os
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / ('bigworld/patch_stock_' + name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class XmppLifecycleTest(unittest.TestCase):
    def test_native_owner_precedes_import_and_retires_before_python(self):
        archive = Path(os.environ.get('WOT_BIGWORLD_ARCHIVE',
                                     '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists():
            self.skipTest('Pinned public-source archive unavailable')
        names = ['client/script_bigworld.cpp', 'client/script_app.cpp',
                 'client/device_app.cpp']
        with tarfile.open(archive) as source:
            original = {name: source.extractfile(next(
                n for n in source.getnames() if n.endswith('/' + name))).read()
                        for name in names}
        xmpp, audio, paths = module('xmpp'), module('audio'), module('script_paths')
        for order in itertools.permutations([xmpp.patch, audio.patch_engine, paths.patch]):
            with tempfile.TemporaryDirectory(prefix='xmpp-lifecycle-') as directory:
                root = Path(directory)
                for name, data in original.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                for apply in order:
                    apply(root)
                first = {name: (root / name).read_bytes() for name in names}
                for apply in reversed(order):
                    apply(root)
                self.assertEqual(first, {name: (root / name).read_bytes() for name in names})
                script = (root / names[0]).read_text()
                start = script.index('bool BigWorldClientScript::init(')
                startup = script[start:]
                self.assertLess(startup.index('Script::init( paths,'),
                                startup.index('initialiseStockXmpp()'))
                self.assertLess(startup.index('initialiseStockXmpp()'),
                                startup.index('EntityType::init('))
                app = (root / names[1]).read_text()
                self.assertEqual(app.count('tickStockXmpp();'), 1)
                self.assertLess(app.index('finiStockXmpp();'),
                                app.index('BigWorldClientScript::fini();'))
                # A drifted second file must not leave the first partly patched.
                for name, data in original.items():
                    (root / name).write_bytes(data)
                target = root / names[1]
                target.write_text(target.read_text().replace('BigWorldClientScript::tick();',
                                                             'unknownTick();'))
                with self.assertRaises(RuntimeError):
                    xmpp.patch(root)
                self.assertEqual((root / names[0]).read_bytes(), original[names[0]])


if __name__ == '__main__':
    unittest.main()
