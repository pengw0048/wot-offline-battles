#include "pch.hpp"
#include "projectile_scene.hpp"

#include "cstdmf/debug.hpp"
#include "moo/geometrics.hpp"
#include "moo/render_context.hpp"
#include "physics2/collision_callback.hpp"
#include "physics2/collision_obstacle.hpp"
#include "pyscript/script.hpp"
#include "wot/projectile_motion.hpp"

#include <algorithm>
#include <cmath>
#include <vector>

DECLARE_DEBUG_COMPONENT2( "StandaloneProjectiles", 0 )

BW_BEGIN_NAMESPACE

namespace
{
ProjectileScene * s_projectileScene = NULL;

wot::BallisticVector3 portable( const Vector3 & value )
{
    return { value.x, value.y, value.z };
}

Vector3 native( wot::BallisticVector3 value )
{
    return Vector3( static_cast<float>( value.x ),
        static_cast<float>( value.y ), static_cast<float>( value.z ) );
}

bool finite( const Vector3 & value )
{
    return std::isfinite( value.x ) && std::isfinite( value.y ) &&
        std::isfinite( value.z );
}

// This first scene adapter only terminates on native static geometry. Moving
// obstacles need the vehicle's real material and current-pose collision owner.
class StaticCollision : public CollisionCallback
{
public:
    virtual int operator()( const CollisionObstacle & obstacle,
        const WorldTriangle & triangle, float /* distance */ )
    {
        if (obstacle.isMovingObstacle() ||
            (triangle.flags() & BW_TRIANGLE_NOCOLLIDE))
        {
            return COLLIDE_ALL;
        }
        return COLLIDE_BEFORE;
    }
};
} // namespace

struct ProjectileScene::State
{
    struct Shot
    {
        wot::BallisticVector3 start;
        wot::BallisticVector3 velocity;
        wot::BallisticVector3 gravity;
        double maxDistance;
        double age;
        BW::string shell;
        std::vector<Vector3> trace;
    };

    State() : configured( false ), speed( 0.0 ), gravity( 0.0 ),
        maxDistance( 0.0 ), reloadTime( 0.0 ), time( 0.0 ), readyTime( 0.0 ),
        remainingAmmo( 0 ), lastImpact( false ) {}

    ClientSpacePtr space;
    bool configured;
    BW::string shell;
    Vector3 muzzle;
    Vector3 localMuzzle;
    double speed;
    double gravity;
    double maxDistance;
    double reloadTime;
    double time;
    double readyTime;
    int remainingAmmo;
    std::vector<Shot> shots;
    std::vector<Vector3> lastTrace;
    bool lastImpact;

    void terminal( const Shot & shot, const char * reason, bool impact )
    {
        lastTrace = shot.trace;
        lastImpact = impact;
        const Vector3 & position = shot.trace.back();
        INFO_MSG( "ProjectileScene: terminal shell=%s reason=%s age=%g position=(%g,%g,%g)\n",
            shot.shell.c_str(), reason, shot.age,
            position.x, position.y, position.z );
    }
};

ProjectileScene::ProjectileScene() : state_( new State() ), hasDrawn_( false )
{
    // Finale ends and presents the D3D scene. Render here while it is open,
    // independently of StandaloneApp's later startup/earlier teardown order.
    MainLoopTasks::root().add( this, "WotStandaloneProjectiles",
        ">Debug", "<Finale", NULL );
}

ProjectileScene::~ProjectileScene()
{
    delete state_;
}

