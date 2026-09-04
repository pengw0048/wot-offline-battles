import importlib.util
import os
import unittest


PORT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_PATH = os.path.join(PORT_ROOT, 'tools', 'audit_client_abi.py')


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        'audit_client_abi_projectile_test', TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _load_audit()


def _instruction(offset, opname, value=None, argument=None):
    return {
        'offset': offset,
        'opname': opname,
        'value': value,
        'argument': argument,
    }


def _unknown_projectile_explosion_fixture():
    """CPython 2.7 instruction fixture for the effect-only branch."""
    return [
        _instruction(19, 'LOAD_FAST', 'self', 0),
        _instruction(
            22, 'LOAD_ATTR', '_ProjectileMover__projectiles', 1),
        _instruction(25, 'LOAD_ATTR', 'get', 2),
        _instruction(28, 'LOAD_FAST', 'shotID', 1),
        _instruction(31, 'CALL_FUNCTION', argument=1),
        _instruction(34, 'STORE_FAST', 'proj', 6),
        _instruction(37, 'LOAD_FAST', 'proj', 6),
        _instruction(40, 'LOAD_CONST', None, 0),
        _instruction(43, 'COMPARE_OP', argument=8),
        _instruction(46, 'POP_JUMP_IF_FALSE', argument=108),
        _instruction(49, 'BUILD_MAP', argument=0),
        _instruction(52, 'STORE_FAST', '_ProjectileMover__proj', 7),
        _instruction(55, 'LOAD_FAST', 'effectsDescr', 2),
        _instruction(58, 'LOAD_FAST', '_ProjectileMover__proj', 7),
        _instruction(61, 'LOAD_CONST', 'effectsDescr', 2),
        _instruction(64, 'STORE_SUBSCR'),
        _instruction(65, 'LOAD_FAST', 'effectMaterial', 3),
        _instruction(68, 'LOAD_FAST', '_ProjectileMover__proj', 7),
        _instruction(71, 'LOAD_CONST', 'effectMaterial', 3),
        _instruction(74, 'STORE_SUBSCR'),
        _instruction(75, 'LOAD_CONST', 0, 4),
        _instruction(78, 'LOAD_FAST', '_ProjectileMover__proj', 7),
        _instruction(81, 'LOAD_CONST', 'attackerID', 5),
        _instruction(84, 'STORE_SUBSCR'),
        _instruction(85, 'LOAD_FAST', 'self', 0),
        _instruction(
            88, 'LOAD_ATTR', '_ProjectileMover__addExplosionEffect', 4),
        _instruction(91, 'LOAD_FAST', 'endPoint', 4),
        _instruction(94, 'LOAD_FAST', '_ProjectileMover__proj', 7),
        _instruction(97, 'LOAD_FAST', 'velocityDir', 5),
        _instruction(100, 'CALL_FUNCTION', argument=3),
        _instruction(103, 'POP_TOP'),
        _instruction(104, 'LOAD_CONST', None, 0),
        _instruction(107, 'RETURN_VALUE'),
        _instruction(108, 'LOAD_FAST', 'proj', 6),
        _instruction(111, 'LOAD_CONST', 'fireMissedTrigger', 6),
        _instruction(114, 'BINARY_SUBSCR'),
    ]


