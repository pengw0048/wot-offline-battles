#include "wot/stock_shader_binary.hpp"

#include <algorithm>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <utility>

namespace wot {
namespace {

class ShaderBytes {
public:
    ShaderBytes(const void* data, std::size_t size)
        : data_(static_cast<const unsigned char*>(data)), size_(size) {}

    bool contains(std::uint64_t offset, std::uint64_t count) const {
        return offset <= size_ && count <= size_ - offset;
    }

    std::uint32_t word(std::uint64_t offset) const {
        const auto at = static_cast<std::size_t>(offset);
        return std::uint32_t(data_[at]) |
               (std::uint32_t(data_[at + 1]) << 8) |
               (std::uint32_t(data_[at + 2]) << 16) |
               (std::uint32_t(data_[at + 3]) << 24);
    }

private:
    const unsigned char* data_;
    std::size_t size_;
};

bool invalid(std::string& error, const char* message) {
    error = message;
    return false;
}

} // namespace

bool decode_stock_dx9_shader(const void* bytes, std::size_t size,
                            StockShaderBinary& output, std::string& error) {
    const ShaderBytes source(bytes, size);
    if (!bytes || !source.contains(0, 0x30)) {
        return invalid(error, "Truncated ARIS shader header");
    }
    // #1513 b3e080 / b2fad0: ARIS version 3, DX9 core format version 1.
    if (source.word(0) != 0x53495241 || source.word(4) != 0x20395844 ||
        source.word(8) != 3 || source.word(0x20) != 1) {
        return invalid(error, "Unsupported ARIS DX9 shader format");
    }

    const std::uint64_t uniform_count_offset =
        std::uint64_t(source.word(0x24)) + 0x1c;
    const std::uint64_t bytecode_offset =
        std::uint64_t(source.word(0x28)) + 0x20;
    const std::uint32_t bytecode_size = source.word(0x2c);
    const std::uint32_t resource_offset = source.word(0x18);
    if (!source.contains(uniform_count_offset, 4) ||
        !source.contains(resource_offset, 4) ||
        !source.contains(bytecode_offset, bytecode_size) || (bytecode_size & 3)) {
        return invalid(error, "ARIS shader section is outside its blob");
    }

    const auto uniform_count_word = source.word(uniform_count_offset);
    const auto resource_count_word = source.word(resource_offset);
    // Both original consumers enter their table loops only for signed n > 0.
    const auto uniform_count = uniform_count_word < 0x80000000u ? uniform_count_word : 0;
    const auto resource_count = resource_count_word < 0x80000000u ? resource_count_word : 0;
    if (!source.contains(uniform_count_offset + 4, std::uint64_t(uniform_count) * 32) ||
        !source.contains(std::uint64_t(resource_offset) + 4,
                         std::uint64_t(resource_count) * 12)) {
        return invalid(error, "Truncated ARIS shader binding table");
    }

    StockShaderBinary decoded;
    decoded.stage = source.word(0x1c);
    decoded.source_hash = source.word(0x10);
    const auto relative = source.word(0x14);
    std::memcpy(&decoded.string_table_relative, &relative, sizeof(relative));
    decoded.bytecode.reserve(bytecode_size / 4);
    for (std::size_t offset = 0; offset < bytecode_size; offset += 4) {
        decoded.bytecode.push_back(source.word(bytecode_offset + offset));
    }

    decoded.uniforms.reserve(uniform_count);
    decoded.register_copies.reserve(uniform_count);
    for (std::uint32_t index = 0; index < uniform_count; ++index) {
        const std::uint64_t at = uniform_count_offset + 4 + std::uint64_t(index) * 32;
        StockShaderUniformBinding binding;
        binding.name_offset = source.word(at);
        binding.uniform_slot = source.word(at + 4);
        binding.source_offset = source.word(at + 8);
        binding.source_size = source.word(at + 12);
        binding.register_kind = source.word(at + 16);
        binding.first_register = source.word(at + 20);
        binding.register_count = source.word(at + 24);
        binding.source_type = source.word(at + 28);
        decoded.uniforms.push_back(binding);
        // b2fad0 ignores other register classes, while retaining their source
        // records here lets the effect owner inspect the original metadata.
        if (binding.register_kind > 2) {
            continue;
        }
        const std::uint32_t unit = binding.register_kind == 0 ? 4 : 16;
        const std::uint64_t end = std::uint64_t(binding.first_register) +
                                  binding.register_count;
        if (end * unit > std::numeric_limits<std::uint32_t>::max()) {
            return invalid(error, "ARIS register range overflows its native byte offset");
        }
        StockShaderRegisterCopy copy;
        copy.kind = static_cast<StockShaderRegisterKind>(binding.register_kind);
        copy.uniform_slot = binding.uniform_slot;
        copy.source_offset = binding.source_offset;
        copy.size = binding.source_size;
        copy.destination_offset = binding.first_register * unit;
        if (copy.kind == StockShaderRegisterKind::floating) {
            copy.size = std::min(copy.size, binding.register_count * 16);
            if (binding.source_type == 1) {
                copy.conversion = StockShaderRegisterConversion::boolean_bits_to_float;
            } else if (binding.source_type == 2) {
                copy.conversion = StockShaderRegisterConversion::integer_bits_to_float;
            }
        }
        decoded.register_copies.push_back(copy);
        auto& range = decoded.register_ranges[binding.register_kind];
        range.first = std::min(range.first, binding.first_register);
        range.end = std::max(range.end, static_cast<std::uint32_t>(end));
    }

    decoded.resources.reserve(resource_count);
    for (std::uint32_t index = 0; index < resource_count; ++index) {
        const std::size_t at = std::size_t(resource_offset) + 4 + std::size_t(index) * 12;
        decoded.resources.push_back({source.word(at), source.word(at + 4),
                                     source.word(at + 8)});
    }
    output = std::move(decoded);
    error.clear();
    return true;
}

void convert_stock_shader_register_words(StockShaderRegisterConversion conversion,
                                         std::uint32_t* words, std::size_t count) {
    if (conversion == StockShaderRegisterConversion::none || count == 0) {
        return;
    }
    if (!words) {
        throw std::invalid_argument("Register conversion requires writable words");
    }
    for (std::size_t index = 0; index < count; ++index) {
        if (conversion == StockShaderRegisterConversion::boolean_bits_to_float) {
            // b2f780 tests integer bits, including float -0.0's nonzero bits.
            words[index] = words[index] ? 0x3f800000 : 0;
        } else if (conversion == StockShaderRegisterConversion::integer_bits_to_float) {
            // b2f8c0 interprets source bits as int32, then converts to binary32.
            std::int32_t integer;
            std::memcpy(&integer, &words[index], sizeof(integer));
            const float floating = static_cast<float>(integer);
            std::memcpy(&words[index], &floating, sizeof(floating));
        } else {
            throw std::invalid_argument("Unknown register conversion");
        }
    }
}

namespace {
class EffectReader {
public:
    EffectReader(const void* bytes, std::size_t size)
        : data_(static_cast<const unsigned char*>(bytes)), source_(bytes, size) {}
    std::uint32_t word(std::uint64_t at) const {
        require(at, 4); return source_.word(at);
    }
    void require(std::uint64_t at, std::uint64_t count) const {
        if (!source_.contains(at, count))
            throw std::runtime_error("ARIE effect section is outside its member");
    }
    const unsigned char* data(std::uint64_t at) const {
        require(at, 0); return data_ + static_cast<std::size_t>(at);
    }
    std::vector<unsigned char> bytes(std::uint64_t at, std::uint64_t count) const {
        require(at, count);
        return {data(at), data(at + count)};
    }
    std::pair<std::uint64_t, std::uint32_t> table(std::uint32_t header,
                                                std::uint32_t stride) const {
        const auto offset = word(header);
        if (offset == 0xffffffff) return {0, 0};
        const auto count = word(offset);
        require(std::uint64_t(offset)+4, std::uint64_t(count)*stride);
        return {std::uint64_t(offset)+4, count};
    }
private:
    const unsigned char* data_;
    ShaderBytes source_;
};

std::string effect_string(const std::vector<unsigned char>& strings, std::uint32_t at) {
    if (at == 0xffffffff) return {};
    if (at >= strings.size()) throw std::runtime_error("ARIE string offset is invalid");
    const auto end = std::find(strings.begin()+at, strings.end(), 0);
    if (end == strings.end()) throw std::runtime_error("Unterminated ARIE string");
    return {strings.begin()+at, end};
}
} // namespace

bool decode_stock_dx9_effect(const void* bytes, std::size_t size,
                            StockEffectBinary& output, std::string& error) {
    if (!bytes || size < 0x50) return invalid(error, "Truncated ARIE effect header");
    try {
        const EffectReader source(bytes, size);
        if (source.word(0) != 0x45495241 || source.word(4) != 0x20395844 ||
            source.word(8) != 4)
            return invalid(error, "Unsupported ARIE DX9 effect format");
        StockEffectBinary decoded;
        decoded.source_hash = source.word(0xc);
        decoded.flags = source.word(0x10);
        decoded.image = source.bytes(0, size);
        const auto strings = source.word(0x14);
        if (strings != 0xffffffff)
            decoded.strings = source.bytes(std::uint64_t(strings)+4, source.word(strings));
        const auto string = [&](std::uint32_t at) { return effect_string(decoded.strings, at); };
        auto table = source.table(0x18, 24);
        for (std::uint32_t i = 0; i < table.second; ++i) {
            const auto at = table.first + std::uint64_t(i)*24;
            decoded.types.push_back({string(source.word(at)), source.word(at+4),
                source.word(at+8), source.word(at+12), source.word(at+16), source.word(at+20), {}});
        }
        const auto read_uniform = [&](std::uint64_t member) {
            source.require(member, 32);
            StockEffectUniform uniform;
            uniform.flags = source.word(member);
            uniform.name = string(source.word(member+4));
            uniform.semantic = string(source.word(member+8));
            uniform.semantic_present = source.word(member+8) != 0xffffffff;
            uniform.type_index = source.word(member+12);
            uniform.byte_offset = source.word(member+16);
            uniform.elements = source.word(member+20);
            uniform.element_padding = source.word(member+24);
            uniform.annotations = source.word(member+28);
            if (uniform.type_index >= decoded.types.size())
                throw std::runtime_error("ARIE uniform type index is invalid");
            return uniform;
        };
        for (std::size_t i = 0; i < decoded.types.size(); ++i) {
            auto& type = decoded.types[i];
            if (type.type_class != 5) continue;
            const auto at = table.first+i*24+type.rows_or_members_offset;
            source.require(at, std::uint64_t(type.columns_or_member_count)*32);
            for (std::uint32_t j = 0; j < type.columns_or_member_count; ++j)
                type.members.push_back(read_uniform(at+std::uint64_t(j)*32));
        }
        table = source.table(0x1c, 28);
        for (std::uint32_t i = 0; i < table.second; ++i) {
            const auto at = table.first + std::uint64_t(i)*28;
            StockEffectUniformBuffer buffer;
            buffer.flags = source.word(at); buffer.name = string(source.word(at+4));
            buffer.annotations = source.word(at+8);
            const auto members = at + source.word(at+12);
            const auto count = source.word(at+16);
            source.require(members, std::uint64_t(count)*32);
            buffer.defaults = source.bytes(at+source.word(at+24), source.word(at+20));
            for (std::uint32_t j = 0; j < count; ++j) {
                const auto member = members+std::uint64_t(j)*32;
                buffer.uniforms.push_back(read_uniform(member));
            }
            decoded.uniform_buffers.push_back(std::move(buffer));
        }
        table = source.table(0x20, 16);
        for (std::uint32_t i = 0; i < table.second; ++i) {
            const auto at = table.first+std::uint64_t(i)*16;
            const auto payload = at+source.word(at+12);
            decoded.selection_domains.push_back({source.word(at), source.word(at+4), source.word(at+8),
                source.bytes(payload+4, source.word(payload))});
        }
        const auto read_resources = [&](std::uint32_t header,
                                         std::vector<StockEffectResource>& result) {
            const auto resources = source.table(header, 24);
            for (std::uint32_t i = 0; i < resources.second; ++i) {
                const auto at = resources.first+std::uint64_t(i)*24;
                StockEffectResource resource{source.word(at), string(source.word(at+4)),
                    string(source.word(at+8)), source.word(at+12), source.word(at+16),
                    source.word(at+20), source.word(at+8) != 0xffffffff};
                if (resource.type_index >= decoded.types.size())
                    throw std::runtime_error("ARIE resource type index is invalid");
                result.push_back(std::move(resource));
            }
        };
        read_resources(0x24, decoded.resource_views);
        read_resources(0x28, decoded.unordered_views);
        const auto stage_table = source.word(0x2c);
        if (stage_table != 0xffffffff) {
            source.require(stage_table, 24);
            for (unsigned stage = 0; stage < 6; ++stage) {
                const auto at = std::uint64_t(stage_table)+source.word(stage_table+stage*4);
                const auto count = source.word(at);
                source.require(at+4, std::uint64_t(count)*4);
                for (std::uint32_t i = 0; i < count; ++i)
                    decoded.resource_indirections[stage].push_back(source.word(at+4+std::uint64_t(i)*4));
            }
        }
        table = source.table(0x30, 16);
        for (std::uint32_t i = 0; i < table.second; ++i) {
            const auto at = table.first+std::uint64_t(i)*16;
            StockEffectTechnique technique;
            technique.name = string(source.word(at)); technique.annotations = source.word(at+4);
            // b3d330: each TECH record owns its relative pass-table offset.
            const auto passes = at+source.word(at+8);
            const auto count = source.word(at+12);
            source.require(passes, std::uint64_t(count)*36);
            for (std::uint32_t j = 0; j < count; ++j) {
                const auto pass_at = passes+std::uint64_t(j)*36;
                StockEffectPass pass;
                pass.name = string(source.word(pass_at));
                pass.annotations = source.word(pass_at+4);
                for (unsigned k = 0; k < 7; ++k) pass.descriptor[k] = source.word(pass_at+8+k*4);
                technique.passes.push_back(std::move(pass));
            }
            decoded.techniques.push_back(std::move(technique));
        }
        table = source.table(0x34, 20);
        for (std::uint32_t i = 0; i < table.second; ++i) {
            std::array<std::uint32_t, 5> state{};
            for (unsigned j = 0; j < state.size(); ++j)
                state[j] = source.word(table.first+std::uint64_t(i)*20+j*4);
            decoded.sampler_states.push_back(state);
        }
        table = source.table(0x38, 24);
        for (std::uint32_t i = 0; i < table.second; ++i) {
            std::array<std::uint32_t, 6> state{};
            for (unsigned j = 0; j < state.size(); ++j)
                state[j] = source.word(table.first+std::uint64_t(i)*24+j*4);
            decoded.render_states.push_back(state);
        }
        table = source.table(0x3c, 8);
        for (std::uint32_t i = 0; i < table.second; ++i) {
            const auto at = table.first+std::uint64_t(i)*8;
            const auto shader_at = source.word(at), shader_size = source.word(at+4);
            source.require(shader_at, shader_size);
            StockShaderBinary shader;
            if (!decode_stock_dx9_shader(source.data(shader_at), shader_size, shader, error))
                return false;
            decoded.shaders.push_back(std::move(shader));
        }
        const auto annotations = source.word(0x40);
        if (annotations != 0xffffffff) {
            const auto groups = source.word(annotations);
            auto at = std::uint64_t(annotations)+4;
            for (std::uint32_t i = 0; i < groups; ++i) {
                const auto count = source.word(at);
                at += 4;
                source.require(at, std::uint64_t(count)*12+4);
                std::vector<StockEffectAnnotation> group;
                for (std::uint32_t j = 0; j < count; ++j) {
                    const auto entry = at+std::uint64_t(j)*12;
                    StockEffectAnnotation annotation;
                    annotation.name = string(source.word(entry));
                    annotation.type = source.word(entry+4);
                    const auto value = entry+source.word(entry+8);
                    if (annotation.type == 1 || annotation.type == 3 || annotation.type == 4)
                        annotation.value = source.bytes(value, 4);
                    else if (annotation.type == 5) {
                        auto end = value;
                        while (true) {
                            source.require(end, 1);
                            if (*source.data(end++) == 0) break;
                        }
                        annotation.value = source.bytes(value, end-value);
                    }
                    group.push_back(std::move(annotation));
                }
                at += std::uint64_t(count)*12;
                const auto payload_size = source.word(at);
                source.require(at+4, payload_size);
                at += std::uint64_t(payload_size)+4;
                decoded.annotations.push_back(std::move(group));
            }
        }
        const auto code = source.word(0x44), data = source.word(0x48);
        if (code != 0xffffffff)
            decoded.selector_code = source.bytes(std::uint64_t(code)+4, source.word(code));
        if (data != 0xffffffff)
            decoded.selector_data = source.bytes(data, source.word(0x4c));
        output = std::move(decoded);
        error.clear(); return true;
    } catch (const std::runtime_error& failure) {
        return invalid(error, failure.what());
    }
}

} // namespace wot
