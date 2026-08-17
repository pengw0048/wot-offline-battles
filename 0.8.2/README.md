World of Tanks 0.8.2 Offline/LAN Battles
========================================

> [!IMPORTANT]
> This release targets the original Windows 0.8.2 client and its embedded
> Python 2.6 runtime. It does not provide the game client or a standalone game.
> Read `START_NATIVE_TEST_HERE.txt` before installing the version-locked native
> bot-physics build.

Play World of Tanks 0.8.2 offline: no login, no server. You go straight to the
hangar, pick a tank and fight bots on the real maps.

Bots use vehicle roles, stable individual personalities and shared team spots.
All 33 stock maps ship with validated standard-battle tactical routes and
prebaked terrain navigation graphs. The baker understands terrain height,
bridge decks and penalized shallow fords; runtime pathfinding rejects cliffs,
deep water and solid obstacles. A short-horizon oriented-hull predictor
separates nearby tanks, and failed terrain segments are remembered briefly so
a stalled bot replans instead of repeating the same bad edge. In combat,
client-probed cover candidates feed a hold/peek/fire/return cycle; cautious and
aggressive personalities weight them differently, while some armoured drivers
also jiggle forward and backward. This AI is used in both normal offline play
and LAN-authority simulation. Assault and encounter variants are intentionally
outside the supported mode.

The same AI can run through the companion server with only one connected
player. In that mode the server owns global route progress, target
reservations, last-known contacts, lane-pressure rebalancing, cover
reservations and revisioned bot orders. The elected authority client keeps
only the work that depends on proprietary client data: spotting observations,
bounded terrain and cover probes, local steering, shell collision and BigWorld
entity control. Normal offline mode uses the same AI locally as a server-free
fallback.


Install
-------

For the native 1.8.59 experiment, read `START_NATIVE_TEST_HERE.txt` and then
open `START_HERE.txt`: close the game, delete or move aside the old
`res_mods\0.8.2`, then drag the package's complete `0.8.2` folder into the
game's `res_mods` folder. The package also includes double-clickable Windows
and macOS LAN-server launchers.

For a source checkout, close the game and run:

    refresh_client.bat "C:\Games\World_of_Tanks_0.8.2"

The batch file copies the client files and removes one stale entry bytecode
file that otherwise hides updated source code in this old client.

For manual installation from a source checkout, copy this directory's
`scripts/` and `gui/` into:

    <WoT 0.8.2 game root>/res_mods/0.8.2/

The entry file must land at:

    res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.py

When updating an existing installation, also delete:

    res_mods/0.8.2/scripts/client/gui/mods/mod_offhangar.pyc

Do not delete `scripts/client/CameraNode.pyc`; it is the old client's mod
loader. Start the game. If the mod loaded, you go straight to the offline
hangar instead of the login screen.

To uninstall, delete `res_mods/0.8.2/scripts` and `res_mods/0.8.2/gui`. Your
settings survive in `<game root>/offhangar_user/`; delete that folder too only
if you want a clean slate.


Settings
--------

Your editable copy is created on first launch at:

    <game root>/offhangar_user/config.json

It is outside `res_mods`, so updating or deleting the mod never touches it.
Options that a mod update adds later fall back to their defaults until you add
them to your file; the shipped defaults live in `config_defaults.json` next to
the mod and are documented there.

Common settings include:

    nickname                your in-game name
    bots_per_team           15 (15 vs 15)
    spotting_enabled        enemies must be spotted to be seen
    perfect_accuracy        shells land in the centre of the circle
    prebattle_countdown_seconds / auto_spawn_delay_seconds


LAN setup
---------

For optional LAN mode, click the visible `LAN SETTINGS` entry in the
upper-right of the offline hangar. If mouse input is unavailable, `F11`
remains a fallback. Enter the server IP and TCP port, toggle LAN battle, and
press `Enter` to save. Start `lan_battle_server.py`, then click `Battle!` on
every client to join its single waiting room. The queue screen opens only
after the server accepts the connection, and its displayed player total
follows the real server roster. The server terminal prints one `JOIN` line per
client. Use the clickable waiting-room panel to choose a map and click `START
BATTLE`; the server broadcasts one start with that map to every client.

The first client in the battle is elected as map-simulation/rules authority.
It uploads vehicle profiles, standard-battle route anchors and limited
spotting observations plus a bounded set of drivable cover candidates. The
server assigns targets, advances or rebalances routes, reserves cover and
sends monotonic revisioned orders back; unchanged orders are omitted from
later snapshots. All clients receive the same bot names, tanks, positions,
movement, firing, HP and deaths, plus shared capture progress and one shared
battle result. If the authority disconnects, the server elects another
connected client and reacquires the short-lived observations.

Only the server process needs an external Python 3 installation; the client
mod uses the Python 2 runtime embedded in the 0.8.2 client. See `LAN_SERVER.md`
for server commands, diagnostics and Parallels network notes.


In battle
---------

    O / P / L   spawn a bot where you aim: your tank as an enemy clone /
                a random enemy / a random ally
    K           leave the battle


Module and crew damage
----------------------

Shells damage modules and crew the way the era did: every device has its own
HP pool from the vehicle descriptor, its own hit chance from the game's
material table, and repairs itself back to roughly half over time. Damaged
modules cost performance, destroyed ones stop working. Fires start from the
engine or a holed fuel tank and burn out on their own. Repair kits, med kits
and the fire extinguisher work, and the damage panel and crew voice lines
follow.

Interior modules and crew have no collision geometry in the 0.8.2 client, so
their hit boxes come from a per-vehicle profile set covering 251 vehicles.
Set `internal_layout_profiles` to `false` to fall back to a coarser compartment
model.

Set `module_test_mode` to `true` to make bot shells roll every module and crew
critical hit without removing hull HP, so the system can be observed without
the player dying. Turn it off for normal play.


Notes
-----

This is an unofficial compatibility mod and is not affiliated with or
endorsed by Wargaming. World of Tanks and related names are trademarks of
their respective owners. You must supply your own lawfully obtained 0.8.2
client and remain responsible for complying with its terms and applicable
law.

Project code is distributed under the GNU General Public License version 3;
see `LICENSE` and `THIRD_PARTY_NOTICES.md` in the release package or repository
root. That license does not grant rights to the World of Tanks client, assets,
trademarks or other Wargaming property.

Debug logging is off by default. Enable `debug_logging` in `config.json` to
write diagnostics into `python.log`. A correctly loaded source build writes:

    Offline Battles source loader active; LAN settings module enabled

If that line is absent, the updated mod entry did not load.

0.8.2 is a 32-bit client. Long non-stop sessions across many maps slowly grow
memory; restart the client if it becomes sluggish after many battles.

The optional `internal_xray_overlay` draws module and crew boxes through the
armour. It is a debug view for offline battles and is not loaded while off. Do
not enable it in a client used to log into a live server.
