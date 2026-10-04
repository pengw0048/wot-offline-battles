"""Retired #1513 vehicle definitions excluded from Bot matchmaking.

These vehicles remain loadable in the client and are intentionally exposed to
launcher garage tools for manual player use. They are excluded only from Bot
lineups because their hidden post-removal balance data is not part of the live
0.9.22 random-battle vehicle set.
"""

RETIRED_BOT_VEHICLES_0922 = frozenset((
    "germany:G85_Auf_Panther",
    "germany:G98_Waffentrager_E100",
    "ussr:R75_SU122_54",
    "ussr:R93_Object263B",
    "ussr:R96_Object_430B",
))
