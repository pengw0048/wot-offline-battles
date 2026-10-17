# Battle freeze: fire intents starve behind the state lock, gun deadlocks, ping pegs at 999

## Summary

During a live round the player's gun silently stops working: the trigger
produces no shell and no reload, the ping indicator pegs at 999, and nearby
bots appear frozen. The round never recovers; quitting and relaunching is the
only way out. Logs show this is not a network failure — the TCP streams stayed
up in both directions until the user quit. It is a **server-side scheduling
failure**: the player connection handler thread starves behind the 30 Hz tick
loop on the single `BattleState` lock, so fire intents are never relayed to the
simulation worker, and because a pending fire intent has **no timeout**, one
lost intent deadlocks the gun for the rest of the round.

## Environment

- Mod version 0.7.7 (build github-34681499323-1), WoT 0.9.22.0.1 CN (#1513)
- Session `20260922T150538Z-192f3742a834`, map `47_canada_a`, 1 player + 29 bots
- Logs: `%LOCALAPPDATA%\WoTOfflineBattles\session-logs\<session>\server.log`,
  `offline-player-python.log`, `offline-worker-python.log` in the game root

## Observed symptoms (player side)

- Ping display shows 999 while the connection is actually alive.
- Firing does nothing: no shell, no reload start; later trigger pulls are
  dropped locally with `intent_pending`.
- Enemies reacting to the player's (frozen) server-side pose appear stuck.

## Evidence timeline (round 1, battle live 18:15:01)

| Time | Evidence |
|---|---|
| 18:16:14.859 | Client: `FIRE TRIGGER intent=1 input=2157` |
| 18:16:17.640 | Client: `FIRE INTENT rejected intent=1 reason=trigger_clock_stale` — the server processed the intent **~2.8 s** after the trigger (budget is `PLAYER_TRIGGER_MAX_LAG_MS = 500` ms) |
| 18:16:33.127 | Client: `FIRE TRIGGER intent=2 input=2899` — **no result ever comes back** |
| 18:16:37–51 | Client: repeated `LOCAL FIRE rejected reason=intent_pending reload=0.000`; intents 3 and 4 are also sent and also vanish |
| (whole round) | Worker log: **zero** `FIRE INTENT RECEIVED` lines (healthy rounds show them with `poll_delay_ms=24–345`) — no intent ever reached the worker |
| (whole round) | Server log: zero `SHOT` / `FIRE INTENT rejected` lines between `BATTLE LIVE` and teardown — no intent was accepted or rejected server-side after intent 1 |
| 18:16:56 | Teardown (user quit): `OUTBOUND FAILURE ... errno=10054` on both endpoints, `PLAYER INPUT rejected ... seq=3720 processed=3719` |

Server overload is directly measurable: `server_tick=2986` at teardown vs
~3750 expected for 125 s of round time at `TICK_HZ = 30` — the tick loop ran at
**~42 ms/tick sustained** (24 ms over budget), i.e. permanently in catch-up
mode. The hidden worker was saturated too (`bots_update` 150–300 ms per frame,
control catch-up debt 590/623 steps on this map).

## Root cause analysis

1. **Single contended lock.** The 30 Hz tick loop (`tick_once`,
   `server/lan_battle_server.py:13333`) holds `BattleState.lock` for the whole
   tick, including building ~110 KB JSON snapshots per endpoint. The per-player
   handler thread (`ClientHandler.handle`) needs the same lock for
   `update_input` and `submit_fire_intent`. The catch-up loop in
   `_run_tick_loop` (line ~14959) runs all due ticks back-to-back with no
   yield, so when the server falls behind, the lock is held ~100 % of the time
   and the handler thread starves for seconds at a time.
2. **500 ms trigger clock budget.** `submit_fire_intent` rejects any intent
   whose frozen trigger clock is older than `PLAYER_TRIGGER_MAX_LAG_MS`
   (`lan_battle_server.py:7499`). A multi-second lock wait turns a legal shot
   into `trigger_clock_stale` (intent 1) or strands it entirely (intent 2:
   handler blocked until teardown, never relayed, never rejected).
3. **No timeout on pending intents — the deadlock amplifier.**
   `PLAYER_FIRE_INTENT_MAX_PENDING = 1`, and `pending_fire_intents` entries are
   only cleared by a worker resolution or round teardown
   (`lan_battle_server.py:7427/7731/8112`). Once intent 2 pended forever, every
   later trigger was dropped client-side (`battle_runtime.py:24967`), so the
   gun never fired and never reloaded for the rest of the round.
4. **Ping 999 is a symptom, not the cause.** The displayed value is the
   `rtt_ms` EMA clamped at 999 (`compat.py:2489`). Pongs are answered on the
   same starved handler thread (`player.send(pong)` even blocks up to
   `OUTBOUND_SYNC_TIMEOUT_SECONDS = 2.0` s waiting for the outbox write), so
   lock starvation inflates the EMA past 999.

## Related failures observed the same day (same overload family)

- 14:24 and 16:15 (`31_airfield`, `101_dday`):
  `OUTBOUND FAILURE reason="message_too_large"` — a **262–283 KB** message at
  the exact battle-end second exceeds `MAX_LINE_BYTES = 256 * 1024`
  (`_serialize_message`, `lan_battle_server.py:1760`) and is dropped. Looks
  like the battle-result/final payload overflows the framing limit.
- 17:02 (`35_steppes`): `WORKER TIMEOUT ... idle=5.01s` →
  `WORKER FAILURE ... round terminated` — worker silence kills the round.
- 13:19 and 15:58: same signature as this report (mid-battle freeze, user
  quit, `errno=10054` on the next snapshot send).

## Proposed fixes (priority order)

1. **Server-side timeout for pending fire intents.** If the worker has not
   resolved an intent within ~1–2 s, commit a terminal rejection
   (e.g. `worker_timeout`) so the client always gets a result and the gun
   recovers. Add a matching client-side expiry for a stale local pending
   intent. This converts a round-killing deadlock into one lost shot.
2. **Eliminate lock starvation.**
   - In `_run_tick_loop`'s catch-up path, yield between consumed due ticks
     (`time.sleep(0)` or cap consecutive catch-up ticks), so handler threads
     always get the lock within a bounded time.
   - Move snapshot serialization out of the lock: freeze state under the lock,
     `json.dumps` outside it, then only enqueue under the lock. Both endpoints
     currently serialize nearly identical ~110 KB snapshots independently at
     30 Hz — serialize once and share the payload.
3. **Slim or slow the snapshot stream** (delta encoding, or 15–20 Hz) to bring
   the 42 ms/tick average back under the 33 ms budget.
4. **Fix the oversized battle-end message** (> 256 KB): find what accumulates
   (statistics/event payload), split it across frames or raise/scope the limit
   so the result always delivers.
5. **Relieve the worker** on heavy maps: spread bot update work across frames
   (its `bots_update` hit 300 ms/frame here); worker slowness feeds back into
   server load via bot-state ingestion under the same lock.

## Diagnostics worth adding

The server logged nothing for the whole 2-minute incident. Suggested
instrumentation:

- Tick duration percentiles and catch-up debt (already exists worker-side;
  mirror it server-side).
- Lock-wait time on the player handler path; warn past ~100 ms.
- Fire intent age: log (and reject) any intent still pending after ~1 s.
- Periodic outbox depth per endpoint (`reliable_messages`,
  `reliable_bytes`, pending snapshot size) so starvation/backlog is visible
  before teardown.
