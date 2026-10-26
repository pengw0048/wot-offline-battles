#pragma once

#include "resmgr/datasection.hpp"
#include "wot/stock_fog.hpp"

BW_BEGIN_NAMESPACE

// Reads the original environment root's Fog child. Missing fields retain the
// corresponding native constructor values, exactly as 0x00a55bd0 does.
void loadStockFogSettings(DataSectionPtr environment,
                          wot::StockFogSettings &settings);

BW_END_NAMESPACE