bool ProjectileScene::configure( DataSectionPtr assembly,
    const Vector3 & origin, ClientSpacePtr space )
{
    this->clear();
    DataSectionPtr shot = assembly ? assembly->openSection( "shot" ) : NULL;
    if (!shot)
    {
        INFO_MSG( "ProjectileScene: no authored shot configuration; firing disabled\n" );
        return true;
    }
    const char * required[] = { "shell", "speed", "gravity", "maxDistance",
        "reloadTime", "maxAmmo", "muzzlePosition" };
    for (size_t index = 0; index < sizeof( required ) / sizeof( required[0] ); ++index)
    {
        if (!shot->openSection( required[index] ))
        {
            ERROR_MSG( "ProjectileScene: shot is missing %s\n", required[index] );
            return false;
        }
    }
    State & state = *state_;
    state.shell = shot->readString( "shell" );
    state.speed = shot->readDouble( "speed" );
    state.gravity = shot->readDouble( "gravity" );
    state.maxDistance = shot->readDouble( "maxDistance" );
    state.reloadTime = shot->readDouble( "reloadTime" );
    state.remainingAmmo = shot->readInt( "maxAmmo" );
    state.localMuzzle = shot->readVector3( "muzzlePosition" );
    state.muzzle = origin + state.localMuzzle;
    if (state.shell.empty() || !finite( state.muzzle ) ||
        !std::isfinite( state.speed ) || state.speed <= 0.0 ||
        !std::isfinite( state.gravity ) || state.gravity < 0.0 ||
        !std::isfinite( state.maxDistance ) || state.maxDistance <= 0.0 ||
        !std::isfinite( state.reloadTime ) || state.reloadTime < 0.0 ||
        state.remainingAmmo < 0)
    {
        ERROR_MSG( "ProjectileScene: authored shot configuration is invalid\n" );
        return false;
    }
    state.space = space;
    state.configured = true;
    s_projectileScene = this;
    INFO_MSG( "ProjectileScene: firing preview configured shell=%s speed=%g gravity=%g range=%g baseReload=%g selectedShellAmmo=%d\n",
        state.shell.c_str(), state.speed, state.gravity,
        state.maxDistance, state.reloadTime, state.remainingAmmo );
    return true;
}

void ProjectileScene::setAssemblyTransform( const Matrix & transform )
{
    if (state_->configured)
        state_->muzzle = transform.applyPoint( state_->localMuzzle );
}

bool ProjectileScene::fire()
{
    State & state = *state_;
    if (!state.configured || !state.space || state.space->cleared() ||
        state.space->loadStatus() < 1.f)
    {
        WARNING_MSG( "ProjectileScene: fire refused; authored shot and loaded native geometry are required\n" );
        return false;
    }
    if (state.time < state.readyTime)
    {
        INFO_MSG( "ProjectileScene: fire refused; base reload remaining=%g\n",
            state.readyTime - state.time );
        return false;
    }
    if (state.remainingAmmo == 0)
    {
        INFO_MSG( "ProjectileScene: fire refused; selected-shell preview ammunition is empty\n" );
        return false;
    }
    Vector3 direction = Moo::rc().invView().applyToUnitAxisVector( 2 );
    if (!finite( direction ) || direction.lengthSquared() <= 0.f)
    {
        WARNING_MSG( "ProjectileScene: fire refused; invalid native camera direction\n" );
        return false;
    }
    direction.normalise();
    State::Shot shot;
    shot.start = portable( state.muzzle );
    shot.velocity = { direction.x * state.speed, direction.y * state.speed,
        direction.z * state.speed };
    shot.gravity = { 0.0, -state.gravity, 0.0 };
    shot.maxDistance = state.maxDistance;
    shot.age = 0.0;
    shot.shell = state.shell;
    shot.trace.push_back( state.muzzle );
    // Freeze all shot state once. Later scene/configuration input cannot
    // rebind a launched shell, muzzle, direction, or its reload deadline.
    state.shots.push_back( shot );
    state.readyTime = state.time + state.reloadTime;
    --state.remainingAmmo;
    INFO_MSG( "ProjectileScene: launched shell=%s muzzle=(%g,%g,%g) direction=(%g,%g,%g) remainingAmmo=%d\n",
        shot.shell.c_str(), state.muzzle.x, state.muzzle.y, state.muzzle.z,
        direction.x, direction.y, direction.z, state.remainingAmmo );
    return true;
}

