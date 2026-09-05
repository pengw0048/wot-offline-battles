"""Offline account economy: prices, balances, ownership and research.

One save either plays the historical sandbox or a career that starts from a
fresh account.  Both run the same code: a sandbox is a career seeded with every
vehicle owned, every item researched and a balance nobody exhausts.  Keeping
one path means a purchase, a sale and a research step are exercised by every
save rather than only by the one that can afford to be wrong.

Prices come from ``price_catalogue``, baked from the pinned client's own item
definitions, because #1513 parses them and then resets ``_g_prices`` to None.
The research tree is not baked: ``VehicleType.unlocksDescrs`` holds
``(xpCost, compactDescr, *requiredCompactDescrs)`` at runtime, and this module
reads it from the live cache so a tree change in the installed client cannot
disagree with what the garage offers.
"""

from gui.mods.offline_lan_0922 import price_catalogue


CREDITS = 'credits'
GOLD = 'gold'
FREE_XP = 'freeXP'
CURRENCIES = (CREDITS, GOLD)

# items/__init__ ITEM_TYPE_NAMES indices used when a compact descriptor has to
# be turned back into the catalogue key that names it.
ITEM_TYPE_NAMES_BY_INDEX = {
    1: 'vehicle',
    2: 'vehicleChassis',
    3: 'vehicleTurret',
    4: 'vehicleGun',
    5: 'vehicleEngine',
    6: 'vehicleFuelTank',
    7: 'vehicleRadio',
    9: 'optionalDevice',
    10: 'shell',
    11: 'equipment',
}
VEHICLE_ITEM_TYPE = 1
OPTIONAL_DEVICE_ITEM_TYPE = 9
SHELL_ITEM_TYPE = 10
EQUIPMENT_ITEM_TYPE = 11
MODULE_ITEM_TYPES = (2, 3, 4, 5, 6, 7)


# Offline-only policy, not #1513 values.  A retail account's starting balance,
# garage slots and barracks berths are server state the client never sees, and
# ``bootcamp_docs/garage_defaults.xml`` carries only panel visibility.  These
# are the explicit product choices for a career save; the launcher can change
# the balances afterwards.
CAREER_CREDITS = 100000
CAREER_GOLD = 0
CAREER_FREE_XP = 0
CAREER_GARAGE_SLOTS = 30
CAREER_BARRACKS_BERTHS = 30

# The historical offline garage: enough of everything that no operation is
# ever refused for lack of funds.
SANDBOX_CREDITS = 100000000
SANDBOX_GOLD = 1000000
SANDBOX_FREE_XP = 100000000
SANDBOX_GARAGE_SLOTS = 2000
SANDBOX_BARRACKS_BERTHS = 2000

CAREER_WALLET = {
    CREDITS: CAREER_CREDITS, GOLD: CAREER_GOLD, FREE_XP: CAREER_FREE_XP}
SANDBOX_WALLET = {
    CREDITS: SANDBOX_CREDITS, GOLD: SANDBOX_GOLD, FREE_XP: SANDBOX_FREE_XP}

# The three recruitment schools #1513 offers, in the order its shop data lists
# them: ``Shop.buyTankman`` sends the player's choice as an index into this
# tuple, and ``ShopCommonStats.tankmanCost`` is what the recruit window reads
# to price them.  The prices are server state the client never receives, so
# free, 20000 credits and 200 gold are the long-published retail numbers,
# chosen the same way the gold exchange rate and the garage slot price were.
# ``baseRoleLoss`` and ``classChangeRoleLoss`` stay at zero because retraining
# does not exist offline yet, and the recruit window reads both.
def _tankman_cost(credits_amount, gold, role_level, premium=False):
    return {
        'credits': credits_amount, 'gold': gold, 'roleLevel': role_level,
        'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0,
        'isPremium': premium,
    }


# ``ShopCommonStats.paidRemovalCost`` falls back to 10 gold when the shop
# publishes none, and ``paidDeluxeRemovalCost`` to 100 crystal.  Those are the
# client's own numbers for taking a complex optional device off a vehicle, so
# a career charges them and the historical sandbox charges nothing.
CAREER_DEVICE_REMOVAL = {'gold': 10}
SANDBOX_DEVICE_REMOVAL = {'gold': 0}

