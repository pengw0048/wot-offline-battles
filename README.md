# World of Tanks Offline Battles

Play standard battles with bots in the Chinese HD Windows client
`0.9.22.0.1 #1513`, alone or with friends on a LAN.

You supply your own client. The client still provides the maps, vehicles,
rendering, HUD and physics. This repository provides the client mod, the bot
and battle logic, a small LAN server and a launcher.

## Play

1. Download `WoT-Offline-Battles-Launcher-Windows.zip` from the releases,
   unpack it, and start `WoT-Offline-Battles-Launcher.exe`.
2. Select your World of Tanks folder. The launcher recognizes the client,
   removes any older mod files and installs the matching mod.
3. Select a mode:
   - **Single player**: you play alone against bots. The launcher runs the
     server for you; every battle uses the same LAN authority path.
   - **Host a LAN battle**: other players join this PC. The launcher starts the
     server and prints the address to give them.
   - **Join a LAN battle**: type the host's address, for example
     `192.168.1.20`.
4. Click **Start game**. In the garage, fit a tank and click **Battle!**.
   Everyone lands in the LAN waiting room over the stock queue screen. The
   host picks the map - **RANDOM MAP**, or **MAP** to browse the client's own
   map window and choose a battle time - and clicks **START BATTLE**.
   **LEAVE** returns you to the garage.

The mod's waiting room and LAN notifications follow the launcher's selected
English or Simplified Chinese language when you start the game. **Automatic**
uses the same resolved system language as the launcher. Restart the game after
changing this selection. Stock garage and battle UI keep the client's language;
Chinese map labels come from the client's own arena catalog. Direct batch-file
launches retain English unless `WOT_OFFLINE_UI_LANGUAGE=zh` is set.

When you host, approve the UAC prompt that opens TCP 28782 for the launcher.
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
simulation worker and joiners) runs identical modified vehicle data. A room
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
  including SPG arcs. Navigation and foliage data ship for all 41 supported
  standard-battle maps.
- Team text chat, minimap pings and fixed battle messages are relayed to
  human teammates, including the sender, independently from Bot responses.
  Text and pings remain available during the countdown and after the sender's
  tank is destroyed; a team with no living Bots can still communicate.
  Text uses the stock team
  channel, formatting and cooldown. Reload, cassette and SPG aim-area messages
  preserve the status supplied by the stock client. Common/all-team chat is
  not supported.
- Stock battle commands can direct nearby allied Bots: request help, name a
  target, ask a Bot to follow or stop, return to base, or ping a minimap cell.
  General requests prefer up to three mobile Bots within 300 metres. If none
  nearby can respond, up to two eligible Bots farther away answer instead.
  Selection is deterministic; a named ally command addresses only that Bot.
  Each assigned Bot replies through the stock team-message system. Movement
  orders last up to two minutes, while short
  tactical commands last 15 seconds. Once accepted, navigation orders override
  autonomous tactical choices; unavailable Bots or missing destinations produce
  no positive reply.
  Minimap requests choose passable ground inside the cell; following Bots
  leave room behind the player. Native menu, marker and sound presentation
  still needs acceptance on the supported Windows client.
- Automatically generated Bot lineups contain no self-propelled artillery.
  Player vehicles and manually assigned Bot lineups remain unrestricted;
  tank destroyers are not artillery and remain in the automatic pool.
- Bot gunnery skill: every Bot is a rookie, regular, veteran or elite gunner.
  The tier picks the crew level its vehicle is trained to, and how long that
  gunner takes to react, how patiently it waits for the aiming circle, how
  far off centre it lays the gun and how badly it leads a moving target. The
  waiting room chooses the whole roster's mix - easy, relaxed, pub mix, hard
  or brutal - and the launcher's exact Bot lineup can pin one slot's tier,
  with or without also pinning its vehicle.
- Live combat statistics, a damage log with assists, hit and critical-damage
  messages, target outlines, vehicle fires, wrecks, and a consumables panel
  that counts down each cooldown.
- A LAN match is one shared battle: lineups, countdown, orders, projectiles,
  health, critical damage, destructibles, capture and results stay
  synchronized through the room's mandatory hidden simulation worker.
- The results screen awards battle heroes, historical, special and
  commemorative medals from the client's own achievement thresholds, and both
  the vehicle and the account dossier keep counting them. Medals the client
  itself retired, cancelled before release, or that need data this
  reconstruction does not own, including Mark of Mastery, are listed with
  their reason in `battle_achievements.py` rather than guessed.

This is a reconstruction from the frozen clients and same-era mechanics, not
Wargaming's retail server. LAN play assumes trusted clients. Native rendering,
physics and frame pacing can only be judged in the Windows client.

## Build it yourself

The `Build Windows launcher` GitHub Actions workflow builds the server, client
package and x64 launcher together and publishes the complete ZIP as one
artifact. For a local build, use this order from the repository root:

```bash
# Windows server, with x64 Python 3.11 and the pinned packager
python -m pip install -r server/requirements-windows-build.txt
pwsh -NoProfile -File server/build_windows_server.ps1
# Client package, with CPython 2.7
python2.7 build_wotmod.py
# Windows launcher, after the client package exists
pwsh -NoProfile -File launcher/build_launcher.ps1
```

The launcher carries the matching LAN server and client mod. It writes the
server address into the client configuration, installs the mod, starts the
game and stops the server when the game closes.

Tests:

```bash
python3 -m unittest discover -s tests
cd launcher && python3 -m unittest discover -s tests
```

Project code is distributed under [`GPL-3.0`](LICENSE). World of Tanks and its
assets are not included; this project is not affiliated with or endorsed by
Wargaming. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for lineage
and bundled runtimes.
