#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace wot {

class CompiledSpaceError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct CompiledSpaceSection {
    std::string name;
    std::uint32_t version = 0;
    std::uint64_t offset = 0;
    std::uint64_t size = 0;
};

struct TerrainBlock {
    // The resource identifier is opaque until the BWST string table is decoded.
    std::uint32_t resource_id = 0;
    std::int16_t x = 0;
    std::int16_t z = 0;
};

struct TerrainMetadata {
    float chunk_size = 0;
    std::int32_t min_x = 0;
    std::int32_t max_x = 0;
    std::int32_t min_z = 0;
    std::int32_t max_z = 0;
    // Authored normal-map, global-map and noise-texture identifiers.
    std::array<std::uint32_t, 3> texture_ids{};
    std::vector<TerrainBlock> blocks;
    // BWT2 contains further settings and spatial data after this prefix.
    std::size_t decoded_bytes = 0;
};

struct CompiledSpace {
    std::uint32_t version = 0;
    std::uint32_t directory_size = 0;
    // The reviewed #1513 files leave this optional BWTB field at zero.
    std::uint64_t declared_file_size = 0;
    std::vector<CompiledSpaceSection> sections;
    TerrainMetadata terrain;
};

// Read the #1513 BWTB directory and BWT2 v2 metadata prefix only. Other
// sections remain opaque; success does not establish renderable geometry,
// collision data, valid resource references, or complete BWT2 validity.
// The returned metadata owns no references into bytes.
CompiledSpace parse_compiled_space(const std::vector<std::uint8_t>& bytes);

} // namespace wot
