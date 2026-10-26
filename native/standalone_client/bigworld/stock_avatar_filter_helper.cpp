#include "pch.hpp"
#include "stock_avatar_filter_helper.hpp"

#include "connection/filter_environment.hpp"
#include "math/boundbox.hpp"
#include "math/matrix.hpp"

#include <algorithm>
#include <cmath>

BW_BEGIN_NAMESPACE

namespace
{
Quaternion quaternionFor( const Direction3D & direction )
{
    // #1513 007ea9b0/007eb260 use D3DX's yaw-pitch-roll convention.
    Quaternion result;
    D3DXQuaternionRotationYawPitchRoll( &result, direction.yaw,
        direction.pitch, direction.roll );
    return result;
}
Direction3D directionFor( const Quaternion & orientation )
{
    Matrix matrix;
    matrix.setRotate( orientation );
    Direction3D result;
    result.yaw = matrix.yaw();
    result.pitch = matrix.pitch();
    result.roll = matrix.roll();
    return result;
}
bool nearVector( const Vector3 & first, const Vector3 & second, float tolerance )
{
    return fabsf( first.x - second.x ) < tolerance &&
        fabsf( first.y - second.y ) < tolerance &&
        fabsf( first.z - second.z ) < tolerance;
}
Vector3 waypointLerp( const Vector3 & first, const Vector3 & second, float fraction )
{
    if (nearVector( first, second, 0.003837f ))
        return second;
    return first * (1.f - fraction) + second * fraction;
}
}

StockAvatarFilterHelper::StockAvatarFilterHelper( FilterEnvironment & environment ) :
    FilterHelper( environment )
{
    this->resetInputs( -2000.0, 0, 0, Position3D::zero(), Vector3::zero(),
        Direction3D( Vector3::zero() ) );
    this->reset( 0.0 );
}

void StockAvatarFilterHelper::reset( double /* time */ )
{
    reset_ = true;
}

StockAvatarFilterHelper::StoredInput & StockAvatarFilterHelper::stored( uint index )
{
    return storedInputs_[(current_ + index) & 7];
}
const StockAvatarFilterHelper::StoredInput & StockAvatarFilterHelper::stored( uint index ) const
{
    return storedInputs_[(current_ + index) & 7];
}

void StockAvatarFilterHelper::resetInputs( double time, SpaceID spaceID,
    EntityID vehicleID, const Position3D & position, const Vector3 & error,
    const Direction3D & direction )
{
    // #1513 007eb260 resolves the position once, seeds eight .01f-spaced
    // inputs, and seeds both waypoints from the same complete stored inputs.
    if (listener_)
        listener_->onReset();
    current_ = 0;
    inputCount_ = 0;
    newInput_ = true;
    lastOutput_ = time;
    Position3D corrected = position;
    bool onGround = false;
    this->resolveOnGroundPosition( corrected, onGround );
    for (uint index = 0; index < NUM_STORED_INPUTS; ++index)
    {
        StoredInput & value = this->stored( index );
        value.time = time - static_cast<double>( index * 0.01f );
        value.spaceID = spaceID;
        value.vehicleID = vehicleID;
        value.position = corrected;
        value.error = error;
        value.direction = direction;
        value.onGround = onGround;
    }
    latency_ = AvatarFilterSettings::s_latencyFrames_ * 0.01f +
        AvatarFilterSettings::s_latencyMinimum_;
    const StoredInput & next = this->stored( 1 );
    next_.time = next.time;
    next_.spaceID = next.spaceID;
    next_.vehicleID = next.vehicleID;
    next_.position = next.position;
    next_.orientation = quaternionFor( next.direction );
    next_.input = next;
    const StoredInput & previous = this->stored( 2 );
    previous_.time = previous.time;
    previous_.spaceID = previous.spaceID;
    previous_.vehicleID = previous.vehicleID;
    previous_.position = previous.position;
    previous_.orientation = quaternionFor( previous.direction );
    previous_.input = previous;
}

void StockAvatarFilterHelper::input( double time, SpaceID spaceID,
    EntityID vehicleID, const Position3D & position, const Vector3 & error,
    const Direction3D & direction )
{
    if (reset_)
    {
        this->resetInputs( time, spaceID, vehicleID, position, error, direction );
        reset_ = false;
        return;
    }
    if (time <= this->stored( 0 ).time)
        return;
    current_ = (current_ - 1) & 7;
    StoredInput & value = this->stored( 0 );
    value.time = time;
    value.spaceID = spaceID;
    value.vehicleID = vehicleID;
    value.position = position;
    value.error = error;
    value.direction = direction;
    this->resolveOnGroundPosition( value.position, value.onGround );
    ++inputCount_;
    newInput_ = true;
}

