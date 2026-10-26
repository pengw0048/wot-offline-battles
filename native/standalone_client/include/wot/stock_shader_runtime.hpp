#pragma once

#include "wot/stock_shader_binary.hpp"

namespace wot {

struct StockShaderUniformView {
    const void* bytes = nullptr;
    std::size_t size = 0;
};

struct StockShaderRegisterBlock {
    StockShaderRegisterRange range;
    // Full buffer from register zero, as allocated by b2fad0. Submission starts
    // at range.first; bool registers use one word, float/int use four words.
    std::vector<std::uint32_t> words;
};

using StockShaderRegisters = std::array<StockShaderRegisterBlock, 3>;

// b2fee0 replays the selected shader's complete copy plan at every application.
// Views are indexed by shader-local UBO slot, after ARIS resource indirection.
bool stage_stock_shader_registers(const StockShaderBinary& shader,
    const std::vector<StockShaderUniformView>& uniforms,
    StockShaderRegisters& output, std::string& error);

struct StockDx9StateValue {
    std::uint32_t state = 0;
    std::uint32_t value = 0;
};

// These are the real D3D9 state/value numbers consumed by SetRenderState and
// SetSamplerState. No D3D SDK types or renderer-owned resources enter this core.
bool stock_dx9_render_states(const std::array<std::uint32_t, 6>& descriptor,
    std::vector<StockDx9StateValue>& output, std::string& error);
// b3b3d0 constructs this descriptor with 1170bc0(0) before an optional RSTD.
std::array<std::uint32_t, 6> stock_dx9_default_render_descriptor();
std::array<StockDx9StateValue, 10> stock_dx9_sampler_states(
    const std::array<std::uint32_t, 5>& descriptor);

} // namespace wot
