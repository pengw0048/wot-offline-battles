#include "stock_tree_materials.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#if defined(_M_IX86) || defined(__i386__)
#include <xmmintrin.h>
#endif

namespace wot
{
namespace
{
std::uint32_t readWord( const std::uint8_t * source )
{
    return std::uint32_t( source[0] ) | (std::uint32_t( source[1] ) << 8) |
        (std::uint32_t( source[2] ) << 16) | (std::uint32_t( source[3] ) << 24);
}

class SptCursor
{
public:
    SptCursor( const std::uint8_t * data, std::size_t size, std::string & error ) :
        data_( data ), size_( size ), error_( error ) {}
    std::size_t position() const { return cursor_; }
    std::size_t remaining() const { return size_ - cursor_; }
    bool fail( const char * reason ) { error_ = reason; return false; }
    bool skip( std::size_t bytes )
    {
        if (!data_ || bytes > remaining()) return fail( "SPT base-tree field is truncated" );
        cursor_ += bytes;
        return true;
    }
    bool word( std::uint32_t & value )
    {
        if (!data_ || remaining() < 4) return fail( "SPT base-tree token is truncated" );
        value = readWord( data_ + cursor_ ); cursor_ += 4; return true;
    }
    bool expect( std::uint32_t expected )
    {
        std::uint32_t value;
        return word( value ) && (value == expected || fail( "SPT base-tree structural token is invalid" ));
    }
    bool string()
    {
        std::uint32_t bytes;
        return word( bytes ) && skip( bytes );
    }
    bool header()
    {
        std::uint32_t length;
        if (!expect( 1000 ) || !word( length )) return false;
        if (length != 12 || remaining() < 12 || std::memcmp( data_ + cursor_, "__IdvSpt_02_", 12 ))
            return fail( "SPT version is not the original __IdvSpt_02_ format" );
        return skip( 12 );
    }
    bool branchLevel()
    {
        if (!expect( 1016 )) return false;
        bool nonempty = false;
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 1017) return nonempty || fail( "SPT branch level is empty" );
            if ((token >= 6000 && token <= 6007) || token == 6017)
            {
                if (!string()) return false;
            }
            else if (token >= 6008 && token <= 6014)
            {
                if (!skip( 4 )) return false;
            }
            else if (token == 6015 || token == 6016)
            {
                if (!skip( 1 )) return false;
            }
            else return fail( "SPT contains malformed general branch information" );
            nonempty = true;
        }
        return false;
    }
    bool branches()
    {
        std::uint32_t count;
        if (!word( count )) return false;
        // 11eef70 treats this count as signed and skips nonpositive counts.
        if (count & 0x80000000u) count = 0;
        if (count > remaining() / 8) return fail( "SPT branch count exceeds its input" );
        for (std::uint32_t i = 0; i < count; ++i) if (!branchLevel()) return false;
        return expect( 1015 );
    }
    bool generalTree()
    {
        bool nonempty = false;
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 1003) return nonempty || fail( "SPT general tree section is empty" );
            if (token == 2000) { if (!string()) return false; }
            else if (token == 1014) { if (!branches()) return false; }
            else if (token == 2002) { if (!skip( 1 )) return false; }
            else if (token >= 2001 && token <= 2007) { if (!skip( 4 )) return false; }
            else return fail( "SPT contains malformed general tree information" );
            nonempty = true;
        }
        return false;
    }
    bool leaf()
    {
        if (!expect( 1007 )) return false;
        bool nonempty = false;
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 1008) return nonempty || fail( "SPT single leaf section is empty" );
            if (token == 4003) { if (!string()) return false; }
            else if (token == 4000) { if (!skip( 1 )) return false; }
            else if (token == 4002 || token == 4007) { if (!skip( 4 )) return false; }
            else if (token == 4001 || (token >= 4004 && token <= 4006)) { if (!skip( 12 )) return false; }
            else return fail( "SPT contains malformed single leaf information" );
            nonempty = true;
        }
        return false;
    }
    bool leafArray()
    {
        std::uint32_t count;
        if (!expect( 1006 ) || !word( count )) return false;
        if (count > remaining() / 8) return fail( "SPT leaf count exceeds its input" );
        for (std::uint32_t i = 0; i < count; ++i) if (!leaf()) return false;
        return expect( 1010 );
    }
    bool generalLeaf()
    {
        bool nonempty = false;
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 1005) return nonempty || fail( "SPT general leaf section is empty" );
            if (token == 1009) { if (!leafArray()) return false; }
            else if (token == 3003 || token == 3006 || token == 3009) { if (!skip( 1 )) return false; }
            else if (token >= 3000 && token <= 3010) { if (!skip( 4 )) return false; }
            else return fail( "SPT contains malformed general leaf information" );
            nonempty = true;
        }
        return false;
    }
    bool wind()
    {
        bool nonempty = false;
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 1012) return nonempty || fail( "SPT general wind section is empty" );
            if (token >= 5000 && token <= 5004) { if (!skip( 12 )) return false; }
            else if (token == 5005) { if (!skip( 4 )) return false; }
            else if (token == 5006) { if (!skip( 1 )) return false; }
            else return fail( "SPT contains malformed general wind information" );
            nonempty = true;
        }
        return false;
    }
    bool baseTree()
    {
        bool nonempty = false;
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 1001) return nonempty || fail( "SPT base-tree section is empty" );
            if (token == 1002) { if (!generalTree()) return false; }
            else if (token == 1004) { if (!generalLeaf()) return false; }
            else if (token == 1011) { if (!wind()) return false; }
            else return fail( "SPT contains malformed base-tree information" );
            nonempty = true;
        }
        return false;
    }
