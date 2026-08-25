# World of Tanks Offline Battles

Play standard battles with bots in legacy Windows clients, alone or with
friends on a LAN. The packaged launcher currently targets 0.9.22; the 0.8.2
port remains available from source:

| Port | Supported client |
| --- | --- |
| [`0.8.2`](0.8.2/) | World of Tanks 0.8.2 |
| [`0.9.22`](0.9.22/) | Chinese HD client 0.9.22.0.1 #1513 |

You supply your own client. It provides maps, vehicles, rendering, HUD and
version-locked native geometry queries. This repository provides the client
mod, Rust bot and battle authority, LAN server and launcher.

## Play

1. Download `WoT-Offline-Battles-Launcher-Windows.zip` from the releases,
   unpack it, and start `WoT-Offline-Battles-Launcher.exe`.
2. Select your World of Tanks folder. The launcher recognizes the client,
   removes any older mod files and installs the matching mod.
3. Select a mode:
   - **Single player**: you play alone against bots. The launcher runs the
     server for you; every battle is a server battle in both clients.
   - **Host a LAN battle**: other players join this PC. The launcher starts the
     server and prints the address to give them.
   - **Join a LAN battle**: type the host's address, for example
     `192.168.1.20`.
4. Click **Start game**. In the garage, fit a tank and click **Battle!**.
   Everyone lands in the LAN waiting room over the stock queue screen. The
   host picks the map and clicks **START BATTLE**. **LEAVE** returns you to
   the garage.

When you host, approve the UAC prompt that opens TCP 28782 for the bundled Rust
server executable.
Run the server only on a network you trust.

## The garage

The 0.9.22 client gets a working offline garage:

- Every vehicle in the client is owned, and every module in its own tech tree
  is unlocked. Each vehicle starts with its top chassis, turret, gun, engine,
  radio and fuel tank, plus an automatic fire extinguisher, a large first aid
  kit and a large repair kit.
- You can change modules, optional devices, consumables, shells, camouflage
  and crew skills. Every item costs nothing.
- The garage is written to `mods/configs/offline_lan_0922/garage_state.json`
  after each change, so it survives a restart.
- The battle runs the vehicle the garage fitted. Crew skills, optional devices
  and consumables move the same values the garage parameters panel shows: view
  range, concealment, reload, aim time, dispersion, traverse, engine power,
  terrain resistance and repair speed.

The launcher's Tools tab can also edit vehicle data directly. A vehicle data
profile is a named set of Packed XML field changes (health, damage,
penetration, armour, speeds, reload and more) made in the launcher's editor;
it never changes `scripts.pkg`. In single player the selected profile is
activated only for that session and removed when the game closes. In a LAN
room the host's profile is pinned for the whole room: the room server shares
the modified package members with every joining launcher, which installs the
same temporary overlay before the game starts, so every client (host, hidden
native-world oracle and joiners) runs identical modified vehicle data. A room
whose profile changed after it started must be restarted first.

## What is in the battle

- 15-versus-15 spawning, countdown, capture, elimination and timeout, then a
  clean next round.
- Same-era gunnery: shell flight time and gravity, dispersion, penetration by
  range, normalization, ricochet, overmatch, spaced armour, HE splash, ramming,
  module and crew damage, fires and repairs.
- Spotting with view range, camouflage, movement, firing, foliage, line of
  sight and last-known positions.
- Bots that use map geometry, terrain, water, firing lanes, team strength and
  shared contacts to route, take cover, pick targets and choose ammunition,
  including SPG arcs. Navigation and foliage data ship for all 33 supported
  0.8.2 maps and all 41 supported 0.9.22 maps.
- Live combat statistics, a damage log with assists, hit and critical-damage
  messages, target outlines, vehicle fires, wrecks, and a consumables panel
  that counts down each cooldown.
- A LAN match is one shared battle: lineups, countdown, orders, projectiles,
  health, critical damage, destructibles, capture and results stay
  synchronized. Losing the mandatory native-world oracle ends the active round
  as a technical failure instead of transferring authority to a player.

This is a reconstruction from the frozen clients and same-era mechanics, not
Wargaming's retail server. LAN play assumes trusted clients. Native rendering,
physics and frame pacing can only be judged in the Windows client.

## Build it yourself

```bash
# 0.9.22 x64 Rust LAN server (Windows)
pwsh -NoProfile -File 0.9.22/server/build_windows_server.ps1
# 0.9.22 client package, with CPython 2.7
python2.7 0.9.22/build_wotmod.py
# Windows launcher, after the 0.9.22 package exists
pwsh -NoProfile -File launcher/build_launcher.ps1
```

The launcher carries the Rust LAN server and the exact 0.9.22 client mod. It
writes the server address, installs the mod, starts the hidden native-world
oracle and visible game client, and stops its child processes when the game
closes.
The `Build Windows launcher` GitHub Actions workflow can also be run manually;
it publishes the complete Windows launcher ZIP as one artifact.

Tests:

```bash
cd 0.8.2 && python3 -m unittest discover -s tests
python3 -m unittest discover -s 0.9.22/tests
cd launcher && python3 -m unittest discover -s tests
```

Project code is distributed under [`GPL-3.0`](LICENSE). World of Tanks and its
assets are not included; this project is not affiliated with or endorsed by
Wargaming. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for lineage
and bundled runtimes.
