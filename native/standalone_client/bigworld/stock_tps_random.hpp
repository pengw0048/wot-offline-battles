#pragma once
#include "stock_tps_particle.hpp"
#include <array>
#include <cmath>
#include <cstdint>

namespace wot
{
class StockTPSRandom
{
public:
    // 005e8c70 consumes exactly 4096 CRT rand() values during startup.
    template<class Generator> explicit StockTPSRandom( Generator generator )
    {
        for (auto & value : values) value = static_cast<float>( generator() ) * 0.000030518509447574615f;
        // 005e8cd0 allocates 80 zeroed pairs, then fills only the first 72.
        // roundf(72 * random) may select entry 72, which remains zero.
        float angle = 0.f;
        for (unsigned index = 0; index < 72; ++index)
        {
            circle[index][0] = std::cos( angle );
            circle[index][1] = std::sin( angle );
            angle = angle + 0.0872664600610733f;
        }
    }
    std::array<float, 4096> values{};
    std::array<std::array<float, 2>, 80> circle{};
    std::uint32_t cursor = 0;
    unsigned faceAxis = 0;
    int faceSign = -1;

    float next() { return values[++cursor & 0xfffu]; }
    float between( float minimum, float maximum )
    { return (maximum - minimum) * next() + minimum; }
    const std::array<float, 2> & nextCircle()
    { return circle[static_cast<unsigned>( std::round( 72.f * next() ) )]; }

    // f9e010/fa1080/fa1240/fa0f10: preserve the original shape law and
    // random-table consumption. Scene transforms are applied by the emitter.
    void scatter( StockTPSVector3 & position, unsigned shape, const StockTPSVector3 & extent )
    {
        if (shape == 1)
        {
            position.x = between( -extent.x, extent.x ) + position.x;
            position.y = between( -extent.y, extent.y ) + position.y;
            position.z = between( -extent.z, extent.z ) + position.z;
        }
        else if (shape == 2)
        {
            if (++faceAxis > 2) faceAxis = 0;
            faceSign = -faceSign;
            position.x = (faceAxis == 0 ? static_cast<float>(faceSign) * extent.x :
                between( -extent.x, extent.x )) + position.x;
            position.y = (faceAxis == 1 ? static_cast<float>(faceSign) * extent.y :
                between( -extent.y, extent.y )) + position.y;
            position.z = (faceAxis == 2 ? static_cast<float>(faceSign) * extent.z :
                between( -extent.z, extent.z )) + position.z;
        }
        else if (shape == 3 || shape == 4)
        {
            const auto first = nextCircle();
            const auto second = nextCircle();
            float x = first[0] * extent.x;
            float y = second[1] * extent.y * first[1];
            float z = second[0] * extent.z * first[1];
            if (shape == 3)
            {
                x = between( -x, x );
                y = between( -y, y );
                z = between( -z, z );
            }
            position.x = x + position.x;
            position.y = y + position.y;
            position.z = z + position.z;
        }
        else if (shape == 5 || shape == 6)
        {
            const auto direction = nextCircle();
            const float x = direction[0] * extent.x;
            const float z = direction[1] * extent.z;
            const float radius = values[(cursor + 1) & 0xfffu];
            if (shape == 6)
            {
                ++cursor;
                position.x = x * radius + position.x;
                position.z = z * radius + position.z;
                position.y = (1.f - radius) * extent.y + position.y;
            }
            else
            {
                ++cursor;
                position.x = between( -x, x ) + position.x;
                position.y = (1.f - radius) * extent.y * next() + position.y;
                position.z = between( -z, z ) + position.z;
            }
        }
    }
};
}
