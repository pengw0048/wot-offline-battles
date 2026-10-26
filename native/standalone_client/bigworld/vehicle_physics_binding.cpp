#include "pch.hpp"
#include "vehicle_physics_binding.hpp"
#include "stock_scene_collision.hpp"

#include "pyscript/script_math.hpp"
#include "wot/vehicle_physics.hpp"

#include <memory>

BW_BEGIN_NAMESPACE

int StockVehiclePhysics_token = 1;

namespace
{
Vector3 nativeVector( wot::Vector3 value )
{
    return Vector3( value.x, value.y, value.z );
}

Matrix nativeMatrix( const wot::VehicleMatrix & value )
{
    Matrix result;
    for (uint32 row = 0; row < 4; ++row)
        for (uint32 column = 0; column < 4; ++column)
            result( row, column ) = value.rows[row][column];
    return result;
}
}

// This wrapper owns precisely one authoritative core. Presentation objects
// retain the wrapper and consume its published outputs; they do not integrate
// their own body or substitute the old offline controller.
class PyWGVehiclePhysics : public PyObjectPlusWithWeakReference
{
    Py_Header( PyWGVehiclePhysics, PyObjectPlusWithWeakReference )

public:
    PyWGVehiclePhysics() : PyObjectPlusWithWeakReference( &s_type_ ),
        collisionProvider_( makeStockGroundCollisionProvider() )
    {
        core_.set_collision_provider( collisionProvider_.get() );
    }

    wot::VehiclePhysics & core() { return core_; }

    // #1513 f50b00 returns the published entity matrix, not the rigid body's
    // centre-of-mass transform. f56240 owns the offset correction.
    PY_RO_ATTRIBUTE_DECLARE( nativeMatrix( core_.entity_transform() ), matrix )
    PY_RO_ATTRIBUTE_DECLARE( nativeVector( core_.linear_velocity() ), velocity )
    PY_RO_ATTRIBUTE_DECLARE( nativeVector( core_.angular_velocity() ), angVelocity )
    PY_RO_ATTRIBUTE_DECLARE( core_.track_state().left_scroll, leftTrackScroll )
    PY_RO_ATTRIBUTE_DECLARE( core_.track_state().right_scroll, rightTrackScroll )

    Vector3 centerOfMass() const
    {
        return nativeVector( core_.center_of_mass() );
    }
    void centerOfMass( Vector3 value )
    {
        core_.set_center_of_mass( { value.x, value.y, value.z } );
    }
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( Vector3, centerOfMass, centerOfMass )

    uint32 movementSignals() const { return core_.inputs().movement_signals; }
    void movementSignals( uint32 value )
    {
        wot::VehiclePhysicsInputs inputs = core_.inputs();
        inputs.movement_signals = value;
        core_.set_inputs( inputs );
    }
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( uint32, movementSignals, movementSignals )

    uint32 cruiseSignals() const { return core_.inputs().cruise_signals; }
    void cruiseSignals( uint32 value )
    {
        wot::VehiclePhysicsInputs inputs = core_.inputs();
        inputs.cruise_signals = value;
        core_.set_inputs( inputs );
    }
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( uint32, cruiseSignals, cruiseSignals )

    bool staticMode() const { return core_.static_mode(); }
    void staticMode( bool value ) { core_.set_static_mode( value ); }
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( bool, staticMode, staticMode )

    uint32 visibilityMask() const { return core_.visibility_mask(); }
    void visibilityMask( uint32 value ) { core_.set_visibility_mask( value ); }
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( uint32, visibilityMask, visibilityMask )

    void setArenaBounds( Vector2 minimum, Vector2 maximum )
    {
        // #1513 f4ab00 stores both Vector2 inputs without local reordering.
        core_.set_arena_bounds( {{ minimum.x, minimum.y }},
            {{ maximum.x, maximum.y }} );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, setArenaBounds,
        ARG( Vector2, ARG( Vector2, END ) ) )

    void removeAllDamperSprings()
    {
        core_.clear_damper_springs();
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, removeAllDamperSprings, END )

    PyObject * addDamperSpringRecord( PyObject * args )
    {
        PyObject * value;
        if (!PyArg_ParseTuple( args, "O:addDamperSpring", &value ))
            return NULL;
        if (!PyTuple_Check( value ))
        {
            PyErr_SetString( PyExc_TypeError,
                "WGVehiclePhysics.addDamperSpring expects a tuple" );
            return NULL;
        }
        if (PyTuple_GET_SIZE( value ) != 5)
        {
            PyErr_SetString( PyExc_TypeError,
                "addDamperSpring expects a five-element spring record" );
            return NULL;
        }
        Vector3 mount;
        wot::VehicleDamperSpring spring;
        if (Script::setData( PyTuple_GET_ITEM( value, 0 ), mount ) != 0 ||
            Script::setData( PyTuple_GET_ITEM( value, 1 ), spring.carrying_length ) != 0 ||
            Script::setData( PyTuple_GET_ITEM( value, 2 ), spring.indent ) != 0 ||
            Script::setData( PyTuple_GET_ITEM( value, 3 ), spring.left ) != 0 ||
            Script::setData( PyTuple_GET_ITEM( value, 4 ), spring.hard_ratio ) != 0)
            return NULL;
        spring.mount_point = { mount.x, mount.y, mount.z };
        // #1513 f49b60 stores the six floats in the selected side's vector;
        // it does not evaluate a suspension force or clamp these lengths.
        core_.add_damper_spring( spring );
        Py_RETURN_NONE;
    }
    PY_METHOD_DECLARE( py_addDamperSpring )

