"""Account permission fields consumed before native garage RPCs are sent."""

import json
import os
from pathlib import Path
import subprocess
import unittest

import test_port_0922_garage as fixture


NATIVE_PROBE = r'''
from __future__ import print_function
import json
import imp
import marshal
import os
import sys
import types
import zipfile

source = os.path.join(sys.argv[1], 'src', 'res', 'scripts', 'client',
                      'gui', 'mods', 'offline_lan_0922', 'account_rpc')
for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
    module = types.ModuleType(name)
    module.__path__ = [os.path.dirname(source)]
    sys.modules[name] = module
data = imp.load_source('validator_data', os.path.join(source, 'data.py'))
economy = imp.load_source('validator_economy', os.path.join(source, 'economy.py'))

def node(code, path):
    for name in path.split('.'):
        code = next(child for child in code.co_consts
                    if isinstance(child, types.CodeType) and child.co_name == name)
    return code

def native(code, path, values=None):
    namespace = {'__builtins__': __builtins__}
    namespace.update(values or {})
    return types.FunctionType(node(code, path), namespace)

class Box(object):
    def __init__(self, **values):
        self.__dict__.update(values)

package = os.path.join(os.environ['WOT_0922_CLIENT'], 'res', 'packages', 'scripts.pkg')
with zipfile.ZipFile(package) as archive:
    def load(path):
        return marshal.loads(archive.read(path)[8:])
    requester = load('scripts/client/gui/shared/utils/requesters/StatsRequester.pyc')
    plugins_code = load('scripts/client/gui/shared/gui_items/processors/plugins.pyc')
    recruit_code = load('scripts/client/gui/shared/gui_items/processors/tankman.pyc')

class Stats(object):
    def __init__(self, value):
        self.value = value
    def getCacheValue(self, key, default):
        return self.value.get(key, default)

for name in ('freeTankmenLeft', 'vehicleSellsLeft', 'freeVehiclesLeft'):
    setattr(Stats, name, property(native(requester, 'StatsRequester.' + name)))

def check(validator, snapshot):
    instance = Box(itemsCache=Box(items=Box(stats=Stats(snapshot))))
    return native(plugins_code, validator + '._validate', {
        'makeError': lambda reason: reason, 'makeSuccess': lambda: True})(instance)

free = data.stats({})['stats']
assert check('FreeTankmanValidator', dict(free, freeTMenLeft=0)) == 'free_tankmen_limit'
assert check('FreeTankmanValidator', free) is True

def free_vehicle_prompt(stats, paid=False, crew_type=0):
    instance = Box(
        vehicle=Box(buyPrices=Box(itemPrice=Box(isDefined=lambda: paid))),
        crewType=crew_type, itemsCache=Box(items=Box(stats=Stats(stats))))
    return native(plugins_code, 'VehicleFreeLimitConfirmator._activeHandler')(instance)

assert free_vehicle_prompt(dict(free, freeVehiclesLeft=0)) is True
assert free_vehicle_prompt(free) is False
assert free_vehicle_prompt(dict(free, freeVehiclesLeft=0), paid=True) is False
assert free_vehicle_prompt(dict(free, freeVehiclesLeft=0), crew_type=1) is False
sales = []
for count in (0, 1, 2):
    snapshot = ({'vehicles': [{'id': index + 1} for index in range(count)]}
                if count else {})
    published = data.stats(snapshot)['stats']
    sales.append((published['vehicleSellsLeft'], check('VehicleSellsLeftValidator', published)))
assert sales == [(0, 'vehicle_sell_limit'), (0, 'vehicle_sell_limit'), (1, True)]
assert check('VehicleSellsLeftValidator', dict(free, vehicleSellsLeft=0)) == 'vehicle_sell_limit'

class Plugins(object):
    def __getattr__(self, name):
        return lambda *args, **kwargs: (name, args, kwargs)

class Processor(object):
    def __init__(self, plugins=()):
        self.plugins = list(plugins)
    def addPlugins(self, plugins):
        self.plugins.extend(plugins)

vehicle = Box(crew=[(0, None)])
cache = Box(items=Box(
    getItemByCD=lambda unused: vehicle,
    shop=Box(tankmanCost=data._tankman_costs({
        'tankmanCosts': economy.CAREER_TANKMAN_COSTS}))))
wiring = {}
for name in ('TankmanRecruit', 'TankmanRecruitAndEquip'):
    namespace = {'__builtins__': __builtins__, 'plugins': Plugins(),
                 'makeIntCompactDescrByID': lambda *args: 1,
                 'Money': lambda **kwargs: kwargs,
                 'Currency': Box(CREDITS='credits', GOLD='gold'),
                 'MONEY_UNDEFINED': {}}
    cls = type(name, (Processor,), {'itemsCache': cache})
    namespace[name] = cls
    cls.__init__ = types.FunctionType(node(recruit_code, name + '.__init__'), namespace)
    setattr(cls, '_' + name + '__getRecruitPrice', types.FunctionType(
        node(recruit_code, name + '.__getRecruitPrice'), namespace))
    rows = []
    for index in (0, 1, 2):
        instance = (cls(0, 1, 'commander', index) if name == 'TankmanRecruit'
                    else cls(vehicle, 0, index))
        entries = dict((kind, (args, kwargs)) for kind, args, kwargs in instance.plugins)
        rows.append((entries['FreeTankmanValidator'][1]['isEnabled'],
                     entries['MoneyValidator'][0][0]))
        assert 'BarracksSlotsValidator' in entries
    assert rows == [(True, {}), (False, {'credits': 20000}), (False, {'gold': 200})]
    wiring[name] = rows
print(json.dumps({'freeRecruitment': True, 'freeVehiclePurchase': True,
                  'saleValidation': sales, 'recruitmentWiring': wiring}))
'''


class TransactionValidatorPayloadTests(unittest.TestCase):
    def test_free_recruitment_is_enabled_without_changing_paid_training(self):
        data = fixture._load('data')
        economy = fixture._load('economy')
        snapshot = fixture.GaragePersistenceTests._matching_snapshot()
        snapshot['tankmanCosts'] = economy.CAREER_TANKMAN_COSTS
        self.assertEqual(1, data.stats(snapshot)['stats']['freeTMenLeft'])
        self.assertEqual(1, data.stats(snapshot)['stats']['freeVehiclesLeft'])
        costs = data.shop(selected_vehicle=snapshot)['tankmanCost']
        self.assertEqual(0, costs[0]['credits'] + costs[0]['gold'])
        self.assertEqual(20000, costs[1]['credits'])
        self.assertEqual(200, costs[2]['gold'])

    @unittest.skipUnless(os.environ.get('WOT_0922_CLIENT'),
                         'requires the exact #1513 client archive')
    def test_exact_native_getters_validators_and_recruitment_wiring(self):
        interpreter = os.environ.get('WOT_PYTHON27')
        if not interpreter:
            environment = dict(os.environ, PYENV_VERSION='2.7.18')
            interpreter = subprocess.check_output(
                ['pyenv', 'which', 'python2.7'], env=environment,
                text=True).strip()
        root = str(Path(__file__).resolve().parents[1])
        output = subprocess.check_output(
            [interpreter, '-c', NATIVE_PROBE, root],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), text=True)
        result = json.loads(output)
        self.assertTrue(result['freeRecruitment'])
        self.assertTrue(result['freeVehiclePurchase'])
        self.assertEqual([1, True], result['saleValidation'][2])


if __name__ == '__main__':
    unittest.main()
