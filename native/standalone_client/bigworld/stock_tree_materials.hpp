#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace wot
{
struct StockTreeMaterials
{
    std::array<float, 13> branch;
    std::array<float, 13> leaf;
    std::array<float, 13> frond;
    float leafLightingAdjustment;
    std::array<std::int32_t, 4> lightingStyles;
};

// Parse an original version-2 SPT lighting section at the actual stream cursor.
// The caller supplies its structural boundary; this does not scan arbitrary
// geometry/string payload bytes for a plausible token. Incomplete material
// sections requiring unrecovered SDK constructor defaults are rejected.
bool decodeStockTreeLightingSection( const std::uint8_t * data, std::size_t size,
    StockTreeMaterials & materials, std::size_t & consumed, std::string & error );

// Validate the original SPT header/base-tree grammar and read its lighting
// section. Stops after lighting; later geometry/animation remains its owner's
// responsibility. Optional precomputed leaf-LOD sections are not yet supported.
bool decodeStockTreeMaterialPrefix( const std::uint8_t * data, std::size_t size,
    StockTreeMaterials & materials, std::size_t & consumed, std::string & error );

std::array<std::uint8_t, 32> packStockTreeMaterials( const StockTreeMaterials & materials );

// Use the actual tree-type owner's handle. This helper never allocates slots.
void writeStockTreeMaterialAtlasSlot( const StockTreeMaterials & materials,
    std::uint32_t handle, std::array<std::uint8_t, 1024> & atlas );
}
