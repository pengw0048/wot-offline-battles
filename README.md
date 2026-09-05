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
   host picks the map and clicks **START BATTLE**. **LEAVE** returns you to
   the garage.

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
- The garage is written to the selected save after each change, so it survives
  a restart.
- The battle runs the vehicle the garage fitted. Crew skills, optional devices
  and consumables move the same values the garage parameters panel shows: view
  range, concealment, reload, aim time, dispersion, traverse, engine power,
  terrain resistance and repair speed.

## Saves

The launcher's Saves tab keeps any number of independent saves. Each one owns
its own garage, crew, account settings and battle results under
`%APPDATA%/Wargaming.net/WorldOfTanks/offline_lan_0922/saves/<save>/`, and the
selected save is written into the client configuration when the game starts.
State written by a build without saves moves into the default save on the next
start.

A new save is created as one of two accounts, and the choice is permanent. A
fully unlocked save is the historical garage: every vehicle, module and
consumable is already owned. A new account starts the way a real World of Tanks
account does, with the tier 1 starter tanks, 100000 credits, 30 garage slots
and nothing else researched; credits and experience are earned in battle, and
vehicles, modules, ammunition and consumables are researched and bought in the
garage at this client's own prices. Gold is spent but not earned, so premium
vehicles and gold ammunition are paid for out of the gold the launcher grants.

Taking a complex optional device off a vehicle follows the client's own rule:
its descriptor says whether the device survives being removed, and one that
does not is destroyed unless the player pays the game's own removal price --
10 gold in a career, nothing in a fully unlocked save.

Ammunition is stock now, not scenery. A battle spends the rounds it fired,
the server reports what it drew by the shell's own position in the gun, and
reloading buys whatever the depot is short of at this client's prices. An
account that cannot pay for a full load does not get one.

A battle leaves the vehicle as damaged as it ended: the client's own
inventory carries the outstanding repair cost beside the remaining health, so
the garage shows the tank destroyed or damaged and the maintenance panel
offers the repair. The bill comes out of the client's own repair formula --
one health point costs what that vehicle charges per point -- and it has to be
paid before the tank can fight again. A save keeps the damage across a
restart, because a restart is not a free repair.

Crew members are recruited from the same three schools the game offers, at
50%, 75% or 100% of their role: free, 20000 credits and 200 gold in a career,
and free in a fully unlocked save. A recruit goes to the barracks or straight
into a seat.

The barracks holds the crew members no vehicle is carrying. Selling a vehicle
can send its crew there instead of dismissing them, a seat can be unloaded and
filled again, and a crew member can move straight from one tank to another;
whoever leaves a seat needs a free berth, which is the same check the game's
own dialogs make before they offer the choice. A vehicle remembers the crew that
left it, so the game's own "return crew" button puts them back where they
were -- until the game is restarted, because the inventory ids a return works
by are rebuilt from the save rather than stored in it. A crew member can only
take a seat in the vehicle they were trained for, and retraining -- at the same three
schools, one crew member or a whole crew at a time -- is how they change
vehicle. The client works out the role level they keep, so the loss is the
game's own.

The launcher's Account tab shows the selected save's credits, gold and free
experience and lets a player set them. Gold is the one currency an offline
account can never earn -- there is no store to buy it from and no battle that
pays it -- so this is where a save gets it. A save has no balances until the
game has started it once, and the game must be closed while they are changed,
because the client owns the same file.

The Shop tab sells every vehicle this client prices in gold: 196 of them, and
145 are reward or event tanks the game's own shop never sold and that no tech
tree leads to. A purchase takes the gold out of the selected save and leaves
the vehicle waiting; the next time the game starts that save, the client builds
it into the garage, stock and with a crew, exactly as a shop purchase arrives.
A vehicle this client cannot build stays waiting and says why in the client
log, so a purchase is never silently lost.

Only the client can name a saved vehicle, so a save written before this
version says nothing about what it owns. The shop refuses to sell to such a
save until the game has started it once; otherwise it would charge gold for a
vehicle the save already has.

Vehicle data profiles are deliberately not part of a save: they change
the client catalogue for a whole room, so they belong to the installation.

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
- Automatically generated Bot lineups contain no self-propelled artillery.
  Player vehicles and manually assigned Bot lineups remain unrestricted;
  tank destroyers are not artillery and remain in the automatic pool.
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
