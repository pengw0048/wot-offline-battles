#pragma once
#include <cstdint>

namespace wot
{
// Original tPS performancer configuration (00aeb920), separate from Pixie.
struct StockTPSPerformanceConfig
{
    float fixedFPS = 50.f;
    float minimumFPS = 3.f;
    float maximumFPS = 15.f;
    float speedMultiplier = 1.f;
    float lodOffset = 0.f;
    float lodMultiplier = 1.f;
};

class StockTPSPerformer
{
public:
    // 00fac190 keeps constructor defaults separate from configuration defaults.
    float normalLodOffset = 0.f, normalLodMultiplier = 1.f;
    float secondaryLodOffset = 0.f, secondaryLodMultiplier = 1.f;
    float baseTimeMultiplier = 1.f;
    float minimumFPS = 5.f, maximumFPS = 28.5f;
    float maximumSpeedMultiplier = 1.f, maximumLodOffset = 0.f, maximumLodMultiplier = 1.f;
    float lodMultiplier = 1.f, lodOffset = 0.f, speedMultiplier = 1.f, density = 1.f;
    float smoothedFrameTime = 29.f;
    bool disabled = true, frozen = false;

    void configure( const StockTPSPerformanceConfig & value )
    {
        // 00fac610. fixedFPS is a native watcher input; the normal runtime
        // branch uses real frame time and does not turn it into a frame cap.
        minimumFPS = value.minimumFPS;
        maximumFPS = value.maximumFPS;
        maximumSpeedMultiplier = value.speedMultiplier;
        maximumLodOffset = value.lodOffset;
        maximumLodMultiplier = value.lodMultiplier;
    }
    void enabled( bool value ) { disabled = !value; }
    void freeze( bool value )
    {
        frozen = value;
        if (value)
        {
            // 00fac690 resets through one real tick before freezing warm-up.
            frozen = false;
            smoothedFrameTime = 0.033333335f;
            tick( 0.033333335f );
            frozen = true;
        }
    }
    void tick( float elapsed )
    {
        if (frozen) return;
        if (disabled)
        {
            speedMultiplier = lodMultiplier = density = 1.f;
            lodOffset = 0.f;
            return;
        }
        smoothedFrameTime = smoothedFrameTime * 0.98333335f + elapsed * 0.016666668f;
        if (!(0.000001f <= smoothedFrameTime)) return;
        const float level = (1.f / smoothedFrameTime - minimumFPS) / (maximumFPS - minimumFPS);
        if (!(0.f <= level))
        {
            speedMultiplier = maximumSpeedMultiplier;
            lodMultiplier = maximumLodMultiplier;
            lodOffset = maximumLodOffset;
            density = 0.01f;
        }
        else if (level > 1.f)
        {
            speedMultiplier = lodMultiplier = density = 1.f;
            lodOffset = 0.f;
        }
        else
        {
            const float reduction = (1.f - level) * (1.f - level);
            speedMultiplier = (maximumSpeedMultiplier - 1.f) * reduction + 1.f;
            lodMultiplier = (maximumLodMultiplier - 1.f) * reduction + 1.f;
            lodOffset = maximumLodOffset * reduction;
            density = level < 0.1f ? 0.1f : level;
        }
    }
    float normalMultiplier( float value ) { normalLodMultiplier = value; return value * lodMultiplier; }
    float normalOffset( float value ) { normalLodOffset = value; return value + lodOffset; }
    float secondaryMultiplier( float value ) { secondaryLodMultiplier = value; return value * lodMultiplier; }
    float secondaryOffset( float value ) { secondaryLodOffset = value; return value + lodOffset; }
    float timeMultiplier( float value ) { baseTimeMultiplier = value; return value * speedMultiplier; }
};
}

#ifndef WOT_STOCK_TPS_QUALITY_PORTABLE
#include "pyscript/script.hpp"
BW_BEGIN_NAMESPACE
bool initialiseStockTPSQuality();
void tickStockTPSQuality( float elapsed );
void finiStockTPSQuality();
void ensureStockTPSPerformer();
wot::StockTPSPerformer * stockTPSPerformer();
std::uint32_t stockTPSInverseRenderGroups();
bool stockTPSGroupDisabled( std::uint32_t inverseGroups );
BW_END_NAMESPACE
#endif
