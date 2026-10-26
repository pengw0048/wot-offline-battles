#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "pyscript/script.hpp"

BW_BEGIN_NAMESPACE
class PyFilter;
class PyEntity;
extern int StockVehicleFilter_token;
void bindStockVehicleFilterOwner( PyFilter * filter, PyEntity * owner );
void tickStockVehicleFilterLagDetection();

// The original TrackScroll callback consumes presentation speed and writes
// only the overrides read by WGVehicleFilter::publishMotion (006eee00).
struct StockVehicleFilterVisualState
{
    float instantaneousSpeed;
    float instantaneousRotationSpeed;
    float averageSpeed;
    float averageRotationSpeed;
    float longitudinalSpeed;
    float strafeSpeed;
    float angularSpeed;
    float suspensionCompressionRate;
    unsigned leftContacts;
    unsigned rightContacts;
    bool strafing;
    bool attached;
};
bool readStockVehicleFilterVisualState( PyObject * filter,
    StockVehicleFilterVisualState & result );
bool setStockVehicleTrackOverrides( PyObject * filter, float leftSpeed,
    float rightSpeed, bool leftLocked, bool rightLocked );
BW_END_NAMESPACE
