#include "pch.hpp"
#include "stock_vehicle_filter.hpp"
#include "vehicle_physics_binding.hpp"
#include "stock_avatar_filter_helper.hpp"
#include "vehicle_appearance.hpp"

#include "avatar_filter.hpp"
#include "py_avatar_filter.hpp"
#include "romp/resource_ref.hpp"
#include "entity.hpp"
#include "py_entity.hpp"
#include "script_player.hpp"
#include "connection_control.hpp"
#include "space/entity_embodiment.hpp"
#include "stock_scene_prediction.hpp"
#include "pyscript/script_math.hpp"
#include "script/init_time_job.hpp"
#include "math/angle.hpp"
#include "wot/stock_gun_history.hpp"
#include "wot/vehicle_physics.hpp"

#include <algorithm>
#include <array>
#include <memory>
#include <vector>
#include <cfloat>

BW_BEGIN_NAMESPACE

int StockVehicleFilter_token = 1;

namespace
{
Vector3 engineVector( wot::Vector3 value )
{
    return Vector3( value.x, value.y, value.z );
}
wot::Vector3 coreVector( const Vector3 & value )
{
    return { value.x, value.y, value.z };
}

struct ChassisOrientationSample
{
    Vector3 angles;
    double time;
};

struct StockVehicleFilterGeometry
{
    float radius = -1.f;
    float planarRadiusSquared = -1.f;
    float verticalRadius = 0.f;
    float minimumY = 0.f;
    float maximumY = 0.f;
    float boundsAge = 1.4f;
    Vector3 local[7];
    Vector3 points[7];
    Vector3 edges[6][2];
    PyObjectPtr model;

    bool load( Entity & owner )
    {
        IEntityEmbodiment * embodiment = owner.pPrimaryEmbodiment();
        if (!embodiment)
            return false;
        PyObject * object = embodiment->scriptObject().get();
        BoundingBox bounds;
        if (!stockVehicleModelBounds( object, bounds ) || bounds.insideOut())
            return false;
        model = object;
        const Vector3 minimum = bounds.minBounds();
        const Vector3 maximum = bounds.maxBounds();
        const float width = std::max( maximum.x - minimum.x, maximum.z - minimum.z );
        planarRadiusSquared = width * 0.5f * width;
        radius = sqrtf( planarRadiusSquared );
        const float supportY = (maximum.y - minimum.y) * 0.8f + minimum.y;
        minimumY = minimum.y - supportY;
        maximumY = maximum.y - supportY;
        local[0].set( minimum.x - 0.09f, supportY, maximum.z + 0.09f );
        local[1].set( maximum.x + 0.09f, supportY, maximum.z + 0.09f );
        local[2].set( minimum.x - 0.09f, supportY, minimum.z - 0.09f );
        local[3].set( maximum.x + 0.09f, supportY, minimum.z - 0.09f );
        const Vector3 center = (minimum + maximum) * 0.5f;
        local[4].set( center.x, minimum.y - 0.09f, center.z );
        local[5].set( center.x, maximum.y + 0.09f, center.z );
        local[6] = center;
        boundsAge = 1.4f;
        return true;
    }
    void update( const Matrix & pose, float elapsed )
    {
        for (unsigned index = 0; index < 7; ++index)
            points[index] = pose.applyPoint( local[index] );
        const unsigned segments[4][2] = { {0,1}, {1,3}, {2,3}, {0,2} };
        for (unsigned index = 0; index < 4; ++index)
        {
            edges[index][0] = points[segments[index][0]];
            edges[index][1] = points[segments[index][1]];
        }
        edges[4][0] = (points[0] + points[1]) * 0.5f;
        edges[4][1] = (points[2] + points[3]) * 0.5f;
        edges[5][0] = (points[0] + points[2]) * 0.5f;
        edges[5][1] = (points[1] + points[3]) * 0.5f;
        if (model && boundsAge > 0.4f)
        {
            PyAttachment * attachment = static_cast<PyAttachment *>( model.get() );
            BoundingBox bounds;
            attachment->worldBoundingBox( bounds, attachment->worldTransform() );
            boundsAge = 0.f;
            if (!bounds.insideOut())
            {
                const Vector3 minimum = bounds.minBounds();
                const Vector3 maximum = bounds.maxBounds();
                const Vector3 center = points[6];
                const float radiusX = std::max( fabsf( center.x - minimum.x ),
                    fabsf( maximum.x - center.x ) );
                const float radiusZ = std::max( fabsf( center.z - minimum.z ),
                    fabsf( maximum.z - center.z ) );
                const float radiusY = std::max( fabsf( center.y - minimum.y ),
                    fabsf( maximum.y - center.y ) );
                const float horizontal = std::max( radiusX, radiusZ );
                planarRadiusSquared = horizontal * horizontal;
                verticalRadius = std::max( radiusY, horizontal * 0.5f );
            }
        }
        else
            boundsAge += elapsed;
    }
};

bool stockSegmentIntersection( const Vector3 & firstStart, const Vector3 & firstEnd,
    const Vector3 & secondStart, const Vector3 & secondEnd,
    Vector3 & point, float & fraction )
{
    // #1513 006ec250 intersects in XZ, then applies its original near-parallel
    // contact-centering blend. Y is a receipt for the collision callback.
    const float firstZ = firstStart.z - firstEnd.z;
    const float firstX = firstStart.x - firstEnd.x;
    const float secondZ = secondEnd.z - secondStart.z;
    const float secondX = secondEnd.x - secondStart.x;
    const float denominator = firstX * secondZ - firstZ * secondX;
    if (fabsf( denominator ) < 0.000001f)
        return false;
    const float offsetX = firstStart.x - secondStart.x;
    const float offsetZ = firstStart.z - secondStart.z;
    const float inverse = 1.f / denominator;
    fraction = (offsetX * secondZ - offsetZ * secondX) * inverse;
    const float otherFraction = (offsetZ * firstX - offsetX * firstZ) * inverse;
    if (!(fraction >= 0.f && fraction <= 1.f && otherFraction >= 0.f && otherFraction <= 1.f))
        return false;
    Vector2 first( firstEnd.x - firstStart.x, firstEnd.z - firstStart.z );
    Vector2 second( secondEnd.x - secondStart.x, secondEnd.z - secondStart.z );
    first.normalise();
    second.normalise();
    const float parallel = fabsf( first.dotProduct( second ) );
    if (parallel >= 0.85f)
    {
        const float weight = (parallel - 0.85f) * 6.6666665f;
        fraction = (1.f - weight) * fraction + weight * 0.5f;
    }
    point = firstStart + (firstEnd - firstStart) * fraction;
    point.y = (secondStart.y + firstStart.y) * 0.5f;
    return true;
}

struct StockVehicleFilterState
{
    float width = 0.f;
    float minimumNormalY = 0.f;
    float velocityErrorCompensation = 0.5f;
    float groundFilterRate = 0.2f;
    Vector3 hullLocalPosition = Vector3::zero();
    Vector3 velocity = Vector3::zero();
    Vector3 acceleration = Vector3::zero();
    Vector3 predictedVelocity = Vector3::zero();
    Vector3 predictedAngularVelocity = Vector3::zero();
    Vector3 predictedAcceleration = Vector3::zero();
    Vector3 predictedAngularAcceleration = Vector3::zero();
    Vector3 position = Vector3::zero();
    Vector3 leftPosition = Vector3::zero();
    Vector3 rightPosition = Vector3::zero();
    Vector3 strafePosition = Vector3::zero();
    float strafeYaw = 0.f;
    float viewerDistance = -1.f;
    float displacement = 0.f;
    float leftDisplacement = 0.f;
    float rightDisplacement = 0.f;
    float gunDisplacement = 0.f;
    float gunYaw = 0.f;
    float averageSpeed = 0.f;
    float averageRotationSpeed = 0.f;
    float instantaneousSpeed = 0.f;
    float instantaneousRotationSpeed = 0.f;
    float leftTrackSpeed = 0.f;
    float rightTrackSpeed = 0.f;
    float longitudinalSpeed = 0.f;
    float strafeSpeed = 0.f;
    float compressionElapsed = 0.f;
    float compressionRate = 0.f;
    float strafeWeight = 0.f;
    unsigned leftContacts = 0;
    unsigned rightContacts = 0;
    bool leftTrackLocked = false;
    bool rightTrackLocked = false;
    bool allowStrafeCompensation = false;
    bool isStrafing = false;
    bool wasStrafing = false;
    bool placingOnGround = false;
    bool forceGroundMatrix = false;
    bool compensateChassisRotation = true;
    bool enforceSpringHardLength = true;
    bool compensateHullPosition = true;
    bool hasInputKeys = false;
    float forwardInput = 0.f;
    float turnInput = 0.f;
    std::array<float, 6> springLengths{};
    Matrix body = Matrix::identity;
    Matrix placingCompensation = Matrix::identity;
    Matrix ground = Matrix::identity;
    Matrix groundFiltered = Matrix::identity;
    Matrix stabilised = Matrix::identity;
    Matrix turret = Matrix::identity;
    Matrix gun = Matrix::identity;
    PyObjectPtr physics;
    PyObjectPtr collisionCallback;
    WeakPyPtr<PyEntity> owner;
    float maxMove = FLT_MAX;
    float maxTime = 2.f;
    wot::StockGunAngleHistory gunHistory;
    std::vector<ChassisOrientationSample> chassisHistory;
    StockVehicleFilterGeometry geometry;
    float traveledSinceCollision = 0.f;
    bool collidingWithEntity = false;

