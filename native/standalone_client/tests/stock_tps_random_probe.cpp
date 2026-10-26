#include "../bigworld/stock_tps_random.hpp"
#include <cstring>
#include <iostream>
std::uint32_t bits( float value )
{ std::uint32_t result;std::memcpy( &result, &value, 4 );return result; }
int main()
{
    wot::StockTPSRandom owner( []() { return 0; } );
    for (auto & value : owner.values) std::cin >> value;
    for (auto & pair : owner.circle) for (auto & value : pair) std::cin >> value;
    unsigned shape;
    wot::StockTPSVector3 position, extent;
    while (std::cin >> shape >> owner.cursor >> owner.faceAxis >> owner.faceSign >>
        position.x >> position.y >> position.z >> extent.x >> extent.y >> extent.z)
    {
        owner.scatter( position, shape, extent );
        std::cout << bits( position.x ) << ' ' << bits( position.y ) << ' ' << bits( position.z ) << ' ' <<
            owner.cursor << ' ' << owner.faceAxis << ' ' << owner.faceSign << '\n';
    }
}
