#pragma once

#include "cstdmf/bw_namespace.hpp"
#include "cstdmf/bw_string.hpp"
#include "cstdmf/stdmf.hpp"

BW_BEGIN_NAMESPACE
class CollisionObstacle;

struct StockObstacleIdentity {
    uint32 chunkID = 0;
    uint32 itemIndex = 0;
    BW::string filename;
    float scale = 0.f;
};

// The loaded static-item owner supplies original WGDE/BSMI/SpTr identity.
// False means the obstacle has no source identity. PyErr, when present,
// reports a lookup failure. No obstacle pointer is used as an item index.
bool readStockObstacleIdentity(const CollisionObstacle& obstacle,
    StockObstacleIdentity& output);

BW_END_NAMESPACE
