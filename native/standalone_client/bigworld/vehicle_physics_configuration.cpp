#include "vehicle_physics_configuration.hpp"

#include <cmath>
#include <limits>
#include <utility>

namespace
{
class Owned
{
public:
    explicit Owned( PyObject * object ) : object_( object ) {}
    ~Owned() { Py_XDECREF( object_ ); }
    PyObject * get() const { return object_; }
private:
    Owned( const Owned & );
    Owned & operator=( const Owned & );
    PyObject * object_;
};

bool readValue( PyObject * object, float & value )
{
    // #1513 f7e600/f80e20/f83d20 require a numeric nb_float conversion.
    if (!object || !PyNumber_Check( object ) || !object->ob_type->tp_as_number ||
        !object->ob_type->tp_as_number->nb_float)
    {
        PyErr_SetString( PyExc_TypeError, "Physics setting must be numeric" );
        return false;
    }
    const double number = PyFloat_AsDouble( object );
    if (PyErr_Occurred()) return false;
    value = static_cast<float>( number );
    if (!std::isfinite( value ))
    {
        PyErr_SetString( PyExc_ValueError, "Physics setting must be finite" );
        return false;
    }
    return true;
}

bool readValue( PyObject * object, bool & value )
{
    // f81a60 checks the exact bool type; integer truthiness is not its ABI.
    if (!object || !PyBool_Check( object ))
    {
        PyErr_SetString( PyExc_TypeError, "Physics setting must be bool" );
        return false;
    }
    value = object == Py_True;
    return true;
}

bool readValue( PyObject * object, std::int32_t & value )
{
    if (!object || !PyNumber_Check( object ) || !object->ob_type->tp_as_number ||
        !object->ob_type->tp_as_number->nb_int)
    {
        PyErr_SetString( PyExc_TypeError, "Physics setting must be integral" );
        return false;
    }
    const long number = PyInt_AsLong( object );
    if (PyErr_Occurred()) return false;
    if (number < std::numeric_limits<std::int32_t>::min() ||
        number > std::numeric_limits<std::int32_t>::max())
    {
        PyErr_SetString( PyExc_OverflowError, "Physics setting exceeds int32" );
        return false;
    }
    value = static_cast<std::int32_t>( number );
    return true;
}

bool readSequence( PyObject * source, std::vector<float> & output )
{
    if (!source || !PySequence_Check( source ))
    {
        PyErr_SetString( PyExc_TypeError, "Physics setting must be a sequence" );
        return false;
    }
    const Py_ssize_t length = PySequence_Size( source );
    if (length < 0) return false;
    output.resize( static_cast<std::size_t>( length ) );
    for (Py_ssize_t index = 0; index < length; ++index)
    {
        Owned item( PySequence_GetItem( source, index ) );
        if (!item.get() || !readValue( item.get(), output[static_cast<std::size_t>( index )] ))
            return false;
    }
    return true;
}

template<std::size_t N>
bool readValue( PyObject * source, std::array<float, N> & output )
{
    std::vector<float> values;
    if (!readSequence( source, values )) return false;
    if (values.size() != N)
    {
        PyErr_Format( PyExc_ValueError, "Physics setting needs %u elements",
            static_cast<unsigned>( N ) );
        return false;
    }
    for (std::size_t index = 0; index < N; ++index) output[index] = values[index];
    return true;
}

bool readValue( PyObject * source, wot::Vector3 & output )
{
    std::array<float, 3> values;
    if (!readValue( source, values )) return false;
    output = { values[0], values[1], values[2] };
    return true;
}

class Reader
{
public:
    explicit Reader( PyObject * source ) : source_( source ) {}

    PyObject * item( const char * name ) const
    {
        if (PyErr_Occurred()) return NULL;
        if (!source_ || !PyDict_Check( source_ ))
        {
            PyErr_SetString( PyExc_TypeError, "Physics configuration must be a dictionary" );
            return NULL;
        }
        PyObject * result = PyDict_GetItemString( source_, name );
        if (!result) PyErr_Format( PyExc_KeyError, "Missing physics setting: %s", name );
        return result;
    }

