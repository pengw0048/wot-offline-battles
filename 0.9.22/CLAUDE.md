# Claude guide for the pinned 0.9.22 client

This guide captures the reverse-engineering and collaboration method for the
top-level `0.9.22/` port. It supplements, but does not replace, the release and
compatibility documents linked from the root `CLAUDE.md`.

## Fixed target and interpreter boundary

The only supported client is Chinese HD `0.9.22.0.1 #1513`, 32-bit x86. Use an
environment variable rather than embedding a developer's local path:

```bash
export WOT_0922_CLIENT=/path/to/World_of_Tanks_0.09.22.00.01_CH_1513_HD
export PYTHONDONTWRITEBYTECODE=1
python3 0.9.22/tools/inspect_client.py "$WOT_0922_CLIENT"
```

The game embeds CPython 2.7.7. We use CPython 2.7.18 as the compatible audit
and build interpreter because it reads the same Python 2.7 PYC format and is
the pinned local toolchain. Do not describe 2.7.18 as the embedded game
runtime.

On Peng's current Mac, resolve the compatible interpreter through pyenv:

```bash
PY27="$(PYENV_VERSION=2.7.18 pyenv which python2.7)"
"$PY27" --version
```

If local pyenv is unavailable, `build_for_client.sh` has a pinned Docker
fallback. Never use Python 3 `marshal` or Python 3 opcode assumptions to
interpret a Python 2.7 code object.

## Reverse-engineering workflow

### 1. Pin identity before inspecting behavior

Run `inspect_client.py` first. It checks the client version/build, x86 PE
machine, mod paths, representative Python 2.7 PYC magic, exact entity
definitions, required assets, and the reviewed native vehicle-filter method
table. Public 0.9.22 sources and a differently numbered regional build are
leads only; they are not contract evidence for #1513.

Record the exact archive/member or executable used for every conclusion. If a
conclusion depends on a binary offset, it is version-locked even when the
symbol name looks familiar.

### 2. Read PYC code objects with Python 2.7

The exact Python client modules are ZIP members of
`res/packages/scripts.pkg`. Python 2.7 PYC files in this client have an
eight-byte header; the code object begins at byte 8. A small read-only probe is
often faster and safer than trying to reconstruct an entire module:

```bash
"$PY27" - "$WOT_0922_CLIENT" __onArenaPeriodChange <<'PY'
from __future__ import print_function
import dis
import marshal
import sys
import types
import zipfile

client, target = sys.argv[1:3]
member = 'scripts/client/Avatar.pyc'  # change to the exact target
package = client + '/res/packages/scripts.pkg'

matches = []
def find(code, path=()):
    here = path + (code.co_name,)
    if code.co_name == target:
        matches.append(('.'.join(here), code))
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            find(value, here)

with zipfile.ZipFile(package, 'r') as archive:
    payload = archive.read(member)
if payload[:4] != '\x03\xf3\r\n':
    raise SystemExit('not CPython 2.7 PYC: %s' % member)
find(marshal.loads(payload[8:]))
if not matches:
    raise SystemExit('code object not found: %s' % target)
for name, code in matches:
    print('\n=== %s args=%r names=%r ===' %
          (name, code.co_varnames[:code.co_argcount], code.co_names))
    dis.dis(code)
PY
```

Usually start narrower than a full disassembly:

- inspect `co_names` and string/number values in `co_consts`;
- recursively locate the relevant nested code object;
- then disassemble only that function and its immediate callers;
- inspect both the producer and every direct consumer;
- verify control-flow guards and cleanup order, not just name presence.

`0.9.22/tools/audit_client_abi.py`, `audit_lobby_consumers.py`, and
`audit_client_lifecycle.py` already contain reusable Python 2.7 code-object
walkers. Extend those gates when a new runtime dependency becomes production
law instead of leaving the evidence in a one-off terminal transcript.

A decompiler can help with orientation, but its reconstructed source is not
the contract. Confirm argument flags, call widths, unpack widths, branches,
private attribute access, and exception paths from the code object and exact
call sites.

### 3. Prove data is consumed, not merely present

