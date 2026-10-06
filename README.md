# wot-0.9.22-offline-battles

Play standard battles with bots in the Chinese HD Windows client
`0.9.22.0.1 #1513`, alone or with friends on a LAN.

You supply your own client. The client still provides the maps, vehicles,
rendering, HUD and physics. This repository provides the client mod, the bot
and battle logic, a small LAN server and a launcher.

Current release: **v0.9.7** — [Release notes](docs/releases/v0.9.7.md).

## Play

1. Download `wot-0.9.22-offline-battles-0.9.7-Windows-x64.zip` from the releases,
   unpack it, and start `wot-0.9.22-offline-battles.exe`.
2. Select your World of Tanks folder. The launcher recognizes the client,
   removes any older mod files and installs the matching mod.
3. Select a mode:
   - **Single player**: you play alone against bots. The launcher runs the
     server for you; every battle uses the same LAN authority path.
   - **Host a LAN battle**: other players join this PC. The launcher starts the
     server and prints the address to give them.
   - **Join a LAN battle**: type the host's address, for example
     `192.168.1.20`.
4. Click **Start game**. In the garage, fit a tank and click **Battle!** or
   **Create Platoon**.
   Everyone lands in the LAN waiting room over the stock queue screen. The
   host picks the map - **RANDOM MAP**, or **MAP** to browse the client's own
   map window and choose a battle time - and clicks **START BATTLE**.
   **LEAVE** returns you to the garage.

Create Platoon opens that same LAN room; the launcher selects the server.
It does not create a separate retail platoon or use online friend invitations.
Have the host enter first, then join the same address and select the same team
to play together. Starting Single player on each PC creates separate rooms.

The launcher also starts a hidden client on each player's PC to drive that
player's tank. It handles body movement and physical contacts independently
of the room's Bot worker. The visible client keeps aiming, firing and drawing
the battle. Both hidden clients load the game, so hosting a room uses three
game processes and requires additional memory. Closing the visible game also
closes its paired driver. If that driver fails, movement stops with a local
message; restart the game from the launcher to recover.

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
  and crew skills. Vehicle equipment uses the catalogue's currency and price;
  customization remains free.
- The garage is written to the selected save after each change, so it survives
  a restart.
- The battle runs the vehicle the garage fitted. Crew skills, optional devices
  and consumables move the same values the garage parameters panel shows: view
  range, concealment, reload, aim time, dispersion, traverse, engine power,
  terrain resistance and repair speed.

Automatic teams share a tier/class template but draw vehicle models
independently from the usable catalogue. The existing model blacklist and
host exclusions still apply. A host's explicit lineup overrides stay explicit.

The [v0.9.7 release notes](docs/releases/v0.9.7.md) cover the follow-up
since v0.9.6: crew hit feedback, steering and ram contacts, lighter Bot ground
support, bridge departures, visibility and audio fixes, crew service records,
offline replays, large LAN state transfers and linked game/session shutdown.
Recordings are stored in `replays/offline` in the game folder; open them from
the launcher's Replay tab. Playback currently supports forward 1x and a manual
camera. Keep the original vehicle profile and use recordings from this version.
The unresolved internal-module inventory attached to the v0.9.6 release still
describes outstanding geometry work; this release does not add guessed layouts.

Grand Battles (30 versus 30) remain unavailable. The 0.9.22 mode requires
Tier X vehicles, a 15-minute battle, up to four SPGs per team, three matched
spawn groups and the large Grand Battle maps. The current package has neither
the validated navigation/spawn data for those maps nor a 60-vehicle protocol
and UI path. Raising the player limit alone would not make this mode playable.

The Training selector opens a LAN training room. The host chooses teams, map,
duration and whether to fill empty slots with Bots, then starts explicitly.
Training awards no credits, XP, bonds, medals or mission progress. Repairs are
free; ammunition and consumables still use the normal inventory and resupply.

