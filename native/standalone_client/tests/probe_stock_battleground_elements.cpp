#define WOT_STOCK_BATTLEGROUND_PORTABLE
#include "../bigworld/stock_battleground_elements.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cstdio>
#include <iostream>
#include <string>
#include <cstring>
#include <cstdint>

int main( int argc, char ** argv )
{
    if (argc == 2 && std::string( argv[1] ) == "--classify")
    {
        float x, z, radius, ceiling, eyeX, eyeY, eyeZ, pointX, pointY, pointZ;
        while (std::cin >> x >> z >> radius >> ceiling >> eyeX >> eyeY >> eyeZ >> pointX >> pointY >> pointZ)
        {
            wot::StockTornadoSplitter splitter;
            splitter.prepare( x, z, radius, ceiling, eyeX, eyeY, eyeZ );
            std::cout << splitter.classify( pointX, pointY, pointZ ) << '\n';
        }
        return 0;
    }
    if (argc == 2 && std::string( argv[1] ) == "--sample")
    {
        double now, start;
        float compression, startRadius, endRadius, climb, startHeight, endHeight;
        while (std::cin >> now >> start >> compression >> startRadius >> endRadius >> climb >> startHeight >> endHeight)
        {
            const auto value = wot::sampleStockGasCloud( now, start, compression, startRadius,
                endRadius, climb, startHeight, endHeight, 30.f, 0.f, 0.f, 0.f, 0.f );
            for (float number : { value.elapsed, value.radius, value.height, value.alpha })
            {
                std::uint32_t bits;
                std::memcpy( &bits, &number, sizeof( bits ) );
                std::cout << bits << ' ';
            }
            std::cout << '\n';
        }
        return 0;
    }
    const auto before = wot::sampleStockGasCloud( 9, 10, 100, 500, 100, 5, 0, 150, 30, 50, 70, 50, 70 );
    assert(before.elapsed == 0 && before.radius == 500 && before.height == 0 && !before.proximity);
    const auto midway = wot::sampleStockGasCloud( 12.5, 10, 100, 500, 100, 5, 0, 150, 30, 50, 70, 540, 70 );
    assert(midway.radius == 490 && midway.alpha == .5f && midway.height == 112.5f);
    assert(midway.edgeDistance == 0 && midway.proximity);
    const auto after = wot::sampleStockGasCloud( 210, 10, 100, 500, 100, 5, 0, 150, 30, 50, 70, 50, -60 );
    assert(after.radius == 100 && after.height == 150 && after.alpha == 1 && after.edgeDistance == -30);
    const auto boundary = wot::sampleStockGasCloud( 10, 10, 100, 500, 100, 5, 0, 150, 30, 0, 0, 470, 0 );
    assert(boundary.proximity && boundary.edgeDistance == 30);
    wot::StockTornadoSplitter splitter;
    splitter.prepare( 0, 0, 10, 100, 0, 0, 0 );
    assert(splitter.classify( 9, 0, 0 ));
    assert(!splitter.classify( 10, 0, 0 ));
    assert(!splitter.classify( -11, 0, 0 ));
    splitter.prepare( 0, 0, 10, 100, 20, 0, 0 );
    assert(splitter.classify( 0, 0, 0 ));
    assert(splitter.classify( 30, 0, 0 ));
    assert(!splitter.classify( -30, 0, 0 ));
    assert(splitter.classify( -30, 0, 100 ));
    splitter.prepare( 0, 0, 10, 100, 20, 101, 0 );
    assert(splitter.classify( 0, 100, 0 ));
    assert(!splitter.classify( 0, 99, 0 ));
    std::puts("Stock BattlegroundElements interpolation and render partition probes passed");
}