    // #1513 006ea410: presentation-side lateral compensation uses the
    // distance to the viewer, without changing any physics body or input.
    void compensateStrafe( Vector3 & point, float yaw, float elapsed )
    {
        if (!allowStrafeCompensation)
        {
            strafePosition = point;
            strafeYaw = yaw;
            return;
        }
        Vector3 delta( point.x - strafePosition.x, 0.f, point.z - strafePosition.z );
        if (delta.lengthSquared() <= 0.000001f)
            return;
        float yawChange = fabsf( yaw - strafeYaw );
        if (yawChange > 0.0004f)
        {
            const float fraction = std::max( 0.f, std::min( 1.f, yawChange * 20.f ) );
            strafePosition = point * fraction + strafePosition * (1.f - fraction);
            delta *= fraction;
        }
        const float height = point.y;
        point.y = 0.f;
        strafePosition.y = 0.f;
        Vector3 unit = delta;
        unit.normalise();
        Matrix facing;
        facing.setRotateY( yaw );
        const Vector3 forward = facing.applyVector( Vector3( 0.f, 0.f, 1.f ) );
        const float projection = forward.dotProduct( unit );
        if (projection < 0.995f && projection > -0.995f)
        {
            const Vector3 projected = strafePosition + forward * (delta.length() * projection);
            const float deviation = (projected - point).length();
            const float distanceWeight = (viewerDistance - 30.f) * 0.033333335f;
            float weight = 0.f;
            if (deviation >= 0.1f)
            {
                if (distanceWeight >= 0.f)
                    weight = std::min( distanceWeight,
                        1.f - std::min( elapsed * 0.5f / deviation, 0.5f ) );
            }
            else
                weight = std::max( 0.f, std::min( 1.f, distanceWeight ) );
            point = projected * weight + point * (1.f - weight);
        }
        point.y = height;
        strafeYaw = yaw;
        strafePosition = point;
    }

    // #1513 006eddc0. Ray preparation and queries use the same provider as
    // WGVehiclePhysics; this filter never owns a collision world or a body.
    bool placeOnGround( const Matrix & candidate, SpaceID spaceID,
        float elapsed, bool publish, Matrix & result, bool * grounded = NULL )
    {
        if (grounded)
            *grounded = false;
        if (!physics)
        {
            result = candidate;
            return true;
        }
        wot::VehiclePhysics * core = unwrapStockVehiclePhysics( physics.get() );
        if (!core)
            return false;
        const std::vector<wot::VehicleDamperSpring> & left = core->damper_springs( true );
        const std::vector<wot::VehicleDamperSpring> & right = core->damper_springs( false );
        if (left.size() < 2 || right.size() < 2)
        {
            // The original guard retains the incoming pose when the vehicle
            // has no pair of carrying points on each side.
            result = candidate;
            return true;
        }
        wot::VehicleCollisionProvider * query = core->collision_provider();
        if (!query)
        {
            PyErr_SetString( PyExc_RuntimeError,
                "WGVehicleFilter requires its vehicle's collision provider" );
            return false;
        }
        struct Ray
        {
            Vector3 origin;
            Vector3 direction;
            float length;
            float hardLength;
        };
        Ray rays[6];
        const Vector3 down = -candidate[1];
        const Vector3 center = engineVector( core->center_of_mass() );
        const wot::VehicleDamperSpring * springs[] =
            { &left.front(), &left.back(), &right.back(), &right.front() };
        const unsigned slots[] = { 0, 1, 3, 4 };
        BoundingBox bounds;
        for (unsigned index = 0; index < 4; ++index)
        {
            const wot::VehicleDamperSpring & spring = *springs[index];
            Ray & ray = rays[slots[index]];
            ray.origin = candidate.applyPoint( engineVector( spring.mount_point ) + center ) -
                down * spring.indent;
            ray.direction = down;
            ray.length = spring.carrying_length + spring.indent;
            ray.hardLength = spring.hard_ratio * spring.carrying_length + spring.indent;
            bounds.addBounds( ray.origin );
            bounds.addBounds( ray.origin + ray.direction * ray.length );
        }
        const unsigned mids[] = { 5, 2 };
        for (unsigned side = 0; side < 2; ++side)
        {
            const Ray & first = rays[side * 3];
            const Ray & last = rays[side * 3 + 1];
            Ray & middle = rays[mids[side]];
            middle.origin = (first.origin + last.origin) * 0.5f;
            middle.direction = down;
            middle.length = (first.length + last.length) * 0.5f;
            middle.hardLength = (first.hardLength + last.hardLength) * 0.5f;
        }
        const wot::VehicleCollisionBounds queryBounds =
            { coreVector( bounds.minBounds() ), coreVector( bounds.maxBounds() ) };
        if (!query->prepare( queryBounds, spaceID, core->visibility_mask() ))
        {
            PyErr_SetString( PyExc_RuntimeError,
                "WGVehicleFilter could not prepare the vehicle collision scene" );
            return false;
        }
        std::array<float, 6> distances;
        Vector3 points[6];
        unsigned contacts[2] = { 0, 0 };
        float minimumHit = 100.f;
        for (unsigned index = 0; index < 6; ++index)
        {
            const Ray & ray = rays[index];
            wot::VehicleCollisionRay input =
                { coreVector( ray.origin ), coreVector( ray.direction ), ray.length };
            wot::VehicleCollisionRayHit hit;
            const bool collided = query->query_ray( input, hit );
            float distance = collided ? hit.distance : ray.length;
            if (collided && enforceSpringHardLength && !grounded)
                distance = std::max( distance, ray.hardLength );
            distances[index] = distance;
            points[index] = ray.origin + ray.direction * distance;
            if (collided)
            {
                ++contacts[(index == 0 || index == 1 || index == 5) ? 0 : 1];
                minimumHit = std::min( minimumHit, hit.distance );
            }
        }
        Vector3 normals[2];
        for (unsigned triangle = 0; triangle < 2; ++triangle)
        {
            const Vector3 * pointsForTriangle = points + triangle * 3;
            normals[triangle].crossProduct( pointsForTriangle[2] - pointsForTriangle[0],
                pointsForTriangle[1] - pointsForTriangle[0] );
            normals[triangle].normalise();
        }
        Vector3 normal = (normals[0] + normals[1]) * 0.5f;
        normal.normalise();
        Vector3 forward = normal.crossProduct( -candidate[0] );
        forward.normalise();
        Vector3 origin = candidate.applyToOrigin();
        // Preserve the original summation order of the six support heights.
        origin.y = (points[1].y + points[0].y + points[2].y +
            points[3].y + points[4].y + points[5].y) * 0.16666667f;
        Matrix inverse;
        inverse.lookAt( origin, forward, normal );
        if (!result.invert( inverse ))
        {
            PyErr_SetString( PyExc_ValueError,
                "WGVehicleFilter ground support produced a singular matrix" );
            return false;
        }
        if (grounded)
            *grounded = (rays[0].length + rays[1].length +
                rays[3].length + rays[4].length) * 0.25f > minimumHit;
        if (publish)
        {
            Matrix adjusted = inverse;
            if (compensateHullPosition)
                adjusted.translation( adjusted.applyToOrigin() - hullLocalPosition );
            placingCompensation.multiply( candidate, adjusted );
            if (compensateHullPosition)
                placingCompensation.translation( placingCompensation.applyToOrigin() +
                    placingCompensation.applyVector( hullLocalPosition ) );
            leftContacts = contacts[0];
            rightContacts = contacts[1];
            compressionElapsed += elapsed;
            if (compressionElapsed > 0.05f)
            {
                float compressed[6];
                for (unsigned index = 0; index < 6; ++index)
                    compressed[index] = std::max( 0.f, springLengths[index] - distances[index] );
                compressionRate = (compressed[5] + compressed[4] + compressed[3] +
                    compressed[2] + compressed[1] + compressed[0]) / compressionElapsed;
                springLengths = distances;
                compressionElapsed = 0.f;
            }
        }
        return true;
    }

