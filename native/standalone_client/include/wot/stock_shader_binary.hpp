#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace wot {

// Parsed #1513 ARIS v3 data, not a D3DX effect or a replacement material.
enum class StockShaderRegisterKind : std::uint32_t {
    boolean = 0,
    integer = 1,
    floating = 2
};

enum class StockShaderRegisterConversion {
    none,
    boolean_bits_to_float,
    integer_bits_to_float
};

struct StockShaderUniformBinding {
    std::uint32_t name_offset = 0;
    std::uint32_t uniform_slot = 0;
    std::uint32_t source_offset = 0;
    std::uint32_t source_size = 0;
    std::uint32_t register_kind = 0;
    std::uint32_t first_register = 0;
    std::uint32_t register_count = 0;
    std::uint32_t source_type = 0;
};

struct StockShaderRegisterCopy {
    StockShaderRegisterKind kind = StockShaderRegisterKind::floating;
    std::uint32_t uniform_slot = 0;
    std::uint32_t source_offset = 0;
    std::uint32_t size = 0;
    std::uint32_t destination_offset = 0;
    StockShaderRegisterConversion conversion = StockShaderRegisterConversion::none;
};

struct StockShaderRegisterRange {
    // Original backend sentinel when this register class has no bindings.
    std::uint32_t first = 0x10000;
    std::uint32_t end = 0;
};

struct StockShaderResourceBinding {
    // Native groups: 1 SRV, 2 sampler, 3 uniform buffer, 11 UAV.
    std::uint32_t kind = 0;
    std::uint32_t resource_index = 0;
    std::uint32_t shader_slot = 0;
};

struct StockShaderBinary {
    std::uint32_t stage = 0; // Native ARIS: 1 vertex, 2 pixel.
    std::uint32_t source_hash = 0;
    std::int32_t string_table_relative = 0;
    std::vector<std::uint32_t> bytecode;
    std::vector<StockShaderUniformBinding> uniforms;
    std::vector<StockShaderRegisterCopy> register_copies;
    std::array<StockShaderRegisterRange, 3> register_ranges;
    std::vector<StockShaderResourceBinding> resources;
};

// Input is one isolated ARISDX9 shader blob from an ARIE shader offset/size
// entry. No resource file or shader bytecode is rewritten. Failure is atomic.
bool decode_stock_dx9_shader(const void* bytes, std::size_t size,
                            StockShaderBinary& output, std::string& error);

// Exact in-place conversions selected by #1513's DX9 register-copy plan.
void convert_stock_shader_register_words(StockShaderRegisterConversion conversion,
                                         std::uint32_t* words, std::size_t count);

struct StockEffectUniform {
    std::uint32_t flags = 0;
    std::string name;
    std::string semantic;
    bool semantic_present = false;
    std::uint32_t type_index = 0;
    std::uint32_t byte_offset = 0;
    std::uint32_t elements = 0;
    std::uint32_t element_padding = 0;
    std::uint32_t annotations = 0xffffffff;
};

struct StockEffectType {
    std::string name;
    std::uint32_t basic_type = 0;
    std::uint32_t type_class = 0;
    std::uint32_t rows_or_members_offset = 0;
    std::uint32_t columns_or_member_count = 0;
    std::uint32_t byte_size = 0;
    std::vector<StockEffectUniform> members;
};

struct StockEffectUniformBuffer {
    std::uint32_t flags = 0;
    std::string name;
    std::uint32_t annotations = 0xffffffff;
    std::vector<StockEffectUniform> uniforms;
    std::vector<unsigned char> defaults;
};

struct StockEffectResource {
    std::uint32_t flags = 0;
    std::string name;
    std::string semantic;
    std::uint32_t annotations = 0xffffffff;
    std::uint32_t type_index = 0;
    std::uint32_t elements = 0;
    bool semantic_present = false;
};

struct StockEffectAnnotation {
    std::string name;
    std::uint32_t type = 0;
    // Native types 1, 3, 4 have four bytes; type 5 includes its terminating NUL.
    std::vector<unsigned char> value;
};

struct StockEffectSelectionDomain {
    std::uint32_t source_offset = 0;
    std::uint32_t value_size = 0;
    std::uint32_t slot = 0;
    std::vector<unsigned char> values;
};

struct StockEffectPass {
    std::string name;
    std::uint32_t annotations = 0xffffffff;
    // Preserve the seven native selector/state fields until their consumer
    // resolves them. They are not a list of fixed shader indices.
    std::array<std::uint32_t, 7> descriptor{};
};

struct StockEffectTechnique {
    std::string name;
    std::uint32_t annotations = 0xffffffff;
    std::vector<StockEffectPass> passes;
};

// Immutable ARIEDX9 v4 definition. Per-material values belong to a separate
// binding; a definition never owns a mutable D3DX parameter table.
struct StockEffectBinary {
    std::uint32_t source_hash = 0;
    std::uint32_t flags = 0;
    // Retain relative metadata (including nested type layouts) byte-for-byte.
    // Typed tables below do not turn unconsumed fields into guessed contracts.
    std::vector<unsigned char> image;
    std::vector<unsigned char> strings;
    std::vector<StockEffectType> types;
    std::vector<StockEffectUniformBuffer> uniform_buffers;
    std::vector<StockEffectSelectionDomain> selection_domains;
    std::vector<StockEffectResource> resource_views;
    std::vector<StockEffectResource> unordered_views;
    std::array<std::vector<std::uint32_t>, 6> resource_indirections;
    std::vector<StockEffectTechnique> techniques;
    std::vector<std::array<std::uint32_t, 5>> sampler_states;
    std::vector<std::array<std::uint32_t, 6>> render_states;
    std::vector<StockShaderBinary> shaders;
    std::vector<std::vector<StockEffectAnnotation>> annotations;
    std::vector<unsigned char> selector_code;
    std::vector<unsigned char> selector_data;
};

// Input is the original ZIP member named "effect", not the outer FXO ZIP.
bool decode_stock_dx9_effect(const void* bytes, std::size_t size,
                            StockEffectBinary& output, std::string& error);

} // namespace wot
