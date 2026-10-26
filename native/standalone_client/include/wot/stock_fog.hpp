#pragma once

#include <algorithm>
#include <array>

namespace wot {

// #1513 0x00a559e0 / 0x00a55b60. These are authored native defaults,
// including fields which the forward reader deliberately does not replace.
struct StockFogSettings {
    using Group = std::array<float, 41>;
    static Group defaults(bool deferred) {
        const float near_low = deferred ? 200.0f : 0.0f;
        const float far_low = deferred ? 500.0f : 300.0f;
        const std::array<float, 4> color = deferred
            ? std::array<float, 4>{0.25f, 0.34f, 0.49f, 1.0f}
            : std::array<float, 4>{1.0f, 0.8f, 0.6f, 1.0f};
        Group value{near_low, 200, 0, 20, far_low, 500, 20, 200,
            0, 20, 200, 1, -500, -500, 500, 500, -480, -480, 480, 480};
        std::copy(color.begin(), color.end(), value.begin() + 20);
        std::copy(color.begin(), color.end(), value.begin() + 24);
        const std::array<float, 4> sun{1.0f, 0.8f, 0.6f, 1.0f};
        std::copy(sun.begin(), sun.end(), value.begin() + 28);
        std::copy(sun.begin(), sun.end(), value.begin() + 32);
        value[36] = value[37] = value[39] = value[40] = 1.0f;
        value[38] = 90.0f;
        return value;
    }
    Group deferred = defaults(true);
    Group forward = defaults(false);
};

// #1513 0x009afe20 chooses the group and collapses height-dependent fields.
// Forward means the original forward-rendering flag, not a guessed fog quality.
inline StockFogSettings::Group stock_fog_selected(const StockFogSettings& settings, bool forward) {
    auto value = forward ? settings.forward : settings.deferred;
    if (forward) value[23] = value[27] = 1.0f;
    if (value[37] == 0.0f) {
        std::copy_n(value.begin() + 20, 4, value.begin() + 24);
        if (!forward) std::copy_n(value.begin() + 28, 4, value.begin() + 32);
        value[1] = value[0];
        value[5] = value[4];
        value[23] = value[27];
    }
    return value;
}

// #1513 0x009b5da0. Output is the original nine-float4 shader ABI, 144 bytes.
// Color alpha is intensity in settings and a separate shader control on output.
inline std::array<float, 36> stock_fog_parameters(const StockFogSettings::Group& source) {
    constexpr float epsilon = 0.0001f; // Exact binary32 0x38d1b717.
    std::array<float, 36> value{};
    const std::array<float, 4> start_adjust{0, 0, epsilon, 0};
    const std::array<float, 4> end_adjust{epsilon, epsilon, 0, epsilon};
    const std::array<float, 4> inner_adjust{epsilon, epsilon, -epsilon, -epsilon};
    for (unsigned i = 0; i < 4; ++i) {
        value[i] = source[i] + start_adjust[i];
        const float end = source[4 + i] + end_adjust[i];
        value[4 + i] = 1.0f / (end - value[i]);
        value[8 + i] = source[8 + i];
        value[16 + i] = source[16 + i] + inner_adjust[i];
        value[12 + i] = 1.0f / (source[12 + i] - value[16 + i]);
        for (unsigned color = 20; color < 36; color += 4)
            value[color + i] = source[color + i] * source[color + 3];
    }
    value[23] = source[36];
    value[27] = source[40];
    value[31] = source[39];
    value[35] = 360.0f / source[38];
    return value;
}

} // namespace wot
