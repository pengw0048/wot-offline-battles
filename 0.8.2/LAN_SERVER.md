# LAN battle MVP

This repository contains an optional LAN battle path for the 0.8.2 offline client.
The normal offline mode remains available. The server-backed path also works
with one connected player and reuses the existing garage, map loading, tank
models, HUD and local driving code. A separate Python 3 process owns the shared
roster, health, deaths, battle rules, global bot orders, static navigation-graph
pathfinding and the relay for human/bot movement, firing and client-resolved
armor impacts.

## Start the server

Run this on the machine that hosts the battle:

```bash
python3 lan_battle_server.py --host 0.0.0.0 --port 28782
```

With no `--map` argument, the server chooses the map initially highlighted in
the waiting room. `--map 04_himmelsdorf` changes that initial selection; any
waiting player can still choose another stock map before clicking start.

Allow TCP port `28782` through the host firewall if clients are on another
machine. Use the host machine's LAN address, for example `192.168.1.20`, in
the client configuration.

## Enable the client path

Close the game and refresh each Windows client from this repository:

```bat
refresh_client.bat "C:\Games\World_of_Tanks_0.8.2"
```

This also removes `mod_offhangar.pyc` from the installed mod. The 0.8.2
`CameraNode.pyc` loader scans bytecode before source, so an old copy of that
one file would silently hide all newer LAN code. Do not delete
`scripts/client/CameraNode.pyc`; it is the loader itself.

In the offline hangar, click the visible `LAN SETTINGS` entry in the
upper-right. The in-game panel lets you enter the server IP and TCP port,
toggle LAN mode, and save with `Enter`. `F11` remains available as a keyboard
fallback. The client does not need a separate Python installation: the network
module runs inside the embedded Python 2 runtime shipped with the 0.8.2 client.

## Enter one battle together

1. Start the server and leave its terminal visible.
2. On every client, enable LAN mode with the same server IP and port.
3. Click `Battle!` on every client. The queue screen opens only after the
   server accepts that client and sends `welcome`.
4. Confirm that the server printed a `JOIN` line for every client.
   The queue screen's player count, vehicle classes and tiers come from the
   real server roster and update whenever another client joins or leaves.
5. In the clickable waiting-room panel, choose a map and click `START BATTLE`.
   The server prints `BATTLE START` and broadcasts that map and roster to every
   client currently in the waiting room. A single connected player may also
   start.

The start button appears after the server accepts the client and sends the
`welcome` message.
Join all players before `BATTLE START`. Once the round begins, the canonical
human and bot slots are frozen, so a late connection is rejected instead of
sharing a spawn slot with an existing bot.

There is no independent client-side LAN countdown. A failed LAN connection
does not silently fall back to a local random battle. Use the queue screen's
cancel button to leave the waiting room.

With normal logging settings, each client writes these milestones to
`python.log`:

```text
LAN connecting to 192.168.1.20:28782
LAN TCP connected to 192.168.1.20:28782
LAN hello sent (protocol 8, build 1.8.59-native-experimental-20260815)
LAN welcome id=1 name=Player-158 vehicle=china:Type_59 team=1 slot=0 map=... phase=waiting
LAN JOIN confirmed; queue screen is now server-backed
LAN queue UI updated: 2 connected player(s)
LAN waiting room: 2 player(s); choose a map and click START BATTLE
LAN BATTLE START received: map=... players=2 delay=0.75
LAN bot authority: player_id=1 local=True
LAN bot manifest received: 30 bot(s)
LAN server-baked navigation waypoints active
```

If the server prints no `TCP connection` line, the problem is before the
protocol: verify LAN mode is ON, the configured IP, Parallels network mode and
the server firewall. If it prints a TCP connection followed by `protocol
mismatch` or `client build mismatch`, replace the complete `0.8.2` folder on
every PC and use the server from the same package.

During a battle the server prints one compact bot-AI line every three seconds:

