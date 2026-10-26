#pragma once

#include "cstdmf/bw_namespace.hpp"

BW_BEGIN_NAMESPACE
class PyFilter;
class PyEntity;
extern int StockVehicleFilter_token;
void bindStockVehicleFilterOwner( PyFilter * filter, PyEntity * owner );
void tickStockVehicleFilterLagDetection();
BW_END_NAMESPACE
