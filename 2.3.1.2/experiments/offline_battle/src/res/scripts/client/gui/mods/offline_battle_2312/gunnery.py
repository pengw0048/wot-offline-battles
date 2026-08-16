"""Own the ammo and the shot, which the cell normally owns.

The client refuses to shoot until the ammo controller holds a loaded
shell, and it only plays the shot effect when the server answers
`vehicle_shoot`. Both arrive through ordinary client methods, so this
module calls them with values read from the vehicle descriptor.
"""
from __future__ import absolute_import

ALL_GUNS = -1
CURRENT_SHELLS = 0


def shell_int_cd(make_int_compact_descr, shot):
    nation_id, item_id = shot.shell.id
    return make_int_compact_descr('shell', nation_id, item_id)


def ammo_layout(gun, make_int_compact_descr):
    """(intCD, quantity, quantity in clip) per shot, sharing gun.maxAmmo."""
    shots = list(gun.shots)
    if not shots:
        return []
    in_clip = max(1, int(gun.clip[0]))
    total = int(gun.maxAmmo)
    share = total // len(shots)
    layout = []
    for index, shot in enumerate(shots):
        quantity = share + (total - share * len(shots) if index == 0 else 0)
        layout.append((shell_int_cd(make_int_compact_descr, shot), quantity,
                       min(in_clip, quantity)))
    return layout


class Gunnery(object):

    def __init__(self, vehicle, scheduler, log):
        self._vehicle_id = vehicle.id
        self._descriptor = vehicle.typeDescriptor
        self._schedule = scheduler
        self._log = log
        self._ammo = {}
        self._kinds = {}
        self._reload_time = float(self._descriptor.gun.reloadTime)
        self._shots_fired = 0

    @property
    def shots_fired(self):
        return self._shots_fired

    def _avatar(self):
        import BigWorld
        return BigWorld.player()

    def publish(self):
        """Load the gun the way the cell does when the vehicle appears."""
        from items import vehicles
        avatar = self._avatar()
        avatar.beforeSetupUpdate(self._vehicle_id)
        gun = self._descriptor.gun
        layout = ammo_layout(gun, vehicles.makeIntCompactDescrByID)
        for index, entry in enumerate(layout):
            int_cd, quantity, in_clip = entry
            self._ammo[int_cd] = [quantity, in_clip]
            self._kinds[int_cd] = int(gun.shots[index].shell.kindIdx)
            avatar.updateVehicleAmmo(self._vehicle_id, int_cd, quantity,
                                     in_clip, 0, 0, 0)
        if layout:
            avatar.updateVehicleSetting(self._vehicle_id, CURRENT_SHELLS,
                                        layout[0][0])
        avatar.updateVehicleGunReloadTime(self._vehicle_id, 0.0,
                                          self._reload_time)
        self._log('ammo_published shells=%s reload=%.2f current=%s'
                  % (len(layout), self._reload_time,
                     layout[0][0] if layout else None))

    def request_shot(self):
        self._schedule(0.0, self._fire)

    def change_setting(self, code, value):
        avatar = self._avatar()
        if avatar is not None:
            avatar.updateVehicleSetting(self._vehicle_id, int(code), value)

    def _fire(self):
        import BigWorld
        avatar = self._avatar()
        vehicle = BigWorld.entities.get(self._vehicle_id)
        if avatar is None or vehicle is None or not vehicle.isStarted:
            return
        int_cd = avatar.guiSessionProvider.shared.ammo.getCurrentShellCD()
        state = self._ammo.get(int_cd)
        if state is None or state[0] <= 0:
            return
        state[0] -= 1
        burst = max(1, int(self._descriptor.gun.burst[0]))
        vehicle.showShooting(burst, ALL_GUNS, self._kinds.get(int_cd, 0))
        avatar.updateVehicleAmmo(self._vehicle_id, int_cd, state[0], state[1],
                                 0, 0, 0)
        avatar.updateVehicleGunReloadTime(self._vehicle_id, self._reload_time,
                                          self._reload_time)
        self._schedule(self._reload_time, self._reloaded)
        self._shots_fired += 1
        if self._shots_fired == 1:
            self._log('shot_fired shell=%s left=%s burst=%s'
                      % (int_cd, state[0], burst))

    def _reloaded(self):
        avatar = self._avatar()
        if avatar is not None:
            avatar.updateVehicleGunReloadTime(self._vehicle_id, 0.0,
                                              self._reload_time)
