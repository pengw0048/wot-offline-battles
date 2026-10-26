#pragma once

#include <Python.h>
#include "wot/vehicle_control.hpp"
#include "wot/vehicle_suspension.hpp"

// Source objects are the existing mod's actual producer results. Every parser
// is atomic: failure sets a Python exception and leaves the output unchanged.
bool parseOfflineVehicleParameters(PyObject* source,
    wot::offline_vehicle_control::Parameters& output);
bool parseOfflineSuspensionParameters(PyObject* source,
    wot::offline_vehicle_control::SuspensionParameters& output);
// Accept the original vehicle_physics module or its current __dict__, after
// apply_tuning. The module's uppercase constants remain the tuning owner.
bool parseOfflineVehicleTuning(PyObject* source,
    wot::offline_vehicle_control::Tuning& output);