    // #1513 006ec880: retain the candidate translation and sample only the
    // chassis orientation history over the original .1-second window.
    Matrix sampleChassisOrientation( double time, const Matrix & candidate ) const
    {
        if (chassisHistory.empty())
            return candidate;
        Vector3 angles = chassisHistory.front().angles;
        for (size_t end = chassisHistory.size(); end > 0; --end)
        {
            const size_t index = end - 1;
            if (!(chassisHistory[index].time < time))
                continue;
            angles = chassisHistory[index].angles;
            if (end != chassisHistory.size())
            {
                const ChassisOrientationSample & next = chassisHistory[end];
                const double fraction = std::max( 0.0,
                    std::min( 1.0, (time - (next.time - 0.1)) * 10.0 ) );
                for (unsigned axis = 0; axis < 3; ++axis)
                    angles[axis] = Angle::normalise( angles[axis] + Angle::normalise(
                        static_cast<float>( Angle::normalise( next.angles[axis] -
                            angles[axis] ) * fraction ) ) );
            }
            break;
        }
        Matrix result;
        result.setRotate( angles.x, angles.y, angles.z );
        result.translation( candidate.applyToOrigin() );
        return result;
    }

    // #1513 006ef230: low-pass only the ground pitch/roll, preserving yaw
    // and translation. This has no effect on authoritative body integration.
    void filterGround( const Matrix & value, float elapsed )
    {
        Vector3 oldForward = groundFiltered[2];
        Vector3 oldRight = groundFiltered[0];
        Vector3 oldUp = groundFiltered[1];
        oldForward.normalise();
        oldRight.normalise();
        oldUp.normalise();
        const float oldPitch = -asinf( std::max( -1.f, std::min( 1.f, oldForward.y ) ) );
        const float oldRoll = atan2f( oldRight.y, oldUp.y );
        Vector3 forward = value[2];
        Vector3 right = value[0];
        Vector3 up = value[1];
        forward.normalise();
        right.normalise();
        up.normalise();
        const float pitch = -asinf( std::max( -1.f, std::min( 1.f, forward.y ) ) );
        const float roll = atan2f( right.y, up.y );
        const float fraction = elapsed / (0.15923566f / groundFilterRate + elapsed);
        groundFiltered.setRotate( atan2f( forward.x, forward.z ),
            oldPitch + (pitch - oldPitch) * fraction,
            oldRoll + (roll - oldRoll) * fraction );
        groundFiltered.translation( value.applyToOrigin() );
    }

    // #1513 006eee00: movement/track receipts are measured from rendered
    // poses. These values do not advance the physics core's track state.
    void publishMotion( float elapsed, const Matrix & value, float sampledGunYaw )
    {
        const Vector3 next = value.applyToOrigin();
        const Vector3 forward = value.applyVector( Vector3( 0.f, 0.f, 1.f ) );
        Vector3 delta = next - position;
        displacement = delta.length();
        if (forward.dotProduct( delta ) < 0.f)
            displacement = -displacement;
        position = next;
        const Vector3 left = next + value.applyVector( Vector3( width * -0.5f, 0.f, 0.f ) );
        delta = left - leftPosition;
        leftDisplacement = leftTrackLocked ? leftTrackSpeed * elapsed : delta.length();
        if (!leftTrackLocked && forward.dotProduct( delta ) < 0.f)
            leftDisplacement = -leftDisplacement;
        leftPosition = left;
        const Vector3 right = next + value.applyVector( Vector3( width * 0.5f, 0.f, 0.f ) );
        delta = right - rightPosition;
        rightDisplacement = rightTrackLocked ? rightTrackSpeed * elapsed : delta.length();
        if (!rightTrackLocked && forward.dotProduct( delta ) < 0.f)
            rightDisplacement = -rightDisplacement;
        rightPosition = right;
        gunDisplacement = sampledGunYaw - gunYaw;
        if (fabsf( gunDisplacement ) > MATH_PI)
            gunDisplacement += gunDisplacement < 0.f ? MATH_PI * 2.f : -MATH_PI * 2.f;
        gunYaw = sampledGunYaw;
    }
};
}

class StockVehicleFilter;
class PyWGVehicleFilter;
static void setStockLagDetection( PyWGVehicleFilter * filter, bool enabled );

class PyWGVehicleFilter : public PyFilter
{
    Py_Header( PyWGVehicleFilter, PyFilter )
public:
    PyWGVehicleFilter() : PyFilter( &s_type_ ), state_( new StockVehicleFilterState() ) {}
    virtual ~PyWGVehicleFilter() { setStockLagDetection( this, false ); }
    const std::shared_ptr<StockVehicleFilterState> & state() const { return state_; }
    void owner( PyEntity * value ) { state_->owner = WeakPyPtr<PyEntity>( value ); }
    bool attached() const { return this->pAttachedFilter() != NULL; }
    MatrixProviderPtr matrixProvider( unsigned kind );
    Vector4ProviderPtr vectorProvider( unsigned kind );
    PY_RO_ATTRIBUTE_DECLARE( this->matrixProvider( 0 ), bodyMatrix )
    PY_RO_ATTRIBUTE_DECLARE( this->matrixProvider( 1 ), placingCompensationMatrix )
    PY_RO_ATTRIBUTE_DECLARE( this->matrixProvider( 2 ), groundPlacingMatrix )
    PY_RO_ATTRIBUTE_DECLARE( this->matrixProvider( 3 ), groundPlacingMatrixFiltered )
    PY_RO_ATTRIBUTE_DECLARE( this->matrixProvider( 4 ), stabilisedMatrix )
    PY_RO_ATTRIBUTE_DECLARE( this->matrixProvider( 5 ), turretMatrix )
    PY_RO_ATTRIBUTE_DECLARE( this->matrixProvider( 6 ), gunMatrix )
    PY_RO_ATTRIBUTE_DECLARE( this->vectorProvider( 0 ), movementInfo )
    PY_RO_ATTRIBUTE_DECLARE( this->vectorProvider( 1 ), speedInfo )
    PY_RO_ATTRIBUTE_DECLARE( this->vectorProvider( 2 ), physicsInfo )
    PY_RW_ATTRIBUTE_DECLARE( state_->width, vehicleWidth )
    PY_RW_ATTRIBUTE_DECLARE( state_->minimumNormalY, vehicleMinNormalY )
    PY_RW_ATTRIBUTE_DECLARE( state_->allowStrafeCompensation, allowStrafeCompensation )
    PY_RW_ATTRIBUTE_DECLARE( state_->velocityErrorCompensation, velocityErrorCompensation )
    PY_RW_ATTRIBUTE_DECLARE( state_->hullLocalPosition, hullLocalPosition )
    PY_RW_ATTRIBUTE_DECLARE( state_->placingOnGround, placingOnGround )
    PY_RW_ATTRIBUTE_DECLARE( state_->maxMove, maxMove )
    PY_RW_ATTRIBUTE_DECLARE( state_->maxTime, maxTime )
    PY_RW_ATTRIBUTE_DECLARE( state_->collisionCallback, vehicleCollisionCallback )
    PY_RO_ATTRIBUTE_DECLARE( state_->velocity, velocity )
    PY_RO_ATTRIBUTE_DECLARE( state_->acceleration, acceleration )
    PY_RO_ATTRIBUTE_DECLARE( state_->strafeSpeed, strafeSpeed )
    PY_RO_ATTRIBUTE_DECLARE( state_->longitudinalSpeed, longitudinalSpeed )
    PY_RO_ATTRIBUTE_DECLARE( fabsf( state_->predictedAngularVelocity.dotProduct(
        state_->body[1] ) ), angularSpeed )
    PY_RO_ATTRIBUTE_DECLARE( state_->compressionRate, suspCompressionRate )
    PY_RO_ATTRIBUTE_DECLARE( state_->leftContacts, numLeftTrackContacts )
    PY_RO_ATTRIBUTE_DECLARE( state_->rightContacts, numRightTrackContacts )
    PY_RW_ATTRIBUTE_DECLARE( state_->isStrafing, isStrafing )
    PY_RO_ATTRIBUTE_DECLARE( this->attached() ? state_->averageSpeed : 0.f, averageSpeed )
    PY_RO_ATTRIBUTE_DECLARE( this->attached() ? state_->averageRotationSpeed : 0.f,
        averageRotationSpeed )
    PY_RO_ATTRIBUTE_DECLARE( this->attached() ? state_->instantaneousSpeed : 0.f,
        instantaneousSpeed )
    PY_RO_ATTRIBUTE_DECLARE( this->attached() ? state_->instantaneousRotationSpeed : 0.f,
        instantaneousRotationSpeed )
    PY_RO_ATTRIBUTE_DECLARE( 0.5f, defaultVelocityErrorCompensation )
    float latency() const;
    void latency( float value );
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( float, latency, latency )
    PY_RW_ATTRIBUTE_DECLARE( AvatarFilterSettings::s_latencyVelocity_, velLatency )
    PY_RW_ATTRIBUTE_DECLARE( AvatarFilterSettings::s_latencyMinimum_, minLatency )
    PY_RW_ATTRIBUTE_DECLARE( AvatarFilterSettings::s_latencyFrames_, latencyFrames )
    PY_RW_ATTRIBUTE_DECLARE( AvatarFilterSettings::s_latencyCurvePower_, latencyCurvePower )