    template<class T> bool field( const char * name, T & output ) const
    {
        PyObject * value = item( name );
        return value && readValue( value, output );
    }

private:
    PyObject * source_;
};

template<class T> struct FloatField
{
    const char * name;
    float T::* member;
};

template<class T, std::size_t N>
bool readFields( const Reader & reader, T & output, const FloatField<T> (&fields)[N] )
{
    for (std::size_t index = 0; index < N; ++index)
        if (!reader.field( fields[index].name, output.*fields[index].member )) return false;
    return true;
}

bool readBody( PyObject * source, wot::VehicleBodyConfiguration & output )
{
    typedef wot::VehicleBodyConfiguration T;
    static const FloatField<T> fields[] = {
        { "gravity", &T::gravity }, { "fullMass", &T::full_mass },
        { "hullCOMShiftY", &T::hull_com_shift_y }, { "hullBoxOffsetZ", &T::hull_box_offset_z },
        { "turretTopWidth", &T::turret_top_width },
        { "smplFwMaxSpeed", &T::simple_forward_max_speed },
        { "smplBkMaxSpeed", &T::simple_backward_max_speed },
        { "smplRotSpeed", &T::simple_rotation_speed },
        { "angVelocityFactor", &T::angular_velocity_factor },
        { "allowedRPMExcess", &T::allowed_rpm_excess },
        { "overspeedResistFactor", &T::overspeed_resistance_factor },
        { "centerRotationFwdSpeed", &T::center_rotation_forward_speed },
        { "movementRevertSpeed", &T::movement_revert_speed },
        { "comFrictionYOffs", &T::com_friction_y_offset },
        { "comSideFriction", &T::com_side_friction }
    };
    const Reader reader( source );
    const Reader gimlet( reader.item( "gimlet" ) );
    return readFields( reader, output, fields ) &&
        reader.field( "hullInertiaFactors", output.hull_inertia_factors ) &&
        reader.field( "hullSize", output.hull_size ) &&
        reader.field( "turretTopPos", output.turret_top_position ) &&
        reader.field( "isRotationAroundCenter", output.rotation_around_center ) &&
        reader.field( "enableSabilization", output.stabilization_enabled ) &&
        gimlet.field( "wPushedRot", output.gimlet_pushed_rotation ) &&
        gimlet.field( "wPushedHB", output.gimlet_pushed_handbrake );
}

bool readShape( PyObject * source, wot::VehicleShapeConfiguration & output )
{
    typedef wot::VehicleShapeConfiguration T;
    static const FloatField<T> fields[] = {
        { "auxClearance", &T::auxiliary_clearance },
        { "tankBoardAngle", &T::tank_board_angle },
        { "tankAftChamferFraction", &T::tank_aft_chamfer_fraction },
        { "tankFrontChamferFraction", &T::tank_front_chamfer_fraction },
        { "terrBoardAngle", &T::terrain_board_angle },
        { "terrFrontChamferFraction", &T::terrain_front_chamfer_fraction },
        { "terrAftChamferFraction", &T::terrain_aft_chamfer_fraction }
    };
    const Reader reader( source );
    return readFields( reader, output, fields ) &&
        reader.field( "useComplexForm", output.use_complex_form );
}

bool readGroundSurface( PyObject * source, wot::VehicleGroundSurfaceConfiguration & output )
{
    typedef wot::VehicleGroundSurfaceConfiguration T;
    static const FloatField<T> fields[] = {
        { "dirtCumulationRate", &T::dirt_cumulation_rate },
        { "dirtReleaseRate", &T::dirt_release_rate },
        { "dirtSideVelocity", &T::dirt_side_velocity }, { "maxDirt", &T::max_dirt },
        { "hbComSideFriction", &T::handbrake_com_side_friction },
        { "sideFriction", &T::side_friction }, { "fwdFriction", &T::forward_friction },
        { "rollingFriction", &T::rolling_friction },
        { "hbSideFrictionAddition", &T::handbrake_side_friction_addition },
        { "rotationFactor", &T::rotation_factor }
    };
    return readFields( Reader( source ), output, fields );
}

bool readGrounds( PyObject * source, std::vector<wot::VehicleGroundConfiguration> & output )
{
    if (!source || !PyDict_Check( source ))
    {
        if (!PyErr_Occurred()) PyErr_SetString( PyExc_TypeError, "Physics grounds must be a dictionary" );
        return false;
    }
    Py_ssize_t position = 0;
    PyObject * key, * value;
    while (PyDict_Next( source, &position, &key, &value ))
    {
        wot::VehicleGroundConfiguration ground;
        const Reader reader( value );
        if (!readValue( key, ground.material_kind ) ||
            !readGroundSurface( reader.item( "firm" ), ground.firm ) ||
            !readGroundSurface( reader.item( "medium" ), ground.medium ) ||
            !readGroundSurface( reader.item( "soft" ), ground.soft )) return false;
        output.push_back( ground );
    }
    return true;
}

bool readChassis( PyObject * source, wot::VehicleChassisConfiguration & output )
{
    typedef wot::VehicleChassisConfiguration T;
    static const FloatField<T> fields[] = {
        { "chassisMass", &T::chassis_mass }, { "chassisMassFraction", &T::chassis_mass_fraction },
        { "clearance", &T::clearance }, { "trackGaugeFactor", &T::track_gauge_factor },
        { "trackToBeLockedDelay", &T::track_to_be_locked_delay },
        { "wheelRadius", &T::wheel_radius }, { "wheelInertiaFactor", &T::wheel_inertia_factor },
        { "wheelFwdInertiaFactor", &T::wheel_forward_inertia_factor },
        { "stiffness0", &T::stiffness_zero }, { "stiffness1", &T::stiffness_one },
        { "damping", &T::damping }, { "wheelRestitution", &T::wheel_restitution },
        { "wheelPenetration", &T::wheel_penetration }, { "brake", &T::brake },
        { "rotationBrake", &T::rotation_brake },
        { "wheelSinkageResistFactor", &T::wheel_sinkage_resistance_factor },
        { "wheelDryFrictionFactor", &T::wheel_dry_friction_factor },
        { "sideFrictionConstantRatio", &T::side_friction_constant_ratio }
    };
    const Reader reader( source );
    if (!readFields( reader, output, fields ) ||
        !reader.field( "wheelDetachOnRoll", output.wheel_detach_on_roll ) ||
        !reader.field( "wheelUsePseudoContacts", output.wheel_use_pseudo_contacts ) ||
        !reader.field( "flatSideFriction", output.flat_side_friction ) ||
        !reader.field( "roadWheelPositions", output.road_wheel_positions ) ||
        !reader.field( "stiffnessFactors", output.stiffness_factors ) ||
        !reader.field( "slopeResistTerrain", output.slope_resistance_terrain ) ||
        !reader.field( "slopeResistStaticObject", output.slope_resistance_static_object ) ||
        !reader.field( "slopeResistDynamicObject", output.slope_resistance_dynamic_object ) ||
        !reader.field( "slopeGripLngTerrain", output.slope_grip_forward_terrain ) ||
        !reader.field( "slopeGripSdwTerrain", output.slope_grip_side_terrain ) ||
        !reader.field( "slopeGripLngStaticObject", output.slope_grip_forward_static_object ) ||
        !reader.field( "slopeGripSdwStaticObject", output.slope_grip_side_static_object ) ||
        !reader.field( "slopeGripLngDynamicObject", output.slope_grip_forward_dynamic_object ) ||
        !reader.field( "slopeGripSdwDynamicObject", output.slope_grip_side_dynamic_object ) ||
        !readGrounds( reader.item( "grounds" ), output.grounds )) return false;
    const Reader aiming( reader.item( "hullAiming" ) );
    const Reader pitch( aiming.item( "pitch" ) );
    const Reader yaw( aiming.item( "yaw" ) );
    return pitch.field( "correctionCenterZ", output.pitch_correction_center_z ) &&
        pitch.field( "correctionSpeed", output.pitch_correction_speed ) &&
        pitch.field( "pitchMin", output.pitch_min ) && pitch.field( "pitchMax", output.pitch_max ) &&
        pitch.field( "correctionStiffness", output.pitch_correction_stiffness ) &&
        pitch.field( "correctionDamping", output.pitch_correction_damping ) &&
        pitch.field( "correctionScale", output.pitch_correction_scale ) &&
        yaw.field( "gimletForce", output.yaw_gimlet_force ) &&
        yaw.field( "stiffness", output.yaw_stiffness ) && yaw.field( "damping", output.yaw_damping ) &&
        yaw.field( "preciseRestitution", output.yaw_precise_restitution ) &&
        yaw.field( "dampingYawDist", output.yaw_damping_distance ) &&
        yaw.field( "preciseYawDist", output.yaw_precise_distance );
}

bool readEngine( PyObject * source, wot::VehicleEngineConfiguration & output )
{
    typedef wot::VehicleEngineConfiguration T;
    static const FloatField<T> fields[] = {
        { "smplEnginePower", &T::simple_engine_power }, { "engineInertia", &T::engine_inertia },
        { "smplEngJoinRatio", &T::simple_engine_join_ratio }, { "idleChoker", &T::idle_choker },
        { "rotationChoker", &T::rotation_choker }, { "rotationByLockChoker", &T::rotation_by_lock_choker },
        { "smplMinRPM", &T::simple_min_rpm }, { "idleRPM", &T::idle_rpm }, { "startRPM", &T::start_rpm },
        { "engVelMax", &T::maximum_forward_angular_velocity },
        { "engVelBkMax", &T::maximum_backward_angular_velocity },
        { "engVelRot", &T::rotation_angular_velocity }
    };
    const Reader reader( source );
    return readFields( reader, output, fields ) && reader.field( "engineLoses", output.losses );
}

bool readGimlet( PyObject * source, wot::VehicleGimletConfiguration & output )
{
    typedef wot::VehicleGimletConfiguration T;
    static const FloatField<T> fields[] = {
        { "wPushedDiag", &T::pushed_diagonal_weight }, { "wPushedRot", &T::pushed_rotation_weight },
        { "wPushedHB", &T::pushed_handbrake_weight }, { "pushInContact", &T::push_in_contact },
        { "pushHB", &T::push_handbrake }, { "pushStop", &T::push_stop },
        { "pushRotOnSpotFixedPeriod", &T::on_spot_fixed_period },
        { "pushRotOnSpotGrowPeriod", &T::on_spot_grow_period },
        { "gimletPushOnSpotInit", &T::on_spot_initial_push },
        { "gimletPushOnSpotFinal", &T::on_spot_final_push },
        { "pushRotOnMoveFixedPeriod", &T::on_move_fixed_period },
        { "pushRotOnMoveGrowPeriod", &T::on_move_grow_period },
        { "gimletPushOnMoveInit", &T::on_move_initial_push },
        { "gimletPushOnMoveFinal", &T::on_move_final_push },
        { "gimletVelScaleMin", &T::minimum_velocity_scale },
        { "gimletVelScaleMax", &T::maximum_velocity_scale }
    };
    return readFields( Reader( source ), output, fields );
}

bool readGears( PyObject * source, std::vector<wot::VehicleFakeGearConfiguration> & output )
{
    const Reader reader( source );
    std::vector<float> speeds, hysteresis, low, high;
    if (!readSequence( reader.item( "switchSpeed" ), speeds ) ||
        !readSequence( reader.item( "switchHysteresis" ), hysteresis ) ||
        !readSequence( reader.item( "lowRpm" ), low ) ||
        !readSequence( reader.item( "highRpm" ), high )) return false;
    if (speeds.size() != hysteresis.size() || speeds.size() != low.size() || speeds.size() != high.size())
    {
        PyErr_SetString( PyExc_ValueError, "Fake gearbox sequences must have equal lengths" );
        return false;
    }
    for (std::size_t index = 0; index < speeds.size(); ++index)
        output.push_back( { speeds[index], hysteresis[index], low[index], high[index] } );
    return true;
}
} // namespace