Packed XML fields can exist without the exact client reader ever assigning
them. Before treating a vehicle or arena field as runtime truth:

1. decode the exact Packed XML member;
2. find the exact reader code object;
3. prove the reader assigns or forwards that field;
4. follow the value to its runtime consumer.

This matters for fields such as old vehicle-physics values that remain in XML
but are initialized to a different value by the #1513 Python reader. Presence
in a resource is evidence of content, not evidence of runtime use.

Use `read_packed_xml()` from `0.9.22/tools/packed_xml.py` as a library parser
for packed script resources. Do not run that file's historical CLI as a
read-only inspector: its main program is an old navigation-probe writer. For
compiled map data, `space_bin_0922.py` is a deliberately limited fail-closed
metadata/safety reader; the complete current decoder is exercised through the
navigation, foliage, and destructible bakers and their vendored versioned
sections. Do not silently reuse an older BWT2 or public-client parser because
the section name is the same.

Run bakers against an explicit temporary output first and diff the result.
Some single-map defaults write into tracked catalog directories. Point a baker
at `0.9.22/navgraphs`, `0.9.22/foliage`, or `0.9.22/destructibles` only when
the task actually authorizes a complete resource update.

### 4. Treat native symbols as a limited surface

PE strings and the method-table probe in `inspect_client.py` can prove that a
Python-exposed native name exists in the exact executable. They do not prove
its units, ownership, callback timing, memory safety, or internal C++ law.

Static Python inspection can prove, for example, that a wrapper calls a native
method after a particular guard. Only the exact Windows runtime can prove the
method works with live BigWorld objects. If the behavior crashes natively,
collect a dump; do not keep changing Python based on a guessed C++ cause.

## How to establish an interface contract

Do not stop after finding a method definition. For every new BigWorld or stock
client dependency, write down and verify:

- exact archive member, class, and method/property name;
- positional arguments, defaults, `*args`/`**kwargs`, and call width;
- return type, tuple/list width, sentinel values, and raised exceptions;
- the producer's field schema and every direct consumer's assumptions;
- private state and guards checked before the operation;
- entity, arena, space, GUI-period, and resource-readiness prerequisites;
- synchronous work versus scheduled callback or coroutine completion;
- ownership: which object may write, replay, cancel, or destroy the state;
- idempotence and behavior when called twice or after teardown;
- failure behavior and whether a fallback is physically safe;
- install/uninstall order and late-callback invalidation.

Then encode the stable parts in the appropriate ABI/lifecycle audit and a
focused producer-consumer test. Entity `.def` flags, mailbox arity, dictionary
keys, and tuple widths are separate contracts; a Python function signature
does not prove them. In particular, a BigWorld `STRING` field may require a
Python 2 byte string rather than an arbitrary `unicode` value. Python private
names are mangled as `_ClassName__name`; verify the real declaring class before
reading or patching one.

Test fakes must reproduce the native guards relevant to the bug. A fake whose
`start()` always succeeds can make an initialization-order bug look green when
the real object rejects the call until another field is initialized.

## BigWorld lifecycle and ownership rules

- Lifecycle ordering is part of the ABI. Account, Lobby, space loading,
  entity `onEnterWorld`, Avatar promotion, arena period, GUI readiness, and
  teardown can overwrite state that was set correctly earlier.
- A BigWorld creation call may synchronously re-enter Python through an entity
  callback before the caller reaches its next source line. Install identity
  tokens and minimum callback-visible state before making such a call.
- Apply a native fix at the last stable boundary that owns it, and verify that
  no later stock callback resets it. Keep adapters narrow, reversible, and
  guarded by current entity/round identity.
- Prefer a typed live surface proved by the exact client over a legacy helper
  that returns successfully but may not affect native state. Do not invent a
  getter or readback merely because a related native setter was verified.
- Keep one owner for each native pose, filter, projectile visual, callback, or
  authority state. Two plausible writers usually create jitter, duplicate
  effects, or cleanup races.
