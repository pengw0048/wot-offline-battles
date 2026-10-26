#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "cstdmf/bw_string.hpp"
#include "moo/graphics_settings.hpp"

BW_BEGIN_NAMESPACE
extern int StockGraphicsPresets_token;
bool initialiseStockGraphicsPresets();
bool stockGraphicsPresetsActive();
bool stockGraphicsPresetDesiredOption(const BW::string& label,int& option);
void setStockGraphicsPresetOverload(const BW::string& name);
void publishStockGraphicsPreferences(const Moo::GraphicsSetting::SettingsVector& settings);
BW_END_NAMESPACE