    bool setVehiclePhysics( ScriptObject value )
    {
        if (!unwrapStockVehiclePhysics( value.get() ))
            return false;
        state_->physics = value.get();
        return true;
    }
    PY_AUTO_METHOD_DECLARE( RETOK, setVehiclePhysics, ARG( ScriptObject, END ) )
    PyObject * getVehiclePhysics()
    {
        PyObject * value = state_->physics ? state_->physics.get() : Py_None;
        Py_INCREF( value );
        return value;
    }
    PY_AUTO_METHOD_DECLARE( RETOWN, getVehiclePhysics, END )
    void forceGroundPlacingMatrix( bool value ) { state_->forceGroundMatrix = value; }
    PY_AUTO_METHOD_DECLARE( RETVOID, forceGroundPlacingMatrix, ARG( bool, END ) )
    void addTriangle( Vector3, Vector3, Vector3 )
    {
        // Exact #1513 006e2af0 validates these three Vector3 arguments then
        // returns None, with no append or geometry side effect.
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, addTriangle,
        ARG( Vector3, ARG( Vector3, ARG( Vector3, END ) ) ) )
    PY_METHOD_DECLARE( py_syncGunAngles )
    PY_METHOD_DECLARE( py_interpolateStabilisedMatrix )
    bool enableStabilisedMatrix( bool value )
    {
        if (!this->attached())
        {
            PyErr_SetString( PyExc_ValueError, "WGVehicleFilter is not attached to an entity" );
            return false;
        }
        state_->compensateChassisRotation = value;
        return true;
    }
    PY_AUTO_METHOD_DECLARE( RETOK, enableStabilisedMatrix, ARG( bool, END ) )
    bool notifyInputKeysDown( float forward, float turn )
    {
        if (!this->attached())
        {
            PyErr_SetString( PyExc_ValueError, "WGVehicleFilter is not attached to an entity" );
            return false;
        }
        // #1513 006ec460 stores the two original scalar command directions;
        // reverse motion flips the remembered rotation direction.
        state_->hasInputKeys = true;
        state_->forwardInput = forward;
        state_->turnInput = forward == -1.f ? -turn : turn;
        return true;
    }
    PY_AUTO_METHOD_DECLARE( RETOK, notifyInputKeysDown,
        ARG( float, ARG( float, END ) ) )
    void enableLagDetection( bool enabled ) { setStockLagDetection( this, enabled ); }
    PY_AUTO_METHOD_DECLARE( RETVOID, enableLagDetection, ARG( bool, END ) )
    static PyWGVehicleFilter * New() { return new PyWGVehicleFilter(); }
    PY_AUTO_FACTORY_DECLARE( PyWGVehicleFilter, END )
protected:
    virtual Filter * getNewFilter();
private:
    std::shared_ptr<StockVehicleFilterState> state_;
};

