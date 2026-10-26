#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace wot
{
// Original CSpeedTreeRT state read from the SPT file. Defaults below are the
// original SDK/engine constructors, not counts inferred from resource names.
struct StockTreeLodConfiguration
{
    std::int32_t leafMethod = 1;
    float transition = 0.07f;
    float exponent = 0.7f;
    float billboardTransition = 0.f;
    float alphaReference = 84.f;
    std::array<std::int32_t, 3> partCounts = {{ 6, 4, 4 }};
};

struct StockTreeLodValues
{
    std::int32_t branch = -1;
    float branchAlpha = -1.f;
    std::int32_t frond = -1;
    float frondAlpha = -1.f;
    std::array<std::int32_t, 2> leaves = {{ -1, -1 }};
    std::array<float, 2> leafAlpha = {{ -1.f, -1.f }};
    float billboardFade = 0.f;
};

// Matches #1513 11d94d0..11d957e and 11da360. Invalid state never publishes
// partially selected parts. This is the old SPT renderer, not the SRT owner.
bool selectStockTreeLods( const StockTreeLodConfiguration & configuration,
    float level, bool dropToBillboard, StockTreeLodValues & values, std::string & error );

// Read the original complete SPT structural stream. Material/geometry payloads
// are traversed at their real boundaries, never searched for token byte strings.
bool decodeStockTreeLodConfiguration( const std::uint8_t * data, std::size_t size,
    StockTreeLodConfiguration & configuration, std::string & error );
}