void ProjectileScene::advance( float elapsed )
{
    State & state = *state_;
    if (!std::isfinite( elapsed ) || elapsed <= 0.f)
    {
        return;
    }
    state.time += elapsed;
    for (std::vector<State::Shot>::iterator shot = state.shots.begin();
        shot != state.shots.end(); )
    {
        const char * terminal = NULL;
        bool impact = false;
        if (!state.space || state.space->cleared())
        {
            terminal = "scene_unavailable";
        }
        const double endTime = std::min( shot->age + elapsed,
            wot::projectile_max_flight_seconds );
        const std::vector<wot::ProjectileTimeInterval> intervals =
            wot::substep_boundaries( shot->age, endTime,
                wot::curvature_limited_substep( shot->gravity ) );
        for (size_t index = 0; !terminal && index < intervals.size(); ++index)
        {
            const wot::ProjectileTimeInterval & interval = intervals[index];
            double stopTime = interval.end;
            wot::BallisticVector3 end = wot::trajectory_position(
                shot->start, shot->velocity, shot->gravity, stopTime );
            bool rangeEnd = wot::projectile_range_distance( shot->start, end ) >=
                shot->maxDistance;
            if (rangeEnd)
            {
                // Shorten the last native sweep to the range boundary, so a
                // triangle beyond it cannot become a false impact.
                double low = interval.start;
                double high = stopTime;
                for (int iteration = 0; iteration < 40; ++iteration)
                {
                    const double middle = (low + high) * 0.5;
                    const wot::BallisticVector3 point = wot::trajectory_position(
                        shot->start, shot->velocity, shot->gravity, middle );
                    if (wot::projectile_range_distance( shot->start, point ) >=
                        shot->maxDistance)
                        high = middle;
                    else
                        low = middle;
                }
                stopTime = high;
                end = wot::trajectory_position( shot->start, shot->velocity,
                    shot->gravity, stopTime );
            }
            const Vector3 start = native( wot::trajectory_position( shot->start,
                shot->velocity, shot->gravity, interval.start ) );
            Vector3 finish = native( end );
            if (!finite( start ) || !finite( finish ))
            {
                terminal = "invalid_trajectory";
                break;
            }
            const Vector3 segment = finish - start;
            const float length = segment.length();
            if (!std::isfinite( length ))
            {
                terminal = "invalid_trajectory";
                break;
            }
            StaticCollision collision;
            const float distance = length > 0.f ?
                state.space->collide( start, finish, collision ) : -1.f;
            if (!std::isfinite( distance ) || distance > length)
            {
                terminal = "invalid_native_collision";
                break;
            }
            if (distance >= 0.f)
            {
                const double fraction = distance / length;
                finish = start + segment * static_cast<float>( fraction );
                shot->age = interval.start +
                    (stopTime - interval.start) * fraction;
                terminal = "static_impact";
                impact = true;
            }
            else
            {
                shot->age = stopTime;
                if (rangeEnd)
                    terminal = "maximum_range";
            }
            shot->trace.push_back( finish );
        }
        if (!terminal && endTime >= wot::projectile_max_flight_seconds)
        {
            shot->age = endTime;
            terminal = "maximum_flight_time";
        }
        if (terminal)
        {
            state.terminal( *shot, terminal, impact );
            shot = state.shots.erase( shot );
        }
        else
            ++shot;
    }
}

void ProjectileScene::draw()
{
    const State & state = *state_;
    const Moo::Colour activeColour( 1.f, 0.9f, 0.15f, 1.f );
    const Moo::Colour terminalColour( state.lastImpact ? 1.f : 0.3f,
        state.lastImpact ? 0.2f : 0.7f, 0.2f, 1.f );
    for (size_t index = 1; index < state.lastTrace.size(); ++index)
        Geometrics::drawLine( state.lastTrace[index - 1], state.lastTrace[index],
            terminalColour, false );
    if (state.lastImpact && !state.lastTrace.empty())
    {
        const Vector3 point = state.lastTrace.back();
        // Display dimensions only; this cross never participates in physics.
        const Vector3 axes[] = { Vector3( 0.5f, 0.f, 0.f ),
            Vector3( 0.f, 0.5f, 0.f ), Vector3( 0.f, 0.f, 0.5f ) };
        for (size_t axis = 0; axis < 3; ++axis)
            Geometrics::drawLine( point - axes[axis], point + axes[axis],
                terminalColour, false );
    }
    for (size_t shot = 0; shot < state.shots.size(); ++shot)
        for (size_t index = 1; index < state.shots[shot].trace.size(); ++index)
            Geometrics::drawLine( state.shots[shot].trace[index - 1],
                state.shots[shot].trace[index], activeColour, false );
    if (!hasDrawn_ && Moo::rc().device())
    {
        hasDrawn_ = true;
        INFO_MSG( "StandaloneApp: first native frame rendered\n" );
    }
}

void ProjectileScene::clear()
{
    if (s_projectileScene == this)
        s_projectileScene = NULL;
    for (size_t index = 0; index < state_->shots.size(); ++index)
        state_->terminal( state_->shots[index], "scene_teardown", false );
    delete state_;
    state_ = new State();
}

static bool standaloneFire()
{
    if (!s_projectileScene)
    {
        WARNING_MSG( "ProjectileScene: fire refused; no configured native assembly\n" );
        return false;
    }
    return s_projectileScene->fire();
}
PY_AUTO_MODULE_FUNCTION( RETDATA, standaloneFire, END, BigWorld )

BW_END_NAMESPACE
