#include "pch.hpp"
#include "projectile_scene.hpp"
#include "target_scene.hpp"

#include "cstdmf/debug.hpp"
#include "moo/geometrics.hpp"
#include "moo/render_context.hpp"
#include "physics2/collision_callback.hpp"
#include "physics2/collision_obstacle.hpp"
#include "pyscript/script.hpp"
#include "romp/font_manager.hpp"
#include "wot/projectile_motion.hpp"
#include "wot/combat_rules.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>
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

// Native static geometry and the target's original BSP both own their actual
// hit distances; the projectile adapter always chooses the nearer surface.
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
        double travelled;
        wot::CombatShot combat;
        double penetrationFactor;
        double damageRoll;
        std::shared_ptr<TargetScene::ShotState> targetState;
        BW::string shell;
        std::vector<Vector3> trace;
    };

    State() : configured( false ), speed( 0.0 ), gravity( 0.0 ),
        maxDistance( 0.0 ), reloadTime( 0.0 ), time( 0.0 ), readyTime( 0.0 ),
        remainingAmmo( 0 ), hasCombat( false ), lastImpact( false ),
        fontAttempted( false ) {}

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
    bool hasCombat;
    wot::CombatShot combat;
    std::vector<Shot> shots;
    std::vector<Vector3> lastTrace;
    bool lastImpact;
    FontPtr font;
    bool fontAttempted;

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

ProjectileScene::ProjectileScene() : state_( new State() ), target_( NULL ),
    hasDrawn_( false )
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
    if (shot->openSection( "kind" ))
    {
        const char * combatFields[] = { "caliber", "damage_armor", "piercing_power" };
        for (size_t index = 0; index < sizeof( combatFields ) / sizeof( combatFields[0] ); ++index)
            if (!shot->openSection( combatFields[index] ))
            {
                ERROR_MSG( "ProjectileScene: combat shot is missing %s\n", combatFields[index] );
                return false;
            }
        state.combat.shell.kind = wot::shell_kind( shot->readString( "kind" ).c_str() );
        state.combat.shell.caliber = shot->readDouble( "caliber" );
        state.combat.shell.damage = shot->readDouble( "damage_armor" );
        const Vector2 piercing = shot->readVector2( "piercing_power" );
        state.combat.piercing_power = { piercing.x, piercing.y };
        state.combat.max_distance = state.maxDistance;
        if (!std::isfinite( state.combat.shell.caliber ) || state.combat.shell.caliber <= 0.0 ||
            !std::isfinite( *state.combat.shell.damage ) || *state.combat.shell.damage < 0.0 ||
            !std::isfinite( piercing.x ) || !std::isfinite( piercing.y ) ||
            piercing.x < 0.f || piercing.y < 0.f)
        {
            ERROR_MSG( "ProjectileScene: combat descriptor has invalid numeric values\n" );
            return false;
        }
        struct OptionalField { const char * name; std::optional<double> * value; };
        OptionalField optionalFields[] = {
            { "explosion_damage_factor", &state.combat.shell.explosion_damage_factor },
            { "explosion_absorption_factor", &state.combat.shell.explosion_absorption_factor },
            { "explosion_edge_damage_factor", &state.combat.shell.explosion_edge_damage_factor }
        };
        for (size_t index = 0; index < sizeof( optionalFields ) / sizeof( optionalFields[0] ); ++index)
            if (shot->openSection( optionalFields[index].name ))
            {
                const double value = shot->readDouble( optionalFields[index].name );
                if (!std::isfinite( value )) return false;
                *optionalFields[index].value = value;
            }
        if (shot->openSection( "explosion_radius" ))
        {
            state.combat.shell.explosion_radius = shot->readDouble( "explosion_radius" );
            if (!std::isfinite( state.combat.shell.explosion_radius ) ||
                state.combat.shell.explosion_radius < 0.0) return false;
        }
        state.hasCombat = true;
    }
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

