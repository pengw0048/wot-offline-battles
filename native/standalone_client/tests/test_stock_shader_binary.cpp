#include "wot/stock_shader_binary.hpp"

#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>

namespace {
using Bytes = std::vector<unsigned char>;
void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
void put(Bytes& bytes, std::size_t at, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i)
        bytes.at(at+i) = static_cast<unsigned char>(value >> (8*i));
}
Bytes shader() {
    Bytes bytes(0x100);
    put(bytes, 0, 0x53495241); put(bytes, 4, 0x20395844);
    put(bytes, 8, 3); put(bytes, 0x10, 0x12345678);
    put(bytes, 0x14, 0xfffff000); put(bytes, 0x18, 0xd0);
    put(bytes, 0x1c, 2); put(bytes, 0x20, 1);
    put(bytes, 0x24, 0x14); put(bytes, 0x28, 0xa0);
    put(bytes, 0x2c, 8); put(bytes, 0x30, 4);
    const std::uint32_t bindings[4][8] = {
        {2, 0, 32, 80, 2, 5, 2, 1},
        {4, 1, 8, 12, 0, 3, 3, 4},
        {6, 2, 0, 16, 1, 1, 1, 2},
        {8, 3, 4, 8, 17, 0xffffffff, 0xffffffff, 0}
    };
    for (unsigned i = 0; i < 4; ++i)
        for (unsigned j = 0; j < 8; ++j) put(bytes, 0x34+i*32+j*4, bindings[i][j]);
    put(bytes, 0xc0, 0xffff0300); put(bytes, 0xc4, 0xffff);
    put(bytes, 0xd0, 3);
    put(bytes, 0xd4, 3); put(bytes, 0xd8, 4); put(bytes, 0xdc, 2);
    put(bytes, 0xe0, 1); put(bytes, 0xe4, 0); put(bytes, 0xe8, 7);
    put(bytes, 0xec, 19); put(bytes, 0xf0, 9); put(bytes, 0xf4, 8);
    return bytes;
}
void reader_checks() {
    const auto bytes = shader();
    wot::StockShaderBinary decoded;
    std::string error;
    check(wot::decode_stock_dx9_shader(bytes.data(), bytes.size(), decoded, error),
          "Valid ARIS decode");
    check(error.empty() && decoded.stage == 2 && decoded.source_hash == 0x12345678 &&
          decoded.string_table_relative == -4096, "Native shader metadata");
    check(decoded.bytecode == std::vector<std::uint32_t>({0xffff0300, 0xffff}),
          "Original bytecode remains intact");
    check(decoded.uniforms.size() == 4 && decoded.register_copies.size() == 3 &&
          decoded.resources.size() == 3 && decoded.resources[2].kind == 19,
          "Unknown bindings retained without a fabricated register mapping");
    const auto& copy = decoded.register_copies[0];
    check(copy.uniform_slot == 0 && copy.source_offset == 32 && copy.size == 32 &&
          copy.destination_offset == 80 &&
          copy.conversion == wot::StockShaderRegisterConversion::boolean_bits_to_float,
          "Float register copy clamps to register capacity and converts bool bits");
    check(decoded.register_copies[1].size == 12 &&
          decoded.register_copies[1].destination_offset == 12 &&
          decoded.register_copies[2].destination_offset == 16,
          "Bool/int register units and copy lengths");
    check(decoded.register_ranges[0].first == 3 && decoded.register_ranges[0].end == 6 &&
          decoded.register_ranges[2].first == 5 && decoded.register_ranges[2].end == 7,
          "Native register ranges");
    auto changed = bytes;
    put(changed, 0x50, 2); // Float source type is the eighth word of the record.
    check(wot::decode_stock_dx9_shader(changed.data(), changed.size(), decoded, error) &&
          decoded.register_copies[0].conversion ==
              wot::StockShaderRegisterConversion::integer_bits_to_float,
          "Integer-to-float register binding");
    changed = bytes; put(changed, 0x30, 0xffffffff); put(changed, 0xd0, 0x80000000);
    check(wot::decode_stock_dx9_shader(changed.data(), changed.size(), decoded, error) &&
          decoded.uniforms.empty() && decoded.resources.empty() &&
          decoded.register_ranges[2].first == 0x10000 &&
          decoded.register_ranges[2].end == 0,
          "Signed nonpositive table counts retain native empty range sentinel");
    for (unsigned failure = 0; failure < 8; ++failure) {
        changed = bytes;
        if (failure == 0) changed.resize(47);
        if (failure == 1) put(changed, 0, 0);
        if (failure == 2) put(changed, 8, 4);
        if (failure == 3) put(changed, 0x24, 0xffffffff);
        if (failure == 4) put(changed, 0x28, 0xffffffff);
        if (failure == 5) put(changed, 0x2c, 7);
        if (failure == 6) put(changed, 0x30, 0x7fffffff);
        if (failure == 7) put(changed, 0x48, 0xffffffff);
        decoded.stage = 0x13579;
        check(!wot::decode_stock_dx9_shader(changed.data(), changed.size(), decoded, error),
              "Invalid shader unexpectedly accepted");
        check(!error.empty() && decoded.stage == 0x13579, "Failure must be atomic");
    }
}
void packing_checks() {
    using C = wot::StockShaderRegisterConversion;
    std::uint32_t words[] = {0, 1, 0x80000000, 0xffffffff};
    wot::convert_stock_shader_register_words(C::boolean_bits_to_float, words, 4);
    check(words[0] == 0 && words[1] == 0x3f800000 && words[2] == 0x3f800000 &&
          words[3] == 0x3f800000, "Native bool conversion tests raw integer bits");
    const std::uint32_t source[] = {0, 1, 0xffffffff, 0x80000000, 0x7fffffff};
    std::uint32_t integers[5];
    std::memcpy(integers, source, sizeof(source));
    wot::convert_stock_shader_register_words(C::integer_bits_to_float, integers, 5);
    const std::uint32_t expected[] = {0, 0x3f800000, 0xbf800000, 0xcf000000, 0x4f000000};
    check(std::memcmp(integers, expected, sizeof(expected)) == 0,
          "Native signed int-to-binary32 conversion");
    wot::convert_stock_shader_register_words(C::none, nullptr, 10);
    wot::convert_stock_shader_register_words(C::boolean_bits_to_float, nullptr, 0);
}
Bytes effect() {
    Bytes bytes(0x500);
    put(bytes, 0, 0x45495241); put(bytes, 4, 0x20395844); put(bytes, 8, 4);
    for (unsigned at = 0x14; at <= 0x48; at += 4) put(bytes, at, 0xffffffff);
    const char strings[] = "float\0$Global\0value\0VALUE\0first\0second\0pass\0meta\0";
    put(bytes, 0x14, 0x50); put(bytes, 0x50, sizeof(strings));
    std::memcpy(bytes.data()+0x54, strings, sizeof(strings));
    put(bytes, 0x18, 0x90); put(bytes, 0x90, 1);
    const std::uint32_t type[] = {0, 3, 1, 1, 1, 4};
    for (unsigned j = 0; j < 6; ++j) put(bytes, 0x94+j*4, type[j]);
    put(bytes, 0x1c, 0xb0); put(bytes, 0xb0, 1);
    const std::uint32_t buffer[] = {0, 6, 0xffffffff, 28, 1, 4, 60};
    for (unsigned j = 0; j < 7; ++j) put(bytes, 0xb4+j*4, buffer[j]);
    const std::uint32_t member[] = {0, 14, 20, 0, 0, 1, 0, 0xffffffff};
    for (unsigned j = 0; j < 8; ++j) put(bytes, 0xd0+j*4, member[j]);
    put(bytes, 0xf0, 0x3f800000);
    put(bytes, 0x20, 0x100); put(bytes, 0x100, 1);
    put(bytes, 0x104, 7); put(bytes, 0x108, 8); put(bytes, 0x10c, 9);
    put(bytes, 0x110, 16); put(bytes, 0x114, 3);
    bytes[0x118] = 1; bytes[0x119] = 2; bytes[0x11a] = 3;
    put(bytes, 0x30, 0x140); put(bytes, 0x140, 2);
    const std::uint32_t techniques[2][4] = {{26, 0, 32, 1}, {32, 0xffffffff, 52, 1}};
    for (unsigned i = 0; i < 2; ++i)
        for (unsigned j = 0; j < 4; ++j) put(bytes, 0x144+i*16+j*4, techniques[i][j]);
    for (unsigned i = 0; i < 2; ++i) {
        const auto at = 0x164+i*36;
        put(bytes, at, 39); put(bytes, at+4, 0xffffffff);
        for (unsigned j = 0; j < 7; ++j) put(bytes, at+8+j*4, i*7+j);
    }
    put(bytes, 0x40, 0x200); put(bytes, 0x200, 1); put(bytes, 0x204, 2);
    put(bytes, 0x208, 44); put(bytes, 0x20c, 3); put(bytes, 0x210, 28);
    put(bytes, 0x214, 44); put(bytes, 0x218, 5); put(bytes, 0x21c, 20);
    put(bytes, 0x220, 8); put(bytes, 0x224, 0x12345678);
    bytes[0x228]='o'; bytes[0x229]='k';
    put(bytes, 0x44, 0x240); put(bytes, 0x240, 2);
    bytes[0x244]=0xaa; bytes[0x245]=0xbb;
    put(bytes, 0x48, 0x248); put(bytes, 0x4c, 3);
    bytes[0x248]=0xcc; bytes[0x249]=0xdd; bytes[0x24a]=0xee;
    return bytes;
}
void definition_checks() {
    auto bytes = effect();
    wot::StockEffectBinary definition;
    std::string error;
    check(wot::decode_stock_dx9_effect(bytes.data(), bytes.size(), definition, error),
          "Valid ARIE definition");
    check(definition.types.size() == 1 && definition.types[0].name == "float" &&
          definition.types[0].byte_size == 4, "Native type definition");
    check(definition.uniform_buffers.size() == 1 &&
          definition.uniform_buffers[0].name == "$Global" &&
          definition.uniform_buffers[0].uniforms[0].semantic == "VALUE" &&
          definition.uniform_buffers[0].defaults == Bytes({0,0,0x80,0x3f}),
          "Uniform record-relative members and defaults");
    check(definition.selection_domains[0].slot == 9 &&
          definition.selection_domains[0].values == Bytes({1,2,3}), "Selection-domain local payload");
    check(definition.techniques.size() == 2 && definition.techniques[1].name == "second" &&
          definition.techniques[1].passes[0].name == "pass" &&
          definition.techniques[1].passes[0].descriptor[0] == 7 &&
          definition.techniques[1].passes[0].descriptor[6] == 13,
          "Each technique owns its pass-relative offset");
    check(definition.annotations.size() == 1 && definition.annotations[0].size() == 2 &&
          definition.annotations[0][0].value == Bytes({0x78,0x56,0x34,0x12}) &&
          definition.annotations[0][1].value == Bytes({'o','k',0}),
          "Annotation value offsets are relative to each entry");
    check(definition.selector_code == Bytes({0xaa,0xbb}) &&
          definition.selector_data == Bytes({0xcc,0xdd,0xee}), "Selector code/data retained");
    for (unsigned failure = 0; failure < 6; ++failure) {
        auto changed = bytes;
        if (failure == 0) changed.resize(0x4f);
        if (failure == 1) put(changed, 4, 0);
        if (failure == 2) put(changed, 0x50, 0xffffffff);
        if (failure == 3) put(changed, 0xdc, 9);
        if (failure == 4) put(changed, 0x160, 0xffffffff);
        if (failure == 5) put(changed, 0x14c, 0xfffffff0);
        definition.source_hash = 0x12345;
        check(!wot::decode_stock_dx9_effect(changed.data(), changed.size(), definition, error),
              "Invalid definition unexpectedly accepted");
        check(definition.source_hash == 0x12345 && !error.empty(),
              "Definition failure must be atomic");
    }
}
} // namespace

int main(int argc, char** argv) {
    try {
        reader_checks(); packing_checks(); definition_checks();
        // Optional local evidence mode; no original resources are test fixtures.
        for (int i = 1; i < argc; ++i) {
            std::ifstream stream(argv[i], std::ios::binary);
            const Bytes bytes{std::istreambuf_iterator<char>(stream),
                              std::istreambuf_iterator<char>()};
            wot::StockShaderBinary shader;
            std::string error;
            if (!wot::decode_stock_dx9_shader(bytes.data(), bytes.size(), shader, error))
                throw std::runtime_error(std::string(argv[i])+": "+error);
        }
        std::cout << "Stock shader binary checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
