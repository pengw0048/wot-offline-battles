#include "pch.hpp"
#include "stock_graphics_initialization.hpp"
#include "stock_vehicle_fashions.hpp"

#include "romp/hdr_support.hpp"

BW_BEGIN_NAMESPACE
void initialiseStockGraphicsConfiguration()
{
    // 009725f9 -> 00a6e5c0(1) writes the real renderer-wide enable flag,
    // including when the current forward pipeline has no HDRSupport object.
    HDRSupport::setGlobalEnabled( true );
    initialiseStockVehicleCamouflageConfig();
}
BW_END_NAMESPACE
