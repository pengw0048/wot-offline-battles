#include "wot/wwise_bank.hpp"

#include <array>
#include <cstddef>
#include <stdexcept>

namespace wot {
namespace {
std::uint32_t read_u32(const std::vector<std::uint8_t>& data, std::size_t offset) {
    return std::uint32_t(data[offset]) |
        (std::uint32_t(data[offset + 1]) << 8) |
        (std::uint32_t(data[offset + 2]) << 16) |
        (std::uint32_t(data[offset + 3]) << 24);
}
} // namespace

std::vector<std::uint8_t> decode_wot_1513_wwise_bank_header(
    const std::vector<std::uint8_t>& bank) {
    // The native reader consumes an eight-byte chunk prefix and twenty-byte
    // fixed header, then skips the authored extension using the chunk length.
    if (bank.size() < 28 || read_u32(bank, 0) != 0x44484b42u) {
        throw std::invalid_argument("Missing or truncated Wwise BKHD header");
    }
    const auto header_size = read_u32(bank, 4);
    if (header_size < 20 || header_size > bank.size() - 8) {
        throw std::invalid_argument("Invalid Wwise BKHD chunk length");
    }
    constexpr std::array<std::uint32_t, 4> keys{{
        0xd2f5b297u, 0xc57da87eu, 0xd10c0503u, 0x6b0403d4u}};
    if ((read_u32(bank, 8) ^ keys[0]) != 125u) {
        throw std::invalid_argument("Incompatible encoded #1513 Wwise bank version");
    }
    auto decoded = bank;
    for (std::size_t field = 0; field < keys.size(); ++field) {
        const auto value = read_u32(bank, 8 + field * 4) ^ keys[field];
        for (unsigned byte = 0; byte < 4; ++byte) {
            decoded[8 + field * 4 + byte] =
                static_cast<std::uint8_t>(value >> (byte * 8));
        }
    }
    return decoded;
}

} // namespace wot
