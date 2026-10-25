#include "pch.hpp"
#include "target_scene.hpp"

#include "cstdmf/debug.hpp"
#include "resmgr/binary_block.hpp"
#include "resmgr/bwresource.hpp"
#include "romp/font_manager.hpp"
#include "wot/combat_rules.hpp"
#include "wot/projectile_motion.hpp"
#include "wot/vehicle_collision.hpp"

#include <algorithm>
#include <cmath>
#include <sstream>
#include <vector>

DECLARE_DEBUG_COMPONENT2( "StandaloneTarget", 0 )

BW_BEGIN_NAMESPACE

namespace
{
wot::Vector3 collisionVector( const Vector3 & vector )
{
    return { vector.x, vector.y, vector.z };
}

wot::BallisticVector3 ballisticVector( const Vector3 & vector )
{
    return { vector.x, vector.y, vector.z };
}

Vector3 nativeVector( wot::Vector3 vector )
{
    return Vector3( vector.x, vector.y, vector.z );
}

wot::VehicleMatrix collisionMatrix( const Matrix & matrix )
{
    wot::VehicleMatrix result;
    for (int row = 0; row < 4; ++row)
        for (int column = 0; column < 4; ++column)
            result.rows[row][column] = matrix( row, column );
    return result;
}

const char * penetrationName( wot::PenetrationResult result )
{
    switch (result)
    {
    case wot::PenetrationResult::ricochet: return "ricochet";
    case wot::PenetrationResult::stopped: return "stopped";
    default: return "penetrated";
    }
}

bool readMaterial( DataSectionPtr source, wot::ArmorMaterial & material )
{
    const char * fields[] = { "armor", "vehicle_damage_factor", "use_hit_angle",
        "may_ricochet", "check_caliber_for_hit_angle_norm",
        "check_caliber_for_ricochet", "collide_once_only",
        "use_antifragmentation_lining" };
    for (size_t index = 0; index < sizeof( fields ) / sizeof( fields[0] ); ++index)
        if (!source->openSection( fields[index] ))
            return false;
    material.armor = source->readDouble( "armor" );
    material.vehicle_damage_factor = source->readDouble( "vehicle_damage_factor" );
    material.use_hit_angle = source->readBool( "use_hit_angle" );
    material.may_ricochet = source->readBool( "may_ricochet" );
    material.check_caliber_for_hit_angle_norm = source->readBool( "check_caliber_for_hit_angle_norm" );
    material.check_caliber_for_ricochet = source->readBool( "check_caliber_for_ricochet" );
    material.collide_once_only = source->readBool( "collide_once_only" );
    material.use_antifragmentation_lining = source->readBool( "use_antifragmentation_lining" );
    // kind is deliberately absent: #1513's descriptor material identity falls
    // back to its stable table entry, rather than the BSP's numeric index.
    return std::isfinite( *material.armor ) &&
        std::isfinite( *material.vehicle_damage_factor );
}
} // namespace

struct TargetScene::ShotState
{
    wot::CombatShot combat;
    double penetrationFactor;
    double damageRoll;
    std::optional<double> firstRange;
    std::vector<wot::ArmorCollision> history;
    std::vector<Vector3> points;
    std::vector<BW::string> names;
};

struct TargetScene::State
{
    struct Component
    {
        BW::string name;
        IEntityEmbodimentPtr model;
        wot::VehicleCollisionMesh mesh;
        std::vector<wot::ArmorMaterial> materials;
        std::vector<bool> present;
    };

    State() : configured( false ), maximumHealth( 0 ), health( 0 ),
        fontAttempted( false ) {}
    bool configured;
    int maximumHealth;
    int health;
    std::vector<Component> components;
    bool fontAttempted;
    FontPtr font;
    BW::string feedback;

    std::vector<wot::VehicleCollisionComponent> collisionComponents() const
    {
        std::vector<wot::VehicleCollisionComponent> result;
        for (size_t index = 0; index < components.size(); ++index)
        {
            const Component & component = components[index];
            result.push_back( { std::string( component.name.c_str() ), &component.mesh,
                collisionMatrix( component.model->worldTransform() ) } );
        }
        return result;
    }

