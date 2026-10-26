#pragma once
#include "stock_tps_renderer.hpp"
#include <array>
#include <cmath>
#include <cstdint>

namespace wot
{
struct StockTPSQuad
{
    std::array<float, 12> corners{};
    float angle = 0.f;
};
static_assert( sizeof(StockTPSQuad) == 52, "Original angle cache entry" );

class StockTPSQuadCache
{
public:
    explicit StockTPSQuadCache( bool vertical )
    {
        float angle = 0.f;
        for (auto & quad : entries)
        {
            const float cosine = std::cos( angle ), sine = std::sin( angle );
            quad.corners = vertical ? std::array<float, 12>{ -cosine, 0.f, -sine,
                sine, 0.f, -cosine, -sine, 0.f, cosine, cosine, 0.f, sine } :
                std::array<float, 12>{ -cosine, -sine, 0.f, sine, -cosine, 0.f,
                    -sine, cosine, 0.f, cosine, sine, 0.f };
            quad.angle = angle;
            angle = angle + 0.004363323f;
        }
    }
    std::array<StockTPSQuad, 1440> entries;
    const StockTPSQuad * find( float rotation ) const
    {
        // fa1920 uses double wrapping and quantisation, separate from the
        // float accumulation used by the f9ce80 cache producer.
        if (!std::isfinite( rotation )) return nullptr;
        double angle = rotation;
        const double circle = 6.2831853071795864769;
        while (angle > circle) angle -= circle;
        while (angle < 0.) angle += circle;
        int index = static_cast<int>( angle * 229.18311805232929151 ) % 1440;
        if (index < 0) index = -index;
        return &entries[static_cast<unsigned>(index)];
    }
};

// f9cb90's authored grid and indices, consumed by f98f10/fa9670. Callers
// supply already evaluated native particle state and UV animation frames.
inline bool appendStockTPSQuad( const StockTPSCommonVertex & common,
    const StockTPSQuad & quad, const std::array<float, 4> & firstFrame,
    const std::array<float, 4> & nextFrame, unsigned tessellation,
    std::vector<StockTPSCommonVertex> & commonStream,
    std::vector<StockTPSOffsetVertex> & offsetStream,
    std::vector<std::uint16_t> & indexStream )
{
    if (tessellation > 3 || commonStream.size() != offsetStream.size()) return false;
    const unsigned segments = tessellation + 1, side = segments + 1;
    if (commonStream.size() > 65536 - side * side) return false;
    const std::uint16_t base = static_cast<std::uint16_t>( commonStream.size() );
    const float step = 1.f / static_cast<float>(segments);
    for (unsigned row = 0; row < side; ++row)
    {
        const float v = static_cast<float>(row) * step;
        float u = 0.f;
        for (unsigned column = 0; column < side; ++column, u = u + step)
        {
            StockTPSOffsetVertex offset{};
            for (unsigned axis = 0; axis < 3; ++axis)
                offset.offset[axis] = u * (quad.corners[3 + axis] - quad.corners[axis]) +
                    quad.corners[axis] + v * (quad.corners[6 + axis] - quad.corners[axis]);
            // The original source's fourth offset DWORD is alignment data.
            // All 18 original PS vertex variants consume TEXCOORD4.xyz only.
            offset.coordinates = {
                (firstFrame[2] - firstFrame[0]) * u + firstFrame[0],
                v * (firstFrame[3] - firstFrame[1]) + firstFrame[1],
                (nextFrame[2] - nextFrame[0]) * u + nextFrame[0],
                v * (nextFrame[3] - nextFrame[1]) + nextFrame[1] };
            commonStream.push_back( common );
            offsetStream.push_back( offset );
        }
    }
    for (unsigned row = 0; row < segments; ++row)
        for (unsigned column = 0; column < segments; ++column)
        {
            const auto a = static_cast<std::uint16_t>( base + row * side + column );
            const auto b = static_cast<std::uint16_t>( a + 1 );
            const auto c = static_cast<std::uint16_t>( a + side );
            const auto d = static_cast<std::uint16_t>( c + 1 );
            indexStream.insert( indexStream.end(), { a, b, c, b, d, c } );
        }
    return true;
}
}
