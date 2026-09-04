from __future__ import print_function

"""Ammunition, reload and shot-scatter state for pinned #1513.

The source implementation keeps this state inside ``offline_battle.py``.
These methods preserve its descriptor reads, bloom/convergence formulas,
empty-at-countdown start and clip transitions. Shot dispersion uses the
bounded two-sigma model documented for #1513 instead of the unbounded 0.8.2
offline approximation.
"""

import math
import random

from gui.mods.offline_lan_0922 import burst_mechanics
from gui.mods.offline_lan_0922 import loadout


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _positive(value, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if value > 0.0 else float(default)


class GunState(object):

    def __init__(self, descriptor, loadout_modifiers=None, ammo_layout=None):
        gun = descriptor.gun
        self.shots = tuple(_field(gun, 'shots', ()) or ())
        self.base_dispersion = _positive(
            _field(gun, 'shotDispersionAngle', 0.1), 0.1)
        factors = _field(gun, 'shotDispersionFactors', {}) or {}
        self.after_shot = _positive(_field(factors, 'afterShot', 1.5), 1.5)
        self.after_shot_in_burst = burst_mechanics.after_shot_factor(
            gun, False)
        self.burst_count, self.burst_interval = \
            burst_mechanics.descriptor_burst(gun)
        self.aim_time = _positive(_field(gun, 'aimingTime', 2.0), 2.0)
        self.reload = _positive(_field(gun, 'reloadTime', 5.0), 5.0)
        clip = _field(gun, 'clip', (1, 2.0)) or (1, 2.0)
        try:
            self.clip_size = max(1, int(clip[0]))
        except (TypeError, ValueError, IndexError):
            self.clip_size = 1
        try:
            self.clip_reload = _positive(clip[1], 2.0)
        except (TypeError, ValueError, IndexError):
            self.clip_reload = 2.0
        maximum = _field(descriptor, 'maxAmmo', None)
        if maximum is None:
            maximum = _field(gun, 'maxAmmo', None)
        if maximum is None:
            maximum = _field(_field(descriptor, 'turret', None),
                             'maxAmmo', 45)
        try:
            maximum = max(0, int(maximum))
        except (TypeError, ValueError):
            maximum = 45
        # The garage layout wins when we know it.  Only an unknown loadout
        # falls back to an even split of maxAmmo.
        self.ammo = self._layout_ammo(ammo_layout)
        if self.ammo is None:
            self.ammo = self._distribute_ammo(maximum, len(self.shots))
        try:
            selected = int(getattr(descriptor, 'activeGunShotIndex', 0))
        except (TypeError, ValueError):
            selected = 0
        self.shot_index = max(0, min(selected, max(0, len(self.shots) - 1)))
        if not self.ammo or self.ammo[self.shot_index] <= 0:
            # Retail loads the first shell the vehicle actually carries.
            for index, count in enumerate(self.ammo):
                if count > 0:
                    self.shot_index = index
                    break
        # #1513 gives each of these its own crew factor: the loader drives
        # reload, the gunner drives aiming and dispersion, and the mounted
        # artefact strength arrives through miscAttrs.
        if loadout_modifiers is None:
            loadout_modifiers = loadout.baseline()
        self.loadout = dict(loadout_modifiers)
        crew_multiplier = _positive(
            self.loadout.get('crew_multiplier'), 1.0)
        self._dispersion_factor = _positive(
            self.loadout.get('dispersion_factor'), crew_multiplier)
        self._aim_time_factor = _positive(
            self.loadout.get('aim_time_factor'), crew_multiplier)
        self._reload_factor = _positive(
            self.loadout.get('reload_factor'), crew_multiplier)
        self.base_dispersion *= self._dispersion_factor
        self.aim_time *= self._aim_time_factor
        self.reload *= self._reload_factor
        self.clip = 0
        self.reload_time = self.reload
        self.reload_duration = self.reload
        self.dispersion = self.base_dispersion
        self.load_started = False
        self.pending_index = None
        self._burst_remaining = 0
        self._burst_total = 0

    @staticmethod
    def _shot_compact_descrs(shots):
        result = []
        for shot in shots:
            shell = _field(shot, 'shell', {})
            try:
                result.append(int(_field(shell, 'compactDescr', 0)))
            except (TypeError, ValueError):
                raise RuntimeError(
                    '#1513 siege-mode gun has an invalid shell descriptor')
        return tuple(result)

    def adopt_descriptor(self, descriptor):
        """Refresh mode-dependent gun law without resetting ammunition.

        ``CompositeVehicleDescriptor`` swaps its active gun, chassis and
        turret when the exact client receives a final Siege-mode state.  Ammo,
        reload progress and bloom remain one continuous battle state, while
        dispersion, aiming, reload and shell objects must come from the newly
        active descriptor.
        """
        gun = descriptor.gun
        shots = tuple(_field(gun, 'shots', ()) or ())
        if (self._shot_compact_descrs(shots) !=
                self._shot_compact_descrs(self.shots)):
            raise RuntimeError(
                '#1513 siege-mode gun changed its ammunition contract')
        factors = _field(gun, 'shotDispersionFactors', {}) or {}
        base_dispersion = (
            _positive(_field(gun, 'shotDispersionAngle', 0.1), 0.1) *
            self._dispersion_factor)
        after_shot = _positive(_field(factors, 'afterShot', 1.5), 1.5)
        after_shot_in_burst = burst_mechanics.after_shot_factor(gun, False)
        burst_count, burst_interval = burst_mechanics.descriptor_burst(gun)
        aim_time = (_positive(_field(gun, 'aimingTime', 2.0), 2.0) *
                    self._aim_time_factor)
        reload_time = (_positive(_field(gun, 'reloadTime', 5.0), 5.0) *
                       self._reload_factor)
        clip = _field(gun, 'clip', (1, 2.0)) or (1, 2.0)
        try:
            clip_reload = _positive(clip[1], 2.0)
        except (TypeError, ValueError, IndexError):
            clip_reload = 2.0
        previous = (
            self.base_dispersion, self.after_shot,
            self.after_shot_in_burst, self.burst_count,
            self.burst_interval, self.aim_time, self.reload,
            self.clip_reload)
        self.shots = shots
        self.base_dispersion = base_dispersion
        self.after_shot = after_shot
        self.after_shot_in_burst = after_shot_in_burst
        self.burst_count = burst_count
        self.burst_interval = burst_interval
        self.aim_time = aim_time
        self.reload = reload_time
        self.clip_reload = clip_reload
        return previous != (
            self.base_dispersion, self.after_shot,
            self.after_shot_in_burst, self.burst_count,
            self.burst_interval, self.aim_time, self.reload,
            self.clip_reload)

    def _layout_ammo(self, ammo_layout):
        """Map a garage shell layout onto this gun's shot order.

        ``ammo_layout`` is the mounted ``{shellCompactDescr: count}`` mapping.
        A shell the current gun cannot fire is ignored, and a shot the player
        carries none of stays at zero, so an empty slot really is empty.
        """
        if ammo_layout is None:
            return None
        wanted = {}
        for compact_descr, count in dict(ammo_layout).items():
            try:
                wanted[int(compact_descr)] = max(0, int(count))
            except (TypeError, ValueError):
                continue
        if not wanted:
            # An explicit empty client snapshot means the tank carries no
            # rounds. Only ``None`` means the garage layout was unavailable
            # and permits the legacy maxAmmo fallback above.
            return [0 for unused_shot in self.shots]
        result = []
        matched = False
        for shot in self.shots:
            shell = _field(shot, 'shell', {})
            compact_descr = _field(shell, 'compactDescr', None)
            try:
                compact_descr = int(compact_descr)
            except (TypeError, ValueError):
                compact_descr = None
            count = wanted.get(compact_descr, 0)
            if compact_descr is not None and compact_descr in wanted:
                matched = True
            result.append(count)
        if not matched:
            raise RuntimeError(
                'client ammunition does not match the installed gun')
        return result

    def bind_client_contract(self, contract, ammo_layout):
        """Bind the visible client's mounted shot order and clip shape.

        The hidden worker still needs its native descriptor for collision and
        presentation geometry, but its local XML must not redefine the
        player's edited shells.  Dynamic reload/clip progress arrives in the
        input checkpoint; this binding supplies the immutable array shape the
        checkpoint indexes.
        """
        if not isinstance(contract, dict):
            raise RuntimeError('client gun contract is invalid')
        try:
            clip_size = int(contract['clip_size'])
            entries = tuple(contract['shots'])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError('client gun contract is invalid')
        if clip_size < 1 or not entries:
            raise RuntimeError('client gun contract is invalid')
        wanted = {}
        try:
            for compact_descr, count in dict(ammo_layout or {}).items():
                wanted[int(compact_descr)] = max(0, int(count))
        except (TypeError, ValueError):
            raise RuntimeError('client ammunition snapshot is invalid')
        shots = []
        ammo = []
        matched = False
        seen = set()
        for entry in entries:
            try:
                compact_descr = int(entry['compact_descr'])
                source_shot = entry['source_shot']
            except (KeyError, TypeError, ValueError):
                raise RuntimeError('client gun contract is invalid')
            if compact_descr < 1 or compact_descr in seen:
                raise RuntimeError('client gun contract is invalid')
            seen.add(compact_descr)
            shots.append(source_shot)
            ammo.append(wanted.get(compact_descr, 0))
            matched = matched or compact_descr in wanted
        if wanted and not matched:
            raise RuntimeError(
                'client ammunition does not match the mounted gun contract')
        self.shots = tuple(shots)
        self.ammo = ammo
        self.clip_size = clip_size
        self.shot_index = max(
            0, min(int(self.shot_index), len(self.shots) - 1))
        if self.ammo[self.shot_index] <= 0:
            for index, count in enumerate(self.ammo):
                if count > 0:
                    self.shot_index = index
                    break
        self.clip = 0
        self.pending_index = None
        self._burst_remaining = 0
        self._burst_total = 0
        self._client_gun_contract = contract
        return True

    @staticmethod
    def _distribute_ammo(maximum, count):
        if count <= 0:
            return []
        # Exact 0.8.2 offline fallback when CurrentVehicle has no shell counts.
        weights = (0.6, 0.3, 0.1)
        result = []
        for index in range(count):
            weight = weights[index] if index < len(weights) else weights[-1]
            quantity = int(maximum * weight)
            if quantity == 0 and maximum > 0:
                quantity = 1
            result.append(quantity)
        return result

    def sync_shell_index(self, index, instant=False):
        """Load ``index`` now, restarting the reload from zero.

        ``instant`` is the finished ``loader_intuition`` perk: the new shell
        arrives loaded instead of starting a reload.
        """
        if not self.shots:
            return False
        try:
            index = int(index)
        except (TypeError, ValueError):
            return False
        index = max(0, min(index, len(self.shots) - 1))
        self.pending_index = None
        if index == self.shot_index:
            return False
        self.shot_index = index
        self.load_started = False
        if instant and index < len(self.ammo) and self.ammo[index] > 0:
            self.clip = min(self.clip_size, self.ammo[index])
            self.reload_time = 0.0
            self.reload_duration = self.reload
            return True
        self.clip = 0
        self.reload_time = self.reload
        self.reload_duration = self.reload
        return True

    def request_shell_index(self, index):
        """Queue ``index`` as the next shell without touching the reload.

        #1513 ``AmmoController.getNextSettingCode`` reads no reload field: the
        first press on a shell key always sends VEHICLE_SETTING.NEXT_SHELLS and
        the second press sends CURRENT_SHELLS.  So a queued shell waits for the
        round in progress, whether that round is loaded or still loading.
        """
        if not self.shots:
            return False
        try:
            index = int(index)
        except (TypeError, ValueError):
            return False
        index = max(0, min(index, len(self.shots) - 1))
        if index >= len(self.ammo) or self.ammo[index] <= 0:
            return False
        if self.shot_index < len(self.ammo) and self.ammo[self.shot_index] <= 0:
            # An empty shell type is never fired, so nothing would ever
            # promote the queued round.
            return self.sync_shell_index(index)
        self.pending_index = None if index == self.shot_index else index
        return False

    def reload_partial_clip(self):
        """Empty a partial cassette and begin one full reload.

        #1513 sends ``VEHICLE_SETTING.RELOAD_PARTIAL_CLIP`` both for a
        partially spent cassette and for a queued shell type.  An already
        empty cassette is already in its full reload cycle and must not have
        that cycle restarted by another reload-key press.
        """
        if self.clip_size <= 1 or not self.shots:
            return False
        current = self.shot_index
        if current >= len(self.ammo) or self.ammo[current] <= 0:
            return False
        pending = self.pending_index
        if pending is not None:
            if (pending < 0 or pending >= len(self.ammo) or
                    self.ammo[pending] <= 0):
                pending = None
            self.pending_index = None
        if pending is None and (
                self.clip >= self.clip_size or
                (self.clip <= 0 and self.reload_time > 0.0)):
            return False
        if pending is not None:
            self.shot_index = pending
        self.clip = 0
        self.reload_time = self.reload
        self.reload_duration = self.reload
        self.load_started = False
        return True

    def can_fire(self, battle_live=True):
        if (not battle_live or not self.shots or self.reload_time > 0.0 or
                self._burst_remaining > 0):
            return False
        if self.shot_index >= len(self.ammo):
            return False
        return self.clip > 0 and self.ammo[self.shot_index] > 0

    def commit_fire(self, reload_factor=1.0):
        if not self.begin_burst(1):
            return False
        return self.commit_burst_round(True, reload_factor)

    def begin_burst(self, count):
        """Freeze the loaded portion consumed by one native trigger."""
        if not self.can_fire(True):
            return False
        try:
            count = int(count)
        except (TypeError, ValueError, OverflowError):
            return False
        available = (self.ammo[self.shot_index]
                     if self.shot_index < len(self.ammo) else 0)
        count = min(count, self.clip, available)
        if count <= 0:
            return False
        self._burst_total = count
        self._burst_remaining = count
        return True

    def commit_burst_round(self, final_round, reload_factor=1.0):
        """Debit one real burst shell and start reload only after the last."""
        if self._burst_remaining <= 0:
            return False
        expected_final = self._burst_remaining == 1
        if bool(final_round) != expected_final:
            return False
        index = self.shot_index
        if (index >= len(self.ammo) or self.ammo[index] <= 0 or
                self.clip <= 0):
            return False
        self.ammo[index] -= 1
        self.clip -= 1
        bloom = (self.after_shot if expected_final else
                 self.after_shot_in_burst)
        jump = self.base_dispersion * bloom
        self.dispersion = math.sqrt(
            self.dispersion ** 2 + jump ** 2)
        self.dispersion = min(
            self.dispersion, self.base_dispersion * 15.0)
        self._burst_remaining -= 1
        if not expected_final:
            self.reload_time = 0.0
            return True
        self._burst_total = 0
        if self.clip > 0:
            self.reload_time = self.clip_reload
            self.reload_duration = self.clip_reload
        else:
            self.reload_time = self.reload * max(0.0, float(reload_factor))
            self.reload_duration = self.reload_time
        if self.ammo[index] <= 0:
            for offset in range(1, len(self.ammo) + 1):
                candidate = (index + offset) % len(self.ammo)
                if self.ammo[candidate] > 0:
                    self.shot_index = candidate
                    self.clip = min(self.clip_size, self.ammo[candidate])
                    self.reload_time = max(self.reload_time, self.reload)
                    self.reload_duration = self.reload_time
                    break
        pending = self.pending_index
        self.pending_index = None
        if (pending is not None and pending != self.shot_index and
                pending < len(self.ammo) and self.ammo[pending] > 0):
            self.shot_index = pending
            self.clip = 0
            self.reload_time = self.reload
            self.reload_duration = self.reload
            self.load_started = False
        return True

    def cancel_burst(self, reload_factor=1.0):
        """End an unlaunched tail after a final physical fire gate closes."""
        if self._burst_remaining <= 0:
            return False
        self._burst_remaining = 0
        self._burst_total = 0
        if self.clip > 0:
            self.reload_time = self.clip_reload
            self.reload_duration = self.clip_reload
        else:
            self.reload_time = self.reload * max(0.0, float(reload_factor))
            self.reload_duration = self.reload_time
        return True

    def tick(self, dt, battle_live, move_speed, rotation_speed,
             turret_speed, descriptor, dispersion_factor=1.0,
             aim_time_factor=1.0):
        dt = max(0.0, float(dt))
        gun = descriptor.gun
        target_dispersion = self.base_dispersion
        try:
            chassis_factors = _field(
                descriptor.chassis, 'shotDispersionFactors')
            if chassis_factors is None:
                raise RuntimeError(
                    '#1513 chassis shot dispersion factors are unavailable')
            move_factor, rotation_factor = chassis_factors
            gun_factors = _field(gun, 'shotDispersionFactors', {}) or {}
            turret_factor = _field(gun_factors, 'turretRotation', 0.0)
            move_term = (float(move_speed) * float(move_factor) *
                         self.loadout.get('bloom_move_factor', 1.0))
            rotation_term = (float(rotation_speed) * float(rotation_factor) *
                             self.loadout.get('bloom_rotation_factor', 1.0))
            turret_term = (float(turret_speed) * float(turret_factor) *
                           self.loadout.get('bloom_turret_factor', 1.0))
            target_dispersion = self.base_dispersion * math.sqrt(
                1.0 + move_term * move_term +
                rotation_term * rotation_term +
                turret_term * turret_term)
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError(
                '#1513 shot dispersion descriptor is invalid: %s' % error)
        target_dispersion *= max(0.0, float(dispersion_factor))
        aiming_time = self.aim_time * max(0.0, float(aim_time_factor))
        if self.dispersion > target_dispersion:
            factor = math.exp(-dt / max(aiming_time, 0.1))
            self.dispersion = target_dispersion + (
                self.dispersion - target_dispersion) * factor
        else:
            self.dispersion = min(
                self.dispersion +
                (target_dispersion - self.dispersion) * 0.2, 5.0)
        if not battle_live:
            self.load_started = False
            return
        self.load_started = True
        if self.reload_time <= 0.0:
            return
        self.reload_time -= dt
        if self.reload_time <= 0.0:
            self.reload_time = 0.0
            if self.clip == 0 and self.shot_index < len(self.ammo):
                self.clip = min(
                    self.clip_size, self.ammo[self.shot_index])

    def scatter(self, direction, perfect_accuracy=False, gauss=None,
                dispersion_angle=None, uniform=None):
        """Scatter inside the current #1513 aiming cone.

        Retail uses a radial normal distribution with the aiming circle as a
        hard boundary. Since 8.6 the circle is the two-sigma limit and an
        outlying normal sample is redistributed from the centre to the edge
        instead of being left outside or piled on its edge. 9.6 subsequently
        reweighted the innermost zone, but the exact server-side zone table is
        not present in the #1513 client.

        The former port added three unbounded ``dispersion / 3`` samples to
        the world axes. About 1.1 percent of its lateral samples landed beyond
        the visible circle, which made a partially aimed shell appear to fly
        somewhere the reticle said was impossible. Sample one documented
        radial two-sigma value and one azimuth around the barrel, then
        construct the final unit vector by angular offset so the visible
        circle and authoritative projectile share one boundary.
        """
        dispersion = (self.dispersion if dispersion_angle is None
                      else float(dispersion_angle))
        if perfect_accuracy or dispersion <= 0.0:
            return direction
        gauss = gauss or random.gauss
        uniform = uniform or random.uniform
        sigma = dispersion / 2.0
        radius = abs(gauss(0.0, sigma))
        if radius > dispersion:
            radius = dispersion * uniform(0.0, 1.0)
        angle = uniform(0.0, 2.0 * math.pi)
        offset_x = radius * math.cos(angle)
        offset_y = radius * math.sin(angle)

        dx, dy, dz = float(direction.x), float(direction.y), float(direction.z)
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= 0.0:
            return direction
        dx, dy, dz = dx / length, dy / length, dz / length

        # Cross the barrel with its least-aligned world axis for a stable
        # orthonormal basis at every turret pitch and yaw.
        if abs(dx) <= abs(dy) and abs(dx) <= abs(dz):
            rx, ry, rz = 1.0, 0.0, 0.0
        elif abs(dy) <= abs(dz):
            rx, ry, rz = 0.0, 1.0, 0.0
        else:
            rx, ry, rz = 0.0, 0.0, 1.0
        tx = dy * rz - dz * ry
        ty = dz * rx - dx * rz
        tz = dx * ry - dy * rx
        tangent_length = math.sqrt(tx * tx + ty * ty + tz * tz)
        tx, ty, tz = (tx / tangent_length, ty / tangent_length,
                      tz / tangent_length)
        ux = dy * tz - dz * ty
        uy = dz * tx - dx * tz
        uz = dx * ty - dy * tx

        if radius > 0.0:
            ox, oy = offset_x / radius, offset_y / radius
            side_x, side_y, side_z = (
                tx * ox + ux * oy,
                ty * ox + uy * oy,
                tz * ox + uz * oy)
            cosine, sine = math.cos(radius), math.sin(radius)
            direction.x = dx * cosine + side_x * sine
            direction.y = dy * cosine + side_y * sine
            direction.z = dz * cosine + side_z * sine
        else:
            direction.x, direction.y, direction.z = dx, dy, dz
        direction.normalise()
        return direction
