# Authority worker probe

This is an opt-in measurement only. It does not start a second client, change
LAN authority, or implement an authority worker. The default is disabled.

Use a windowed #1513 client. Set this in
`mods/configs/offline_lan_0922/config.json` before starting the client:

```json
"authority_worker_probe": {
  "enabled": true,
  "stageSeconds": 15.0
}
```

After the client reaches the lobby, find its exact `WorldOfTanks.exe` process
id and start the external supervisor before entering battle:

```powershell
py -3 authority_worker_probe_supervisor.py `
  --pid 1234 `
  --report "C:\Games\World_of_Tanks\mods\configs\offline_lan_0922\authority_worker_probe.jsonl"
```

The battle must select that client as bot authority. Once battle time starts,
the client measures draw-on, draw-off, and an externally supervised hidden
window stage. The supervisor matches both process id and the probe's unique
run id, refuses fullscreen-sized/borderless windows, checks a stable window
handle twice, and restores immediately if matching client heartbeats stop.
Each 15-second client stage discards its first 5 seconds before assessment.

The report is appended to
`mods/configs/offline_lan_0922/authority_worker_probe.jsonl`. Draw-on and
draw-off records can report `PASS_OPERATIONAL`, `DEGRADED_LOW_FPS`, or
`FAIL_STALLED`. The client-side hidden result remains
`RAW_ONLY_EXTERNAL_WINDOW_EVIDENCE_REQUIRED`; it is valid only when joined
with that run id's `supervisor_stage_result` showing both `hidden` and
`restored` as true.

Disable the flag after the measurement. Do not distribute this probe as a
release feature until it passes real Windows runs.
