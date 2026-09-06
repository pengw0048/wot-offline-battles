"""Encode Bot publications for tests that describe Bots as mappings.

The wire carries one positional integer row per Bot.  Tests stay readable by
building the mapping they mean and passing it through the real encoder, so a
layout change cannot pass the suite while breaking the wire.
"""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / 'src' / 'res' / 'scripts' /
           'client'))

from gui.mods.offline_lan_0922 import bot_state_codec


def row(state):
    """Encode one Bot mapping exactly as the worker would publish it."""
    return bot_state_codec.encode_row(state)


def rows(states):
    return [bot_state_codec.encode_row(state) for state in states or ()]


def publication(message):
    """Return one ``bot_state`` message with its ``bots`` encoded as rows.

    A message that already carries rows, or that is not a publication at all,
    passes through so a caller can wrap a dispatch boundary unconditionally.
    """
    if not isinstance(message, dict) or 'bots' not in message:
        return message
    message = dict(message)
    message['rows'] = rows(message.pop('bots'))
    return message


PLACEHOLDER_CONTRACTS = tuple(
    {'name': name, 'kind': kind, 'id': index, 'compactDescr': 400 + index,
     'tags': [], 'reuseCount': 0, 'cooldownSeconds': 90.0,
     'autoactivate': kind == 'extinguisher', 'fireStartingChanceFactor': 1.0,
     'repairAll': kind != 'extinguisher', 'bonusValue': 0.0,
     'crewLevelIncrease': 0.0, 'enginePowerFactor': 1.0,
     'turretRotationSpeedFactor': 1.0, 'engineHpLossPerSecond': 0.0,
     'autoReactionSeconds': 1.5}
    for index, (name, kind) in enumerate((
        ('autoExtinguishers', 'extinguisher'),
        ('largeMedkit', 'medkit'),
        ('largeRepairkit', 'repairkit'))))


def round_constants(equipment_contracts=PLACEHOLDER_CONTRACTS):
    """Return the per-round constants a manifest entry carries."""
    return {'equipment_contracts': list(equipment_contracts)}


def decoded(wire, index=0, static=None):
    """Return one Bot mapping from a publication, encoded or not."""
    if isinstance(wire, dict) and 'rows' not in wire:
        return wire['bots'][index]
    rows_ = wire['rows'] if isinstance(wire, dict) else wire
    return bot_state_codec.decode_row(
        rows_[index], round_constants() if static is None else static)


def bots(message, static=None):
    """Return the Bot mappings a publication or a roster message carries.

    A ``bot_state`` publication carries positional rows; a manifest, roster or
    server snapshot still carries mappings. Tests read either through here.
    """
    if isinstance(message, dict) and 'rows' in message:
        constants = round_constants() if static is None else static
        return [bot_state_codec.decode_row(row, constants)
                for row in message['rows']]
    return message['bots']
