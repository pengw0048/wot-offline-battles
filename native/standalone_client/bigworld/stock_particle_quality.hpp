#pragma once

#include "pyscript/script.hpp"
#include "wot/stock_particle_quality.hpp"

BW_BEGIN_NAMESPACE
// The actual App configuration path, resources.xml/system/graphicsSettingsXML.
// This restores profile production; renderer/particle consumers attach to the
// same state and must be restored before exporting wg_initCustomSettings.
wot::StockParticleQuality & stockParticleQuality();
bool initialiseStockParticleQualityProfiles();
BW_END_NAMESPACE
