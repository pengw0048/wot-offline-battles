#include "pch.hpp"
#include "stock_scene_collision.hpp"

#include "physics2/collision_callback.hpp"
#include "physics2/collision_obstacle.hpp"
#include "space/client_space.hpp"
#include "space/space_manager.hpp"

#include <cmath>

BW_BEGIN_NAMESPACE

namespace
{
Vector3 nativeVector( const wot::Vector3 & value )
{
    return Vector3( value.x, value.y, value.z );
}

bool finite( const Vector3 & value )
{
    return std::isfinite( value.x ) && std::isfinite( value.y ) &&
        std::isfinite( value.z );
}

class GroundRayHit : public CollisionCallback
{
public:
    GroundRayHit( const Vector3 & direction, float parameterLength ) :
        direction_( direction ), directionLength_( direction.length() ),
        nearest_( parameterLength ), found_( false ) {}

    int operator()( const CollisionObstacle & obstacle,
        const WorldTriangle & triangle, float distance ) override
    {
        // #1513 registers physics ray handlers for StaticModelFeature and
        // Terrain2SceneFeature, not dynamic vehicle embodiments. Their own
        // meshes must never become suspension support surfaces.
        if (obstacle.isMovingObstacle())
            return COLLIDE_ALL;
        const bool terrain = (triangle.flags() & TRIANGLE_TERRAIN) != 0;
        if (!terrain)
        {
            // Exact BSPRaycastNoDestrCb_ (0x00f5e600). The original placing
            // call at 0x006eddc0 passes a null destruction context. Preserve
            // its material interval as well as its 0x50 triangle flag mask.
            const unsigned material = triangle.materialKind();
            if ((triangle.flags() & 0x50u) != 0 ||
                (material >= 0x47u && material <= 0x64u))
                return COLLIDE_ALL;
        }
        const float parameter = distance / directionLength_;
        if (!std::isfinite( parameter ) || parameter < 0.f || parameter > nearest_)
            return COLLIDE_ALL;

        // #1513 f5f220 transforms static BSP normals by inverse transpose and
        // deliberately leaves their magnitude intact. Terrain ad7630 also
        // returns the triangle cross product, rather than a normalized normal.
        Matrix inverseTranspose;
        inverseTranspose.transpose( obstacle.transformInverse() );
        const Vector3 normal = inverseTranspose.applyVector( triangle.normal() );
        if (terrain && direction_.dotProduct( normal ) >= 0.f)
            return COLLIDE_ALL;
        if (!finite( normal ))
            return COLLIDE_ALL;
        nearest_ = parameter;
        hit_.distance = parameter;
        hit_.normal = { normal.x, normal.y, normal.z };
        hit_.is_terrain = terrain;
        found_ = true;
        return COLLIDE_BEFORE;
    }

    bool result( wot::VehicleCollisionRayHit & output ) const
    {
        if (found_) output = hit_;
        return found_;
    }

private:
    Vector3 direction_;
    float directionLength_;
    float nearest_;
    bool found_;
    wot::VehicleCollisionRayHit hit_;
};

class GroundCollisionProvider : public wot::VehicleCollisionProvider
{
public:
    bool prepare( const wot::VehicleCollisionBounds & bounds,
        std::uint32_t spaceID, std::uint32_t visibilityMask ) override
    {
        space_ = NULL;
        const Vector3 minimum = nativeVector( bounds.minimum );
        const Vector3 maximum = nativeVector( bounds.maximum );
        if (!finite( minimum ) || !finite( maximum ) ||
            minimum.x > maximum.x || minimum.y > maximum.y || minimum.z > maximum.z)
            return false;
        ClientSpace * space = SpaceManager::instance().space( spaceID );
        if (!space || space->cleared())
            return false;
        // The original f5fb60 does not consume its visibility-mask argument.
        // The public space supplies the broad phase per ray; retaining its
        // reference keeps callbacks safe across a pending space teardown.
        (void)visibilityMask;
        space_ = space;
        return true;
    }

    bool query_ray( const wot::VehicleCollisionRay & ray,
        wot::VehicleCollisionRayHit & output ) override
    {
        if (!space_ || space_->cleared())
            return false;
        const Vector3 origin = nativeVector( ray.origin );
        const Vector3 direction = nativeVector( ray.direction );
        const Vector3 end = origin + direction * ray.length;
        const float directionLength = direction.length();
        if (!finite( origin ) || !finite( direction ) || !finite( end ) ||
            !std::isfinite( directionLength ) || directionLength == 0.f ||
            !std::isfinite( ray.length ) || ray.length < 0.f)
            return false;
        GroundRayHit query( direction, ray.length );
        space_->collide( origin, end, query );
        // Public collision traversal may retain a rejected terrain/BSP hit in
        // its returned distance. Only an accepted callback owns this result.
        return query.result( output );
    }

private:
    ClientSpacePtr space_;
};
}

std::unique_ptr<wot::VehicleCollisionProvider> makeStockGroundCollisionProvider()
{
    return std::unique_ptr<wot::VehicleCollisionProvider>( new GroundCollisionProvider() );
}

BW_END_NAMESPACE
