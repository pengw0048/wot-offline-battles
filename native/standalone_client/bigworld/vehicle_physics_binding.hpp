#pragma once

#include "pyscript/pyobject_plus.hpp"

namespace wot
{
class VehiclePhysics;
}

BW_BEGIN_NAMESPACE

extern int StockVehiclePhysics_token;

// The returned core belongs to the Python object. A caller retaining it must
// also retain that object, as the stock filter and simulator do.
// A type mismatch sets TypeError and returns NULL.
wot::VehiclePhysics * unwrapStockVehiclePhysics( PyObject * object );

BW_END_NAMESPACE