    void removeModels()
    {
        for (size_t index = 0; index < components.size(); ++index)
            if (components[index].model && components[index].model->space())
                components[index].model->leaveSpace();
    }
};

TargetScene::TargetScene() : state_( new State() ) {}
TargetScene::~TargetScene() { clear(); delete state_; }

bool TargetScene::configure( DataSectionPtr assembly, const Vector3 & origin,
    const EntityEmbodiments & models )
{
    clear();
    State & state = *state_;
    if (!assembly || !assembly->openSection( "combat/max_health" ))
    {
        ERROR_MSG( "TargetScene: target has no descriptor max_health\n" );
        return false;
    }
    state.maximumHealth = assembly->readInt( "combat/max_health" );
    state.health = state.maximumHealth;
    BW::vector<DataSectionPtr> definitions;
    assembly->openSections( "component", definitions );
    if (state.health <= 0 || definitions.empty() || definitions.size() != models.size())
        return false;
    state.components.reserve( definitions.size() );
    try
    {
        for (size_t index = 0; index < definitions.size(); ++index)
        {
            DataSectionPtr definition = definitions[index];
            State::Component component;
            component.name = definition->readString( "name" );
            component.model = models[index];
            const BW::string resource = definition->readString( "collision_mesh" );
            DataSectionPtr section = resource.empty() ? NULL : BWResource::openSection( resource );
            BinaryPtr binary = section ? section->asBinary() : NULL;
            if (!binary || binary->len() <= 0 || component.name.empty() || !component.model)
            {
                ERROR_MSG( "TargetScene: missing collision mesh for %s: %s\n",
                    component.name.c_str(), resource.c_str() );
                return false;
            }
            BW::vector<DataSectionPtr> materials;
            definition->openSections( "material", materials );
            std::vector<std::string> names;
            for (size_t materialIndex = 0; materialIndex < materials.size(); ++materialIndex)
            {
                DataSectionPtr material = materials[materialIndex];
                const BW::string name = material->readString( "name" );
                if (name.empty() || !material->openSection( "present" ))
                    return false;
                names.push_back( std::string( name.c_str() ) );
                const bool present = material->readBool( "present" );
                wot::ArmorMaterial value;
                if (present && !readMaterial( material, value ))
                {
                    ERROR_MSG( "TargetScene: incomplete armor material %s/%s\n",
                        component.name.c_str(), name.c_str() );
                    return false;
                }
                component.materials.push_back( value );
                component.present.push_back( present );
            }
            const std::uint8_t * bytes = static_cast<const std::uint8_t *>( binary->data() );
            component.mesh = wot::parse_vehicle_collision_bsp2(
                std::vector<std::uint8_t>( bytes, bytes + binary->len() ), names );
            state.components.push_back( std::move( component ) );
        }
    }
    catch (const std::exception & error)
    {
        ERROR_MSG( "TargetScene: collision resources rejected: %s\n", error.what() );
        return false;
    }
    state.configured = true;
    INFO_MSG( "TargetScene: original BSP target loaded; hp=%d components=%u origin=(%g,%g,%g)\n",
        state.health, static_cast<uint32>( state.components.size() ), origin.x, origin.y, origin.z );
    return true;
}

bool TargetScene::active() const { return state_->configured && state_->health > 0; }

bool TargetScene::aimPoint( const Vector3 & start, const Vector3 & finish,
    Vector3 & point ) const
{
    point = finish;
    if (!active() || (finish - start).lengthSquared() <= 0.f)
        return true;
    try
    {
        const std::vector<wot::VehicleCollisionHit> hits = wot::collide_vehicle_segment(
            state_->collisionComponents(), collisionVector( start ), collisionVector( finish ) );
        if (!hits.empty())
            point = nativeVector( hits.front().point );
        return true;
    }
    catch (const std::exception & error)
    {
        WARNING_MSG( "TargetScene: read-only aim query failed: %s\n", error.what() );
        return false;
    }
}

std::shared_ptr<TargetScene::ShotState> TargetScene::createShot(
    const wot::CombatShot & combat, double factor, double damageRoll ) const
{
    std::shared_ptr<ShotState> shot( new ShotState() );
    shot->combat = combat;
    shot->penetrationFactor = factor;
    shot->damageRoll = damageRoll;
    return shot;
}

