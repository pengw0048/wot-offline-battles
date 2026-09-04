"""Shared #1513 vehicle-module fitting and battle-eligibility rules."""


NON_STANDARD_BATTLE_TAGS = frozenset((
    'event_battles', 'premiumIGR', 'observer', 'unrecoverable'))
NON_STANDARD_BATTLE_NAMES = frozenset(('usa:T23',))


def is_standard_battle_vehicle(vehicle_type):
    """Return whether #1513 exposes this type to ordinary tank battles.

    The stock catalogue also contains observer, event, IGR and unrecoverable
    environment helper entities.  Some of them can be constructed in the
    garage but omit resources required by ``Vehicle.prerequisites``;
    ``Env_Artillery`` is one example whose shell has no renderable projectile.
    ``secret`` alone is only a catalogue-visibility tag and remains playable.
    """
    name = str(getattr(vehicle_type, 'name', '') or '')
    tags = set(getattr(vehicle_type, 'tags', ()) or ())
    return bool(
        name and name not in NON_STANDARD_BATTLE_NAMES and
        not NON_STANDARD_BATTLE_TAGS.intersection(tags))


def _sort_text(value):
    try:
        text_type = unicode
    except NameError:
        text_type = str
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return text_type(value or '')


def _price_info(component):
    value = getattr(component, 'price', None)
    if value is None:
        try:
            from items import vehicles
            prices = getattr(vehicles, '_g_prices', None)
            item_prices = None if prices is None else prices.get(
                'itemPrices')
            if item_prices is not None:
                getter = getattr(item_prices, 'tryGetPrice', None)
                if getter is not None:
                    value = getter(component.compactDescr, {})
                else:
                    value = item_prices.getPrices(component.compactDescr)
        except (AttributeError, KeyError, TypeError, ImportError):
            value = None
    if isinstance(value, dict):
        return (value.get('gold', 0), value.get('crystal', 0),
                value.get('credits', 0))
    if isinstance(value, (tuple, list)):
        values = list(value) + [0, 0, 0]
        return (values[1], values[2], values[0])
    if value is None:
        return (0, 0, 0)
    return (0, 0, value)


def _gui_component_key(component, index):
    """Reproduce the same-type portion of #1513 FittingItem.__cmp__."""
    return (int(getattr(component, 'level', 1)),
            _price_info(component),
            _sort_text(getattr(component, 'userString', None) or
                       getattr(component, 'name', '')),
            index)


def _research_costs(vehicle_type):
    """Return #1513 TechTreeDataProvider's per-vehicle unlock prices."""
    costs = {}
    for unlock in getattr(vehicle_type, 'unlocksDescrs', ()) or ():
        if len(unlock) >= 2:
            # g_techTreeDP.load uses assignment, so a later duplicate wins.
            costs[unlock[1]] = unlock[0]
    return costs


def _gun_shots_per_minute(gun):
    clip = getattr(gun, 'clip', (1, 0.0)) or (1, 0.0)
    burst = getattr(gun, 'burst', (1, 0.0)) or (1, 0.0)
    clip_size = int(clip[0])
    burst_size = int(burst[0])
    if clip_size <= 0 or burst_size <= 0:
        return 0.0
    clip_count = clip_size // burst_size if clip_size > 1 else 1
    cycle_time = (
        float(getattr(gun, 'reloadTime', 0.0)) +
        float(burst_size - 1) * float(burst[1]) * clip_count +
        float(clip_count - 1) * float(clip[1]))
    if cycle_time <= 0.0:
        return 0.0
    return float(burst_size * clip_count * 60) / cycle_time


