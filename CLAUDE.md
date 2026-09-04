# Repository working agreement

This file is the single source of truth for repository-wide agent guidance.
`AGENTS.md` is a symlink to this file so tools that discover either filename
receive the same instructions. Do not duplicate or independently edit the
symlink target. Read this file before investigating or changing the repository.

## Working with Peng

- Discuss plans, evidence, tradeoffs, progress, and results with Peng in
  Chinese. Keep code, comments, commit messages, logs, and shared repository
  documentation in English.
- Lead with the observed outcome and supporting evidence. Separate a confirmed
  fact from an inference, and say exactly which boundary still needs a real
  Windows client.
- Prefer the correct root-cause fix with a complete, data-driven scope. Keep
  changes no broader than the affected class of behavior, but do not optimize
  for the smallest diff when the same defect can affect other vehicles, maps,
  or adapters. Audit and cover the full affected class; do not add speculative
  compatibility layers, configuration, or infrastructure unrelated to the
  demonstrated problem.
- Present meaningful tradeoffs instead of silently choosing one. If empirical
  behavior contradicts documentation or a prior conclusion, reproduce the
  behavior first and update the conclusion.
- Treat Peng's gameplay observations as runtime evidence. A screenshot may be
  ambiguous, but do not dismiss the observation because the screenshot is
  ambiguous; inspect the exact map, client data, logs, and lifecycle.
- Fix an in-scope bug directly rather than handing it to an imaginary future
  owner. Preserve unrelated user changes in a dirty worktree.
- Peng has explicitly permitted in-scope changes to be committed and pushed
  directly to `main`. Never force-push. Create a tag or publish a release only
  when Peng explicitly requests it. Keep validation proportional to his
  instruction: report missing Windows evidence honestly, but do not invent an
  extra review, full-CI, or native-acceptance gate after he explicitly asks to
  commit, package, or release without it.

## Start every task from the exact current state

Run these checks before relying on an old handoff, test count, hash, or path:

```bash
pwd
git status --short --branch
git worktree list
git log -5 --oneline --decorate
git rev-parse HEAD
git rev-parse origin/main
```

Before basing work intended for push, or reporting that `HEAD == origin/main`,
run `git fetch --prune origin`. Without a fetch, `origin/main` is only the last
locally observed remote state. An offline diagnostic task need not fetch.

Other agents and worktrees may be active. Coordinate ownership of shared files
before editing them, and rerun validation after the final files stop changing.
A passing test from a drifting intermediate tree is not release evidence.

Do not use destructive Git commands to clean up work that you did not create.
Generated `__pycache__`, `.pyc`, `.pyo`, logs, and release output should not be
committed; remove only exact generated targets after verifying what they are.

## Supported target and repository layout

- The only supported client is Chinese HD `0.9.22.0.1 #1513`, 32-bit x86,
  with an embedded CPython 2.7.7 runtime.
- The live implementation is at the repository root: `src/`, `server/`,
  `tests/`, `tools/`, `navgraphs/`, `foliage/`, and `destructibles/`. Paths
  such as `0.8.2/`, `0.9.22/`, and `ports/0.9.22/` are retired and must not be
  restored.
- The launcher, server, tests, and package build support only this exact client.
  Do not add compatibility machinery for another client without an explicit
  product decision from Peng.
- Historical references to 0.8.2 describe provenance of retained gameplay
  laws, not a supported runtime, package, source tree, or protocol peer.

## Evidence ladder

Use the lowest layer that answers the question, but never claim that it proves
a higher layer:

1. Current repository source and pure-data tests prove local logic.
2. Exact pinned resource data and bytecode prove build-specific Python
   contracts and static content.
3. Contract tests and audits prove that the local adapter matches the reviewed
   contract.
4. Independent package inspection proves what was actually shipped.
5. Only acceptance on the exact Windows client can prove the specific
   BigWorld rendering, native physics, timing, lifecycle, performance, and
   gameplay-feel claims exercised by that acceptance.
6. A native crash requires a first-chance/full dump or minidump plus the
   matching executable, package, `python.log`, and server log. Static guesses
   are not a crash diagnosis.