- Do not fabricate a physical result when a descriptor, native node, collision
  body, or resource is missing: do not launch from a guessed muzzle, pass
  through an unknown obstacle, or accept an unproved hit. Contain that failure
  to the current operation, publish an explicit terminal outcome, and keep the
  rest of the battle running whenever the shared state remains coherent.
- Every scheduled callback must check battle/round/entity identity and become
  harmless after cleanup. Teardown should be idempotent even after partial
  startup.

## Python 2 client discipline

Client code below `0.9.22/src/res/scripts/client/` must parse and run on Python
2.7. Avoid Python 3-only syntax and library assumptions: annotations,
f-strings, keyword-only arguments, `pathlib`, ordered-dict dependence, and
implicit text/bytes conversions are common mistakes.

Compile without writing adjacent bytecode during ordinary validation:

```bash
"$PY27" - <<'PY'
from __future__ import print_function
import os

root = '0.9.22/src/res/scripts/client'
paths = sorted(os.path.join(base, name)
               for base, unused_dirs, files in os.walk(root)
               for name in files if name.endswith('.py'))
for path in paths:
    compile(open(path, 'rb').read(), path, 'exec')
print('CPython 2.7 source compile passed: %d files' % len(paths))
PY
```

The server and test harness use Python 3. Keep protocol payloads plain JSON at
the boundary so neither runtime depends on the other's object model.

## LAN, authority, and asynchronous work

- Every room has one mandatory hidden native worker. The only simulation path
  is visible client -> LAN server -> hidden worker -> LAN server -> replicas.
  Visible clients submit player input and fire intent and never become Bot or
  projectile authority. The removed visible-client and pure-Python simulation
  fallbacks must not be reintroduced.
- A client `_send()` returning true may mean only that an immutable payload was
  admitted to a local sender queue. It is not a server acknowledgement.
- Freeze or project mutable state at the wire boundary. JSON mapping keys must
  be text; local integer-keyed structures may need canonicalization there.
- Wait for an observable server state, acknowledgement, accepted counter, or
  queue drain in tests. Fixed 50 ms sleeps have already proved too short when a
  manifest performs synchronous setup before the next message is consumed.
- Preserve round, revision, authority epoch, sequence, and base-revision
  fences. Snapshot/takeover state must distinguish otherwise ambiguous states
  explicitly rather than infer them from a counter.
- Operations that must survive authority loss need a durable server record.
  Do not rely on FIFO order between two separately admitted messages when the
  invariant requires atomicity.
- Repeated messages should be either idempotent with the same fingerprint or
  contained before any partial mutation. Validate a complete batch before
  applying its first item. A recoverable bad or unverifiable message is local
  to that message; it must not end the round, freeze all Bots, or disconnect
  every player.
- Coalesce only genuinely superseded state. Preserve barriers, accepted fire
  intents, projectile terminals, destruction events, and other one-shot
  transitions. Apply elapsed time when a legal update is delayed instead of
  silently discarding the interval.

The server owns room admission, round lifecycle, timing and shared ledgers.
The hidden worker is the sole native simulation authority for Bot movement,
map collision and projectile progression; the server validates and commits
its shared outcomes. Do not infer authority from which side happens to
calculate a presentation value.

## Performance investigations

Start from captured `PERF` stage timing and a reproducible scene. Separate
render-frame time, code executed outside the measured callback, networking,
Bot update, Bot event publication, presentation, native ray queries, and
projectile terminal work.

Look for feedback loops. A low frame rate can reduce a per-frame probe budget,
which can defer motion receipts, which can leave vehicles stationary and make
traffic worse. Measure queue age, retries, reuse, and fairness rather than only
the nominal per-frame cap.

Prefer these optimizations in order:

1. stop sending or copying state the consumer never reads;
2. reuse a typed receipt while its geometric containment remains valid;
3. distribute bounded work fairly across frames;
4. reduce redundant broad-phase candidates or cache exact pure-data results;
5. only then consider cadence or budget changes, while preserving the final
   physical safety gate.

Do not lower projectile/ray safety budgets merely to make a microbenchmark
green. That can move the cost into catch-up frames or make visuals and damage
diverge. Native Windows frame pacing remains the performance acceptance.

