"""Give the stock lobby feature configs something to read offline.

Offline there is no account login, so lobbyContext holds an empty
ServerSettings: schema models are missing and lobby predicates such as the
story-mode app-loader observer raise while the gameplay state machine
notifies its observers. This module installs real models where an empty
config deserializes, reports every feature that stays unconfigured, and
answers the remaining lookups with a disabled-feature model.
"""
from __future__ import absolute_import


class _DisabledConfig(object):
    """Every read reports "feature is off"."""

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return DISABLED_CONFIG

    def __nonzero__(self):
        return False

    __bool__ = __nonzero__

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __contains__(self, item):
        return False

    def __call__(self, *args, **kwargs):
        return DISABLED_CONFIG

    def __repr__(self):
        return '<offline disabled config>'


DISABLED_CONFIG = _DisabledConfig()

_original_get_config_model = None
_installed_get_config_model = None
_reported_schemas = set()


def install(log):
    global _original_get_config_model, _installed_get_config_model
    from helpers import server_settings
    if _installed_get_config_model is not None:
        return
    settings_class = server_settings.ServerSettings
    original = settings_class.getConfigModel

    def get_config_model(settings, schema):
        try:
            return original(settings, schema)
        except Exception:
            key = getattr(schema, 'gpKey', None)
            if key not in _reported_schemas:
                _reported_schemas.add(key)
                log('config_model_disabled schema=%s' % (key,))
            return DISABLED_CONFIG

    settings_class.getConfigModel = get_config_model
    _original_get_config_model = original
    _installed_get_config_model = get_config_model
    log('config_model_fallback_installed')


def uninstall(log):
    global _original_get_config_model, _installed_get_config_model
    if _installed_get_config_model is None:
        return
    from helpers import server_settings
    settings_class = server_settings.ServerSettings
    if settings_class.getConfigModel is _installed_get_config_model:
        settings_class.getConfigModel = _original_get_config_model
    _original_get_config_model = None
    _installed_get_config_model = None
    _reported_schemas.clear()
    log('config_model_fallback_removed')


def seed(log, phase):
    """Publish ServerSettings so schema models and feature configs exist."""
    from helpers import dependency
    from schema_manager import getSchemaManager
    from game_params_common.scope import clientFilter
    from skeletons.gui.lobby_context import ILobbyContext
    seeded = {}
    unconfigured = []
    for info in getSchemaManager().getSchemasInfo():
        schema = info.schema
        try:
            schema.deserialize({}, filter_=clientFilter, skipValidation=True)
        except Exception:
            unconfigured.append(schema.gpKey)
            continue
        seeded[schema.gpKey] = {}
    lobby_context = dependency.instance(ILobbyContext)
    current = lobby_context.getServerSettings()
    if current is not None:
        merged = dict(current.getSettings())
        merged.update(seeded)
        seeded = merged
    lobby_context.setServerSettings(seeded)
    _notify_lobby_controllers(log)
    log('server_settings_seeded phase=%s models=%s unconfigured=%s'
        % (phase, len(seeded), len(unconfigured)))


def _notify_lobby_controllers(log):
    """Hand the new settings to controllers that only read them on login."""
    from helpers import dependency
    from skeletons.gui.game_control import IVehiclePostProgressionController
    controller = dependency.instance(IVehiclePostProgressionController)
    try:
        controller.onAccountBecomePlayer()
    except Exception as error:
        log('lobby_controller_refresh_failed controller=%s error=%s'
            % (type(controller).__name__, type(error).__name__))
