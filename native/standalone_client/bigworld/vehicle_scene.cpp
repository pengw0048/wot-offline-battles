#include "pch.hpp"
#include "vehicle_scene.hpp"

#include "cstdmf/debug.hpp"
#include "input/input.hpp"
#include "physics2/collision_callback.hpp"
#include "physics2/collision_obstacle.hpp"
#include "terrain/base_terrain_block.hpp"
#include "wot/vehicle_control.hpp"

#include <algorithm>
#include <cfloat>
#include <cmath>
#include <vector>

DECLARE_DEBUG_COMPONENT2( "StandaloneVehicle", 0 )

BW_BEGIN_NAMESPACE

namespace
{
bool finite( const Vector3 & value )
{
    return std::isfinite( value.x ) && std::isfinite( value.y ) &&
        std::isfinite( value.z );
}

// Terrain is the support owner's responsibility. These queries admit only
// blocking static triangles, as in the native projectile scene.
class BodyCollision : public CollisionCallback
{
public:
    virtual int operator()( const CollisionObstacle & obstacle,
        const WorldTriangle & triangle, float /* distance */ )
    {
        if (obstacle.isMovingObstacle() ||
            (triangle.flags() & (BW_TRIANGLE_NOCOLLIDE | TRIANGLE_TERRAIN)))
            return COLLIDE_ALL;
        return COLLIDE_BEFORE;
    }
};

struct GroundPlane
{
    double y;
    double pitch;
    double roll;
};
} // namespace

struct VehicleScene::State
{
    struct Component
    {
        IEntityEmbodimentPtr model;
        Vector3 offset;
        BoundingBox bounds;
        bool body;
    };

    State() : configured( false ), supported( false ), reported( false ),
        x( 0 ), y( 0 ), z( 0 ), yaw( 0 ), speed( 0 ), omega( 0 ),
        length( 0 ), width( 0 ), groundBottom( 0 )
    {
        transform.setIdentity();
    }

    ClientSpacePtr space;
    std::vector<Component> components;
    wot::offline_vehicle_control::Parameters parameters;
    bool configured;
    bool supported;
    bool reported;
    double x, y, z, yaw, speed, omega;
    double length, width, groundBottom;
    Matrix transform;

    bool height( double sampleX, double sampleZ, double & result ) const
    {
        const float value = space->findTerrainHeight( Vector3(
            static_cast<float>( sampleX ), static_cast<float>( y ),
            static_cast<float>( sampleZ ) ) );
        // The chunk-space adapter returns FLT_MIN for an absent column; the
        // terrain block has a different sentinel for unavailable terrain.
        if (!std::isfinite( value ) || value == FLT_MIN ||
            value == Terrain::BaseTerrainBlock::NO_TERRAIN)
            return false;
        result = value;
        return true;
    }

    bool ground( double px, double pz, double heading, GroundPlane & plane ) const
    {
        const double sine = std::sin( heading ), cosine = std::cos( heading );
        double front, rear, right, left, center;
        if (!height( px + sine * length * 0.5, pz + cosine * length * 0.5, front ) ||
            !height( px - sine * length * 0.5, pz - cosine * length * 0.5, rear ) ||
            !height( px + cosine * width * 0.5, pz - sine * width * 0.5, right ) ||
            !height( px - cosine * width * 0.5, pz + sine * width * 0.5, left ) ||
            !height( px, pz, center ))
            return false;
        // vehicle_physics.sampled_ground_plane and battle_runtime's existing
        // GROUND_PLANE_EPSILON. Reject discontinuous support, never invent it.
        const double longMid = (front + rear) * 0.5;
        const double sideMid = (right + left) * 0.5;
        const double residual = std::max( std::fabs( longMid - sideMid ),
            std::max( std::fabs( center - longMid ), std::fabs( center - sideMid ) ) );
        if (residual > 0.35)
            return false;
        const double forwardHeight = (front - rear) / length;
        const double rightHeight = (right - left) / width;
        plane.y = center - groundBottom;
        plane.pitch = -std::atan2( front - rear, length );
        plane.roll = std::atan2( rightHeight,
            std::sqrt( 1.0 + forwardHeight * forwardHeight ) );
        return std::isfinite( plane.y ) && std::isfinite( plane.pitch ) &&
            std::isfinite( plane.roll );
    }