```text
BOT AI reports=t1:2,t2:3 accepted=5 contacts=t1:2/2,t2:3/3 targets=t1:8,t2:9 fire=t1:5,t2:6 modes=engage:17,route:12 nav=baked,cell:4000mm,nodes:16808 nav_total=direct:20,local:4,reactive:3 recovered:9 nav_active=direct:2,local:1,reactive:0 astar=pending:5,oldest:420ms,tick_age:0ms,done:41,failed:2 orders=server:18,client:18,loaded:29,acked:18 aim=targeted:17,aligned:6,traversing:11,limited:7,alive:29 driver=moving:24,drive:20,avoid:4,blocked:0,recovery:1,arrived:4,wait:0 safety=water:2/0,edge:5/1,veto:w0,t1,o0,e0
BOT NAV active=True map=07_lakeville nodes=... plans=... direct=... cache=... complete=... partial=... pending=... failed=0 budget=.../...ms oldest=...ms avg=...ms max=...ms paths=...
```

`reports` is the authority client's current visible-contact count, `contacts`
is visible/remembered state accepted by the server, `targets` is the number of
bots with a combat target, and `fire` is the number currently authorized to
shoot. `nav=baked` is direct proof that the shipped graph is active; `runtime`
means a developer build fell back to live terrain probes because its graph was
missing or rejected. Every stock map in the complete package should report
`baked`. `nav_total` is cumulative while `nav_active`, `aim` and `driver` are
current samples. `tick_age` reveals a stalled path scheduler. A healthy order
pipeline has matching `orders` server/client/acked revisions and `loaded:29`;
`wait` counts bots deliberately holding while an order body is being recovered.
`safety` counts water rollbacks and baked-edge rollbacks as total/current.
`BOT NAV active=True` proves that long static A* paths are being resolved by the
Python 3 server rather than the 0.8.2 render thread. A cold search is advanced
fairly across server ticks under a six-millisecond total budget; `pending` and
`oldest` expose queue pressure without allowing one difficult route to stall
networking. `partial` is safe forward progress that is automatically continued
until the real order destination is reached. `failed=0` is expected for the
shipped 33-map graph set; a non-zero value activates an explicit per-bot client
fallback instead of reusing a stale waypoint.
Each actual client-simulated shot also prints `BOT FIRE`. This separates
spotting, delivery, navigation and client execution without enabling verbose
client debug logging.

The server also prints one process/tick profile every five seconds while a
battle is active:

```text
SERVER PERF cpu_core=2.8% tick=30.0Hz avg=0.92ms p95=1.31ms max=2.40ms overruns=0 late_max=0.00ms stage=move:0.02,plan:0.18,nav:0.61,snapshot:0.01,diag:0.01,dispatch:0.07,events:0.00ms wire=encode:0.03,socket:0.00ms messages=30.0/s data=421.9KiB/s snapshot=base:14400B,orders:0B,attach:0/150 outbound=reliable:0,latest:1,coalesced:0,inflight:0,age_max:0ms,send_max:1ms sent=snapshot:30.0/s/421.9KiB/s
```

`cpu_core` is Python process CPU relative to one core, not whole-machine CPU.
The server has a 33.3 ms budget at 30 Hz. Sustained `p95` below that budget and
`overruns=0` rule out a saturated server tick. Battle socket writes use the
asynchronous sender, so backpressure appears in `outbound` in-flight age and
`send_max` rather than the tick's normally zero `wire.socket` value.
`snapshot` separates the average base body from the compact order increment
and reports how often that increment was attached. `messages/data` count
offered encoded work; `sent` counts only successful sendall completions by
message type, so coalesced snapshots no longer masquerade as wire bandwidth.

