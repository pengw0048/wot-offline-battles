#include "wot/compiled_space.hpp"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using Bytes = std::vector<std::uint8_t>;

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void u16(Bytes& bytes, std::size_t at, std::uint16_t value) {
    for (std::size_t i = 0; i < 2; ++i) {
        bytes.at(at + i) = static_cast<std::uint8_t>(value >> (i * 8));
    }
}

void u32(Bytes& bytes, std::size_t at, std::uint32_t value) {
    for (std::size_t i = 0; i < 4; ++i) {
        bytes.at(at + i) = static_cast<std::uint8_t>(value >> (i * 8));
    }
}

void u64(Bytes& bytes, std::size_t at, std::uint64_t value) {
    u32(bytes, at, static_cast<std::uint32_t>(value));
    u32(bytes, at + 4, static_cast<std::uint32_t>(value >> 32));
}

void f32(Bytes& bytes, std::size_t at, float value) {
    std::uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    u32(bytes, at, bits);
}

void fourcc(Bytes& bytes, std::size_t at, const std::string& text) {
    require(text.size() == 4, "Fixture FourCC must have four bytes");
    std::copy(text.begin(), text.end(), bytes.begin() + at);
}

// Two blocks and a deliberately uninterpreted BWT2 tail. BWSG content is
// opaque metadata, so the fixture does not pretend to contain geometry.
constexpr std::size_t terrain_at = 72;
constexpr std::size_t terrain_size = 67;
constexpr std::size_t geometry_at = terrain_at + terrain_size;

Bytes fixture() {
    Bytes bytes(geometry_at + 3, 0);
    fourcc(bytes, 0, "BWTB");
    u32(bytes, 4, 1);
    u32(bytes, 8, terrain_at);
    u32(bytes, 20, 2);
    fourcc(bytes, 24, "BWT2");
    u32(bytes, 28, 2);
    u64(bytes, 32, terrain_at);
    u64(bytes, 40, terrain_size);
    fourcc(bytes, 48, "BWSG");
    u32(bytes, 52, 2);
    u64(bytes, 56, geometry_at);
    u64(bytes, 64, 3);
    u32(bytes, terrain_at, 32);
    f32(bytes, terrain_at + 4, 100.0f);
    u32(bytes, terrain_at + 8, static_cast<std::uint32_t>(-2));
    u32(bytes, terrain_at + 12, 4);
    u32(bytes, terrain_at + 16, static_cast<std::uint32_t>(-5));
    u32(bytes, terrain_at + 20, 3);
    u32(bytes, terrain_at + 24, 0xf0123456U);
    u32(bytes, terrain_at + 28, 0x81234567U);
    u32(bytes, terrain_at + 32, 0xffffffffU);
    u32(bytes, terrain_at + 36, 8);
    u32(bytes, terrain_at + 40, 2);
    u32(bytes, terrain_at + 44, 0xff000001U);
    u16(bytes, terrain_at + 48, static_cast<std::uint16_t>(-2));
    u16(bytes, terrain_at + 50, 3);
    u32(bytes, terrain_at + 52, 17);
    u16(bytes, terrain_at + 56, 4);
    u16(bytes, terrain_at + 58, static_cast<std::uint16_t>(-5));
    std::fill(bytes.begin() + terrain_at + 60, bytes.begin() + geometry_at, 0xff);
    return bytes;
}

std::size_t rejection_count = 0;

void reject(const Bytes& bytes, const std::string& reason) {
    try {
        static_cast<void>(wot::parse_compiled_space(bytes));
    } catch (const wot::CompiledSpaceError&) {
        ++rejection_count;
        return;
    }
    throw std::runtime_error("Accepted malformed input: " + reason);
}

void mutate_and_reject(const std::string& reason,
                       const std::function<void(Bytes&)>& mutate) {
    auto bytes = fixture();
    mutate(bytes);
    reject(bytes, reason);
}

