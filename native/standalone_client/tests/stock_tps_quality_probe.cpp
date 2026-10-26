#define WOT_STOCK_TPS_QUALITY_PORTABLE
#include "../bigworld/stock_tps_quality.hpp"
#include <cassert>
#include <cstring>
#include <iostream>

int main( int argc, char ** )
{
    if (argc > 1)
    {
        wot::StockTPSPerformer owner;
        float elapsed;
        int disabled, frozen;
        while (std::cin >> owner.smoothedFrameTime >> elapsed >> owner.minimumFPS >> owner.maximumFPS >>
            owner.maximumSpeedMultiplier >> owner.maximumLodOffset >> owner.maximumLodMultiplier >> disabled >> frozen)
        {
            owner.disabled = disabled != 0;
            owner.frozen = frozen != 0;
            owner.speedMultiplier = 4.f;
            owner.lodMultiplier = 5.f;
            owner.lodOffset = 6.f;
            owner.density = 7.f;
            owner.tick( elapsed );
            for (float value : { owner.smoothedFrameTime, owner.speedMultiplier,
                owner.lodMultiplier, owner.lodOffset, owner.density })
            {
                std::uint32_t bits;
                std::memcpy( &bits, &value, sizeof( bits ) );
                std::cout << bits << ' ';
            }
            std::cout << '\n';
        }
        return 0;
    }
    wot::StockTPSPerformer owner;
    assert(owner.disabled && owner.smoothedFrameTime == 29.f);
    owner.tick( .1f );
    assert(owner.smoothedFrameTime == 29.f && owner.density == 1.f);
    wot::StockTPSPerformanceConfig config;
    config.speedMultiplier = .25f;
    config.lodMultiplier = 4.f;
    config.lodOffset = 200.f;
    owner.configure( config );
    owner.enabled( true );
    owner.tick( 1.f );
    assert(owner.density == .01f && owner.speedMultiplier == .25f && owner.lodMultiplier == 4.f);
    owner.freeze( true );
    const float smoothed = owner.smoothedFrameTime;
    owner.tick( 10.f );
    assert(owner.smoothedFrameTime == smoothed && owner.density == 1.f);
    owner.freeze( false );
    owner.tick( .04f );
    assert(owner.smoothedFrameTime != smoothed);
    owner.normalMultiplier( 3.f ); owner.normalOffset( 4.f );
    owner.secondaryMultiplier( 5.f ); owner.secondaryOffset( 6.f );owner.timeMultiplier( .5f );
    assert(owner.normalLodMultiplier == 3 && owner.normalLodOffset == 4 &&
        owner.secondaryLodMultiplier == 5 && owner.secondaryLodOffset == 6 && owner.baseTimeMultiplier == .5f);
    std::cout << "Native tPS performancer lifecycle and adaptive coefficients passed\n";
}
