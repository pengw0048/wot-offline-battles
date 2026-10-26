#include "pch.hpp"

#include "pyscript/script.hpp"
#include "pyscript/script_math.hpp"
#include "wot/gun_ballistics.hpp"

#include <cmath>
#include <exception>

BW_BEGIN_NAMESPACE

namespace
{
wot::Vector3 portable( const Vector3 & value )
{
    return { value.x, value.y, value.z };
}

bool finite( const Vector3 & value )
{
    return std::isfinite( value.x ) && std::isfinite( value.y ) && std::isfinite( value.z );
}

bool readCurve( PyObject * object, wot::GunPitchCurve & result,
    const char * argument )
{
    PyObjectPtr sequence( PySequence_Fast( object, argument ), PyObjectPtr::STEAL_REFERENCE );
    if (!sequence)
        return false;
    const Py_ssize_t count = PySequence_Fast_GET_SIZE( sequence.get() );
    if (count < 2)
    {
        PyErr_SetString( PyExc_ValueError, "Gun pitch curve needs at least two Vector2 nodes" );
        return false;
    }
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        Vector2 point;
        if (Script::setData( PySequence_Fast_GET_ITEM( sequence.get(), index ), point, argument ) != 0)
            return false;
        if (!std::isfinite( point.x ) || !std::isfinite( point.y ))
        {
            PyErr_SetString( PyExc_ValueError, "Gun pitch curve nodes must be finite" );
            return false;
        }
        result.push_back( { point.x, point.y } );
    }
    return true;
}

PyObject * failure( const std::exception & error )
{
    PyErr_SetString( PyExc_ValueError, error.what() );
    return NULL;
}
} // namespace

PyObject * py_wg_calcGunPitchLimits( PyObject * args )
{
    if (PyTuple_Size( args ) != 3)
    {
        PyErr_SetString( PyExc_TypeError, "wg_calcGunPitchLimits expects float, Vector2 sequence, Vector2 sequence" );
        return NULL;
    }
    float yaw;
    wot::GunPitchCurve minimum, maximum;
    if (Script::setData( PyTuple_GET_ITEM( args, 0 ), yaw, "wg_calcGunPitchLimits argument 1" ) != 0 ||
        !readCurve( PyTuple_GET_ITEM( args, 1 ), minimum, "wg_calcGunPitchLimits argument 2" ) ||
        !readCurve( PyTuple_GET_ITEM( args, 2 ), maximum, "wg_calcGunPitchLimits argument 3" ))
        return NULL;
    try
    {
        const auto result = wot::gun_pitch_limits( yaw, minimum, maximum );
        return Script::getData( Vector2( result[0], result[1] ) );
    }
    catch (const std::exception & error) { return failure( error ); }
}
PY_MODULE_FUNCTION( wg_calcGunPitchLimits, BigWorld )