class StockVehicleFilter : public Filter, public StockAvatarFilterHelper::Listener
{
public:
    struct Lifetime { bool alive = true; };
    struct CollisionReceipt
    {
        PyObjectPtr callback;
        PyObjectPtr first;
        PyObjectPtr second;
        Vector3 point;
        double time;
    };
    StockVehicleFilter( PyWGVehicleFilter * owner,
        const std::shared_ptr<StockVehicleFilterState> & state ) :
        Filter( owner ), state_( state ), helper_( std::make_shared<StockAvatarFilterHelper>( this->environment() ) )
    {
        helper_->listener( this );
        // 006e9ed0 transfers the sampled matrices, but starts a new gun
        // history when the shorter-lived native filter is recreated.
        state_->gunHistory = wot::StockGunAngleHistory();
    }
    virtual ~StockVehicleFilter()
    {
        lifetime_->alive = false;
        helper_->listener( NULL );
    }
    StockAvatarFilterHelper & helper() { return *helper_; }
    const StockAvatarFilterHelper & helper() const { return *helper_; }
    Entity * entity() const
    {
        const PyEntity * owner = state_->owner.get();
        return owner && !owner->isDestroyed() ? owner->pEntity().get() : NULL;
    }
    virtual void reset( double time ) { helper_->reset( time ); }
    virtual bool getLastInput( double & time, SpaceID & spaceID, EntityID & vehicleID,
        Position3D & position, Vector3 & error, Direction3D & direction ) const
    {
        return helper_->getLastInput( time, spaceID, vehicleID, position, error, direction );
    }
    virtual void input( double time, SpaceID spaceID, EntityID vehicleID,
        const Position3D & position, const Vector3 & error, const Direction3D & direction )
    {
        std::shared_ptr<Lifetime> lifetime = lifetime_;
        std::shared_ptr<StockAvatarFilterHelper> activeHistory = helper_;
        std::shared_ptr<StockVehicleFilterState> state = state_;
        Entity * player = ScriptPlayer::entity();
        float distance = -1.f;
        if (player)
            distance = std::max( 0.f, (player->position() - position).length() - error.length() );
        Entity * ownVehicle = NULL;
        if (player)
        {
            // The original input path resolves the player's attached vehicle
            // through the same Python fallback before deciding whether to
            // reset a formerly distant replica's history.
            ScriptObject attached = player->pPyEntity().callMethod( "getVehicleAttached",
                ScriptErrorClear(), true );
            if (attached && PyEntity::Check( attached ))
            {
                PyEntity * attachedEntity = static_cast<PyEntity *>( attached.get() );
                if (!attachedEntity->isDestroyed())
                    ownVehicle = attachedEntity->pEntity().get();
            }
        }
        if (!lifetime->alive)
            return;
        if (this->entity() != ownVehicle && state->viewerDistance != -1.f &&
            distance <= 50.f && state->viewerDistance - distance >= 50.f &&
            state->predictedVelocity.lengthSquared() < 1.f)
            activeHistory->reset( time );
        state->viewerDistance = distance;
        double oldTime;
        SpaceID oldSpace;
        EntityID oldVehicle;
        Position3D oldPosition;
        Vector3 oldError;
        Direction3D oldDirection;
        if (activeHistory->getLastInput( oldTime, oldSpace, oldVehicle, oldPosition, oldError, oldDirection ) &&
            (time - oldTime > static_cast<double>( state->maxTime ) ||
             (position - oldPosition).length() - error.length() >= state->maxMove))
            activeHistory->reset( time );
        activeHistory->input( time, spaceID, vehicleID, position, error, direction );
    }
    virtual void output( double time, MovementFilterTarget & target )
    {
        Entity & owner = static_cast<Entity &>( target );
        EntityPtr heldOwner( &owner );
        std::shared_ptr<Lifetime> lifetime = lifetime_;
        const float elapsed = static_cast<float>( time - helper_->lastOutputTime() );
        if (elapsed == 0.f)
            return;
        double inputTime;
        SpaceID inputSpace;
        EntityID inputVehicle;
        Position3D inputPosition;
        Vector3 inputError;
        Direction3D inputDirection;
        if (!helper_->getLastInput( inputTime, inputSpace, inputVehicle,
                inputPosition, inputError, inputDirection ))
            return;
        const float oldYaw = owner.direction().yaw;
        SpaceID spaceID;
        EntityID vehicleID;
        Position3D point;
        Vector3 velocity;
        Quaternion orientation;
        failed_ = false;
        std::shared_ptr<StockAvatarFilterHelper> activeHistory = helper_;
        activeHistory->outputQuaternion( time, spaceID, vehicleID, point, velocity, orientation );
        if (!lifetime->alive || owner.isDestroyed() || owner.pFilter() != this)
            return;
        const bool predictionFailed = failed_;
        if (predictionFailed && PyErr_Occurred())
            PyErr_Print();
        std::vector<CollisionReceipt> callbacks;
        callbacks.swap( collisionReceipts_ );
        for (size_t index = 0; index < callbacks.size(); ++index)
        {
            const CollisionReceipt & receipt = callbacks[index];
            PyObjectPtr args( Py_BuildValue( "(OONd)", receipt.first.get(), receipt.second.get(),
                Script::getData( receipt.point ), receipt.time ), PyObjectPtr::STEAL_REFERENCE );
            if (args)
            {
                PyObjectPtr result( PyObject_CallObject( receipt.callback.get(), args.get() ),
                    PyObjectPtr::STEAL_REFERENCE );
                if (!result)
                    PyErr_Print();
            }
            else
                PyErr_Print();
            if (!lifetime->alive || owner.isDestroyed() || owner.pFilter() != this)
                return;
        }
        if (predictionFailed)
            return;
        Matrix inputRotation;
        inputRotation.setRotate( orientation );
        Direction3D inputOutputDirection;
        inputOutputDirection.yaw = inputRotation.yaw();
        inputOutputDirection.pitch = inputRotation.pitch();
        inputOutputDirection.roll = inputRotation.roll();
        target.setSpaceVehiclePositionAndDirection( spaceID, vehicleID, point, inputOutputDirection );
        if (!lifetime->alive || owner.isDestroyed() || owner.pFilter() != this)
            return;
        owner.localVelocity( velocity );
        if (owner.waitingForVehicle())
            return;
        point = owner.position();
        const Direction3D commonDirection = owner.direction();
        Matrix candidate;
        candidate.setRotate( commonDirection.yaw, commonDirection.pitch, commonDirection.roll );
        candidate.translation( point );
        float yaw = state_->gunYaw;
        float pitch = 0.f;
        if (state_->gunHistory.sample( time - helper_->latency(),
                helper_->previousWaypointTime(), yaw, pitch ))
        {
            state_->turret.setRotateY( yaw );
            state_->gun.setRotateX( pitch );
        }
        if (!state_->isStrafing)
        {
            if (state_->wasStrafing)
            {
                state_->strafePosition = state_->position;
                state_->strafeYaw = oldYaw;
            }
            state_->strafeWeight = 1.f;
        }
        else
            state_->strafeWeight = std::max( 0.f, state_->strafeWeight - (elapsed + elapsed) );
        state_->wasStrafing = state_->isStrafing;
        if (state_->strafeWeight > 0.f)
        {
            Vector3 compensated = point;
            state_->compensateStrafe( compensated, candidate.yaw(), elapsed );
            point = compensated * state_->strafeWeight + point * (1.f - state_->strafeWeight);
            candidate.translation( point );
        }
        state_->body = candidate;
        if (state_->compensateChassisRotation)
            state_->stabilised = state_->sampleChassisOrientation(
                time - helper_->latency(), candidate );
        Matrix placed;
        if (state_->placingOnGround || state_->forceGroundMatrix)
        {
            if (!state_->placeOnGround( candidate, spaceID, elapsed, true, placed ))
            {
                PyErr_Print();
                return;
            }
        }
        else
            placed = candidate;
        state_->ground = placed;
        state_->filterGround( placed, elapsed );
        Matrix rendered = state_->placingOnGround ? placed : candidate;
        if (!state_->placingOnGround)
            state_->placingCompensation = Matrix::identity;
        const Vector3 oldVelocity = state_->velocity;
        state_->velocity = (rendered.applyToOrigin() - state_->position) * (1.f / elapsed);
        state_->acceleration = (state_->velocity - oldVelocity) * (1.f / elapsed);
        Direction3D direction;
        direction.yaw = rendered.yaw();
        direction.pitch = rendered.pitch();
        direction.roll = rendered.roll();
        Position3D localPosition = rendered.applyToOrigin();
        Direction3D localDirection = direction;
        this->transformFromCommon( spaceID, vehicleID, localPosition, localDirection );
        target.setSpaceVehiclePositionAndDirection( spaceID, vehicleID, localPosition, localDirection );
        if (!lifetime->alive || owner.isDestroyed() || owner.pFilter() != this)
            return;
        state_->publishMotion( elapsed, rendered, yaw );
        float rotation = direction.yaw - oldYaw;
        if (fabsf( rotation ) > MATH_PI)
            rotation += rotation < 0.f ? MATH_PI * 2.f : -MATH_PI * 2.f;
        const float window = std::min( elapsed, 0.2f );
        state_->averageRotationSpeed =
            ((0.2f - window) * state_->averageRotationSpeed + rotation) * 5.f;
        state_->averageSpeed =
            ((0.2f - window) * state_->averageSpeed + state_->displacement) * 5.f;
        state_->instantaneousSpeed = state_->displacement * (1.f / window);
        state_->instantaneousRotationSpeed = rotation * (1.f / window);
        state_->strafeSpeed = state_->velocity.dotProduct( state_->body[0] );
        state_->longitudinalSpeed = -state_->velocity.dotProduct( state_->body[2] );
    }
    virtual void onReset()
    {
        Entity * owner = this->entity();
        if (!owner)
            return;
        state_->collidingWithEntity = false;
        state_->traveledSinceCollision = 0.f;
        state_->viewerDistance = -1.f;
        state_->strafePosition = owner->position();
        state_->position = owner->position();
        Matrix pose;
        const Direction3D direction = owner->direction();
        pose.setRotate( direction.yaw, direction.pitch, direction.roll );
        pose.translation( state_->position );
        state_->body = pose;
        state_->leftPosition = pose.applyPoint( Vector3( -state_->width * 0.5f, 0.f, 0.f ) );
        state_->rightPosition = pose.applyPoint( Vector3( state_->width * 0.5f, 0.f, 0.f ) );
        float yaw = 0.f;
        float pitch = 0.f;
        if (state_->gunHistory.sample( helper_->lastOutputTime() - helper_->latency(),
                helper_->previousWaypointTime(), yaw, pitch ))
        {
            state_->turret.setRotateY( yaw );
            state_->gun.setRotateX( pitch );
        }
        state_->gunYaw = yaw;
    }
    virtual void onWaypoint( double time, StockAvatarFilterHelper::Waypoint & current,
        StockAvatarFilterHelper::Waypoint & next );
protected:
    virtual bool tryCopyState( const MovementFilter & source )
    {
        const StockVehicleFilter * other = dynamic_cast<const StockVehicleFilter *>( &source );
        if (!other)
            return false;
        *helper_ = *other->helper_;
        helper_->listener( this );
        return true;
    }
private:
    void updateVelocityReceipts( const StockAvatarFilterHelper::Waypoint & current,
        const StockAvatarFilterHelper::Waypoint & next );
    bool predictWaypoints( double time, StockAvatarFilterHelper::Waypoint & current,
        StockAvatarFilterHelper::Waypoint & next );
    void collideEntities( const StockAvatarFilterHelper::Waypoint & current,
        const StockAvatarFilterHelper::Waypoint & next );
    std::shared_ptr<StockVehicleFilterState> state_;
    std::shared_ptr<StockAvatarFilterHelper> helper_;
    bool failed_ = false;
    std::shared_ptr<Lifetime> lifetime_ = std::make_shared<Lifetime>();
    std::vector<CollisionReceipt> collisionReceipts_;
};