def _gun_damage_per_minute(gun, vehicle_descriptor):
    shots = getattr(gun, 'shots', ()) or ()
    if not shots:
        return 0.0
    gun_id = getattr(gun, 'id', (None, None))[1]
    variants = []
    if vehicle_descriptor is not None:
        current_turret = getattr(vehicle_descriptor, 'turret', None)
        for candidate in getattr(current_turret, 'guns', ()) or ():
            if getattr(candidate, 'id', (None, None))[1] == gun_id:
                variants.append(candidate)
        if not variants:
            vehicle_type = getattr(vehicle_descriptor, 'type', None)
            for turrets in getattr(vehicle_type, 'turrets', ()) or ():
                for turret in turrets:
                    for candidate in getattr(turret, 'guns', ()) or ():
                        if getattr(candidate, 'id', (None, None))[1] == gun_id:
                            variants.append(candidate)
    if not variants:
        variants = [gun]
    shell = getattr(shots[0], 'shell', None)
    damage = getattr(shell, 'damage', (0.0,)) if shell is not None else (0.0,)
    shots_per_minute = min(_gun_shots_per_minute(value)
                           for value in variants)
    return round(shots_per_minute * float(damage[0]))


def _valuable_parameter(component, component_kind, vehicle_descriptor):
    if component_kind == 'chassis':
        return getattr(component, 'maxLoad', 0)
    if component_kind == 'turret':
        return getattr(component, 'primaryArmor', ())
    if component_kind == 'gun':
        return _gun_damage_per_minute(component, vehicle_descriptor)
    if component_kind == 'engine':
        return getattr(component, 'power', 0)
    if component_kind == 'radio':
        return getattr(component, 'distance', 0)
    return None


def _installation_accepted(result):
    """Interpret #1513's ``mayInstall*`` result without tuple truthiness."""
    if isinstance(result, (tuple, list)):
        return bool(result and result[0])
    return bool(result)


def _first_installable(indexed, selector, may_install):
    """Repeat one TopModulesChecker comparator after rejecting a module."""
    remaining = list(indexed)
    while remaining:
        selected = selector(remaining)
        if selected is None:
            return None
        if may_install is None or _installation_accepted(
                may_install(selected[1])):
            return selected[1]
        remaining.remove(selected)
    return None


def top_component(components, vehicle_type=None, component_kind=None,
                  vehicle_descriptor=None, may_install=None):
    """Return the module selected by #1513's TopModulesChecker rules.

    A unique highest level wins immediately.  When the highest level is tied,
    the stock checker compares the research prices of every candidate, not
    only the tied candidates.  If this vehicle has no research price for any
    candidate, the module-specific valuable parameter is used instead.
    """
    candidates = list(components or ())
    if not candidates:
        return None
    indexed = list(enumerate(candidates))

    # Fuel tanks and compatibility callers without the retail comparison
    # context retain the old deterministic rule.
    if vehicle_type is None or component_kind is None:
        return _first_installable(
            indexed,
            lambda values: max(values, key=lambda value: (
                int(getattr(value[1], 'level', 1)), value[0])),
            may_install)

    def by_unique_level(values):
        ordered = sorted(
            values, key=lambda value: int(getattr(value[1], 'level', 1)))
        if (len(ordered) > 1 and
                int(getattr(ordered[-1][1], 'level', 1)) ==
                int(getattr(ordered[-2][1], 'level', 1))):
            return None
        return ordered[-1]

    selected = _first_installable(indexed, by_unique_level, may_install)
    if selected is not None:
        return selected

    costs = _research_costs(vehicle_type)

    def by_research_cost(values):
        researched = []
        for index, component in values:
            compact_descr = getattr(component, 'compactDescr', None)
            if compact_descr in costs:
                researched.append((costs[compact_descr],
                                   _gui_component_key(component, index),
                                   (index, component)))
        if not researched:
            return None
        return max(researched, key=lambda value: value[:2])[2]

    selected = _first_installable(indexed, by_research_cost, may_install)
    if selected is not None:
        return selected

    def by_valuable_parameter(values):
        valuable = [(_valuable_parameter(
                         component, component_kind, vehicle_descriptor),
                     _gui_component_key(component, index), (index, component))
                    for index, component in values]
        if not valuable:
            return None
        return max(valuable, key=lambda value: value[:2])[2]

    return _first_installable(indexed, by_valuable_parameter, may_install)


