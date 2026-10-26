#pragma once

#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>

namespace wot
{
// #1513 a62800 owns loaded textures under channel-tagged, process-wide IDs.
// It deliberately allocates a new ID for repeated resource names.
template<class TexturePtr> class StockTraceTextureRegistry
{
public:
    typedef std::map<std::uint32_t, TexturePtr> Textures;
    explicit StockTraceTextureRegistry(std::uint32_t &nextID) : nextID_(nextID) {}

    template<class Loader> int add(const std::string &name, Loader load)
    {
        const std::size_t separator = name.find(';');
        if (separator == std::string::npos)
        {
            TexturePtr texture = load(name);
            if (!texture)
                return -1;
            textures_[nextID_ | 0x60000000u] = texture;
        }
        else
        {
            const std::size_t second = name.find_first_not_of(" \t", separator + 1);
            if (second == std::string::npos)
                throw std::out_of_range("Trace texture pair has no second resource");
            TexturePtr first = load(name.substr(0, separator));
            TexturePtr last = load(name.substr(second));
            if (!first || !last)
                return -1;
            textures_[nextID_ | 0x20000000u] = first;
            textures_[nextID_ | 0x40000000u] = last;
        }
        return static_cast<int>(nextID_++);
    }

    // Native trace consumers must use this channel-tagged resource map.
    // Exposing it avoids a second registry with unrelated IDs.
    const Textures &textures() const { return textures_; }

private:
    std::uint32_t &nextID_;
    Textures textures_;
};

inline float stockDamageStickerAngle(float degrees)
{
    // 652210 preserves these two single-precision multiplications in order.
    return (degrees * 3.1415927f) * 0.0055555557f;
}
}

#ifndef WOT_STOCK_DECAL_RESOURCES_PORTABLE
#include "cstdmf/bw_namespace.hpp"
#include "moo/base_texture.hpp"

BW_BEGIN_NAMESPACE

class StockTraceTextureOwner
{
public:
    typedef wot::StockTraceTextureRegistry<Moo::BaseTexturePtr> Registry;
    StockTraceTextureOwner();
    int textureIndex(const BW::string &name);
    const Registry::Textures &textures() const { return registry_.textures(); }
private:
    Registry registry_;
};

BW_END_NAMESPACE
#endif
