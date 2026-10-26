#pragma once

#include "cstdmf/stdmf.hpp"

BW_BEGIN_NAMESPACE
extern int StockGraphicsSettingsConsumers_token;
bool initialiseStockGraphicsSettingsConsumers();
void finaliseStockGraphicsSettingsConsumers();
bool drawStockBinoculars();
bool stockSniperFloraVisible();
bool initialiseStockSniperFloraSetting();
BW_END_NAMESPACE
