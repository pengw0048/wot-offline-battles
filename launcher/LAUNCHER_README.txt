WoT Offline Battles Launcher
================================

The launcher installs the mod, writes the server address, and starts World of
Tanks. Single player owns one Rust LAN server and one hidden native-world
oracle for that session. An Online LAN room owns one persistent Rust server
and exactly one hidden oracle until Stop LAN room is clicked.

1. Start WoT-Offline-Battles-Launcher.exe from this folder. Keep the folder
   together; the launcher needs the files beside it.
2. Select your World of Tanks folder. The list holds the folders you used
   before, plus any game the launcher finds in the usual install locations.
   Use Browse... for a folder that is not in the list. The launcher reports
   which client it found. The only supported client is the exact Chinese HD
   0.9.22.0.1 #1513 build; another build is not compatible.
3. Select a tab:
   - Single player: you play alone against bots. The launcher runs the LAN
     server for you, because every battle is a server battle.
   - Online: to host, click Start LAN room and use the address the launcher
     prints; then start the game on this PC. To join, type the host PC's
     address, for example 192.168.1.20 or 192.168.1.20:28782.
4. Type a player name. Other players see it in the LAN room. Test connection
   checks the address you typed, or reports whether port 28782 on this PC is
   already taken when you host.
5. Click Start game. The launcher removes older mod files, installs the mod
   for that client, and starts the game. This takes a few seconds. It validates
   and stages the complete package before replacing the old mod, and restores
   the previous mod if that replacement fails.

In the game, fit a tank and click Battle!. Everyone lands in the LAN waiting
room, drawn over the stock battle queue screen. The room host selects the map
and the total tanks for each team, including human players, from 1 through 15,
then clicks START BATTLE. The room settings can be changed there without
restarting the game or LAN server. LEAVE closes the room and returns you to
the garage.

On the 0.9.22 client the garage works offline. Every vehicle is owned and every
module in its own tech tree is unlocked, each vehicle arrives with its top
modules and three consumables, and every item costs nothing. Change modules,
optional devices, consumables, shells, camouflage and crew skills; the garage
is saved after each change and the battle uses what you fitted.

The Tools tab also edits vehicle data directly. A vehicle data profile is a
named set of Packed XML field changes (health, damage, penetration, armour,
speeds, reload and other values) made in the editor window; it never changes
scripts.pkg. In single player the selected profile is activated only for that
session and removed again when the game closes. When you start a LAN room on
the Online tab, the selected profile is pinned for the whole room: the room
server shares the modified package members with every player who joins, their
launcher installs the same temporary overlay before the game starts, and
original vehicle data is restored after the session. A room whose profile was
changed after it was started must be restarted before Start game accepts it.

For the exact 0.9.22 client, Repair startup validates the mod configuration
and reinstalls the package while retaining the saved endpoint, garage,
vehicle-data overrides, account progress, battle results and isolated client
preferences. Reset all offline data is a separate confirmed operation. It
deletes the endpoint, account, garage, post-battle, configuration and isolated
client-preference files; it does not delete vehicle-data overrides, other mods
or the normal World of Tanks profile. Both operations require the game to be
closed and leave unrelated mods alone.

If a normal, current World of Tanks client is stuck while loading, the repair
tab can move its shared `%APPDATA%\Wargaming.net\WorldOfTanks\preferences.xml`
aside. The launcher keeps the old file beside it as a timestamped backup; it
does not delete the file or change offline saved data.

The launcher gives the exact 0.9.22 client its own graphics, window, zoom and
input settings. It creates a complete res_mods engine-config overlay from the
installed stock file, changing only the preferences location to
%LOCALAPPDATA%\WoTOfflineBattles\client_profiles\0.9.22\preferences.xml.
The stock engine_config.xml and the profile used by another World of Tanks
installation are never changed. The first offline launch therefore starts
with a new profile and needs its settings chosen once. An existing
engine_config.xml overlay from another tool is reported as a conflict and is
left unchanged.

The client sometimes closes its first process and starts another one while it
starts up. The launcher waits eight seconds after the last game process before
it stops the server, so that restart does not end your battle.

Your 0.9.22 saved address, account state, garage, pending post-battle results
and configuration stay in
mods\configs\offline_lan_0922. Other authors' .wotmod files stay where they
are.

One limit to expect. The exact #1513 client is 32-bit; its executable is
large-address-aware and can normally address about 4 GB on 64-bit Windows, but
a very long session can still run out of memory and exit. Restart the client
between long sessions.

When you host, approve the UAC prompt that opens TCP 28782 for the bundled
WoT-0.9.22-LAN-Server.exe. Cancelling is nonfatal, but other PCs may remain
unable to connect. Run this trusted-LAN server only on a network you trust.