For example, a Python unit test can prove that a native call is made with the
reviewed arguments. It cannot prove that the native implementation renders,
owns memory safely, or feels identical to retail.

## Exact-client investigation and Python boundary

Use an environment variable rather than embedding a developer path:

```bash
export WOT_0922_CLIENT=/path/to/World_of_Tanks_0.09.22.00.01_CH_1513_HD
export PYTHONDONTWRITEBYTECODE=1
python3 tools/inspect_client.py "$WOT_0922_CLIENT"
```

The pinned compatible build/audit interpreter is CPython 2.7.18. It reads the
same Python 2.7 PYC format, but it is not the embedded 2.7.7 game runtime. On
Peng's Mac, resolve it through pyenv when available:

```bash
PY27="$(PYENV_VERSION=2.7.18 pyenv which python2.7)"
"$PY27" --version
```

- Run `tools/inspect_client.py` before drawing conclusions from a client. It
  checks the regional build, x86 executable, mod paths, representative PYC
  magic, entity definitions, required assets, and reviewed native method table.
  Public source or a differently numbered regional build is a lead, not #1513
  contract evidence.
- Read the exact Python client modules from `res/packages/scripts.pkg` with a
  Python 2.7 interpreter. Its PYC code object begins after the eight-byte
  header. Inspect the producer and every direct consumer, including call and
  unpack widths, guards, cleanup, and private-name ownership. A decompiler is
  orientation, not the contract.
- A packed XML field is not runtime truth until the exact #1513 reader assigns
  or forwards it and a runtime consumer uses it. `tools/packed_xml.py` is a
  library parser, not a client-data inspector by itself.
- Native symbols prove only that an exposed name exists. They do not prove
  units, ownership, callback timing, memory safety, or C++ behavior. Use exact
  Windows runtime evidence, and collect a dump for native crashes.
- For a new BigWorld dependency, verify the exact archive member, signature,
  return/sentinel/exception shape, producer-consumer schema, lifecycle guards,
  asynchronous completion, ownership, idempotence, failure behavior, and
  teardown order. Encode stable contracts in focused ABI/lifecycle audits.
- Test fakes must reproduce the native guards relevant to the bug. A fake that
  always succeeds can hide an initialization-order failure.

Client code below `src/res/scripts/client/` must parse and run on Python 2.7.
Avoid annotations, f-strings, keyword-only arguments, `pathlib`, ordered-dict
dependence, and implicit text/bytes assumptions. Compile it without writing
adjacent bytecode:

```bash
"$PY27" - <<'PY'
from __future__ import print_function
import os

root = 'src/res/scripts/client'
paths = sorted(os.path.join(base, name)
               for base, unused_dirs, files in os.walk(root)
               for name in files if name.endswith('.py'))
for path in paths:
    compile(open(path, 'rb').read(), path, 'exec')
print('CPython 2.7 source compile passed: %d files' % len(paths))
PY
```

The server and tests use Python 3. Keep protocol payloads plain JSON so neither
runtime depends on the other's object model. Run resource bakers into a
temporary output and diff it before replacing tracked catalogs.

## BigWorld lifecycle and ownership

- Account, lobby, space load, entity `onEnterWorld`, Avatar promotion, arena
  period, GUI readiness, and teardown order are part of the ABI and may
  overwrite earlier state.
- A native creation call may synchronously re-enter Python. Install identity
  tokens and minimum callback-visible state before making it.
- Apply a native fix at the last stable owner, then prove no later stock
  callback resets it. Keep adapters narrow, reversible, and fenced by current
  entity and round identity.
- Do not fabricate a physical result when a descriptor, native node, collision
  body, or resource is missing. Contain the operation, publish its terminal
  outcome, and preserve coherent shared state.
- Every scheduled callback must validate battle, round, and entity identity.
  Cleanup must be harmless after partial startup and safe to call twice.

## Current operating model

- Every room has one mandatory hidden native worker. The only simulation path
  is `visible client -> LAN server -> hidden worker -> LAN server -> replicas`.
  Visible clients submit player input and fire intent; they never become Bot or
  projectile authority. Do not restore visible-client authority or the removed
  pure-Python simulation fallback.