    Vector3 getPointVelocity( Vector3 point ) const
    {
        return nativeVector( core_.point_velocity( { point.x, point.y, point.z } ) );
    }
    PY_AUTO_METHOD_DECLARE( RETDATA, getPointVelocity, ARG( Vector3, END ) )

    void applyImpulse( Vector3 point, Vector3 impulse )
    {
        core_.apply_impulse( { point.x, point.y, point.z },
            { impulse.x, impulse.y, impulse.z } );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, applyImpulse,
        ARG( Vector3, ARG( Vector3, END ) ) )

    void applyImpulseToCoM( Vector3 impulse )
    {
        core_.apply_impulse_to_com( { impulse.x, impulse.y, impulse.z } );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, applyImpulseToCoM, ARG( Vector3, END ) )

    PY_METHOD_DECLARE( py_setDamperSpringsLength )

    static PyWGVehiclePhysics * New() { return new PyWGVehiclePhysics(); }
    PY_AUTO_FACTORY_DECLARE( PyWGVehiclePhysics, END )

private:
    // #1513 creates one scene-obstacle query cache per physics wrapper.
    // Its lifetime encloses the core's non-owning provider reference.
    std::unique_ptr<wot::VehicleCollisionProvider> collisionProvider_;
    wot::VehiclePhysics core_;
};

PyObject * PyWGVehiclePhysics::py_addDamperSpring( PyObject * args )
{
    return this->addDamperSpringRecord( args );
}

PyObject * PyWGVehiclePhysics::py_setDamperSpringsLength( PyObject * args )
{
    PyObject * leftValue;
    PyObject * rightValue;
    if (!PyArg_ParseTuple( args, "OO:setDamperSpringsLength", &leftValue, &rightValue ))
        return NULL;
    std::vector<float> lengths[2];
    PyObject * values[] = { leftValue, rightValue };
    for (size_t side = 0; side < 2; ++side)
    {
        PyObjectPtr sequence( PySequence_Fast( values[side],
            "setDamperSpringsLength expects two float sequences" ),
            PyObjectPtr::STEAL_REFERENCE );
        if (!sequence)
            return NULL;
        const Py_ssize_t count = PySequence_Fast_GET_SIZE( sequence.get() );
        lengths[side].reserve( static_cast<size_t>( count ) );
        for (Py_ssize_t index = 0; index < count; ++index)
        {
            float value;
            if (Script::setData( PySequence_Fast_GET_ITEM( sequence.get(), index ), value ) != 0)
                return NULL;
            lengths[side].push_back( value );
        }
    }
    // #1513 validates both side counts before changing either side.
    if (!core_.set_damper_springs_lengths( lengths[0], lengths[1] ))
    {
        PyErr_SetString( PyExc_ValueError,
            "setDamperSpringsLength requires one length per existing spring" );
        return NULL;
    }
    Py_RETURN_NONE;
}

PY_TYPEOBJECT( PyWGVehiclePhysics )
PY_BEGIN_METHODS( PyWGVehiclePhysics )
    PY_METHOD( removeAllDamperSprings )
    PY_METHOD( addDamperSpring )
    PY_METHOD( getPointVelocity )
    PY_METHOD( applyImpulse )
    PY_METHOD( applyImpulseToCoM )
    PY_METHOD( setDamperSpringsLength )
    PY_METHOD( setArenaBounds )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyWGVehiclePhysics )
    PY_ATTRIBUTE( matrix )
    PY_ATTRIBUTE( velocity )
    PY_ATTRIBUTE( angVelocity )
    PY_ATTRIBUTE( centerOfMass )
    PY_ATTRIBUTE( movementSignals )
    PY_ATTRIBUTE( cruiseSignals )
    PY_ATTRIBUTE( staticMode )
    PY_ATTRIBUTE( visibilityMask )
    PY_ATTRIBUTE( leftTrackScroll )
    PY_ATTRIBUTE( rightTrackScroll )
PY_END_ATTRIBUTES()
PY_FACTORY_NAMED( PyWGVehiclePhysics, "WGVehiclePhysics", BigWorld )

wot::VehiclePhysics * unwrapStockVehiclePhysics( PyObject * object )
{
    if (!object || !PyWGVehiclePhysics::Check( object ))
    {
        PyErr_SetString( PyExc_TypeError,
            "Expected a WGVehiclePhysics instance" );
        return NULL;
    }
    return &static_cast<PyWGVehiclePhysics *>( object )->core();
}

BW_END_NAMESPACE
