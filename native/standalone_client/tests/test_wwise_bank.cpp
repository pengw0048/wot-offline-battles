#include "wot/wwise_bank.hpp"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void write_u32(std::vector<std::uint8_t>& data, std::size_t offset,
    std::uint32_t value) {
    for (unsigned byte = 0; byte < 4; ++byte)
        data[offset + byte] = static_cast<std::uint8_t>(value >> (byte * 8));
}
void rejected(const std::vector<std::uint8_t>& bank) {
    const auto before = bank;
    bool threw = false;
    try { wot::decode_wot_1513_wwise_bank_header(bank); }
    catch (const std::invalid_argument&) { threw = true; }
    check(threw, "Invalid encoded bank was accepted");
    check(before == bank, "Failed decode changed source bytes");
}
void contracts() {
    for (std::uint32_t size : {20u, 24u, 28u, 32u}) {
        std::vector<std::uint8_t> encoded(size + 8 + 16, 0xa5);
        write_u32(encoded, 0, 0x44484b42u);
        write_u32(encoded, 4, size);
        write_u32(encoded, 8, 125u ^ 0xd2f5b297u);
        write_u32(encoded, 12, 0x12345678u ^ 0xc57da87eu);
        write_u32(encoded, 16, 0x87654321u ^ 0xd10c0503u);
        write_u32(encoded, 20, 0xabcd5678u ^ 0x6b0403d4u);
        auto expected = encoded;
        write_u32(expected, 8, 125u);
        write_u32(expected, 12, 0x12345678u);
        write_u32(expected, 16, 0x87654321u);
        write_u32(expected, 20, 0xabcd5678u);
        const auto before = encoded;
        const auto decoded = wot::decode_wot_1513_wwise_bank_header(encoded);
        check(decoded == expected, "Header XOR or byte preservation changed");
        check(encoded == before, "Decode changed source bank");
        rejected(decoded);
        for (std::size_t length = 0; length < size + 8; ++length)
            rejected({encoded.begin(), encoded.begin() + length});
        auto bad = encoded;
        write_u32(bad, 4, 19u); rejected(bad);
        write_u32(bad, 4, 0xffffffffu); rejected(bad);
        bad = encoded; write_u32(bad, 8, 128u ^ 0xd2f5b297u); rejected(bad);
        bad = encoded; bad[0] = 'P'; rejected(bad);
    }
}
} // namespace

int main(int argc, char** argv) {
    try {
        contracts();
        if (argc == 3) {
            std::ifstream input(argv[1], std::ios::binary);
            if (!input) throw std::runtime_error("Cannot read bank fixture");
            const std::vector<std::uint8_t> encoded{
                std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
            const auto decoded = wot::decode_wot_1513_wwise_bank_header(encoded);
            std::ofstream output(argv[2], std::ios::binary);
            output.write(reinterpret_cast<const char*>(decoded.data()), decoded.size());
            if (!output) throw std::runtime_error("Cannot write decoded bank fixture");
        } else if (argc != 1) {
            throw std::runtime_error("Expected zero arguments or input/output bank paths");
        }
        std::cout << "Pinned Wwise bank header checks passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    return 0;
}
