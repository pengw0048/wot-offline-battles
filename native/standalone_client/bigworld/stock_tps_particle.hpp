#pragma once
#include "stock_tps_resources.hpp"
#include <cmath>
#include <cstdint>

namespace wot
{
struct StockTPSVector3 { float x = 0.f, y = 0.f, z = 0.f; };

// Native tPS_Particle_Fx movement state. The emitter's resource lease must
// outlive its particles; source points into that immutable resource tree.
class StockTPSParticle
{
public:
    const StockTPSEmitterResource * source = nullptr;
    std::uint32_t flags = 0;
    StockTPSVector3 velocity, position, inheritedVelocity, additionalVelocity;
    float speedMultiplier = 1.f, windMultiplier = 1.f;
    float delay = 0.f, lifetime = 0.f, age = 0.f;
    float gravity = 0.f, windResponse = 0.f, inheritedVelocityDuration = 0.f;
    std::int64_t ownerIdentity = -1;
    unsigned children = 0;

    bool retired() const { return (flags & 0x201u) == 0x201u; }

    // faa540/faa760 use separate fixed steps for ordinary and special passes.
    void tick( float elapsed, const StockTPSVector3 & wind,
        std::int64_t identity, bool specialPass = false, bool paused = false )
    {
        const float step = specialPass ? 0.083333336f : 0.011111111f;
        if (!specialPass || elapsed > step)
        {
            while (elapsed = elapsed - step, 0.f < elapsed)
                advance( step, wind, identity, paused );
            advance( elapsed + step, wind, identity, paused );
        }
        else
            advance( elapsed, wind, identity, paused );
    }

private:
    const StockTPSCurve * curve( unsigned index ) const
    {
        if (!source || source->curves[index].times.size() <= 1) return nullptr;
        return &source->curves[index];
    }
    bool sample( unsigned index, float & value ) const
    {
        const auto * keys = curve( index );
        std::array<float, 4> result{};
        if (!keys || !keys->sample( age / lifetime, result )) return false;
        value = result[0];
        return true;
    }
    StockTPSVector3 displacement( float elapsed )
    {
        // fa3030 computes displacement before fa3770 applies gravity.
        StockTPSVector3 result{ velocity.x * elapsed * speedMultiplier,
            velocity.y * elapsed * speedMultiplier, velocity.z * elapsed * speedMultiplier };
        if (0.f < inheritedVelocityDuration)
        {
            const float fraction = (inheritedVelocityDuration - age) / inheritedVelocityDuration;
            if (0.f < fraction)
            {
                result.x = result.x + inheritedVelocity.x * fraction * elapsed;
                result.y = result.y + inheritedVelocity.y * fraction * elapsed;
                result.z = result.z + inheritedVelocity.z * fraction * elapsed;
            }
            else
                inheritedVelocityDuration = 0.f;
        }
        result.x = additionalVelocity.x * windMultiplier * elapsed + result.x;
        result.y = additionalVelocity.y * windMultiplier * elapsed + result.y;
        result.z = additionalVelocity.z * windMultiplier * elapsed + result.z;
        return result;
    }
    void advance( float elapsed, const StockTPSVector3 & wind,
        std::int64_t identity, bool paused )
    {
        if (retired()) return;
        if (flags & 0x10000u)
        {
            if (!curve( 3 )) flags &= ~0x10000u;
            else if (!sample( 3, speedMultiplier )) speedMultiplier = 1.f;
        }
        if (windResponse != 0.f)
        {
            const float x = wind.x - velocity.x;
            const float y = wind.y - velocity.y;
            const float z = wind.z - velocity.z;
            const float distance = std::sqrt( y * y + x * x + z * z );
            const float inverse = 1.f / (std::sqrt( distance ) + 0.0001f);
            const float amount = windResponse * distance * elapsed;
            velocity.x = x * inverse * amount + velocity.x;
            velocity.y = y * inverse * amount + velocity.y;
            velocity.z = z * inverse * amount + velocity.z;
        }
        if ((flags & 0x4000u) && curve( 4 ) && !sample( 4, windMultiplier )) windMultiplier = 1.f;
        // fa3770: pause leaves particle movement/lifetime untouched. The
        // surrounding pass still samples curves and computes wind response.
        if (paused) return;
        if (ownerIdentity >= 0 && ownerIdentity != identity) flags |= 1u;
        if (flags & 1u)
        {
            flags |= 0x200u;
            return;
        }
        if (delay <= 0.f)
        {
            float movementTime = elapsed;
            if (delay < 0.f)
            {
                movementTime = delay + elapsed;
                delay = 0.f;
            }
            const StockTPSVector3 movement = displacement( movementTime );
            if (!(flags & 0x80u))
            {
                float scale = gravity;
                if (!(flags & 0x2000u) || sample( 2, scale ))
                    velocity.y = velocity.y - scale * 9.8f * elapsed;
            }
            position.x = position.x + movement.x;
            position.y = movement.y + position.y;
            position.z = movement.z + position.z;
            age = age + elapsed;
            if (lifetime < age)
                flags |= children || (flags & 0x40u) ? 0x20u : 1u;
        }
        else
            delay = delay - elapsed;
    }
};
}