The launcher keeps its settings in
%LOCALAPPDATA%\WoTOfflineBattles\launcher.json. For the exact 0.9.22 client,
crash report collection is enabled by default on the launch page. It monitors
both the visible client and the hidden native-world oracle. If either one closes
unexpectedly, the launcher creates a ZIP and asks whether you want to report
the crash. Choosing Yes only selects that ZIP in Windows Explorer; the launcher
never uploads it. Choosing No deletes that newly created ZIP.

"Create error report" and automatic crash reports copy only the exact log
slices from the latest launcher game session into a ZIP in
%LOCALAPPDATA%\WoTOfflineBattles\reports. A confirmed crash report can also
contain debugging information from the crashing client. Configuration,
vehicle profiles, and saved results are not copied as separate files.

If the hosted server never opens port 28782, another server may already use
that port. Close it and start the game again.

License, source, and bundled runtimes
=====================================

This launcher is part of wot-offline-battles and is distributed under GNU GPL
version 3, without warranty. LICENSE and THIRD_PARTY_NOTICES.md are included
beside this file. The corresponding source is available at:

https://github.com/pengw0048/wot-offline-battles

The executable bundles CPython 3.11.9, distributed under the Python Software
Foundation License Version 2 and the licenses/notices for software incorporated
into Python. The complete terms and corresponding source release are available
at:

https://docs.python.org/3.11/license.html
https://www.python.org/downloads/release/python-3119/

The launcher window uses Tk. The executable therefore also bundles Tcl/Tk 8.6,
distributed under the Tcl/Tk license, a BSD-style license. The complete terms
are available at:

https://www.tcl-lang.org/software/tcltk/license.html

The executable is produced with PyInstaller 6.21.0. Its embedded bootloader and
loader use GPL-2.0-or-later with the PyInstaller bootloader exception; runtime
hooks are under Apache License 2.0, and the isolated helper is also available
under MIT. The complete PyInstaller 6.21.0 licensing terms are available at:

https://github.com/pyinstaller/pyinstaller/blob/v6.21.0/COPYING.txt

Microsoft Sysinternals ProcDump is not included in this launcher. The first
time the launcher asks about native crash dumps, choosing Enable downloads the
32-bit ProcDump executable directly from Microsoft's official site to
%LOCALAPPDATA%\WoTOfflineBattles\tools\procdump.exe. Choosing Enable also
accepts Microsoft's license terms. If the download fails, crash-dump collection
stays disabled and the game can still be launched normally.

The optional full-memory checkbox changes future captures from ProcDump's Mini
format to Full format. It is off by default because Full dumps can be very
large; enable it only when a difficult crash needs deeper diagnosis.

https://learn.microsoft.com/en-us/sysinternals/downloads/procdump
https://learn.microsoft.com/en-us/sysinternals/license-faq
https://learn.microsoft.com/en-us/sysinternals/license-terms

World of Tanks and its assets are not included with this server. This project
is unofficial and is not endorsed by Wargaming.


CPython 3.11.9 license
======================

The following text is the LICENSE file distributed with CPython 3.11.9.

A. HISTORY OF THE SOFTWARE
==========================