CAREER_TANKMAN_COSTS = (
    _tankman_cost(0, 0, 50),
    _tankman_cost(20000, 0, 75),
    _tankman_cost(0, 200, 100, premium=True),
)
SANDBOX_TANKMAN_COSTS = (
    _tankman_cost(0, 0, 50),
    _tankman_cost(0, 0, 75),
    _tankman_cost(0, 0, 100, premium=True),
)


class EconomyError(Exception):
    pass


class InsufficientFunds(EconomyError):
    """The account cannot pay for an operation it otherwise could perform."""


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def empty_wallet():
    return {CREDITS: 0, GOLD: 0, FREE_XP: 0}


def normalized_wallet(value):
    """Return a wallet with exactly the three balances, never negative."""
    value = value if isinstance(value, dict) else {}
    wallet = empty_wallet()
    for name in wallet:
        wallet[name] = max(0, _int(value.get(name)))
    return wallet


def price_index(vehicles_module, nations_module):
    """Return ``{compactDescr: (credits, gold, not_in_shop)}``.

    The installed client is the authority on which items exist and what their
    compact descriptors are; the baked catalogue only supplies the amount.  An
    item this client has but the catalogue does not is published at no price
    rather than made unbuyable, because a missing price is a baking gap and
    must not silently remove a vehicle from the garage.
    """
    index = {}
    make = vehicles_module.makeIntCompactDescrByID
    cache = vehicles_module.g_cache
    vehicle_list = vehicles_module.g_list

    for nation_id, nation in enumerate(nations_module.NAMES):
        # ``getIDsByName`` is how #1513 itself resolves a research target, so
        # ask it rather than reading a name attribute off the list item. A
        # catalogue name this client does not have simply does not resolve.
        priced_ids = {}
        for name in price_catalogue.vehicle_names(nation):
            try:
                resolved = vehicle_list.getIDsByName('%s:%s' % (nation, name))
            except Exception:
                continue
            if resolved and int(resolved[0]) == nation_id:
                priced_ids[int(resolved[1])] = name
        for vehicle_type_id in vehicles_module.g_list.getList(nation_id):
            name = priced_ids.get(int(vehicle_type_id))
            price = (price_catalogue.vehicle_price(nation, name)
                     if name else None)
            index[make('vehicle', nation_id, vehicle_type_id)] = (
                price or (0, 0, False))

        for item_type_name, accessor in (
                ('vehicleChassis', cache.chassisIDs),
                ('vehicleTurret', cache.turretIDs),
                ('vehicleGun', cache.gunIDs),
                ('vehicleEngine', cache.engineIDs),
                ('vehicleFuelTank', cache.fuelTankIDs),
                ('vehicleRadio', cache.radioIDs)):
            for name, item_id in accessor(nation_id).items():
                index[make(item_type_name, nation_id, item_id)] = (
                    price_catalogue.component_price(
                        nation, item_type_name, name) or (0, 0, False))

        for name, item_id in cache.shellIDs(nation_id).items():
            index[make('shell', nation_id, item_id)] = (
                price_catalogue.shell_price(nation, name) or (0, 0, False))

    for accessor in (cache.optionalDevices, cache.equipments):
        for descriptor in accessor().values():
            compact_descr = _int(getattr(descriptor, 'compactDescr', 0))
            name = getattr(descriptor, 'name', None)
            if compact_descr and name:
                index[compact_descr] = (
                    price_catalogue.artefact_price(name) or (0, 0, False))
    return index


def shop_prices(index):
    """Return the #1513 shop mapping and the set of items it never offered."""
    prices = {}
    not_in_shop = set()
    for compact_descr, price in index.items():
        published = price_catalogue.money(price)
        if published is not None:
            prices[compact_descr] = published
        if price[price_catalogue.NOT_IN_SHOP]:
            not_in_shop.add(compact_descr)
    return prices, not_in_shop


def cost(index, compact_descr, count=1):
    """Return what ``count`` of one item costs, as a currency mapping."""
    price = index.get(_int(compact_descr))
    if price is None:
        return {CREDITS: 0}
    count = max(1, _int(count, 1))
    if price[price_catalogue.GOLD]:
        return {GOLD: price[price_catalogue.GOLD] * count}
    return {CREDITS: price[price_catalogue.CREDITS] * count}


