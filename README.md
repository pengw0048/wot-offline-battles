# World of Tanks Offline Battles

An unofficial compatibility project for playing standard battles with bots in
two legacy Windows clients:

| Port | Supported client | Play modes |
| --- | --- | --- |
| [`0.8.2`](0.8.2/) | World of Tanks 0.8.2 | Server-free single-player or trusted LAN |
| [`0.9.22`](0.9.22/) | Chinese HD client 0.9.22.0.1 #1513 | Server-backed single-player or trusted LAN |

The [`2.3.1.1`](2.3.1.1/) directory is a development-only interface POC for
the pinned North American HD client. It observes the stock offline map loader;
it is not yet a playable offline battle port. Its formal porting baseline
requires reuse of the mature 0.8.2 behavior instead of extending the POC into
a parallel implementation.

The current repository head is a pre-release test candidate. A formal release
will follow validation on two Windows PCs.

The original client still provides the maps, vehicles, rendering, HUD, physics
and other proprietary runtime data. This repository provides the client mods,
bot and battle logic, a small LAN coordinator, build tools and tests. It does
not include the game client or its assets, and it is not a replacement for the
original BigWorld server.

## What makes this repository different

The two credited reference projects are much narrower at the revisions used
here. [`mod_offhangar_legacy`](https://github.com/SigmaTel71/mod_offhangar_legacy/tree/312534823dab535457f8578d9eae6cf3c549944e)
describes itself as a partially functional offline hangar: it bypasses login
and supplies enough account data to inspect vehicles, but does not implement an
arena or combat. [`wot-offline-server`](https://github.com/the-tuxedo-cat/wot-offline-server/tree/c0bc550c46deac980194b7b860ee8781d53ec97b)
is an unfinished map-and-vehicle sandbox: it can load a map, an Avatar and one
hard-coded test vehicle, while firing only plays an effect and its aiming
handlers are stubs.

This repository carries those foundations into a much higher-fidelity
reconstruction of a standard World of Tanks battle:

- **The original client remains the game engine.** Each version-locked port
  drives the stock BigWorld maps, Avatar/Vehicle lifecycle, vehicle
  descriptors, gun, reticle, camera, collision, destructibles and HUD. The two
  ports are audited against their exact embedded Python runtimes and native
  contracts instead of sharing transplanted bytecode or replacing the client
  with a detached simulator.
- **Combat follows same-era tank mechanics, not simple hitpoint trading.** A
  round includes 15-versus-15 spawning, countdown, movement and gun limits,
  ammunition and reloads, elapsed shell flight with gravity and moving-target
  sweeps, dispersion, range-dependent penetration, normalization, ricochet,
  overmatch, spaced armour, HE splash, ramming, module and crew damage, fires
  and repairs. Spotting accounts for view range, camouflage, movement, firing,
  foliage, line of sight and last-known positions. Capture, elimination and
  timeout produce a shared result, followed by clean repeated-round state.
- **Bots fight the map and battle state, not just the nearest target.** Vehicle
  class and statistics shape stable roles and personalities. Map geometry,
  terrain, water, traffic, firing lanes, team strength and shared contacts feed
  route, cover, target and ammunition decisions. Bots can defend, flank, angle,
  peek, withdraw, stage artillery and use ballistic SPG arcs. The repository
  ships map-specific navigation and foliage data for all 33 supported 0.8.2
  maps and all 41 supported 0.9.22 maps.
- **A LAN match is one shared battle, not a collection of moving vehicles.**
  The trusted-LAN coordinator synchronizes lineups, countdown, tactical orders,
  projectiles, HP and critical damage, destructibles, capture, results and
  round transitions. Round and revision fencing rejects stale or duplicate
  combat state, while authority failover preserves the match if the active bot
  controller disconnects.

This is a reconstruction from the frozen clients and same-era mechanics, not a
source-identical reimplementation of Wargaming's retail server. It implements
standard battles only; LAN play assumes trusted clients and uses a coordinator,
not a full or adversarial retail server. The pre-release candidate still awaits
final validation on two Windows PCs. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for exact project lineage and
licensing.

## Installation

You must supply your own compatible Windows client. Close the game before
copying files, and use the client and server from the same repository revision.

### World of Tanks 0.8.2

1. Delete or move aside the existing `<game root>\res_mods\0.8.2` directory.
2. From this repository, run:

   ```bat
   0.8.2\refresh_client.bat "C:\Games\World_of_Tanks_0.8.2"
   ```

   Alternatively, copy `0.8.2/scripts/` and `0.8.2/gui/` into
   `<game root>\res_mods\0.8.2\`.
3. Start the game. A successful installation goes directly to the offline
   garage; select a tank and click **Battle!** for a local battle.

For LAN play, install Python 3 on one computer and double-click
`0.8.2\RUN_SERVER.bat`. On every client, open **LAN SETTINGS**, enter that
computer's LAN address and port `28782`, enable LAN Battle, then click
**Battle!**. The waiting-room host chooses a map and starts the battle.

The current 0.8.2 native-physics build accepts only its pinned executable. See
[`0.8.2/START_HERE.txt`](0.8.2/START_HERE.txt) if it does not load.

### World of Tanks 0.9.22.0.1 #1513

1. Use the exact frozen Chinese HD `0.9.22.0.1 #1513` client.
2. Obtain the matching client ZIP and extract it directly into the game root.
   The archive already contains the correct `mods` layout. Before the formal
   release, this ZIP is supplied as a separate test artifact; it is not stored
   in the source repository.
3. Install Python 3 on the computer that will host the battle, then run from
   this repository:

   ```bat
   py -3 0.9.22\server\lan_battle_server.py --host 0.0.0.0 --port 28782
   ```

4. Allow TCP port `28782` through the host firewall. In each client, use the
   stock **Battle!** flow and edit the `LAN SERVER: host:port` line in the
   native window to `<host LAN IP>:28782`. The first waiting player chooses a
   map and starts the shared round.

See [`0.9.22/INSTALL.txt`](0.9.22/INSTALL.txt) for troubleshooting and the
exact package boundary.

Project code is distributed under [`GPL-3.0`](LICENSE). World of Tanks and its
assets are not included; this project is not affiliated with or endorsed by
Wargaming.
