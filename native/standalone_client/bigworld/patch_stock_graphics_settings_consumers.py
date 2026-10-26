"""Restore the reviewed binoculars owner and its real sniper Flora draw gate."""
from pathlib import Path
import argparse


def insert_once(text, marker, addition):
    if addition in text:
        return text
    if text.count(marker) != 1:
        raise RuntimeError('Graphics consumers anchor is not unique: ' + repr(marker))
    return text.replace(marker, marker + addition)


def patch(root):
    root = Path(root)
    path = root / 'client/gui_app.cpp'
    text = path.read_text()
    text = insert_once(text, '#include "gui_app.hpp"',
                       '\n#include "stock_graphics_settings_consumers.hpp"')
    text = insert_once(text, '    // access simple gui',
                       '\n    if (!initialiseStockGraphicsSettingsConsumers()) return false;')
    text = insert_once(text, 'void GUIApp::fini()\n{\n    BW_GUARD;',
                       '\n    finaliseStockGraphicsSettingsConsumers();')
    # 006338c9 draws binoculars before GUI composition, after scene/post-FX.
    text = insert_once(text, '    rp().beginGUIDraw();',
                       '\n    if (!drawStockBinoculars())\n'
                       '        ERROR_MSG("Binoculars: lens draw failed\\n");')
    path.write_text(text)

    path = root / 'lib/romp/flora.cpp'
    text = path.read_text()
    text = insert_once(text, '#include "flora.hpp"',
                       '\n#include "stock_graphics_settings_consumers.hpp"')
    # Do not gate update/population, density, or debug bounding-box drawing.
    text = insert_once(text,
        '\tif ( !pRenderer_ ||\n\t\t !s_enabled_ ||',
        '\n\t\t !stockSniperFloraVisible() ||')
    path.write_text(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bigworld_root', type=Path)
    patch(parser.parse_args().bigworld_root)