- The launcher installs and starts the matching server and worker together.
  Do not build speculative compatibility machinery for combinations it never
  creates. This is a trusted-LAN product, not an anti-cheat boundary.
- Validate wire shape, actor/round identity, and safe numeric bounds, but do
  not reject legal #1513 behavior because a narrow invariant guessed that it
  was impossible. A recoverable bad, stale, duplicate, or locally
  unverifiable message must be contained to that message or operation; it
  must not freeze every Bot, end the round as a system-error draw, disconnect
  all clients, or return everyone to the garage.
- Coalesce only state that is genuinely superseded. Preserve barriers,
  accepted fire intents, projectile terminal events, destruction events, and
  other one-shot transitions. Never silently discard an admitted shot or a
  frame merely because the next update arrived late.
- An accepted operation needs an observable terminal outcome. A fire intent,
  for example, must become a launched/terminal projectile or an explicit local
  failure with correct ammunition and reload state. A sound-only no-op that
  permits immediate refiring is a bug.
- Use exact client descriptors and client-owned input for the vehicle, gun,
  shell, crew, equipment, muzzle, pose, and control state the client already
  knows. Do not invent coefficients, clamps, fallback parameters, or simplified
  formulas to make a result look plausible.

## Runtime lessons from Windows playtesting

### Bot motion and navigation

- Planning cadence and motion cadence are separate. When no new decision
  arrives, continue the last valid movement command. Stop only for an explicit
  tactical hold, terminal state, proved physical blocker, or safety condition.
  Apply elapsed time when an update is late rather than throwing the interval
  away.
- Do not make vehicle avoidance or contact prediction expensive enough to
  produce repeated throttle-zero commands. Reduced planning or spotting
  cadence is acceptable; visible walk-stop-walk motion is not. Friendly ram
  damage is currently disabled, so elaborate teammate avoidance is lower
  priority than continuous movement.
- Navigation hazards must agree across A*, direct shortcuts, path smoothing,
  local recovery, and the runtime motion probe. Shallow water is a high-cost
  passable fallback when no dry path exists, not a preferred shortcut that
  ends in a deeper-water rejection loop.
- Route endpoints, capture targets, and tactical objectives are distinct. Do
  not send the whole team to one base coordinate when staging, screening,
  artillery deployment, or map-authored routes are available.
- Diagnose a stationary Bot by separating presentation from authoritative
  motion. At a bounded cadence record Bot ID, combat mode, route target, water
  depth, motion-probe verdict, throttle, planner age, and queue age. A healthy
  planning frequency does not prove that it issued movement.

### Projectiles, collision, and native presentation

- Freeze an accepted shot's shell, muzzle, direction, dispersion, timing, and
  relevant vehicle state exactly once. Deferred shell selection must not
  rebind it. A projectile/native-query failure is local to that projectile and
  must preserve terminal, reload, and ammunition bookkeeping.
- Human and Bot paths need parity coverage. Bot-only projectile tests do not
  prove that a human shot advances, resolves, damages, reports feedback, and
  settles ammunition correctly.
- Evaluate moving targets against the correct pose and time. Never leave
  collision, reticle, or projectile code using a vehicle's spawn pose, and do
  not mix a current client reticle with an unrelated historical target pose
  without an explicit latency model.
- Keep one owner for each remote pose, gun/turret angle, track state,
  visibility state, projectile visual, and native callback. Competing native
  and replicated writers cause jitter, extreme gun angles, stale shadows,
  frozen minimap markers, and birth-pose collision errors.
- Visibility covers the complete presentation: entity, compound, decals,
  tracks, shadow, outline, marker, minimap notification, and repeated spotted
  sound. Render/AOI range applies to friendly vehicles as well as enemies.
- Collision damage uses horizontal contact-normal closing speed, not full
  relative or vertical velocity. Preserve the first-impact face; a later SAT
  separation normal is not a new impact. Do not select the whole vehicle's
  thinnest armour when the contact ray cannot establish a matching plate.
- Do not tune collision formulas by feel. The current explicit product choice
  is no friendly ram damage and a temporary 25% enemy-ram scale while exact
  retail calibration remains under investigation.