void ProjectileScene::target( TargetScene * target ) { target_ = target; }

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
    if (target_ && target_->active() && !state.hasCombat)
    {
        WARNING_MSG( "ProjectileScene: fire refused; target damage requires exported combat shot data\n" );
        return false;
    }
    const Vector3 cameraOrigin = Moo::rc().invView().applyToOrigin();
    Vector3 direction = Moo::rc().invView().applyToUnitAxisVector( 2 );
    if (!finite( cameraOrigin ) || !finite( direction ) || direction.lengthSquared() <= 0.f)
    {
        WARNING_MSG( "ProjectileScene: fire refused; invalid native camera direction\n" );
        return false;
    }
    direction.normalise();
    // A third-person camera is offset from the real muzzle. Copying its
    // direction as a parallel muzzle ray makes a centred target miss sideways.
    // Select the camera ray's nearest actual surface, then converge the muzzle
    // on that point. This query never applies damage or corrects ballistic drop.
    Vector3 aim = cameraOrigin + direction * static_cast<float>( state.maxDistance );
    const float rayLength = (aim - cameraOrigin).length();
    if (!finite( aim ) || !std::isfinite( rayLength ) || rayLength <= 0.f)
    {
        WARNING_MSG( "ProjectileScene: fire refused; invalid camera aiming ray\n" );
        return false;
    }
    StaticCollision aimCollision;
    const float aimDistance = state.space->collide( cameraOrigin, aim, aimCollision );
    if (!std::isfinite( aimDistance ) || aimDistance > rayLength)
    {
        WARNING_MSG( "ProjectileScene: fire refused; invalid native aiming query\n" );
        return false;
    }
    if (aimDistance >= 0.f)
        aim = cameraOrigin + (aim - cameraOrigin) * (aimDistance / rayLength);
    if (target_)
    {
        Vector3 targetAim;
        if (!target_->aimPoint( cameraOrigin, aim, targetAim ))
            return false;
        aim = targetAim;
    }
    // A clear camera ray's range endpoint is directional input only, never a
    // fabricated hit. Actual damage still waits for the advancing projectile.
    direction = aim - state.muzzle;
    if (!finite( direction ) || direction.lengthSquared() <= 0.f)
    {
        WARNING_MSG( "ProjectileScene: fire refused; aiming point coincides with muzzle\n" );
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
    shot.travelled = 0.0;
    shot.shell = state.shell;
    shot.combat = state.combat;
    shot.penetrationFactor = state.hasCombat ? wot::sample_penetration_factor() : 1.0;
    shot.damageRoll = state.hasCombat ? wot::shell_damage_roll( shot.combat ) : 0.0;
    if (target_ && target_->active())
        shot.targetState = target_->createShot( shot.combat,
            shot.penetrationFactor, shot.damageRoll );
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
        BW::string ownedTerminal;
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
            const Vector3 nativeFinish = distance >= 0.f ?
                start + segment * (distance / length) : finish;
            if (target_ && shot->targetState && length > 0.f)
            {
                const TargetScene::TraceResult hit = target_->trace( *shot->targetState,
                    native( shot->start ), start, nativeFinish, shot->travelled );
                if (hit.terminal)
                {
                    finish = hit.point;
                    const double fraction = (nativeFinish - start).length() / length * hit.fraction;
                    shot->age = interval.start + (stopTime - interval.start) * fraction;
                    shot->trace.push_back( finish );
                    ownedTerminal = hit.reason;
                    terminal = ownedTerminal.c_str();
                    impact = hit.reason != "target_query_failure";
                    break;
                }
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
            shot->travelled += (finish - start).length();
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
    State & state = *state_;
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
    if (target_)
        target_->draw();
    if (state.configured && Moo::rc().device())
    {
        // Presentation only: the centre selects the camera ray's aiming point.
        // The muzzle trajectory still has its real offset and ballistic drop.
        const float centerX = Moo::rc().screenWidth() * 0.5f;
        const float centerY = Moo::rc().screenHeight() * 0.5f;
        const Moo::Colour colour( 0.85f, 1.f, 0.85f, 1.f );
        Geometrics::drawRect( Vector2( centerX - 7.f, centerY ),
            Vector2( centerX + 8.f, centerY + 1.f ), colour );
        Geometrics::drawRect( Vector2( centerX, centerY - 7.f ),
            Vector2( centerX + 1.f, centerY + 8.f ), colour );
        if (FontManager::pInstance())
        {
            if (!state.fontAttempted)
            {
                state.font = FontManager::instance().get( "system_small.font" );
                state.fontAttempted = true;
            }
            if (state.font && FontManager::instance().begin( *state.font ))
            {
                std::ostringstream status;
                status << "Ammo: " << state.remainingAmmo << "   Reload: "
                    << std::max( 0.0, state.readyTime - state.time ) << " s";
                state.font->drawString( BW::string( status.str().c_str() ), 16, 60 );
                state.font->drawString( "W/S drive  A/D steer  Space brake  Mouse aim  LMB fire  Arrows camera",
                    16, 82 );
                FontManager::instance().end();
            }
        }
    }
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
    target_ = NULL;
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
