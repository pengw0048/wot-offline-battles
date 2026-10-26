#pragma once
#include "stock_tps_resources.hpp"
#include <cstdint>
#include <limits>

namespace wot
{
// fa3980's ordinary source clock. The sink owns real particles and child
// emitters; these separate callbacks preserve the original substep order.
class StockTPSEmissionClock
{
public:
    explicit StockTPSEmissionClock( const StockTPSEmitterResource & resource ) :
        rate_( resource.scalar( 0x74 ) ) {}
    float remaining = 0.f;

    template<class Sink> void advance( float elapsed, float density, Sink & sink )
    {
        const float scaled = density * elapsed;
        if (0.f <= remaining - scaled)
        {
            sink.advanceChildren( elapsed );
            sink.advanceBase( elapsed );
            remaining = remaining - scaled;
            return;
        }
        const float interval = 1.f / rate_;
        if (scaled <= interval)
        {
            sink.advanceChildren( elapsed );
            sink.advanceBase( elapsed );
            sink.emit();
            remaining = interval;
            return;
        }
        float cursor = interval - remaining;
        if (0.f <= cursor)
        {
            float step = cursor;
            unsigned emitted = 0;
            for (; cursor < scaled; cursor = cursor + interval)
            {
                if (emitted == 256)
                {
                    remaining = interval;
                    return;
                }
                ++emitted;
                sink.advanceBase( step );
                sink.emit();
                step = interval;
            }
            // The original uses elapsed here, not density * elapsed.
            remaining = interval - (cursor - elapsed);
            sink.advanceChildren( elapsed );
            sink.advanceBase( remaining );
            return;
        }
        sink.advanceChildren( elapsed );
        sink.advanceBase( elapsed );
    }

    // f9e8c0: burst resources consume the performancer's density once when
    // creating their real particles; ordinary sources consume it in the clock.
    static int burstCount( const StockTPSEmitterResource & resource, float density )
    {
        if (!(resource.word( 8 ) & 0x100u)) return 1;
        const float scaled = (resource.scalar( 0x74 ) - 1.f) * density;
        // x86 CVTTSS2SI returns INT_MIN for unordered/out-of-range input.
        const int count = scaled >= -2147483648.f && scaled < 2147483648.f ?
            static_cast<int>( scaled ) : (std::numeric_limits<int>::min)();
        return count + 1;
    }
private:
    float rate_;
};
}
