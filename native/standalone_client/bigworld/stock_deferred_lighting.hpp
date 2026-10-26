#pragma once

#include "resmgr/datasection.hpp"
#include "math/vector3.hpp"

BW_BEGIN_NAMESPACE
struct StockDeferredLightingSettings
{
    float sphericalHarmonics[28] = {};
    bool loaded = false;
};

void packStockDeferredSphericalHarmonics( const Vector3 (&source)[9], float (&packed)[28] );
bool loadStockDeferredLightingSettings( DataSectionPtr environment, StockDeferredLightingSettings & settings );
void activateStockDeferredLightingSettings( const StockDeferredLightingSettings & settings );
void deactivateStockDeferredLightingSettings();
bool initialiseStockDeferredLightingConstants();
void finaliseStockDeferredLightingConstants();
BW_END_NAMESPACE
