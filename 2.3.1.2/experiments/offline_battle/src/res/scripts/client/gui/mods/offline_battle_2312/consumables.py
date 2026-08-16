"""The three standard consumables, answered the way the cell answers.

The panel learns the loadout from updateVehicleAmmo, and using an item
sends cell.vehicle_changeSetting(ACTIVATE_EQUIPMENT, item id), which the
bridge routes here. The effects are the copied critical_damage laws.
"""
from __future__ import absolute_import

from gui.mods.offline_battle_2312 import critical_damage

ITEM_NAMES = ('smallRepairkit', 'smallMedkit', 'handExtinguishers')
KINDS = {'smallRepairkit': 'repair', 'smallMedkit': 'medkit',
         'handExtinguishers': 'extinguisher'}


class Consumables(object):

    def __init__(self, avatar, vehicle_id, log, critical):
        self._avatar = avatar
        self._vehicle_id = vehicle_id
        self._log = log
        self._critical = critical
        self._items = {}

    def publish(self):
        """One of each item, the loadout an account normally carries."""
        from items import vehicles
        for equipment in vehicles.g_cache.equipments().values():
            name = getattr(equipment, 'name', None)
            if name not in ITEM_NAMES:
                continue
            item_id = int(equipment.id[1])
            self._items[item_id] = {
                'kind': KINDS[name],
                'int_cd': int(equipment.compactDescr),
                'quantity': 1,
            }
            self._avatar.updateVehicleAmmo(
                self._vehicle_id, int(equipment.compactDescr), 1, 0, 0, 0, 0)
        self._log('consumables_published items=%s' % (sorted(self._items),))

    def activate(self, item_id):
        """Apply one use through the copied law and spend the item."""
        import BigWorld
        item = self._items.get(int(item_id))
        if item is None or item['quantity'] <= 0:
            self._log('consumable_rejected id=%s' % (item_id,))
            return False
        vehicle = BigWorld.entities.get(self._vehicle_id)
        if vehicle is None or int(getattr(vehicle, 'health', 0)) <= 0:
            return False
        kind = item['kind']
        if kind == 'repair':
            payload = critical_damage.repair_device(vehicle, repair_all=True)
        elif kind == 'medkit':
            payload = critical_damage.restore_crew(vehicle, restore_all=True)
        else:
            payload = critical_damage.use_extinguisher(vehicle)
        if payload is None:
            self._log('consumable_no_effect id=%s kind=%s' % (item_id, kind))
            return False
        item['quantity'] = 0
        self._avatar.updateVehicleAmmo(self._vehicle_id, item['int_cd'],
                                       0, 0, 0, 0, 0)
        self._critical.present(vehicle, payload)
        self._log('consumable_used id=%s kind=%s' % (item_id, kind))
        return True
