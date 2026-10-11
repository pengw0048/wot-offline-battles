"""Keep the mounted commander's gender through native voice mode changes."""


def commander_group(crew):
    """Freeze TankmanDescr.group while the garage Account still exists."""
    for entry in crew or ():
        member = entry[1] if isinstance(entry, tuple) and len(entry) == 2 else entry
        descriptor = getattr(member, 'descriptor', None)
        if getattr(descriptor, 'role', None) == 'commander':
            return int(descriptor.group)
    return 0


def install(patch):
    import BigWorld
    import SoundGroups
    from account_helpers.settings_core.options import AltVoicesSetting
    original = SoundGroups.SoundModes.setCurrentNation

    def set_current_nation(modes, nation,
                           genderSwitch=SoundGroups.CREW_GENDER_SWITCHES.DEFAULT):
        player = BigWorld.player()
        # The stock Vehicle, settings and postmortem controller all call this
        # final owner. Correcting only refreshNationalVoice would be undone
        # when the user applies Standard or changes the controlled vehicle.
        # Preserve the native language mapping and special setMode voices.
        if getattr(player, 'fakeServer', None) is not None:
            attached = getattr(player, 'getVehicleAttached', None)
            vehicle = attached() if callable(attached) else None
            arena = getattr(player, 'arena', None)
            row = getattr(arena, 'vehicles', {}).get(getattr(vehicle, 'id', None))
            if row is None and arena is None:
                # The audio settings preview can also run in the garage.
                from CurrentVehicle import g_currentVehicle
                item = g_currentVehicle.item if g_currentVehicle.isPresent() else None
                if item is not None:
                    row = {'crewGroup': commander_group(getattr(item, 'crew', ()))}
            if row is not None:
                female = bool(int(row.get('crewGroup', 0)) & 1)
                switches = SoundGroups.CREW_GENDER_SWITCHES
                genderSwitch = switches.FEMALE if female else switches.MALE
        return original(modes, nation, genderSwitch)

    patch(SoundGroups.SoundModes, 'setCurrentNation', set_current_nation)

    def refresh_attached_voice():
        player = BigWorld.player()
        # Client-only Avatars have no server-created Entity.vehicle link.
        # AltVoicesSetting.clearPreviewSound therefore skips its native
        # refresh even though getVehicleAttached resolves the current tank.
        if (getattr(player, 'fakeServer', None) is None or
                getattr(player, 'arena', None) is None or
                getattr(player, 'vehicle', None) is not None):
            return
        attached = getattr(player, 'getVehicleAttached', None)
        vehicle = attached() if callable(attached) else None
        if (vehicle is not None and getattr(vehicle, 'inWorld', False) and
                getattr(vehicle, 'isStarted', False)):
            # This owns native nation mapping, commander gender and special
            # crew modes. Refresh only after the setting commits its mapping.
            vehicle.refreshNationalVoice()

    original_set = AltVoicesSetting.setSystemValue
    original_clear = AltVoicesSetting.clearPreviewSound

    def set_system_value(setting, value):
        result = original_set(setting, value)
        if result:
            refresh_attached_voice()
        return result

    def clear_preview_sound(setting):
        result = original_clear(setting)
        refresh_attached_voice()
        return result

    patch(AltVoicesSetting, 'setSystemValue', set_system_value)
    patch(AltVoicesSetting, 'clearPreviewSound', clear_preview_sound)