Long terrain routes use bounded weighted-A* continuations instead of one large
search per bot. Moving tanks are handled by the local driver and are not baked
into the shared static path cache. Water deeper than 90 cm is a hard navigation
boundary. Water from 12 cm through 90 cm is marked as a high-cost shallow ford,
so a bot uses a required ford but strongly prefers a dry route. Runtime steering,
cover and peek safety use the same deep-water limit.

If a snapshot cannot be delivered, the server prints `SEND DROP` with the
player and socket error before removing that connection. An unexpected tick
exception prints `BATTLE TICK ERROR (server remains running)` and does not kill
the server's battle loop silently. Either line is actionable evidence to include
with the client `python.log` when reporting a frozen ping/lag indicator.

During a battle each client also writes one compact transport summary every
five seconds:

```text
LAN NET window=5.0s chunks=... messages=... snapshots=... bot_updates=... max_socket_gap=... max_snapshot_gap=... max_bot_gap=... max_queue_age=... max_pending=... rtt=...
```

`max_socket_gap` is measured by the socket thread and exposes a real delivery
pause. `max_queue_age` means data had already reached the machine but the game
thread applied it late. `snapshots` is the server delivery rate, while
`bot_updates` is how often the elected authority actually supplied a new bot
pose. These fields distinguish LAN trouble, a delayed server stream, an
overloaded authority client, and a blocked local game thread without enabling
per-packet logging.

In battle, opposing LAN humans use the same local 50 m proximity spot,
view-range/terrain line-of-sight check, allied vision and five-second spot
memory as NPC opponents. Allied humans remain visible. When a human dies, the
server freezes that player's final pose and the client rebinds the marker proxy
to the grounded wreck so late input packets cannot separate the two.

Human input, bot state and server snapshots run at 30 Hz. Between packets the
client advances remote humans and shared bots every render frame with
exponential interpolation and up to 50 ms of bounded velocity prediction.
Corrections larger than 25 metres snap immediately. The stock battle HUD's
ping and connection indicator are fed by the measured LAN round-trip time and
snapshot freshness instead of fixed placeholder values.

Reliable combat events are queued before the same tick's canonical snapshot,
so client-simulated fire, collision or water damage is acknowledged before
that HP can appear in a snapshot. Snapshots and events both carry `round_id`;
the client rejects an older round before applying timing, authority, bot
orders, HP or presentation side effects. Roster contents and recipients are
captured atomically, and every inbound command remains tied to the exact
connection object that owns its numeric player id, so a delayed old handler
cannot mutate or relabel a later round after ids are reused.

If you prefer to prepare a config file manually, use these values:

```json
{
    "network_mode": true,
    "network_server_host": "192.168.1.20",
    "network_server_port": 28782,
    "network_map_name": "server_random"
}
```

The actual file contains the complete existing configuration; merge these
values into it rather than replacing the file. The server supplies the valid
map list; the clicked waiting-room selection is authoritative for the round.

## Current protocol boundary

The server speaks a small newline-delimited JSON protocol, not the original
Wargaming/BigWorld server protocol. Protocol v8 has one waiting room per server
process and a server-authoritative `battle_start` barrier. The server fixes one
30-second combat deadline and continuously includes remaining battle time in
the 30 Hz snapshots. Each client closes the normal loading page on its own and
joins the remaining shared countdown instead of starting a new local timer.
The exact lineup descriptors and collision resources are prepared behind that
loading page; native bot entities are then staged while the countdown is
visible, without holding the client on `Awaiting players`. It synchronizes
player identity, selected vehicle, opposing team, position, hull/turret aim,
shell selection, firing, impact outcome, health and death.
The firing client reuses the existing 0.8.2 map collision, shell and armor
calculation and reports that result; the server validates and owns the shared
HP result. Damage caused by local bots, fire, drowning and collisions is
reported downward by the affected client so other players see the resulting
health and death state. Generic
`Defaultplayer` names become `Player-<IP suffix>` and receive a numeric suffix
if necessary. Remote tanks are rendered through the existing offline
mock-vehicle resource path. Vehicle movement still uses the existing client
physics and is relayed through the server; this trusted-LAN checkpoint is not
anti-cheat authoritative. Local garage data remains client-side.

