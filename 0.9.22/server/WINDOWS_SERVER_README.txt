WoT 0.9.22 Offline LAN Server
================================

WoT-0.9.22-LAN-Server.exe is the x64 Rust LAN server for the exact Chinese HD
0.9.22.0.1 #1513 client package from the same repository revision.

The normal LAN-host path is the WoT Offline Battles Launcher's Online tab:
click Start LAN room, wait for the server and hidden oracle to become ready,
then start the host game. The room keeps exactly one hidden oracle until Stop
LAN room is clicked. START_OFFLINE_0922.bat starts only a diagnostic visible
client; it is not a complete single-player server entry point.

The hidden client is a version-locked native-world oracle for BigWorld
collision and geometry observations. The Rust server owns room lifecycle,
portable simulation, bots, projectiles, combat, replication and results. A
missing or failed oracle closes or freezes the room; authority never falls back
to a visible player client.

The server listens on TCP 28782. A LAN room binds 0.0.0.0 and supports up to
30 players; single player binds loopback only. The launcher passes exact team
and Bot-lineup settings before startup. Keep the server running for the whole
room.

On Windows, a non-loopback server checks for an inbound rule scoped to this
exact executable and TCP 28782 before binding. Approve the UAC prompt on a
trusted private LAN. Cancelling is nonfatal, but another PC may remain unable
to connect. Loopback-only single player does not request a firewall rule.

If the process exits immediately, another program may already be using TCP
28782, the startup configuration may be invalid, or the build may not match
this package. Close the conflicting process, keep the matching client package
installed, and retry through the launcher. A missing oracle is reported when a
room attempts to start a battle; it does not prevent the bare server from
listening.

License and source
==================

This server is part of wot-offline-battles and is distributed under GNU GPL
version 3, without warranty. Corresponding source, Cargo.lock, the project
license and third-party notices are available at:

https://github.com/pengw0048/wot-offline-battles

The executable is built from Rust. Rust's standard library and the crate
dependencies used by this server are distributed under their respective open
source licenses; see Cargo.lock and THIRD_PARTY_NOTICES.md in the source tree.

World of Tanks and its assets are not included. This project is unofficial and
is not endorsed by Wargaming.
