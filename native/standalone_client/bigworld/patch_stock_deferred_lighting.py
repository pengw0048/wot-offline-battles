#!/usr/bin/env python3
"""Attach recovered environment SH and BRDF inputs to their public owners."""
import argparse
from pathlib import Path


def replace_once(text, old, new, label, marker=None):
    if (marker or new) in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError('Unexpected public engine source for ' + label)
    return text.replace(old, new, 1)


READER = r'''// #1513's PBS settings are loaded from the actual skyGradientDome root.
void EnviroMinder::loadStockDeferredLighting(const DataSectionPtr& data, bool loadFromExternal)
{
    const BW::string resource = data->readString("skyGradientDome");
    DataSectionPtr source = (!resource.empty() && loadFromExternal) ?
        BWResource::openSection(resource) : data;
    loadStockDeferredLightingSettings(source, stockDeferredLightingSettings_);
    if (s_activatedEM_ == this)
        activateStockDeferredLightingSettings(stockDeferredLightingSettings_);
}

'''


def apply(root):
    root = Path(root)
    changes = {}

    def edit(relative, substitutions):
        path = root / relative
        text = path.read_text()
        for substitution in substitutions:
            text = replace_once(text, *substitution)
        changes[path] = text

    edit('lib/romp/enviro_minder.hpp', [
        ('#define ENVIRO_MINDER_HPP', '#define ENVIRO_MINDER_HPP\n#include "stock_deferred_lighting.hpp"', 'lighting owner include', '#include "stock_deferred_lighting.hpp"'),
        ('void loadHDR(const DataSectionPtr& data, bool loadFromExternal);',
         'StockDeferredLightingSettings stockDeferredLightingSettings_;\n    void loadStockDeferredLighting(const DataSectionPtr& data, bool loadFromExternal);\n    void loadHDR(const DataSectionPtr& data, bool loadFromExternal);', 'environment SH owner', 'StockDeferredLightingSettings stockDeferredLightingSettings_;'),
    ])
    edit('lib/romp/enviro_minder.cpp', [
        ('        loadTimeOfDay(pDS, loadFromExternal);',
         '        loadTimeOfDay(pDS, loadFromExternal);\n        loadStockDeferredLighting(pDS, loadFromExternal);', 'load real environment SH', '        loadStockDeferredLighting(pDS, loadFromExternal);'),
        ('    s_activatedEM_ = this;',
         '    s_activatedEM_ = this;\n    activateStockDeferredLightingSettings(stockDeferredLightingSettings_);', 'publish active environment SH', '\n    activateStockDeferredLightingSettings(stockDeferredLightingSettings_);'),
        ('    s_activatedEM_ = NULL;',
         '    deactivateStockDeferredLightingSettings();\n    s_activatedEM_ = NULL;', 'retire previous environment SH', '    deactivateStockDeferredLightingSettings();'),
        ('void EnviroMinder::loadHDR(const DataSectionPtr& data, bool loadFromExternal)',
         READER + 'void EnviroMinder::loadHDR(const DataSectionPtr& data, bool loadFromExternal)', 'environment SH reader', 'void EnviroMinder::loadStockDeferredLighting('),
    ])
    edit('lib/moo/deferred_lights_manager.cpp', [
        ('#include "deferred_lights_manager.hpp"',
         '#include "deferred_lights_manager.hpp"\n#include "stock_deferred_lighting.hpp"', 'lighting producer include', '#include "stock_deferred_lighting.hpp"'),
        ('void LightsManager::createManagedObjects()\n{\n    BW_GUARD;',
         'void LightsManager::createManagedObjects()\n{\n    BW_GUARD;\n    if (!initialiseStockDeferredLightingConstants())\n        ERROR_MSG("Deferred lighting: original shared inputs could not be initialised\\n");', 'managed lighting inputs', '    if (!initialiseStockDeferredLightingConstants())'),
        ('void LightsManager::deleteManagedObjects()\n{\n    BW_GUARD;',
         'void LightsManager::deleteManagedObjects()\n{\n    BW_GUARD;\n    finaliseStockDeferredLightingConstants();', 'managed lighting teardown', '    finaliseStockDeferredLightingConstants();'),
    ])
    header = Path(__file__).with_name('stock_deferred_lighting.hpp').read_text()
    # Validate all anchors and inputs before publishing any engine source.
    for path, text in changes.items():
        path.write_text(text)
    (root / 'lib/stock_deferred_lighting.hpp').write_text(header)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    apply(parser.parse_args().engine_root)
