#define WOT_STOCK_TPS_RENDERER_PORTABLE
#include "../bigworld/stock_tps_geometry.hpp"
#include <cassert>
#include <iostream>
int main( int argc, char ** )
{
    wot::StockTPSQuadCache cache( false );
    if (argc > 1)
    {
        float angle;
        while (std::cin >> angle)
        {
            const auto * result = cache.find( angle );
            if (!result) std::cout << -1 << '\n';
            else std::cout << result - cache.entries.data() << '\n';
        }
        return 0;
    }
    wot::StockTPSCommonVertex vertex{};
    const std::array<float, 4> texture = { .1f, .2f, .8f, .9f };
    for (unsigned tessellation = 0; tessellation <= 3; ++tessellation)
    {
        std::vector<wot::StockTPSCommonVertex> common;
        std::vector<wot::StockTPSOffsetVertex> offsets;
        std::vector<std::uint16_t> indices;
        assert( wot::appendStockTPSQuad( vertex, cache.entries[0], texture, texture, tessellation, common, offsets, indices ) );
        assert( common.size() == (tessellation + 2) * (tessellation + 2) );
        assert( offsets.size() == common.size() && indices.size() == (tessellation + 1) * (tessellation + 1) * 6 );
        for (auto index : indices) assert( index < common.size() );
        assert( !wot::appendStockTPSQuad( vertex, cache.entries[0], texture, texture, 4, common, offsets, indices ) );
    }
    assert( !cache.find( std::nanf("") ) && !cache.find( INFINITY ) );
    std::cout << "Original tPS angle cache and four tessellation grids passed\n";
}
