#!/usr/bin/env python3
"""Attach the actual trace manager to world updates and deferred decal draws."""
import argparse
from pathlib import Path


def apply(root):
    root = Path(root)
    pending = {}

    def edit(relative, old, new, present=None):
        text = pending.get(relative)
        if text is None:
            text = (root / relative).read_text()
        if (present or new) in text:
            return
        if text.count(old) != 1:
            raise ValueError('Vehicle traces owner anchor changed: ' + relative + ': ' + repr(old))
        pending[relative] = text.replace(old, new, 1)

    cpp = 'lib/moo/deferred_pipeline.cpp'
    edit(cpp, '#include "deferred_pipeline.hpp"',
         '#include "deferred_pipeline.hpp"\n#include "stock_vehicle_traces.hpp"',
         '#include "stock_vehicle_traces.hpp"')
    edit(cpp, '    success &= m_lightsManager->init();',
         '    success &= m_lightsManager->init();\n'
         '    success &= initialiseStockVehicleTraceRenderer();',
         '    success &= initialiseStockVehicleTraceRenderer();')
    edit(cpp, 'DeferredPipeline::~DeferredPipeline()\n{',
         'DeferredPipeline::~DeferredPipeline()\n{\n    finaliseStockVehicleTraceRenderer();',
         '    finaliseStockVehicleTraceRenderer();')
    edit(cpp, '    m_decalsManager->tick(dt);',
         '    m_decalsManager->tick(dt);\n    tickStockVehicleTraceRenderer(dt);',
         '    tickStockVehicleTraceRenderer(dt);')
    edit(cpp, '            m_decalsManager->setQualityOption(option);',
         '            m_decalsManager->setQualityOption(option);\n'
         '            setStockVehicleTraceQuality(static_cast<unsigned>(option));',
         '            setStockVehicleTraceQuality(static_cast<unsigned>(option));')
    edit(cpp, '    m_decalsManager->draw();',
         '    m_decalsManager->draw();\n'
         '    if (!drawStockVehicleTraces())\n'
         '        ERROR_MSG("Vehicle traces: deferred draw failed\\n");',
         '    if (!drawStockVehicleTraces())')

    cpp = 'client/world_app.cpp'
    edit(cpp, '#include "pch.hpp"', '#include "pch.hpp"\n#include "stock_vehicle_traces.hpp"',
         '#include "stock_vehicle_traces.hpp"')
    # Keep the GlobalEmbodiments line and its existing presentation consumers
    # intact. The component samples the final provider pose in this same scope.
    edit(cpp, '    }\n}\n\n\nvoid WorldApp::inactiveTick(',
         '        tickStockVehicleTraces();\n    }\n}\n\n\nvoid WorldApp::inactiveTick(',
         '        tickStockVehicleTraces();')

    application = Path(__file__).resolve().parent
    headers = ((application / 'stock_vehicle_traces.hpp', root / 'lib/stock_vehicle_traces.hpp'),
               (application.parent / 'include/wot/stock_vehicle_traces.hpp',
                root / 'lib/wot/stock_vehicle_traces.hpp'))
    sources = [(destination, source.read_text()) for source, destination in headers]
    for relative, text in pending.items():
        (root / relative).write_text(text)
    for destination, text in sources:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    apply(parser.parse_args().engine_root)
