#!/usr/bin/env python
"""Reject unreviewed BigWorld/native resource acquisition lifecycles.

The #1513 client exposes several constructors whose return value is not proof
that the native object is ready.  This source audit keeps their call-site
inventory deliberately small and pins the ownership order at each reviewed
boundary.  The bytecode-facing stock contracts remain in
``audit_client_lifecycle.py``; this file guards our own adapter code.

The implementation intentionally uses only syntax and modules shared by
CPython 2.7 and Python 3 so it can run beside the target-client audits.
"""

from __future__ import print_function

import argparse
import ast
import io
import json
import os
import sys


try:
    STRING_TYPES = (basestring,)
except NameError:
    STRING_TYPES = (str,)


RESOURCE_CALLS = frozenset((
    'createSpace',
    'createEntity',
    'ProjectileMover',
    'PyTrackScroll',
))


# (relative path, containing function, acquisition name, expected count)
#
# A new entry is a code-review decision: adding a native constructor without
# extending this inventory fails the build, even if it is added to an already
# reviewed function.
APPROVED_ACQUISITIONS = (
    ('compat.py', 'OfflineCompatibility._create_account_player',
     'createSpace', 1),
    ('compat.py', 'OfflineCompatibility._create_account_player',
     'createEntity', 1),
    ('destructibles_authority.py', '_ensure_chunk', 'createEntity', 1),
    ('entities/bigworld_binding.py', 'BigWorldVehicleBinding.create_vehicle',
     'createEntity', 1),
    ('entities/remote_vehicle.py',
     '_RemoteShotPresenter._projectile_mover', 'ProjectileMover', 1),
    ('entities/remote_vehicle.py',
     'RemoteVehicleFactory._assemble_track_animation', 'PyTrackScroll', 1),
    ('entities/remote_vehicle.py', 'RemoteVehicleFactory._loaded',
     'createEntity', 1),
)


