#include "wot/stock_gun_history.hpp"

#include <cmath>

namespace wot {
namespace {

float normalise(float value) {
    // #1513 00648be0 uses these single-precision constants and reduction.
    constexpr float pi = 3.1415927410125732f;
    constexpr float three_pi = 9.4247779846191406f;
    constexpr float two_pi = 6.2831854820251465f;
    if (value > pi) {
        if (value <= three_pi) return value - two_pi;
    } else if (value <= -pi) {
        if (value > -three_pi) return value + two_pi;
    } else {
        return value;
    }
    const float scaled = value * 0.15915493667125702f + 0.5f;
    return value - std::floor(scaled) * two_pi;
}

float interpolate(float first, float second, double fraction) {
    const float delta = normalise(second - first);
    const float step = normalise(static_cast<float>(static_cast<double>(delta) * fraction));
    return normalise(step + first);
}

} // namespace

void StockGunAngleHistory::sync(float yaw, float pitch, double time) {
    while (!samples_.empty() && time - samples_.back().time < 0.010000000000000002)
        samples_.pop_back();
    samples_.push_back({normalise(yaw), normalise(pitch), time});
    if (samples_.size() > 24)
        samples_.erase(samples_.begin(), samples_.end() - 8);
}

bool StockGunAngleHistory::sample(double render_time, double previous_output_time,
                                 float& yaw, float& pitch) const {
    if (samples_.empty()) return false;
    for (std::size_t end = samples_.size(); end > 0; --end) {
        const std::size_t index = end - 1;
        const StockGunAngleSample& first = samples_[index];
        // The original comparison is strict; an exact sample timestamp uses
        // the preceding interval, including its long-gap presentation window.
        if (!(first.time < render_time)) continue;
        if (end == samples_.size()) {
            yaw = first.yaw;
            pitch = first.pitch;
            return true;
        }
        const StockGunAngleSample& second = samples_[end];
        const double interval = second.time - first.time;
        double fraction;
        if (interval >= 0.12) {
            const double remaining = second.time - previous_output_time;
            if (render_time <= previous_output_time || remaining <= 0.09000000000000001 ||
                remaining >= 0.12)
                fraction = (render_time - (second.time - 0.1)) * 10.0;
            else
                fraction = (render_time - previous_output_time) / remaining;
        } else {
            fraction = (render_time - first.time) / interval;
        }
        if (fraction < 0.0) fraction = 0.0;
        if (fraction >= 1.0) fraction = 1.0;
        yaw = interpolate(first.yaw, second.yaw, fraction);
        pitch = interpolate(first.pitch, second.pitch, fraction);
        return true;
    }
    yaw = samples_.front().yaw;
    pitch = samples_.front().pitch;
    return true;
}

} // namespace wot