def _component_may_install(descriptor, component, position=0):
    checker = getattr(descriptor, 'mayInstallComponent', None)
    if checker is None:
        return True
    return checker(component.compactDescr, position)


def _turret_may_install(descriptor, turret, gun, position=0):
    checker = getattr(descriptor, 'mayInstallTurret', None)
    if checker is None:
        return True
    return checker(turret.compactDescr, gun.compactDescr, position)


def _mounted_component(descriptor, attribute, position=0):
    if attribute == 'turret':
        turrets = getattr(descriptor, 'turrets', None)
        if turrets is not None:
            return turrets[position][0]
    if attribute == 'gun':
        turrets = getattr(descriptor, 'turrets', None)
        if turrets is not None:
            return turrets[position][1]
    return getattr(descriptor, attribute, None)


def _require_installed(unused_result, descriptor, expected, attribute,
                       position=0):
    """Require the descriptor postcondition; #1513 return values vary."""
    mounted = _mounted_component(descriptor, attribute, position)
    if (mounted is None or
            getattr(mounted, 'compactDescr', None) != expected.compactDescr):
        raise ValueError('failed to install top %s' % attribute)


def install_top_modules(descriptor):
    """Fit the top module of every slot in the order #1513 accepts."""
    vehicle_type = descriptor.type
    chassis_candidates = tuple(getattr(vehicle_type, 'chassis', ()) or ())
    chassis = top_component(
        chassis_candidates, vehicle_type, 'chassis',
        descriptor, lambda component: _component_may_install(
            descriptor, component))
    if chassis is None and chassis_candidates:
        raise ValueError('no installable top chassis')
    if chassis is not None:
        result = descriptor.installComponent(chassis.compactDescr, 0)
        _require_installed(result, descriptor, chassis, 'chassis')

    # #1513 rejects turrets through installComponent.  installTurret takes
    # the compatible turret/gun pair and resolves the final hull correctly.
    for position, turrets in enumerate(
        getattr(vehicle_type, 'turrets', ()) or ()):
        remaining_turrets = list(turrets)
        installed = False
        while remaining_turrets:
            turret = top_component(
                remaining_turrets, vehicle_type, 'turret', descriptor)
            if turret is None:
                break
            gun = top_component(
                getattr(turret, 'guns', ()), vehicle_type, 'gun', descriptor,
                lambda component: _turret_may_install(
                    descriptor, turret, component, position))
            if gun is not None:
                result = descriptor.installTurret(
                    turret.compactDescr, gun.compactDescr, position)
                _require_installed(
                    result, descriptor, turret, 'turret', position)
                _require_installed(result, descriptor, gun, 'gun', position)
                installed = True
                break
            remaining_turrets.remove(turret)
        if turrets and not installed:
            raise ValueError(
                'no installable top turret/gun at position %d' % position)

    for attribute in ('engines', 'radios', 'fuelTanks'):
        candidates = tuple(getattr(vehicle_type, attribute, ()) or ())
        component_kind = {'engines': 'engine', 'radios': 'radio'}.get(
            attribute)
        mounted_attribute = {
            'engines': 'engine', 'radios': 'radio',
            'fuelTanks': 'fuelTank'}[attribute]
        if component_kind is None:
            component = top_component(
                candidates, may_install=(
                    lambda value: _component_may_install(
                        descriptor, value)))
        else:
            component = top_component(
                candidates, vehicle_type, component_kind, descriptor,
                lambda value: (
                    _component_may_install(descriptor, value)))
        if component is None and candidates:
            raise ValueError('no installable top %s' % mounted_attribute)
        if component is not None:
            result = descriptor.installComponent(component.compactDescr, 0)
            _require_installed(
                result, descriptor, component, mounted_attribute)
    return descriptor
