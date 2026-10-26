#pragma once

#include <Python.h>
#include "wot/vehicle_physics.hpp"

// Parse the original physics_shared.configurePhysics payload. The output is
// published only after the complete configuration has been read successfully.
bool parseStockVehiclePhysicsConfiguration(
    PyObject * source, wot::VehiclePhysicsConfiguration & output );