void test_metadata() {
    auto bytes = fixture();
    const auto parsed = wot::parse_compiled_space(bytes);
    require(parsed.version == 1 && parsed.directory_size == terrain_at,
            "BWTB metadata changed");
    require(parsed.declared_file_size == 0, "Zero declared size was not preserved");
    require(parsed.sections.size() == 2 && parsed.sections[0].name == "BWT2" &&
                parsed.sections[1].name == "BWSG" &&
                parsed.sections[1].version == 2 &&
                parsed.sections[1].offset == geometry_at && parsed.sections[1].size == 3,
            "Section metadata or authored order changed");
    const auto& terrain = parsed.terrain;
    require(terrain.chunk_size == 100.0f && terrain.min_x == -2 && terrain.max_x == 4 &&
                terrain.min_z == -5 && terrain.max_z == 3,
            "Terrain settings changed");
    require(terrain.texture_ids == std::array<std::uint32_t, 3>{
                0xf0123456U, 0x81234567U, 0xffffffffU}, "Texture identifiers changed");
    require(terrain.blocks.size() == 2 && terrain.blocks[0].resource_id == 0xff000001U &&
                terrain.blocks[0].x == -2 && terrain.blocks[0].z == 3 &&
                terrain.blocks[1].resource_id == 17 && terrain.blocks[1].x == 4 &&
                terrain.blocks[1].z == -5, "Terrain block records changed");
    require(terrain.decoded_bytes == 60, "Opaque tail was counted as decoded data");
    bytes.clear();
    require(parsed.sections[0].name == "BWT2" && parsed.terrain.blocks[0].x == -2,
            "Returned metadata depends on the source buffer lifetime");
}

void test_valid_variants() {
    auto bytes = fixture();
    u64(bytes, 12, bytes.size());
    require(wot::parse_compiled_space(bytes).declared_file_size == bytes.size(),
            "Explicit file size was not accepted");

    bytes = fixture();
    std::swap_ranges(bytes.begin() + 24, bytes.begin() + 48, bytes.begin() + 48);
    require(wot::parse_compiled_space(bytes).sections[0].name == "BWSG",
            "Valid unordered directory was rejected or reordered");

    bytes = fixture();
    f32(bytes, terrain_at + 4, 50.5f);
    require(wot::parse_compiled_space(bytes).terrain.chunk_size == 50.5f,
            "Reader hard-coded a 100 metre block size");

    bytes = fixture();
    u32(bytes, terrain_at + 8, static_cast<std::uint32_t>(-32768));
    u32(bytes, terrain_at + 12, 32767);
    u16(bytes, terrain_at + 48, 0x8000U);
    u16(bytes, terrain_at + 56, 0x7fffU);
    const auto limits = wot::parse_compiled_space(bytes);
    require(limits.terrain.blocks[0].x == -32768 && limits.terrain.blocks[1].x == 32767,
            "Signed coordinate limits were not preserved");

    bytes = fixture();
    u32(bytes, terrain_at + 40, 0);
    require(wot::parse_compiled_space(bytes).terrain.blocks.empty(),
            "Empty block list was fabricated or rejected");

    bytes = fixture();
    fourcc(bytes, 48, "XyZ1");
    u32(bytes, 52, 99);
    u64(bytes, 56, bytes.size());
    u64(bytes, 64, 0);
    require(wot::parse_compiled_space(bytes).sections[1].version == 99,
            "Unknown opaque section metadata was rejected");
}

