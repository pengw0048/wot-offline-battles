#include "stock_tree_lod.hpp"
#include "stock_tree_materials.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <initializer_list>

namespace wot
{
namespace
{
// Store each scalar operation as binary32, as the original SSE instructions do.
// In particular, a compiler may otherwise contract the alpha multiply/add.
float add( float a, float b ) { volatile float result = a + b; return result; }
float sub( float a, float b ) { volatile float result = a - b; return result; }
float mul( float a, float b ) { volatile float result = a * b; return result; }
float div( float a, float b ) { volatile float result = a / b; return result; }

float alphaCurve( const StockTreeLodConfiguration & state, float base )
{
    // The original calls the CRT double pow import and rounds its result to
    // float before the remaining MULSS/ADDSS instructions.
    const float power = static_cast<float>( std::pow( double( base ), double( state.exponent ) ) );
    return add( mul( sub( 255.f, state.alphaReference ), power ), state.alphaReference );
}

class SptTail
{
public:
    SptTail( const std::uint8_t * data, std::size_t size, std::size_t start,
        StockTreeLodConfiguration & state, std::string & error ) :
        data_( data ), size_( size ), cursor_( start ), state_( state ), error_( error ) {}
    bool fail( const char * message ) { error_ = message; return false; }
    bool skip( std::size_t bytes )
    {
        if (bytes > size_ - cursor_) return fail( "SPT runtime field is truncated" );
        cursor_ += bytes;
        return true;
    }
    bool word( std::uint32_t & value )
    {
        if (size_ - cursor_ < 4) return fail( "SPT runtime token is truncated" );
        const std::uint8_t * p = data_ + cursor_;
        value = std::uint32_t( p[0] ) | (std::uint32_t( p[1] ) << 8) |
            (std::uint32_t( p[2] ) << 16) | (std::uint32_t( p[3] ) << 24);
        cursor_ += 4;
        return true;
    }
    bool scalar( float & value )
    {
        std::uint32_t bits;
        if (!word( bits )) return false;
        std::memcpy( &value, &bits, 4 );
        return true;
    }
    bool integer( std::int32_t & value )
    {
        std::uint32_t bits;
        if (!word( bits )) return false;
        std::memcpy( &value, &bits, 4 );
        return true;
    }
    bool expect( std::uint32_t expected )
    {
        std::uint32_t token;
        return word( token ) && (token == expected || fail( "SPT runtime structural token is invalid" ));
    }
    bool string()
    {
        std::uint32_t count;
        return word( count ) && skip( count );
    }
    static bool has( std::initializer_list<std::uint32_t> fields, std::uint32_t token )
    {
        return std::find( fields.begin(), fields.end(), token ) != fields.end();
    }
    bool fields( std::uint32_t end, std::initializer_list<std::uint32_t> words,
        std::initializer_list<std::uint32_t> bytes = {},
        std::initializer_list<std::uint32_t> strings = {},
        std::initializer_list<std::uint32_t> vectors = {} )
    {
        std::uint32_t token;
        while (word( token ))
        {
            if (token == end) return true;
            if (has( words, token )) { if (!skip( 4 )) return false; }
            else if (has( bytes, token )) { if (!skip( 1 )) return false; }
            else if (has( strings, token )) { if (!string()) return false; }
            else if (has( vectors, token )) { if (!skip( 12 )) return false; }
            else return unexpected( token );
        }
        return false;
    }
    bool unexpected( std::uint32_t token )
    {
        error_ = "SPT runtime section contains unexpected token " + std::to_string( token );
        return false;
    }
    bool lod()
    {
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 9001) return true;
            if (token == 9002) { if (!integer( state_.leafMethod )) return false; }
            else if (token == 9003) { if (!scalar( state_.transition )) return false; }
            else if (token == 9004) { if (!scalar( state_.exponent )) return false; }
            else if (token == 9009) { if (!skip( 4 )) return false; }
            else if (token == 9005)
            {
                while (word( token ))
                {
                    if (token == 9006) break;
                    if (token == 9007) { if (!integer( state_.partCounts[0] )) return false; }
                    else if (token == 9011) { if (!integer( state_.partCounts[2] )) return false; }
                    else if (has( { 9008, 9010, 9012, 9013, 9014 }, token ))
                    { if (!skip( 4 )) return false; }
                    else return unexpected( token );
                }
                if (token != 9006) return false;
            }
            else return unexpected( token );
        }
        return false;
    }
    bool textureCoordinates()
    {
        std::uint32_t token, count;
        while (word( token ))
        {
            if (token == 10001) return true;
            if (token >= 10002 && token <= 10004)
            {
                if (!word( count ) || count > (size_ - cursor_) / 32 || !skip( std::size_t( count ) * 32 ))
                    return fail( "SPT texture-coordinate count exceeds its input" );
            }
            else if (token == 10005) { if (!string()) return false; }
            else if (token == 10006 || token == 10007) { if (!skip( 1 )) return false; }
            else return unexpected( token );
        }
        return false;
    }
    bool fronds()
    {
        std::uint32_t token, count;
        while (word( token ))
        {
            if (token == 13001) return true;
            if (token == 13009) { if (!integer( state_.partCounts[1] )) return false; }
            else if (token == 13005) { if (!string()) return false; }
            else if (token == 13007) { if (!skip( 1 )) return false; }
            else if (token == 13008)
            {
                if (!word( count ) || count > (size_ - cursor_) / 8)
                    return fail( "SPT frond texture count exceeds its input" );
                for (std::uint32_t i = 0; i < count; ++i)
                    if (!expect( 14000 ) || !fields( 14001, { 14003, 14004, 14005, 14006 }, {}, { 14002 } )) return false;
            }
            else if (has( { 13002, 13003, 13004, 13006, 13010, 13011, 13012, 13013, 14007, 14008 }, token ))
            { if (!skip( 4 )) return false; }
            else return unexpected( token );
        }
        return false;
    }
    bool repeated( std::uint32_t end, std::uint32_t begin, std::uint32_t close,
        std::initializer_list<std::uint32_t> words, std::initializer_list<std::uint32_t> bytes = {},
        std::initializer_list<std::uint32_t> strings = {} )
    {
        std::uint32_t token;
        while (word( token ))
        {
            if (token == end) return true;
            if (token != begin) return unexpected( token );
            if (!fields( close, words, bytes, strings )) return false;
        }
        return false;
    }
    bool sequence( std::uint32_t end, std::uint32_t first, std::uint32_t last,
        std::uint32_t firstWidth = 4 )
    {
        std::uint32_t token;
        while (word( token ))
        {
            if (token == end) return true;
            if (token != first || !skip( firstWidth )) return unexpected( token );
            for (std::uint32_t field = first + 1; field <= last; ++field)
                if (!expect( field ) || !skip( 4 )) return false;
        }
        return false;
    }
    bool mapCollection()
    {
        return expect( 70000 ) && fields( 70001, {}, {}, { 70002, 70003, 70004, 70005, 70006, 70007, 70008 } );
    }
    bool mapBank()
    {
        std::uint32_t token, count;
        while (word( token ))
        {
            if (token == 60001) return true;
            if (token == 60002 || token == 60005 || token == 60009)
            { if (!mapCollection()) return false; }
            else if (token == 60003 || token == 60004)
            {
                if (!expect( token == 60003 ? 60007 : 60008 ) || !word( count ) || count > (size_ - cursor_) / 8)
                    return fail( "SPT map-bank count exceeds its input" );
                for (std::uint32_t i = 0; i < count; ++i) if (!mapCollection()) return false;
            }
            else if (token == 60006) { if (!string()) return false; }
            else return unexpected( token );
        }
        return false;
    }
    bool mesh()
    {
        std::uint32_t token;
        while (word( token ))
        {
            if (token == 71015) return true;
            if (token == 71001) { if (!skip( 4 )) return false; }
            else if (token == 71002)
            {
                while (word( token ))
                {
                    if (token == 71014) break;
                    if (token == 71003) { if (!string()) return false; }
                    else if (has( { 71004, 71012, 71013 }, token )) { if (!skip( 4 )) return false; }
                    else if (token == 71005)
                    {
                        // A vertex contains four vector3 fields and one UV pair.
                        while (word( token ))
                        {
                            if (token == 71011) break;
                            if (token >= 71006 && token <= 71009) { if (!skip( 12 )) return false; }
                            else if (token == 71010) { if (!skip( 8 )) return false; }
                            else return unexpected( token );
                        }
                        if (token != 71011) return false;
                    }
                    else return unexpected( token );
                }
                if (token != 71014) return false;
            }
            else return unexpected( token );
        }
        return false;
    }
    bool read()
    {
        std::uint32_t token;
        while (cursor_ < size_ && word( token ))
        {
            switch (token)
            {
            case 9000: if (!lod()) return false; break;
            case 11000: if (!fields( 11001, { 11002 } )) return false; break;
            case 10000: if (!textureCoordinates()) return false; break;
            case 12000:
                while (word( token ) && token != 12001)
                {
                    if (token < 12002 || token > 12004) return unexpected( token );
                    if (!skip( 4 * std::size_t( token - 11998 ) )) return false;
                }
                if (token != 12001) return false;
                break;
            case 13000: if (!fronds()) return false; break;
            case 15000: if (!sequence( 15001, 15002, 15003, 1 )) return false; break;
            case 16000: if (!sequence( 16001, 16002, 16012 )) return false; break;
            case 16013: if (!skip( 4 )) return false; break;
            case 16014: if (!scalar( state_.billboardTransition )) return false; break;
            case 18000: if (!fields( 18001, {}, {}, { 18005 }, { 18002, 18003, 18004 } )) return false; break;
            case 19000: if (!fields( 19001, {}, {}, { 19002 } )) return false; break;
            case 20000:
                while (word( token ) && token != 20001)
                {
                    if (token == 20002) { if (!string()) return false; }
                    else if (token == 20003 || token == 20004) { if (!skip( 1 )) return false; }
                    else if (token == 20005) { if (!skip( 32 )) return false; }
                    else return unexpected( token );
                }
                if (token != 20001) return false;
                break;
            case 21000: case 21001: if (!skip( 4 )) return false; break;
            case 22000: if (!skip( 1 )) return false; break;
            case 23000: if (!sequence( 23001, 23002, 23003 )) return false; break;
            case 24000: if (!expect( 24002 ) || !skip( 4 ) || !expect( 24003 ) || !skip( 4 ) || !expect( 24001 )) return false; break;
            case 25000: if (!fields( 25001, { 25002, 25003, 25004, 25005, 25006 }, { 25007 } )) return false; break;
            case 26000:
                if (!repeated( 26001, 26002, 26003,
                    { 26004, 26005, 26006, 26007, 26008, 26010, 26011, 26012, 26015, 26016, 26017, 26021 },
                    { 26009, 26023 }, { 26013, 26014, 26018, 26019, 26020, 26022 } )) return false;
                break;
            case 27000: if (!fields( 27001, { 27003, 27004, 27005, 27006 }, { 27002 } )) return false; break;
            case 28000: if (!fields( 28001, { 28003, 28004 }, { 28002 } )) return false; break;
            case 29000: if (!fields( 29001, { 29002 } )) return false; break;
            case 30000: if (!fields( 30001, { 30002, 30003, 30004, 30005, 30006, 30007, 30008, 30009 } )) return false; break;
            case 40000:
                while (word( token ) && token != 40001)
                {
                    if (has( { 40002, 40003, 40004, 40005 }, token ))
                    { if (!skip( 4 )) return false; }
                    else if (token == 40006)
                    {
                        if (!expect( 1016 ) || !fields( 1017,
                            { 6008, 6009, 6010, 6011, 6012, 6013, 6014 }, { 6015, 6016 },
                            { 6000, 6001, 6002, 6003, 6004, 6005, 6006, 6007, 6017 } )) return false;
                    }
                    else if (token == 40007)
                    {
                        if (!fields( 40008,
                            { 15003, 16002, 16003, 16004, 16005, 16006, 16007, 16008, 16009, 16010, 16011, 16012,
                              23002, 23003, 26004, 26005, 26006, 26007, 26008, 26010, 26011, 26012,
                              26015, 26016, 26017, 26021 },
                            { 15002, 26009, 26023 }, { 26013, 26014, 26018, 26019, 26020, 26022 } )) return false;
                    }
                    else return unexpected( token );
                }
                if (token != 40001) return false;
                break;
            case 50000:
                if (!repeated( 50001, 50002, 50003,
                    { 50004, 50005, 50008, 50010, 50013, 50014, 50015, 50016, 50017 },
                    { 50006, 50007, 50009, 50011, 50012, 50018 } )) return false;
                break;
            case 60000: if (!mapBank()) return false; break;
            case 71000: if (!mesh()) return false; break;
            case 72000:
                if (!repeated( 72004, 7000, 7001, { 72002, 72003, 72005, 72006 }, { 72001 } )) return false;
                break;
            case 73000:
                if (!repeated( 73001, 73002, 73003, { 73004, 73005, 73006 } )) return false;
                break;
            case 74000: if (!fields( 74001, { 74002 } )) return false; break;
            case 75000: if (!fields( 75001, { 75002, 75003, 75005 }, { 75004 } )) return false; break;
            default: return unexpected( token );
            }
        }
        return cursor_ == size_;
    }
private:
    const std::uint8_t * data_;
    std::size_t size_;
    std::size_t cursor_;
    StockTreeLodConfiguration & state_;
    std::string & error_;
};
}

