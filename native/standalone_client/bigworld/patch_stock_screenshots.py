#!/usr/bin/env python3
"""Restore #1513 screenshot save tasks and completion notification ownership."""

import argparse
from pathlib import Path


SAVE_TASK = '''    // #1513 9288f0 captures on the main thread, then 9243d0 saves
    // in the background and 924580 notifies Python on the main thread.
    if (!queueStockScreenshotSave(systemMemorySurface, d3dFromat, name,
            stockScreenshotCallback_))
        return BW::string();

    return name;'''

PUBLIC_SAVE = '''    BW::wstring wname;
    bw_utf8tow( name, wname );
    if (FAILED ( D3DXSaveSurfaceToFile(\x20
            wname.c_str(),
            d3dFromat,
            systemMemorySurface.pComObject(),
            NULL,
            NULL) ) )
    {
        ERROR_MSG( "Moo::RenderContext::screenShot - unable to save "
                   "surface to image file\\n" );
        return BW::string();
    }

    INFO_MSG( "Moo::RenderContext::screenShot - saved image %s\\n",
        name.c_str() );

    return name;'''


def replace(source, old, new, label):
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError('Pinned screenshot owner anchor changed: ' + label)
    return source.replace(old, new, 1)


def patch(root):
    root = Path(root)
    header = root / 'lib/moo/render_context.hpp'
    source = header.read_text()
    # Keep Python and the complete owner out of this widely included header.
    source = source.replace('#include "stock_screenshot_callback.hpp"\n', '')
    source = replace(source, '#include <stack>', '#include <stack>\n#include <memory>', 'shared owner declaration')
    if 'namespace wot { class StockScreenshotCallback; }' not in source:
        source = replace(source, 'BW_BEGIN_NAMESPACE',
            'namespace wot { class StockScreenshotCallback; }\n\nBW_BEGIN_NAMESPACE', 'opaque owner declaration')
    anchor = '\t\tconst BW::string\t\t\tscreenShot( const BW::string& fileExt = "bmp", const BW::string& fileName = "shot", bool autoNumber = true );'
    source = replace(source, anchor,
        anchor + '\n\t\tstd::shared_ptr<wot::StockScreenshotCallback> stockScreenshotCallback() const\n'
        '\t\t\t{ return stockScreenshotCallback_; }', 'owner accessor')
    source = source.replace(
        '\t\tstd::shared_ptr<wot::StockScreenshotCallback> stockScreenshotCallback_ =\n'
        '\t\t\tstd::make_shared<wot::StockScreenshotCallback>();',
        '\t\tstd::shared_ptr<wot::StockScreenshotCallback> stockScreenshotCallback_;')
    if 'std::shared_ptr<wot::StockScreenshotCallback> stockScreenshotCallback_;' not in source:
        source = replace(source, '\t\tEffectVisualContext*\teffectVisualContext_;',
            '\t\tEffectVisualContext*\teffectVisualContext_;\n'
            '\t\tstd::shared_ptr<wot::StockScreenshotCallback> stockScreenshotCallback_;', 'owner storage')
    header.write_text(source)

    implementation = root / 'lib/moo/render_context.cpp'
    source = implementation.read_text()
    source = replace(source, '#include "render_context.hpp"',
        '#include "render_context.hpp"\n#include "stock_screenshot_callback.hpp"', 'complete owner implementation')
    source = replace(source, '  effectVisualContext_( NULL )',
        '  effectVisualContext_( NULL ),\n'
        '  stockScreenshotCallback_(std::make_shared<wot::StockScreenshotCallback>())', 'complete owner initialization')
    source = replace(source, PUBLIC_SAVE, SAVE_TASK, 'background save boundary')
    source = replace(source, '                name = findName.str();',
        '                name = findName.str();\n'
        '                // Reserve the name while an earlier save is pending.\n'
        '                sequence++;', 'pending filename reservation')
    source = replace(source, 'RenderContext::~RenderContext()\n{',
        'RenderContext::~RenderContext()\n{\n    stockScreenshotCallback_->close();', 'owner destructor')
    implementation.write_text(source)

    script = root / 'client/script_bigworld.cpp'
    source = script.read_text()
    source = replace(source, '#include "script_bigworld.hpp"',
        '#include "script_bigworld.hpp"\n#include "stock_screenshot_callback.hpp"', 'capture declaration')
    source = replace(source, '\tMoo::rc().screenShot( format, fullName );',
        '\tqueueStockScreenshotCapture( format, fullName );', 'Python capture scheduling')
    script.write_text(source)

    app = root / 'client/app.cpp'
    source = app.read_text()
    source = replace(source,
        '\t\t\t\tBW::string fileName = Moo::rc().screenShot( ',
        '\t\t\t\tMoo::rc().screenShot( ', 'hotkey capture result')
    source = replace(source,
        '\t\t\t\tif( !fileName.empty() )\n\t\t\t\t{\n'
        '\t\t\t\t\tclientChatMsg( "Screenshot saved: " + BWResource::getFilename( fileName ) );\n'
        '\t\t\t\t}',
        '\t\t\t\t// Saved-file feedback is emitted by the completion callback.',
        'hotkey completion feedback')
    app.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
