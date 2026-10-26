#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace wot
{
enum StockTreePart { treeBranch, treeFrond, treeLeaf, treeBillboard };

struct StockTreeGeometryPart
{
    std::uint32_t vertexCount = 0;
    std::uint32_t vertexStride = 0;
    std::vector<std::uint8_t> vertices;
    std::vector<std::vector<std::uint32_t>> lodIndices;
    std::string diffuseMap;
    std::string normalMap;
};

struct StockTreeGeometry
{
    std::array<float, 3> minimumBounds;
    std::array<float, 3> maximumBounds;
    float leafRockScalar;
    float leafRustleScalar;
    std::array<StockTreeGeometryPart, 4> parts;
};

// Read the actual version-106 cached geometry. Does not generate trees or
// invent wind matrices, LOD selection, topology or per-instance identities.
bool decodeStockTreeGeometry( const std::uint8_t * data, std::size_t size,
    StockTreeGeometry & geometry, std::string & error );
}