void StockVehicleFilter::updateVelocityReceipts(
    const StockAvatarFilterHelper::Waypoint & current,
    const StockAvatarFilterHelper::Waypoint & next )
{
    if (current.time != current.input.time || next.time != next.input.time)
        return;
    const float elapsed = static_cast<float>( next.time - current.time );
    Quaternion inverse;
    Quaternion difference;
    D3DXQuaternionInverse( &inverse, &current.orientation );
    D3DXQuaternionMultiply( &difference, &inverse, &next.orientation );
    const float sign = difference.w <= 0.f ? -1.f : 1.f;
    difference.x *= sign;
    difference.y *= sign;
    difference.z *= sign;
    difference.w *= sign;
    const float angle = acosf( std::max( -1.f, std::min( 1.f, difference.w ) ) ) * 2.f;
    Vector3 angular( difference.x, difference.y, difference.z );
    angular.normalise();
    angular *= angle;
    const Vector3 linear = next.position - current.position;
    const float window = std::min( elapsed, 0.25f );
    const float inverseTime = 1.f / elapsed;
    const float remainder = 0.25f - window;
    for (unsigned axis = 0; axis < 3; ++axis)
    {
        // #1513 006f0060 retains these two EMA stages and operation order.
        const float linearChange = linear[axis] * inverseTime * window * 4.f -
            state_->predictedVelocity[axis] * window * 4.f;
        const float angularChange = angular[axis] * inverseTime * window * 4.f -
            state_->predictedAngularVelocity[axis] * window * 4.f;
        state_->predictedVelocity[axis] += linearChange;
        state_->predictedAngularVelocity[axis] += angularChange;
        state_->predictedAcceleration[axis] =
            (state_->predictedAcceleration[axis] * remainder +
                linearChange * inverseTime * window) * 4.f;
        state_->predictedAngularAcceleration[axis] =
            (state_->predictedAngularAcceleration[axis] * remainder +
                angularChange * inverseTime * window) * 4.f;
    }
}

void StockVehicleFilter::collideEntities( const StockAvatarFilterHelper::Waypoint & current,
    const StockAvatarFilterHelper::Waypoint & next )
{
    Entity * owner = this->entity();
    if (!owner)
        return;
    StockVehicleFilterGeometry & geometry = state_->geometry;
    if (geometry.radius < 0.f && !geometry.load( *owner ))
        return;
    state_->traveledSinceCollision += (next.position - current.position).length();
    Matrix candidate;
    candidate.setRotate( next.orientation );
    candidate.translation( next.position );
    geometry.update( candidate, static_cast<float>( next.time - current.time ) );
    struct Gather : EntityVisitor
    {
        std::vector<EntityPtr> entities;
        bool onVisitEntity( Entity * value )
        {
            entities.push_back( EntityPtr( value ) );
            return true;
        }
    } gather;
    ConnectionControl::instance().visitEntities( gather );
    state_->collidingWithEntity = false;
    for (size_t index = 0; index < gather.entities.size(); ++index)
    {
        Entity * other = gather.entities[index].get();
        if (other == owner || other->isDestroyed() || other->spaceID() != owner->spaceID())
            continue;
        StockVehicleFilter * otherFilter = dynamic_cast<StockVehicleFilter *>( other->pFilter() );
        if (!otherFilter)
            continue;
        std::shared_ptr<StockVehicleFilterState> otherState = otherFilter->state_;
        const StockVehicleFilterGeometry & otherGeometry = otherState->geometry;
        if (otherGeometry.radius < 0.f ||
            (next.position - otherState->position).length() -
                (geometry.radius + otherGeometry.radius) * 1.1f >= 0.f)
            continue;
        float best = -1.f;
        Vector3 point;
        for (unsigned first = 0; first < 6; ++first)
            for (unsigned second = 0; second < 6; ++second)
            {
                float fraction;
                Vector3 contact;
                if (stockSegmentIntersection( geometry.edges[first][0], geometry.edges[first][1],
                        otherGeometry.edges[second][0], otherGeometry.edges[second][1],
                        contact, fraction ) &&
                    (best == -1.f || fabsf( fraction - 0.5f ) < fabsf( best - 0.5f )))
                {
                    const float height = geometry.edges[first][0].y;
                    const float otherHeight = otherGeometry.edges[second][0].y;
                    if (otherGeometry.minimumY + otherHeight <= height &&
                        height <= otherGeometry.maximumY + otherHeight)
                    {
                        point = contact;
                        best = fraction;
                    }
                }
            }
        if (best == -1.f)
            continue;
        state_->collidingWithEntity = true;
        if (state_->traveledSinceCollision > 0.3f || otherState->traveledSinceCollision > 0.3f)
        {
            state_->traveledSinceCollision = 0.f;
            otherState->traveledSinceCollision = 0.f;
            if (state_->collisionCallback && PyCallable_Check( state_->collisionCallback.get() ))
            {
                CollisionReceipt receipt;
                receipt.callback = state_->collisionCallback;
                receipt.first = owner->pPyEntity().get();
                receipt.second = other->pPyEntity().get();
                receipt.point = point;
                receipt.time = next.time;
                collisionReceipts_.push_back( receipt );
            }
        }
    }
}

void StockVehicleFilter::onWaypoint( double time,
    StockAvatarFilterHelper::Waypoint & current, StockAvatarFilterHelper::Waypoint & next )
{
    std::shared_ptr<Lifetime> lifetime = lifetime_;
    this->collideEntities( current, next );
    if (helper_->extrapolating())
    {
        const bool succeeded = this->predictWaypoints( time, current, next );
        if (lifetime->alive)
            failed_ = !succeeded;
    }
    else
        this->updateVelocityReceipts( current, next );
}

