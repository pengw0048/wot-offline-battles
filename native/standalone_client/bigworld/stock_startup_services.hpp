#pragma once

#include "pyscript/script.hpp"

BW_BEGIN_NAMESPACE
extern int StockStartupServices_token;

// Registers only the settings whose original consumers are Python-owned.
// This is a constituent of wg_initCustomSettings, not its replacement: the
// renderer/particle settings must also be restored before exporting that API.
bool initialiseStockScriptGraphicsSettings();
BW_END_NAMESPACE