Bot planning and motion must remain decoupled. If a planning update is late,
the Bot continues its last valid movement command unless it has an explicit
tactical or physical reason to stop. Lowering planning, avoidance, or spotting
cadence is preferable to emitting repeated throttle-zero commands. Hazard
policy must be identical in A*, direct shortcuts, smoothing, local recovery,
and runtime probes; otherwise a dry A* route can still turn into a shallow-
water shortcut and a visible move/reject loop.

## Failure evidence

For a Python failure, preserve the first traceback and enough preceding
`python.log` and server log to establish the current round, entity, and
message. Avoid enabling unbounded per-frame logs before reproducing it.

For a native crash, collect at minimum:

- exact `WorldOfTanks.exe` identity and client build;
- matching WOTMOD/overlay and Git revision; calculate a checksum only when an
  integrity question actually requires it;
- reproduction steps and whether the crash is deterministic;
- `python.log` and server log with synchronized timestamps;
- first-chance/full dump or minidump from the crashing process.

Use the dump to identify the native module, faulting instruction, thread, and
Python-to-native call boundary. A clean Python traceback log does not prove a
native adapter is safe, and a static disassembly does not prove the crash is
fixed.

To exercise ordered-input recovery on the exact client, arm the LAN server's
one-shot input-fault hook before launching it:

```bat
set WOT_0922_INPUT_FAULT=aim_yaw
```

Any class in `PLAYER_INPUT_FAULT_CLASSES` breaks exactly one frame per round
per player by rewriting one field, which then fails the production
pre-admission validator. Leave the variable unset in normal play.

## Validation and release

Ordinary changes run the focused test, the port suite, Python 2.7 source
compilation for changed client files, and `git diff --check`. These tools stay
available for exact-client questions, but none of them is a required ritual:

```bash
export PYTHONDONTWRITEBYTECODE=1
python3 0.9.22/tools/inspect_client.py "$WOT_0922_CLIENT"
"$PY27" 0.9.22/tools/audit_client_abi.py "$WOT_0922_CLIENT"
"$PY27" 0.9.22/tools/audit_client_lifecycle.py "$WOT_0922_CLIENT"
```

Build client artifacts with `0.9.22/build_for_client.sh "$WOT_0922_CLIENT"`.
That script is not read-only: it rebuilds `0.9.22/dist` and removes older
outputs produced by this port. Build the Windows server and the launcher on
Windows CI, not by pretending a macOS artifact proves the Windows executable.
The packaged default endpoint is loopback; the user-owned
`server_endpoint.json` must never ship in the overlay.

Current desktop-launcher releases are Windows x64 and contain only 0.9.22.
Inspect the final Actions artifact's PE machine type, nested `0.9.22.zip` and
Alpha `.wotmod`; do not add 0.8.2 or Map Studio to this distribution.

## Recurring traps

- Old `ports/0.9.22` commands and old test counts are stale. Discover the live
  top-level paths and counts.
- A successful wrapper call is not proof that native state changed. Read back
  the typed property or verify the exact consumer.
- A resource field, native string, or similarly named public source method is
  not enough; prove the #1513 reader and call path.
- A normalized catalog key does not prove that a native resource lookup is
  case-insensitive. Preserve the exact case-spelled filename at that boundary.
- A screenshot can obscure two distinct objects. Trust the reported symptom,
  then inspect exact spatial data and visibility masks before concluding that
  it is a perspective effect.
- A unit-test fake that omits a native guard can produce a false green.
- A fixed sleep can produce a false red or false green in asynchronous tests.
- A planner can run at a healthy frequency while repeatedly issuing stop or
  probe-rejected movement. Log combat mode, target, water depth, probe verdict,
  throttle and queue age before blaming worker throughput.
- A hazard penalty in A* is ineffective if direct-link, smoothing, or local-
  recovery paths bypass the same hazard classification.
- Updating source-audit hashes before behavior freezes turns a review gate into
  a rubber stamp.
- Running Python 3 imports against the client source can leave ignored
  `__pycache__` beside release inputs. Clean exact generated files and rerun
  provenance/package checks before release.
- Static and package gates never replace the final exact Windows client test.
