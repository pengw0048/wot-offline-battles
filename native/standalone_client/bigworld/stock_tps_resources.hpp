#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace wot
{
// #1513 faf0b0/faeee0: six scalar curves, one RGBA curve, then one scalar.
struct StockTPSCurve
{
    unsigned components = 1;
    std::vector<float> times;
    std::vector<float> values;
    // f98a30/fadfc0/fadf20 extrapolate before the first key, then retain
    // the last value after the last key. Empty curves provide no value.
    bool sample( float time, std::array<float, 4> & output ) const;
};
struct StockTPSEmitterResource
{
    // faf0b0 copies the fixed descriptor before rebuilding the eight curves.
    // Serialized pointer fields remain opaque bytes and are never dereferenced.
    std::array<std::uint8_t, 1508> descriptor{};
    std::array<StockTPSCurve, 8> curves;
    std::uint32_t word( std::size_t offset ) const;
    float scalar( std::size_t offset ) const;
};
struct StockTPSEffectResource
{
    // faefc0: the 140-byte descriptor belongs to this LOD, not the emitter.
    std::array<std::uint8_t, 140> descriptor{};
    std::vector<StockTPSEmitterResource> emitters;
    std::uint32_t word( std::size_t offset ) const;
    float scalar( std::size_t offset ) const;
};
class StockTPSResource
{
public:
    // faf730 consumes the 80-byte 1004 header and its sequential 1003 LODs.
    // All 3,435 original forward/deferred PFX resources use this exact format.
    bool read( const void * bytes, std::size_t size, std::string & error );
    const std::array<std::uint8_t, 64> & descriptor() const { return descriptor_; }
    const std::vector<StockTPSEffectResource> & effects() const { return effects_; }
private:
    std::array<std::uint8_t, 64> descriptor_{};
    std::vector<StockTPSEffectResource> effects_;
};
}
