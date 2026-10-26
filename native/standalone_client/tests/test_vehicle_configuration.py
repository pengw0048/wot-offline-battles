"""Authority configuration must not rewrite the stock presentation cache."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = (Path(__file__).resolve().parents[1] / 'bigworld/res/scripts/client/'
          'StandaloneVehicleConfiguration.py')


class VehicleType:
    __slots__ = ('id', 'name', 'mode', 'xphysics', 'speedLimits')


class CompositeVehicleDescriptor:
    def __init__(self, normal, siege):
        self.__dict__['_CompositeVehicleDescriptor__vehicleDescr'] = normal
        self.__dict__['_CompositeVehicleDescriptor__siegeDescr'] = siege
        self.__dict__['_CompositeVehicleDescriptor__vehicleMode'] = 1

    @property
    def defaultVehicleDescr(self):
        return self.__vehicleDescr

    @property
    def siegeVehicleDescr(self):
        return self.__siegeDescr

    def __getattr__(self, name):
        selected = self.__siegeDescr if self.__vehicleMode == 1 else self.__vehicleDescr
        return getattr(selected, name)

    def __setattr__(self, name, value):
        setattr(self.__vehicleDescr, name, value)
        setattr(self.__siegeDescr, name, value)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.resources = {}
        self.calls = []
        self.detailed = {'mode': 1, 'detailed': {'gravityFactor': 1.0}}
        self.vehicles = ModuleType('items.vehicles')
        self.vehicles._VEHICLE_TYPE_XML_PATH = 'scripts/item_defs/vehicles/'
        self.vehicles.VEHICLE_MODE_FILE_SUFFIX = {0: '', 1: '_siege_mode'}
        self.vehicles.CompositeVehicleDescriptor = CompositeVehicleDescriptor

        def read(context, section, subsection):
            self.calls.append(('read', context, section, subsection))
            return self.detailed

        self.vehicles._readXPhysics = read
        items = ModuleType('items')
        items.vehicles = self.vehicles
        modules = {
            'items': items, 'items.vehicles': self.vehicles,
            'ResMgr': SimpleNamespace(openSection=self.resources.get),
            'nations': SimpleNamespace(NAMES=('ussr', 'sweden')),
            'physics_shared': SimpleNamespace(initVehiclePhysicsServer=
                lambda physics, descriptor: self.calls.append(
                    ('configure', physics, descriptor))),
        }
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('configuration_under_test', SOURCE)
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def descriptor(self, nation=0, name='T-34', mode=0):
        kind = VehicleType()
        kind.id = (nation, 1)
        kind.name = ('ussr', 'sweden')[nation] + ':' + name
        kind.mode = mode
        kind.xphysics = {'rotationSpeedLimit': 0.4}
        kind.speedLimits = (15.0, 5.0)
        return SimpleNamespace(type=kind, chassis=object(), engine=object(),
                               weight=30000.0, miscAttrs={'enginePower': 1.05})

    def test_original_server_producer_gets_selected_components_and_own_type(self):
        source = self.descriptor()
        resource = 'scripts/item_defs/vehicles/ussr/T-34.xml'
        section = object()
        self.resources[resource] = section
        physics = object()
        result = self.module.configure(physics, source)
        self.assertIsNot(result, source)
        self.assertIsNot(result.type, source.type)
        self.assertIs(result.chassis, source.chassis)
        self.assertIs(result.engine, source.engine)
        self.assertEqual(result.miscAttrs, source.miscAttrs)
        self.assertEqual(self.calls[0], ('read', (None, resource), section, 'physics'))
        self.assertEqual(self.calls[1], ('configure', physics, result))
        result.type.xphysics['detailed']['gravityFactor'] = 0.8
        self.assertEqual(source.type.xphysics, {'rotationSpeedLimit': 0.4})

    def test_siege_mode_uses_original_mode_resource_table(self):
        source = self.descriptor(1, 'S16_Kranvagn', 1)
        resource = 'scripts/item_defs/vehicles/sweden/S16_Kranvagn_siege_mode.xml'
        self.resources[resource] = object()
        self.module.authorityDescriptor(source)
        self.assertEqual(self.calls[0][1], (None, resource))

    def test_missing_resource_never_configures_with_guessed_defaults(self):
        with self.assertRaisesRegex(RuntimeError, 'resource is unavailable'):
            self.module.configure(object(), self.descriptor())
        self.assertEqual(self.calls, [])

    def test_composite_keeps_two_modes_and_does_not_forward_type_into_client(self):
        normal = self.descriptor(1, 'S10_Strv_103_0', 0)
        siege = self.descriptor(1, 'S10_Strv_103_0', 1)
        source = CompositeVehicleDescriptor(normal, siege)
        for suffix in ('', '_siege_mode'):
            self.resources['scripts/item_defs/vehicles/sweden/S10_Strv_103_0' + suffix + '.xml'] = object()
        original_types = normal.type, siege.type
        result = self.module.authorityDescriptor(source)
        self.assertEqual((normal.type, siege.type), original_types)
        self.assertIsNot(result.defaultVehicleDescr, normal)
        self.assertIsNot(result.siegeVehicleDescr, siege)
        self.assertIsNot(result.defaultVehicleDescr.type, result.siegeVehicleDescr.type)
        self.assertEqual(result.type.mode, 1)
        self.assertEqual(normal.type.xphysics, {'rotationSpeedLimit': 0.4})
        self.assertEqual(siege.type.xphysics, {'rotationSpeedLimit': 0.4})

    def test_missing_detailed_configuration_preserves_client_cache(self):
        source = self.descriptor()
        self.resources['scripts/item_defs/vehicles/ussr/T-34.xml'] = object()
        for missing in (None, {'mode': 1, 'detailed': None}):
            self.detailed = missing
            with self.assertRaisesRegex(RuntimeError, 'no detailed physics'):
                self.module.configure(object(), source)
        self.assertEqual(source.type.xphysics, {'rotationSpeedLimit': 0.4})
        self.assertFalse(any(call[0] == 'configure' for call in self.calls))


if __name__ == '__main__':
    unittest.main()
