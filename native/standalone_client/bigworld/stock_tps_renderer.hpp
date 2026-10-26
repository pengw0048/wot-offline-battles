#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace wot
{
// Exact lit_particles stream contract consumed by shaders/wg_particles/ps.fx.
struct StockTPSCommonVertex
{
    std::array<float, 3> position;
    std::array<float, 4> colour;
    std::array<float, 4> parameters;
    std::array<float, 3> direction;
    std::array<std::uint16_t, 12> shading;
    std::array<float, 4> timing;
};
struct StockTPSOffsetVertex
{
    std::array<float, 4> offset;
    std::array<float, 4> coordinates;
};
static_assert( sizeof(StockTPSCommonVertex) == 96, "Original tPS common stream" );
static_assert( sizeof(StockTPSOffsetVertex) == 32, "Original tPS offset stream" );

struct StockTPSDrawParameters
{
    int quality, transformMode, debugMode, offscreenMode, transparencyMode;
    float aspectRatio, colourMultiplier;
};
class StockTPSDrawResource
{
public:
    virtual ~StockTPSDrawResource() = default;
    virtual bool load( std::string & error ) = 0;
    virtual bool draw( const std::vector<StockTPSCommonVertex> & common,
        const std::vector<StockTPSOffsetVertex> & offsets,
        const std::vector<std::uint16_t> & indices,
        const StockTPSDrawParameters & parameters, std::string & error ) = 0;
};
}
#ifndef WOT_STOCK_TPS_RENDERER_PORTABLE
#include "cstdmf/bw_namespace.hpp"
#include "cstdmf/bw_string.hpp"
BW_BEGIN_NAMESPACE
std::shared_ptr<wot::StockTPSDrawResource> createStockTPSDrawResource( const BW::string & texture );
BW_END_NAMESPACE
#endif
