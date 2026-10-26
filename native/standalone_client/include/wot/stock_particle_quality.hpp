#pragma once

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

namespace wot {

// #1513 f9a790/f9ade0. These defaults belong to the original profile record,
// not to the XML reader, whose defaults are provided separately by 624a00.
struct StockParticleQualityProfile {
    std::string name;
    float lod_offset = 0.f;
    float lod_multiplier = 1.f;
    float time_multiplier = 1.f;
    float tessellation_scale = 0.5f;
    std::int32_t tessellation_maximum = 3;
    std::int32_t render_quality = 4;
    std::int32_t id = 1;
};

// Ordered values supplied by f9afa0/f9b120 to the original particle runtime.
// A runtime must consume the complete record; storing it alone does not make
// EFFECTS_QUALITY or SNIPER_MODE_EFFECTS_QUALITY effective.
struct StockParticleQualityOutput {
    std::uint32_t type_mask;
    float lod_limit;
    float secondary_lod_multiplier;
    float secondary_lod_offset;
    float lod_multiplier;
    float lod_offset;
    float time_multiplier;
    std::int32_t tessellation_maximum;
    float tessellation_scale;
    std::int32_t render_quality;
};

class StockParticleQuality {
public:
    StockParticleQuality() {
        normal_.id = 2;
        sniper_.id = 6;
    }

    // f9a790 updates an existing name in place. An unknown authored label is
    // retained with ID zero rather than silently becoming another preset.
    void register_profile(const std::string& label, bool sniper,
        StockParticleQualityProfile value) {
        value.name = label + (sniper ? "_sniper" : "");
        value.render_quality = (std::max)(0, (std::min)(4, value.render_quality));
        for (auto& previous : profiles_) {
            if (previous.name == value.name) {
                value.id = previous.id;
                previous = value;
                return;
            }
        }
        value.id = label == "MAX" ? 1 : label == "HIGH" ? 2 :
            label == "MEDIUM" ? 3 : label == "LOW" ? 4 :
            label == "SNIPER" ? 6 : 0;
        if (value.id >= 1 && value.id <= 4 && sniper)
            value.id += 4;
        profiles_.push_back(value);
    }

    // f9b2a0/f9b380: missing ordinary IDs preserve the current record; only
    // IDs 9/10 have the explicit partial fallback defined by the original.
    void select_profile(std::int32_t id, bool sniper) {
        auto& selected = sniper ? sniper_ : normal_;
        for (const auto& value : profiles_) {
            if (value.id == id) {
                selected = value;
                return;
            }
        }
        if (id == 9 || id == 10) {
            selected.id = id;
            selected.lod_offset = 4000.f;
            selected.render_quality = 0;
        }
    }

    const StockParticleQualityProfile& normal() const { return normal_; }
    const StockParticleQualityProfile& sniper() const { return sniper_; }
    const std::vector<StockParticleQualityProfile>& profiles() const { return profiles_; }

    // renderer_mode_one is the exact a49a80()==1 branch. Its renderer owner
    // must supply that fact, rather than inferring it from a quality index.
    StockParticleQualityOutput project(bool sniper, bool renderer_mode_one,
        bool suppress_tessellation) const {
        const auto& selected = sniper ? sniper_ : normal_;
        const auto& secondary = sniper ? normal_ : sniper_;
        const bool distant = renderer_mode_one &&
            (sniper_.id == 9 || sniper_.id == 8) &&
            (normal_.id == 9 || normal_.id == 4);
        const float extra = distant ? 5000.f : 0.f;
        const float normal_offset = distant && normal_.id != 9 ? 0.f : normal_.lod_offset;
        return {
            selected.id == 9 ? 0x80000000U : 0xffffffffU,
            extra + 4000.f,
            distant ? 1.f : secondary.lod_multiplier,
            (sniper ? normal_offset : sniper_.lod_offset) + extra,
            distant ? 1.f : selected.lod_multiplier,
            (sniper ? sniper_.lod_offset : normal_offset) + extra,
            selected.time_multiplier,
            suppress_tessellation ? 0 : selected.tessellation_maximum,
            selected.tessellation_scale,
            selected.render_quality
        };
    }

private:
    std::vector<StockParticleQualityProfile> profiles_;
    StockParticleQualityProfile normal_;
    StockParticleQualityProfile sniper_;
};

} // namespace wot