    Matrix pose( double px, double pz, double heading, const GroundPlane & plane ) const
    {
        Matrix result;
        result.setRotate( static_cast<float>( heading ),
            static_cast<float>( plane.pitch ), static_cast<float>( plane.roll ) );
        result.translation( Vector3( static_cast<float>( px ),
            static_cast<float>( plane.y ), static_cast<float>( pz ) ) );
        return result;
    }

    static void corners( const Component & component, const Matrix & pose,
        Vector3 * points )
    {
        const Vector3 & low = component.bounds.minBounds();
        const Vector3 & high = component.bounds.maxBounds();
        for (int index = 0; index < 8; ++index)
            points[index] = pose.applyPoint( component.offset + Vector3(
                index & 1 ? high.x : low.x, index & 2 ? high.y : low.y,
                index & 4 ? high.z : low.z ) );
    }

    bool clearMotion( const Matrix & from, const Matrix & to, bool rotation ) const
    {
        // Sweep all twelve faces of the real collision-bound box during
        // translation. Rotation uses its eight corner trajectories, so this
        // preview does not claim a recovered rotating BSP/contact solver.
        static const int faces[][3] = {
            {0,2,3},{0,3,1},{4,5,7},{4,7,6},
            {0,4,6},{0,6,2},{1,3,7},{1,7,5},
            {0,1,5},{0,5,4},{2,6,7},{2,7,3}
        };
        for (size_t index = 0; index < components.size(); ++index)
        {
            const Component & component = components[index];
            if (!component.body)
                continue;
            Vector3 start[8], finish[8];
            corners( component, from, start );
            corners( component, to, finish );
            if (rotation)
            {
                for (int corner = 0; corner < 8; ++corner)
                {
                    const float distance = (finish[corner] - start[corner]).length();
                    if (distance <= 0.f)
                        continue;
                    BodyCollision collision;
                    const float hit = space->collide( start[corner], finish[corner], collision );
                    if (!std::isfinite( hit ) || hit >= 0.f)
                        return false;
                }
            }
            else
            {
                const Vector3 delta = to.applyToOrigin() - from.applyToOrigin();
                if (delta.lengthSquared() <= 0.f)
                    continue;
                for (size_t face = 0; face < sizeof( faces ) / sizeof( faces[0] ); ++face)
                {
                    const WorldTriangle triangle( start[faces[face][0]],
                        start[faces[face][1]], start[faces[face][2]] );
                    BodyCollision collision;
                    const float hit = space->collide( triangle, triangle.v0() + delta, collision );
                    if (!std::isfinite( hit ) || hit >= 0.f)
                        return false;
                }
            }
        }
        return true;
    }

    void publish( const Matrix & next )
    {
        // Build every component transform before publishing any of them. No
        // animation or render pass runs between these same-tick assignments.
        std::vector<Matrix> transforms;
        for (size_t index = 0; index < components.size(); ++index)
        {
            Matrix component = next;
            component.translation( next.applyPoint( components[index].offset ) );
            transforms.push_back( component );
        }
        transform = next;
        for (size_t index = 0; index < components.size(); ++index)
            components[index].model->worldTransform( transforms[index] );
    }

    void supportStatus( bool available )
    {
        if (!reported || supported != available)
            INFO_MSG( "VehicleScene: terrain support %s; position=(%g,%g,%g)\n",
                available ? "available" : "unavailable; movement held", x, y, z );
        supported = available;
        reported = true;
    }

