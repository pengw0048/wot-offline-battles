#ifndef WOT_PROJECTILE_SCENE_HPP
#define WOT_PROJECTILE_SCENE_HPP

#include "cstdmf/main_loop_task.hpp"
#include "resmgr/datasection.hpp"
#include "space/client_space.hpp"

BW_BEGIN_NAMESPACE

// A static assembly firing preview, owned by the independent native scene.
// Its descriptor capacity is loaded entirely with the selected preview shell;
// it does not model vehicle armour, crew modifiers, or a mixed shell loadout.
class ProjectileScene : public MainLoopTask
{
public:
    ProjectileScene();
    ~ProjectileScene();

    bool configure( DataSectionPtr assembly, const Vector3 & origin,
        ClientSpacePtr space );
    bool fire();
    void advance( float elapsed );
    void clear();
    virtual void draw();

private:
    ProjectileScene( const ProjectileScene & );
    ProjectileScene & operator=( const ProjectileScene & );
    struct State;
    State * state_;
    bool hasDrawn_;
};

BW_END_NAMESPACE

#endif // WOT_PROJECTILE_SCENE_HPP
