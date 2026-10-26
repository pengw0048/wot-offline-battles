#pragma once

#include "math/matrix.hpp"

BW_BEGIN_NAMESPACE
extern int StockWotModule_token;
struct StockAuxVehiclePhysicsData
{
    float yaw, pitch, roll, leftScroll, rightScroll, normalisedRPM;
};
StockAuxVehiclePhysicsData unpackStockAuxVehiclePhysicsData( uint64 packed );
Matrix computeStockStabilisedVehicleMatrix( const Matrix & source, uint64 packed );
BW_END_NAMESPACE