def refund(index, compact_descr, count=1):
    """Return what selling ``count`` of one item pays back.

    #1513 sets ``SELL_PRICE_FACTOR`` to 0.5 for clients.  A gold item refunds
    credits at that fraction of its gold price the way retail does not; there
    is no offline gold sink to return to, so gold purchases are refunded in
    gold to keep the ledger reversible.
    """
    price = index.get(_int(compact_descr))
    if price is None:
        return {CREDITS: 0}
    count = max(1, _int(count, 1))
    factor = price_catalogue.SELL_PRICE_FACTOR
    if price[price_catalogue.GOLD]:
        return {GOLD: int(price[price_catalogue.GOLD] * count * factor)}
    return {CREDITS: int(price[price_catalogue.CREDITS] * count * factor)}


def can_afford(wallet, amount):
    wallet = normalized_wallet(wallet)
    for currency in CURRENCIES:
        if _int(amount.get(currency)) > wallet[currency]:
            return False
    return True


def debit(wallet, amount):
    """Subtract one currency mapping, or raise before changing anything."""
    if not can_afford(wallet, amount):
        raise InsufficientFunds(
            'the account cannot pay %s' % (dict(amount),))
    for currency in CURRENCIES:
        value = _int(amount.get(currency))
        if value:
            wallet[currency] = wallet[currency] - value
    return wallet


def credit(wallet, amount):
    for currency in CURRENCIES:
        value = _int(amount.get(currency))
        if value:
            wallet[currency] = max(0, wallet[currency] + value)
    return wallet


# ---- research -----------------------------------------------------------


def unlock_descriptors(vehicles_module, vehicle_type_compact_descr):
    """Return one vehicle type's live research list.

    Each entry is ``(xpCost, compactDescr, *requiredCompactDescrs)``: the
    exact shape ``VehicleType.__convertAndValidateUnlocksDescrs`` builds and
    ``Stats.unlock``'s ``unlockIdx`` indexes.
    """
    vehicle_type = vehicles_module.getVehicleType(
        _int(vehicle_type_compact_descr))
    return list(getattr(vehicle_type, 'unlocksDescrs', ()) or ())


def autounlocked_items(vehicles_module, vehicle_type_compact_descr):
    """Return the items a vehicle grants for free once it is owned."""
    vehicle_type = vehicles_module.getVehicleType(
        _int(vehicle_type_compact_descr))
    return [_int(value)
            for value in getattr(vehicle_type, 'autounlockedItems', ()) or ()]


def unlock_requirements(vehicles_module, vehicle_type_compact_descr,
                        unlock_index):
    """Return ``(compact_descr, xp_cost, required)`` for one research step."""
    descriptors = unlock_descriptors(
        vehicles_module, vehicle_type_compact_descr)
    index = _int(unlock_index, -1)
    if not 0 <= index < len(descriptors):
        raise EconomyError('unknown research step %r' % (unlock_index,))
    descriptor = tuple(descriptors[index])
    if len(descriptor) < 2:
        raise EconomyError('research step %d is malformed' % index)
    return (_int(descriptor[1]), max(0, _int(descriptor[0])),
            tuple(_int(value) for value in descriptor[2:]))


def is_elite(vehicles_module, vehicle_type_compact_descr, unlocks):
    """Return whether every item this vehicle researches is already unlocked."""
    descriptors = unlock_descriptors(
        vehicles_module, vehicle_type_compact_descr)
    for descriptor in descriptors:
        descriptor = tuple(descriptor)
        if len(descriptor) >= 2 and _int(descriptor[1]) not in unlocks:
            return False
    return True


def spend_research(wallet, vehicle_xp, vehicle_type_compact_descr, xp_cost):
    """Take a research cost from this vehicle's XP, topped up with free XP.

    #1513 spends the vehicle's own accumulated XP first and covers the
    remainder from the account's free XP, which is what the tech tree's
    "research for free experience" action does.
    """
    xp_cost = max(0, _int(xp_cost))
    key = _int(vehicle_type_compact_descr)
    available = max(0, _int(vehicle_xp.get(key)))
    from_vehicle = min(available, xp_cost)
    remainder = xp_cost - from_vehicle
    if remainder > wallet[FREE_XP]:
        raise InsufficientFunds(
            'research needs %d more experience' % (
                remainder - wallet[FREE_XP]))
    vehicle_xp[key] = available - from_vehicle
    wallet[FREE_XP] = wallet[FREE_XP] - remainder
    return {'vehicleXP': from_vehicle, 'freeXP': remainder}