bool StockAvatarFilterHelper::getLastInput( double & time, SpaceID & spaceID,
    EntityID & vehicleID, Position3D & position, Vector3 & error,
    Direction3D & direction ) const
{
    if (reset_)
        return false;
    const StoredInput & value = this->stored( 0 );
    time = value.time;
    spaceID = value.spaceID;
    vehicleID = value.vehicleID;
    position = value.position;
    error = value.error;
    direction = value.direction;
    return true;
}

double StockAvatarFilterHelper::output( double time, SpaceID & spaceID,
    EntityID & vehicleID, Position3D & position, Vector3 & velocity,
    Direction3D & direction )
{
    Quaternion orientation;
    const double result = this->outputQuaternion( time, spaceID, vehicleID,
        position, velocity, orientation );
    direction = directionFor( orientation );
    return result;
}

double StockAvatarFilterHelper::outputQuaternion( double time, SpaceID & spaceID,
    EntityID & vehicleID, Position3D & position, Vector3 & velocity,
    Quaternion & orientation )
{
    // #1513 007eb0a0. The source public history supplies this same latency
    // update; its Euler output is deliberately replaced below.
    if (newInput_)
    {
        newInput_ = false;
        const int maximum = static_cast<int>( std::min( inputCount_, 8u ) ) - 1;
        if (maximum > 0)
        {
            const float frames = std::max( 0.f, std::min(
                AvatarFilterSettings::s_latencyFrames_, static_cast<float>( maximum ) ) );
            const double older = this->stored( maximum ).time;
            const double newest = this->stored( 0 ).time;
            const double ratio = static_cast<double>( static_cast<float>( maximum ) - frames ) /
                static_cast<double>( maximum );
            idealLatency_ = static_cast<float>( time - (ratio * (newest - older) + older) );
        }
        idealLatency_ = std::max( idealLatency_, AvatarFilterSettings::s_latencyMinimum_ );
    }
    const float change = static_cast<float>( time - lastOutput_ ) *
        AvatarFilterSettings::s_latencyVelocity_ * std::min( 1.f,
            powf( fabsf( idealLatency_ - latency_ ),
                AvatarFilterSettings::s_latencyCurvePower_ ) );
    if (idealLatency_ > latency_)
        latency_ = std::min( latency_ + change, idealLatency_ );
    else
        latency_ = std::max( latency_ - change, idealLatency_ );
    lastOutput_ = time;
    const double outputTime = time - static_cast<double>( latency_ );
    this->extract( outputTime, spaceID, vehicleID, position, velocity, orientation );
    return outputTime;
}

void StockAvatarFilterHelper::extract( double time, SpaceID & spaceID,
    EntityID & vehicleID, Position3D & position, Vector3 & velocity,
    Quaternion & orientation )
{
    if (!AvatarFilterHelper::isActive())
    {
        const StoredInput & value = this->stored( 0 );
        spaceID = value.spaceID;
        vehicleID = value.vehicleID;
        position = value.position;
        velocity.setZero();
        orientation = quaternionFor( value.direction );
        return;
    }
    if (next_.time < time)
        this->chooseNextWaypoint( time );
    const float fraction = static_cast<float>(
        (time - previous_.time) / (next_.time - previous_.time) );
    spaceID = next_.spaceID;
    vehicleID = next_.vehicleID;
    if (nearVector( previous_.position, next_.position, 0.0004f ))
        position = next_.position;
    else
        position = previous_.position * (1.f - fraction) + next_.position * fraction;
    velocity = (next_.position - previous_.position) *
        (1.f / static_cast<float>( next_.time - previous_.time ));
    if (fabsf( previous_.orientation.x - next_.orientation.x ) < 0.0004f &&
        fabsf( previous_.orientation.y - next_.orientation.y ) < 0.0004f &&
        fabsf( previous_.orientation.z - next_.orientation.z ) < 0.0004f &&
        fabsf( previous_.orientation.w - next_.orientation.w ) < 0.0004f)
        orientation = next_.orientation;
    else
        D3DXQuaternionSlerp( &orientation, &previous_.orientation,
            &next_.orientation, fraction );
}

