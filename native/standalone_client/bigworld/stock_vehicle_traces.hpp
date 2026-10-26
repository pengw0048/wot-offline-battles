#pragma once

#include "cstdmf/bw_namespace.hpp"

BW_BEGIN_NAMESPACE
extern int StockVehicleTraces_token;
void tickStockVehicleTraces();
bool initialiseStockVehicleTracesSetting();
bool initialiseStockVehicleTraceRenderer();
void finaliseStockVehicleTraceRenderer();
void tickStockVehicleTraceRenderer(float elapsed);
void setStockVehicleTraceQuality(unsigned option);
bool drawStockVehicleTraces();
BW_END_NAMESPACE
