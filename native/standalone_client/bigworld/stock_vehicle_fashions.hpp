#pragma once

#include "pyscript/script.hpp"

BW_BEGIN_NAMESPACE
class PyModel;
extern int StockVehicleFashions_token;

// The compound adapter uses the public PyModel fashion attachment protocol.
// None removes a fashion; every other accepted object is an actual native
// vehicle material fashion, never a generic Python callback.
bool isStockVehicleFashion( PyObject * object );
bool setStockVehiclePartFashion( PyModel * model, PyObject * object );
// Original wg_initCustomSettings loads the shared material exclusion rules.
void initialiseStockVehicleCamouflageConfig();
BW_END_NAMESPACE
