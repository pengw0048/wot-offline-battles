#include "stock_tree_geometry.hpp"
#include <cmath>
#include <cstring>
#include <utility>

namespace wot
{
namespace
{
class Cursor
{
public:
    Cursor( const std::uint8_t * data, std::size_t size, std::string & error ) :
        data_( data ), size_( size ), error_( error ) {}
    std::size_t remaining() const { return size_ - cursor_; }
    bool fail( const char * reason ) { error_ = reason; return false; }
    bool word( std::uint32_t & value )
    {
        if (!data_ || remaining() < 4) return fail( "Cached tree field is truncated" );
        const auto * p = data_ + cursor_;
        value = std::uint32_t( p[0] ) | (std::uint32_t( p[1] ) << 8) |
            (std::uint32_t( p[2] ) << 16) | (std::uint32_t( p[3] ) << 24);
        cursor_ += 4;
        return true;
    }
    bool scalar( float & value )
    {
        std::uint32_t bits;
        if (!word( bits )) return false;
        std::memcpy( &value, &bits, sizeof( value ) );
        return std::isfinite( value ) || fail( "Cached tree bounds or leaf scalars are not finite" );
    }
    bool vertices( std::uint32_t count, std::uint32_t stride, std::vector<std::uint8_t> & output )
    {
        if (count > remaining() / stride) return fail( "Cached tree vertex count exceeds its input" );
        const std::size_t bytes = std::size_t( count ) * stride;
        output.assign( data_ + cursor_, data_ + cursor_ + bytes );
        cursor_ += bytes;
        return true;
    }
    bool path( std::string & output )
    {
        std::uint32_t bytes;
        if (!word( bytes )) return false;
        if (bytes > remaining()) return fail( "Cached tree texture path is truncated" );
        output.assign( reinterpret_cast<const char *>( data_ + cursor_ ), bytes );
        if (output.find( '\0' ) != std::string::npos)
            return fail( "Cached tree texture path contains a NUL byte" );
        cursor_ += bytes;
        return true;
    }
private:
    const std::uint8_t * data_;
    std::size_t size_;
    std::string & error_;
    std::size_t cursor_ = 0;
};
}

bool decodeStockTreeGeometry( const std::uint8_t * data, std::size_t size,
    StockTreeGeometry & geometry, std::string & error )
{
    Cursor cursor( data, size, error );
    std::uint32_t version;
    if (!cursor.word( version )) return false;
    if (version != 106) return cursor.fail( "Cached tree version is not the original 106 format" );
    StockTreeGeometry next = {};
    for (auto & value : next.minimumBounds) if (!cursor.scalar( value )) return false;
    for (auto & value : next.maximumBounds) if (!cursor.scalar( value )) return false;
    for (unsigned i = 0; i < 3; ++i)
        if (next.minimumBounds[i] > next.maximumBounds[i]) return cursor.fail( "Cached tree bounds are inverted" );
    if (!cursor.scalar( next.leafRockScalar ) || !cursor.scalar( next.leafRustleScalar )) return false;
    const std::uint32_t strides[] = { 52, 52, 88, 68 };
    for (unsigned part = 0; part < 4; ++part)
    {
        auto & output = next.parts[part];
        output.vertexStride = strides[part];
        if (!cursor.word( output.vertexCount ) ||
            !cursor.vertices( output.vertexCount, output.vertexStride, output.vertices )) return false;
        std::uint32_t lods;
        if (!cursor.word( lods )) return false;
        if (lods > cursor.remaining() / 4) return cursor.fail( "Cached tree LOD count exceeds its input" );
        output.lodIndices.resize( lods );
        for (auto & lod : output.lodIndices)
        {
            std::uint32_t indices;
            if (!cursor.word( indices )) return false;
            if (indices > cursor.remaining() / 4) return cursor.fail( "Cached tree index count exceeds its input" );
            lod.resize( indices );
            for (auto & index : lod)
            {
                if (!cursor.word( index )) return false;
                if (index >= output.vertexCount) return cursor.fail( "Cached tree index is outside its vertex slot" );
            }
        }
        if (!cursor.path( output.diffuseMap ) || !cursor.path( output.normalMap )) return false;
    }
    if (cursor.remaining()) return cursor.fail( "Cached tree has trailing data" );
    geometry = std::move( next );
    error.clear();
    return true;
}
}