# Ordered events use (kind, suffix).  Calls are matched by their dotted callee
# suffix; commits are matched by their dotted/subscript assignment target.
ORDERED_CONTRACTS = (
    (
        'bootstrap.py',
        '_cleanup_runtime',
        'bootstrap callback ownership follows native cancellation',
        (('call', 'cancelCallback'), ('assign', '_callback_id')),
    ),
    (
        'bootstrap.py',
        '_cleanup_runtime',
        'bootstrap session ownership follows runtime teardown',
        (('call', 'session.stop'), ('assign', '_session')),
    ),
    (
        'bootstrap.py',
        '_cleanup_runtime',
        'worker presentation ownership follows native deactivation',
        (('call', 'worker_presentation.deactivate'),
         ('assign', '_worker_presentation')),
    ),
    (
        'bootstrap.py',
        '_cleanup_runtime',
        'intro skip ownership follows hook removal',
        (('call', 'intro_skip.uninstall'), ('assign', '_intro_skip')),
    ),
    (
        'bootstrap.py',
        '_cleanup_runtime',
        'announcement ownership follows hook removal',
        (('call', 'announcement_ui.uninstall'),
         ('assign', '_announcement_ui')),
    ),
    (
        'bootstrap.py',
        '_remove_lobby_listener',
        'lobby-listener ownership follows event-bus removal',
        (('call', 'g_eventBus.removeListener'),
         ('assign', '_lobby_listener_installed')),
    ),
    (
        'worker_presentation.py',
        'WorkerPresentation._restore_audio',
        'native volume restoration precedes audio-hook release',
        (('call', 'original'),
         ('assign', 'wwise.WW_setMasterVolume')),
    ),
    (
        'worker_presentation.py',
        'WorkerPresentation._rollback',
        'worker presentation owners survive all native restoration',
        (('call', 'self._restore_window'),
         ('call', 'self._restore_audio'), ('call', 'self._clear')),
    ),
    (
        'lobby_ui.py',
        'ServerAnnouncementUI.uninstall',
        'announcement hook ownership follows exact restoration',
        (('call', 'self._restore'), ('assign', 'self._installed')),
    ),
    (
        'lobby_ui.py',
        'IntroVideoSkip.uninstall',
        'startup hook entry is removed only after exact restoration',
        (('call', 'setattr'), ('call', 'self._replaced.pop')),
    ),
    (
        'authority_worker.py',
        'WorkerSession._start_round',
        'worker publishes a partial runtime before native battle startup',
        (('call', 'self._battle_factory'), ('assign', 'self.runtime'),
         ('call', 'runtime.start')),
    ),
    (
        'authority_worker.py',
        'WorkerSession._retire_runtime',
        'worker releases runtime ownership after native and draw teardown',
        (('call', 'runtime.stop'), ('call', 'self._draw.restore'),
         ('assign', 'self.runtime')),
    ),
    (
        'authority_worker.py',
        'WorkerSession._worker_failure',
        'worker transport ownership follows runtime and socket cleanup',
        (('call', '_retire_runtime'), ('call', 'client.stop'),
         ('assign', 'self.client')),
    ),
    (
        'authority_worker.py',
        'WorkerSession.stop',
        'explicit worker stop retains runtime through native cleanup',
        (('call', 'runtime.stop'), ('call', 'self._draw.restore'),
         ('assign', 'self.runtime')),
    ),
    (
        'authority_worker.py',
        'WorkerSession.stop',
        'explicit worker stop retains transport through socket cleanup',
        (('call', 'client.stop'), ('assign', 'self.client')),
    ),
    (
        'compat.py',
        'OfflineCompatibility._discard_partial_account',
        'partial Account cleanup releases spaces before repository ownership',
        (('call', 'clear_all_spaces'), ('call', 'delete_repository'),
         ('assign', 'runtime.account_module.g_accountRepository')),
    ),
    (
        'compat.py',
        'OfflineCompatibility._create_account_player',
        'offline Account is promoted only after its space and entity exist',
        (('call', 'createSpace'), ('call', 'createEntity'),
         ('call', 'player')),
    ),
    (
        'entities/remote_vehicle.py',
        '_RemoteShotPresenter._projectile_mover',
        'ProjectileMover ownership follows the stock space binding',
        (('call', 'ProjectileMover'), ('call', 'set_space_id'),
         ('assign', 'self._mover')),
    ),
    (
        'entities/remote_vehicle.py',
        '_RemoteShotPresenter.destroy',
        'ProjectileMover native teardown precedes owner release',
        (('call', 'callback'), ('assign', 'self._mover'),
         ('assign', 'self._projectile_shots')),
    ),
    (
        'entities/remote_vehicle.py',
        '_RemoteAppearance.detach',
        'bound-effect ownership follows native effect teardown',
        (('call', 'effects.destroy'),
         ('assign', 'self._bound_effects')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicleFactory._assemble_track_animation',
        'track-scroll ownership follows activation and filter binding',
        (('call', 'PyTrackScroll'), ('call', '_attach_flying_info'),
         ('call', 'activate'), ('call', 'setData'),
         ('call', 'attach_track_animation')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicle._release_track_animation',
        'track-scroll native cleanup precedes Python reference release',
        (('call', 'deactivate'), ('call', 'setData'),
         ('assign', 'self.track_scroll'),
         ('assign', 'self.track_filter')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicle._release_stickers',
        'sticker ownership follows native detach',
        (('call', 'stickers.detach'),
         ('assign', 'self._vehicle_stickers')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicle.retain_wreck_model',
        'wreck effect ownership follows native effect teardown',
        (('call', 'effects.destroy'),
         ('assign', 'self.appearance._bound_effects')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicle.detach_visual',
        'visual owners follow attached-resource and compound teardown',
        (('call', 'self._release_stickers'),
         ('call', 'self._release_track_animation'),
         ('assign', 'entity.model'), ('assign', 'model.matrix'),
         ('call', 'self.appearance.detach'),
         ('assign', 'self.bw_entity'),
         ('assign', 'self.bw_entity_id'), ('assign', 'self.model')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicleFactory._loaded',
        'fallback entity ownership follows native lookup and model setup',
        (('call', 'createEntity'), ('assign', 'visual'),
         ('call', '_assemble_track_animation'), ('assign', 'visual.model'),
         ('call', 'attach_visual')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicleFactory.destroy',
        'fallback entity owner is removed after native entity teardown',
        (('call', 'vehicle.detach_visual'), ('call', 'destroyEntity'),
         ('call', 'self._vehicles.pop')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicleFactory.destroy_all',
        'descriptor ownership follows chassis-shape teardown',
        (('call', 'forget_chassis_shape'),
         ('call', 'self._descriptors.pop')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicleFactory.destroy_all',
        'BSP tester ownership follows native model release',
        (('call', 'releaseBspModel'), ('call', 'self._hit_testers.pop')),
    ),
    (
        'destructibles_authority.py',
        '_ensure_chunk',
        'destructible request ownership follows createEntity validation',
        (('call', 'createEntity'), ('assign', 'entry'),
         ('assign', 'entities[]')),
    ),
    (
        'destructibles_authority.py',
        '_ensure_chunk',
        'failed destructible entity cleanup precedes retry ownership',
        (('call', 'destroy'), ('call', 'entities.pop'),
         ('call', 'createEntity')),
    ),
    (
        'entities/avatar_server.py',
        'AvatarServerBridge.destroy',
        'local Vehicle id remains owned through arena and engine teardown',
        (('call', 'arena_vehicle_removed'),
         ('call', 'destroy_entity'), ('assign', 'self._vehicle_id')),
    ),
    (
        'entities/native_remote_vehicle.py',
        'NativeRemoteVehicleFactory.create',
        'native Vehicle registries precede arena publication',
        (('call', 'create_vehicle'), ('assign', 'self._states[]'),
         ('assign', 'self._vehicles[]'),
         ('call', 'arena_vehicle_added')),
    ),
    (
        'entities/native_remote_vehicle.py',
        '_NativeRemoteState.detach',
        'native pose overlay cleanup precedes state owner release',
        (('call', 'clear_vehicle_pose_overlay'),
         ('assign', 'self.model_changed'), ('assign', 'self.entity')),
    ),
    (
        'entities/native_remote_vehicle.py',
        'NativeRemoteVehicleFactory.destroy',
        'native Vehicle owner is removed after engine teardown',
        (('call', 'state.detach'), ('call', 'destroy_entity'),
         ('call', 'self._states.pop'), ('call', 'self._vehicles.pop')),
    ),
    (
        'entities/native_remote_vehicle.py',
        'NativeRemoteVehicleFactory._retire_failed_creates',
        'failed native Vehicle id is discarded only after engine teardown',
        (('call', 'destroy_entity'),
         ('call', 'self._failed_creates.discard')),
    ),
)


# Cleanup orders are required inside one exception handler.  This prevents a
# nearby cleanup helper or an unrelated normal-path call from satisfying the
# contract accidentally.
HANDLER_CONTRACTS = (
    (
        'compat.py',
        'OfflineCompatibility._create_account_player',
        'partial Account creation clears entity/space ownership',
        (('call', '_discard_partial_account'),),
    ),
    (
        'entities/remote_vehicle.py',
        '_RemoteShotPresenter._projectile_mover',
        'partial ProjectileMover setup destroys before reporting failure',
        (('call', 'destroy'), ('call', '_report_failure')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicleFactory._assemble_track_animation',
        'partial PyTrackScroll setup removes callback and raw filter data',
        (('call', 'deactivate'), ('call', 'setData')),
    ),
    (
        'entities/remote_vehicle.py',
        'RemoteVehicleFactory._loaded',
        'partial fallback entity setup destroys the native entity',
        (('call', 'destroyEntity'),),
    ),
    (
        'entities/native_remote_vehicle.py',
        'NativeRemoteVehicleFactory.create',
        'failed arena publication removes registries before tombstoning',
        (('call', 'self._states.pop'), ('call', 'self._vehicles.pop'),
         ('call', 'self._failed_creates.add')),
    ),
)


REQUIRED_IDENTIFIERS = (
    (
        'compat.py',
        'OfflineCompatibility._create_account_player',
        ('_OFFLINE_INIT_COMPLETE', '_OFFLINE_PLAYER_READY'),
        'Account readiness must be checked before ownership is returned',
    ),
    (
        'destructibles_authority.py',
        '_ensure_chunk',
        ('entityID', 'pending', 'ready'),
        'destructible entity ids remain pending until a controller exists',
    ),
)


REQUIRED_EXCEPTION_BINDINGS = (
    (
        'entities/native_remote_vehicle.py',
        'NativeRemoteVehicleFactory.create',
        'error',
        '_retire_failed_creates',
        'arena registration cleanup retains the original exception',
    ),
)


def _relative_path(path):
    return path.replace(os.sep, '/')


def _attribute_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Subscript):
        base = _attribute_name(node.value)
        if base:
            parts.append(base + '[]')
    if not parts:
        return None
    return '.'.join(reversed(parts))


def _literal_string(node):
    string_node = getattr(ast, 'Str', None)
    if string_node is not None and isinstance(node, string_node):
        return node.s
    # ast.Constant does not exist on CPython 2.7.
    constant = getattr(ast, 'Constant', None)
    if constant is not None and isinstance(node, constant):
        return node.value if isinstance(node.value, STRING_TYPES) else None
    return None


def _assignment_target(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _attribute_name(node)
    if isinstance(node, ast.Subscript):
        base = _attribute_name(node.value)
        return None if base is None else base + '[]'
    return None


class _ModuleIndex(ast.NodeVisitor):
    def __init__(self):
        self._scope = []
        self.functions = {}
        self.acquisitions = []
        self._import_aliases = {}
        self._assignment_aliases = {}

    def _qualname(self):
        return '.'.join(self._scope) if self._scope else '<module>'

    def visit_ClassDef(self, node):
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node):
        self._scope.append(node.name)
        self.functions[self._qualname()] = node
        self.generic_visit(node)
        self._scope.pop()

    def visit_ImportFrom(self, node):
        for item in node.names:
            if item.name in RESOURCE_CALLS:
                self._import_aliases[item.asname or item.name] = item.name

    def visit_Assign(self, node):
        canonical = None
        value_name = _attribute_name(node.value)
        if value_name:
            leaf = value_name.rsplit('.', 1)[-1].replace('[]', '')
            if leaf in RESOURCE_CALLS:
                canonical = leaf
        if canonical is not None:
            for target in node.targets:
                name = _assignment_target(target)
                if name is not None and '.' not in name and '[]' not in name:
                    self._assignment_aliases[(self._qualname(), name)] = \
                        canonical
        self.generic_visit(node)

    def _resource_name(self, callee):
        name = _attribute_name(callee)
        if name:
            leaf = name.rsplit('.', 1)[-1].replace('[]', '')
            if leaf in RESOURCE_CALLS:
                return leaf
            if isinstance(callee, ast.Name):
                alias = self._assignment_aliases.get(
                    (self._qualname(), callee.id))
                if alias is None:
                    alias = self._import_aliases.get(callee.id)
                if alias is not None:
                    return alias
        # Reject direct getattr(BigWorld, 'createEntity')(...) evasions too.
        if isinstance(callee, ast.Call):
            getter = _attribute_name(callee.func)
            if getter and getter.rsplit('.', 1)[-1] == 'getattr' and \
                    len(callee.args) >= 2:
                value = _literal_string(callee.args[1])
                if value in RESOURCE_CALLS:
                    return value
        return None

    def visit_Call(self, node):
        resource = self._resource_name(node.func)
        if resource is not None:
            self.acquisitions.append((self._qualname(), resource,
                                      int(getattr(node, 'lineno', 0))))
        self.generic_visit(node)


class _EventIndex(ast.NodeVisitor):
    def __init__(self):
        self.events = []

    def visit_FunctionDef(self, unused_node):
        # A nested helper does not execute as part of the reviewed lifecycle.
        return None

    def visit_Lambda(self, unused_node):
        return None

    def visit_Call(self, node):
        name = _attribute_name(node.func)
        if name is None and isinstance(node.func, ast.Name):
            name = node.func.id
        if name is not None:
            self.events.append(('call', name,
                                int(getattr(node, 'lineno', 0))))
        self.generic_visit(node)

    def visit_Assign(self, node):
        # Evaluate/record the acquisition before committing its returned owner.
        self.visit(node.value)
        for target in node.targets:
            name = _assignment_target(target)
            if name is not None:
                self.events.append(('assign', name,
                                    int(getattr(node, 'lineno', 0))))

    def visit_AnnAssign(self, node):
        # Reached only under Python 3; keeping it here makes synthetic tests
        # fail consistently if typed code is ever introduced into the tree.
        if node.value is not None:
            self.visit(node.value)
        name = _assignment_target(node.target)
        if name is not None:
            self.events.append(('assign', name,
                                int(getattr(node, 'lineno', 0))))


def _events(node):
    index = _EventIndex()
    if isinstance(node, ast.FunctionDef):
        for statement in node.body:
            index.visit(statement)
    else:
        index.visit(node)
    return tuple(index.events)


def _event_matches(event, marker):
    kind, name, unused_line = event
    expected_kind, suffix = marker
    return kind == expected_kind and \
        (name == suffix or name.endswith('.' + suffix))


def _ordered(events, markers):
    positions = []
    after = -1
    for marker in markers:
        match = None
        for index in range(after + 1, len(events)):
            if _event_matches(events[index], marker):
                match = index
                break
        if match is None:
            return None
        positions.append(events[match][2])
        after = match
    return tuple(positions)


class _HandlerIndex(ast.NodeVisitor):
    def __init__(self):
        self.handlers = []

    def visit_FunctionDef(self, unused_node):
        return None

    def visit_Lambda(self, unused_node):
        return None

    def generic_visit(self, node):
        for handler in getattr(node, 'handlers', ()):
            if handler not in self.handlers:
                self.handlers.append(handler)
        ast.NodeVisitor.generic_visit(self, node)


def _handlers(node):
    index = _HandlerIndex()
    if isinstance(node, ast.FunctionDef):
        for statement in node.body:
            index.visit(statement)
    else:
        index.visit(node)
    return tuple(index.handlers)


def _handler_binding(handler):
    name = getattr(handler, 'name', None)
    if isinstance(name, STRING_TYPES):
        return name
    if isinstance(name, ast.Name):
        return name.id
    return None


def _identifier_inventory(node):
    values = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            values.add(child.id)
        elif isinstance(child, ast.Attribute):
            values.add(child.attr)
        else:
            value = _literal_string(child)
            if value is not None:
                values.add(value)
    return values


def _destructible_guard_precedes_commit(node):
    acquire_line = None
    guard_line = None
    commit_line = None
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _attribute_name(child.func)
            if name and name.rsplit('.', 1)[-1] == 'createEntity':
                acquire_line = int(child.lineno)
        elif isinstance(child, ast.If):
            identifiers = _identifier_inventory(child.test)
            if 'entityID' in identifiers and 'int' in identifiers:
                guard_line = int(child.lineno)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if _assignment_target(target) == 'entities[]':
                    commit_line = int(child.lineno)
    return (acquire_line is not None and guard_line is not None and
            commit_line is not None and
            acquire_line < guard_line < commit_line)


def _read_modules(source_root):
    modules = {}
    for directory, dirnames, filenames in os.walk(source_root):
        dirnames[:] = sorted(name for name in dirnames
                            if name != '__pycache__')
        for filename in sorted(filenames):
            if not filename.endswith('.py'):
                continue
            path = os.path.join(directory, filename)
            relative = _relative_path(os.path.relpath(path, source_root))
            # Keep bytes here.  CPython 2.7 rejects a Unicode input containing
            # its own ``# coding:`` declaration, while both supported parsers
            # correctly decode a byte source according to that declaration.
            with io.open(path, 'rb') as source_file:
                source = source_file.read()
            try:
                tree = ast.parse(source, filename=relative)
            except SyntaxError as error:
                raise ValueError('%s cannot be parsed: %s' %
                                 (relative, error))
            index = _ModuleIndex()
            index.visit(tree)
            modules[relative] = (tree, index)
    return modules


def _function(modules, relative, qualname):
    module = modules.get(relative)
    if module is None:
        raise ValueError('missing reviewed source file: %s' % relative)
    node = module[1].functions.get(qualname)
    if node is None:
        raise ValueError('missing reviewed function: %s:%s' %
                         (relative, qualname))
    return node


def _acquisition_errors(modules, approved_acquisitions):
    actual = {}
    locations = {}
    for relative, unused_tree_and_index in sorted(modules.items()):
        index = unused_tree_and_index[1]
        for qualname, resource, line in index.acquisitions:
            key = (relative, qualname, resource)
            actual[key] = actual.get(key, 0) + 1
            locations.setdefault(key, []).append(line)

    expected = dict(
        ((relative, qualname, resource), count)
        for relative, qualname, resource, count in approved_acquisitions)
    errors = []
    for key in sorted(set(actual) | set(expected)):
        actual_count = actual.get(key, 0)
        expected_count = expected.get(key, 0)
        if actual_count != expected_count:
            relative, qualname, resource = key
            errors.append(
                '%s:%s %s acquisitions=%s expected=%s lines=%r' %
                (relative, qualname, resource, actual_count, expected_count,
                 locations.get(key, ())))
    return expected, errors


def audit_acquisition_inventory(source_root, approved_acquisitions=None):
    """Audit only the closed constructor inventory.

    Tests use this narrow entry point with synthetic sources; the build calls
    :func:`audit`, which additionally checks all ownership contracts.
    """
    source_root = os.path.abspath(source_root)
    if not os.path.isdir(source_root):
        raise ValueError('native adapter source root not found: %s' %
                         source_root)
    modules = _read_modules(source_root)
    if approved_acquisitions is None:
        approved_acquisitions = APPROVED_ACQUISITIONS
    expected, errors = _acquisition_errors(
        modules, approved_acquisitions)
    if errors:
        raise ValueError('; '.join(errors))
    return {
        'sourceRoot': source_root,
        'approvedAcquisitionSites': len(expected),
    }


def audit(source_root):
    source_root = os.path.abspath(source_root)
    if not os.path.isdir(source_root):
        raise ValueError('native adapter source root not found: %s' %
                         source_root)
    modules = _read_modules(source_root)
    expected, errors = _acquisition_errors(
        modules, APPROVED_ACQUISITIONS)

    checked = []
    for relative, qualname, reason, markers in ORDERED_CONTRACTS:
        try:
            node = _function(modules, relative, qualname)
        except ValueError as error:
            errors.append(str(error))
            continue
        positions = _ordered(_events(node), markers)
        if positions is None:
            errors.append('%s:%s violates ownership order %r' %
                          (relative, qualname, markers))
        else:
            checked.append({'file': relative, 'function': qualname,
                            'contract': reason, 'lines': positions})

    for relative, qualname, reason, markers in HANDLER_CONTRACTS:
        try:
            node = _function(modules, relative, qualname)
        except ValueError as error:
            errors.append(str(error))
            continue
        matching = None
        for handler in _handlers(node):
            positions = _ordered(_events(handler), markers)
            if positions is not None:
                matching = positions
                break
        if matching is None:
            errors.append('%s:%s lacks exception cleanup order %r' %
                          (relative, qualname, markers))
        else:
            checked.append({'file': relative, 'function': qualname,
                            'contract': reason, 'lines': matching})

    for relative, qualname, identifiers, reason in REQUIRED_IDENTIFIERS:
        try:
            node = _function(modules, relative, qualname)
        except ValueError as error:
            errors.append(str(error))
            continue
        present = _identifier_inventory(node)
        missing = tuple(item for item in identifiers if item not in present)
        if missing:
            errors.append('%s:%s lacks lifecycle identifiers %r' %
                          (relative, qualname, missing))
        else:
            checked.append({'file': relative, 'function': qualname,
                            'contract': reason})

    for (relative, qualname, binding, cleanup_call,
         reason) in REQUIRED_EXCEPTION_BINDINGS:
        try:
            node = _function(modules, relative, qualname)
        except ValueError as error:
            errors.append(str(error))
            continue
        matching = None
        for handler in _handlers(node):
            if _handler_binding(handler) != binding:
                continue
            positions = _ordered(
                _events(handler), (('call', cleanup_call),))
            if positions is not None:
                matching = positions
                break
        if matching is None:
            errors.append(
                '%s:%s must bind exception %s around cleanup %s' %
                (relative, qualname, binding, cleanup_call))
        else:
            checked.append({'file': relative, 'function': qualname,
                            'contract': reason, 'lines': matching})

    try:
        destructible = _function(
            modules, 'destructibles_authority.py', '_ensure_chunk')
    except ValueError as error:
        errors.append(str(error))
    else:
        if not _destructible_guard_precedes_commit(destructible):
            errors.append(
                'destructibles_authority.py:_ensure_chunk must validate the '
                'positive createEntity id before committing entities[chunkID]')
        else:
            checked.append({
                'file': 'destructibles_authority.py',
                'function': '_ensure_chunk',
                'contract': 'positive createEntity id guard precedes commit',
            })

    if errors:
        raise ValueError('; '.join(errors))
    return {
        'sourceRoot': source_root,
        'pythonRuntime': '%d.%d.%d' % sys.version_info[:3],
        'approvedAcquisitionSites': len(expected),
        'checkedOwnershipContracts': len(checked),
        'contracts': checked,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Audit reviewed BigWorld/native ownership boundaries.')
    parser.add_argument('source_root')
    args = parser.parse_args(argv)
    try:
        report = audit(args.source_root)
    except (IOError, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
