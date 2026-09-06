# Third-Party Notices

## Project lineage

Some mechanics retained in the current #1513 implementation were originally
derived from the World of Tanks 0.8.2 reconstruction in
[`SigmaTel71/mod_offhangar_legacy`](https://github.com/SigmaTel71/mod_offhangar_legacy)
at commit `312534823dab535457f8578d9eae6cf3c549944e`, licensed under the GNU
General Public License version 3. That project identifies its own work as a
port of Renat "IzeBerg" Illiev's `mod_offhangar` v1.0.0.0.

The historical source snapshot also followed files published in
[`PseudoJoker-1/WoT-0.8.2-offline-Battles`](https://github.com/PseudoJoker-1/WoT-0.8.2-offline-Battles).
That repository did not declare a software license when this notice was
prepared. The GPL-3.0 license in this repository covers the GPL-derived work
and the contributions whose authors have applied it; it does not create a
license for independently copyrightable third-party material. Permission or
replacement is still required for any such material retained from that source.

## Tuxedo offline-server reference

The 0.9.22 map-picker and entity-lifecycle adapters were informed by
[`the-tuxedo-cat/wot-offline-server`](https://github.com/the-tuxedo-cat/wot-offline-server)
at commit `c0bc550c46deac980194b7b860ee8781d53ec97b`, licensed under the Boost
Software License 1.0. The license text is included at
[`licenses/Boost-1.0.txt`](licenses/Boost-1.0.txt).

## Compiled-space tooling

The build-time decoder under `tools/vendor/wot_space_bin_utils` comes from
[`SkepticalFox/wot-space.bin-utils`](https://bitbucket.org/SkepticalFox/wot-space.bin-utils/)
and is distributed under the WTFPL version 2. Its license text is retained in
that vendored directory.

## Windows download runtimes and packager

The downloadable Windows LAN server and the desktop launcher bundle
CPython 3.11.9, distributed under
the Python Software Foundation License Version 2 together with the licenses
and notices for software incorporated into Python. The complete terms and
source release are published by the Python Software Foundation at
[`docs.python.org/3.11/license.html`](https://docs.python.org/3.11/license.html)
and
[`python.org/downloads/release/python-3119`](https://www.python.org/downloads/release/python-3119/).

The launcher window uses Tk, so the launcher executable also bundles Tcl/Tk 8.6
under the BSD-style Tcl/Tk license. The complete terms are published at
[`tcl-lang.org/software/tcltk/license.html`](https://www.tcl-lang.org/software/tcltk/license.html).

Both executables are produced with PyInstaller 6.21.0. Its bootloader and loader
are GPL-2.0-or-later with the PyInstaller bootloader exception, its runtime
hooks are under Apache License 2.0, and its isolated helper is additionally
available under the MIT license. The complete version-pinned licensing terms
are retained upstream in
[`PyInstaller v6.21.0 COPYING.txt`](https://github.com/pyinstaller/pyinstaller/blob/v6.21.0/COPYING.txt).

## Microsoft Sysinternals ProcDump

Microsoft Sysinternals ProcDump is not distributed with this project. If a user
chooses to enable native crash dumps, the launcher downloads the 32-bit
executable directly from Microsoft's official download host into that user's
local application-data directory. The download and use of ProcDump are governed
by Microsoft's [license terms](https://learn.microsoft.com/en-us/sysinternals/license-terms).
Official information is available from the
[ProcDump documentation](https://learn.microsoft.com/en-us/sysinternals/downloads/procdump)
and [Sysinternals licensing FAQ](https://learn.microsoft.com/en-us/sysinternals/license-faq).

## Optional local chat model and inference runtime

The optional Bot team-chat feature is off unless a player chooses to install
it, and neither half is distributed with this project.

When a player enables it, the launcher downloads a Qwen2.5-Instruct model in
GGUF form, published by Alibaba Cloud's Qwen team under the Apache License
2.0. The same file is offered from
[ModelScope](https://www.modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF)
and [Hugging Face](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF);
ModelScope is listed first because it is reachable from mainland China. Both
mirrors were verified to serve the identical file, and the exact repositories,
file names, sizes and SHA-256 digests are pinned in
[`server/bot_chat_models.py`](server/bot_chat_models.py).

Inference runs in a separate `llama-server` process from
[`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp), distributed
under the MIT License. The launcher downloads the pinned CPU-only Windows
build from that project's official release assets.

## Wargaming intellectual property

This project is an unofficial compatibility modification. It does not include
or license the World of Tanks game client. The GPL-3.0 license applies only to
the covered project code and does not grant rights to Wargaming's games,
assets, trademarks, or other intellectual property.

This work includes trademarks and/or copyrighted works that are the exclusive
property of Wargaming. All rights reserved by Wargaming. This work is
unofficial and is not endorsed by Wargaming.
