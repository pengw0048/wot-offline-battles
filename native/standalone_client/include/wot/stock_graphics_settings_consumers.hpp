#pragma once

namespace wot
{
// Exact #1513 WGBinoculars defaults and 006f8330/006f8430 projection.
struct StockBinocularsState
{
    bool enabled = false;
    bool lens_effects = false;
    float blue_offset = 6.f;
    float green_offset = 3.f;
    float aberration_radius = .5f;
    float distortion_amount = -.003f;
    float center_x = 0.f;
    float center_y = 0.f;

    void setMaskCenter(float x, float y)
    {
        if (x <= 1.f && x >= -1.f && y <= 1.f && y >= -1.f)
        {
            center_x = x;
            center_y = -y;
        }
    }
    void setParams(float green, float blue, float radius, float distortion)
    {
        green_offset = green;
        blue_offset = blue;
        aberration_radius = radius;
        distortion_amount = distortion;
    }
};

inline bool stockSniperFloraVisible(bool grass_enabled, bool binoculars_enabled)
{
    // 00a3aad0 gates drawing; population/update and FLORA_QUALITY stay separate.
    return grass_enabled || !binoculars_enabled;
}
}