bool StockVehicleFilter::predictWaypoints( double time,
    StockAvatarFilterHelper::Waypoint & current, StockAvatarFilterHelper::Waypoint & next )
{
    // #1513 006eaca0 is a presentation predictor over authoritative inputs.
    // It never steps WGVehiclePhysics or becomes the owner of the vehicle.
    const float age = static_cast<float>( time - helper_->extrapolationStart() );
    const float elapsed = static_cast<float>( next.time - current.time );
    const auto clearPrediction = [this]()
    {
        state_->predictedVelocity.setZero();
        state_->predictedAngularVelocity.setZero();
        state_->predictedAcceleration.setZero();
        state_->predictedAngularAcceleration.setZero();
    };
    if (age >= 6.f)
    {
        next.position = current.position;
        next.orientation = current.orientation;
        clearPrediction();
        return true;
    }
    Matrix rotation;
    rotation.setRotate( current.orientation );
    Vector3 forward = rotation[2];
    forward.normalise();
    const float yaw = atan2f( forward.x, forward.z );
    Matrix yawRotation;
    yawRotation.setRotate( yaw, 0.f, 0.f );
    const bool backward = state_->predictedVelocity.dotProduct( yawRotation[2] ) < 0.f;
    float linearScale = age >= 4.f ? 1.f - (age - 4.f) * 0.5f : 1.f;
    float angularScale = linearScale;
    float movementScale = 1.f;
    if (age > 0.f && state_->hasInputKeys)
    {
        if (state_->forwardInput == 0.f && state_->turnInput == 0.f)
        {
            const float fraction = std::max( 0.f, std::min( 1.f, age * 0.66f ) );
            movementScale = 1.f - fraction * fraction;
        }
        else if (backward != (state_->forwardInput == -1.f))
            movementScale = std::max( -1.f, std::min( 1.f, (1.f - age) * 1.33f ) );
        const float angularDirection = state_->predictedAngularVelocity.y > 0.f ? 1.f :
            (state_->predictedAngularVelocity.y < 0.f ? -1.f : 0.f);
        if (angularDirection != state_->turnInput)
            angularScale = (1.f - std::max( 0.f, std::min( 1.f, age + age ) )) * linearScale;
    }
    const float speed = state_->predictedVelocity.length();
    const float angularSpeed = state_->predictedAngularVelocity.length();
    Quaternion orientation = current.orientation;
    if (angularSpeed > 0.01f)
    {
        const Vector3 angular = state_->predictedAngularVelocity * angularScale * elapsed;
        Quaternion delta;
        D3DXQuaternionRotationAxis( &delta, &angular, angular.length() );
        D3DXQuaternionMultiply( &orientation, &orientation, &delta );
    }
    Vector3 predictedVelocity = Vector3::zero();
    Vector3 point = current.position;
    bool grounded = true;
    if (speed >= state_->velocityErrorCompensation || angularSpeed > 0.01f)
    {
        if (speed >= state_->velocityErrorCompensation)
        {
            rotation.setRotate( orientation );
            predictedVelocity = rotation[2] * speed * (backward ? -1.f : 1.f);
            point += predictedVelocity * elapsed * linearScale * movementScale;
        }
        Matrix candidate;
        candidate.setRotate( orientation );
        candidate.translation( point );
        Matrix placed;
        Entity * owner = this->entity();
        if (!owner)
        {
            PyErr_SetString( PyExc_RuntimeError, "WGVehicleFilter lost its entity during prediction" );
            return false;
        }
        if (!state_->placeOnGround( candidate, owner->spaceID(), elapsed, false, placed, &grounded ))
            return false;
        if (grounded)
        {
            const float fraction = std::max( 0.f, std::min( 1.f,
                elapsed / ((6.f - age) * 0.08066667f + 0.016f) ) );
            const Vector3 placedPoint = placed.applyToOrigin();
            if (!almostEqual( point, placedPoint, 0.0004f ))
                point = point * (1.f - fraction) + placedPoint * fraction;
            Quaternion groundOrientation;
            D3DXQuaternionRotationMatrix( &groundOrientation, &placed );
            if (fabsf( orientation.x - groundOrientation.x ) < 0.0004f &&
                fabsf( orientation.y - groundOrientation.y ) < 0.0004f &&
                fabsf( orientation.z - groundOrientation.z ) < 0.0004f &&
                fabsf( orientation.w - groundOrientation.w ) < 0.0004f)
                orientation = groundOrientation;
            else
                D3DXQuaternionSlerp( &orientation, &orientation, &groundOrientation, fraction );
        }
        else
        {
            Position3D terrain( point.x, -12001.f, point.z );
            bool onGround = false;
            if (this->resolveOnGroundPosition( terrain, onGround ) && onGround && point.y > terrain.y)
            {
                // 006ef7b0 installs (0,-9.82f,0) in the predictor's
                // acceleration, then 006f0ba0 performs v + a * elapsed.
                state_->predictedAcceleration.set( 0.f, -9.82f, 0.f );
                predictedVelocity = state_->predictedVelocity + state_->predictedAcceleration * elapsed;
                point = current.position + predictedVelocity * elapsed * linearScale;
                if (angularSpeed * angularScale < 0.01f)
                {
                    Vector3 direction = predictedVelocity;
                    direction.normalise();
                    rotation.setRotate( orientation );
                    if (direction.dotProduct( rotation[2] ) > 0.95f)
                    {
                        const Vector3 right = Vector3( 0.f, 1.f, 0.f ).crossProduct( direction );
                        const Vector3 up = direction.crossProduct( right );
                        Matrix basis;
                        basis.row( 0, Vector4( right.x, right.y, right.z, 0.f ) );
                        basis.row( 1, Vector4( up.x, up.y, up.z, 0.f ) );
                        basis.row( 2, Vector4( direction.x, direction.y, direction.z, 0.f ) );
                        basis.row( 3, Vector4( 0.f, 0.f, 0.f, 1.f ) );
                        D3DXQuaternionRotationMatrix( &orientation, &basis );
                    }
                }
            }
        }
    }
    state_->predictedVelocity = predictedVelocity;
    next.position = point;
    next.orientation = orientation;
    if (state_->geometry.radius >= 0.f)
    {
        bool blocked = state_->collidingWithEntity;
        if (!blocked && grounded)
        {
            rotation.setRotate( orientation );
            blocked = rotation[1].y < state_->minimumNormalY;
        }
        if (!blocked)
        {
            Entity * owner = this->entity();
            if (!owner)
            {
                PyErr_SetString( PyExc_RuntimeError, "WGVehicleFilter lost its scene-query owner" );
                return false;
            }
            if (!queryStockScenePrediction( owner->spaceID(), &state_->geometry.edges[0][0],
                    state_->owner, state_->predictedVelocity, blocked ))
                return false;
        }
        if (blocked)
        {
            next.position = current.position;
            next.orientation = current.orientation;
            clearPrediction();
        }
    }
    return true;
}

class StockFilterMatrixProvider : public MatrixProvider
{
    Py_Header( StockFilterMatrixProvider, MatrixProvider )
public:
    StockFilterMatrixProvider( PyWGVehicleFilter * owner, unsigned kind ) :
        MatrixProvider( false, &s_type_ ), owner_( owner ), kind_( kind ) {}
    virtual void matrix( Matrix & result ) const
    {
        const PyWGVehicleFilter * owner = static_cast<PyWGVehicleFilter *>( owner_.get() );
        const StockVehicleFilterState & state = *owner->state();
        const Matrix * values[] = { &state.body, &state.placingCompensation,
            &state.ground, &state.groundFiltered, &state.stabilised,
            &state.turret, &state.gun };
        result = kind_ >= 5 && !owner->attached() ? Matrix::identity : *values[kind_];
    }
private:
    PyObjectPtr owner_;
    unsigned kind_;
};
PY_TYPEOBJECT( StockFilterMatrixProvider )
PY_BEGIN_METHODS( StockFilterMatrixProvider )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockFilterMatrixProvider )
PY_END_ATTRIBUTES()

class StockFilterVectorProvider : public Vector4Provider
{
    Py_Header( StockFilterVectorProvider, Vector4Provider )
public:
    StockFilterVectorProvider( PyWGVehicleFilter * owner, unsigned kind ) :
        Vector4Provider( false, &s_type_ ), owner_( owner ), kind_( kind ) {}
    virtual void output( Vector4 & result ) override
    {
        const StockVehicleFilterState & state =
            *static_cast<PyWGVehicleFilter *>( owner_.get() )->state();
        if (kind_ == 0)
            result.set( state.displacement, state.leftDisplacement,
                state.rightDisplacement, state.gunDisplacement );
        else if (kind_ == 1)
            result.set( state.averageSpeed, state.averageRotationSpeed,
                state.instantaneousSpeed, state.instantaneousRotationSpeed );
        else
            result.set( state.strafeSpeed, state.longitudinalSpeed,
                static_cast<float>( state.leftContacts ), static_cast<float>( state.rightContacts ) );
    }
private:
    PyObjectPtr owner_;
    unsigned kind_;
};
PY_TYPEOBJECT( StockFilterVectorProvider )
PY_BEGIN_METHODS( StockFilterVectorProvider )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockFilterVectorProvider )
PY_END_ATTRIBUTES()

MatrixProviderPtr PyWGVehicleFilter::matrixProvider( unsigned kind )
{
    return MatrixProviderPtr( new StockFilterMatrixProvider( this, kind ), true );
}
Vector4ProviderPtr PyWGVehicleFilter::vectorProvider( unsigned kind )
{
    return Vector4ProviderPtr( new StockFilterVectorProvider( this, kind ), true );
}

void bindStockVehicleFilterOwner( PyFilter * filter, PyEntity * owner )
{
    if (filter && PyWGVehicleFilter::Check( filter ))
        static_cast<PyWGVehicleFilter *>( filter )->owner( owner );
}

