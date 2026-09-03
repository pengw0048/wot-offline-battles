# Repository working agreement

This file is the single source of truth for repository-wide agent guidance.
`AGENTS.md` is a symlink to this file so tools that discover either filename
receive the same instructions. Do not duplicate or independently edit the
symlink target. Read this file before changing the repository. When working
under a client version, also read its version-local guide before investigating
or editing that implementation: `0.8.2/CLAUDE.md` or `0.9.22/CLAUDE.md`.

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

## Keep the two client lines separate

- `0.8.2/` targets its own client and Python 2.6 runtime. Its code, package,
  tests, and native-physics boundary are version-local. Follow
  `0.8.2/CLAUDE.md` for its legacy-runtime, collision, streaming, and package
  workflow.
- `0.9.22/` targets only Chinese HD client `0.9.22.0.1 #1513`, x86, with an
  embedded Python 2.7 runtime. Follow `0.9.22/CLAUDE.md` for its exact-client
  workflow.
- Do not transplant PYC files, private names, entity schemas, native offsets,
  map parsers, or lifecycle assumptions across versions. Reuse gameplay law
  only after proving the target adapter.
- `ports/0.9.22` is a retired path. The live port is the top-level `0.9.22/`
  directory.
- Current desktop-launcher releases contain only 0.9.22. Keep 0.8.2 and Map
  Studio as separate work and separate artifacts unless Peng explicitly
  changes that packaging decision.

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

## Current 0.9.22 operating model

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

## Canonical documentation

Keep the documentation small. These files have owners; do not duplicate them:

- Root `CLAUDE.md`: repository-wide agent guidance; `AGENTS.md` only links to
  it.
- Root `README.md`: what the project is, how a player runs it, how to build it.
- `0.8.2/CLAUDE.md` and `0.9.22/CLAUDE.md`: version-local technical guidance
  that supplements this file without redefining repository-wide policy.
- `0.9.22/INSTALL.txt`: what the 0.9.22 package contains and how to play.
- `0.9.22/COMPATIBILITY_REVIEW.md`: exact-client interfaces and lifecycle
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