bool selectStockTreeLods( const StockTreeLodConfiguration & state, float level,
    bool drop, StockTreeLodValues & result, std::string & error )
{
    if (!std::isfinite( level ) || level < 0.f || level > 1.f ||
        !std::isfinite( state.transition ) || state.transition < 0.f ||
        !std::isfinite( state.exponent ) || state.exponent <= 0.f ||
        !std::isfinite( state.billboardTransition ) || state.billboardTransition < 0.f ||
        state.billboardTransition >= 1.f || !std::isfinite( state.alphaReference ))
    {
        error = "Original tree LOD state contains an invalid scalar";
        return false;
    }
    for (std::int32_t count : state.partCounts)
    {
        if (count < 0 || count > 6)
        {
            error = "Original tree LOD count exceeds its six draw queues";
            return false;
        }
    }
    StockTreeLodValues next;
    const float remaining = sub( 1.f, level );
    const float leafOverlap = mul( state.transition, 2.f );
    const float fadeSpan = sub( 1.f, state.billboardTransition );
    const float billboardWidth = mul( leafOverlap, fadeSpan );
    if (level <= 0.f)
    {
        next.branch = drop ? -1 : state.partCounts[0] - 1;
        next.frond = drop ? -1 : state.partCounts[1] - 1;
        next.billboardFade = drop ? 1.f : 0.f;
    }
    else if (level >= 1.f)
    {
        next.branch = next.frond = 0;
    }
    else
    {
        next.branch = static_cast<std::int32_t>( mul( float( state.partCounts[0] ), remaining ) );
        next.frond = static_cast<std::int32_t>( mul( float( state.partCounts[1] ), remaining ) );
    }
    if (!(drop && level <= 0.f))
        next.branchAlpha = next.frondAlpha = state.alphaReference;
    if (drop && level > 0.f && level < 1.f && level < billboardWidth)
    {
        next.branchAlpha = next.frondAlpha = alphaCurve( state, sub( 1.f, div( level, billboardWidth ) ) );
        next.billboardFade = std::min( mul( sub( 1.f, div( level, billboardWidth ) ), 1.5f ), 1.f );
    }
    if (level <= 0.f)
    {
        next.leaves[0] = drop ? -1 : state.partCounts[2] - 1;
        next.leafAlpha[0] = drop ? 255.f : state.alphaReference;
    }
    else if (level >= 1.f)
    {
        next.leaves[0] = 0;
        next.leafAlpha[0] = state.alphaReference;
    }
    else if (state.leafMethod == 1)
    {
        // The original Compute derives both plateau lengths even for a single
        // leaf LOD, whose explicit special case is 1 rather than a division.
        const std::int32_t intervals = state.partCounts[2] - 1;
        float plateau = 1.f;
        float billboardPlateau = 1.f;
        if (intervals != 0)
        {
            plateau = div( sub( 1.f, mul( mul( float( intervals ), state.transition ), 2.f ) ), float( intervals ) );
            billboardPlateau = div( sub( 1.f, mul( float( state.partCounts[2] ), leafOverlap ) ), float( intervals ) );
        }
        const float flatWidth = drop ? billboardPlateau : plateau;
        const float width = add( leafOverlap, flatWidth );
        next.leafAlpha[1] = 255.f;
        if (drop && level < billboardWidth)
        {
            next.leafAlpha[0] = alphaCurve( state, sub( 1.f, div( level, billboardWidth ) ) );
            next.leaves[0] = next.leafAlpha[0] == 255.f ? -1 : state.partCounts[2] - 1;
        }
        else
        {
            if (drop) level = sub( level, leafOverlap );
            if (width == 0.f || !std::isfinite( width ))
            {
                error = "Original tree leaf LOD interval is singular";
                return false;
            }
            const float quotient = div( level, width );
            if (!std::isfinite( quotient ) || quotient <= -2147483648.f || quotient >= 2147483648.f)
            {
                error = "Original tree leaf LOD quotient exceeds the native integer domain";
                return false;
            }
            const std::int32_t interval = static_cast<std::int32_t>( quotient );
            const std::int32_t selected = std::max( state.partCounts[2] - interval - 1, std::int32_t( 0 ) );
            next.leaves[0] = selected;
            const float position = sub( level, mul( float( interval ), width ) );
            next.leafAlpha[0] = state.alphaReference;
            if (!(position < flatWidth))
            {
                if (leafOverlap == 0.f)
                {
                    error = "Original tree leaf transition interval is singular";
                    return false;
                }
                const float blend = div( sub( position, flatWidth ), leafOverlap );
                next.leafAlpha[1] = blend <= state.billboardTransition ? state.alphaReference :
                    alphaCurve( state, div( sub( blend, state.billboardTransition ), fadeSpan ) );
                next.leaves[1] = selected;
                next.leaves[0] = selected - 1;
                if (blend < fadeSpan)
                    next.leafAlpha[0] = alphaCurve( state, sub( 1.f, div( blend, fadeSpan ) ) );
            }
        }
    }
    else
    {
        next.leaves[0] = state.leafMethod == 3 ? 0 :
            static_cast<std::int32_t>( mul( float( state.partCounts[2] ), remaining ) );
        next.leafAlpha[0] = state.alphaReference;
    }
    result = next;
    error.clear();
    return true;
}

bool decodeStockTreeLodConfiguration( const std::uint8_t * data, std::size_t size,
    StockTreeLodConfiguration & result, std::string & error )
{
    StockTreeMaterials materials;
    std::size_t consumed = 0;
    if (!decodeStockTreeMaterialPrefix( data, size, materials, consumed, error )) return false;
    StockTreeLodConfiguration next;
    SptTail tail( data, size, consumed, next, error );
    if (!tail.read()) return false;
    // The original full SPT loader performs this conversion after all blocks.
    if (next.leafMethod == 2)
    {
        next.leafMethod = 1;
        next.billboardTransition = .5f;
    }
    result = next;
    error.clear();
    return true;
}
}