PyObject * py_wg_getShotAngles( PyObject * args )
{
    const Py_ssize_t count = PyTuple_Size( args );
    if (count != 8 && count != 9)
    {
        PyErr_SetString( PyExc_TypeError, "wg_getShotAngles expects 8 arguments and optional adjust bool" );
        return NULL;
    }
    Vector3 turret, gun, target;
    Matrix vehicle;
    float speed, gravity, currentYaw, currentPitch;
    bool adjust = true;
    if (Script::setData( PyTuple_GET_ITEM( args, 0 ), turret, "wg_getShotAngles argument 1" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 1 ), gun, "wg_getShotAngles argument 2" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 2 ), vehicle, "wg_getShotAngles argument 3" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 3 ), speed, "wg_getShotAngles argument 4" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 4 ), gravity, "wg_getShotAngles argument 5" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 5 ), currentYaw, "wg_getShotAngles argument 6" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 6 ), currentPitch, "wg_getShotAngles argument 7" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 7 ), target, "wg_getShotAngles argument 8" ) != 0 ||
        (count == 9 && Script::setData( PyTuple_GET_ITEM( args, 8 ), adjust,
            "wg_getShotAngles argument 9" ) != 0))
        return NULL;
    bool finiteMatrix = true;
    for (int row = 0; row < 4; ++row)
        for (int column = 0; column < 4; ++column)
            finiteMatrix = finiteMatrix && std::isfinite( vehicle( row, column ) );
    if (!finite( turret ) || !finite( gun ) || !finite( target ) || !finiteMatrix ||
        !std::isfinite( speed ) || speed <= 0.f || !std::isfinite( gravity ) ||
        !std::isfinite( currentYaw ) || !std::isfinite( currentPitch ))
    {
        PyErr_SetString( PyExc_ValueError, "Shot angle geometry and positive speed must be finite" );
        return NULL;
    }
    Matrix inverse;
    // Preserve the original D3DX 4x4 inversion and its singular return path.
    // Public Matrix::invert instead assumes 3x4 and applies an epsilon test.
    if (!D3DXMatrixInverse( reinterpret_cast<D3DXMATRIX *>( &inverse ), NULL,
            reinterpret_cast<const D3DXMATRIX *>( &vehicle ) ))
        return Py_BuildValue( "(ff)", currentYaw, currentPitch );
    D3DXVECTOR4 localTarget, worldPivot;
    D3DXVec3Transform( &localTarget, reinterpret_cast<const D3DXVECTOR3 *>( &target ),
        reinterpret_cast<const D3DXMATRIX *>( &inverse ) );
    const Vector3 worldGravity( 0.f, -gravity, 0.f );
    Vector3 localGravity;
    D3DXVec3TransformNormal( reinterpret_cast<D3DXVECTOR3 *>( &localGravity ),
        reinterpret_cast<const D3DXVECTOR3 *>( &worldGravity ),
        reinterpret_cast<const D3DXMATRIX *>( &inverse ) );
    const Vector3 pivot = turret + Vector3( 0.f, gun.y, 0.f );
    D3DXVec3Transform( &worldPivot, reinterpret_cast<const D3DXVECTOR3 *>( &pivot ),
        reinterpret_cast<const D3DXMATRIX *>( &vehicle ) );
    const float radius = std::sqrt( gun.x * gun.x + gun.z * gun.z );
    const float verticalRadius = std::sqrt( vehicle( 0, 1 ) * vehicle( 0, 1 ) +
        vehicle( 2, 1 ) * vehicle( 2, 1 ) ) * radius;
    const float dx = worldPivot.x - target.x, dz = worldPivot.z - target.z;
    const wot::GunAimGeometry geometry = {
        { localTarget.x - turret.x, (localTarget.y - turret.y) - gun.y,
            localTarget.z - turret.z }, portable( localGravity ), gun.x, gun.z,
        (verticalRadius + worldPivot.y) - target.y, std::sqrt( dx * dx + dz * dz )
    };
    try
    {
        const auto result = wot::gun_shot_angles( geometry, speed, gravity,
            currentYaw, currentPitch, adjust );
        return Py_BuildValue( "(ff)", result[0], result[1] );
    }
    catch (const std::exception & error) { return failure( error ); }
}
PY_MODULE_FUNCTION( wg_getShotAngles, BigWorld )

PyObject * py_wg_computeProjectileTrajectory( PyObject * args )
{
    if (PyTuple_Size( args ) != 5)
    {
        PyErr_SetString( PyExc_TypeError, "wg_computeProjectileTrajectory expects Vector3, Vector3, Vector3, float, float" );
        return NULL;
    }
    Vector3 begin, velocity, gravity;
    float time, epsilon;
    if (Script::setData( PyTuple_GET_ITEM( args, 0 ), begin, "wg_computeProjectileTrajectory argument 1" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 1 ), velocity, "wg_computeProjectileTrajectory argument 2" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 2 ), gravity, "wg_computeProjectileTrajectory argument 3" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 3 ), time, "wg_computeProjectileTrajectory argument 4" ) != 0 ||
        Script::setData( PyTuple_GET_ITEM( args, 4 ), epsilon, "wg_computeProjectileTrajectory argument 5" ) != 0)
        return NULL;
    try
    {
        const auto points = wot::gun_projectile_trajectory( portable( begin ),
            portable( velocity ), portable( gravity ), time, epsilon );
        PyObjectPtr list( PyList_New( points.size() ), PyObjectPtr::STEAL_REFERENCE );
        if (!list) return NULL;
        for (size_t index = 0; index < points.size(); ++index)
        {
            PyObject * point = Script::getData( Vector3( points[index].x,
                points[index].y, points[index].z ) );
            if (!point) return NULL;
            PyList_SET_ITEM( list.get(), index, point );
        }
        Py_INCREF( list.get() );
        return list.get();
    }
    catch (const std::exception & error) { return failure( error ); }
}
PY_MODULE_FUNCTION( wg_computeProjectileTrajectory, BigWorld )

BW_END_NAMESPACE