    void step( double elapsed, double throttle, double steer, bool handbrake )
    {
        GroundPlane plane;
        if (!ground( x, z, yaw, plane ))
        {
            speed = omega = 0.0;
            supportStatus( false );
            return;
        }
        supportStatus( true );
        y = plane.y;
        const Matrix start = pose( x, z, yaw, plane );
        speed = wot::offline_vehicle_control::longitudinal_step(
            parameters, speed, throttle, steer != 0.0, plane.pitch, elapsed,
            false, 0, handbrake );
        const double nextX = x + std::sin( yaw ) * speed * elapsed;
        const double nextZ = z + std::cos( yaw ) * speed * elapsed;
        GroundPlane endpoint;
        bool advanced = false;
        if (ground( nextX, nextZ, yaw, endpoint ))
        {
            // Translation keeps the current orientation for a genuine native
            // triangle sweep. The endpoint's support owns subsequent tilt.
            Matrix translation = start;
            translation.translation( Vector3( static_cast<float>( nextX ),
                static_cast<float>( endpoint.y ), static_cast<float>( nextZ ) ) );
            if (clearMotion( start, translation, false ))
            {
                x = nextX; z = nextZ; plane = endpoint;
                advanced = true;
            }
        }
        if (!advanced && speed != 0.0)
        {
            // Existing hard_contact_step's blocked, non-deflected branch.
            speed *= std::pow( 0.35, elapsed * 60.0 );
            if (std::fabs( speed ) < 0.05) speed = 0.0;
        }
        omega = wot::offline_vehicle_control::traverse_step( parameters,
            omega, steer, speed, elapsed, 0, throttle );
        const double nextYaw = std::remainder( yaw + omega * elapsed,
            2.0 * 3.14159265358979323846 );
        GroundPlane rotated;
        if (ground( x, z, nextYaw, rotated ) &&
            clearMotion( pose( x, z, yaw, plane ),
                pose( x, z, nextYaw, rotated ), true ))
        {
            yaw = nextYaw;
            plane = rotated;
        }
        else
            omega = 0.0;
        y = plane.y;
        publish( pose( x, z, yaw, plane ) );
    }
};

VehicleScene::VehicleScene() : state_( new State() ) {}
VehicleScene::~VehicleScene() { delete state_; }

