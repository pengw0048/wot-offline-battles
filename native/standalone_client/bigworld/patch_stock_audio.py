#!/usr/bin/env python3
"""Connect stock Wwise lifetime to the pinned application's stable owners."""

import argparse
from pathlib import Path


def patch_engine(root):
    client = Path(root) / 'client'
    patches = {
        'device_app.cpp': [
            ('#include "pch.hpp"', '#include "pch.hpp"\n#include "stock_vehicle_audio.hpp"'),
            ('#include "pch.hpp"', '#include "pch.hpp"\n#include "stock_audio.hpp"'),
            ('    // 4. Sound\n#if FMOD_SUPPORT\n    soundEnabled_',
             '    // 4. Sound\n'
             '    if (configSection->readBool("soundMgr/enabled", false) &&\n'
             '        !initialiseStockAudio(configSection, s_hWnd_))\n'
             '    {\n'
             '        ERROR_MSG("DeviceApp::init: Wwise initialisation failed\\n");\n'
             '        return false;\n'
             '    }\n#if FMOD_SUPPORT\n    soundEnabled_'),
            ('    Waters::instance().checkVolumes();\n',
             '    Waters::instance().checkVolumes();\n'
             '    tickStockAudio(\n'
             '        CameraApp::instance().clientCamera().camera()->invView(),\n'
             '        dRenderTime_);\n'),
            ('void DeviceApp::fini()\n{\n    BW_GUARD;\n',
             'void DeviceApp::fini()\n{\n    BW_GUARD;\n'
             '    finiStockVehicleAudio();\n    finiStockAudio();\n')],
        'script_app.cpp': [
            ('#include "pch.hpp"', '#include "pch.hpp"\n#include "stock_vehicle_audio.hpp"'),
            ('#include "pch.hpp"', '#include "pch.hpp"\n#include "stock_audio.hpp"'),
            ('\tBigWorldClientScript::fini();',
             '\tfiniStockVehicleAudio();\n\tfiniStockAudio();\n\tBigWorldClientScript::fini();')],
    }
    for name, replacements in patches.items():
        path = client / name
        source = path.read_text()
        for old, new in replacements:
            if old == '#include "pch.hpp"' and new.splitlines()[1] in source:
                continue
            if new in source:
                continue
            if source.count(old) != 1:
                raise ValueError('Pinned audio lifecycle anchor changed: ' + name)
            source = source.replace(old, new, 1)
        path.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