Armory Special Offers now lists bond vehicles with a garage slot and 100%
crew. The permanent bond vehicle shop began in 2019, after 0.9.22; this offline
extension uses the first official assortment intersected with client assets.
The five retired definitions have explicit offline prices: tier VII
Aufkl. Panther 6000, tier IX SU-122-54 12000, and tier X Object 430B,
Object 263B and Waffentrager E 100 15000 bonds each. These are the old
definitions, not their later replacements or alternate variants.
Special Offers uses its own bond quotes. The tech tree retains the native
catalogue gold values, including 32000 for 121B and 9000 for Panzer 58 Mutz;
catalogue availability flags still apply. A bond purchase includes the slot
and trained crew; an ordinary catalogue purchase uses its displayed currency
and normal slot/crew terms.
The regular Shop excludes those bond offers. Vehicle checkboxes select
unresearched, owned or rented vehicles; with none selected, the list contains
available purchases. Multiple selected categories are combined.
Special Offers shows only the tank section, class/tier filters and owned/rental
checkboxes. Its filters are saved independently from the regular Shop. The
regular Shop's special-offers selector opens the complete bond catalogue with
those filters cleared; the redundant selector is hidden inside Special Offers.
Shop and Inventory complete missing category filters from the client's saved
selection and native defaults, including when returning from vehicle recovery.
Reopening repairs incomplete saved filters; a failed table update releases its
loading overlay so the page can be used again.

Premium vehicles sold from this build onward enter the native Shop recovery
list. Recovery costs the hull's sale value plus 10% in credits and requires a
free garage slot. Premiums offered in the regular shop can be recovered for
72 hours; premiums outside its assortment have no deadline. Earlier sales
cannot be reconstructed from saves that never recorded them. Badge selection
now requires the native earned achievement; old unrestricted cosmetic
selections are cleared. Personal campaigns settle supported battle conditions
and their corresponding rewards as described below.

Personal Reserves has purchase, activation, Close and Escape actions. It offers
44 distinct historical bonus/duration combinations across combat XP, crew XP,
free XP and credits, including small/medium/large bonuses and 1/2/4/6-hour
timers. These come from a preserved April 2016 WG API catalogue; completeness
against later China-only 0.9.22 event offers is not established. Native client
quality filters and artwork classify the bonuses. The original four one-hour
offers and save identities are preserved. New gold prices scale proportionally
from those offline offers, not from a claimed historical retail price list.
Up to three different resource types can run together; a stronger reserve can
replace its type after confirmation. Timers continue offline and eligibility
is fixed at battle start. Both native reserve surfaces expose all three slots.
The Missions tab selects three daily offline goals for completed standard
battles, damage and victories. Returning to the garage after destruction still
qualifies when the round settles. A voluntary exit that triggers the native
abandonment warning cannot advance these goals or consume daily x2. Surviving
and watching the battle to its end are separate facts.
Each goal grants one reserve automatically; goals reset at 00:00 UTC and are
selected from a fixed reward pool. These prices and daily goals are custom
offline rules. Purchases, timers, rewards and receipt deduplication persist
with the selected save.

The preserved API names label the included bonus strengths as follows; this
is a catalogue of named variants, not a claim about every later event offer or
the exact Chinese client's continuous quality thresholds. Duration is a
separate property.

| Resource | Small | Medium | Large |
| --- | --- | --- | --- |
| Combat XP | 5% | 10% | 15%, 25%, 50%, 100% |
| Crew XP | 25% | 50% | 75%, 100%, 200%, 300% |
| Free XP | 20%, 50% | 75% | 100%, 200%, 300% |
| Credits | 5% | 10% | 15%, 25%, 50% |

