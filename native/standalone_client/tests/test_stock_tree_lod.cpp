#ifdef NDEBUG
#undef NDEBUG
#endif
#include "stock_tree_lod.hpp"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <vector>

namespace
{
void writeValues( std::ostream & output, const wot::StockTreeLodValues & value )
{
    output.write( reinterpret_cast<const char *>( &value.branch ), 4 );
    output.write( reinterpret_cast<const char *>( &value.branchAlpha ), 4 );
    output.write( reinterpret_cast<const char *>( &value.frond ), 4 );
    output.write( reinterpret_cast<const char *>( &value.frondAlpha ), 4 );
    output.write( reinterpret_cast<const char *>( value.leaves.data() ), 8 );
    output.write( reinterpret_cast<const char *>( value.leafAlpha.data() ), 8 );
    output.write( reinterpret_cast<const char *>( &value.billboardFade ), 4 );
}
}

int main( int argc, char ** argv )
{
    if (argc == 4 && std::strcmp( argv[3], "--decode" ) == 0)
    {
        std::ifstream input( argv[1], std::ios::binary );
        std::ofstream output( argv[2], std::ios::binary | std::ios::trunc );
        if (!input || !output) return 2;
        std::size_t caseNumber = 0;
        while (input.peek() != std::char_traits<char>::eof())
        {
            std::uint32_t size;
            input.read( reinterpret_cast<char *>( &size ), 4 );
            if (!input || size > 0x1000000) return 3;
            std::vector<std::uint8_t> data( size );
            input.read( reinterpret_cast<char *>( data.data() ), size );
            if (!input) return 3;
            wot::StockTreeLodConfiguration state;
            std::string error;
            if (!wot::decodeStockTreeLodConfiguration( data.data(), data.size(), state, error ))
            {
                std::fprintf( stderr, "Case %zu: %s\n", caseNumber, error.c_str() );
                return 4;
            }
            wot::StockTreeLodConfiguration unchanged;
            unchanged.leafMethod = 123;
            assert( !wot::decodeStockTreeLodConfiguration( data.data(), data.size() - 1, unchanged, error ) );
            assert( unchanged.leafMethod == 123 && !error.empty() );
            data.push_back( 0 );
            assert( !wot::decodeStockTreeLodConfiguration( data.data(), data.size(), unchanged, error ) );
            assert( unchanged.leafMethod == 123 && !error.empty() );
            output.write( reinterpret_cast<const char *>( &state.leafMethod ), 4 );
            output.write( reinterpret_cast<const char *>( &state.transition ), 4 );
            output.write( reinterpret_cast<const char *>( &state.exponent ), 4 );
            output.write( reinterpret_cast<const char *>( &state.billboardTransition ), 4 );
            output.write( reinterpret_cast<const char *>( &state.alphaReference ), 4 );
            output.write( reinterpret_cast<const char *>( state.partCounts.data() ), 12 );
            ++caseNumber;
        }
        return output ? 0 : 5;
    }
    if (argc == 3)
    {
        std::ifstream input( argv[1], std::ios::binary );
        std::ofstream output( argv[2], std::ios::binary | std::ios::trunc );
        if (!input || !output) return 2;
        while (input.peek() != std::char_traits<char>::eof())
        {
            wot::StockTreeLodConfiguration state;
            float level;
            std::uint32_t drop;
            input.read( reinterpret_cast<char *>( &state.leafMethod ), 4 );
            input.read( reinterpret_cast<char *>( &state.transition ), 4 );
            input.read( reinterpret_cast<char *>( &state.exponent ), 4 );
            input.read( reinterpret_cast<char *>( &state.billboardTransition ), 4 );
            input.read( reinterpret_cast<char *>( &state.alphaReference ), 4 );
            input.read( reinterpret_cast<char *>( state.partCounts.data() ), 12 );
            input.read( reinterpret_cast<char *>( &level ), 4 );
            input.read( reinterpret_cast<char *>( &drop ), 4 );
            if (!input) return 3;
            wot::StockTreeLodValues value;
            std::string error;
            if (!wot::selectStockTreeLods( state, level, drop != 0, value, error ))
            {
                std::fprintf( stderr, "%s\n", error.c_str() );
                return 4;
            }
            writeValues( output, value );
        }
        return output ? 0 : 5;
    }
    if (argc != 1) return 1;
    wot::StockTreeLodConfiguration state;
    state.partCounts = {{ 3, 2, 3 }};
    state.transition = .1f;
    state.exponent = 1.4f;
    state.billboardTransition = .5f;
    wot::StockTreeLodValues value;
    std::string error;
    assert( wot::selectStockTreeLods( state, 1.f, true, value, error ) );
    assert( value.branch == 0 && value.frond == 0 && value.leaves[0] == 0 );
    assert( value.branchAlpha == 84.f && value.billboardFade == 0.f );
    assert( wot::selectStockTreeLods( state, 0.f, true, value, error ) );
    assert( value.branch == -1 && value.frond == -1 && value.leaves[0] == -1 );
    assert( value.billboardFade == 1.f && value.leafAlpha[0] == 255.f );
    assert( wot::selectStockTreeLods( state, 0.f, false, value, error ) );
    assert( value.branch == 2 && value.frond == 1 && value.leaves[0] == 2 );
    value.branch = 123;
    assert( !wot::selectStockTreeLods( state, std::numeric_limits<float>::quiet_NaN(), true, value, error ) );
    assert( value.branch == 123 && !error.empty() );
    state.partCounts[0] = 7;
    assert( !wot::selectStockTreeLods( state, .5f, true, value, error ) );
    assert( value.branch == 123 );
    std::puts( "Original tree LOD selection boundaries passed" );
    return 0;
}
