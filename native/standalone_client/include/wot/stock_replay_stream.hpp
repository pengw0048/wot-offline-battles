#pragma once
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace wot {
enum class ReplayContainer { PacketStream, LegacyZlib, EncryptedZlib };
struct ReplayPacketIndex {
    std::uint32_t size=0, type=0;
    float time=0;
    std::size_t headerOffset=0, payloadOffset=0;
};

// Owns the unmodified packet bytes. The time index is separate so equal or
// decreasing timestamps never reorder the recorded packet stream.
class StockReplayStream {
public:
    bool decode(const std::vector<std::uint8_t>& file, std::string& error);
    bool encode(ReplayContainer format, std::vector<std::uint8_t>& file, std::string& error) const;
    bool append(std::uint32_t type, float time, const std::vector<std::uint8_t>& payload, std::string& error);
    void clear();
    bool packetString(std::size_t packet, std::string& value, std::string& error) const;
    // Returns packet count when no timestamp is at or after the target.
    std::size_t lowerBoundTime(float target) const;
    ReplayContainer format() const { return format_; }
    const std::vector<std::uint8_t>& bytes() const { return bytes_; }
    const std::vector<ReplayPacketIndex>& packets() const { return packets_; }
    const std::vector<std::size_t>& timeIndex() const { return timeIndex_; }
    const std::vector<std::string>& metadata() const { return metadata_; }
    void metadata(std::vector<std::string> value) { metadata_=std::move(value); }
    float duration() const { return duration_; }
    std::size_t terminalOffset() const { return terminalOffset_; }
private:
    bool index(std::string& error);
    ReplayContainer format_=ReplayContainer::PacketStream;
    std::vector<std::string> metadata_;
    std::vector<std::uint8_t> bytes_;
    std::vector<ReplayPacketIndex> packets_;
    std::vector<std::size_t> timeIndex_;
    float duration_=0;
    std::size_t terminalOffset_=std::numeric_limits<std::size_t>::max();
};
} // namespace wot
