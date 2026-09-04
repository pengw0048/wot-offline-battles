import importlib.util
import ast
import os
import tempfile
import textwrap
import unittest


PORT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_PATH = os.path.join(
    PORT_ROOT, 'tools', 'audit_native_resource_ownership.py')
SOURCE_ROOT = os.path.join(
    PORT_ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
    'offline_lan_0922')


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        'audit_native_resource_ownership', TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _load_tool()


class NativeResourceOwnershipAuditTests(unittest.TestCase):
    def _synthetic_root(self, source):
        temporary = tempfile.TemporaryDirectory()
        path = os.path.join(temporary.name, 'synthetic.py')
        with open(path, 'w', encoding='utf-8') as source_file:
            source_file.write(textwrap.dedent(source))
        self.addCleanup(temporary.cleanup)
        return temporary.name

    def test_repository_inventory_and_ownership_contracts_pass(self):
        report = AUDIT.audit(SOURCE_ROOT)

        self.assertEqual(7, report['approvedAcquisitionSites'])
        self.assertGreaterEqual(report['checkedOwnershipContracts'], 14)

    def test_new_create_entity_call_site_is_rejected(self):
        source_root = self._synthetic_root('''
            def unreviewed_spawn(BigWorld):
                return BigWorld.createEntity(
                    'OfflineEntity', 1, 0, (0, 0, 0), (0, 0, 0), {})
        ''')

        with self.assertRaisesRegex(
                ValueError,
                r'synthetic\.py:unreviewed_spawn createEntity '
                r'acquisitions=1 expected=0'):
            AUDIT.audit_acquisition_inventory(
                source_root, approved_acquisitions=())

    def test_import_alias_cannot_hide_projectile_mover_acquisition(self):
        source_root = self._synthetic_root('''
            from ProjectileMover import ProjectileMover as NativeMover

            def unreviewed_tracer():
                return NativeMover()
        ''')

        with self.assertRaisesRegex(
                ValueError,
                r'synthetic\.py:unreviewed_tracer ProjectileMover '
                r'acquisitions=1 expected=0'):
            AUDIT.audit_acquisition_inventory(
                source_root, approved_acquisitions=())

    def test_second_constructor_in_reviewed_function_is_rejected(self):
        source_root = self._synthetic_root('''
            def reviewed(BigWorld):
                first = BigWorld.createSpace()
                second = BigWorld.createSpace()
                return first, second
        ''')
        approved = (('synthetic.py', 'reviewed', 'createSpace', 1),)

        with self.assertRaisesRegex(
                ValueError,
                r'synthetic\.py:reviewed createSpace acquisitions=2 '
                r'expected=1'):
            AUDIT.audit_acquisition_inventory(
                source_root, approved_acquisitions=approved)

    def test_ownership_commit_before_initialization_violates_order(self):
        tree = ast.parse(textwrap.dedent('''
            def setup(self):
                self._mover = ProjectileMover()
                set_space_id(self.space_id)
        '''))
        index = AUDIT._ModuleIndex()
        index.visit(tree)
        node = index.functions['setup']

        positions = AUDIT._ordered(
            AUDIT._events(node),
            (('call', 'ProjectileMover'), ('call', 'set_space_id'),
             ('assign', 'self._mover')))

        self.assertIsNone(positions)


if __name__ == '__main__':
    unittest.main()
