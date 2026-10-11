"""Freeze the regular mission carried by the local ClientArena roster.

The stock TAB description resolves ClientArena.personalMissionIDs through its
personal mission cache. A selected account mission alone does not populate
that roster field. Capture eligibility while the garage descriptor and save
still belong to the lobby; never reconstruct it from a retired Account.
"""

from gui.mods.offline_lan_0922.account_rpc import data

_VEHICLE_CLASSES = frozenset(('lightTank', 'mediumTank', 'heavyTank',
                              'AT-SPG', 'SPG'))


def selected_mission_ids(snapshot, descriptor, definition_provider=None):
    """Return selected, unlocked missions for this exact vehicle descriptor.

    Main-complete missions remain eligible for an honors attempt. Completed
    honors, another class/tier and missing operation prerequisites do not.
    Definitions come from the installed client's mission resources, shared
    with battle settlement, rather than an independent unlock table.
    """
    if not isinstance(snapshot, dict) or descriptor is None:
        return ()
    if definition_provider is None:
        from gui.mods.offline_lan_0922.personal_campaign import mission_definition
        definition_provider = mission_definition
    from gui.mods.offline_lan_0922.personal_campaign import value

    vehicle_type = descriptor.type
    vehicle_tags = set(vehicle_type.tags)
    vehicle_level = int(vehicle_type.level)
    selected = snapshot.get('personalMissionSelections') or {}
    completed = data.personal_mission_completed(
        snapshot.get('personalMissionProgress'))
    result = []
    for mission_id in data.personal_mission_regular_selection(
            selected.get('regular', ())):
        if completed.get(str(mission_id), 0) >= 2:
            continue
        definition = definition_provider(mission_id)
        metadata = definition.get('metadata') if definition else None
        if metadata is None:
            continue
        tags = set(value(metadata, 'tags').split()) & _VEHICLE_CLASSES
        minimum = int(value(metadata, 'minLevel'))
        maximum = int(value(metadata, 'maxLevel'))
        required = [int(qid) for qid in
                    value(metadata, 'requiredUnlocks').split()]
        if (tags & vehicle_tags and minimum <= vehicle_level <= maximum and
                all(completed.get(str(qid), 0) >= 1 for qid in required)):
            result.append(mission_id)
    return tuple(result)