void test_malformed_directory() {
    const auto complete = fixture();
    for (std::size_t length = 0; length < complete.size(); ++length) {
        reject(Bytes(complete.begin(), complete.begin() + length), "truncated container");
    }
    mutate_and_reject("wrong magic", [](Bytes& b) { fourcc(b, 0, "WXYZ"); });
    mutate_and_reject("wrong BWTB version", [](Bytes& b) { u32(b, 4, 2); });
    mutate_and_reject("wrong directory size", [](Bytes& b) { u32(b, 8, 24); });
    mutate_and_reject("overflowing directory count", [](Bytes& b) {
        u32(b, 20, 0xffffffffU);
    });
    mutate_and_reject("declared size mismatch", [](Bytes& b) { u64(b, 12, b.size() + 1); });
    mutate_and_reject("duplicate sections", [](Bytes& b) { fourcc(b, 48, "BWT2"); });
    mutate_and_reject("non-ASCII section", [](Bytes& b) { b[48] = 0x80; });
    mutate_and_reject("missing BWT2", [](Bytes& b) { fourcc(b, 24, "MISS"); });
    mutate_and_reject("section points into directory", [](Bytes& b) { u64(b, 32, 24); });
    mutate_and_reject("section past EOF", [](Bytes& b) { u64(b, 56, b.size() + 1); });
    mutate_and_reject("64-bit offset truncated", [](Bytes& b) { u64(b, 32, 0x100000048ULL); });
    mutate_and_reject("64-bit size truncated", [](Bytes& b) { u64(b, 40, 0x100000043ULL); });
    mutate_and_reject("offset addition overflow", [](Bytes& b) {
        u64(b, 32, std::numeric_limits<std::uint64_t>::max() - 3);
    });
    mutate_and_reject("size addition overflow", [](Bytes& b) {
        u64(b, 40, std::numeric_limits<std::uint64_t>::max());
    });
    mutate_and_reject("overlapping sections", [](Bytes& b) { u64(b, 56, terrain_at + 1); });
}

void test_malformed_terrain() {
    mutate_and_reject("old BWT2 version", [](Bytes& b) { u32(b, 28, 1); });
    mutate_and_reject("old BWT2 settings layout", [](Bytes& b) { u32(b, terrain_at, 20); });
    mutate_and_reject("short BWT2 block-list header", [](Bytes& b) { u64(b, 40, 43); });
    mutate_and_reject("short BWT2 block list", [](Bytes& b) { u64(b, 40, 59); });
    mutate_and_reject("zero entry size", [](Bytes& b) { u32(b, terrain_at + 36, 0); });
    mutate_and_reject("unknown entry size", [](Bytes& b) { u32(b, terrain_at + 36, 16); });
    mutate_and_reject("overflowing block count", [](Bytes& b) {
        u32(b, terrain_at + 40, 0xffffffffU);
    });
    for (const auto value : {0.0f, -1.0f, std::numeric_limits<float>::infinity(),
                            -std::numeric_limits<float>::infinity(),
                            std::numeric_limits<float>::quiet_NaN()}) {
        mutate_and_reject("nonpositive or nonfinite chunk size", [value](Bytes& b) {
            f32(b, terrain_at + 4, value);
        });
    }
    mutate_and_reject("inverted X bounds", [](Bytes& b) { u32(b, terrain_at + 8, 5); });
    mutate_and_reject("inverted Z bounds", [](Bytes& b) {
        u32(b, terrain_at + 20, static_cast<std::uint32_t>(-6));
    });
    mutate_and_reject("block below X bound", [](Bytes& b) {
        u16(b, terrain_at + 48, static_cast<std::uint16_t>(-3));
    });
    mutate_and_reject("block above Z bound", [](Bytes& b) { u16(b, terrain_at + 50, 4); });
    mutate_and_reject("duplicate block coordinates", [](Bytes& b) {
        u16(b, terrain_at + 56, static_cast<std::uint16_t>(-2));
        u16(b, terrain_at + 58, 3);
    });
}

} // namespace

int main() {
    try {
        test_metadata();
        test_valid_variants();
        test_malformed_directory();
        test_malformed_terrain();
        std::cout << "Compiled-space metadata tests passed ("
                  << rejection_count << " malformed inputs rejected)\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Compiled-space metadata test failed: " << error.what() << '\n';
        return 1;
    }
}
