#include "wot/stock_shader_runtime.hpp"

#include <iostream>
#include <stdexcept>

namespace {
void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
std::uint32_t state(const std::vector<wot::StockDx9StateValue>& states, std::uint32_t key) {
    for (auto item : states) if (item.state == key) return item.value;
    throw std::runtime_error("Missing expected D3D9 state");
}
void register_checks() {
    wot::StockShaderBinary shader;
    shader.register_ranges = {{{2,3}, {1,2}, {0,2}}};
    using Kind = wot::StockShaderRegisterKind;
    using Conversion = wot::StockShaderRegisterConversion;
    shader.register_copies = {{Kind::boolean, 0, 8, 4, 8, Conversion::none},
        {Kind::integer, 0, 12, 4, 16, Conversion::none},
        {Kind::floating, 0, 0, 8, 0, Conversion::boolean_bits_to_float},
        {Kind::floating, 1, 0, 4, 16, Conversion::integer_bits_to_float}};
    const std::uint32_t first[] = {0,0x80000000,0xffffffff,0x12345678};
    const std::uint32_t second[] = {0xfffffff9};
    std::vector<wot::StockShaderUniformView> inputs{{first,sizeof(first)}, {second,sizeof(second)}};
    wot::StockShaderRegisters registers;
    std::string error;
    check(wot::stage_stock_shader_registers(shader, inputs, registers, error), "Valid register plan");
    check(registers[0].words == std::vector<std::uint32_t>({0,0,0xffffffff}),
          "Bool registers use one word and keep the prefix from zero");
    check(registers[1].words.size() == 8 && registers[1].words[4] == 0x12345678,
          "Int registers retain raw signed integer bits");
    check(registers[2].words[0] == 0 && registers[2].words[1] == 0x3f800000 &&
          registers[2].words[4] == 0xc0e00000, "Per-copy native bool/int-to-float conversion");
    auto invalid = shader; invalid.register_copies[3].source_offset = 100;
    registers[0].range.first = 777;
    check(!wot::stage_stock_shader_registers(invalid, inputs, registers, error) &&
          registers[0].range.first == 777, "Register staging failure is atomic");
}
void state_checks() {
    const auto packed = std::uint64_t(1 | (4 << 1) | (5 << 5) | (2 << 9) |
        (6 << 12) | (7 << 16) | (4 << 20) | (3 << 23)) | (std::uint64_t(0xb4000000) << 25);
    std::array<std::uint32_t,6> descriptor{{static_cast<std::uint32_t>(packed),
        static_cast<std::uint32_t>(packed >> 32),0x3fc00000,
        2 | (2 << 3) | (1 << 5) | (7 << 6) | (2 << 10) | (3 << 13) | (4 << 16) | (129 << 19),
        0x4321fe7f,1}};
    std::vector<wot::StockDx9StateValue> values;
    std::string error;
    check(wot::stock_dx9_render_states(wot::stock_dx9_default_render_descriptor(), values, error) &&
          state(values,7) == 1 && state(values,14) == 1 && state(values,23) == 4 &&
          state(values,22) == 3 && state(values,168) == 15 && state(values,161) == 1,
          "Native default descriptor for passes without RSTD");
    check(wot::stock_dx9_render_states(descriptor, values, error) && values.size() == 28,
          "Complete native render-state submission");
    check(state(values,27) == 1 && state(values,171) == 3 && state(values,19) == 5 &&
          state(values,20) == 6 && state(values,206) == 1 && state(values,209) == 5,
          "Blend and separate-alpha states");
    check(state(values,7) == 1 && state(values,14) == 1 && state(values,195) == 0xb4000000 &&
          state(values,175) == 0x3fc00000 && state(values,23) == 4 && state(values,8) == 2,
          "Depth bias retains float bits; depth/raster enums map to D3D9");
    check(state(values,52) == 1 && state(values,56) == 6 && state(values,57) == 129 &&
          state(values,58) == 127 && state(values,59) == 254 && state(values,168) == 1 &&
          state(values,190) == 2 && state(values,191) == 3 && state(values,192) == 4,
          "Stencil masks/reference and all four color-write masks");
    descriptor[0] |= 15 << 1;
    values[0].value = 999;
    check(!wot::stock_dx9_render_states(descriptor, values, error) && values[0].value == 999,
          "Invalid state enum cannot fabricate a successful state");
    const std::array<std::uint32_t,5> sampler{{0 | (1<<2) | (2<<4) | (2<<6) | (1<<8) | (3<<10),
        0xaabbccdd,0xbe800000,7,16}};
    const auto states = wot::stock_dx9_sampler_states(sampler);
    check(states[0].value == 1 && states[1].value == 2 && states[2].value == 3 &&
          states[3].value == 0xaabbccdd && states[4].state == 5 && states[4].value == 2 &&
          states[5].state == 6 && states[5].value == 1 && states[6].value == 3 &&
          states[7].value == 7 && states[8].value == 16 && states[9].state == 8 &&
          states[9].value == 0xbe800000, "Sampler address/filter/LOD/border values and native order");
}
} // namespace
int main() {
    try {
        register_checks(); state_checks();
        std::cout << "Stock shader runtime checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
