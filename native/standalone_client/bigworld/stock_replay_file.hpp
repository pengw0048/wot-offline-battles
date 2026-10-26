#pragma once
#include "wot/stock_replay_stream.hpp"

namespace wot {
// Paths are UTF-8, including on Windows. Failed reads preserve the current
// stream; failed saves preserve the previously published destination file.
class StockReplayFile {
public:
    bool open(const std::string& path, std::string& error);
    bool save(const std::string& path, ReplayContainer format, std::string& error) const;
    void close() { stream_.clear(); cursor_=0; }
    bool seekPacket(std::size_t index);
    bool next(ReplayPacketIndex& packet);
    std::size_t cursor() const { return cursor_; }
    StockReplayStream& stream() { return stream_; }
    const StockReplayStream& stream() const { return stream_; }
private:
    StockReplayStream stream_;
    std::size_t cursor_=0;
};
} // namespace wot