private:
    const std::uint8_t * data_;
    std::size_t size_;
    std::string & error_;
    std::size_t cursor_ = 0;
};

std::uint8_t materialByte( float value )
{
    // b03a40 uses MULSS, CVTTSS2SI, then the integer's low byte. Direct
    // float->uint8 conversion is undefined for values outside [0,256).
#if defined(_M_IX86) || defined(__i386__)
    return static_cast<std::uint8_t>( _mm_cvttss_si32(
        _mm_mul_ss( _mm_set_ss( value ), _mm_set_ss( 255.f ) ) ) );
#else
    volatile float product = value * 255.f;
    if (!std::isfinite( product ) || product < -2147483648.f || product >= 2147483648.f)
        return 0; // CVTTSS2SI's integer-indefinite result is 0x80000000.
    return static_cast<std::uint8_t>( static_cast<std::int32_t>( product ) );
#endif
}
}

bool decodeStockTreeLightingSection( const std::uint8_t * data, std::size_t size,
    StockTreeMaterials & materials, std::size_t & consumed, std::string & error )
{
    if (!data || size < 4 || readWord( data ) != 8000)
    {
        error = "SPT lighting section must begin at token 8000";
        return false;
    }
    StockTreeMaterials next = {};
    unsigned fields = 0;
    std::size_t cursor = 4;
    while (cursor <= size && size - cursor >= 4)
    {
        const std::uint32_t token = readWord( data + cursor );
        cursor += 4;
        if (token == 8001)
        {
            if (fields != 255)
            {
                error = "SPT lighting section needs unsupported SDK defaults";
                return false;
            }
            materials = next;
            consumed = cursor;
            error.clear();
            return true;
        }
        if (token < 8002 || token > 8009)
        {
            error = "SPT contains malformed lighting information";
            return false;
        }
        const unsigned field = token - 8002;
        const bool material = token == 8003 || token == 8005 || token == 8009;
        const std::size_t bytes = material ? 52 : 4;
        if (size - cursor < bytes)
        {
            error = "SPT lighting field is truncated";
            return false;
        }
        if (material)
        {
            auto & output = token == 8003 ? next.branch : token == 8005 ? next.leaf : next.frond;
            for (unsigned i = 0; i < 13; ++i)
            {
                const std::uint32_t bits = readWord( data + cursor + i * 4 );
                std::memcpy( &output[i], &bits, sizeof( bits ) );
            }
        }
        else if (token == 8006)
        {
            const std::uint32_t bits = readWord( data + cursor );
            std::memcpy( &next.leafLightingAdjustment, &bits, sizeof( bits ) );
        }
        else
        {
            const unsigned index = token == 8002 ? 0 : token == 8004 ? 1 : token == 8007 ? 2 : 3;
            const std::uint32_t bits = readWord( data + cursor );
            std::memcpy( &next.lightingStyles[index], &bits, sizeof( bits ) );
        }
        cursor += bytes;
        fields |= 1u << field;
    }
    error = "SPT lighting section has no closing token 8001";
    return false;
}

bool decodeStockTreeMaterialPrefix( const std::uint8_t * data, std::size_t size,
    StockTreeMaterials & materials, std::size_t & consumed, std::string & error )
{
    // 11eed30 dispatches the versioned base sections; these readers validate
    // lengths and structural tokens without building procedural geometry.
    SptCursor cursor( data, size, error );
    if (!cursor.header() || !cursor.baseTree()) return false;
    const std::size_t lighting = cursor.position();
    std::uint32_t token;
    if (!cursor.word( token )) return false;
    if (token == 7000)
        return cursor.fail( "SPT precomputed leaf LOD before lighting is not supported" );
    if (token != 8000)
        return cursor.fail( "SPT lighting is not at the validated base-tree boundary" );
    std::size_t used;
    if (!decodeStockTreeLightingSection( data + lighting, size - lighting, materials, used, error )) return false;
    consumed = lighting + used;
    return true;
}

std::array<std::uint8_t, 32> packStockTreeMaterials( const StockTreeMaterials & materials )
{
    std::array<std::uint8_t, 32> packed;
    const std::array<float, 13> * const parts[] = {
        &materials.branch, &materials.frond, &materials.leaf, &materials.leaf };
    for (unsigned part = 0; part < 4; ++part)
    {
        const auto & source = *parts[part];
        for (unsigned channel = 0; channel < 3; ++channel)
        {
            packed[part * 8 + channel] = materialByte( source[channel] );
            packed[part * 8 + 4 + channel] = materialByte( source[3 + channel] );
        }
        packed[part * 8 + 3] = part > 1 ? materialByte( materials.leafLightingAdjustment ) : 0;
        packed[part * 8 + 7] = 0;
    }
    return packed;
}

void writeStockTreeMaterialAtlasSlot( const StockTreeMaterials & materials,
    std::uint32_t handle, std::array<std::uint8_t, 1024> & atlas )
{
    const auto packed = packStockTreeMaterials( materials );
    std::copy( packed.begin(), packed.end(), atlas.begin() + std::min( handle, 31u ) * 32 );
}
}
