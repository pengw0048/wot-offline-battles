#pragma once

#include <algorithm>
#include <cmath>

namespace wot
{
struct StockGasCloudSample
{
    float elapsed;
    float radius;
    float height;
    float alpha;
    float edgeDistance;
    bool proximity;
};

// #1513 008b3880: server-clock interpolation, quadratic climb, horizontal edge.
inline StockGasCloudSample sampleStockGasCloud( double now, double start,
    float compressionTime, float startRadius, float endRadius, float climbTime,
    float startHeight, float endHeight, float proximityDistance,
    float centerX, float centerZ, float cameraX, float cameraZ )
{
    StockGasCloudSample value;
    value.elapsed = static_cast<float>( std::max( now - start, 0.0 ) );
    const float compression = std::min( value.elapsed / compressionTime, 1.f );
    value.alpha = std::min( value.elapsed / climbTime, 1.f );
    value.radius = (endRadius - startRadius) * compression + startRadius;
    value.height = startHeight - (endHeight - startHeight) * value.alpha * (value.alpha - 2.f);
    const float x = cameraX - centerX;
    const float z = cameraZ - centerZ;
    value.edgeDistance = value.radius - std::sqrt( x * x + z * z );
    value.proximity = value.edgeDistance <= proximityDistance;
    return value;
}
// #1513 00f9b5c0 / 00f9b4e0: transparent sprite render buckets around
// the gas cylinder. Both results remain drawable; this is not particle culling.
struct StockTornadoSplitter
{
    float x = 0, z = 0, radiusSquared = 0, ceiling = 0;
    float cameraX = 0, cameraZ = 0;
    bool cameraAbove = false, cameraInside = false;
    void prepare( float centerX, float centerZ, float radius, float top,
        float eyeX, float eyeY, float eyeZ )
    {
        x = centerX; z = centerZ; radiusSquared = radius * radius; ceiling = top;
        cameraX = eyeX - x; cameraZ = eyeZ - z;
        cameraAbove = ceiling < eyeY;
        cameraInside = cameraX * cameraX + cameraZ * cameraZ < radiusSquared;
    }
    bool classify( float pointX, float pointY, float pointZ ) const
    {
        if (cameraAbove) return ceiling <= pointY;
        const float dx = pointX - x, dz = pointZ - z;
        const bool inside = dx * dx + dz * dz < radiusSquared;
        if (cameraInside) return inside;
        if (!inside && cameraZ * dz + dx * cameraX <= 0.f)
        {
            const float cross = dz * cameraX - cameraZ * dx;
            const float xDistance = dx - cameraX, zDistance = dz - cameraZ;
            return (xDistance * xDistance + zDistance * zDistance) * radiusSquared -
                cross * cross <= 0.f;
        }
        return true;
    }
};
}

#ifndef WOT_STOCK_BATTLEGROUND_PORTABLE
#include "cstdmf/bw_namespace.hpp"
#include "particle/particles.hpp"
#include "math/matrix.hpp"
#include "moo/draw_context.hpp"
BW_BEGIN_NAMESPACE
extern int StockBattlegroundElements_token;
class ParticleSystemRenderer;
bool drawStockBattlegroundParticles( ParticleSystemRenderer * renderer, const Matrix & world,
    Particles::iterator begin, Particles::iterator end );
void tickStockBattlegroundElements();
void beginStockBattlegroundParticles( const Moo::DrawContext * context );
bool stockBattlegroundParticlePhase( const Moo::DrawContext * context );
bool isStockBattlegroundParticleItem( Moo::DrawContext::UserDrawItem * item );
void drawStockBattlegroundParticleItem( Moo::DrawContext::UserDrawItem * item );
void finishStockBattlegroundParticles();
void finiStockBattlegroundElements();
bool drawStockBattlegroundElements();
BW_END_NAMESPACE
#endif
