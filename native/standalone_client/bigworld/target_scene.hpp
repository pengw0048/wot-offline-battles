#ifndef WOT_TARGET_SCENE_HPP
#define WOT_TARGET_SCENE_HPP

#include "resmgr/datasection.hpp"
#include "space/entity_embodiment.hpp"

#include <memory>

namespace wot { struct CombatShot; }

BW_BEGIN_NAMESPACE

class TargetScene
{
public:
    struct ShotState;
    struct TraceResult
    {
        bool terminal;
        double fraction;
        Vector3 point;
        BW::string reason;
    };

    TargetScene();
    ~TargetScene();
    bool configure( DataSectionPtr assembly, const Vector3 & origin,
        const EntityEmbodiments & models );
    bool active() const;
    // Read-only camera-ray selection. A clear ray returns its supplied endpoint.
    bool aimPoint( const Vector3 & start, const Vector3 & finish,
        Vector3 & point ) const;
    std::shared_ptr<ShotState> createShot( const wot::CombatShot & shot,
        double penetrationFactor, double damageRoll ) const;
    TraceResult trace( ShotState & shot, const Vector3 & origin,
        const Vector3 & start, const Vector3 & finish, double travelled );
    void draw();
    void clear();

private:
    TargetScene( const TargetScene & );
    TargetScene & operator=( const TargetScene & );
    struct State;
    State * state_;
};

BW_END_NAMESPACE

#endif // WOT_TARGET_SCENE_HPP