bool VehicleScene::configure( DataSectionPtr assembly, const Vector3 & origin,
    ClientSpacePtr space, const EntityEmbodiments & models )
{
    clear();
    state_->transform.setTranslate( origin );
    DataSectionPtr drive = assembly ? assembly->openSection( "drive" ) : NULL;
    if (!drive)
    {
        INFO_MSG( "VehicleScene: no authored drive configuration; driving disabled\n" );
        return true;
    }
    if (!space)
    {
        INFO_MSG( "VehicleScene: no native terrain space; driving disabled\n" );
        return true;
    }
    const char * fields[] = { "mass", "power_w", "speed_forward", "speed_backward",
        "rotation_speed", "terrain_resistance", "specific_friction",
        "brake_deceleration", "track_center", "native_power_ratio" };
    for (size_t index = 0; index < sizeof( fields ) / sizeof( fields[0] ); ++index)
        if (!drive->openSection( fields[index] ))
        {
            ERROR_MSG( "VehicleScene: drive is missing %s\n", fields[index] );
            return false;
        }
    State & state = *state_;
    wot::offline_vehicle_control::Parameters & p = state.parameters;
    p.mass = drive->readDouble( "mass" );
    p.power_w = drive->readDouble( "power_w" );
    p.speed_forward = drive->readDouble( "speed_forward" );
    p.speed_backward = drive->readDouble( "speed_backward" );
    p.rotation_speed = drive->readDouble( "rotation_speed" );
    const Vector3 resistance = drive->readVector3( "terrain_resistance" );
    p.terrain_resistance = { resistance.x, resistance.y, resistance.z };
    p.specific_friction = drive->readDouble( "specific_friction" );
    p.brake_deceleration = drive->readDouble( "brake_deceleration" );
    p.track_center = drive->readDouble( "track_center" );
    p.native_power_ratio = drive->readDouble( "native_power_ratio" );
    const double positive[] = { p.mass, p.power_w, p.speed_forward, p.speed_backward,
        p.rotation_speed, resistance.x, resistance.y, resistance.z,
        p.specific_friction, p.brake_deceleration, p.track_center, p.native_power_ratio };
    for (size_t index = 0; index < sizeof( positive ) / sizeof( positive[0] ); ++index)
        if (!std::isfinite( positive[index] ) || positive[index] <= 0.0)
        {
            ERROR_MSG( "VehicleScene: drive parameters must be finite and positive\n" );
            return false;
        }
    BW::vector<DataSectionPtr> definitions;
    assembly->openSections( "component", definitions );
    if (definitions.size() != models.size() || !finite( origin ))
        return false;
    bool chassis = false, hull = false;
    for (size_t index = 0; index < definitions.size(); ++index)
    {
        DataSectionPtr definition = definitions[index];
        State::Component component;
        component.model = models[index];
        component.offset = definition->readVector3( "position" );
        const BW::string name = definition->readString( "name" );
        component.body = name == "chassis" || name == "hull";
        if (component.body)
        {
            if (!definition->openSection( "collision_min" ) ||
                !definition->openSection( "collision_max" ))
            {
                ERROR_MSG( "VehicleScene: %s has no authored collision bounds\n", name.c_str() );
                return false;
            }
            const Vector3 low = definition->readVector3( "collision_min" );
            const Vector3 high = definition->readVector3( "collision_max" );
            if (!finite( low ) || !finite( high ) || low.x >= high.x ||
                low.y >= high.y || low.z >= high.z)
                return false;
            component.bounds.setBounds( low, high );
            if (name == "hull")
            {
                hull = true;
                // Same footprint selection as battle_runtime._sample_ground_plane.
                state.length = std::max( 3.0, double( std::fabs( low.z ) + std::fabs( high.z ) ) );
                state.width = std::max( 2.0, double( std::fabs( low.x ) + std::fabs( high.x ) ) );
            }
            else
            {
                chassis = true;
                state.groundBottom = low.y + component.offset.y;
            }
        }
        if (!finite( component.offset ) || !component.model)
            return false;
        state.components.push_back( component );
    }
    if (!chassis || !hull)
    {
        ERROR_MSG( "VehicleScene: driving requires chassis/hull collision bounds and native terrain\n" );
        return false;
    }
    state.space = space;
    state.x = origin.x; state.y = origin.y; state.z = origin.z;
    state.transform.setTranslate( origin );
    state.configured = true;
    INFO_MSG( "VehicleScene: offline controller configured; W/S drive, A/D steer, Space handbrake; collision-bound box preview, terrain class 0\n" );
    return true;
}

void VehicleScene::advance( float elapsed )
{
    State & state = *state_;
    if (!state.configured || !std::isfinite( elapsed ) || elapsed <= 0.f)
        return;
    if (!state.space || state.space->cleared() || state.space->loadStatus() < 1.f)
    {
        state.speed = state.omega = 0.0;
        state.supportStatus( false );
        return;
    }
    const double throttle = int( InputDevices::isKeyDown( KeyCode::KEY_W ) ) -
        int( InputDevices::isKeyDown( KeyCode::KEY_S ) );
    const double steer = int( InputDevices::isKeyDown( KeyCode::KEY_D ) ) -
        int( InputDevices::isKeyDown( KeyCode::KEY_A ) );
    const bool handbrake = InputDevices::isKeyDown( KeyCode::KEY_SPACE );
    // Existing integrator's 0.1-second slice, retaining the complete elapsed
    // interval by substepping instead of dropping a delayed frame's remainder.
    double remaining = elapsed;
    while (remaining > 0.0)
    {
        const double slice = std::min( remaining, 0.1 );
        state.step( slice, throttle, steer, handbrake );
        remaining -= slice;
    }
}

void VehicleScene::clear() { delete state_; state_ = new State(); }
bool VehicleScene::supported() const { return state_->configured && state_->supported; }
const Matrix & VehicleScene::transform() const { return state_->transform; }

BW_END_NAMESPACE
