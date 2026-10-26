"""Verify that the native pinger composes with existing script lifecycle owners."""
from pathlib import Path
import importlib.util
import itertools
import os
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ('bigworld/patch_stock_' + name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class PingerLifecycleTest(unittest.TestCase):
    def test_composition_idempotence_and_transactional_failure(self):
        archive = Path(os.environ.get('WOT_BIGWORLD_ARCHIVE', '/tmp/wot-bigworld-engine-14.4.1.tar.gz'))
        if not archive.exists():
            self.skipTest('Pinned public-source archive unavailable')
        names = ['client/script_bigworld.cpp', 'client/script_app.cpp', 'client/device_app.cpp']
        with tarfile.open(archive) as source:
            original = {name: source.extractfile(next(n for n in source.getnames()
                        if n.endswith('/' + name))).read() for name in names}
        pinger, xmpp, audio, paths = [module(name) for name in ('pinger', 'xmpp', 'audio', 'script_paths')]
        for order in itertools.permutations([pinger.patch, xmpp.patch, audio.patch_engine, paths.patch]):
            with tempfile.TemporaryDirectory(prefix='pinger-lifecycle-') as directory:
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
                startup = script[script.index('bool BigWorldClientScript::init('):]
                self.assertEqual(startup.count('initialiseStockPinger('), 1)
                self.assertLess(startup.index('Script::init( paths,'), startup.index('initialiseStockPinger('))
                self.assertLess(startup.index('initialiseStockPinger('), startup.index('EntityType::init('))
                app = (root / names[1]).read_text()
                self.assertEqual(app.count('finiStockPinger();'), 1)
                self.assertLess(app.index('finiStockPinger();'), app.index('BigWorldClientScript::fini();'))
                for name, data in original.items():
                    (root / name).write_bytes(data)
                path = root / names[1]
                path.write_text(path.read_text().replace('BigWorldClientScript::fini();', 'unknownFini();'))
                with self.assertRaises(RuntimeError):
                    pinger.patch(root)
                self.assertEqual((root / names[0]).read_bytes(), original[names[0]])


if __name__ == '__main__':
    unittest.main()
