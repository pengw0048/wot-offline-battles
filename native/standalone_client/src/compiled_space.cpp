#include "wot/compiled_space.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <set>
#include <utility>

namespace wot {
namespace {

constexpr std::size_t directory_record_size = 24;
constexpr std::size_t terrain_prefix_size = 44;

class Reader {
public:
    Reader(const std::vector<std::uint8_t>& bytes, std::size_t begin,
           std::size_t length)
        : bytes_(bytes), begin_(begin), length_(length) {
        if (begin > bytes.size() || length > bytes.size() - begin) {
            throw CompiledSpaceError("Byte range extends past space.bin");
        }
    }

    std::uint16_t u16(std::size_t at) const {
        require(at, 2);
        return static_cast<std::uint16_t>(
            std::uint32_t(bytes_[begin_ + at]) |
            (std::uint32_t(bytes_[begin_ + at + 1]) << 8));
    }

    std::uint32_t u32(std::size_t at) const {
        require(at, 4);
        std::uint32_t result = 0;
        for (std::size_t i = 0; i < 4; ++i) {
            result |= std::uint32_t(bytes_[begin_ + at + i]) << (i * 8);
        }
        return result;
    }

    std::uint64_t u64(std::size_t at) const {
        require(at, 8);
        return std::uint64_t(u32(at)) | (std::uint64_t(u32(at + 4)) << 32);
    }

    std::int16_t i16(std::size_t at) const {
        const auto value = u16(at);
        return static_cast<std::int16_t>(
            value < 0x8000U ? std::int32_t(value) : std::int32_t(value) - 0x10000);
    }

    std::int32_t i32(std::size_t at) const {
        const auto value = u32(at);
        return static_cast<std::int32_t>(
            value < 0x80000000U ? std::int64_t(value)
                                : std::int64_t(value) - 0x100000000LL);
    }

    float f32(std::size_t at) const {
        static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
                      "Compiled-space resources require IEEE 754 float32");
        const auto bits = u32(at);
        float value;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }

    std::string fourcc(std::size_t at) const {
        require(at, 4);
        std::string result;
        for (std::size_t i = 0; i < 4; ++i) {
            const auto value = bytes_[begin_ + at + i];
            if (value > 0x7fU) {
                throw CompiledSpaceError("Non-ASCII BWTB section identifier");
            }
            result.push_back(static_cast<char>(value));
        }
        return result;
    }

private:
    void require(std::size_t at, std::size_t count) const {
        if (at > length_ || count > length_ - at) {
            throw CompiledSpaceError("Truncated compiled-space metadata");
        }
    }

    const std::vector<std::uint8_t>& bytes_;
    std::size_t begin_;
    std::size_t length_;
};

TerrainMetadata parse_terrain(const std::vector<std::uint8_t>& bytes,
                              const CompiledSpaceSection& section) {
    if (section.version != 2) {
        throw CompiledSpaceError("Unsupported BWT2 version; #1513 requires version 2");
    }
    if (section.size < terrain_prefix_size) {
        throw CompiledSpaceError("Truncated BWT2 settings or block-list header");
    }
    const Reader reader(bytes, static_cast<std::size_t>(section.offset),
                        static_cast<std::size_t>(section.size));
    if (reader.u32(0) != 32) {
        throw CompiledSpaceError("Unsupported BWT2 settings size; expected 32 bytes");
    }
    TerrainMetadata result;
    result.chunk_size = reader.f32(4);
    if (!std::isfinite(result.chunk_size) || result.chunk_size <= 0) {
        throw CompiledSpaceError("BWT2 chunk size must be finite and positive");
    }
    result.min_x = reader.i32(8);
    result.max_x = reader.i32(12);
    result.min_z = reader.i32(16);
    result.max_z = reader.i32(20);
    if (result.min_x > result.max_x || result.min_z > result.max_z) {
        throw CompiledSpaceError("Inverted BWT2 terrain bounds");
    }
    for (std::size_t i = 0; i < result.texture_ids.size(); ++i) {
        result.texture_ids[i] = reader.u32(24 + i * 4);
    }
    const auto entry_size = reader.u32(36);
    const auto count = reader.u32(40);
    if (entry_size != 8) {
        throw CompiledSpaceError("Unsupported BWT2 terrain entry size; expected 8 bytes");
    }
    // Divide before multiplying so hostile counts cannot wrap or cause an
    // allocation before the entire block list has been admitted.
    if (count > (section.size - terrain_prefix_size) / entry_size) {
        throw CompiledSpaceError("Truncated BWT2 terrain block list");
    }
    result.blocks.reserve(count);
    std::set<std::pair<std::int16_t, std::int16_t>> coordinates;
    for (std::size_t i = 0; i < count; ++i) {
        const auto at = terrain_prefix_size + i * entry_size;
        TerrainBlock block{reader.u32(at), reader.i16(at + 4), reader.i16(at + 6)};
        if (block.x < result.min_x || block.x > result.max_x ||
            block.z < result.min_z || block.z > result.max_z) {
            throw CompiledSpaceError("BWT2 terrain block lies outside its declared bounds");
        }
        if (!coordinates.emplace(block.x, block.z).second) {
            throw CompiledSpaceError("Duplicate BWT2 terrain block coordinates");
        }
        result.blocks.push_back(block);
    }
    result.decoded_bytes = terrain_prefix_size + std::size_t(count) * entry_size;
    return result;
}

} // namespace