At battle start the server elects one connected client as map-simulation/rules
authority. That client chooses one exact bot manifest and uploads each tank's
vehicle profile plus the assigned standard-battle route. It reports only
contacts observed through client-side range and terrain line-of-sight checks.
The server retains last-known contacts, reserves targets across the team,
advances uploaded routes, shifts at most one adaptable tank toward a pressured
lane, chooses combat mode and shell, and emits monotonic revisioned bot orders.
Order bodies use an application-level acknowledgement and bounded retransmit;
the client keeps its last executable revision and requests a resync rather than
clearing every bot to an accidental local hold when a busy frame coalesces
snapshots.
For nearby visible contacts the authority also probes a bounded fan of
drivable, dry, low-slope cover and peek points. The server validates those
points against the bot's shared pose, scores them by role/personality, reserves
them across the team, and controls the approach/hold/peek/return cycle. The
server resolves each movement order through the shipped static navigation
graph with resumable, per-tick-budgeted weighted A* and includes a short
canonical waypoint in every snapshot. The authority client
executes that waypoint with the real map collision, local driver, armor and
shell systems, then publishes canonical pose/fire/HP state.
Every client therefore renders the same population and combat result. If the
authority disconnects, the server elects the next player, preserves canonical
bot/HP/rules state, and clears the departed client's short-lived contacts and
cover probes until the new authority reports its own observations. The same
authority publishes
base-capture progress, capture interruption and the final winner/reason for
capture, team elimination or timer expiry. The server remains the shared
source of truth for HP and the final battle result.
The top HUD score is recomputed from the same canonical alive/dead roster after
every death, including shells, fire, collisions and drowning, rather than from
client-local frag side effects.

### Bot planner portability boundary

`server_bot_ai.py`, `server_bot_navigation.py` and the data-only navigation
graph reader shared with the client are intentionally independent of BigWorld.
Their inputs and outputs are JSON-compatible dictionaries carried by protocol
v8:

- `bot_manifest`: identity, team, vehicle profile, shell profiles and sparse
  route waypoints;
- `bot_observation`: authority-reported visible/hidden contacts with explicit
  `target_kind` and shared coordinates, plus bounded client-probed cover and
  peek affordances for visible contacts;
- `bot_state`: the latest authority-executed bot pose, health and fire state;
- `bot_orders`: route index, movement/aim points, target identity, combat mode,
  throttle override and shell index, guarded by `bot_order_revision`. The rich
  planner body remains server-local; only executable fields are projected onto
  the wire, and only the elected authority receives that list when its
  acknowledged revision is stale. Replicas consume canonical bot snapshots.
- snapshot navigation fields: `nav_source`, `nav_order_revision` and a short
  canonical `nav_x/nav_y/nav_z` waypoint resolved from the static graph.

This boundary is suitable for replacing the Python server planner with Go
without moving proprietary map queries or BigWorld entity control off the
client. A Go implementation must preserve non-omniscient contact handling,
stable orders between revisions and target identity as `(target_kind, id)`.

This is an implementation checkpoint, not a complete replacement for the
retail battle server. One elected client still owns map collision, short-range
driving, shell/armor resolution and the original client physics. Authority
failover preserves canonical bot poses, HP, rules and uploaded routes, but
intentionally discards authority-derived contacts/cover probes and cannot
preserve every client-local recovery or reload timer. It does not provide the
retail server's authoritative physics, complete cross-client module/crew
state, reconnection recovery, anti-cheat, NAT traversal or internet-safe
authentication. Keep it on a trusted LAN while testing.

## Disable or roll back

Set `network_mode` back to `false` to return to the original offline path. The
Git baseline before the LAN changes is:

```text
d58ed2e chore: baseline offline battles release
```
