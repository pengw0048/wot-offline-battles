#ifdef NDEBUG
#undef NDEBUG
#endif
#include "stock_tree_wind.hpp"
#include <cassert>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <limits>

namespace
{
std::uint32_t randomInput( void * context )
{
    static const std::uint32_t values[] = { 0, 16384, 32767, 32767, 800, 12000, 20000, 30000, 0, 32767, 0 };
    std::size_t & index = *static_cast<std::size_t *>( context );
    return values[index++ % (sizeof( values ) / sizeof( values[0] ))];
}
}

int main( int argc, char ** argv )
{
    if (argc != 3 && argc != 4) return 1;
    std::ifstream source( argv[1], std::ios::binary );
    if (!source) return 2;
    const std::string input( (std::istreambuf_iterator<char>( source )), std::istreambuf_iterator<char>() );
    wot::StockTreeWindParameters parameters;
    std::string error;
    assert( wot::decodeStockTreeWindParameters( input.data(), input.size(), parameters, error ) );
    assert( parameters.matrixCount == 6 && parameters.leafAngleCount == 8 );
    const auto original = parameters;
    parameters.matrixCount = 123;
    assert( !wot::decodeStockTreeWindParameters( "", 0, parameters, error ) );
    assert( parameters.matrixCount == 123 && !error.empty() );
    std::string malformed( input );
    malformed[malformed.find( "WindResponseAndLimit" )] = '\0';
    assert( !wot::decodeStockTreeWindParameters( malformed.data(), malformed.size(), parameters, error ) );
    assert( parameters.matrixCount == 123 );
    parameters = original;
    wot::StockTreeWind wind;
    assert( !wind.advance( 0.f, NULL, NULL, error ) );
    assert( wind.initialise( parameters, error ) );
    auto unsupported = parameters;
    unsupported.matrixCount = 5;
    const auto beforeInitialiseFailure = wind.matrices();
    assert( !wind.initialise( unsupported, error ) );
    assert( wind.matrices() == beforeInitialiseFailure );
    assert( wind.setStrengthAndDirection( .7f, {{ 1.f, 0.f, 2.f }}, error ) );
    std::size_t index = 0;
    const float times[] = { 0.f, .016f, .032f, .05f, .1f, 1.f, 2.f, 5.f, 6.f, 7.f, 8.f, 11.f, 15.f };
    std::ofstream output( argv[argc - 1], std::ios::binary | std::ios::trunc );
    if (!output) return 2;
    if (argc == 4)
    {
        std::ifstream steps( argv[2], std::ios::binary );
        if (!steps) return 2;
        while (steps.peek() != std::char_traits<char>::eof())
        {
            float time, strength;
            std::array<float, 3> direction;
            std::uint32_t reset;
            steps.read( reinterpret_cast<char *>( &time ), 4 );
            steps.read( reinterpret_cast<char *>( &strength ), 4 );
            steps.read( reinterpret_cast<char *>( direction.data() ), 12 );
            steps.read( reinterpret_cast<char *>( &reset ), 4 );
            if (!steps) return 2;
            if (reset) wind.reset();
            assert( wind.setStrengthAndDirection( strength, direction, error ) );
            assert( wind.advance( time, randomInput, &index, error ) );
            for (const auto & matrix : wind.matrices())
                output.write( reinterpret_cast<const char *>( matrix.data() ), 64 );
            for (const auto & angles : wind.leafAngles())
                output.write( reinterpret_cast<const char *>( angles.data() ), 16 );
            const std::uint32_t consumed = static_cast<std::uint32_t>( index );
            output.write( reinterpret_cast<const char *>( &consumed ), 4 );
        }
        return output ? 0 : 4;
    }
    for (float time : times)
    {
        if (!wind.advance( time, randomInput, &index, error ))
        {
            std::fprintf( stderr, "%s\n", error.c_str() );
            return 3;
        }
        for (const auto & matrix : wind.matrices())
            output.write( reinterpret_cast<const char *>( matrix.data() ), 64 );
        for (const auto & angles : wind.leafAngles())
            output.write( reinterpret_cast<const char *>( angles.data() ), 16 );
    }
    assert( index == 11 );
    const auto beforeFailure = wind.matrices();
    assert( !wind.advance( std::numeric_limits<float>::quiet_NaN(), randomInput, &index, error ) );
    assert( beforeFailure == wind.matrices() && index == 11 );
    wind.reset();
    const auto beforeRandomFailure = wind.matrices();
    assert( !wind.advance( .016f, NULL, NULL, error ) );
    assert( beforeRandomFailure == wind.matrices() );
    std::puts( "Original tree wind state and terminal failures passed" );
    return output ? 0 : 4;
}
