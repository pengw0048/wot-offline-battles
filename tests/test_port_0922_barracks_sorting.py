"""Run on Python 3 and real CPython 2.7: price longs must not escape cmp."""

from __future__ import print_function

import ast
import functools
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = os.path.join(ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
                    'offline_lan_0922')
try:
    Long = long
except NameError:
    class Long(int):
        def __sub__(self, other):
            return Long(int(self) - int(other))


def load_functions(relative, names, namespace):
    """Execute the production adapters without importing BigWorld/Scaleform."""
    with open(os.path.join(PORT, relative), 'rb') as source:
        tree = ast.parse(source.read())
    tree.body = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(tree.body) != len(names):
        raise AssertionError('missing production function in %s' % relative)
    eval(compile(tree, relative, 'exec'), namespace)


def compare(first, second):
    return (first > second) - (first < second)


def native_sorted(values, comparator):
    if sys.version_info[0] == 2:
        return sorted(values, cmp=comparator)

    def strict_compare(first, second):
        result = comparator(first, second)
        if type(result) is not int:
            raise TypeError('comparison function must return int, not long')
        return result
    return sorted(values, key=functools.cmp_to_key(strict_compare))


class BarracksSortingTests(unittest.TestCase):
    def setUp(self):
        self.patches = []

        def patch(owner, name, replacement):
            self.patches.append((owner, name, owner.__dict__[name]))
            setattr(owner, name, replacement)

        class FittingItem(object):
            def __init__(self, nation, kind, level, price, name):
                self.nation = nation
                self.kind = kind
                self.level = level
                self.price = price
                self.name = name

            def __cmp__(self, other):
                if other is None:
                    return 1
                for name in ('nation', 'kind', 'level'):
                    result = compare(getattr(self, name), getattr(other, name))
                    if result:
                        return result
                for currency in ('gold', 'crystal', 'credits'):
                    result = (self.price.get(currency, 0) -
                              other.price.get(currency, 0))
                    if result:
                        return result
                return compare(self.name, other.name)

        self.item_type = FittingItem
        self.original = FittingItem.__dict__['__cmp__']
        self.namespace = {'_patch': patch}
        price_namespace = {'_NATIVE_LONG': Long}
        load_functions(os.path.join('account_rpc', 'requests.py'),
                       ('_native_price_value',), price_namespace)
        self.price = price_namespace['_native_price_value']

    def tearDown(self):
        for owner, name, original in reversed(self.patches):
            setattr(owner, name, original)

    def install(self):
        load_functions('offline_services_ui.py',
                       ('_install_item_comparisons',), self.namespace)
        self.namespace['_install_item_comparisons'](self.item_type)

    def item(self, amount, currency='credits', nation=0, kind=0, level=8,
             name='vehicle'):
        return self.item_type(nation, kind, level,
                              self.price({currency: amount}), name)

    def test_native_price_producer_reproduces_report_before_fix(self):
        rows = [self.item(100), self.item(200)]
        self.assertIs(type(rows[0].price['credits']), Long)
        self.assertIs(type(self.original(*rows)), Long)
        with self.assertRaises(TypeError):
            native_sorted(rows, lambda a, b: a.__cmp__(b))

    def test_all_price_currencies_sort_without_mutating_native_longs(self):
        self.install()
        for currency in ('gold', 'crystal', 'credits'):
            rows = [self.item(n, currency) for n in (400, 0, 200, 100)]
            prices = [dict(row.price) for row in rows]
            ordered = native_sorted(rows, lambda a, b: a.__cmp__(b))
            self.assertEqual([0, 100, 200, 400],
                             [row.price[currency] for row in ordered])
            self.assertEqual(prices, [row.price for row in rows])
            self.assertTrue(all(type(row.price[currency]) is Long for row in rows))

    def test_sign_is_preserved_even_beyond_32_and_64_bit_bounds(self):
        self.install()
        for amount in (0, 1, 2147483648, 2 ** 80):
            first, second = self.item(amount), self.item(0)
            for left, right in ((first, second), (second, first)):
                result = left.__cmp__(right)
                self.assertIs(type(result), int)
                self.assertEqual(compare(self.original(left, right), 0), result)

    def test_original_precedence_none_and_equal_results_are_preserved(self):
        self.install()
        rows = [self.item(200), self.item(100, nation=1),
                self.item(100, kind=1), self.item(100, level=9),
                self.item(200, name='alpha'), self.item(200)]
        for first in rows:
            for second in rows + [None]:
                self.assertEqual(compare(self.original(first, second), 0),
                                 first.__cmp__(second))

    def test_barracks_comparator_can_forward_vehicle_comparison(self):
        self.install()
        vehicles = [self.item(n) for n in (400, 100, 200)]

        class Tankman(object):
            def __init__(self, identifier):
                self.identifier = identifier
                self.vehicle = vehicles[identifier % len(vehicles)]

        def crew_compare(first, second):
            return (first.vehicle.__cmp__(second.vehicle) or
                    compare(first.identifier, second.identifier))

        for count in (0, 1, 7, 229, 3000):
            rows = [Tankman(n) for n in reversed(range(count))]
            ordered = native_sorted(rows, crew_compare)
            expected = sorted(rows, key=lambda row: (
                row.vehicle.price['credits'], row.identifier))
            self.assertEqual(expected, ordered)
            self.assertEqual(list(reversed(range(count))),
                             [row.identifier for row in rows])

    def test_existing_vehicle_subclass_and_direct_fitting_sort_both_use_fix(self):
        class Vehicle(self.item_type):
            def __cmp__(self, other):
                return super(Vehicle, self).__cmp__(other)

        first = Vehicle(0, 0, 8, self.price({'gold': 100}), 'first')
        second = Vehicle(0, 0, 8, self.price({'gold': 50}), 'second')
        self.install()
        self.assertEqual([second, first], native_sorted(
            [first, second], lambda a, b: a.__cmp__(b)))
        self.assertIs(type(first.__cmp__(second)), int)

    def test_stock_failure_is_not_hidden_and_patch_can_be_restored(self):
        self.install()
        with self.assertRaises(AttributeError):
            self.item(100).__cmp__(object())
        self.tearDown()
        self.patches[:] = []
        self.assertIs(self.item_type.__dict__['__cmp__'], self.original)
        with self.assertRaises(TypeError):
            native_sorted([self.item(100), self.item(200)],
                          lambda a, b: a.__cmp__(b))


if __name__ == '__main__':
    unittest.main()
