#ifndef WOT_STOCK_WOT_MODULE_PORTABLE
#include "pch.hpp"
#include "stock_wot_module.hpp"
#include "pyscript/script.hpp"
#endif
#include <cstring>

BW_BEGIN_NAMESPACE
int StockWotModule_token = 1;

namespace
{
float floatFromBits( uint32 bits )
{
    float value;
    std::memcpy( &value, &bits, sizeof( value ) );
    return value;
}

float unpackField( uint32 field, uint32 scaleBits, uint32 offsetBits )
{
    // #1513 f5b920 uses separate MULSS and SUBSS operations. Store the
    // product before subtracting so an FMA cannot change the rounded result.
    volatile float product = static_cast<float>( field ) * floatFromBits( scaleBits );
    return product - floatFromBits( offsetBits );
}
}

StockAuxVehiclePhysicsData unpackStockAuxVehiclePhysicsData( uint64 packed )
{
    // These binary32 constants are the original PE operands, including the
    // asymmetric yaw/roll and pitch denominators (32768/8192 and 4095).
    StockAuxVehiclePhysicsData result;
    result.yaw = unpackField( static_cast<uint32>( packed & 0x7fff ), 0x39490fdb, 0x40490fdb );
    result.pitch = unpackField( static_cast<uint32>( (packed >> 15) & 0xfff ), 0x3a491c6d, 0x3fc90fdb );
    result.roll = unpackField( static_cast<uint32>( (packed >> 27) & 0x1fff ), 0x3a490fdb, 0x40490fdb );
    result.leftScroll = unpackField( static_cast<uint32>( (packed >> 40) & 0xff ), 0x3e34b4b5, 0x41700000 );
    result.rightScroll = unpackField( static_cast<uint32>( (packed >> 48) & 0xff ), 0x3e34b4b5, 0x41700000 );
    result.normalisedRPM = static_cast<float>( packed >> 56 ) * floatFromBits( 0x3b9a33ce );
    return result;
}

Matrix computeStockStabilisedVehicleMatrix( const Matrix & source, uint64 packed )
{
    const StockAuxVehiclePhysicsData data = unpackStockAuxVehiclePhysicsData( packed );
    Matrix result;
    D3DXMatrixRotationYawPitchRoll( &result, data.yaw, data.pitch, data.roll );
    // f5b110 preserves only the provider's translation, not its rotation,
    // scale or perspective components. The returned Matrix owns a snapshot.
    result( 3, 0 ) = source( 3, 0 );
    result( 3, 1 ) = source( 3, 1 );
    result( 3, 2 ) = source( 3, 2 );
    result( 3, 3 ) = 1.f;
    return result;
}

static PyObject * py_computeStabilisedVehicleMatrixU64( PyObject * args )
{
    PyObject * provider;
    PyObject * value;
    if (!PyArg_ParseTuple( args, "OO:computeStabilisedVehicleMatrixU64", &provider, &value )) return NULL;
    Matrix source;
    uint64 packed;
    if (Script::setData( provider, source, "() argument 1" ) ||
        Script::setData( value, packed, "() argument 2" )) return NULL;
    return Script::getData( computeStockStabilisedVehicleMatrix( source, packed ) );
}
PY_MODULE_FUNCTION( computeStabilisedVehicleMatrixU64, WoT )

static PyObject * py_unpackAuxVehiclePhysicsData( PyObject * args )
{
    PyObject * value;
    if (!PyArg_ParseTuple( args, "O:unpackAuxVehiclePhysicsData", &value )) return NULL;
    uint64 packed;
    if (Script::setData( value, packed, "() argument 1" )) return NULL;
    const StockAuxVehiclePhysicsData data = unpackStockAuxVehiclePhysicsData( packed );
    return Py_BuildValue( "(ffffff)", data.yaw, data.pitch, data.roll,
        data.leftScroll, data.rightScroll, data.normalisedRPM );
}
PY_MODULE_FUNCTION( unpackAuxVehiclePhysicsData, WoT )
BW_END_NAMESPACE
