"""The medal-bond schedule introduced in 9.20.1 and used in 0.9.22.

Source: https://worldoftanks.eu/en/news/general-news/920-1-bonds-and-medals/
Table: https://eu-wotp.wgcdn.co/dcont/fb/image/medals_en.jpg

These are per-battle awards, not first-time account achievements. Premium
time, vehicle XP factors and offline reward multipliers do not multiply them.
The separate all-Tier-X base-XP payout is deliberately not estimated: its
retail conversion table is not present in the pinned client resources.
"""


# Tier IV-VII, VIII-IX, X. A zero means the medal cannot earn bonds there.
MEDAL_BONDS = {
    'mainGun': (1, 1, 2),
    'supporter': (1, 1, 2),
    'steelwall': (1, 1, 2),
    'medalPascucci': (1, 1, 3),
    'warrior': (1, 1, 3),
    'sniper2': (1, 1, 3),
    'medalLehvaslaiho': (1, 1, 0),
    'evileye': (1, 3, 5),
    'medalOrlik': (1, 3, 0),
    'invader': (1, 3, 5),
    'defender': (1, 3, 5),
    'scout': (1, 3, 5),
    'medalDumitru': (3, 5, 7),
    'medalOskin': (3, 5, 0),
    'medalStark': (3, 5, 7),
    'huntsman': (3, 5, 7),
    'medalGore': (3, 5, 7),
    'medalTamadaYoshio': (3, 5, 0),
    'medalKolobanov': (5, 7, 10),
    'medalFadin': (5, 7, 10),
    'medalNikolas': (5, 7, 0),
    'medalBillotte': (5, 10, 10),
    'medalBurda': (5, 10, 0),
    'medalDeLanglade': (5, 10, 10),
    'medalBrunoPietro': (5, 10, 10),
    'medalTarczay': (5, 10, 10),
    'heroesOfRassenay': (5, 10, 15),
    'medalHalonen': (3, 5, 0),
    'medalRadleyWalters': (3, 5, 7),
    'medalLafayettePool': (5, 10, 10),
}


def medal_reward(name, vehicle_tier):
    if not 4 <= vehicle_tier <= 10 or name not in MEDAL_BONDS:
        return 0
    if name in ('medalRadleyWalters', 'medalLafayettePool') and vehicle_tier < 5:
        return 0
    if name == 'medalHalonen' and vehicle_tier > 8:
        return 0
    column = 0 if vehicle_tier <= 7 else (1 if vehicle_tier <= 9 else 2)
    return MEDAL_BONDS[name][column]


def medal_rewards(achievements, vehicle_tier):
    return dict((name, medal_reward(name, vehicle_tier))
                for name in set(achievements)
                if medal_reward(name, vehicle_tier))


def shop_rewards(record_db_ids):
    groups = {}
    medals = {}
    for group_id, name in enumerate(sorted(MEDAL_BONDS)):
        db_id = record_db_ids.get(('achievements', name))
        if db_id is None:
            continue
        groups[group_id] = {
            'arenaTypes': [1],
            'bonus': {'crystal': 1},
            'vehicleMultipliers': [medal_reward(name, tier)
                                   for tier in range(1, 11)],
        }
        medals[(db_id, 0)] = [group_id]
    return {'isEnabled': True, 'groups': groups, 'medals': medals}