Python was created in the early 1990s by Guido van Rossum at Stichting
Mathematisch Centrum (CWI, see https://www.cwi.nl) in the Netherlands
as a successor of a language called ABC.  Guido remains Python's
principal author, although it includes many contributions from others.

In 1995, Guido continued his work on Python at the Corporation for
National Research Initiatives (CNRI, see https://www.cnri.reston.va.us)
in Reston, Virginia where he released several versions of the
software.

In May 2000, Guido and the Python core development team moved to
BeOpen.com to form the BeOpen PythonLabs team.  In October of the same
year, the PythonLabs team moved to Digital Creations, which became
Zope Corporation.  In 2001, the Python Software Foundation (PSF, see
https://www.python.org/psf/) was formed, a non-profit organization
created specifically to own Python-related Intellectual Property.
Zope Corporation was a sponsoring member of the PSF.

All Python releases are Open Source (see https://opensource.org for
the Open Source Definition).  Historically, most, but not all, Python
releases have also been GPL-compatible; the table below summarizes
the various releases.

    Release         Derived     Year        Owner       GPL-
                    from                                compatible? (1)

    0.9.0 thru 1.2              1991-1995   CWI         yes
    1.3 thru 1.5.2  1.2         1995-1999   CNRI        yes
    1.6             1.5.2       2000        CNRI        no
    2.0             1.6         2000        BeOpen.com  no
    1.6.1           1.6         2001        CNRI        yes (2)
    2.1             2.0+1.6.1   2001        PSF         no
    2.0.1           2.0+1.6.1   2001        PSF         yes
    2.1.1           2.1+2.0.1   2001        PSF         yes
    2.1.2           2.1.1       2002        PSF         yes
    2.1.3           2.1.2       2002        PSF         yes
    2.2 and above   2.1.1       2001-now    PSF         yes

Footnotes:

(1) GPL-compatible doesn't mean that we're distributing Python under
    the GPL.  All Python licenses, unlike the GPL, let you distribute
    a modified version without making your changes open source.  The
    GPL-compatible licenses make it possible to combine Python with
    other software that is released under the GPL; the others don't.

(2) According to Richard Stallman, 1.6.1 is not GPL-compatible,
    because its license has a choice of law clause.  According to
    CNRI, however, Stallman's lawyer has told CNRI's lawyer that 1.6.1
    is "not incompatible" with the GPL.

Thanks to the many outside volunteers who have worked under Guido's
direction to make these releases possible.


B. TERMS AND CONDITIONS FOR ACCESSING OR OTHERWISE USING PYTHON
===============================================================

Python software and documentation are licensed under the
Python Software Foundation License Version 2.

Starting with Python 3.8.6, examples, recipes, and other code in
the documentation are dual licensed under the PSF License Version 2
and the Zero-Clause BSD license.

Some software incorporated into Python is under different licenses.
The licenses are listed with code falling under that license.


PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
--------------------------------------------

1. This LICENSE AGREEMENT is between the Python Software Foundation
("PSF"), and the Individual or Organization ("Licensee") accessing and
otherwise using this software ("Python") in source or binary form and
its associated documentation.

2. Subject to the terms and conditions of this License Agreement, PSF hereby
grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
analyze, test, perform and/or display publicly, prepare derivative works,
distribute, and otherwise use Python alone or in any derivative version,
provided, however, that PSF's License Agreement and PSF's notice of copyright,
i.e., "Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation;
All Rights Reserved" are retained in Python alone or in any derivative version
prepared by Licensee.

3. In the event Licensee prepares a derivative work that is based on
or incorporates Python or any part thereof, and wants to make
the derivative work available to others as provided herein, then
Licensee hereby agrees to include in any such work a brief summary of
the changes made to Python.

4. PSF is making Python available to Licensee on an "AS IS"
basis.  PSF MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PSF MAKES NO AND
DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON WILL NOT
INFRINGE ANY THIRD PARTY RIGHTS.

5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS
A RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON,
OR ANY DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.

6. This License Agreement will automatically terminate upon a material
breach of its terms and conditions.

7. Nothing in this License Agreement shall be deemed to create any
relationship of agency, partnership, or joint venture between PSF and
Licensee.  This License Agreement does not grant permission to use PSF
trademarks or trade name in a trademark sense to endorse or promote
products or services of Licensee, or any third party.

8. By copying, installing or otherwise using Python, Licensee
agrees to be bound by the terms and conditions of this License
Agreement.


BEOPEN.COM LICENSE AGREEMENT FOR PYTHON 2.0
-------------------------------------------

BEOPEN PYTHON OPEN SOURCE LICENSE AGREEMENT VERSION 1

1. This LICENSE AGREEMENT is between BeOpen.com ("BeOpen"), having an
office at 160 Saratoga Avenue, Santa Clara, CA 95051, and the
Individual or Organization ("Licensee") accessing and otherwise using
this software in source or binary form and its associated
documentation ("the Software").

2. Subject to the terms and conditions of this BeOpen Python License
Agreement, BeOpen hereby grants Licensee a non-exclusive,
royalty-free, world-wide license to reproduce, analyze, test, perform
and/or display publicly, prepare derivative works, distribute, and
otherwise use the Software alone or in any derivative version,
provided, however, that the BeOpen Python License is retained in the
Software, alone or in any derivative version prepared by Licensee.

3. BeOpen is making the Software available to Licensee on an "AS IS"
basis.  BEOPEN MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, BEOPEN MAKES NO AND
DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF THE SOFTWARE WILL NOT
INFRINGE ANY THIRD PARTY RIGHTS.

4. BEOPEN SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF THE
SOFTWARE FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS
AS A RESULT OF USING, MODIFYING OR DISTRIBUTING THE SOFTWARE, OR ANY
DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.

5. This License Agreement will automatically terminate upon a material
breach of its terms and conditions.

6. This License Agreement shall be governed by and interpreted in all
respects by the law of the State of California, excluding conflict of
law provisions.  Nothing in this License Agreement shall be deemed to
create any relationship of agency, partnership, or joint venture
between BeOpen and Licensee.  This License Agreement does not grant
permission to use BeOpen trademarks or trade names in a trademark
sense to endorse or promote products or services of Licensee, or any
third party.  As an exception, the "BeOpen Python" logos available at
http://www.pythonlabs.com/logos.html may be used according to the
permissions granted on that web page.

7. By copying, installing or otherwise using the software, Licensee
agrees to be bound by the terms and conditions of this License
Agreement.


CNRI LICENSE AGREEMENT FOR PYTHON 1.6.1
---------------------------------------

1. This LICENSE AGREEMENT is between the Corporation for National
Research Initiatives, having an office at 1895 Preston White Drive,
Reston, VA 20191 ("CNRI"), and the Individual or Organization
("Licensee") accessing and otherwise using Python 1.6.1 software in
source or binary form and its associated documentation.

2. Subject to the terms and conditions of this License Agreement, CNRI
hereby grants Licensee a nonexclusive, royalty-free, world-wide
license to reproduce, analyze, test, perform and/or display publicly,
prepare derivative works, distribute, and otherwise use Python 1.6.1
alone or in any derivative version, provided, however, that CNRI's
License Agreement and CNRI's notice of copyright, i.e., "Copyright (c)
1995-2001 Corporation for National Research Initiatives; All Rights
Reserved" are retained in Python 1.6.1 alone or in any derivative
version prepared by Licensee.  Alternately, in lieu of CNRI's License
Agreement, Licensee may substitute the following text (omitting the
quotes): "Python 1.6.1 is made available subject to the terms and
conditions in CNRI's License Agreement.  This Agreement together with
Python 1.6.1 may be located on the internet using the following
unique, persistent identifier (known as a handle): 1895.22/1013.  This
Agreement may also be obtained from a proxy server on the internet
using the following URL: http://hdl.handle.net/1895.22/1013".

3. In the event Licensee prepares a derivative work that is based on
or incorporates Python 1.6.1 or any part thereof, and wants to make
the derivative work available to others as provided herein, then
Licensee hereby agrees to include in any such work a brief summary of
the changes made to Python 1.6.1.

4. CNRI is making Python 1.6.1 available to Licensee on an "AS IS"
basis.  CNRI MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, CNRI MAKES NO AND
DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON 1.6.1 WILL NOT
INFRINGE ANY THIRD PARTY RIGHTS.

5. CNRI SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
1.6.1 FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS
A RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON 1.6.1,
OR ANY DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.

6. This License Agreement will automatically terminate upon a material
breach of its terms and conditions.

7. This License Agreement shall be governed by the federal
intellectual property law of the United States, including without
limitation the federal copyright law, and, to the extent such
U.S. federal law does not apply, by the law of the Commonwealth of
Virginia, excluding Virginia's conflict of law provisions.
Notwithstanding the foregoing, with regard to derivative works based
on Python 1.6.1 that incorporate non-separable material that was
previously distributed under the GNU General Public License (GPL), the
law of the Commonwealth of Virginia shall govern this License
Agreement only as to issues arising under or with respect to
Paragraphs 4, 5, and 7 of this License Agreement.  Nothing in this
License Agreement shall be deemed to create any relationship of
agency, partnership, or joint venture between CNRI and Licensee.  This
License Agreement does not grant permission to use CNRI trademarks or
trade name in a trademark sense to endorse or promote products or
services of Licensee, or any third party.

8. By clicking on the "ACCEPT" button where indicated, or by copying,
installing or otherwise using Python 1.6.1, Licensee agrees to be
bound by the terms and conditions of this License Agreement.

        ACCEPT


CWI LICENSE AGREEMENT FOR PYTHON 0.9.0 THROUGH 1.2
--------------------------------------------------

Copyright (c) 1991 - 1995, Stichting Mathematisch Centrum Amsterdam,
The Netherlands.  All rights reserved.

Permission to use, copy, modify, and distribute this software and its
documentation for any purpose and without fee is hereby granted,
provided that the above copyright notice appear in all copies and that
both that copyright notice and this permission notice appear in
supporting documentation, and that the name of Stichting Mathematisch
Centrum or CWI not be used in advertising or publicity pertaining to
distribution of the software without specific, written prior
permission.

STICHTING MATHEMATISCH CENTRUM DISCLAIMS ALL WARRANTIES WITH REGARD TO
THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS, IN NO EVENT SHALL STICHTING MATHEMATISCH CENTRUM BE LIABLE
FOR ANY SPECIAL, INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

ZERO-CLAUSE BSD LICENSE FOR CODE IN THE PYTHON DOCUMENTATION
----------------------------------------------------------------------

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