TargetScene::TraceResult TargetScene::trace( ShotState & shot,
    const Vector3 & origin, const Vector3 & start, const Vector3 & finish,
    double travelled )
{
    TraceResult result = { false, 1.0, finish, "" };
    if (!active())
        return result;
    State & state = *state_;
    try
    {
        const std::vector<wot::VehicleCollisionHit> hits = wot::collide_vehicle_segment(
            state.collisionComponents(), collisionVector( start ), collisionVector( finish ) );
        for (size_t index = 0; index < hits.size(); ++index)
        {
            const wot::VehicleCollisionHit & hit = hits[index];
            State::Component & component = state.components[hit.component_index];
            const Vector3 point = nativeVector( hit.point );
            const wot::ArmorMaterial * material = component.present[hit.material_index] ?
                &component.materials[hit.material_index] : NULL;
            shot.history.push_back( { travelled + hit.distance, hit.hit_angle_cos,
                material, std::string( component.name.c_str() ) } );
            shot.points.push_back( point );
            shot.names.push_back( component.mesh.materials[hit.material_index].c_str() );
            if (!shot.firstRange)
                shot.firstRange = wot::projectile_range_distance(
                    ballisticVector( origin ), ballisticVector( point ) );
            const std::optional<wot::ArmorContact> contact = wot::resolve_armor_contact(
                shot.combat, *shot.firstRange, shot.history, 0.0, shot.penetrationFactor );
            if (!contact)
            {
                INFO_MSG( "TargetScene: pass-through component=%s material=%s point=(%g,%g,%g)\n",
                    component.name.c_str(), shot.names.back().c_str(), point.x, point.y, point.z );
                continue;
            }
            const size_t contactIndex = contact->collision_index;
            const bool structural = contact->layer == wot::ArmorLayer::structural;
            int loss = 0;
            if (structural)
            {
                loss = wot::damage( shot.combat, contact->result,
                    contact->material.armor.value_or( 0.0 ), shot.damageRoll );
                loss = std::min( state.health, std::max( 0, loss ) );
                state.health -= loss;
            }
            const char * verdict = penetrationName( contact->result );
            std::ostringstream feedback;
            feedback << verdict << "  damage " << loss << "  " << contact->component
                << "/" << shot.names[contactIndex].c_str();
            state.feedback = feedback.str().c_str();
            INFO_MSG( "TargetScene: contact component=%s material=%s result=%s layer=%s armor=%g effectiveArmor=%g piercing=%g loss=%g damage=%d hp=%d/%d\n",
                contact->component.c_str(), shot.names[contactIndex].c_str(), verdict,
                structural ? "structural" : "external", contact->material.armor.value_or( 0.0 ),
                contact->effective_armor, contact->piercing, contact->accumulated_loss,
                loss, state.health, state.maximumHealth );
            result.terminal = true;
            result.point = shot.points[contactIndex];
            result.fraction = hit.fraction;
            result.reason = structural ? "target_structural_contact" : "target_external_stop";
            if (state.health == 0)
            {
                state.removeModels();
                INFO_MSG( "TargetScene: target destroyed; models removed from native space\n" );
            }
            return result;
        }
    }
    catch (const std::exception & error)
    {
        WARNING_MSG( "TargetScene: projectile-local collision failure: %s\n", error.what() );
        result.terminal = true;
        result.fraction = 0.0;
        result.point = start;
        result.reason = "target_query_failure";
    }
    return result;
}

void TargetScene::draw()
{
    State & state = *state_;
    if (!state.configured || !FontManager::pInstance())
        return;
    if (!state.fontAttempted)
    {
        state.font = FontManager::instance().get( "system_small.font" );
        state.fontAttempted = true;
    }
    if (state.font && FontManager::instance().begin( *state.font ))
    {
        std::ostringstream text;
        text << "Target HP: " << state.health << "/" << state.maximumHealth;
        if (state.health == 0) text << "  DESTROYED";
        state.font->drawString( BW::string( text.str().c_str() ), 16, 16 );
        state.font->drawString( state.feedback, 16, 38 );
        FontManager::instance().end();
    }
}

void TargetScene::clear()
{
    state_->removeModels();
    delete state_;
    state_ = new State();
}

BW_END_NAMESPACE
