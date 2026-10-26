#if defined(_MSC_VER) && !defined(_CRT_SECURE_NO_WARNINGS)
// These scans write only fixed-width numbers and retain the original CRT parser.
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "stock_tree_wind.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <utility>

namespace wot
{
namespace
{
float add( float a, float b ) { volatile float result = a + b; return result; }
float sub( float a, float b ) { volatile float result = a - b; return result; }
float mul( float a, float b ) { volatile float result = a * b; return result; }
float div( float a, float b ) { volatile float result = a / b; return result; }
float clamp( float value, float low, float high ) { return std::max( low, std::min( value, high ) ); }
StockTreeWind::Matrix identity()
{
    return {{ 1.f, 0.f, 0.f, 0.f, 0.f, 1.f, 0.f, 0.f,
        0.f, 0.f, 1.f, 0.f, 0.f, 0.f, 0.f, 1.f }};
}
StockTreeWind::Matrix rotation( float angle, float x, float z )
{
    const float radians = div( angle, 57.29578f );
    const float sine = float( std::sin( double( radians ) ) );
    const float cosine = float( std::cos( double( radians ) ) );
    const float other = sub( 1.f, cosine );
    return {{ add( mul( mul( x, other ), x ), cosine ), mul( z, sine ), mul( mul( x, other ), z ), 0.f,
        -mul( sine, z ), cosine, mul( x, sine ), 0.f,
        mul( mul( x, other ), z ), -mul( sine, x ), add( mul( mul( z, other ), z ), cosine ), 0.f,
        0.f, 0.f, 0.f, 1.f }};
}
StockTreeWind::Matrix multiply( const StockTreeWind::Matrix & left, const StockTreeWind::Matrix & right )
{
    StockTreeWind::Matrix result = {};
    for (std::size_t row = 0; row < 4; ++row)
        for (std::size_t column = 0; column < 4; ++column)
            for (std::size_t k = 0; k < 4; ++k)
                result[row * 4 + column] = add( result[row * 4 + column], mul( right[k * 4 + column], left[row * 4 + k] ) );
    return result;
}
bool validate( const StockTreeWindParameters & p, std::string & error )
{
    if (p.matrixCount != 6 || p.leafAngleCount < 1 || p.leafAngleCount > 128)
    {
        error = "Original tree shaders require six wind matrices and 1..128 leaf angles";
        return false;
    }
    const float scalars[] = { p.response, p.responseLimit, p.maximumBend, p.branchExponent,
        p.leafExponent, p.gustMinimum, p.gustMaximum, p.gustDurationMinimum, p.gustDurationMaximum, p.gustFrequency };
    for (float value : scalars)
        if (!std::isfinite( value ) || value < 0.f)
        {
            error = "Original tree wind parameters contain an invalid scalar";
            return false;
        }
    for (const auto & oscillator : p.oscillation)
        for (float value : oscillator)
            if (!std::isfinite( value ))
            {
                error = "Original tree wind oscillator contains a nonfinite scalar";
                return false;
            }
    return true;
}
}

bool decodeStockTreeWindParameters( const char * data, std::size_t size,
    StockTreeWindParameters & result, std::string & error )
{
    if (!data || std::memchr( data, 0, size ))
    {
        error = "Original tree wind input is absent or contains NUL";
        return false;
    }
    StockTreeWindParameters next;
    unsigned fields = 0;
    const std::string source( data, size );
    std::size_t begin = 0;
    const char * const names[] = { "NumWindMatricesAndLeafAngles", "WindResponseAndLimit", "MaxBendAngle",
        "BranchExponent", "LeafExponent", "GustStrengthMinMax", "GustDurationMinMax", "GustFrequency",
        "BranchOscillationX_LaLsHaHs", "BranchOscillationY_LaLsHaHs", "LeafRocking_LaLsHaHs", "LeafRustling_LaLsHaHs" };
    while (begin < source.size() && fields != 0xfff)
    {
        const std::size_t end = source.find( '\n', begin );
        const std::string line = source.substr( begin, end == std::string::npos ? end : end - begin );
        begin = end == std::string::npos ? source.size() : end + 1;
        bool known = false;
        for (unsigned field = 0; field < 12; ++field)
        {
            const std::size_t length = std::strlen( names[field] );
            if (line.compare( 0, length, names[field] ) != 0) continue;
            known = true;
            const char * value = line.c_str() + length;
            int converted = 0, needed = 1;
            switch (field)
            {
            case 0: converted = std::sscanf( value, "%d %d", &next.matrixCount, &next.leafAngleCount ); needed = 2; break;
            case 1: converted = std::sscanf( value, "%g %g", &next.response, &next.responseLimit ); needed = 2; break;
            case 2: converted = std::sscanf( value, "%g", &next.maximumBend ); break;
            case 3: converted = std::sscanf( value, "%g", &next.branchExponent ); break;
            case 4: converted = std::sscanf( value, "%g", &next.leafExponent ); break;
            case 5: converted = std::sscanf( value, "%g %g", &next.gustMinimum, &next.gustMaximum ); needed = 2; break;
            case 6: converted = std::sscanf( value, "%g %g", &next.gustDurationMinimum, &next.gustDurationMaximum ); needed = 2; break;
            case 7: converted = std::sscanf( value, "%g", &next.gustFrequency ); break;
            default:
                {
                    auto & oscillator = next.oscillation[field - 8];
                    converted = std::sscanf( value, "%g %g %g %g", &oscillator[0], &oscillator[2], &oscillator[1], &oscillator[3] );
                    needed = 4;
                }
            }
            if (converted != needed)
            {
                error = "Original tree wind input has a malformed required field";
                return false;
            }
            fields |= 1u << field;
            break;
        }
        if (!known && line.compare( 0, 24, "GustStrengthFreqDuration" ) == 0)
        {
            float strength, frequency, duration;
            if (std::sscanf( line.c_str() + 24, "%g %g %g", &strength, &frequency, &duration ) != 3)
            {
                error = "Original tree legacy gust input is malformed";
                return false;
            }
            next.gustMinimum = mul( strength, .25f ); next.gustMaximum = mul( strength, 1.25f );
            next.gustFrequency = frequency;
            next.gustDurationMinimum = mul( duration, .5f ); next.gustDurationMaximum = mul( duration, 1.5f );
            fields |= 0xe0;
        }
    }
    if (fields != 0xfff)
    {
        error = "Original tree wind input omits a required field";
        return false;
    }
    if (!validate( next, error )) return false;
    result = next;
    error.clear();
    return true;
}

void StockTreeWind::Response::update( float time, float response, float limit )
{
    const float dt = std::min( time, .03f );
    float delta = mul( sub( target, value ), mul( dt, response ) );
    if (limit != 0.f)
    {
        if (std::fabs( previousDelta ) < std::fabs( delta ) && dt > 0.f && previousTime > 0.f)
        {
            const float acceleration = sub( div( delta, dt ), div( previousDelta, previousTime ) );
            delta = add( mul( clamp( acceleration, -limit, limit ), dt ), previousDelta );
        }
        previousDelta = delta;
        previousTime = dt;
    }
    value = add( value, delta );
}

bool StockTreeWind::initialise( const StockTreeWindParameters & parameters, std::string & error )
{
    if (!validate( parameters, error )) return false;
    parameters_ = parameters;
    leafAngles_.assign( std::size_t( parameters.leafAngleCount ), {{ 0.f, 0.f, 0.f, 0.f }} );
    initialised_ = true;
    strength_ = 0.f;
    reset();
    error.clear();
    return true;
}

void StockTreeWind::reset()
{
    for (auto & matrix : matrices_) matrix = identity();
    responses_ = {};
    phase_ = {};
    lastTime_ = gustStrength_ = gustEnd_ = 0.f;
}

bool StockTreeWind::setStrengthAndDirection( float strength, const std::array<float, 3> & direction,
    std::string & error )
{
    if (!initialised_ || !std::isfinite( strength ) ||
        !std::isfinite( direction[0] ) || !std::isfinite( direction[1] ) || !std::isfinite( direction[2] ))
    {
        error = "Original tree wind strength/direction is unavailable or nonfinite";
        return false;
    }
    strength_ = clamp( strength, 0.f, 1.f );
    const float lengthSquared = add( add( mul( direction[0], direction[0] ), mul( direction[1], direction[1] ) ), mul( direction[2], direction[2] ) );
    const float length = lengthSquared == 0.f ? 1.f : float( std::sqrt( double( lengthSquared ) ) );
    for (std::size_t i = 0; i < 3; ++i) responses_[i + 1].target = div( direction[i], length );
    error.clear();
    return true;
}

bool StockTreeWind::advance( float time, RandomSource random, void * context, std::string & error )
{
    if (!initialised_ || !std::isfinite( time ))
    {
        error = "Original tree wind time is unavailable or nonfinite";
        return false;
    }
    StockTreeWind next( *this );
    if (!next.step( time, random, context, error )) return false;
    *this = std::move( next );
    error.clear();
    return true;
}

bool StockTreeWind::step( float time, RandomSource random, void * context, std::string & error )
{
    const float dt = sub( time, lastTime_ );
    lastTime_ = time;
    const auto draw = [&]( float & value )
    {
        if (!random) { error = "Original tree wind has no CRT random source"; return false; }
        const std::uint32_t number = random( context );
        if (number > 32767) { error = "Original tree wind random value exceeds the Win32 CRT domain"; return false; }
        value = float( number );
        return true;
    };
    if (gustEnd_ < time)
    {
        gustStrength_ = 0.f;
        float number;
        if (!draw( number )) return false;
        if (div( number, 32767.f ) < mul( parameters_.gustFrequency, dt ))
        {
            if (!draw( number )) return false;
            gustStrength_ = add( div( mul( sub( parameters_.gustMaximum, parameters_.gustMinimum ), number ), 32767.f ), parameters_.gustMinimum );
            if (!draw( number )) return false;
            gustEnd_ = add( add( div( mul( sub( parameters_.gustDurationMaximum, parameters_.gustDurationMinimum ), number ), 32767.f ), time ), parameters_.gustDurationMinimum );
        }
    }
    responses_[0].target = clamp( add( gustStrength_, strength_ ), 0.f, 1.f );
    responses_[0].update( dt, parameters_.response, parameters_.responseLimit );
    responses_[0].value = clamp( responses_[0].value, 0.f, 1.f );
    for (std::size_t i = 1; i < 4; ++i) responses_[i].update( dt, parameters_.response, parameters_.responseLimit );
    const float branch = float( std::pow( double( responses_[0].value ), double( parameters_.branchExponent ) ) );
    const float leaf = float( std::pow( double( responses_[0].value ), double( parameters_.leafExponent ) ) );
    for (std::size_t i = 0; i < 4; ++i)
    {
        const auto & oscillator = parameters_.oscillation[i];
        const float factor = i < 2 ? branch : leaf;
        phase_[i] = add( mul( add( mul( sub( oscillator[3], oscillator[2] ), factor ), oscillator[2] ), dt ), phase_[i] );
    }
    const auto oscillate = [&]( std::size_t oscillatorIndex, std::size_t angle )
    {
        const auto & oscillator = parameters_.oscillation[oscillatorIndex];
        const float factor = oscillatorIndex < 2 ? branch : leaf;
        const float wave = float( std::sin( double( add( float( angle ), phase_[oscillatorIndex] ) ) ) );
        return mul( wave, add( mul( sub( oscillator[1], oscillator[0] ), factor ), oscillator[0] ) );
    };
    const float x = responses_[1].value, y = responses_[2].value, z = responses_[3].value;
    const float square = add( add( mul( x, x ), mul( y, y ) ), mul( z, z ) );
    const float normaliser = div( 1.f, square <= 1.1920928955078125e-5f ? .01f : float( std::sqrt( double( square ) ) ) );
    const float horizontal = sub( 1.f, std::fabs( mul( normaliser, y ) ) );
    // 5e9cd0 initialises the SDK's axis offset to binary32(pi * .5).
    const float angle = sub( float( std::atan2( double( x ), double( z ) ) ), 1.5707963705062866f );
    const float negativeCosine = -float( std::cos( double( angle ) ) );
    const float negativeSine = -float( std::sin( double( angle ) ) );
    const float bend = mul( parameters_.maximumBend, branch );
    for (std::size_t i = 0; i < matrices_.size(); ++i)
        matrices_[i] = multiply( rotation( mul( add( bend, oscillate( 1, i ) ), horizontal ), negativeSine, negativeCosine ),
            rotation( mul( oscillate( 0, i ), horizontal ), -negativeCosine, negativeSine ) );
    for (std::size_t i = 0; i < leafAngles_.size(); ++i)
    {
        leafAngles_[i][0] = mul( oscillate( 2, i ), .017453292f );
        leafAngles_[i][1] = mul( oscillate( 3, i ), .017453292f );
    }
    return true;
}
}
