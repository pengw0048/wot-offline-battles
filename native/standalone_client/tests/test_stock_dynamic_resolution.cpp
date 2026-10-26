#ifdef NDEBUG
#undef NDEBUG
#endif
#include "wot/stock_dynamic_resolution.hpp"

#include <cassert>
#include <cmath>
#include <limits>

static bool near(float a, float b) { return std::fabs(a - b) < 0.00001f; }

int main() {
    wot::StockDynamicResolution state;
    assert(state.enabled() && !state.paused());
    assert(state.config().critical_fps == 24.f && state.config().safe_fps == 30.f);
    state.set_scale(-1.f);
    assert(near(state.scale(), 0.1f));
    state.set_scale(2.f);
    assert(state.scale() == 1.f);
    state.set_scale(std::numeric_limits<float>::quiet_NaN());
    assert(state.scale() == 1.f);
    state.set_scale(0.73f);
    assert(state.scene_dimension(100.f, true) == 73);
    assert(state.scene_dimension(101.f, true) == 74);
    assert(state.scene_dimension(0.f, true) == 1);
    assert(state.scene_dimension(101.f, false) == 101);
    auto projection = state.project(101, 55);
    assert(near(projection.parameters[0], 74.f / 101.f));
    assert(near(projection.parameters[1], 40.f / 55.f));
    assert(near(projection.parameters[2], 1.f / 74.f));
    assert(near(projection.downsample_parameters[0], 37.f / 50.f));
    assert(near(projection.downsample_parameters[1], 20.f / 27.f));
    assert(near(projection.downsample_parameters[2], 2.f / (101.f * 37.f / 50.f)));
    state.set_scale(1.f);
    projection = state.project(101, 55);
    assert(projection.parameters == (std::array<float, 4>{{1.f, 1.f, 0.f, 0.f}}));
    for (int i = 0; i < 4; ++i) {
        assert(projection.downsample_parameters[i * 4] == 1.f);
        assert(projection.downsample_parameters[i * 4 + 1] == 1.f);
        assert(projection.downsample_parameters[i * 4 + 2] == 0.f);
        assert(projection.downsample_parameters[i * 4 + 3] == 0.f);
    }

    // Manual baseline survives multiple automatic reductions and pause cycles.
    state.enable(false, 40);
    state.set_scale(0.8f);
    state.enable(true, 40);
    state.tick(0.5f, 45);
    assert(near(state.scale(), 0.8f));
    state.tick(0.5f, 50);
    assert(near(state.scale(), 0.7f));
    state.tick(1.f, 60);
    assert(near(state.scale(), 0.65f));
    state.pause(true, 60);
    assert(near(state.scale(), 0.8f));
    state.tick(20.f, 2000);
    assert(near(state.scale(), 0.8f));
    state.pause(false, 2000);
    state.tick(0.5f, 2005);
    assert(near(state.scale(), 0.8f));
    state.tick(0.5f, 2010);
    assert(near(state.scale(), 0.7f));
    state.tick(2.f, 2070); // 30 fps over the full late interval.
    assert(near(state.scale(), 0.75f));
    state.tick(1.f, 2095); // Above critical, below safe: retain current scale.
    assert(near(state.scale(), 0.75f));
    state.tick(1.f, 2125);
    assert(near(state.scale(), 0.8f));
    state.tick(1.f, 2155);
    assert(near(state.scale(), 0.8f));

    // At the lower bound, a full correction step must fit before reducing.
    state.set_scale(0.5f);
    state.tick(1.f, 2165);
    assert(near(state.scale(), 0.5f));
    state.enable(false, 2165);
    assert(near(state.scale(), 0.8f));
    state.pause(true, 2200);
    state.set_scale(0.6f);
    state.pause(false, 2300);
    assert(near(state.scale(), 0.6f));
    state.tick(10.f, 2400);
    assert(near(state.scale(), 0.6f));

    // Frame deltas retain the renderer counter's unsigned rollover semantics.
    state.enable(true, 0xfffffff0U);
    state.tick(1.f, 0xfffffffaU);
    assert(near(state.scale(), 0.5f));
    state.tick(1.f, 24U);
    assert(near(state.scale(), 0.55f));

    // Exact engine_config.xml uses a different threshold from ctor defaults.
    wot::StockDynamicResolution configured;
    auto config = configured.config();
    config.critical_fps = 15.f;
    config.safe_fps = 15.f * 1.15f;
    configured.configure(config);
    configured.tick(1.f, 20);
    assert(configured.scale() == 1.f);
    configured.tick(1.f, 30);
    assert(near(configured.scale(), 0.9f));
    configured.tick(1.f, 46); // 16 FPS is within the hysteresis band.
    assert(near(configured.scale(), 0.9f));
    configured.tick(1.f, 64);
    assert(near(configured.scale(), 0.95f));
}