- Once a fragile destructible is positively identified locally, its collision
  and presentation may yield immediately while the worker publishes the
  canonical event. Do not stop or rewind the tank while waiting. Real walls
  and unidentified objects must continue to block.

### Performance investigations

- Start from captured `PERF` stage timing and a reproducible scene. Separate
  render-frame time, networking, Bot update/publication, presentation, native
  queries, and projectile terminal work.
- Measure feedback loops, queue age, retries, reuse, and fairness. A low frame
  rate can shrink a per-frame probe budget, defer motion receipts, and make
  traffic progressively worse.
- Prefer removing unread state, reusing geometrically valid receipts,
  distributing bounded work fairly, and reducing redundant candidates before
  changing cadence or safety budgets.
- Do not reduce projectile or collision safety to improve a microbenchmark.
  Exact Windows frame pacing is the performance acceptance boundary.

## Canonical documentation

Keep the documentation small. These files have owners; do not duplicate them:

- Root `CLAUDE.md`: repository-wide agent guidance; `AGENTS.md` only links to
  it.
- Root `README.md`: what the project is, how a player runs it, how to build it.
- Root `INSTALL.txt`: what the client package contains and how to play.
- Root `COMPATIBILITY_REVIEW.md`: exact-client interfaces and lifecycle
  evidence for the #1513 port.
- `launcher/LAUNCHER_README.txt`: the text shipped inside the launcher
  download, including the bundled-runtime licenses.
- `.github/workflows/tests.yml`: what CI actually executes.

Do not add a new document for a change that fits in code, a test, or one of the
files above. Do not hard-code a current test count, release status, CI URL, or
source hash in this instruction file.

## Change, validation, and handoff discipline

- Diagnose with read-only checks first. Implement only when the task includes a
  change.
- Test the narrow failure first, then the relevant subsystem, and run a broader
  gate only when the risk or explicit request requires it. Use
  `PYTHONDONTWRITEBYTECODE=1` for Python tests where possible. If Peng asks for
  no review, no CI, or an immediate commit/package, obey rather than adding
  process theatre.
- For asynchronous tests, wait for a state transition or acknowledgement. Do
  not use a small fixed sleep as proof that a handler or worker consumed a
  message.
- Unless Peng explicitly asks to skip them, inspect the staged diff, run the
  proportional checks, commit one coherent change, push `main` when requested,
  and verify the exact pushed commit's relevant CI for runtime, packaging, CI,
  or release behavior.

A useful final handoff records:

- exact `HEAD`, branch, and whether `HEAD == origin/main`;
- files changed and the user-visible result;
- exact commands run and their results;
- package or runtime identity when relevant;
- what remains unproved, especially native Windows behavior;
- whether work was committed, pushed, tagged, or released.

Never describe a task as complete merely because the static tree is green when
the stated acceptance requires native Windows evidence.

## Peng's Windows VM, diagnostics, and release delivery

- Windows `C:\\Mac\\Home\\Desktop` maps to macOS `/Users/peng/Desktop`.
  Shared-folder writes can leave files owned by root on macOS. If a precise
  replacement is blocked, use `prlctl exec` to perform it from Windows rather
  than weakening permissions broadly.
- Deploy test builds through the launcher. Do not manually leave another mod
  copy in the game directory; stale copies and cross-owner moves have caused
  installation failures. Keep the shared Desktop free of ambiguous old builds.
- Treat one report as a coherent session: server log, visible-client
  `python.log`, hidden-worker log, launcher log, and dump when present. Start
  ProcDump and helpers without a visible console or focus steal. Normal Alt+F4
  or launcher-requested termination is not a crash.
- Persist valid garage, module, and crew changes at safe change boundaries;
  avoid per-frame disk writes, but do not make persistence depend on a perfect
  shutdown or one particular launcher button.
- For releases, align every version pin, build the final Windows launcher from
  the intended `main` commit with the x64 GitHub Actions workflow, and inspect
  the outer ZIP, PE machine type, nested `0.9.22.zip`, and contained `.wotmod`.
  Automated packaging proves the artifact contract, not untested gameplay.
- Do not calculate or report checksums unless explicitly requested or required
  by an integrity gate.
