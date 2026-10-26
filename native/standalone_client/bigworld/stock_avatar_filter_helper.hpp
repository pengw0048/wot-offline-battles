#pragma once

#include "connection/avatar_filter_helper.hpp"
#include "connection/filter_helper.hpp"
#include "math/quat.hpp"

BW_BEGIN_NAMESPACE

// The public eight-input/error-box history, with the #1513 Quaternion
// waypoint/output contract. It is driven by FilterEnvironment and has no
// independently integrated position or vehicle physics state.
class StockAvatarFilterHelper : public FilterHelper
{
public:
    static const uint NUM_STORED_INPUTS = 8;
    explicit StockAvatarFilterHelper( FilterEnvironment & environment );
    void reset( double time );
    void input( double time, SpaceID spaceID, EntityID vehicleID,
        const Position3D & position, const Vector3 & error, const Direction3D & direction );
    double output( double time, SpaceID & spaceID, EntityID & vehicleID,
        Position3D & position, Vector3 & velocity, Direction3D & direction );
    double outputQuaternion( double time, SpaceID & spaceID, EntityID & vehicleID,
        Position3D & position, Vector3 & velocity, Quaternion & orientation );
    bool getLastInput( double & time, SpaceID & spaceID, EntityID & vehicleID,
        Position3D & position, Vector3 & error, Direction3D & direction ) const;
    float latency() const { return latency_; }
    void latency( float value ) { latency_ = value; }
    double lastOutputTime() const { return lastOutput_; }
    double previousWaypointTime() const { return previous_.time; }
    bool extrapolating() const { return extrapolating_; }
    double extrapolationStart() const { return extrapolationStart_; }

    struct StoredInput
    {
        double time;
        SpaceID spaceID;
        EntityID vehicleID;
        Position3D position;
        Vector3 error;
        Direction3D direction;
        bool onGround;
    };
    struct Waypoint
    {
        double time;
        SpaceID spaceID;
        EntityID vehicleID;
        Position3D position;
        Quaternion orientation;
        StoredInput input;
        void changeCoordinates( FilterEnvironment & environment,
            SpaceID newSpace, EntityID newVehicle );
    };
    class Listener
    {
    public:
        virtual ~Listener() {}
        virtual void onReset() = 0;
        virtual void onWaypoint( double time, Waypoint & current, Waypoint & next ) = 0;
    };
    void listener( Listener * value ) { listener_ = value; }

private:
    StoredInput & stored( uint index );
    const StoredInput & stored( uint index ) const;
    void resetInputs( double time, SpaceID spaceID, EntityID vehicleID,
        const Position3D & position, const Vector3 & error, const Direction3D & direction );
    void chooseNextWaypoint( double time );
    void extract( double time, SpaceID & spaceID, EntityID & vehicleID,
        Position3D & position, Vector3 & velocity, Quaternion & orientation );

    StoredInput storedInputs_[NUM_STORED_INPUTS];
    uint current_ = 0;
    uint inputCount_ = 0;
    Waypoint next_;
    Waypoint previous_;
    float latency_ = 0.f;
    float idealLatency_ = 0.f;
    double lastOutput_ = 0.0;
    bool newInput_ = false;
    bool reset_ = true;
    Listener * listener_ = NULL;
    bool extrapolating_ = false;
    double extrapolationStart_ = -1.0;
    float extrapolationLatency_ = 0.f;
};

BW_END_NAMESPACE