The [2017 official guide](https://wargaming.net/support/en/products/wot/article/18943/)
lists the four resource types. The [2022 redesign](https://worldoftanks.com/en/news/updates/1-18-1-improved-personal-reserves/)
merged crew/free XP into the modern three-type system, which is not used here.

The launcher's **Customize save** window keeps the v0.8.4 **Garage vehicles**
list and simple vehicle/tier labels, including the five supported retired
vehicles (Aufkl. Panther, Waffentrager E 100, SU-122-54, Object 263B and
Object 430B). Its **Personal missions** editor covers
all 300 regular campaign missions (StuG IV, T28 Concept, T 55A and Object 260).
Each mission can be incomplete, completed, or completed with honors. Honors
also checks completion; clearing completion also clears honors. Changes stay
in the editor when switching operations and vehicle classes; **Save** applies
them to the selected save. Required earlier missions are completed automatically
without honors. Clearing a main completion also clears that chain's final and
every mission in all five classes of every later operation. Clearing honors
alone changes no other mission or operation, including order-skipped finals.
Edits are settled when the game next opens the garage. Resetting main completion
withdraws the corresponding main and honors rewards, including dependent
operation rewards; resetting honors withdraws only its additional rewards.
An eligible reset mission replaces the previous selection of its class.
The durable reward journal records the actual payout, so a successful withdrawal
allows that reward to be earned again without duplicating property. A failed
withdrawal preserves the original progress and property and shows its reason
in the mission editor and system messages. Currency, consumables and other
quantity rewards are reclaimed only up to the remaining balance or depot
stock; spending them never blocks a reset or makes the count negative.
Mounted items, occupied slots/bunks and orders pledged to other missions are
retained. Unknown reward provenance or insufficient room for crew returned
from a reward tank can still prevent a complete reset. A reward woman
permanently removed from the recovery list no longer blocks her mission reset;
the claim is cleared without removing another crew member.
Elapsed premium time cannot be undone; only the remaining earned interval is
withdrawn. Daily missions remain separate.

Before each operation tank grant, the garage is checked for the same vehicle.
An already owned vehicle receives its **full original vehicle value in credits**,
using the catalogue's credit price plus any gold price at the account exchange
rate, without the selling discount or custom bond-shop price. This compensation
is an explicit offline policy, not a verified historical KongZhong rule.
Cancellation reverses the recorded compensation, leaving the pre-existing tank
alone. A tank actually granted by the mission is withdrawn; its crew and fitted
items return to the barracks/depot. A saved vehicle source marker prevents a
later purchased replacement from being mistaken for the original reward.
Old operation claims without vehicle provenance cannot establish whether a
tank or compensation was originally paid; those resets are refused rather
than taking a purchased vehicle or leaving an unknown cash payout behind.

Personal-mission account badges follow the current completion and honors
requirements. Cancelling those requirements also removes the corresponding
badge and its equipped selection. The original battle TAB receives the active
mission for the current tank class and tier. Battle results carry personal
mission progress in the native lower-left quest area, while system messages
list actual rewards, vehicle compensation and withdrawals. Launcher changes
queue these messages for the next garage load and retain undelivered notices.
Manual vehicle additions, badge ownership edits and wallet changes also queue
native system messages with the actual vehicles, crew, badges and amounts.
Repeated unchanged saves do not produce new notices. Vehicle construction,
its notice and its inbox acknowledgement commit only after the garage saves.

Dismissed crew recovery holds at most 100 members, keeping the newest. The
offline policy charges 100 gold immediately and expires seven days after
dismissal. At that deadline or when pushed out by newer entries,
the member is permanently dismissed. These server-supplied durations are
offline policy, not a verified historical Chinese server configuration.

The account panel also edits account badge ownership.
Badge choices and translated names come from the installed client's
catalogue. Removing an equipped badge clears its selection. Close the game
before saving these edits; new saves use the edited values on first startup.
There is no separate order-quantity editor. Orders come from the corresponding
mission honors rewards, whether earned in battle or checked in the mission
editor. The available balance is rebuilt from unique honored-final reward
claims minus orders assigned to missions. Unsupported quantities from the old
manual input are removed; repeating completion or refunding a pledge cannot
create a new entitlement. The four regular operations can earn 20 orders in
total (five honored finals per operation), including orders currently assigned.
Old pledges remain recorded even when they exceed legitimate earnings, with
no free orders available until the shortfall is covered or the pledges reset.
Spending orders records the amount assigned to each mission;
honors completion returns those orders. Resetting that mission also returns
its assigned orders once, independently of withdrawing earned honor orders.

Premium purchases use the original six durations: 360, 180, 30, 7, 3 and 1 day,
with the installed client's own icons and labels. Battle-result friendly-fire
labels remain the installed client's own localization: credits have penalty and compensation rows; XP has only a
penalty. Offline voice chat has no authenticated Vivox service and remains
unavailable; opening sound settings no longer retries that service.
Standard and Commander voice settings apply immediately in battle, including
after preview cancellation. The attached vehicle's native refresh selects
the language and special crew voices; commander gender remains preserved.

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
10 gold in either a career or a fully unlocked save. Improved equipment
uses its separate 200-bond removal price.

The fourth equipment slot accepts the fifteen 0.9.22 directives. Buy them
with bonds, mount one for a battle, and enable their separate auto-resupply
switch if wanted. A used directive is consumed once per battle, including
when its perk was never triggered. The six improved optional devices also
cost bonds (3000-5000), rather than credits. Directives cost 2-12 bonds and
cannot be sold; improved equipment resale yields credits, not bonds.

Epic medals and Battle Hero achievements award bonds according to the
[9.20.1 schedule retained in 0.9.22](https://worldoftanks.eu/en/news/general-news/920-1-bonds-and-medals/).
Awards depend on vehicle tier and appear individually in the battle results.
They persist with the save and receive its offline earnings multiplier.
Premium bonuses do not multiply bonds. Directive purchases and automatic
resupply still charge their normal prices. The wallet, battle-result medal
rows and lifetime totals use the same rounded award, applied only once even
after a retry or restart.
The launcher's Saves tab can also edit the bond balance. Older saves start
with zero bonds. The historical additional base-XP bond payout for all-Tier-X
battles is not implemented: its exact conversion table is still unavailable.
Ranked-season and clan-event rewards are outside the offline modes.

Ammunition and consumables are stock now, not scenery. A battle spends the
rounds it fired and one of each consumable it used, the server reports both,
and reloading buys whatever the depot is short of at this client's prices. An
account that cannot pay for a full load does not get one.

A battle leaves the vehicle as damaged as it ended: the client's own
inventory carries the outstanding repair cost beside the remaining health, so
the garage shows the tank destroyed or damaged and the maintenance panel
offers the repair. The bill comes out of the client's own repair formula --
one health point costs what that vehicle charges per point -- and it has to be
paid before the tank can fight again. A save keeps the damage across a
restart, because a restart is not a free repair.

Friendly fire counts only applied HP loss and earns no damage or kill reward.
It reduces battle XP, and charges the victim's hull repair cost plus a 10%
credit fine before automatic maintenance. Victims receive their repair
compensation independently of the offender's funds. These charges and
compensation do not receive the save earnings multiplier. A result can show
negative credit income when the charge uses the existing garage balance;
retries and restarts preserve the original settlement. The published
[team-damage guide](https://wotgame.cn/zh-cn/content/guide/general/teamkill/)
does not disclose the XP coefficient, so this port reverses its existing
offline damage/kill XP valuation. It does not claim exact retail XP penalties.

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
- Automatically generated lineups allow up to three self-propelled artillery
  per team, counting human artillery toward that quota. The available vehicle
  pool and roster size determine whether artillery is selected. Manually
  assigned Bot lineups retain the host's choices; tank destroyers do not count
  toward the artillery quota.
- Bot competence is a spectrum, not a tier. Each Bot gets one rating that
  sets the crew level its vehicle is trained to, how long that gunner takes
  to react, how patiently it waits for the aiming circle, how far off centre
  it lays the gun and how badly it leads a moving target. The same rating is
  the chance it does the tactically right thing each time the situation
  comes up: reaching for cover, peeking out of it, angling its hull, picking
  the round that beats the armour in front of it, aiming at the part it can
  actually hurt, turning on whoever just hit it, breaking off when it should,
  avoiding a crossfire and going around a flank. A weak Bot is not a Bot that
  does everything slightly worse; it is one that does not think of some of
  it. The waiting room chooses how competence is spread over the whole roster
  - easy, relaxed, pub mix, hard or brutal - and the launcher's exact Bot
  lineup can still pin one slot to a named tier, with or without also pinning
  its vehicle.
- Live combat statistics, a damage log with assists, hit and critical-damage
  messages, target outlines, vehicle fires, wrecks, and a consumables panel
  that counts down each cooldown.
- A LAN match is one shared battle. Each player simulates their own shells;
  the mandatory hidden worker simulates Bots and their shells. The server
  commits shared health, critical damage and destruction once, and keeps the
  lineups, countdown, orders, capture and results synchronized.
- The results screen awards battle heroes, historical, special and
  commemorative medals from the client's own achievement thresholds, and both
  the vehicle and the account dossier keep counting them. Medals the client
  itself retired, cancelled before release, or that need data this
  reconstruction does not own are listed with their reason in
  `battle_achievements.py` rather than guessed.
- The small battle-result commendations also settle into their native records:
  Shellproof, Fire for Effect, Fighter, Duelist, Demolition Expert, Arsonist,
  Bruiser, Hand of God, Eye for an Eye, Spotter and Battle Buddy. Battle Buddy
  counts 50 consecutive battles without friendly HP or module damage across
  vehicles; Spotter keeps each vehicle's best qualifying assist result.
- Completed offline daily missions use the native completed tick and reward
  block. Both Standard (localized Chinese) and Commander national voices use
  the mounted commander's gender, including after changing voice settings.
- Battle payments follow the published structure: Credits are a base amount
  per vehicle tier that alone carries the 1.85 victory multiplier, a
  tier-independent amount per point of damage, double for detecting artillery,
  and one capture payment split between the vehicles that completed it; XP
  adds 50 percent on a win and returns five percent as Free XP. Damage XP
  uses an offline tier curve derived from the captured tables, and kill XP
  uses victim durability. These are balance approximations, not recovered
  retail formulas: percentile ratios do not identify XP coefficients, and
  durability does not uniquely determine tier.
- A premium vehicle's own XP bonus and crew-training rate come from the
  client's own `premiumVehicleXPFactor` and `crewXpFactor`. The XP bonus is
  banked and shown in the results breakdown, but it never enters the number
  the mastery badge ranks, which stays the bare battle XP.
- Mastery badges and Marks of Excellence use the real bar. Wargaming computes
  both from the live player population, so `tools/bake_mastery_thresholds_0922.py`
  captures the published retail tables into `mastery_catalog.py`: base XP for
  each mastery class, and average combined damage by percentile for the gun
  marks. A battle's own base XP decides the class the results screen shows,
  and the vehicle's own average combined damage decides its marks. That
  average is retail's: an exponential moving average over 100 battles that
  starts from zero on a fresh vehicle, so marks take most of a hundred good
  battles rather than one.

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
# Reproduce the verified recorder resource, then build with CPython 2.7
python tools/build_replay_runtime.py
python2.7 build_wotmod.py
# Windows launcher, after the client package exists
pwsh -NoProfile -File launcher/build_launcher.ps1
```

The launcher carries the matching LAN server and client mod. It writes the
server address into the client configuration, installs the mod, starts the
game and stops the server when the game closes.

### Rebuilding navigation from the original client

The navigation baker reads placed BSMI/BSMO colliders and resolves each BSP
triangle's material ID through its model's compiled remap. Original
destructible surfaces (materials 71 through 85) are omitted before raster,
clearance, node, link, connected-component and route generation. Hard faces
inside mixed models, replacement surfaces and vehicle-only bridge decks stay
in the collision input. Birth-overlap checks are separate from route costs.
Missing collision resources or ambiguous material mappings abort the bake;
there is no render-mesh fallback and no runtime hole repair in this tool.

The tracked graphs are **not regenerated by changing the baker's source**.
A clean rebake requires the original Chinese HD 0.9.22.0.1 #1513 client,
including its map, script, shared-model and vehicle resource packages. Do not
mark old graph JSON as newly baked or install a partial batch. With Python
3.11, first inspect that client, then write to a separate output directory:

```bash
python3 tools/inspect_client.py "$WOT_0922_CLIENT"
# Diagnose one map without replacing the shipped catalog.
python3 tools/bake_navigation_0922.py --client "$WOT_0922_CLIENT" --map 31_airfield --bake --output /tmp/airfield-clean.json
# Rebuild the complete map set and its manifest before packaging.
python3 tools/bake_all_navigation_0922.py --client "$WOT_0922_CLIENT" --output-dir /tmp/navgraphs-clean --jobs 1
```

Review the generated artifact differences and retain the existing terrain,
water, bridge, route and spawn validations. The batch publishes only after
every map passes. These source changes alone do not disable the live repair
logic in separately distributed test builds; deployment of regenerated data
and removal of that runtime dependency remain a distinct integration step.

Tests:

```bash
python3 -m unittest discover -s tests
cd launcher && python3 -m unittest discover -s tests
```

Project code is distributed under [`GPL-3.0`](LICENSE). World of Tanks and its
assets are not included; this project is not affiliated with or endorsed by
Wargaming. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for lineage
and bundled runtimes.

### Static navigation review removal test

This test branch removes broad native static-edge review and its live missing-cell/link repair machinery for every map. The rebuilt Airfield graph is retained; the other 40 graphs are unchanged and are not represented as newly baked. Local displaced-hull connector checks, moving-vehicle avoidance, wreck costs, per-Bot contact recovery and final physical collision remain. Native gameplay and performance require testing on the exact client.
