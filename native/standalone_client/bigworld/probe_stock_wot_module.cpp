// Exercise the production module registry, Matrix conversion and DirectX math.
#include "cstdmf/cstdmf_windows.hpp"
#include "cstdmf/allocator.hpp"
#include "cstdmf/log_msg.hpp"
#include "stock_wot_module.hpp"
#include "pyscript/script.hpp"
#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>

namespace
{
void require( bool value, const char * message )
{
    if (!value) throw std::runtime_error( message );
}
BW::uint32 bits( float value )
{
    BW::uint32 result;
    std::memcpy( &result, &value, sizeof( result ) );
    return result;
}
}

int main()
{
    BW_SYSTEMSTAGE_MAIN();
    BW::LogMsg::automatedTest( true );
    SetErrorMode( SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX );
    int outcome = 1;
    Py_NoSiteFlag = 1;
    Py_Initialize();
    BW::Script::runInitTimeJobs();
    try
    {
        const BW::StockAuxVehiclePhysicsData zero = BW::unpackStockAuxVehiclePhysicsData( 0 );
        require( bits( zero.yaw ) == 0xc0490fdb && bits( zero.pitch ) == 0xbfc90fdb &&
            bits( zero.roll ) == 0xc0490fdb && zero.leftScroll == -15.f &&
            zero.rightScroll == -15.f && zero.normalisedRPM == 0.f, "Zero field decode changed" );
        BW::Matrix source;
        source.setScale( 2.f, 3.f, 4.f );
        source( 3, 0 ) = 101.f; source( 3, 1 ) = -202.f; source( 3, 2 ) = 303.f;
        source( 0, 3 ) = 5.f; source( 3, 3 ) = 7.f;
        PyObject * module = PyImport_ImportModule( "WoT" );
        require( module != NULL, "WoT registry did not create the production module" );
        PyObject * provider = BW::Script::getData( source );
        require( provider != NULL, "Cannot construct the real Matrix provider" );
        PyObject * scope = PyModule_GetDict( PyImport_AddModule( "__main__" ) );
        require( PyDict_SetItemString( scope, "source", provider ) == 0, "Cannot expose Matrix" );
        const Py_ssize_t refs = Py_REFCNT( provider );
        const BW::uint64 packed = 0xe7c53a65432abcdeULL;
        PyObject * number = PyLong_FromUnsignedLongLong( packed );
        require( number != NULL, "Cannot construct uint64" );
        PyObject * result = PyObject_CallMethod( module, const_cast<char *>( "computeStabilisedVehicleMatrixU64" ),
            const_cast<char *>( "OO" ), provider, number );
        Py_DECREF( number );
        require( result && result != provider, "Matrix output does not own a new snapshot" );
        require( Py_REFCNT( provider ) == refs, "Module retained its MatrixProvider" );
        BW::Matrix actual, original;
        require( BW::Script::setData( result, actual ) == 0 && BW::Script::setData( provider, original ) == 0,
            "Real Matrix conversion failed" );
        require( std::memcmp( &source, &original, sizeof( source ) ) == 0, "Input Matrix was mutated" );
        for (unsigned axis = 0; axis < 3; ++axis)
        {
            require( actual( 3, axis ) == source( 3, axis ) && actual( axis, 3 ) == 0.f,
                "Output translation or perspective components changed" );
            float length = 0.f;
            for (unsigned component = 0; component < 3; ++component)
                length += actual( axis, component ) * actual( axis, component );
            require( std::fabs( length - 1.f ) < 0.000002f, "Output retained input scale" );
        }
        require( actual( 3, 3 ) == 1.f, "Output is not affine" );
        Py_DECREF( result ); Py_DECREF( provider ); Py_DECREF( module );
        require( PyRun_SimpleString( R"PY(
import WoT
assert len(WoT.unpackAuxVehiclePhysicsData(0))==6
assert all(type(v) is float for v in WoT.unpackAuxVehiclePhysicsData(0))
assert WoT.unpackAuxVehiclePhysicsData(True)==WoT.unpackAuxVehiclePhysicsData(1)
assert WoT.unpackAuxVehiclePhysicsData(1.9)==WoT.unpackAuxVehiclePhysicsData(1)
assert WoT.unpackAuxVehiclePhysicsData(0xffffffffffffffffL)[5]>1.19
for value,kind in ((-1,ValueError),(-1L,TypeError),(1L<<64,TypeError),([],TypeError),('1',TypeError),(None,TypeError)):
    for function,args in ((WoT.unpackAuxVehiclePhysicsData,(value,)),(WoT.computeStabilisedVehicleMatrixU64,(source,value))):
        try:function(*args)
        except kind:pass
        else:raise AssertionError('Invalid uint64 accepted')
for function,args in ((WoT.unpackAuxVehiclePhysicsData,()),(WoT.unpackAuxVehiclePhysicsData,(0,1)),
                      (WoT.computeStabilisedVehicleMatrixU64,(source,)),(WoT.computeStabilisedVehicleMatrixU64,(source,0,1)),
                      (WoT.computeStabilisedVehicleMatrixU64,(None,0))):
    try:function(*args)
    except TypeError:pass
    else:raise AssertionError('Invalid arity/provider accepted')
)PY" ) == 0, "Production Python conversion/arity contract failed" );
        std::cout << "Real WoT module registration, uint64 conversion, Matrix ownership and DirectX rotation passed" << std::endl;
        outcome = 0;
    }
    catch (const std::exception & error)
    {
        if (PyErr_Occurred()) PyErr_Print();
        std::cerr << "WoT module probe failed: " << error.what() << std::endl;
    }
    BW::Script::runFiniTimeJobs();
    Py_Finalize();
    BW::LogMsg::fini();
    return outcome;
}
