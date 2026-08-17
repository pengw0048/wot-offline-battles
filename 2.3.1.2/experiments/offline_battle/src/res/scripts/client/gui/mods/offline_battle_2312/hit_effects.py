"""Play the armor-hit effect the cell normally commands.

The stock ``Vehicle.showDamageFromShot`` decodes cell-encoded hit points
and runs ``appearance.boundEffects.addNewToNode`` with the shell's
armor effect group. This module builds the same call from the pose this
runtime already owns, so no cell encoding is needed.
"""
from __future__ import absolute_import

from gui.mods.offline_battle_2312 import damage

FULL_DAMAGE = 1.0
NO_DAMAGE = 0.0


def effect_group(result, crits):
    """The stock hitEffectGroup for one resolved shell."""
    if result is None:
        return None
    if result == damage.RICOCHET:
        return 'armorRicochet'
    if crits:
        return 'armorCriticalHit'
    if result == damage.PIERCED:
        return 'armorHit'
    return 'armorResisted'


def _nearest_part(collisions):
    best_name, best_dist = 'hull', None
    for collision in collisions or ():
        dist = float(collision.dist)
        if best_dist is None or dist < best_dist:
            best_name, best_dist = collision.compName, dist
    return best_name


def show(target, shell, landing, result, crits, attacker_id, points):
    import Math
    from items import vehicles as items_vehicles
    from vehicle_systems.tankStructure import TankPartNames, TankPartIndexes
    group = effect_group(result, crits)
    appearance = getattr(target, 'appearance', None)
    if group is None or appearance is None:
        return
    try:
        effects_descr = items_vehicles.g_cache.shotEffects[shell.effectsIndex]
        key_points, effects, _unused = effects_descr[group]
    except (AttributeError, IndexError, KeyError, TypeError):
        return
    part = _nearest_part(landing.collisions)
    alive = bool(target.isAlive())
    try:
        node_name = TankPartNames.getActualNodeNameByPartName(part, alive)
    except Exception:
        node_name = part
    direction = (Math.Vector3(landing.segment_end) -
                 Math.Vector3(landing.segment_start))
    direction.normalise()
    try:
        world_to_node = Math.Matrix(appearance.compoundModel.node(node_name))
        world_to_node.invert()
        local_dir = world_to_node.applyVector(direction)
        matrix = Math.Matrix()
        matrix.setRotateYPR((local_dir.yaw, local_dir.pitch, 0.0))
        matrix.translation = world_to_node.applyPoint(
            Math.Vector3(landing.point))
    except Exception:
        return
    is_player = bool(getattr(target, 'isPlayerVehicle', False))
    fullscreen = is_player and alive
    try:
        part_index = list(TankPartIndexes.ALL).index(part)
    except ValueError:
        part_index = 0
    appearance.boundEffects.addNewToNode(
        node_name, matrix, effects, key_points,
        isPlayerVehicle=is_player,
        showShockWave=fullscreen,
        showFlashBang=fullscreen,
        showFriendlyFlashBang=False,
        entity_id=target.id,
        damageFactor=FULL_DAMAGE if points else NO_DAMAGE,
        attackerID=int(attacker_id),
        hitdir=direction,
        surfaceNormal=matrix.applyVector(Math.Vector3(0.0, 0.0, -1.0)),
        componentIdx=part_index,
        isDynCollision=False)
    try:
        appearance.receiveShotImpulse(direction,
                                      effects_descr['targetImpulse'])
    except Exception:
        pass
    try:
        import BigWorld
        from AvatarInputHandler import ShakeReason
        reason = ShakeReason.HIT if points else ShakeReason.HIT_NO_DAMAGE
        BigWorld.player().inputHandler.onVehicleShaken(
            target, reason, Math.Vector3(landing.point), direction,
            effects_descr['caliber'],
            effects_descr['targetCameraSensitivity'])
    except Exception:
        pass
