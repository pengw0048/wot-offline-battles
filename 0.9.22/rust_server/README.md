# Offline Battles Rust LAN Server

This crate is the protocol-v5 LAN server for the exact World of Tanks
0.9.22.0.1 #1513 client. It owns room and round lifecycle, the fixed 30 Hz
simulation, bot decisions, projectiles, combat, results, and replication.

A hidden copy of the game client remains the native-world oracle. It answers
version-locked BigWorld geometry, vehicle, node, water, and destructible
queries for the server; it does not own gameplay state. The ordinary Python
client mod connects players and the hidden oracle to this process, but there is
no Python-server authority mode.

## Build and test

From the repository root:

```console
cargo test --manifest-path 0.9.22/rust_server/Cargo.toml --locked
cargo build --manifest-path 0.9.22/rust_server/Cargo.toml --release --locked
```

On x64 Windows, the delivery script builds the MSVC target and writes the
single branded executable plus its README to `0.9.22/dist/server`:

```powershell
pwsh -NoProfile -File 0.9.22/server/build_windows_server.ps1
```

## Run

The server defaults to `0.0.0.0:28782`, 15 tanks per team, and a random
supported map. Point it at the exact client root so battle start can load the
selected map's released v2 navigation graph (the packaged launcher sets this
automatically):

```console
export WOT_0922_VEHICLE_OVERLAY_ROOT=/path/to/World_of_Tanks_0.09.22.00.01_CH_1513_HD
cargo run --manifest-path 0.9.22/rust_server/Cargo.toml --release -- serve
```

An absent or incompatible selected-map graph refuses battle start; the server
does not replace it with an unsafe straight-line route.

Use `--help` for endpoint, map, team-size, and receipt-state options. The
packaged launcher starts the hidden native oracle for single-player and
LAN-host sessions.

Two diagnostic commands remain available:

```console
cargo run --manifest-path 0.9.22/rust_server/Cargo.toml -- clock-probe 10
cargo run --manifest-path 0.9.22/rust_server/Cargo.toml -- validate-stream trace.jsonl
```