namespace
{
std::vector<WeakPyPtr<PyWGVehicleFilter> > s_lagFilters;
bool s_lagDetected = false;
class StockFilterScriptShutdown : public Script::FiniTimeJob
{
    virtual void fini()
    {
        s_lagFilters.clear();
        s_lagDetected = false;
    }
} s_stockFilterScriptShutdown;
}

static void setStockLagDetection( PyWGVehicleFilter * filter, bool enabled )
{
    for (size_t index = 0; index < s_lagFilters.size(); )
    {
        PyWGVehicleFilter * value = s_lagFilters[index].get();
        if (!value || value == filter)
            s_lagFilters.erase( s_lagFilters.begin() + index );
        else
            ++index;
    }
    if (enabled)
        s_lagFilters.push_back( WeakPyPtr<PyWGVehicleFilter>( filter ) );
}

void tickStockVehicleFilterLagDetection()
{
    // #1513 006da4c0 samples registered filters' virtual is-lagging result;
    // 006e4bc0 reads their attached helper's extrapolation state. The battle
    // debug controller consumes this through BigWorld.statLagDetected.
    s_lagDetected = false;
    for (size_t index = 0; index < s_lagFilters.size(); )
    {
        PyWGVehicleFilter * owner = s_lagFilters[index].get();
        if (!owner)
        {
            s_lagFilters.erase( s_lagFilters.begin() + index );
            continue;
        }
        const StockVehicleFilter * filter =
            static_cast<const StockVehicleFilter *>( owner->pAttachedFilter() );
        if (filter && filter->helper().extrapolating())
            s_lagDetected = true;
        ++index;
    }
}

static bool statLagDetected() { return s_lagDetected; }
PY_AUTO_MODULE_FUNCTION( RETDATA, statLagDetected, END, BigWorld )

Filter * PyWGVehicleFilter::getNewFilter()
{
    return new StockVehicleFilter( this, state_ );
}

float PyWGVehicleFilter::latency() const
{
    const StockVehicleFilter * filter =
        static_cast<const StockVehicleFilter *>( this->pAttachedFilter() );
    return filter ? filter->helper().latency() : FLT_MAX;
}

void PyWGVehicleFilter::latency( float value )
{
    StockVehicleFilter * filter = static_cast<StockVehicleFilter *>( this->pAttachedFilter() );
    if (filter)
        filter->helper().latency( value );
}

PyObject * PyWGVehicleFilter::py_syncGunAngles( PyObject * args )
{
    float yaw;
    float pitch;
    if (!PyArg_ParseTuple( args, "ff:syncGunAngles", &yaw, &pitch ))
        return NULL;
    if (!this->attached())
    {
        PyErr_SetString( PyExc_ValueError, "WGVehicleFilter is not attached to an entity" );
        return NULL;
    }
    state_->gunHistory.sync( yaw, pitch, this->getTimeNow() );
    Py_RETURN_NONE;
}

PyObject * PyWGVehicleFilter::py_interpolateStabilisedMatrix( PyObject * args )
{
    double time;
    if (!PyArg_ParseTuple( args, "d:interpolateStabilisedMatrix", &time ))
        return NULL;
    if (!this->attached())
    {
        PyErr_SetString( PyExc_ValueError, "WGVehicleFilter is not attached to an entity" );
        return NULL;
    }
    return Script::getData( state_->sampleChassisOrientation( time, state_->body ) );
}

PY_TYPEOBJECT( PyWGVehicleFilter )
PY_BEGIN_METHODS( PyWGVehicleFilter )
    PY_METHOD( addTriangle )
    PY_METHOD( setVehiclePhysics )
    PY_METHOD( getVehiclePhysics )
    PY_METHOD( forceGroundPlacingMatrix )
    PY_METHOD( syncGunAngles )
    PY_METHOD( notifyInputKeysDown )
    PY_METHOD( enableStabilisedMatrix )
    PY_METHOD( interpolateStabilisedMatrix )
    PY_METHOD( enableLagDetection )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyWGVehicleFilter )
    PY_ATTRIBUTE( bodyMatrix )
    PY_ATTRIBUTE( placingCompensationMatrix )
    PY_ATTRIBUTE( groundPlacingMatrix )
    PY_ATTRIBUTE( groundPlacingMatrixFiltered )
    PY_ATTRIBUTE( stabilisedMatrix )
    PY_ATTRIBUTE( turretMatrix )
    PY_ATTRIBUTE( gunMatrix )
    PY_ATTRIBUTE( movementInfo )
    PY_ATTRIBUTE( speedInfo )
    PY_ATTRIBUTE( physicsInfo )
    PY_ATTRIBUTE( vehicleWidth )
    PY_ATTRIBUTE( vehicleMinNormalY )
    PY_ATTRIBUTE( allowStrafeCompensation )
    PY_ATTRIBUTE( velocityErrorCompensation )
    PY_ATTRIBUTE( defaultVelocityErrorCompensation )
    PY_ATTRIBUTE( hullLocalPosition )
    PY_ATTRIBUTE( placingOnGround )
    PY_ATTRIBUTE( maxMove )
    PY_ATTRIBUTE( maxTime )
    PY_ATTRIBUTE( vehicleCollisionCallback )
    PY_ATTRIBUTE( velocity )
    PY_ATTRIBUTE( acceleration )
    PY_ATTRIBUTE( strafeSpeed )
    PY_ATTRIBUTE( longitudinalSpeed )
    PY_ATTRIBUTE( angularSpeed )
    PY_ATTRIBUTE( suspCompressionRate )
    PY_ATTRIBUTE( numLeftTrackContacts )
    PY_ATTRIBUTE( numRightTrackContacts )
    PY_ATTRIBUTE( isStrafing )
    PY_ATTRIBUTE( averageSpeed )
    PY_ATTRIBUTE( averageRotationSpeed )
    PY_ATTRIBUTE( instantaneousSpeed )
    PY_ATTRIBUTE( instantaneousRotationSpeed )
    PY_ATTRIBUTE( latency )
    PY_ATTRIBUTE( velLatency )
    PY_ATTRIBUTE( minLatency )
    PY_ATTRIBUTE( latencyFrames )
    PY_ATTRIBUTE( latencyCurvePower )
PY_END_ATTRIBUTES()
PY_FACTORY_NAMED( PyWGVehicleFilter, "WGVehicleFilter", BigWorld )

bool readStockVehicleFilterVisualState( PyObject * filter,
    StockVehicleFilterVisualState & result )
{
    if (!filter || !PyWGVehicleFilter::Check( filter ))
        return false;
    const PyWGVehicleFilter * wrapper = static_cast<PyWGVehicleFilter *>( filter );
    const StockVehicleFilterState & state = *wrapper->state();
    result.attached = wrapper->attached();
    result.instantaneousSpeed = result.attached ? state.instantaneousSpeed : 0.f;
    result.instantaneousRotationSpeed = result.attached ? state.instantaneousRotationSpeed : 0.f;
    result.averageSpeed = result.attached ? state.averageSpeed : 0.f;
    result.averageRotationSpeed = result.attached ? state.averageRotationSpeed : 0.f;
    result.longitudinalSpeed = state.longitudinalSpeed;
    result.strafeSpeed = state.strafeSpeed;
    result.angularSpeed = fabsf( state.predictedAngularVelocity.dotProduct( state.body[1] ) );
    result.suspensionCompressionRate = state.compressionRate;
    result.leftContacts = state.leftContacts;
    result.rightContacts = state.rightContacts;
    result.strafing = state.isStrafing;
    return true;
}

bool setStockVehicleTrackOverrides( PyObject * filter, float leftSpeed,
    float rightSpeed, bool leftLocked, bool rightLocked )
{
    if (!filter || !PyWGVehicleFilter::Check( filter ))
        return false;
    PyWGVehicleFilter * wrapper = static_cast<PyWGVehicleFilter *>( filter );
    if (!wrapper->attached())
        return false;
    StockVehicleFilterState & state = *wrapper->state();
    state.leftTrackSpeed = leftSpeed;
    state.rightTrackSpeed = rightSpeed;
    state.leftTrackLocked = leftLocked;
    state.rightTrackLocked = rightLocked;
    return true;
}

BW_END_NAMESPACE
