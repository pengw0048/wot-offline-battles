#!/usr/bin/env python3
"""Restore original graphics-preset publication and explicit live application."""
import argparse
from pathlib import Path


SNAPSHOT_AND_APPLY = r'''GraphicsSetting::SettingsVector GraphicsSetting::stockCurrentSettings()
{
    SettingsVector result;
    for (const auto& setting : settings())
        if (setting->label() != "presets")
            result.push_back(std::make_pair(setting->label(), setting->activeOption()));
    std::sort(result.begin(), result.end());
    return result;
}

void GraphicsSetting::captureStockPresetBaseline()
{
    getSettingsData().s_oldSettings = stockCurrentSettings();
}

void GraphicsSetting::applyStockPreset(const SettingsVector& input)
{
    // 91ce80 snapshots live settings once, then forces each listed callback.
    // Unknown labels are ignored here; publication remains a separate step.
    captureStockPresetBaseline();
    for (const auto& entry : input)
    {
        GraphicsSettingPtr setting = getFromLabel(entry.first);
        if (setting && !setting->options().empty())
            setting->selectOption(static_cast<int>(entry.second), true);
    }
}

'''


def apply(root):
    root = Path(root)
    pending = {}

    def edit(name, old, new, present=None):
        text = pending.get(name)
        if text is None:
            text = (root/name).read_text()
        if (present or new) in text:
            return
        if text.count(old) != 1:
            raise ValueError('Graphics presets owner anchor changed: ' + name + ': ' + repr(old))
        pending[name] = text.replace(old, new, 1)

    cpp = 'lib/moo/graphics_settings.cpp'
    edit(cpp, '#include "graphics_settings.hpp"',
         '#include "graphics_settings.hpp"\n#include "stock_graphics_presets.hpp"',
         '#include "stock_graphics_presets.hpp"')
    edit(cpp, '\tGraphicsSetting::commitSettings(tempSettings);',
         '\tpublishStockGraphicsPreferences(tempSettings);\n\tGraphicsSetting::commitSettings(tempSettings);')
    edit(cpp, '\tStringIntMap::const_iterator setIt = latentSettings.find(label);',
         '\tStringIntMap::const_iterator setIt = latentSettings.find(label);\n'
         '    int stockDesiredOption = 0;\n'
         '    const bool stockDesired = stockGraphicsPresetDesiredOption(label, stockDesiredOption);\n'
         '    if (stockDesired)\n'
         '    {\n'
         '        // 91c320 reads desired state without invoking the callback.\n'
         '        setting->activeOption_ = stockDesiredOption;\n'
         '        optionInSetting = stockDesiredOption;\n'
         '    }')
    edit(cpp, '\telse if (setIt != latentSettings.end())',
         '\telse if (!stockDesired && setIt != latentSettings.end())')
    edit(cpp, 'void GraphicsSetting::commitSettings( SettingsVector& _settings )',
         SNAPSHOT_AND_APPLY + 'void GraphicsSetting::commitSettings( SettingsVector& _settings )',
         'GraphicsSetting::SettingsVector GraphicsSetting::stockCurrentSettings()')
    edit(cpp, '''bool GraphicsSetting::needsRestart()
{
\treturn getSettingsData().s_needsRestart;
}''', '''bool GraphicsSetting::needsRestart()
{
    if (stockGraphicsPresetsActive())
    {
        // 91eeb0/91d010 compare the last apply snapshot, not a sticky flag.
        for (const auto& previous : getSettingsData().s_oldSettings)
        {
            GraphicsSettingPtr setting = getFromLabel(previous.first);
            if (setting && setting->activeOption() != static_cast<int>(previous.second) &&
                setting->requiresRestart()) return true;
        }
        return false;
    }
\treturn getSettingsData().s_needsRestart;
}''')
    header = 'lib/moo/graphics_settings.hpp'
    edit(header, '\tstatic void revertSettings();',
         '\tstatic void revertSettings();\n'
         '\tstatic SettingsVector stockCurrentSettings();\n'
         '\tstatic void captureStockPresetBaseline();\n'
         '\tstatic void applyStockPreset(const SettingsVector& settings);')

    cpp = 'client/bw_winmain.cpp'
    edit(cpp, '#include "bw_winmain.hpp"',
         '#include "bw_winmain.hpp"\n#include "stock_graphics_presets.hpp"',
         '#include "stock_graphics_presets.hpp"')
    edit(cpp, '\t\t\telse if (arg == "--config" || arg == "-c")',
         '\t\t\telse if (arg == "--presetOverload")\n'
         '\t\t\t{\n\t\t\t\tsetStockGraphicsPresetOverload(arg2);\n\t\t\t}\n'
         '\t\t\telse if (arg == "--config" || arg == "-c")',
         '\t\t\telse if (arg == "--presetOverload")')

    application = Path(__file__).resolve().parent
    copies = ((application/'stock_graphics_presets.hpp',root/'lib/stock_graphics_presets.hpp'),
              (application.parent/'include/wot/stock_graphics_presets.hpp',root/'lib/wot/stock_graphics_presets.hpp'))
    contents = [(destination,source.read_text()) for source,destination in copies]
    for name, text in pending.items():
        (root/name).write_text(text)
    for destination, text in contents:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root',type=Path)
    apply(parser.parse_args().engine_root)
