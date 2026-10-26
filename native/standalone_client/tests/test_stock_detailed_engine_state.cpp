#include "../bigworld/stock_detailed_engine_state.hpp"

#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cmath>
#include <iostream>
#include <string>

namespace {
bool near(float left, float right) { return std::fabs(left - right) < .00002f; }
wot::DetailedEngineStateData configured() {
    wot::DetailedEngineStateData value;
    value.forward_limit = 20.f; value.backward_limit = 6.f;
    value.total_speed_limit = 26.f;
    value.rpm_min = 600.f; value.rpm_max = 2400.f;
    value.rotation_limit = .3f;
    value.mode = 2; value.engine_load = 2;
    return value;
}
struct Fixture {
    float first = 0.f, second = 0.f, rpm = .75f, rotation = 0.f;
    std::int32_t gear = 2;
    std::array<float, 3> forward = {{0.f, 0.f, 1.f}};
    unsigned speedCalls = 0, rpmCalls = 0, shiftCalls = 0;
    std::string order;
    wot::DetailedEngineStateInputs inputs() {
        wot::DetailedEngineStateInputs value;
        value.speed = [this](float& out) {
            order += 's'; out = speedCalls++ ? second : first; return true;
        };
        value.rotation_speed = [this](float& out) { order += 'r'; out = rotation; return true; };
        value.physics_rpm = [this](float& out) { order += 'p'; ++rpmCalls; out = rpm; return true; };
        value.physics_gear = [this](std::int32_t& out) { order += 'g'; out = gear; return true; };
        value.forward = [this](std::array<float, 3>& out) { order += 'm'; out = forward; return true; };
        value.on_gear_up = [this]() { order += 'u'; ++shiftCalls; return true; };
        return value;
    }
};
}
int main() {
    // Expected snapshots were obtained by executing original #1513 0x7958c0
    // in x86 Unicorn. They cover the previous-gear RPM gate, two separate NPC
    // speed reads, shift callbacks and the normalized terrain-facing matrix.
    {
        auto state = configured(); state.player = true;
        Fixture fixture;
        assert(wot::refresh_detailed_engine_state(state, fixture.inputs(), .25f));
        assert(state.gear == 2 && state.gear_up && state.rpm == 600.f);
        assert(state.relative_rpm == 0.f && fixture.rpmCalls == 0);
        assert(fixture.order == "sgurm");
    }
    {
        auto state = configured(); state.player = true;
        state.physics_gear = 2; state.gear = 2;
        Fixture fixture; fixture.first = 10.f; fixture.rotation = .6f; fixture.gear = 3;
        assert(wot::refresh_detailed_engine_state(state, fixture.inputs(), .25f));
        assert(state.gear == 3 && state.gear_up && state.rpm == 1950.f);
        assert(state.relative_rpm == .75f && state.relative_rotation_speed == 2.f);
        assert(fixture.order == "spgurm");
    }
    {
        auto state = configured();
        Fixture fixture; fixture.first = 10.f; fixture.second = 12.f;
        assert(wot::refresh_detailed_engine_state(state, fixture.inputs(), .25f));
        assert(state.gear == 1 && state.gear_up && state.smoothed_speed == 1.5f);
        assert(near(state.rpm, 77.94872283935547f));
        assert(near(state.relative_rpm, .7794871926307678f));
        assert(fixture.order == "sgsurm");
    }
    {
        auto state = configured(); state.smoothed_speed = 20.f; state.estimated_gear = 2;
        Fixture fixture; fixture.first = -5.f; fixture.second = -8.f;
        fixture.forward = {{0.f, .5f, 1.f}};
        assert(wot::refresh_detailed_engine_state(state, fixture.inputs(), .5f));
        assert(state.gear == 2 && !state.gear_up && state.gear2 == 100 && state.rpm == 90.f);
        assert(near(state.pitch, -.46364760398864746f));
        assert(near(state.roughness, .9272952079772949f));
        assert(near(state.physics_load, .9272952079772949f));
    }
    {
        auto state = configured(); state.player = true;
        Fixture fixture;
        auto input = fixture.inputs();
        // A callback can synchronously destroy its native component. The
        // adapter's identity guard must stop before reading later providers.
        input.on_gear_up = []() { return false; };
        assert(!wot::refresh_detailed_engine_state(state, input, .25f));
        assert(fixture.order == "sg");
    }
    std::cout << "Stock detailed engine state snapshots passed\n";
}