void StockAvatarFilterHelper::Waypoint::changeCoordinates( FilterEnvironment & environment,
    SpaceID newSpace, EntityID newVehicle )
{
    if (spaceID == newSpace && vehicleID == newVehicle)
        return;
    Direction3D direction = directionFor( orientation );
    environment.transformIntoCommon( spaceID, vehicleID, position, direction );
    spaceID = newSpace;
    vehicleID = newVehicle;
    environment.transformFromCommon( spaceID, vehicleID, position, direction );
    orientation = quaternionFor( direction );
}

void StockAvatarFilterHelper::chooseNextWaypoint( double time )
{
    // Public BigWorld's error-box waypoint construction, retained under the
    // original module boundary. #1513's orientation is a Quaternion at every
    // waypoint rather than public AvatarFilterHelper's Euler direction.
    if (this->stored( 0 ).time <= time)
    {
        Waypoint next = next_;
        next.time = time;
        if (!extrapolating_)
        {
            extrapolating_ = true;
            extrapolationStart_ = time;
            extrapolationLatency_ = latency_;
        }
        if (listener_)
            listener_->onWaypoint( time, next_, next );
        previous_ = next_;
        next_ = next;
        return;
    }
    int index = 7;
    while (index > 0)
    {
        if (this->stored( index ).time > time)
            break;
        --index;
    }
    const StoredInput & lookAhead = this->stored( 0 );
    const StoredInput & nextInput = this->stored( index );
    Waypoint next;
    next.time = nextInput.time;
    next.spaceID = nextInput.spaceID;
    next.vehicleID = nextInput.vehicleID;
    next.orientation = quaternionFor( nextInput.direction );
    next.input = nextInput;
    previous_.changeCoordinates( this->environment(), next.spaceID, next.vehicleID );
    next_.changeCoordinates( this->environment(), next.spaceID, next.vehicleID );
    const float lookFraction = static_cast<float>( (lookAhead.time - previous_.time) /
        (next_.time - previous_.time) );
    Position3D lookPosition = waypointLerp( previous_.position, next_.position, lookFraction );
    Direction3D unused( Vector3::zero() );
    this->transformIntoCommon( next.spaceID, next.vehicleID, lookPosition, unused );
    this->transformFromCommon( lookAhead.spaceID, lookAhead.vehicleID, lookPosition, unused );
    lookPosition.clamp( lookAhead.position - lookAhead.error,
        lookAhead.position + lookAhead.error );
    this->transformIntoCommon( lookAhead.spaceID, lookAhead.vehicleID, lookPosition, unused );
    this->transformFromCommon( next.spaceID, next.vehicleID, lookPosition, unused );
    const BoundingBox newBounds( next.input.position - next.input.error,
        next.input.position + next.input.error );
    const BoundingBox oldBounds( next_.input.position - next_.input.error,
        next_.input.position + next_.input.error );
    if (next.spaceID == next_.input.spaceID && next.vehicleID == next_.input.vehicleID &&
        !almostEqual( next.input.error, next_.input.error ) && newBounds.intersects( oldBounds ))
        next.position = next_.position;
    else
    {
        const float fraction = static_cast<float>( (nextInput.time - next_.time) /
            (lookAhead.time - next_.time) );
        next.position = waypointLerp( next_.position, lookPosition, fraction );
    }
    const BoundingBox nextBounds( nextInput.position - nextInput.error,
        nextInput.position + nextInput.error );
    if (!nextBounds.intersects( next.position ))
    {
        Position3D clamped = next.position;
        clamped.clamp( nextInput.position - nextInput.error, nextInput.position + nextInput.error );
        const Vector3 lookVector = next.position - next_.position;
        const Vector3 clampedVector = clamped - next_.position;
        next.position = next_.position;
        if (lookVector.lengthSquared() > 0.f)
            next.position += clampedVector.projectOnto( lookVector );
        next.position.clamp( nextInput.position - nextInput.error,
            nextInput.position + nextInput.error );
    }
    if (extrapolating_)
    {
        extrapolating_ = false;
        extrapolationStart_ = -1.0;
    }
    if (listener_)
        listener_->onWaypoint( time, next_, next );
    previous_ = next_;
    next_ = next;
}

BW_END_NAMESPACE
