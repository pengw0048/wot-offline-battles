#ifndef WOT_STOCK_TPS_RESOURCES_PORTABLE
#include "pch.hpp"
#endif
#include "stock_tps_resources.hpp"
#include <cstring>
#include <stdexcept>
#include <utility>

namespace wot
{
namespace
{
class Reader
{
public:
    Reader( const void * bytes, std::size_t size ) :
        bytes_( static_cast<const std::uint8_t *>( bytes ) ), size_( size ) {}
    std::size_t position() const { return cursor_; }
    std::size_t remaining() const { return size_ - cursor_; }
    void copy( void * output, std::size_t size )
    {
        if (size > remaining()) throw std::runtime_error( "Truncated tPS resource" );
        if (size) std::memcpy( output, bytes_ + cursor_, size );
        cursor_ += size;
    }
    std::uint32_t word()
    {
        std::uint32_t value;
        copy( &value, sizeof(value) );
        return value;
    }
    void require( bool value, const char * message )
    {
        if (!value) throw std::runtime_error( message );
    }
private:
    const std::uint8_t * bytes_;
    std::size_t size_, cursor_ = 0;
};
template<class Result, std::size_t Size>
Result field( const std::array<std::uint8_t, Size> & bytes, std::size_t offset )
{
    if (offset > Size || sizeof(Result) > Size - offset)
        throw std::out_of_range( "tPS descriptor field" );
    Result value;
    std::memcpy( &value, bytes.data() + offset, sizeof(value) );
    return value;
}
void readCurve( Reader & input, StockTPSCurve & curve, unsigned components )
{
    const std::uint32_t count = input.word();
    input.require( input.word() == 0x50505050u, "Invalid tPS key curve signature" );
    input.require( count <= 2048, "tPS key curve exceeds native capacity" );
    input.require( count <= input.remaining() / ((components + 1) * sizeof(float)),
        "Truncated tPS key curve" );
    curve.components = components;
    curve.times.resize( count );
    curve.values.resize( std::size_t(count) * components );
    input.copy( curve.times.data(), curve.times.size() * sizeof(float) );
    input.copy( curve.values.data(), curve.values.size() * sizeof(float) );
}
StockTPSEmitterResource readEmitter( Reader & input )
{
    const std::size_t start = input.position();
    input.require( input.word() == 1001, "Invalid tPS emitter type" );
    const std::uint32_t recordSize = input.word();
    input.require( input.word() == 1508, "Unsupported #1513 tPS emitter descriptor" );
    StockTPSEmitterResource result;
    input.copy( result.descriptor.data(), result.descriptor.size() );
    for (unsigned index = 0; index < result.curves.size(); ++index)
        readCurve( input, result.curves[index], index == 6 ? 4 : 1 );
    input.require( input.position() - start == recordSize, "Invalid tPS emitter record size" );
    return result;
}
StockTPSEffectResource readEffect( Reader & input )
{
    const std::size_t start = input.position();
    input.require( input.word() == 1003, "Invalid tPS effect type" );
    const std::uint32_t recordSize = input.word();
    input.require( input.word() == 2, "Unsupported #1513 tPS effect version" );
    const std::uint32_t count = input.word();
    input.require( input.word() == 140, "Unsupported #1513 tPS effect descriptor" );
    StockTPSEffectResource result;
    input.copy( result.descriptor.data(), result.descriptor.size() );
    // Even an emitter with empty curves needs its fixed descriptor and headers.
    input.require( count <= input.remaining() / (12 + 1508 + 8 * 8),
        "Truncated tPS emitter list" );
    result.emitters.reserve( count );
    for (std::uint32_t index = 0; index < count; ++index)
        result.emitters.push_back( readEmitter( input ) );
    input.require( input.position() - start == recordSize, "Invalid tPS effect record size" );
    return result;
}
}
bool StockTPSCurve::sample( float time, std::array<float, 4> & output ) const
{
    if (times.empty()) return false;
    std::size_t index = 1;
    for (; index < times.size(); ++index)
    {
        if (time <= times[index])
        {
            const float fraction = (time - times[index - 1]) / (times[index] - times[index - 1]);
            for (unsigned component = 0; component < components; ++component)
            {
                const float previous = values[(index - 1) * components + component];
                output[component] = (values[index * components + component] - previous) * fraction + previous;
            }
            return true;
        }
    }
    for (unsigned component = 0; component < components; ++component)
        output[component] = values[(times.size() - 1) * components + component];
    return true;
}
std::uint32_t StockTPSEmitterResource::word( std::size_t offset ) const
{ return field<std::uint32_t>( descriptor, offset ); }
float StockTPSEmitterResource::scalar( std::size_t offset ) const
{ return field<float>( descriptor, offset ); }
std::uint32_t StockTPSEffectResource::word( std::size_t offset ) const
{ return field<std::uint32_t>( descriptor, offset ); }
float StockTPSEffectResource::scalar( std::size_t offset ) const
{ return field<float>( descriptor, offset ); }

bool StockTPSResource::read( const void * bytes, std::size_t size, std::string & error )
{
    try
    {
        if (!bytes && size) throw std::runtime_error( "Missing tPS resource data" );
        Reader input( bytes, size );
        input.require( input.word() == 1004, "Unsupported #1513 tPS root type" );
        input.require( input.word() == size, "Invalid tPS resource size" );
        input.word(); // Native 1004 reserved DWORD, not a generated version.
        const std::uint32_t count = input.word();
        StockTPSResource loaded;
        input.copy( loaded.descriptor_.data(), loaded.descriptor_.size() );
        input.require( count <= input.remaining() / (20 + 140), "Truncated tPS LOD list" );
        loaded.effects_.reserve( count );
        for (std::uint32_t index = 0; index < count; ++index)
            loaded.effects_.push_back( readEffect( input ) );
        input.require( !input.remaining(), "Trailing tPS resource data" );
        *this = std::move( loaded );
        error.clear();
        return true;
    }
    catch (const std::exception & failure)
    {
        error = failure.what();
        return false;
    }
}
}
