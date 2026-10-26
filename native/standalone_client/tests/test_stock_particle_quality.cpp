#include "wot/stock_particle_quality.hpp"

#include <cassert>

int main() {
    wot::StockParticleQuality state;
    assert(state.normal().id == 2 && state.sniper().id == 6);
    assert(state.normal().tessellation_scale == 0.5f);
    wot::StockParticleQualityProfile high;
    high.lod_offset = 60.f;
    high.lod_multiplier = 1.1f;
    high.time_multiplier = 1.5f;
    high.render_quality = 8;
    state.register_profile("HIGH", false, high);
    state.register_profile("HIGH", true, high);
    state.register_profile("LOW", false, high);
    state.register_profile("LOW", true, high);
    state.register_profile("author-label", false, high);
    state.select_profile(2, false);
    assert(state.normal().render_quality == 4);
    assert(state.normal().lod_offset == 60.f);
    state.select_profile(123, false);
    assert(state.normal().id == 2 && state.normal().lod_offset == 60.f);
    // Unknown authored labels remain addressable as native ID zero.
    state.select_profile(0, false);
    assert(state.normal().name == "author-label");
    const auto count = state.profiles().size();
    high.render_quality = -1;
    high.time_multiplier = 2.f;
    state.register_profile("HIGH", false, high);
    assert(state.profiles().size() == count);
    state.select_profile(2, false);
    assert(state.normal().render_quality == 0 && state.normal().time_multiplier == 2.f);
    state.select_profile(8, true);
    auto output = state.project(false, true, false);
    assert(output.lod_limit == 4000.f && output.lod_offset == 60.f);
    state.select_profile(4, false);
    output = state.project(false, true, false);
    assert(output.lod_limit == 9000.f && output.lod_offset == 5000.f);
    assert(output.secondary_lod_offset == 5060.f && output.lod_multiplier == 1.f);
    output = state.project(true, true, true);
    assert(output.lod_offset == 5060.f && output.secondary_lod_offset == 5000.f);
    assert(output.tessellation_maximum == 0);
    state.select_profile(9, false);
    assert(state.normal().lod_offset == 4000.f && state.normal().render_quality == 0);
    output = state.project(false, true, false);
    assert(output.type_mask == 0x80000000U && output.lod_offset == 9000.f);
    state.select_profile(10, false);
    output = state.project(false, true, false);
    assert(output.type_mask == 0xffffffffU && output.lod_limit == 4000.f);
}
