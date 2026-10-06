"""Complete positive-player messages for the private driver contracts."""

from effective_params_fixture import effective_params
from gui.mods.offline_lan_0922.lan_client import (
    CLIENT_CAPABILITIES, PROTOCOL_VERSION)


def local_player(player_id=3):
    return {
        'id': player_id, 'name': 'Human', 'vehicle': 'ussr:R11_MS-1',
        'team': 1, 'slot': 0, 'x': 12.0, 'y': 1.0, 'z': 24.0,
        'yaw': 0.0, 'aim_yaw': 0.0, 'gun_pitch': 0.0, 'speed': 0.0,
        'world_pose': True, 'health': 90, 'max_health': 90, 'alive': True,
        'critical': {}, 'critical_revision': 0, 'critical_base_revision': 0,
        'critical_ack_seq': 0, 'input_seq': 0, 'up_cosine': 1.0,
        'landing_observation_seq': 0,
        'equipment_states': [], 'equipment_revision': 0,
        'equipment_intent_seq': 0,
        'equipment_intent_result': {
            'intent_seq': 0, 'accepted': False, 'reason': ''},
        'outfits': {}, 'vehicle_compact_descr': 'dGVzdA==',
        'effective_params': effective_params(),
    }


def driver_bind(generation=1, round_id=2, player_id=3):
    local = local_player(player_id)
    start = {
        'type': 'battle_start', 'protocol': PROTOCOL_VERSION,
        'round_id': round_id, 'state_revision': 4, 'phase': 'loading',
        'map': '07_lakeville', 'players': [local],
        'host_player_id': player_id, 'bot_authority_id': -1,
        'authority_epoch': 1, 'server_time_ms': 1000,
    }
    welcome = {
        'type': 'welcome', 'protocol': PROTOCOL_VERSION, 'phase': 'waiting',
        'player_id': player_id, 'round_id': round_id, 'state_revision': 4,
        'host_player_id': player_id, 'team': 1, 'slot': 0,
        'max_health': 90, 'authority_epoch': 1, 'bot_authority_id': -1,
        'map': '07_lakeville',
        'spawn': {key: local[key] for key in ('x', 'y', 'z', 'yaw')},
        'name': local['name'], 'vehicle': local['vehicle'],
        'outfits': {}, 'vehicle_compact_descr': local['vehicle_compact_descr'],
        'effective_params': local['effective_params'],
        'capabilities': list(CLIENT_CAPABILITIES),
        'server_capabilities': list(CLIENT_CAPABILITIES),
    }
    return {
        'type': 'driver_bind', 'generation': generation,
        'round_id': round_id, 'player_id': player_id,
        'welcome': welcome, 'roster': dict(start, type='roster'),
        'start': start, 'config': {},
    }


def room_message(kind, round_id=2, tick=1):
    value = {
        'type': kind, 'protocol': PROTOCOL_VERSION, 'round_id': round_id,
        'server_tick': tick, 'server_time_ms': 1000 + tick,
        'authority_epoch': 1, 'bot_authority_id': -1,
    }
    if kind == 'snapshot':
        value.update(players=[local_player()], bots=[], bot_manifest=[],
                     projectiles=[], projectile_revision=0,
                     bot_state_revision=0)
    elif kind == 'events':
        value['events'] = [{'kind': 'driver_test_event', 'sequence': tick}]
    elif kind == 'battle_live':
        value.update(state_revision=5, countdown_seconds=10.0,
                     battle_duration_seconds=900.0, timing={
                         'phase': 'prebattle', 'start_in_ms': 10000,
                         'remaining_ms': 900000, 'duration_ms': 900000})
    return value


def envelope(message, generation=1, round_id=2):
    return {'type': 'driver_message', 'generation': generation,
            'round_id': round_id, 'message': message}