bool parseStockVehiclePhysicsConfiguration( PyObject * source,
    wot::VehiclePhysicsConfiguration & output )
{
    wot::VehiclePhysicsConfiguration candidate;
    const Reader reader( source );
    if (!reader.field( "vehicleType", candidate.vehicle_type )) return false;
    if (candidate.vehicle_type != 0)
    {
        PyErr_SetString( PyExc_ValueError, "WGVehiclePhysics requires Tank vehicleType 0" );
        return false;
    }
    PyObject * modes = reader.item( "modes" );
    if (!modes || !PyDict_Check( modes ))
    {
        if (!PyErr_Occurred()) PyErr_SetString( PyExc_TypeError, "Physics modes must be a dictionary" );
        return false;
    }
    Py_ssize_t position = 0;
    PyObject * key, * value;
    while (PyDict_Next( modes, &position, &key, &value ))
    {
        wot::VehiclePhysicsModeConfiguration mode;
        const char * name = PyString_AsString( key );
        if (!name) return false;
        mode.name = name;
        const Reader current( value );
        if (!current.field( "mode_index", mode.mode_index ) ||
            !readBody( value, mode.body ) ||
            !readShape( current.item( "shape" ), mode.shape ) ||
            !current.field( "siegeModeAvailable", mode.siege_mode_available ) ||
            !readChassis( current.item( "chassis" ), mode.chassis ) ||
            !readEngine( current.item( "engine" ), mode.engine )) return false;
        candidate.modes.push_back( std::move( mode ) );
    }
    const Reader normal( Reader( modes ).item( "normal" ) );
    const Reader anchor( normal.item( "anchor" ) );
    const Reader rail( normal.item( "rail" ) );
    const Reader gearbox( normal.item( "fakegearbox" ) );
    if (!anchor.field( "anchorMaxReactionFactor", candidate.anchor_maximum_reaction_factor ) ||
        !anchor.field( "anchorConstFraction", candidate.anchor_constant_fraction ) ||
        !anchor.field( "anchorVelFactor", candidate.anchor_velocity_factor ) ||
        !rail.field( "railFactorInContact", candidate.rail_factor_in_contact ) ||
        !readGimlet( normal.item( "gimlet" ), candidate.gimlet ) ||
        !readGears( gearbox.item( "fwdgears" ), candidate.forward_gears ) ||
        !readGears( gearbox.item( "bkwdgears" ), candidate.backward_gears )) return false;
    output = std::move( candidate );
    return true;
}
