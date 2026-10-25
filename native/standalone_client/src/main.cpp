#include "wot/compiled_space.hpp"
#include "wot/point_velocity.hpp"

#include <array>
#include <charconv>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

float from_bits(std::uint32_t bits) {
    float value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

std::uint32_t to_bits(float value) {
    std::uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

void point_velocity_stream() {
    std::string line;
    std::size_t line_number = 0;
    while (std::getline(std::cin, line)) {
        ++line_number;
        std::istringstream input(line);
        std::array<float, 12> values{};
        for (float& value : values) {
            std::string token;
            std::uint32_t bits = 0;
            input >> token;
            const auto parsed = std::from_chars(
                token.data(), token.data() + token.size(), bits, 16);
            if (token.empty() || token.size() > 8 || parsed.ec != std::errc{} ||
                parsed.ptr != token.data() + token.size()) {
                throw std::runtime_error("invalid float bits on line " +
                                         std::to_string(line_number));
            }
            value = from_bits(bits);
        }
        std::string extra;
        if (input >> extra) {
            throw std::runtime_error("extra input on line " +
                                     std::to_string(line_number));
        }
        const auto result = wot::point_velocity(
            {values[0], values[1], values[2]},
            {values[3], values[4], values[5]},
            {values[6], values[7], values[8]},
            {values[9], values[10], values[11]});
        std::cout << std::hex << std::setfill('0') << std::setw(8)
                  << to_bits(result.x) << ' ' << std::setw(8)
                  << to_bits(result.y) << ' ' << std::setw(8)
                  << to_bits(result.z) << '\n';
    }
    if (std::cin.bad()) {
        throw std::runtime_error("failed to read point-velocity input");
    }
}

std::string json_string(const std::string& value) {
    std::ostringstream output;
    output << '"';
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') {
            output << '\\' << c;
        } else if (c < 0x20) {
            output << "\\u" << std::hex << std::setfill('0') << std::setw(4)
                   << static_cast<unsigned int>(c);
        } else {
            output << c;
        }
    }
    output << '"';
    return output.str();
}

void inspect_space(const char* path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("cannot open compiled space");
    }
    const std::vector<std::uint8_t> bytes(
        (std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    if (input.bad()) {
        throw std::runtime_error("failed to read compiled space");
    }
    const auto space = wot::parse_compiled_space(bytes);
    const auto& terrain = space.terrain;
    std::cout << std::setprecision(std::numeric_limits<float>::max_digits10)
              << "{\"format\":\"wot-standalone-space-metadata\","
              << "\"version\":" << space.version
              << ",\"sections\":[";
    bool separator = false;
    for (const auto& section : space.sections) {
        if (separator) std::cout << ',';
        separator = true;
        std::cout << "{\"name\":" << json_string(section.name)
                  << ",\"version\":" << section.version
                  << ",\"offset\":" << section.offset
                  << ",\"size\":" << section.size << '}';
    }
    std::cout << "],\"terrain\":{\"chunk_size\":" << terrain.chunk_size
              << ",\"bounds\":[" << terrain.min_x << ',' << terrain.max_x
              << ',' << terrain.min_z << ',' << terrain.max_z
              << "],\"texture_ids\":[" << terrain.texture_ids[0] << ','
              << terrain.texture_ids[1] << ',' << terrain.texture_ids[2]
              << "],\"blocks\":[";
    separator = false;
    for (const auto& block : terrain.blocks) {
        if (separator) std::cout << ',';
        separator = true;
        std::cout << '[' << block.resource_id << ',' << block.x << ','
                  << block.z << ']';
    }
    std::cout << "],\"decoded_bytes\":" << terrain.decoded_bytes
              << "}}\n";
}

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "point-velocity-bits") {
            point_velocity_stream();
        } else if (argc == 3 && std::string(argv[1]) == "inspect-space") {
            inspect_space(argv[2]);
        } else {
            std::cerr << "Usage: wot-native-core inspect-space PATH\n"
                         "       wot-native-core point-velocity-bits\n";
            return 2;
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Error: " << error.what() << '\n';
        return 1;
    }
}
