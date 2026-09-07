"""Save slots: the client resolves earned progress from one named slot."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load_port_source(name):
    path = PACKAGE_ROOT / (name + '.py')
    spec = importlib.util.spec_from_file_location(
        'saveslots0922_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SaveSlotConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = _load_port_source('config')
        self.addCleanup(
            self.config.set_active_save_slot,
            self.config.DEFAULT_SAVE_SLOT)

    def test_a_slot_id_may_not_escape_the_saves_directory(self):
        for value in ('', '.', '..', 'a/b', 'a\\b', '-lead', '_lead',
                      'x' * 65, None, 3):
            self.assertFalse(self.config.valid_save_slot(value), repr(value))
        for value in ('default', 'a', 'save-2', 'A_1', 'x' * 64):
            self.assertTrue(self.config.valid_save_slot(value), repr(value))

    def test_setting_an_unusable_slot_leaves_the_previous_one_active(self):
        self.config.set_active_save_slot('keep-me')
        with self.assertRaises(ValueError):
            self.config.set_active_save_slot('../escape')
        self.assertEqual('keep-me', self.config.active_save_slot())

    def test_each_slot_owns_a_separate_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.config.save_slot_state_path(
                'garage_state.json', 'default', directory)
            second = self.config.save_slot_state_path(
                'garage_state.json', 'career', directory)
            self.assertNotEqual(first, second)
            self.assertEqual(
                os.path.join(directory, 'saves', 'career',
                             'garage_state.json'),
                second)

    def test_the_default_slot_adopts_state_written_before_save_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = os.path.join(directory, 'garage_state.json')
            with open(legacy, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 4, 'vehicles': {}}, stream)

            path = self.config.save_slot_state_path(
                'garage_state.json', 'default', directory)

            self.assertEqual(
                os.path.join(directory, 'saves', 'default',
                             'garage_state.json'),
                path)
            self.assertTrue(os.path.isfile(path))
            with open(path, 'rb') as stream:
                self.assertEqual(4, json.load(stream)['schema'])
            # The old copy is retained so an older package still starts.
            self.assertTrue(os.path.isfile(legacy))

    def test_a_new_slot_never_inherits_the_pre_save_slot_state(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = os.path.join(directory, 'garage_state.json')
            with open(legacy, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 4, 'vehicles': {}}, stream)

            path = self.config.save_slot_state_path(
                'garage_state.json', 'career', directory)

            self.assertFalse(os.path.isfile(path))

    def test_load_publishes_the_configured_slot_to_every_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(
                '{"schema": 3, "save_slot": "career"}', encoding='utf-8')

            config = self.config.load(str(path))

            self.assertEqual('career', config['save_slot'])
            self.assertEqual('career', self.config.active_save_slot())

    def test_an_unusable_configured_slot_is_quarantined_to_the_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(
                '{"schema": 3, "save_slot": "../escape"}', encoding='utf-8')

            config = self.config.load(str(path))

            self.assertEqual(
                self.config.DEFAULT_SAVE_SLOT, config['save_slot'])

    def test_a_config_written_before_save_slots_keeps_the_default_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"schema": 2}', encoding='utf-8')

            config = self.config.load(str(path))

            self.assertEqual(
                self.config.DEFAULT_SAVE_SLOT, config['save_slot'])
            self.assertEqual(
                self.config.DEFAULT_CONFIG['schema'], config['schema'])


class SaveSlotStoreTests(unittest.TestCase):
    """Every earned-progress store must follow the active slot."""

    def test_the_three_stores_name_their_own_file(self):
        names = {}
        for module in ('garage_store', 'postbattle_store', 'state'):
            source = (PACKAGE_ROOT / 'account_rpc' /
                      (module + '.py')).read_text(encoding='utf-8')
            self.assertIn(
                'port_config.save_slot_state_path(STATE_FILE_NAME)', source,
                module)
            for line in source.splitlines():
                if line.startswith('STATE_FILE_NAME = '):
                    names[module] = line.split('=', 1)[1].strip()
        self.assertEqual(
            {'garage_store': "'garage_state.json'",
             'postbattle_store': "'postbattle_state.json'",
             'state': "'account_state.json'"},
            names)

    def test_an_in_memory_store_is_still_available_to_the_hidden_worker(self):
        """``None`` keeps meaning "do not persist" after the slot change."""
        source = (PACKAGE_ROOT / 'bootstrap.py').read_text(encoding='utf-8')
        self.assertIn('AccountState(path=None)', source)
        state_source = (PACKAGE_ROOT / 'account_rpc' /
                        'state.py').read_text(encoding='utf-8')
        self.assertIn('if self._path is None', state_source)


if __name__ == '__main__':
    unittest.main()
