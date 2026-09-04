from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PORT_ROOT = ROOT


class ReleaseBuilderTests(unittest.TestCase):
    def test_the_package_defaults_to_loopback_and_keeps_user_settings(self):
        source = (PORT_ROOT / 'build_wotmod.py').read_text(encoding='utf-8')
        self.assertIn("'host': '127.0.0.1'", source)
        self.assertIn("'port': 28782", source)
        self.assertNotIn('os.environ.get(', source)
        self.assertNotIn('server_endpoint.json', source)

    def test_the_package_never_carries_user_owned_state_files(self):
        """State files are created in the player's external data directory."""
        source = (PORT_ROOT / 'build_wotmod.py').read_text(encoding='utf-8')
        for name in ('server_endpoint.json', 'account_state.json',
                     'garage_state.json', 'postbattle_state.json',
                     'waiting_room_state.json'):
            self.assertNotIn(name, source, name)

    def test_the_state_owners_write_only_into_the_external_user_directory(self):
        package = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
                   'mods' / 'offline_lan_0922')
        for module, name in (
                ('account_rpc/state.py', 'account_state.json'),
                ('account_rpc/garage_store.py', 'garage_state.json'),
                ('account_rpc/postbattle_store.py',
                 'postbattle_state.json')):
            source = (package / module).read_text(encoding='utf-8')
            self.assertIn(name, source, module)
            self.assertIn('port_config.USER_DATA_DIR', source, module)
            self.assertIn('migrate_legacy_user_file', source, module)


if __name__ == '__main__':
    unittest.main()
