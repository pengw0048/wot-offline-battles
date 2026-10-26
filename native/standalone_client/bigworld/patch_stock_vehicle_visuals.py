#!/usr/bin/env python3
"""Restore the separate #1513 Device and World presentation update phases."""

import argparse
from pathlib import Path


def patch_engine(root):
    client = Path(root) / 'client'
    patches = {
        'device_app.cpp': [
            ('#include "pch.hpp"',
             '#include "pch.hpp"\n#include "stock_vehicle_visuals.hpp"'),
            ('    Moo::rc().nextFrame();\n',
             '    Moo::rc().nextFrame();\n'
             '    tickStockVehicleComponents( dGameTime );\n'),
            ('    ConnectionControl::instance().tick( dGameTime );\n\n'
             '    Moo::rc().disableResourcePreloads();',
             '    ConnectionControl::instance().tick( dGameTime );\n'
             '    tickStockVehicleComponents( dGameTime );\n\n'
             '    Moo::rc().disableResourcePreloads();')],
        'world_app.cpp': [
            ('#include "pch.hpp"', '#include "pch.hpp"\n#include "stock_vehicle_audio.hpp"'),
            ('#include "pch.hpp"',
             '#include "pch.hpp"\n#include "stock_vehicle_visuals.hpp"'),
            ('        GlobalEmbodiments::tick( dGameTime );\n',
             '        GlobalEmbodiments::tick( dGameTime );\n'
             '        tickStockVehicleTrackScrolls( App::instance().getGameTimeFrameStart() );\n'
             '        tickStockVehicleAudioTasks();\n')],
    }
    updates = []
    for name, replacements in patches.items():
        path = client / name
        source = path.read_text()
        for before, after in replacements:
            if before == '#include "pch.hpp"' and after.splitlines()[1] in source:
                continue
            if after in source:
                continue
            if source.count(before) != 1:
                raise ValueError('Pinned vehicle presentation anchor changed: ' + name)
            source = source.replace(before, after, 1)
        updates.append((path, source))
    for path, source in updates:
        path.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