CompiledSpace parse_compiled_space(const std::vector<std::uint8_t>& bytes) {
    if (bytes.size() < directory_record_size) {
        throw CompiledSpaceError("space.bin is shorter than the BWTB header");
    }
    const Reader reader(bytes, 0, bytes.size());
    if (reader.fourcc(0) != "BWTB") {
        throw CompiledSpaceError("space.bin is not a BWTB container");
    }
    CompiledSpace result;
    result.version = reader.u32(4);
    if (result.version != 1) {
        throw CompiledSpaceError("Unsupported BWTB version; #1513 requires version 1");
    }
    result.directory_size = reader.u32(8);
    result.declared_file_size = reader.u64(12);
    const auto count = reader.u32(20);
    const std::uint64_t expected_directory =
        directory_record_size + std::uint64_t(count) * directory_record_size;
    if (result.directory_size != expected_directory || expected_directory > bytes.size()) {
        throw CompiledSpaceError("Invalid BWTB directory size");
    }
    if (result.declared_file_size != 0 && result.declared_file_size != bytes.size()) {
        throw CompiledSpaceError("BWTB declared file size does not match space.bin");
    }
    std::set<std::string> names;
    result.sections.reserve(count);
    for (std::size_t i = 0; i < count; ++i) {
        const auto at = directory_record_size + i * directory_record_size;
        CompiledSpaceSection section{reader.fourcc(at), reader.u32(at + 4),
                                     reader.u64(at + 8), reader.u64(at + 16)};
        if (!names.insert(section.name).second) {
            throw CompiledSpaceError("Duplicate BWTB section identifier");
        }
        if (section.offset < result.directory_size || section.offset > bytes.size() ||
            section.size > bytes.size() - section.offset) {
            throw CompiledSpaceError("BWTB section range is outside the payload");
        }
        result.sections.push_back(std::move(section));
    }

    // Directory order is not a layout guarantee. Validate ranges separately
    // while preserving the authored order in the returned metadata.
    std::vector<const CompiledSpaceSection*> ranges;
    for (const auto& section : result.sections) {
        if (section.size != 0) {
            ranges.push_back(&section);
        }
    }
    std::sort(ranges.begin(), ranges.end(), [](const auto* left, const auto* right) {
        return left->offset < right->offset;
    });
    std::uint64_t end = result.directory_size;
    for (const auto* section : ranges) {
        if (section->offset < end) {
            throw CompiledSpaceError("Overlapping BWTB section ranges");
        }
        end = section->offset + section->size;
    }
    const auto terrain = std::find_if(result.sections.begin(), result.sections.end(),
        [](const auto& section) { return section.name == "BWT2"; });
    if (terrain == result.sections.end()) {
        throw CompiledSpaceError("space.bin has no BWT2 terrain section");
    }
    result.terrain = parse_terrain(bytes, *terrain);
    return result;
}

} // namespace wot