class ClientAbiProjectileAuditTests(unittest.TestCase):
    def test_controlled_tracer_dependencies_are_pinned(self):
        effects = AUDIT.EXPECTED_ABI[
            'scripts/client/helpers/EffectsList.pyc']
        self.assertEqual(
            ('self', 'model', 'data', 'key', '**args'),
            effects['EffectsList.attachTo'])
        self.assertEqual(
            ('self', 'data', 'key'),
            effects['EffectsList.detachFrom'])
        self.assertEqual(
            ('self', 'data', 'keepPosteffects'),
            effects['EffectsList.detachAllFrom'])

        control = AUDIT.EXPECTED_CODE_NAMES[
            'scripts/client/AvatarInputHandler/control_modes.pyc']
        self.assertTrue({
            '_ShellingControl__targetModel', 'motors', 'signal',
            'Math', 'Matrix',
        }.issubset(control['_ShellingControl.setTargetModelMatrix']))
        self.assertTrue({
            'BigWorld', 'Model', 'addMotor', 'Servo', 'Math', 'Matrix',
            'addModel', 'delModel',
        }.issubset(control['_ShellingControl.__createTargetModel']))

        selected_area = AUDIT.EXPECTED_CODE_NAMES[
            'scripts/client/CombatSelectedArea.pyc']
        self.assertTrue({
            'BigWorld', 'Model', 'addModel', 'Math', 'Matrix', 'Servo',
            'addMotor', '_CombatSelectedArea__matrix',
        }.issubset(selected_area['CombatSelectedArea.setup']))
        self.assertTrue({
            '_CombatSelectedArea__matrix', 'setRotateYPR', 'translation',
        }.issubset(selected_area['CombatSelectedArea.relocate']))

        mover_names = AUDIT.EXPECTED_CODE_NAMES[
            'scripts/client/ProjectileMover.pyc']
        self.assertTrue({
            'BigWorld', 'Model', 'addModel', 'addMotor', 'visible',
            'visibleAttachments', 'attachTo', 'FlockManager', 'onProjectile',
        }.issubset(mover_names['ProjectileMover.add']))
        self.assertTrue({
            'TriggersManager', 'fireTrigger', 'PLAYER_SHOT_MISSED',
        }.issubset(mover_names['ProjectileMover.explode']))
        self.assertTrue({
            'onProjectileHit', 'FlockManager', 'getManager', 'onProjectile',
        }.issubset(mover_names['ProjectileMover.__notifyProjectileHit']))
        self.assertTrue({
            'detachFrom', '_ProjectileMover__addExplosionEffect',
        }.issubset(mover_names['ProjectileMover.__killProjectile']))
        self.assertTrue({
            'detachAllFrom', 'delMotor', 'delModel',
        }.issubset(mover_names['ProjectileMover.__delProjectile']))

        mover_literals = AUDIT.EXPECTED_CODE_LITERALS[
            'scripts/client/ProjectileMover.pyc']
        self.assertTrue({
            'artilleryID', 'projectile', 'flying', 'isPlayerVehicle',
            'isArtillery',
        }.issubset(mover_literals['ProjectileMover.add']))
        self.assertTrue({
            'effectsDescr', 'effectMaterial', 'attackerID',
            'fireMissedTrigger', 'showExplosion',
        }.issubset(mover_literals['ProjectileMover.explode']))
        self.assertTrue({
            'effectsDescr', 'caliber', 'autoScaleProjectile',
        }.issubset(
            mover_literals['ProjectileMover.__notifyProjectileHit']))
        self.assertTrue({
            'effectsDescr', 'projectile', 'effectsData', 'stopFlying',
            'showExplosion',
        }.issubset(mover_literals['ProjectileMover.__killProjectile']))
        self.assertTrue({
            'effectsDescr', 'projectile', 'effectsData', 'model', 'motor',
        }.issubset(mover_literals['ProjectileMover.__delProjectile']))
        for method in (
                'ProjectileMover.add', 'ProjectileMover.explode',
                'ProjectileMover.__notifyProjectileHit',
                'ProjectileMover.__killProjectile',
                'ProjectileMover.__delProjectile'):
            self.assertTrue(
                set(mover_names[method]).isdisjoint(mover_literals[method]))
        self.assertTrue({
            'fireMissedTrigger', 'TriggersManager', 'g_manager',
            'fireTrigger', 'TRIGGER_TYPE', 'PLAYER_SHOT_MISSED',
        }.isdisjoint(mover_names['ProjectileMover.__delProjectile']))
        self.assertEqual(
            2.0,
            AUDIT.EXPECTED_CLASS_CONSTANTS[
                'scripts/client/ProjectileMover.pyc'][
                    'ProjectileMover'][
                        '_ProjectileMover__PROJECTILE_TIME_AFTER_DEATH'])

    def test_unknown_projectile_uses_effect_only_terminal_branch(self):
        self.assertTrue(
            AUDIT._matches_unknown_projectile_explosion_branch(
                _unknown_projectile_explosion_fixture()))

    def test_projectile_lookup_must_guard_the_effect_branch(self):
        instructions = _unknown_projectile_explosion_fixture()
        instructions[9]['opname'] = 'POP_JUMP_IF_TRUE'

        self.assertFalse(
            AUDIT._matches_unknown_projectile_explosion_branch(instructions))

    def test_synthetic_projectile_must_carry_zero_attacker(self):
        instructions = _unknown_projectile_explosion_fixture()
        instructions[20]['value'] = 1

        self.assertFalse(
            AUDIT._matches_unknown_projectile_explosion_branch(instructions))

    def test_unknown_projectile_branch_cannot_launch_a_projectile(self):
        for forbidden in ('add', 'addProjectile'):
            with self.subTest(forbidden=forbidden):
                instructions = _unknown_projectile_explosion_fixture()
                instructions[25]['value'] = forbidden

                self.assertFalse(
                    AUDIT._matches_unknown_projectile_explosion_branch(
                        instructions))

    def test_unknown_projectile_branch_must_return_before_live_path(self):
        instructions = _unknown_projectile_explosion_fixture()
        instructions[32]['opname'] = 'NOP'

        self.assertFalse(
            AUDIT._matches_unknown_projectile_explosion_branch(instructions))


if __name__ == '__main__':
    unittest.main()
