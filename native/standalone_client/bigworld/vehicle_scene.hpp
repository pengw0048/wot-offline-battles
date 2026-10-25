#ifndef WOT_VEHICLE_SCENE_HPP
#define WOT_VEHICLE_SCENE_HPP

#include "resmgr/datasection.hpp"
#include "space/client_space.hpp"
#include "space/entity_embodiment.hpp"

BW_BEGIN_NAMESPACE

// Terrain driving preview using the existing offline mod's controller. Native
// queries use descriptor collision bounds; this is not the retail track solver.
class VehicleScene
{
public:
    VehicleScene();
    ~VehicleScene();
    bool configure( DataSectionPtr assembly, const Vector3 & origin,
        ClientSpacePtr space, const EntityEmbodiments & models );
    void advance( float elapsed );
    void clear();
    bool supported() const;
    const Matrix & transform() const;

private:
    VehicleScene( const VehicleScene & );
    VehicleScene & operator=( const VehicleScene & );
    struct State;
    State * state_;
};

BW_END_NAMESPACE

#endif // WOT_VEHICLE_SCENE_HPP
