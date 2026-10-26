#pragma once

#include <array>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>

namespace wot {

// The one presentation state owned by the stock Svarog component. Field groups
// follow #1513 DetailedEngineState (0x00795190 / 0x007958c0), not a second
// authoritative engine/gearbox simulation.
struct DetailedEngineStateData {
    float forward_limit = 0.f, backward_limit = 0.f;
    float rpm_min = 0.f, rpm_max = 0.f, total_speed_limit = 0.f;
    float rotation_limit = 0.f;
    bool player = false;
    float rotation_speed = 0.f, relative_rotation_speed = 0.f;
    std::int32_t previous_gear = 0, estimated_gear = 0;
    std::int32_t gear2 = 0, gear3 = 0;
    float physics_rpm = 0.f, physics_rpm_absolute = 0.f;
    std::int32_t physics_gear = 0;
    float pitch = 0.f, speed = 0.f, smoothed_speed = 0.f, shift_hold = 0.f;
    float rpm = 0.f, relative_rpm = 0.f;
    std::int32_t gear = 0, mode = 0;
    bool starting = false, gear_up = false;
    float physics_load = 0.f, relative_speed = 0.f;
    float max_climb_angle = .34906587f;
    std::int32_t engine_load = 0, engine_state = 0;
    float roughness = 0.f, gear_rpm_factor = .2f;
};

struct DetailedEngineStateInputs {
    std::function<bool(float&)> speed, rotation_speed, physics_rpm;
    std::function<bool(std::int32_t&)> physics_gear;
    std::function<bool(std::array<float, 3>&)> forward;
    std::function<bool()> on_gear_up;
};

inline float detailed_engine_clamp(float value, float minimum, float maximum) {
    float output = minimum;
    if (minimum <= value) {
        output = value;
        if (maximum <= value) output = maximum;
    }
    return output;
}

inline std::int32_t detailed_engine_integer(double value) {
    // MSVC x86's original conversion helper returns the indefinite integer for
    // NaN and overflow. Avoid an undefined host-language float-to-int cast.
    if (!(value >= -2147483648. && value < 2147483648.))
        return std::numeric_limits<std::int32_t>::min();
    return static_cast<std::int32_t>(value);
}

inline bool refresh_detailed_engine_state(DetailedEngineStateData& state,
    const DetailedEngineStateInputs& input, float step) {
    if (!input.speed || !input.speed(state.speed)) return false;
    state.relative_speed = detailed_engine_clamp(state.speed /
        (state.speed <= 0.f ? state.backward_limit : state.forward_limit), -1.f, 1.f);
    state.physics_rpm = 0.f;
    if (state.physics_gear && input.physics_rpm && !input.physics_rpm(state.physics_rpm))
        return false;
    state.relative_rpm = state.physics_rpm;
    state.physics_rpm_absolute = (state.rpm_max - state.rpm_min) * state.physics_rpm + state.rpm_min;
    state.rpm = state.physics_rpm_absolute;
    state.physics_gear = 0;
    if (input.physics_gear && !input.physics_gear(state.physics_gear)) return false;

    if (state.player) {
        const std::int32_t old = state.gear;
        state.gear = state.physics_gear;
        state.gear_up = old < state.physics_gear;
    } else {
        float speed;
        if (!input.speed(speed)) return false;
        const float gear_step = state.total_speed_limit * .33333334f;
        state.smoothed_speed = (speed - state.smoothed_speed) * .5f * step + state.smoothed_speed;
        const float quantized = static_cast<float>(std::floor(std::fabs(state.smoothed_speed) * 50.f));
        const auto gear = detailed_engine_integer(std::ceil(
            static_cast<double>(quantized * .02f / gear_step)));
        state.gear = gear;
        const auto old = state.estimated_gear;
        if (old != gear) state.previous_gear = old;
        state.gear_up = old != gear && gear > old && state.engine_load >= 2;
        state.gear2 = gear == 2 && state.previous_gear < 2 ? 100 : 0;
        state.gear3 = gear == 3 && state.previous_gear <= 2 ? 100 : 0;
        state.estimated_gear = gear;
        if (!gear || state.engine_load < 2) state.relative_rpm = 0.f;
        else {
            const float floor = state.gear_rpm_factor * static_cast<float>(gear);
            state.relative_rpm = std::fabs((state.smoothed_speed - float(gear) * gear_step) /
                state.total_speed_limit + 1.f) * (1.f - floor) + floor;
        }
        state.rpm = state.relative_rpm * 100.f;
    }
    if (state.gear_up) {
        if (input.on_gear_up && !input.on_gear_up()) return false;
        state.shift_hold = 1.f;
    } else state.shift_hold = state.shift_hold <= step ? 0.f : state.shift_hold - step;

    if (!input.rotation_speed || !input.rotation_speed(state.rotation_speed)) return false;
    state.engine_load = state.mode == 3 ? 2 : state.mode;
    state.relative_rotation_speed = state.rotation_speed / state.rotation_limit;
    std::array<float, 3> forward;
    if (!input.forward || !input.forward(forward)) return false;
    const float length = std::sqrt(forward[0] * forward[0] + forward[1] * forward[1] + forward[2] * forward[2]);
    const float y = length == 0.f ? 0.f : forward[1] / length;
    const float old_pitch = state.pitch;
    state.pitch = -std::asin(detailed_engine_clamp(y, -1.f, 1.f));
    state.roughness = (state.pitch - old_pitch) * (-1.f / step);
    if (state.mode < 2) state.physics_load = float(state.mode) - 1.f;
    else {
        const float relative = state.relative_speed;
        const float absolute = std::fabs(relative);
        if (absolute < .01f || state.shift_hold != 0.f) state.physics_load = 1.f;
        else {
            float slope = std::fabs(state.pitch) / state.max_climb_angle;
            if (relative * state.pitch > 0.f) slope = -slope;
            const float speed_scale = (relative <= 0.f ? 1.1111112f : 3.030303f) * absolute;
            const float inverse_speed = detailed_engine_clamp(1.f /
                detailed_engine_clamp(speed_scale, .01f, 1.f), 0.f, 2.f);
            const float load = detailed_engine_clamp(inverse_speed * (slope + 1.f) * .5f, 0.f, 1.f);
            const float roughness = state.roughness <= 0.f ? 0.f : state.roughness;
            const float overspeed = detailed_engine_clamp(absolute - .9f, 0.f, 1.f);
            const float combined = detailed_engine_clamp(overspeed + roughness + load, 0.f, 1.f);
            state.physics_load = std::fabs(state.relative_rotation_speed);
            if (state.physics_load <= combined) state.physics_load = combined;
        }
    }
    return true;
}

} // namespace wot
