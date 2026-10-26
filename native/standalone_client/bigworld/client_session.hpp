#ifndef WOT_CLIENT_SESSION_HPP
#define WOT_CLIENT_SESSION_HPP

#include "script/script_object.hpp"
#include "space/client_space.hpp"

BW_BEGIN_NAMESPACE

// Owns the original #1513 Avatar/Vehicle lifecycle. It does not simulate bodies,
// create replacement gameplay entities, or manufacture stock readiness flags.
class ClientSession
{
public:
    enum Stage
    {
        IDLE, AVATAR_LOADING, VEHICLE_LOADING, BATTLE_LOADING, ACTIVE,
        FAILED, STOPPING
    };

    ClientSession();
    ~ClientSession();
    bool begin( SpaceID spaceID, const ScriptDict & avatarProperties,
        const Vector3 & position, const Vector3 & directionRPY,
        const ScriptObject & avatarServer );
    bool spawnOwnVehicle( const ScriptDict & vehicleProperties,
        const Vector3 & position, const Vector3 & directionRPY,
        const ScriptObject & vehicleCellServer,
        const ScriptObject & selectVehicle );
    bool advance();
    bool stop();
    Stage stage() const;
    const BW::string & error() const;
    ScriptObject avatar() const;
    ScriptObject vehicle() const;

private:
    ClientSession( const ClientSession & );
    ClientSession & operator=( const ClientSession & );
    struct State;
    State * state_;
};

BW_END_NAMESPACE

#endif // WOT_CLIENT_SESSION_HPP
