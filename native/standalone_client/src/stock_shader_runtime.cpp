#include "wot/stock_shader_runtime.hpp"

#include <cstring>
#include <limits>
#include <utility>

namespace wot {
namespace {
bool invalid(std::string& error, const char* message) { error = message; return false; }
} // namespace

bool stage_stock_shader_registers(const StockShaderBinary& shader,
    const std::vector<StockShaderUniformView>& uniforms,
    StockShaderRegisters& output, std::string& error) {
    StockShaderRegisters staged;
    for (unsigned kind = 0; kind < staged.size(); ++kind) {
        staged[kind].range = shader.register_ranges[kind];
        const auto words = std::uint64_t(staged[kind].range.end)*(kind == 0 ? 1 : 4);
        if (words > std::numeric_limits<std::size_t>::max()/sizeof(std::uint32_t))
            return invalid(error, "Shader register buffer exceeds the host address space");
        staged[kind].words.resize(static_cast<std::size_t>(words), 0);
    }
    for (const auto& copy : shader.register_copies) {
        const auto kind = static_cast<std::size_t>(copy.kind);
        if (kind >= staged.size() || copy.uniform_slot >= uniforms.size())
            return invalid(error, "Shader register copy has an invalid source slot");
        const auto& source = uniforms[copy.uniform_slot];
        auto& destination = staged[kind].words;
        const auto destination_size = std::uint64_t(destination.size())*4;
        if ((!source.bytes && copy.size) || copy.source_offset > source.size ||
            copy.size > source.size-copy.source_offset ||
            copy.destination_offset > destination_size ||
            copy.size > destination_size-copy.destination_offset || (copy.destination_offset & 3))
            return invalid(error, "Shader register copy is outside its source or destination");
        if (copy.size) {
            auto* at = destination.data()+copy.destination_offset/4;
            std::memcpy(at, static_cast<const unsigned char*>(source.bytes)+copy.source_offset, copy.size);
            convert_stock_shader_register_words(copy.conversion, at, copy.size/4);
        }
    }
    output = std::move(staged); error.clear(); return true;
}

bool stock_dx9_render_states(const std::array<std::uint32_t, 6>& d,
    std::vector<StockDx9StateValue>& output, std::string& error) {
    // b3a240's identity enum tables produce this same packed descriptor;
    // b2d030 and its b4ca10/b2f1d0/etc. converters produce these D3D9 values.
    const auto src = (d[0] >> 1) & 15, dst = (d[0] >> 5) & 15;
    const auto blend = (d[0] >> 9) & 7, src_alpha = (d[0] >> 12) & 15;
    const auto dst_alpha = (d[0] >> 16) & 15, blend_alpha = (d[0] >> 20) & 7;
    const auto depth = (d[0] >> 23) & 3;
    const auto comparison = d[3] & 7, cull = (d[3] >> 3) & 3;
    const auto stencil = (d[3] >> 6) & 15;
    if (src > 10 || dst > 10 || src_alpha > 10 || dst_alpha > 10 ||
        blend > 4 || blend_alpha > 4 || comparison > 5 || cull > 2 || stencil > 8)
        return invalid(error, "Unsupported ARIE render-state enum");
    const std::uint32_t comparisons[] = {8, 2, 4, 5, 7, 3};
    const std::uint32_t stencil_comparisons[] = {8, 8, 2, 4, 5, 7, 3, 6, 1};
    const auto depth_bias = static_cast<std::uint32_t>(
        ((std::uint64_t(d[1]) << 32) | d[0]) >> 25);
    std::vector<StockDx9StateValue> states{
        {27, d[0] & 1}, {171, blend+1}, {19, src+1}, {20, dst+1},
        {206, 1}, {209, blend_alpha+1}, {207, src_alpha+1}, {208, dst_alpha+1},
        {7, depth & 1}, {14, depth >> 1}, {195, depth_bias}, {175, d[2]},
        {23, comparisons[comparison]}, {22, cull+1}, {8, ((d[3] >> 5) & 1) ? 2u : 3u},
        {52, stencil != 0 ? 1u : 0u}, {56, stencil_comparisons[stencil]},
        {53, ((d[3] >> 10) & 7)+1}, {54, ((d[3] >> 13) & 7)+1},
        {55, ((d[3] >> 16) & 7)+1}, {57, (d[3] >> 19) & 255},
        {58, d[4] & 255}, {59, (d[4] >> 8) & 255},
        {168, (d[4] >> 16) & 15}, {190, (d[4] >> 20) & 15},
        {191, (d[4] >> 24) & 15}, {192, d[4] >> 28}, {161, d[5] & 1}
    };
    output = std::move(states); error.clear(); return true;
}

std::array<StockDx9StateValue, 10> stock_dx9_sampler_states(
    const std::array<std::uint32_t, 5>& d) {
    // b3d330's byte lookup tables are identity maps. b2dc00 adds one for
    // address modes, retains filter enums, and submits raw LOD-bias bits.
    return {{{1, (d[0] & 3)+1}, {2, ((d[0] >> 2) & 3)+1},
        {3, ((d[0] >> 4) & 3)+1}, {4, d[1]}, {5, (d[0] >> 6) & 3},
        {6, (d[0] >> 8) & 3}, {7, (d[0] >> 10) & 3},
        {9, d[3]}, {10, d[4]}, {8, d[2]}}};
}

std::array<std::uint32_t, 6> stock_dx9_default_render_descriptor() {
    return {{0x01811022, 0, 0, 0x12, 0xffff0000, 1}};
}

} // namespace wot
