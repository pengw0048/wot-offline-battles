#pragma once

#include <cstdint>
#include <vector>

namespace wot {

// #1513 0x01248e50 installs four keys consumed by 0x012abc60 after reading
// BKHD. Restore those fields for a standard Wwise bank reader. All remaining
// bytes, including the variable-length BKHD extension, are preserved.
//
// This accepts the pinned client's encoded header, not an already decoded
// bank or a file package. Invalid/truncated headers and a decoded version other
// than the original reader's 125 throw std::invalid_argument. Header acceptance
// does not establish that an SDK can instantiate the bank's required plug-ins.
std::vector<std::uint8_t> decode_wot_1513_wwise_bank_header(
    const std::vector<std::uint8_t>& bank);

} // namespace wot
